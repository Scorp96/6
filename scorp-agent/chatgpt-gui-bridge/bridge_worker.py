import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


def _parse_time(value: str) -> dt.datetime:
    value = value.replace('Z', '+00:00')
    return dt.datetime.fromisoformat(value)


def validate_live_binding(task: dict, request: dict):
    if task.get('task_id') != request.get('task_id'):
        raise ValueError('TASK_ID_MISMATCH')
    if task.get('project_id') != request.get('project_id'):
        raise ValueError('PROJECT_ID_MISMATCH')
    if task.get('state') != 'WAITING' or task.get('waiting_reason') != 'GPT_CONTINUATION_REQUIRED':
        raise ValueError('TASK_NOT_WAITING_CONTINUATION')
    if task.get('continuation_request_id') != request.get('request_id'):
        raise ValueError('CONTINUATION_REQUEST_MISMATCH')
    if int(task.get('generation', -1)) != int(request.get('continuation_generation', -2)):
        raise ValueError('CONTINUATION_GENERATION_MISMATCH')
    if task.get('safety_class') != 'standard' or request.get('safety_class') != 'standard':
        raise ValueError('AUTO_SAFETY_CLASS_REJECTED')
    if task.get('authorization_requirement') not in (None, ''):
        raise ValueError('AUTHORIZATION_REQUIRED')
    last = task.get('last_result') or {}
    if request.get('previous_action_id') != last.get('action_id'):
        raise ValueError('PREVIOUS_ACTION_MISMATCH')
    if request.get('previous_result_sha256') != last.get('result_sha256'):
        raise ValueError('PREVIOUS_RESULT_MISMATCH')
    return True


def select_pending_request(registry: dict, requests: list[dict], ledger: dict, now: dt.datetime):
    tasks = {t.get('task_id'): t for t in registry.get('tasks', [])}
    eligible = []
    for request in requests:
        rid = request.get('request_id')
        if not rid or request.get('protocol_version') != 'scorp.orchestrator/continuation-request-v1':
            continue
        if request.get('safety_class') != 'standard':
            continue
        if not request.get('issue_number') or request.get('publication_state') not in ('PUBLISHED', 'RECONCILED'):
            continue
        try:
            if _parse_time(request['expires_at']) <= now:
                continue
        except Exception:
            continue
        entry = ledger.get(rid, {}) or {}
        state = entry.get('state')
        if state in ('POSTED', 'ADOPTED'):
            continue
        if state == 'GUI_INFLIGHT':
            lease_until = entry.get('lease_until')
            if not lease_until:
                continue
            try:
                if _parse_time(lease_until) > now:
                    continue
            except Exception:
                continue
        if state == 'RETRY_WAIT':
            retry_at = entry.get('next_retry_at')
            if not retry_at:
                continue
            try:
                if _parse_time(retry_at) > now:
                    continue
            except Exception:
                continue
        task = tasks.get(request.get('task_id'))
        if task is None:
            continue
        try:
            validate_live_binding(task, request)
        except ValueError:
            continue
        eligible.append((int(task.get('priority', 0)), request.get('created_at', ''), rid, request, task))
    if not eligible:
        return None
    eligible.sort(key=lambda x: (-x[0], x[1], x[2]))
    return eligible[0][3], eligible[0][4]

def load_previous_result(task: dict, request: dict, state_root: str):
    expected = request.get('previous_result_sha256')
    if not expected:
        return None
    last = task.get('last_result') or {}
    validate_live_binding(task, request)
    issue = int(last['issue_number'])
    action = str(last['action_id'])
    path = Path(state_root) / f'issue-{issue}-action-{action}-result.json'
    if not path.is_file():
        raise ValueError('PREVIOUS_RESULT_FILE_MISSING')
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise ValueError('PREVIOUS_RESULT_HASH_MISMATCH')
    try:
        return json.loads(raw.decode('utf-8'))
    except Exception as exc:
        raise ValueError('PREVIOUS_RESULT_INVALID_JSON') from exc


