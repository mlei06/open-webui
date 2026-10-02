#!/usr/bin/env python3
"""Idempotently provision the document translator in a running Open WebUI.

Sets up, through the authenticated admin API (nothing is written to the
database or the data volume directly):

  1. the workspace tool tools/document_translator.py (create or update), its
     valves (gateway URL and the shared translator API key; stored encrypted at
     rest when the server runs with ENABLE_VALVE_ENCRYPTION=true, which
     docker-compose.yaml sets), and a public read grant so every user can use it;
  2. the base model registered with a public read grant (a non-admin user
     cannot use a preset whose base model has no registered row).

The "Document Translator" model preset (and the other presets) is owned by presets.py
from models/presets.json; it attaches this tool and the `doctranslator` connection. The
translator gateway MCP tool server itself is registered by mcp_servers.py from
mcp/mcp.json, which is the single owner of tool server registration. This script reports
whether that connection is registered yet.

Reuses the helpers of davy_connection.py. Standard library only. Secrets are
never printed: only fixed status text is written to the terminal. Settings come
from Michael/.env (see .env.example): TRANSLATOR_GATEWAY_URL,
TRANSLATOR_API_KEY, TRANSLATOR_BASE_MODEL, optional TRANSLATOR_ID.
"""

import sys

from davy_connection import MICHAEL_DIR, ApiError, call, get_token, load_env

TOOL_ID = 'document_translator'
TOOL_FILE = MICHAEL_DIR / 'tools' / 'document_translator.py'
MCP_CONN_ID = 'doctranslator'  # registered by mcp_servers.py (mcp/mcp.json); presets.py attaches it
PUBLIC_READ = [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]


def settings(env):
    url = (env.get('TRANSLATOR_GATEWAY_URL') or '').strip()
    key = (env.get('TRANSLATOR_API_KEY') or '').strip()
    base_model = (env.get('TRANSLATOR_BASE_MODEL') or '').strip()
    missing = [
        n
        for n, v in (
            ('TRANSLATOR_GATEWAY_URL', url),
            ('TRANSLATOR_API_KEY', key),
            ('TRANSLATOR_BASE_MODEL', base_model),
        )
        if not v
    ]
    if missing:
        raise ApiError('missing in Michael/.env: ' + ', '.join(missing))
    if not url.startswith(('http://', 'https://')):
        raise ApiError('TRANSLATOR_GATEWAY_URL must be an http(s) URL')
    return {'url': url, 'key': key, 'base_model': base_model, 'translator_id': (env.get('TRANSLATOR_ID') or '').strip()}


def upsert_tool(base, token, cfg):
    """Create or update the workspace tool and its valves. Returns (tool_changed, valves_changed)."""
    source = TOOL_FILE.read_text()
    form = {
        'id': TOOL_ID,
        'name': 'Document Translator',
        'content': source,
        'meta': {'description': 'Translate a chat attachment and return the translated file as a download.'},
        'access_grants': PUBLIC_READ,
    }
    tools = call(base, 'GET', '/api/v1/tools/', token) or []
    existing = next((t for t in tools if t.get('id') == TOOL_ID), None)
    changed = False
    if existing is None:
        call(base, 'POST', '/api/v1/tools/create', token, form)
        changed = True
    else:
        current = call(base, 'GET', f'/api/v1/tools/id/{TOOL_ID}', token) or {}
        grants = {
            (g.get('principal_type'), g.get('principal_id'), g.get('permission'))
            for g in current.get('access_grants') or []
        }
        if current.get('content') != source or ('user', '*', 'read') not in grants:
            call(base, 'POST', f'/api/v1/tools/id/{TOOL_ID}/update', token, form)
            changed = True

    desired = {'GATEWAY_URL': cfg['url'], 'TRANSLATOR_API_KEY': cfg['key']}
    if cfg['translator_id']:
        desired['TRANSLATOR_ID'] = cfg['translator_id']
    valves = call(base, 'GET', f'/api/v1/tools/id/{TOOL_ID}/valves', token) or {}
    merged = {**valves, **desired}
    valves_changed = merged != valves
    if valves_changed:
        call(base, 'POST', f'/api/v1/tools/id/{TOOL_ID}/valves/update', token, merged)
    return changed, valves_changed


def mcp_connection_registered(base, token):
    """True when the gateway connection exists (registering it is mcp_servers.py's job)."""
    res = call(base, 'GET', '/api/v1/configs/tool_servers', token) or {}
    return any((c.get('info') or {}).get('id') == MCP_CONN_ID for c in res.get('TOOL_SERVER_CONNECTIONS') or [])


def grants_of(model):
    return {
        (g.get('principal_type'), g.get('principal_id'), g.get('permission'))
        for g in (model or {}).get('access_grants') or []
    }


def get_model(base, token, model_id):
    try:
        return call(base, 'GET', f'/api/v1/models/model?id={model_id}', token)
    except ApiError as e:
        if 'HTTP 404' in str(e):
            return None
        raise


def register_base_model(base, token, base_model):
    """Give the base model a registered row with a public read grant, keeping any existing grants."""
    current = get_model(base, token, base_model)
    if current is not None and ('user', '*', 'read') in grants_of(current):
        return False
    keep = (current or {}).get('access_grants') or []
    call(
        base,
        'POST',
        '/api/v1/models/model/access/update',
        token,
        {'id': base_model, 'name': (current or {}).get('name') or base_model, 'access_grants': [*keep, *PUBLIC_READ]},
    )
    return True


def main():
    env = load_env()
    base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
    ok = True

    def report(passed, msg):
        nonlocal ok
        ok = ok and passed
        print(f'[{"PASS" if passed else "FAIL"}] {msg}')

    try:
        cfg = settings(env)
    except ApiError as e:
        print(f'[FAIL] {e}')
        return 1

    try:
        token = get_token(env, base)
        report(True, f'authenticated as admin at {base}')

        tool_changed, valves_changed = upsert_tool(base, token, cfg)
        report(True, 'workspace tool ' + ('created/updated' if tool_changed else 'already up to date'))
        report(True, 'tool valves ' + ('updated' if valves_changed else 'already up to date'))

        if mcp_connection_registered(base, token):
            report(True, f'MCP tool server "{MCP_CONN_ID}" is registered (managed by mcp_servers.py)')
        else:
            print(f'[NOTE] MCP tool server "{MCP_CONN_ID}" is not registered yet: run bootstrap/mcp_servers.py (the presets attach it)')

        base_changed = register_base_model(base, token, cfg['base_model'])
        report(
            True,
            'base model '
            + ('registered with public read grant' if base_changed else 'already registered with public read grant'),
        )
        print('[NOTE] model presets (including Document Translator) are created by bootstrap/presets.py')
    except ApiError as e:
        report(False, str(e))

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
