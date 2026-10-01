#!/usr/bin/env python3
"""Idempotently provision the document translator in a running Open WebUI.

Sets up, through the authenticated admin API (nothing is written to the
database or the data volume directly):

  1. the workspace tool tools/document_translator.py (create or update), its
     valves (gateway URL and the shared translator API key), and a public read
     grant so every user can use it;
  2. the translator gateway as a native MCP tool server (bearer auth, shared
     key), exposing only the read-only helpers (capabilities, status, cancel);
  3. a "Document Translator" model preset: file context off, native function
     calling, built-in file tools off, both tools attached, public read access,
     and the base model registered with a public read grant (a non-admin user
     cannot use a preset whose base model has no registered row).

Reuses the helpers of davy_connection.py. Standard library only. Secrets are
never printed: only fixed status text is written to the terminal. Settings come
from Michael/.env (see .env.example): TRANSLATOR_GATEWAY_URL,
TRANSLATOR_API_KEY, TRANSLATOR_BASE_MODEL, optional TRANSLATOR_ID.
"""

import sys
import time

from davy_connection import MICHAEL_DIR, ApiError, call, get_token, load_env

TOOL_ID = 'document_translator'
TOOL_FILE = MICHAEL_DIR / 'tools' / 'document_translator.py'
MCP_CONN_ID = 'doctranslator'
MCP_EXPOSED_TOOLS = ['translation_capabilities', 'get_translation_status', 'cancel_translation']
MODEL_ID = 'document-translator'
MODEL_NAME = 'Document Translator'
PUBLIC_READ = [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]

SYSTEM_PROMPT = (
    'You translate documents the user attaches to the chat. The attached files are listed in an '
    '<attached_files> tag with an id for each file.\n'
    '- To translate an attachment, call translate_attachment with that id and the target language '
    'code (for example zh, en, fr, de, ja). Ask the user for the target language if it is not clear.\n'
    '- Never read, quote, summarize or re-type the document yourself, and never ask the user to paste '
    'its text. The tool reads the file on the server.\n'
    '- When the tool returns a download link, give the user that link exactly as returned.\n'
    '- If the tool reports an error, tell the user what it says. If it reports that the translation is '
    'still running, tell the user, and call deliver_translation with the job id when they ask again.\n'
    '- To list supported languages and formats, use the translator capabilities tool. Use the translator '
    'status or cancel tool only for a job id you were given.\n'
    "Answer briefly, in the user's language."
)


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


def upsert_mcp_connection(base, token, cfg):
    """Add/update the gateway connection by info.id; leave other connections alone. Returns (connection, changed)."""
    res = call(base, 'GET', '/api/v1/configs/tool_servers', token) or {}
    conns = list(res.get('TOOL_SERVER_CONNECTIONS') or [])
    want = {
        'type': 'mcp',
        'url': cfg['url'],
        'path': '',
        'auth_type': 'bearer',
        'key': cfg['key'],
        'headers': None,
        'config': {
            'enable': True,
            'function_name_filter_list': ','.join(MCP_EXPOSED_TOOLS),
            'access_grants': PUBLIC_READ,
        },
        'info': {
            'id': MCP_CONN_ID,
            'name': 'Document Translator gateway',
            'description': 'Translator status, cancel and capabilities. Translation itself runs through the Document Translator tool.',
        },
    }
    idx = next((i for i, c in enumerate(conns) if (c.get('info') or {}).get('id') == MCP_CONN_ID), None)
    if idx is None:
        conns.append(want)
        changed = True
    else:
        merged = {**conns[idx], **want, 'config': {**(conns[idx].get('config') or {}), **want['config']}}
        changed = merged != conns[idx]
        conns[idx] = merged
    if changed:
        call(base, 'POST', '/api/v1/configs/tool_servers', token, {'TOOL_SERVER_CONNECTIONS': conns})
    return want, changed


def verify_mcp(base, token, conn):
    res = call(base, 'POST', '/api/v1/configs/tool_servers/verify', token, conn)
    names = {s.get('name') for s in (res or {}).get('specs') or []}
    return sorted(set(MCP_EXPOSED_TOOLS) - names)


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


def upsert_preset(base, token, cfg):
    desired = {
        'id': MODEL_ID,
        'base_model_id': cfg['base_model'],
        'name': MODEL_NAME,
        'is_active': True,
        'access_grants': PUBLIC_READ,
        'meta': {
            'description': 'Translates attached documents and returns the translated file.',
            'capabilities': {'file_context': False, 'file_upload': True, 'builtin_tools': True},
            'builtinTools': {'files': False, 'knowledge': False, 'time': False, 'user_input': False},
            'toolIds': [f'server:mcp:{MCP_CONN_ID}', TOOL_ID],
        },
        'params': {'function_calling': 'native', 'system': SYSTEM_PROMPT},
    }
    current = get_model(base, token, MODEL_ID)
    if current is None:
        call(base, 'POST', '/api/v1/models/create', token, desired)
        return True
    meta, params = current.get('meta') or {}, current.get('params') or {}
    same = (
        current.get('base_model_id') == desired['base_model_id']
        and current.get('name') == desired['name']
        and current.get('is_active') is True
        and ('user', '*', 'read') in grants_of(current)
        and all(meta.get(k) == v for k, v in desired['meta'].items())
        and all(params.get(k) == v for k, v in desired['params'].items())
    )
    if same:
        return False
    call(
        base,
        'POST',
        '/api/v1/models/model/update',
        token,
        {**desired, 'meta': {**meta, **desired['meta']}, 'params': {**params, **desired['params']}},
    )
    return True


def preset_visible(base, token, attempts=5):
    """A freshly created preset can briefly be missing from the model list."""
    for _ in range(attempts):
        res = call(base, 'GET', '/api/models', token) or {}
        if any(m.get('id') == MODEL_ID for m in res.get('data') or []):
            return True
        time.sleep(2)
    return False


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

        conn, changed = upsert_mcp_connection(base, token, cfg)
        report(True, 'MCP tool server ' + ('added/updated' if changed else 'already up to date'))
        try:
            missing = verify_mcp(base, token, conn)
            report(
                not missing,
                'MCP tool server verifies and lists '
                + (
                    'all expected tools'
                    if not missing
                    else f'no {len(missing)} expected tool(s): ' + ', '.join(missing)
                ),
            )
        except ApiError as e:
            report(
                False,
                f'MCP tool server verification failed: {e} (check the gateway URL is reachable from the Open WebUI container, and the key)',
            )

        base_changed = register_base_model(base, token, cfg['base_model'])
        report(
            True,
            'base model '
            + ('registered with public read grant' if base_changed else 'already registered with public read grant'),
        )
        report(
            True,
            'translator preset ' + ('created/updated' if upsert_preset(base, token, cfg) else 'already up to date'),
        )
        report(preset_visible(base, token), 'translator preset is listed by Open WebUI')
    except ApiError as e:
        report(False, str(e))

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