def build_decision_comment(decision: dict) -> str:
    return 'SCORP_CONT_DECISION\n' + json.dumps(decision, ensure_ascii=False, separators=(',', ':'))

def _comment_author(comment: dict) -> str:
    if isinstance(comment.get('user'), dict):
        return str(comment['user'].get('login') or '')
    return str(comment.get('author') or '')


def _validate_decision_binding(decision: dict, request: dict):
    if decision.get('protocol_version') != 'scorp.orchestrator/continuation-decision-v1':
        raise ValueError('DECISION_PROTOCOL_MISMATCH')
    pairs = (
        ('request_id', 'request_id'),
        ('task_id', 'task_id'),
        ('expected_registry_sequence', 'registry_sequence'),
        ('previous_continuation_generation', 'continuation_generation'),
        ('previous_action_id', 'previous_action_id'),
        ('previous_result_sha256', 'previous_result_sha256'),
    )
    for dk, rk in pairs:
        if decision.get(dk) != request.get(rk):
            raise ValueError('DECISION_BINDING_MISMATCH_' + dk)
    if not decision.get('decision_id'):
        raise ValueError('DECISION_ID_EMPTY')
    mutation = decision.get('mutation')
    if not isinstance(mutation, dict):
        raise ValueError('DECISION_MUTATION_MISSING')
    from bridge_core import build_bound_decision
    build_bound_decision(request, mutation)
    return decision


def find_existing_decision(comments: list[dict], request: dict, trusted_actor: str):
    found = []
    for comment in comments:
        if _comment_author(comment) != trusted_actor:
            continue
        body = str(comment.get('body') or '')
        if not body.startswith('SCORP_CONT_DECISION\n'):
            continue
        try:
            decision = json.loads(body.split('\n', 1)[1])
        except Exception as exc:
            raise ValueError('TRUSTED_DECISION_INVALID_JSON') from exc
        _validate_decision_binding(decision, request)
        found.append(decision)
    if len(found) > 1:
        raise ValueError('DUPLICATE_TRUSTED_DECISION')
    return found[0] if found else None


