#!/usr/bin/env python3
"""End-to-end proof on a THROWAWAY stack: everything is visible and usable by non-admin users.

Run it after `provision.py` against a stack with its own compose project name, host port and volume
(it refuses port 3000 and the project name "michael"), with MICHAEL_ENV_FILE pointing at that
stack's env file and the MOCK mail provider (no mail is ever sent):

    MICHAEL_ENV_FILE=/path/to/throwaway.env python3 Michael/tests/stack_e2e.py [--skip-mail]

Accounts are made the way production does it, through the admin path (POST /api/v1/auths/add), never
by sign-up. It then, as ordinary (non-admin) users:

  * confirms an unauthenticated sign-up is refused;
  * lists the model selector and the tools: every preset, workspace tool and tool server is there;
  * chats through every preset (the answer comes through the real tool loop of the web UI, using
    tests/socket_chat.py inside the container) and uses a tool where the preset has one;
  * proves privacy: another user's private knowledge base and file are not visible, the user can add
    to the SOPs knowledge base, and the Knowledge Base Manager tool deletes nothing unless the user
    confirms in the dialog (answered "no" here, then "yes");
  * mail (mock provider): Lenny drafts through the mail tools, the draft is sent through the mail
    service REST API with the user's own session token and an attachment, and the recorded message
    comes from the user's company address, or lenny@lenovo.com for a user without one.

Prints PASS/FAIL lines and exits 1 on any failure. Throwaway stacks only; secrets are never printed.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'bootstrap'))
from davy_connection import ApiError, call, get_token, load_env  # noqa: E402
from knowledge_bases import list_knowledge_bases  # noqa: E402
from presets import load_presets  # noqa: E402

ok_all = True


def report(ok, msg):
    global ok_all
    ok_all = ok_all and ok
    print(f'[{"PASS" if ok else "FAIL"}] {msg}')


def raw_status(base, method, path, token=None, body=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


class Stack:
    def __init__(self, env):
        self.env = env
        self.base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
        self.project = env.get('COMPOSE_PROJECT_NAME') or os.environ.get('COMPOSE_PROJECT_NAME') or 'michael'
        self.container = f'{self.project}-open-webui-1'
        self.mail_container = f'{self.project}-mail-service-1'

    def chat(self, user, model, prompt, confirm='no', wait=240, want_tool=None, tries=2):
        """Result dict of a UI-style chat; retried once when a tool the prompt demands was not called (model variance)."""
        for _ in range(tries):
            res = self.chat_once(user, model, prompt, confirm, wait)
            if want_tool is None or any(t.startswith(want_tool) for t in res['tools']):
                break
        return res

    def chat_once(self, user, model, prompt, confirm='no', wait=240):
        cmd = ['docker', 'exec', '-i', '-e', 'BASE=http://127.0.0.1:8080', '-e', f'TOKEN={user["token"]}', '-e', f'CONFIRM={confirm}', '-e', f'WAIT={wait}', self.container, 'python', '-', model, prompt]
        out = subprocess.run(cmd, input=(HERE / 'socket_chat.py').read_text(), capture_output=True, text=True, timeout=wait + 120)
        try:
            return json.loads(out.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {'text': '', 'tools': [], 'confirmations': [], 'timeout': True, 'error': out.stderr[-300:]}

    def rest(self, user, method, path, body=None, filename=None):
        """Call the mail service REST API from inside the Open WebUI container with the user's own session token."""
        cmd = ['docker', 'exec', '-i', self.container, 'python', '-c', MAIL_REST, user['token'], method, path]
        if body is not None or filename:
            cmd.append(json.dumps(body))
        if filename:
            cmd.append(filename)
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return json.loads(out.stdout)


MAIL_REST = r'''
import json, sys, httpx
tok, method, path = sys.argv[1], sys.argv[2], sys.argv[3]
body = json.loads(sys.argv[4]) if len(sys.argv) > 4 else None
files = None
if len(sys.argv) > 5:
    files = {'file': (sys.argv[5], b'synthetic attachment for the e2e proof\n', 'text/plain')}
r = httpx.request(method, 'http://mail-service:8000' + path, headers={'Authorization': 'Bearer ' + tok}, json=body if files is None else None, files=files, timeout=60)
print(json.dumps({'status': r.status_code, 'body': r.json() if r.headers.get('content-type', '').startswith('application/json') else r.text[:200]}))
'''


