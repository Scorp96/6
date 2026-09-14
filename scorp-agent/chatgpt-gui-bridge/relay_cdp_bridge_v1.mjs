import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const statePath = path.resolve(process.argv[2] || 'relay-cdp-state-v1.json');
const logicalSession = String(process.argv[3] || '').trim();
const argv = process.argv.slice(4);
if (!logicalSession) throw new Error('RELAY_CDP_SESSION_EMPTY');
if (!argv.length) throw new Error('RELAY_CDP_COMMAND_EMPTY');

function loadState() {
  if (!fs.existsSync(statePath)) return {protocol_version: 'scorp.relay-cdp/v1', sessions: {}};
  const value = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  if (!value || value.protocol_version !== 'scorp.relay-cdp/v1' || typeof value.sessions !== 'object') {
    throw new Error('RELAY_CDP_STATE_INVALID');
  }
  return value;
}

function saveState(value) {
  fs.mkdirSync(path.dirname(statePath), {recursive: true});
  const tmp = `${statePath}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(value, null, 2) + '\n', 'utf8');
  fs.renameSync(tmp, statePath);
}

function relayEndpoint() {
  const root = path.join(os.homedir(), '.chrome-use');
  const candidates = fs.readdirSync(root)
    .filter((name) => name.startsWith('relay-cdp-url-'))
    .map((name) => {
      const full = path.join(root, name);
      return {full, mtime: fs.statSync(full).mtimeMs};
    })
    .sort((a, b) => b.mtime - a.mtime);
  if (!candidates.length) throw new Error('RELAY_CDP_ENDPOINT_MISSING');
  const endpoint = fs.readFileSync(candidates[0].full, 'utf8').trim();
  if (!/^wss?:\/\//.test(endpoint)) throw new Error('RELAY_CDP_ENDPOINT_INVALID');
  return endpoint;
}

class CdpSocket {
  constructor(endpoint) {
    this.ws = new WebSocket(endpoint);
    this.seq = 0;
    this.pending = new Map();
  }
  async open(timeoutMs = 5000) {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('RELAY_CDP_CONNECT_TIMEOUT')), timeoutMs);
      this.ws.addEventListener('open', () => { clearTimeout(timer); resolve(); }, {once: true});
      this.ws.addEventListener('error', () => { clearTimeout(timer); reject(new Error('RELAY_CDP_SOCKET_ERROR')); }, {once: true});
    });
    this.ws.addEventListener('message', (event) => {
      let msg;
      try { msg = JSON.parse(String(event.data)); } catch { return; }
      if (!msg.id || !this.pending.has(msg.id)) return;
      const waiter = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      if (msg.error) waiter.reject(new Error(`${msg.error.code || 'CDP'}:${msg.error.message || 'error'}`));
      else waiter.resolve(msg.result || {});
    });
  }
  send(method, params = {}, sessionId = undefined, timeoutMs = 5000) {
    const id = ++this.seq;
    const payload = {id, method, params};
    if (sessionId) payload.sessionId = sessionId;
    this.ws.send(JSON.stringify(payload));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`RELAY_CDP_COMMAND_TIMEOUT:${method}`));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timer); resolve(value); },
        reject: (err) => { clearTimeout(timer); reject(err); },
      });
    });
  }
  close() { try { this.ws.close(); } catch {} }
}

async function targetInfos(cdp) {
  const result = await cdp.send('Target.getTargets');
  return Array.isArray(result.targetInfos) ? result.targetInfos : [];
}

async function requireTarget(cdp, state) {
  const row = state.sessions[logicalSession];
  const targetId = row && String(row.target_id || '').trim();
  if (!targetId) throw new Error('RELAY_CDP_TARGET_MISSING');
  const infos = await targetInfos(cdp);
  if (!infos.some((info) => info.targetId === targetId && info.type === 'page')) {
    throw new Error('RELAY_CDP_TARGET_GONE');
  }
  return targetId;
}

async function attach(cdp, targetId) {
  const result = await cdp.send('Target.attachToTarget', {targetId, flatten: true});
  if (!result.sessionId) throw new Error('RELAY_CDP_ATTACH_FAILED');
  return result.sessionId;
}

async function evaluate(cdp, sessionId, expression, {awaitPromise = true} = {}) {
  const result = await cdp.send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise,
    userGesture: true,
  }, sessionId, 8000);
  if (result.exceptionDetails) throw new Error('RELAY_CDP_EVAL_EXCEPTION');
  return result.result ? result.result.value : undefined;
}

async function waitEvaluate(cdp, sessionId, expression, predicate, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let value;
  while (Date.now() < deadline) {
    value = await evaluate(cdp, sessionId, expression);
    if (predicate(value)) return value;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return value;
}

function jsonExpr(value) { return JSON.stringify(String(value)); }

const state = loadState();
const cdp = new CdpSocket(relayEndpoint());
await cdp.open();

try {
  const command = argv[0];
  if (command === 'open') {
    const url = String(argv[1] || '').trim();
    if (!url) throw new Error('RELAY_CDP_OPEN_URL_MISSING');
    let row = state.sessions[logicalSession];
    let targetId = row && String(row.target_id || '').trim();
    const infos = await targetInfos(cdp);
    if (targetId && !infos.some((info) => info.targetId === targetId && info.type === 'page')) targetId = '';
    let sid;
    if (!targetId) {
      const created = await cdp.send('Target.createTarget', {url});
      targetId = created.targetId;
      if (!targetId) throw new Error('RELAY_CDP_CREATE_TARGET_FAILED');
      state.sessions[logicalSession] = {target_id: targetId};
      saveState(state);
      sid = await attach(cdp, targetId);
    } else {
      sid = await attach(cdp, targetId);
      await cdp.send('Page.navigate', {url}, sid, 8000);
    }
    const readyUrl = await waitEvaluate(
      cdp,
      sid,
      'location.href',
      (value) => typeof value === 'string' && /^https:\/\/chatgpt\.com(?:\/|$)/.test(value) && value.replace(/\/$/, '') === url.replace(/\/$/, ''),
      10000,
    );
    if (typeof readyUrl !== 'string' || readyUrl.replace(/\/$/, '') !== url.replace(/\/$/, '')) {
      throw new Error('RELAY_CDP_OPEN_URL_NOT_READY');
    }
    console.log(JSON.stringify({success: true, data: {targetId, url: readyUrl}}));
  } else if (command === 'get' && argv[1] === 'url') {
    const targetId = await requireTarget(cdp, state);
    const sid = await attach(cdp, targetId);
    const url = await evaluate(cdp, sid, 'location.href');
    console.log(JSON.stringify({success: true, data: {url}}));
  } else if (command === 'bringToFront') {
    const targetId = await requireTarget(cdp, state);
    await cdp.send('Target.activateTarget', {targetId});
    console.log(JSON.stringify({success: true, data: {broughtToFront: true}}));
  } else if (command === 'read') {
    const targetId = await requireTarget(cdp, state);
    const sid = await attach(cdp, targetId);
    const content = await evaluate(cdp, sid, `(() => {
      const rows = [...document.querySelectorAll('[data-message-author-role]')]
        .map(el => ({role: el.getAttribute('data-message-author-role'), text: (el.innerText || el.textContent || '').trim()}))
        .filter(row => row.text);
      if (!rows.length) return document.body ? document.body.innerText : '';
      return rows.map(row => row.role === 'assistant' ? '#### ChatGPT said:\\n' + row.text : row.role === 'user' ? '#### You said:\\n' + row.text : row.text).join('\\n');
    })()`);
    console.log(JSON.stringify({success: true, data: {content: String(content || '')}}));
  } else if (command === 'snapshot' && argv.includes('-i')) {
    const targetId = await requireTarget(cdp, state);
    const sid = await attach(cdp, targetId);
    const info = await waitEvaluate(cdp, sid, `(() => {
      const editor = document.querySelector('#prompt-textarea') || document.querySelector('[contenteditable="true"]');
      const send = document.querySelector('button[data-testid="send-button"]') || [...document.querySelectorAll('button')].find(b => /^(send|send message|send prompt|发送|发送消息|发送提示词)$/i.test((b.getAttribute('aria-label') || b.innerText || '').trim()));
      return {editor: !!editor, send: !!send};
    })()`, (v) => v && v.editor, 10000);
    const refs = {};
    if (info && info.editor) refs.editor = {role: 'textbox', name: 'Prompt'};
    if (info && info.send) refs.send = {role: 'button', name: 'Send'};
    console.log(JSON.stringify({success: true, data: {refs}}));
  } else if (command === 'fill') {
    if (argv[1] !== '@editor') throw new Error('RELAY_CDP_FILL_REF_INVALID');
    const text = String(argv[2] || '');
    const targetId = await requireTarget(cdp, state);
    const sid = await attach(cdp, targetId);
    const outcome = await evaluate(cdp, sid, `(() => {
      const text = ${jsonExpr(text)};
      const el = document.querySelector('#prompt-textarea') || document.querySelector('[contenteditable="true"]');
      if (!el) return {ok:false, reason:'EDITOR_MISSING'};
      el.focus();
      if ('value' in el && !el.isContentEditable) {
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        if (setter) setter.call(el, text); else el.value = text;
        el.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:text}));
      } else {
        document.execCommand('selectAll', false, null);
        const inserted = document.execCommand('insertText', false, text);
        if (!inserted) {
          el.textContent = text;
          el.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:text}));
        }
      }
      const observed = ('value' in el && !el.isContentEditable) ? el.value : (el.innerText || el.textContent || '');
      return {ok: observed === text, observed};
    })()`);
    if (!outcome || !outcome.ok) throw new Error('RELAY_CDP_FILL_VERIFY_FAILED');
    console.log(JSON.stringify({success: true, data: {filled: true}}));
  } else if (command === 'click') {
    if (argv[1] !== '@send') throw new Error('RELAY_CDP_CLICK_REF_INVALID');
    const targetId = await requireTarget(cdp, state);
    const sid = await attach(cdp, targetId);
    const clicked = await evaluate(cdp, sid, `(() => {
      const send = document.querySelector('button[data-testid="send-button"]') || [...document.querySelectorAll('button')].find(b => /^(send|send message|send prompt|发送|发送消息|发送提示词)$/i.test((b.getAttribute('aria-label') || b.innerText || '').trim()));
      if (!send || send.disabled) return false;
      send.click();
      return true;
    })()`);
    if (!clicked) throw new Error('RELAY_CDP_SEND_BUTTON_MISSING');
    console.log(JSON.stringify({success: true, data: {clicked: true}}));
  } else {
    throw new Error(`RELAY_CDP_COMMAND_UNSUPPORTED:${argv.join(' ')}`);
  }
} finally {
  cdp.close();
  setTimeout(() => process.exit(0), 15);
}