async def obtain_and_publish_decision(request: dict, task: dict, previous_result: dict, github, gui_turn,
                                      ledger: dict, persist_ledger, refresh_task, trusted_actor: str, conversation_url=None):
    validate_live_binding(task, request)
    request_id = request['request_id']
    issue_number = int(request['issue_number'])
    entry = ledger.get(request_id, {}) or {}
    existing = find_existing_decision(github.list_comments(issue_number), request, trusted_actor)
    if existing is not None:
        ledger[request_id] = {'state': 'ADOPTED', 'decision_id': existing['decision_id']}
        persist_ledger(ledger)
        return {'status': 'ADOPTED', 'decision': existing}
    from gui_transport import render_controller_prompt
    from bridge_core import build_bound_decision
    if entry.get('state') == 'RESPONSE_CAPTURED':
        decision = entry.get('decision')
        if not isinstance(decision, dict):
            raise ValueError('CAPTURED_DECISION_MISSING')
        _validate_decision_binding(decision, request)
        conversation_url = entry.get('conversation_url')
        live_task = refresh_task()
        validate_live_binding(live_task, request)
        github.post_comment(issue_number, build_decision_comment(decision))
        verified = find_existing_decision(github.list_comments(issue_number), request, trusted_actor)
        if verified is None or verified.get('decision_id') != decision.get('decision_id'):
            raise ValueError('DECISION_PUBLICATION_NOT_VERIFIED')
        ledger[request_id] = {'state': 'POSTED', 'decision_id': decision['decision_id'], 'conversation_url': conversation_url}
        persist_ledger(ledger)
        return {'status': 'POSTED', 'decision': decision, 'conversation_url': conversation_url}

    now = dt.datetime.now(dt.timezone.utc)
    attempts = int(entry.get('attempts', 0)) + 1
    ledger[request_id] = {
        'state': 'GUI_INFLIGHT',
        'attempts': attempts,
        'started_at': now.isoformat().replace('+00:00', 'Z'),
        'lease_until': (now + dt.timedelta(minutes=20)).isoformat().replace('+00:00', 'Z'),
    }
    persist_ledger(ledger)
    prompt = render_controller_prompt(request, previous_result)
    try:
        mutation, snapshot, conversation_url = await gui_turn(prompt, request_id, conversation_url=conversation_url)
        decision = build_bound_decision(request, mutation)
    except Exception as exc:
        backoff_seconds = min(300, 30 * (2 ** min(attempts - 1, 4)))
        retry_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=backoff_seconds)
        ledger[request_id] = {
            'state': 'RETRY_WAIT',
            'attempts': attempts,
            'next_retry_at': retry_at.isoformat().replace('+00:00', 'Z'),
            'last_error': f'{type(exc).__name__}: {exc}',
        }
        persist_ledger(ledger)
        raise
    live_task = refresh_task()
    validate_live_binding(live_task, request)
    existing = find_existing_decision(github.list_comments(issue_number), request, trusted_actor)
    if existing is not None:
        ledger[request_id] = {'state': 'ADOPTED', 'decision_id': existing['decision_id'], 'conversation_url': conversation_url}
        persist_ledger(ledger)
        return {'status': 'ADOPTED', 'decision': existing, 'conversation_url': conversation_url}

    ledger[request_id] = {
        'state': 'RESPONSE_CAPTURED',
        'attempts': attempts,
        'decision_id': decision['decision_id'],
        'decision': decision,
        'conversation_url': conversation_url,
        'captured_at': dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    persist_ledger(ledger)
    github.post_comment(issue_number, build_decision_comment(decision))
    verified = find_existing_decision(github.list_comments(issue_number), request, trusted_actor)
    if verified is None or verified.get('decision_id') != decision.get('decision_id'):
        raise ValueError('DECISION_PUBLICATION_NOT_VERIFIED')
    ledger[request_id] = {'state': 'POSTED', 'decision_id': decision['decision_id'], 'conversation_url': conversation_url}
    persist_ledger(ledger)
    return {'status': 'POSTED', 'decision': decision, 'conversation_url': conversation_url}

def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            'w', encoding='utf-8', newline='\n', dir=path.parent,
            prefix=path.name + '.', suffix='.tmp', delete=False,
        ) as handle:
            tmp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        last_error = None
        for attempt in range(5):
            try:
                os.replace(tmp_name, path)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                if attempt >= 4:
                    raise
                time.sleep(0.01 * (attempt + 1))
        if last_error is not None:
            raise last_error
        tmp_name = None
    finally:
        if tmp_name:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except Exception:
                pass


class BridgeProcessAlreadyRunning(RuntimeError):
    """Raised when another bridge worker owns the process lease."""


class BridgeProcessLease:
    """Hold an OS-level singleton lease for one bridge worker process.

    The lock is only a process-ownership guard.  It is not runtime state and
    does not replace SQLite authority, JSON ledgers, or durable actor leases.
    The kernel releases it automatically if the process exits unexpectedly.
    """

    def __init__(self, path):
        self.path = Path(path)
        self._handle = None

    def acquire(self):
        if self._handle is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - exercised on non-Windows CI only
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            handle.close()
            raise BridgeProcessAlreadyRunning(str(self.path)) from exc
        self._handle = handle
        return self

    def release(self):
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - exercised on non-Windows CI only
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def _load_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding='utf-8-sig'))


