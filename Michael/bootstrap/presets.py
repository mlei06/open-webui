#!/usr/bin/env python3
"""Idempotently create the Open WebUI model presets declared in models/presets.json.

Each preset (Lenny, Document Translator, Web Searcher, Office Agent) is a custom model
with its system prompt (prompts/<name>.md), the tool servers and workspace tools it may
reach, capabilities, built-in tools, default features and the user-context filter
already filled out. Through the authenticated admin API this script:

  1. validates models/presets.json and reads every prompt file;
  2. registers the base model with a public read grant (a non-admin user cannot use a
     preset whose base model has no registered row);
  3. creates or updates each preset, touching only the settings the file declares and
     writing nothing when the live model already matches;
  4. checks that the tool servers and tools the presets name are registered. An
     unregistered one (for example the mail server before mail-service is up) is a NOTE,
     not a failure: registration belongs to mcp_servers.py and translator_tool.py, and
     the preset works as soon as the connection exists.

Base model: --base-model, else PRESETS_BASE_MODEL, else TRANSLATOR_BASE_MODEL from
Michael/.env, else "base_model" in models/presets.json (gemma-4-31b-it).

Options: --check changes nothing and exits 1 when a preset differs; --base-model ID.
Reuses the helpers of davy_connection.py. Standard library only. Secrets are never
printed: only ids, counts and fixed status text are written.
"""

import argparse
import json
import sys
import time

from davy_connection import MICHAEL_DIR, ApiError, call, get_token, load_env

PRESETS_JSON = MICHAEL_DIR / 'models' / 'presets.json'
PROMPT_DIR = MICHAEL_DIR / 'prompts'
PUBLIC_READ = [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]
# Every built-in tool category Open WebUI knows; a preset enables only the ones it lists.
BUILTIN_CATEGORIES = (
    'time', 'user_input', 'memory', 'chats', 'notes', 'knowledge', 'files', 'channels', 'notifications',
    'web_search', 'image_generation', 'code_interpreter', 'tasks', 'automations', 'calendar', 'subagents',
)  # fmt: skip
FEATURES = ('web_search', 'image_generation', 'code_interpreter')
# meta/params keys this script owns; every other key of a live model is left alone.
META_KEYS = ('description', 'capabilities', 'builtinTools', 'toolIds', 'filterIds', 'defaultFeatureIds')


class ConfigError(Exception):
    """A problem in models/presets.json or a prompt file; the message is secret-free."""


def tool_id(ref):
    """The Open WebUI tool id for a {"server": id} or {"tool": id} reference."""
    return f'server:mcp:{ref["server"]}' if 'server' in ref else ref['tool']