def call_upload(base, token, filename, text):
    """Upload a small text file as `token` (no knowledge base, no processing); returns the file id."""
    boundary = secrets.token_hex(8)
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: text/plain\r\n\r\n{text}\r\n--{boundary}--\r\n').encode()
    req = urllib.request.Request(base + '/api/v1/files/?process=false', data=body, method='POST', headers={'Authorization': f'Bearer {token}', 'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)['id']


def make_user(stack, admin, name, email):
    """Create (replace) an ordinary user through the admin path and sign in."""
    for u in (call(stack.base, 'GET', '/api/v1/users/all', admin) or {}).get('users', []):
        if u.get('email') == email:
            call(stack.base, 'DELETE', f'/api/v1/users/{u["id"]}', admin)
    password = secrets.token_urlsafe(12)
    call(stack.base, 'POST', '/api/v1/auths/add', admin, {'name': name, 'email': email, 'password': password, 'role': 'user'})
    res = call(stack.base, 'POST', '/api/v1/auths/signin', body={'email': email, 'password': password})
    return {'email': email, 'password': password, 'token': res['token'], 'role': res['role'], 'id': res['id']}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--skip-mail', action='store_true')
    ap.add_argument('--skip-chat', action='store_true', help='skip the model chats (API checks only)')
    args = ap.parse_args()
    env = load_env()
    stack = Stack(env)
    if stack.base.endswith(':3000') or stack.project == 'michael':
        print('[FAIL] refusing to run against the live stack (port 3000 / project michael)')
        return 1
    doc, presets = load_presets()
    admin = get_token(env, stack.base)
    report(True, f'admin session at {stack.base} (project {stack.project})')

    cfg = call(stack.base, 'GET', '/api/v1/auths/admin/config', admin)
    report(cfg.get('ENABLE_SIGNUP') is False and cfg.get('DEFAULT_USER_ROLE') == 'user', 'sign-up is closed and the default role is user')
    code = raw_status(stack.base, 'POST', '/api/v1/auths/signup', None, {'name': 'Intruder', 'email': 'intruder@e2e.test', 'password': secrets.token_urlsafe(12)})
    report(code == 403, f'an unauthenticated sign-up request is refused (HTTP {code})')

    tag = secrets.token_hex(3)  # fresh addresses every run: sign-in is rate limited per address
    plain = make_user(stack, admin, 'Plain User', f'plain.user.{tag}@e2e.test')
    jane = make_user(stack, admin, 'Jane Doe', f'jane.doe.{tag}@lenovo.com')
    report(plain['role'] == 'user' and jane['role'] == 'user', 'accounts made by the admin path are ordinary users (not admins)')

    # --- visibility: model selector and tools -------------------------------------------------
    models = {m['id'] for m in call(stack.base, 'GET', '/api/models', plain['token'])['data']}
    for p in presets:
        report(p['id'] in models, f'preset {p["name"]} is in a non-admin user\'s model selector')
    visible = {t.get('id') for t in call(stack.base, 'GET', '/api/v1/tools/', plain['token']) or []}
    for ref in sorted({r['tool'] for p in presets for r in p['tools'] if 'tool' in r}):
        report(ref in visible, f'tool {ref} is usable by a non-admin user')
    for ref in sorted({r['server'] for p in presets for r in p['tools'] if 'server' in r}):
        report(f'server:mcp:{ref}' in visible, f'tool server {ref} is usable by a non-admin user')
    ids = {m['id']: m for m in call(stack.base, 'GET', '/api/models', plain['token'])['data']}
    lenny_tools = set(((ids['lenny'].get('info') or {}).get('meta') or {}).get('toolIds', []))
    report(lenny_tools >= {f'server:mcp:{s}' for s in ('doctranslator', 'employee_directory', 'mail')} | {'document_translator', 'generate_slide_pptx', 'generate_docx_documents', 'knowledge_base_manager'}, 'Lenny carries every tool')

    # --- knowledge: read, write, privacy ------------------------------------------------------
    kbs = list_knowledge_bases(stack.base, plain['token'])
    sops = next((k for k in kbs if k.get('name') == 'SOPs'), None)
    report(sops is not None, 'the SOPs knowledge base is listed for a non-admin user')
    private = call(stack.base, 'POST', '/api/v1/knowledge/create', admin, {'name': 'E2E admin private', 'description': 'private to the admin', 'access_grants': []})
    names = {k.get('name') for k in list_knowledge_bases(stack.base, plain['token'])}
    report('E2E admin private' not in names, "another user's private knowledge base is not listed")
    report(raw_status(stack.base, 'GET', f'/api/v1/knowledge/{private["id"]}', plain['token']) in (401, 403, 404), "another user's private knowledge base cannot be opened")
    call(stack.base, 'DELETE', f'/api/v1/knowledge/{private["id"]}/delete', admin)
    if sops:
        from knowledge_bases import upload

        fid = upload(stack.base, plain['token'], sops['id'], 'e2e-user-note.md', '# E2E note\nAdded by an ordinary user.\n')
        report(bool(fid), 'a non-admin user can add a file to the SOPs knowledge base (write access)')
        if fid:
            call(stack.base, 'POST', f'/api/v1/knowledge/{sops["id"]}/file/remove', plain['token'], {'file_id': fid})
            raw_status(stack.base, 'DELETE', f'/api/v1/files/{fid}', plain['token'])  # removing it from the base may already delete it
        secret = call_upload(stack.base, admin, 'admin-private.txt', 'private to the admin')
        report(raw_status(stack.base, 'GET', f'/api/v1/files/{secret}/content', plain['token']) in (401, 403, 404), "another user's private file cannot be read")
        call(stack.base, 'DELETE', f'/api/v1/files/{secret}', admin)

    if args.skip_chat:
        return 0 if ok_all else 1

    # --- chats ----------------------------------------------------------------------------------
    for p in presets:
        res = stack.chat(plain, p['id'], 'Reply with exactly one word: PONG')
        report(bool(res['text'].strip()) and not res['timeout'], f'chat through {p["name"]} answers as a non-admin user ({res["text"][:40]!r})')
    res = stack.chat(plain, 'lenny', 'According to the SOPs knowledge base, who owns the PATH package tracking process? Answer in one short sentence.')
    report('knowledge' in ' '.join(res['tools']).lower() or 'Michael' in res['text'], f'Lenny answers from the SOPs knowledge base ({res["text"][:80]!r})')
    res = stack.chat(plain, 'web-searcher', 'Search the web: what is the capital of Japan? One sentence with a source.')
    report(not res['timeout'] and 'Tokyo' in res['text'], f'Web Searcher answers a web question ({len(res["tools"])} tool call(s))')
    res = stack.chat(plain, 'office-agent', 'Find "Smith" in the employee directory and tell me what the directory says.', want_tool='employee_directory')
    report('employee_directory_search_employees' in res['tools'], 'Office Agent calls the employee directory tool as a non-admin user')

    # --- knowledge base manager: deletion needs the user's own confirmation --------------------
    mine = call(stack.base, 'POST', '/api/v1/knowledge/create', plain['token'], {'name': 'E2E my notes', 'description': 'scratch', 'access_grants': []})
    prompt = f'Delete my knowledge base named "E2E my notes" (id {mine["id"]}). I want it deleted; confirm=true.'
    res = stack.chat(plain, 'knowledge-base-manager', prompt, confirm='no', want_tool='delete_knowledge')
    still = raw_status(stack.base, 'GET', f'/api/v1/knowledge/{mine["id"]}', plain['token']) == 200
    report(bool(res['confirmations']) and still, f'declining the confirmation dialog deletes nothing (dialogs shown: {len(res["confirmations"])})')
    res = stack.chat(plain, 'knowledge-base-manager', prompt, confirm='yes', want_tool='delete_knowledge')
    gone = raw_status(stack.base, 'GET', f'/api/v1/knowledge/{mine["id"]}', plain['token']) != 200
    report(bool(res['confirmations']) and gone, 'confirming the dialog deletes the knowledge base')
    if not gone:
        call(stack.base, 'DELETE', f'/api/v1/knowledge/{mine["id"]}/delete', plain['token'])

    if args.skip_mail:
        return 0 if ok_all else 1

    # --- mail (mock provider) ------------------------------------------------------------------
    for user, expect in ((jane, jane['email']), (plain, 'lenny@lenovo.com')):
        subject = f'E2E {secrets.token_hex(3)}'
        res = stack.chat(user, 'lenny', f'Draft an email to alice@lenovo.com with the subject "{subject}" and the body "Proof run, please ignore." Use your mail tools and do not ask me anything.', want_tool='mail_')
        report(any(t.startswith('mail_') for t in res['tools']), f'Lenny drafts through the mail tools for {user["email"]} ({", ".join(res["tools"]) or "no tool call"})')
        drafts = stack.rest(user, 'GET', '/api/drafts')['body'].get('drafts', [])
        draft = next((d for d in drafts if d.get('subject') == subject), None)
        report(draft is not None, 'the draft exists for the signed-in user')
        if not draft:
            continue
        who = stack.rest(user, 'GET', '/api/whoami')['body']
        report(who.get('from') == expect, f'the sender for {user["email"]} is {who.get("from")}')
        up = stack.rest(user, 'POST', f'/api/drafts/{draft["id"]}/attachments', filename='proof.txt')
        report(up['status'] == 201, f'an attachment uploads to the draft (HTTP {up["status"]})')
        cur = stack.rest(user, 'GET', f'/api/drafts/{draft["id"]}')['body']
        send = stack.rest(user, 'POST', f'/api/drafts/{draft["id"]}/send', {'to': cur['to'], 'cc': cur.get('cc', []), 'subject': cur['subject'], 'body': cur['body'], 'version': cur['version']})
        report(send['status'] == 200, f'the user\'s own session sends the draft (HTTP {send["status"]})')
        time.sleep(1)
        rec = subprocess.run(['docker', 'exec', stack.mail_container, 'sh', '-c', 'cat /data/mock_emails.jsonl'], capture_output=True, text=True).stdout.splitlines()
        sent = [json.loads(x) for x in rec if subject in x]
        text = json.dumps(sent[-1]) if sent else ''
        report(bool(sent) and expect in text and 'proof.txt' in text, f'the recorded message is from {expect} with the attachment (mock provider, nothing was sent)')
    return 0 if ok_all else 1


if __name__ == '__main__':
    try:
        code = main()
    except ApiError as e:
        print(f'[FAIL] {e}')
        code = 1
    print('RESULT: ' + ('PASS' if code == 0 and ok_all else 'FAIL'))
    sys.exit(0 if code == 0 and ok_all else 1)