class ConversationRegistry:
    _ROLES = {'A', 'B', 'C'}
    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        value = _load_json(self.path, {}) or {}
        return value if isinstance(value, dict) else {}

    def get_url(self, task_id: str):
        entry = self._load().get(task_id) or {}
        return entry.get('conversation_url')

    def record(self, task_id: str, request_id: str, decision_id: str, conversation_url: str):
        from gui_transport import validate_conversation_url
        canonical = validate_conversation_url(conversation_url)
        value = self._load()
        old = value.get(task_id) or {}
        value[task_id] = {
            'conversation_url': canonical,
            'created_request_id': old.get('created_request_id') or request_id,
            'last_request_id': request_id,
            'last_decision_id': decision_id,
            'updated_at': dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z'),
        }
        atomic_write_json(self.path, value)
        return canonical


    @classmethod
    def _role(cls, role: str) -> str:
        value = str(role or '').strip().upper()
        if value not in cls._ROLES:
            raise ValueError('ROLE_INVALID')
        return value

    @classmethod
    def _role_key(cls, mission_id: str, role: str) -> str:
        mission = str(mission_id or '').strip()
        if not mission:
            raise ValueError('MISSION_ID_EMPTY')
        return f'role:{mission}:{cls._role(role)}'

    def get_role_url(self, mission_id: str, role: str):
        key = self._role_key(mission_id, role)
        entry = self._load().get(key) or {}
        return entry.get('conversation_url')

    def record_role(self, mission_id: str, role: str, turn_id: str, conversation_url: str):
        from gui_transport import validate_conversation_url
        role = self._role(role)
        mission = str(mission_id or '').strip()
        key = self._role_key(mission, role)
        canonical = validate_conversation_url(conversation_url)
        value = self._load()
        old = value.get(key) or {}
        old_url = old.get('conversation_url')
        if old_url and old_url != canonical:
            raise ValueError('ROLE_CONVERSATION_CHANGED')
        prefix = f'role:{mission}:'
        for other_key, other in value.items():
            if other_key == key or not str(other_key).startswith(prefix):
                continue
            if isinstance(other, dict) and other.get('conversation_url') == canonical:
                raise ValueError('ROLE_CONVERSATION_NOT_DISTINCT')
        value[key] = {
            'mission_id': mission,
            'role': role,
            'conversation_url': canonical,
            'created_turn_id': old.get('created_turn_id') or turn_id,
            'last_turn_id': turn_id,
            'updated_at': dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z'),
        }
        atomic_write_json(self.path, value)
        return canonical


def _load_outbox(outbox_dir):
    result = []
    for path in sorted(Path(outbox_dir).glob('*.json')):
        try:
            value = _load_json(path)
            if isinstance(value, dict):
                result.append(value)
        except Exception:
            continue
    return result

