import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const expectedUrl = String(process.argv[2] || '').trim();
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

const ws = new WebSocket(endpoint);
let seq = 0;
let completed = false;
const pending = new Map();
const timeout = setTimeout(() => {
  if (completed) return;
  console.error('RELAY_CDP_TIMEOUT');
  try { ws.close(); } catch {}
  process.exit(2);
}, 5000);

function send(method, params = {}) {
  const id = ++seq;
  ws.send(JSON.stringify({id, method, params}));
  return new Promise((resolve, reject) => pending.set(id, {resolve, reject}));
}

ws.addEventListener('message', (event) => {
  let msg;
  try { msg = JSON.parse(String(event.data)); } catch { return; }
  if (!msg.id || !pending.has(msg.id)) return;
  const waiter = pending.get(msg.id);
  pending.delete(msg.id);
  if (msg.error) waiter.reject(new Error(`${msg.error.code || 'CDP'}:${msg.error.message || 'error'}`));
  else waiter.resolve(msg.result || {});
});
ws.addEventListener('error', () => {
  if (completed) return;
  clearTimeout(timeout);
  console.error('RELAY_CDP_SOCKET_ERROR');
  process.exit(3);
});
ws.addEventListener('open', async () => {
  try {
    const version = await send('Browser.getVersion');
    const targets = await send('Target.getTargets');
    const pages = (targets.targetInfos || []).filter((t) => t.type === 'page');
    const chatgpt = pages.filter((t) => String(t.url || '').startsWith('https://chatgpt.com/'));
    const exact = expectedUrl ? pages.filter((t) => String(t.url || '') === expectedUrl) : [];
    completed = true;
    clearTimeout(timeout);
    console.log(JSON.stringify({
      ok: true,
      product: version.product || null,
      protocolVersion: version.protocolVersion || null,
      pageTargets: pages.length,
      chatgptTargets: chatgpt.length,
      exactTargetCount: exact.length,
      exactTargetIds: exact.map((t) => t.targetId),
    }));
    ws.close();
    setTimeout(() => process.exit(0), 20);
  } catch (err) {
    clearTimeout(timeout);
    console.error(`RELAY_CDP_FAILED:${err?.message || err}`);
    process.exit(4);
  }
});