def load_presets(path=PRESETS_JSON, prompt_dir=PROMPT_DIR):
    """Parse and validate the declaration; returns (doc, presets with their prompt text). Raises ConfigError."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        raise ConfigError(f'{path.name} is missing or not valid JSON') from None
    errors = []
    if not isinstance(doc, dict) or doc.get('schemaVersion') != 1:
        raise ConfigError(f'{path.name}: schemaVersion must be 1')
    if not isinstance(doc.get('base_model'), str) or not doc['base_model'].strip():
        errors.append('base_model must be a non-empty string')
    filters = doc.get('filter_ids')
    if not (isinstance(filters, list) and all(isinstance(f, str) and f for f in filters)):
        errors.append('filter_ids must be a list of function ids')
    presets = doc.get('presets')
    if not (isinstance(presets, list) and presets):
        raise ConfigError(f'{path.name}: presets must be a non-empty list')
    seen, out = set(), []
    for i, p in enumerate(presets):
        pid = p.get('id') if isinstance(p, dict) else None
        where = f'presets[{i}]' + (f' ({pid})' if isinstance(pid, str) else '')
        if not (isinstance(pid, str) and pid):
            errors.append(f'{where}: missing id')
            continue
        if pid in seen:
            errors.append(f'{where}: duplicate id')
        seen.add(pid)
        for key in ('name', 'description', 'prompt'):
            if not (isinstance(p.get(key), str) and p[key].strip()):
                errors.append(f'{where}: {key} must be a non-empty string')
        if not isinstance(p.get('capabilities'), dict) or not all(isinstance(v, bool) for v in p['capabilities'].values()):
            errors.append(f'{where}: capabilities must map names to booleans')
        for key, allowed in (('builtin_tools', BUILTIN_CATEGORIES), ('default_features', FEATURES)):
            vals = p.get(key)
            if not (isinstance(vals, list) and all(v in allowed for v in vals)):
                errors.append(f'{where}: {key} must be a list drawn from {", ".join(allowed)}')
        refs = p.get('tools')
        if not (isinstance(refs, list) and all(isinstance(r, dict) and len({'server', 'tool'} & set(r)) == 1 for r in refs)):
            errors.append(f'{where}: tools must be a list of {{"server": id}} or {{"tool": id}}')
        if not isinstance(p.get('params'), dict):
            errors.append(f'{where}: params must be an object')
        if p.get('capabilities', {}).get('web_search') is not True and (
            'web_search' in p.get('default_features', []) or 'web_search' in p.get('builtin_tools', [])
        ):
            errors.append(f'{where}: web_search is used but the web_search capability is off')
        try:
            text = (prompt_dir / p['prompt']).read_text().strip()
        except (OSError, KeyError, TypeError):
            errors.append(f'{where}: cannot read prompt file {p.get("prompt")}')
            continue
        if not text:
            errors.append(f'{where}: prompt file is empty')
        out.append({**p, 'system': text})
    if errors:
        raise ConfigError(f'{path.name}: ' + '; '.join(errors))
    return doc, out


def desired_model(preset, base_model, filter_ids):
    """The model form Open WebUI should hold for a preset."""
    return {
        'id': preset['id'],
        'base_model_id': base_model,
        'name': preset['name'],
        'is_active': True,
        'access_grants': PUBLIC_READ,
        'meta': {
            'description': preset['description'],
            'capabilities': preset['capabilities'],
            'builtinTools': {c: c in preset['builtin_tools'] for c in BUILTIN_CATEGORIES},
            'toolIds': [tool_id(r) for r in preset['tools']],
            'filterIds': list(filter_ids),
            'defaultFeatureIds': list(preset['default_features']),
        },
        'params': {'function_calling': 'native', 'system': preset['system'], **preset['params']},
    }


def grants_of(model):
    return {
        (g.get('principal_type'), g.get('principal_id'), g.get('permission'))
        for g in (model or {}).get('access_grants') or []
    }


def matches(current, want):
    """True when the live model already carries everything the declaration sets."""
    meta, params = current.get('meta') or {}, current.get('params') or {}
    return (
        current.get('base_model_id') == want['base_model_id']
        and current.get('name') == want['name']
        and current.get('is_active') is True
        and ('user', '*', 'read') in grants_of(current)
        and all(meta.get(k) == want['meta'][k] for k in META_KEYS)
        and all(params.get(k) == v for k, v in want['params'].items())
    )


def get_model(base, token, model_id):
    try:
        return call(base, 'GET', f'/api/v1/models/model?id={model_id}', token)
    except ApiError as e:
        if 'HTTP 404' in str(e):
            return None
        raise


def base_model_state(base, token, base_model):
    """True when the base model already has a registered row with a public read grant."""
    return ('user', '*', 'read') in grants_of(get_model(base, token, base_model))


def register_base_model(base, token, base_model):
    """Give the base model a public read grant, keeping any existing grants."""
    current = get_model(base, token, base_model)
    keep = (current or {}).get('access_grants') or []
    call(
        base,
        'POST',
        '/api/v1/models/model/access/update',
        token,
        {'id': base_model, 'name': (current or {}).get('name') or base_model, 'access_grants': [*keep, *PUBLIC_READ]},
    )


def upsert(base, token, want, apply):
    """Return 'created', 'updated' or 'unchanged' (and write it when apply is true)."""
    current = get_model(base, token, want['id'])
    if current is None:
        if apply:
            call(base, 'POST', '/api/v1/models/create', token, want)
        return 'created'
    if matches(current, want):
        return 'unchanged'
    if apply:
        meta, params = current.get('meta') or {}, current.get('params') or {}
        call(
            base,
            'POST',
            '/api/v1/models/model/update',
            token,
            {**want, 'meta': {**meta, **want['meta']}, 'params': {**params, **want['params']}},
        )
    return 'updated'


def registered_ids(base, token):
    """(MCP connection ids, workspace tool ids, active filter function ids) currently in Open WebUI."""
    res = call(base, 'GET', '/api/v1/configs/tool_servers', token) or {}
    servers = {(c.get('info') or {}).get('id') for c in res.get('TOOL_SERVER_CONNECTIONS') or []}
    tools = {t.get('id') for t in call(base, 'GET', '/api/v1/tools/', token) or []}
    funcs = {f.get('id') for f in call(base, 'GET', '/api/v1/functions/', token) or [] if f.get('is_active')}
    return servers, tools, funcs


def missing_refs(presets, filter_ids, servers, tools, funcs):
    """[(preset id, kind, id, optional)] for referenced things that are not registered."""
    out = []
    for p in presets:
        for r in p['tools']:
            if 'server' in r and r['server'] not in servers:
                out.append((p['id'], 'tool server', r['server'], bool(r.get('optional'))))
            if 'tool' in r and r['tool'] not in tools:
                out.append((p['id'], 'workspace tool', r['tool'], bool(r.get('optional'))))
        out += [(p['id'], 'filter function', f, False) for f in filter_ids if f not in funcs]
    return out


def visible(base, token, ids, attempts=5):
    """A freshly created preset can briefly be missing from the model list; returns the ids still missing."""
    missing = set(ids)
    for _ in range(attempts):
        res = call(base, 'GET', '/api/models', token) or {}
        missing -= {m.get('id') for m in res.get('data') or []}
        if not missing:
            break
        time.sleep(2)
    return sorted(missing)


def web_search_enabled(base, token):
    """True/False from the retrieval config, None when it cannot be read."""
    try:
        cfg = call(base, 'GET', '/api/v1/retrieval/config', token) or {}
    except ApiError:
        return None
    web = cfg.get('web') or {}
    return bool(web.get('ENABLE_WEB_SEARCH') and web.get('WEB_SEARCH_ENGINE'))


def run(argv=None):
    ap = argparse.ArgumentParser(description='Create or update the model presets of models/presets.json in Open WebUI.')
    ap.add_argument('--check', action='store_true', help='change nothing; exit 1 if a preset differs')
    ap.add_argument('--base-model', help='base model id (default: PRESETS_BASE_MODEL, TRANSLATOR_BASE_MODEL, then presets.json)')
    args = ap.parse_args(argv)
    ok = True

    def report(passed, msg):
        nonlocal ok
        ok = ok and passed
        print(f'[{"PASS" if passed else "FAIL"}] {msg}')

    env = load_env()
    base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
    try:
        doc, presets = load_presets()
    except ConfigError as e:
        print(f'[FAIL] {e}')
        print('RESULT: FAIL')
        return 1
    base_model = (
        args.base_model or env.get('PRESETS_BASE_MODEL') or env.get('TRANSLATOR_BASE_MODEL') or doc['base_model']
    ).strip()
    report(True, f'presets.json is valid ({len(presets)} preset(s)); base model {base_model}')

    try:
        token = get_token(env, base)
        report(True, f'authenticated as admin at {base}')

        if base_model_state(base, token, base_model):
            report(True, 'base model already registered with public read grant')
        elif args.check:
            report(False, 'base model has no public read grant (run without --check)')
        else:
            register_base_model(base, token, base_model)
            report(True, 'base model registered with public read grant')

        for p in presets:
            what = upsert(base, token, desired_model(p, base_model, doc['filter_ids']), not args.check)
            if what == 'unchanged':
                report(True, f'{p["id"]}: preset already up to date')
            elif args.check:
                report(False, f'{p["id"]}: preset would be {what} (run without --check)')
            else:
                report(True, f'{p["id"]}: preset {what}')

        for pid, kind, ref, optional in missing_refs(presets, doc['filter_ids'], *registered_ids(base, token)):
            if kind == 'filter function':
                print(f'[NOTE] {pid}: filter "{ref}" is not installed or not active: run bootstrap/user_context.py')
            elif optional:
                print(f'[NOTE] {pid}: optional {kind} "{ref}" is not registered yet; the preset uses it once it is (mcp_servers.py)')
            else:
                print(f'[NOTE] {pid}: {kind} "{ref}" is not registered yet: run bootstrap/mcp_servers.py and bootstrap/translator_tool.py')

        if any('web_search' in p['default_features'] or p['capabilities'].get('web_search') for p in presets):
            state = web_search_enabled(base, token)
            if state is False:
                print('[NOTE] web search is not configured in Open WebUI (ENABLE_WEB_SEARCH and WEB_SEARCH_ENGINE): '
                      'presets with web search will have no search tool until it is')

        if not args.check:
            gone = visible(base, token, [p['id'] for p in presets])
            report(not gone, 'all presets are listed by Open WebUI' if not gone else 'not listed: ' + ', '.join(gone))
    except ApiError as e:
        report(False, str(e))

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(run())