class BridgeRuntime:
    def __init__(self, orchestrator_root, state_root, bridge_root,
                 github, gui_turn, trusted_actor, role_gui_turn=None, v3_runtime=None):
        self.orchestrator_root = Path(orchestrator_root)
        self.state_root = Path(state_root)
        self.bridge_root = Path(bridge_root)
        self.github = github
        self.gui_turn = gui_turn
        self.role_gui_turn = role_gui_turn
        self.v3_runtime = v3_runtime
        self.trusted_actor = trusted_actor
        self.ledger_path = self.bridge_root / 'ledger.json'
        self.health_path = self.bridge_root / 'health.json'
        self.conversations = ConversationRegistry(self.bridge_root / 'conversation-registry.json')
        self.role_relay = None
        if role_gui_turn is not None:
            from role_relay import RoleRelayRuntime
            self.role_relay = RoleRelayRuntime(self.bridge_root, self.conversations, role_gui_turn)

    def _persist_ledger(self, ledger):
        atomic_write_json(self.ledger_path, ledger)

    def _health(self, status, now, request_id=None, error=None):
        payload = {
            'protocol_version': 'scorp.gui-bridge/health-v1',
            'status': status,
            'heartbeat_at': now.isoformat(),
            'last_request_id': request_id,
            'error': error,
        }
        atomic_write_json(self.health_path, payload)
        return payload
    async def run_once(self, now=None):
        now = now or dt.datetime.now(dt.timezone.utc)
        registry = _load_json(self.orchestrator_root / 'registry.json', {'tasks': []})
        requests = _load_outbox(self.orchestrator_root / 'continuation-outbox')
        ledger = _load_json(self.ledger_path, {}) or {}
        selected = select_pending_request(registry, requests, ledger, now)
        if selected is None:
            if self.v3_runtime is not None:
                try:
                    v3_result = await self.v3_runtime.run_once()
                    v3_status = str(v3_result.get('status') or 'IDLE').upper()
                    if v3_status != 'IDLE':
                        self._health('V3_' + v3_status, now, v3_result.get('turn_id'))
                        return v3_result
                except Exception as exc:
                    self._health('ERROR', now, error=str(exc))
                    raise
            if self.role_relay is not None:
                try:
                    relay_result = await self.role_relay.run_once()
                    relay_status = relay_result.get('status', 'IDLE')
                    if relay_status == 'IDLE':
                        self._health('IDLE', now)
                    else:
                        self._health('ROLE_' + relay_status, now, relay_result.get('turn_id'))
                    return relay_result
                except Exception as exc:
                    self._health('ERROR', now, error=str(exc))
                    raise
            self._health('IDLE', now)
            return {'status': 'IDLE'}
        request, task = selected
        request_id = request['request_id']
        try:
            previous = load_previous_result(task, request, str(self.state_root))
            def refresh_task():
                live = _load_json(self.orchestrator_root / 'registry.json', {'tasks': []})
                matches = [t for t in live.get('tasks', []) if t.get('task_id') == task.get('task_id')]
                if len(matches) != 1:
                    raise ValueError('LIVE_TASK_LOOKUP_AMBIGUOUS')
                return matches[0]
            conversation_url = self.conversations.get_url(task.get('task_id'))
            result = await obtain_and_publish_decision(
                request, task, previous, self.github, self.gui_turn,
                ledger, self._persist_ledger, refresh_task, self.trusted_actor, conversation_url)
            returned_url = result.get('conversation_url')
            decision = result.get('decision') or {}
            if returned_url and decision.get('decision_id'):
                self.conversations.record(task['task_id'], request_id, decision['decision_id'], returned_url)
            self._health(result['status'], now, request_id)
            return result
        except Exception as exc:
            self._health('ERROR', now, request_id, str(exc))
            raise

import subprocess
import tempfile

class GitHubCli:
    def __init__(self, repo, runner=None, timeout=30):
        self.repo = repo
        self.runner = runner or subprocess.run
        self.timeout = timeout

    def _run(self, argv, input_text=None):
        cp = self.runner(argv, input=input_text, text=True,
                         capture_output=True, timeout=self.timeout)
        if cp.returncode != 0:
            detail = (cp.stderr or cp.stdout or '').strip()
            raise RuntimeError('GH_API_FAILED: ' + detail)
        try:
            return json.loads(cp.stdout or 'null')
        except Exception as exc:
            raise RuntimeError('GH_API_INVALID_JSON') from exc

    def list_comments(self, issue_number):
        comments = []
        page = 1
        while True:
            endpoint = f'/repos/{self.repo}/issues/{int(issue_number)}/comments?per_page=100&page={page}'
            value = self._run(['gh','api',endpoint])
            if not isinstance(value, list):
                raise RuntimeError('GH_COMMENTS_NOT_LIST')
            comments.extend(value)
            if len(value) < 100:
                return comments
            page += 1

    def post_comment(self, issue_number, body):
        endpoint = f'/repos/{self.repo}/issues/{int(issue_number)}/comments'
        payload = json.dumps({'body': body}, ensure_ascii=False, separators=(',', ':'))
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n', suffix='.json', delete=False) as handle:
                handle.write(payload)
                temp_path = handle.name
            return self._run(['gh','api','--method','POST',endpoint,'--input',temp_path])
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass

import argparse
import asyncio

