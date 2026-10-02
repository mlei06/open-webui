#!/usr/bin/env python3
"""Idempotently import the Knowledge Base Manager tool into a running Open WebUI.

tools/knowledge_base_manager.json is an Open WebUI tool export (a JSON list holding one tool:
id, name, meta and the Python source). Through the authenticated admin API this script creates
or updates that workspace tool (the source and description are compared, so a re-run with no
change writes nothing) and gives every user a public read grant so the Knowledge Base Manager
preset works for everyone.

The tool calls Open WebUI's own API with the signed-in user's token, so it can only do what that
user may do: it lists and edits the user's own knowledge bases and the ones shared with write
access. It needs no valves and stores no secrets. The model preset that uses it is declared in
models/presets.json and created by presets.py.

Option: --check changes nothing and exits 1 when the tool is missing or differs. Reuses the
helpers of davy_connection.py. Standard library only. Nothing secret is printed.
"""

import argparse
import json
import sys

from davy_connection import MICHAEL_DIR, ApiError, call, get_token, load_env

TOOL_FILE = MICHAEL_DIR / 'tools' / 'knowledge_base_manager.json'
PUBLIC_READ = [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]


def load_tool(path=TOOL_FILE):
    """The tool form to send to Open WebUI. Raises ApiError (secret-free) when the export is unusable."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        raise ApiError(f'{path.name} is missing or not valid JSON') from None
    tool = doc[0] if isinstance(doc, list) and len(doc) == 1 else doc
    if not (isinstance(tool, dict) and all(isinstance(tool.get(k), str) and tool[k].strip() for k in ('id', 'name', 'content'))):
        raise ApiError(f'{path.name}: expected an Open WebUI tool export with id, name and content')
    if 'class Tools' not in tool['content']:
        raise ApiError(f'{path.name}: the source has no Tools class')
    meta = tool.get('meta') if isinstance(tool.get('meta'), dict) else {}
    return {'id': tool['id'], 'name': tool['name'], 'content': tool['content'], 'meta': meta, 'access_grants': PUBLIC_READ}


def grants_of(tool):
    return {(g.get('principal_type'), g.get('principal_id'), g.get('permission')) for g in (tool or {}).get('access_grants') or []}


def state(base, token, form):
    """'missing', 'differs' or 'current'."""
    tools = call(base, 'GET', '/api/v1/tools/', token) or []
    if not any(t.get('id') == form['id'] for t in tools):
        return 'missing'
    live = call(base, 'GET', f'/api/v1/tools/id/{form["id"]}', token) or {}
    same = (
        live.get('content') == form['content']
        and live.get('name') == form['name']
        and (live.get('meta') or {}).get('description') == form['meta'].get('description')
        and ('user', '*', 'read') in grants_of(live)
    )
    return 'current' if same else 'differs'


def run(argv=None):
    ap = argparse.ArgumentParser(description='Import the Knowledge Base Manager tool into Open WebUI.')
    ap.add_argument('--check', action='store_true', help='change nothing; exit 1 if the tool is missing or differs')
    args = ap.parse_args(argv)
    ok = True

    def report(passed, msg):
        nonlocal ok
        ok = ok and passed
        print(f'[{"PASS" if passed else "FAIL"}] {msg}')

    env = load_env()
    base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
    try:
        form = load_tool()
        report(True, f'tool export is valid (id {form["id"]})')
        token = get_token(env, base)
        report(True, f'authenticated as admin at {base}')
        current = state(base, token, form)
        if current == 'current':
            report(True, 'workspace tool already up to date')
        elif args.check:
            report(False, f'workspace tool is {current} (run without --check)')
        else:
            if current == 'missing':
                call(base, 'POST', '/api/v1/tools/create', token, form)
            else:
                call(base, 'POST', f'/api/v1/tools/id/{form["id"]}/update', token, form)
            report(state(base, token, form) == 'current', f'workspace tool {"created" if current == "missing" else "updated"}')
        print('[NOTE] the Knowledge Base Manager preset is created by bootstrap/presets.py')
    except ApiError as e:
        report(False, str(e))

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(run())