async def run_daemon(
    runtime,
    poll_seconds=10,
    max_cycles=None,
    sleeper=asyncio.sleep,
    *,
    max_consecutive_failures=None,
    on_error=None,
):
    cycles = 0
    consecutive_failures = 0
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        try:
            await runtime.run_once()
            consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            if on_error is not None:
                on_error(exc, cycles)
            if max_consecutive_failures is not None and consecutive_failures >= max_consecutive_failures:
                return
        if max_cycles is not None and cycles >= max_cycles:
            break
        await sleeper(poll_seconds)


def build_v3_driver(transport, project_root, chrome_use_executable, timeout_seconds=30):
    value = str(transport or "").strip().lower()
    if value == "chrome-use":
        executable = str(chrome_use_executable or "").strip()
        if not executable:
            raise ValueError("CHROME_USE_EXECUTABLE_REQUIRED")
        from chrome_use_cli_v3 import ChromeUseCliV3
        from chrome_use_actor_driver_v3 import ChromeUseActorDriverV3
        cli = ChromeUseCliV3(executable=executable)
        return ChromeUseActorDriverV3(
            cli,
            Path(project_root) / "chrome-use-driver-v3.json",
            timeout_seconds=timeout_seconds,
        )
    if value == "windows-mcp":
        from windows_mcp_actor_driver_v3 import WindowsMcpActorDriverV3
        return WindowsMcpActorDriverV3()
    raise ValueError("V3_TRANSPORT_INVALID")


def build_runtime_from_args(args):
    from gui_transport import run_live_turn, run_role_turn
    from production_v3_runtime import ProductionV3Runtime
    gh = GitHubCli(args.control_repo)
    driver = build_v3_driver(
        args.v3_transport,
        args.v3_project_root,
        args.chrome_use_executable,
        timeout_seconds=getattr(args, 'v3_transport_timeout_seconds', 30),
    )
    v3_runtime = ProductionV3Runtime(
        args.v3_project_root,
        max_inflight=args.v3_max_inflight,
        max_workers=getattr(args, 'v3_max_workers', 4),
        driver=driver,
        adaptive_browser_poll=(str(args.v3_transport).strip().lower() == 'chrome-use'),
    )
    return BridgeRuntime(args.orchestrator_root, args.state_root, args.bridge_root,
                         gh, run_live_turn, args.trusted_actor, role_gui_turn=run_role_turn,
                         v3_runtime=v3_runtime)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--orchestrator-root', required=True)
    parser.add_argument('--state-root', required=True)
    parser.add_argument('--bridge-root', required=True)
    parser.add_argument('--control-repo', default='Scorp96/scorp-control-plane')
    parser.add_argument('--trusted-actor', default='Scorp96')
    parser.add_argument('--v3-project-root', default=r'C:\ScorpAgent\state-v3\active')
    parser.add_argument('--v3-max-inflight', type=int, default=5)
    parser.add_argument('--v3-max-workers', type=int, default=4)
    parser.add_argument('--v3-transport', choices=('chrome-use','windows-mcp'), default='chrome-use')
    parser.add_argument('--chrome-use-executable', default=r'C:\ScorpAgent\p0-transport-bakeoff\chrome-use\bin\chrome-use.exe')
    parser.add_argument('--v3-transport-timeout-seconds', type=int, default=30)
    parser.add_argument('--poll-seconds', type=int, default=10)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    process_lease = BridgeProcessLease(Path(args.bridge_root) / 'bridge-worker.lock')
    try:
        process_lease.acquire()
    except BridgeProcessAlreadyRunning:
        return 0
    try:
        runtime = build_runtime_from_args(args)
        if args.once:
            return asyncio.run(runtime.run_once())
        def report_error(exc, cycle):
            runtime._health(
                'ERROR',
                dt.datetime.now(dt.timezone.utc),
                error=f'{type(exc).__name__}: {exc}; consecutive_cycle={cycle}',
            )
        return asyncio.run(
            run_daemon(
                runtime,
                args.poll_seconds,
                max_consecutive_failures=3,
                on_error=report_error,
            )
        )
    finally:
        process_lease.release()

if __name__ == '__main__':
    main()
