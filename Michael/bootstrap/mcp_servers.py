#!/usr/bin/env python3
"""Idempotently register the external tool servers of mcp/mcp.json in a running Open WebUI.

mcp/mcp.json (schema: mcp/mcp.schema.json, reference: docs/ExternalToolServers.md) declares
each server's type, URL, environment variables, auth, access and tool filter. This script is
the single owner of server registration. Through the authenticated admin API it:

  1. validates mcp.json against the schema and resolves every ${VARIABLE} from
     Michael/.env and the process environment (a missing required variable fails
     before anything is sent);
  2. reads the whole connection list (GET /api/v1/configs/tool_servers), merges each
     declared server by info.id, and writes the list back only when something changed
     (the admin route replaces the whole list), keeping every connection it does not
     manage exactly as it is;
  3. verifies each enabled server (POST /api/v1/configs/tool_servers/verify) and
     confirms the declared tools are listed.

Options: --config FILE reads another inventory; --check changes nothing and exits 1 when the live list differs from mcp.json;
--prune also removes connections this script registered earlier whose id is no longer
declared (connections it never registered are never touched); --no-verify skips step 3.

Standard library only. Secrets are never printed: only counts, ids, variable names and
fixed status text are written. Settings come from Michael/.env (see .env.example) plus
the admin credentials used by the other bootstrap scripts.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from davy_connection import MICHAEL_DIR, ApiError, call, get_token, load_env
import case_safety

MCP_JSON = MICHAEL_DIR / 'mcp' / 'mcp.json'
SCHEMA_JSON = MICHAEL_DIR / 'mcp' / 'mcp.schema.json'
MANAGED_BY = 'michael/mcp.json'
VAR_REF = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')


class ConfigError(Exception):
    """A problem in mcp.json or in the variables it needs; the message is secret-free."""


# --- schema validation (the JSON Schema subset mcp.schema.json uses) ------------------------


def _is_type(value, name):
    return {
        'object': isinstance(value, dict),
        'array': isinstance(value, list),
        'string': isinstance(value, str),
        'boolean': isinstance(value, bool),
        'integer': isinstance(value, int) and not isinstance(value, bool),
        'number': isinstance(value, (int, float)) and not isinstance(value, bool),
    }[name]


def validate(value, schema, root, path='$'):
    """Return a list of 'path: problem' strings; empty means valid."""
    if '$ref' in schema:
        target = root
        for part in schema['$ref'].lstrip('#/').split('/'):
            target = target[part]
        errors = validate(value, target, root, path)
        extra = {k: v for k, v in schema.items() if k != '$ref'}
        return errors + (validate(value, extra, root, path) if extra else [])
    errors = []
    if 'type' in schema and not _is_type(value, schema['type']):
        return [f'{path}: expected {schema["type"]}']
    if 'const' in schema and value != schema['const']:
        errors.append(f'{path}: must be {json.dumps(schema["const"])}')
    if 'enum' in schema and value not in schema['enum']:
        errors.append(f'{path}: must be one of {", ".join(json.dumps(v) for v in schema["enum"])}')
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0):
            errors.append(f'{path}: must not be empty')
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            errors.append(f'{path}: does not match {schema["pattern"]}')
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0):
            errors.append(f'{path}: needs at least {schema["minItems"]} item(s)')
        if 'items' in schema:
            for i, item in enumerate(value):
                errors += validate(item, schema['items'], root, f'{path}[{i}]')
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value:
                errors.append(f'{path}: missing "{key}"')
        props = schema.get('properties', {})
        for key, item in value.items():
            if key in props:
                errors += validate(item, props[key], root, f'{path}.{key}')
            elif schema.get('additionalProperties') is False:
                errors.append(f'{path}: unknown key "{key}"')
    for sub in schema.get('allOf', []):
        errors += validate(value, sub, root, path)
    if 'if' in schema and not validate(value, schema['if'], root, path):
        errors += validate(value, schema.get('then', {}), root, path)
    if 'not' in schema:
        if 'anyOf' in schema['not']:
            hit = any(not validate(value, sub, root, path) for sub in schema['not']['anyOf'])
        else:
            hit = not validate(value, schema['not'], root, path)
        if hit:
            errors.append(f'{path}: combination of keys not allowed here')
    return errors


# --- mcp.json -------------------------------------------------------------------------------


def name_allowed(name, filter_list):
    """Open WebUI's is_string_allowed: entries match name suffixes; a leading ! blocks."""
    allow = [f for f in filter_list if not f.startswith('!')]
    block = [f[1:] for f in filter_list if f.startswith('!')]
    if allow and not name.endswith(tuple(allow)):
        return False
    return not (block and name.endswith(tuple(block)))


def load_servers(path=MCP_JSON, schema_path=SCHEMA_JSON):
    """Parse and validate mcp.json; return its server list. Raises ConfigError."""
    try:
        doc = json.loads(path.read_text())
        schema = json.loads(schema_path.read_text())
    except (OSError, ValueError) as e:
        raise ConfigError(f'cannot read {path.name} or its schema: {type(e).__name__}') from None
    errors = validate(doc, schema, schema)
    if errors:
        raise ConfigError(f'{path.name} does not match {schema_path.name}: ' + '; '.join(errors[:6]))
    servers = doc['servers']
    errors = []
    seen = set()
    for s in servers:
        sid = s['id']
        if sid in seen:
            errors.append(f'duplicate id "{sid}"')
        seen.add(sid)
        names = [v['name'] for v in s['env']]
        if len(set(names)) != len(names):
            errors.append(f'{sid}: duplicate variable names in env')
        declared = set(names)
        refs = set(VAR_REF.findall(s['url']))
        for ref in sorted(refs - declared):
            errors.append(f'{sid}: url references ${{{ref}}} which is not declared in env')
        key_env = s['auth'].get('key_env')
        if key_env and key_env not in declared:
            errors.append(f'{sid}: auth.key_env {key_env} is not declared in env')
        elif key_env and not next(v for v in s['env'] if v['name'] == key_env)['secret']:
            errors.append(f'{sid}: bearer key variable {key_env} must be flagged secret')
        for v in s['env']:
            if v['secret'] and 'default' in v:
                errors.append(f'{sid}: secret variable {v["name"]} must not have a default')
            if v['required'] and 'default' in v:
                errors.append(f'{sid}: required variable {v["name"]} must not have a default')
        flt = s.get('function_name_filter_list') or []
        for tool in s['tools']:
            if flt and not name_allowed(tool, flt):
                errors.append(f'{sid}: tool {tool} is hidden by function_name_filter_list')
    if errors:
        raise ConfigError(f'{path.name}: ' + '; '.join(errors))
    return servers


def resolve(server, env):
    """Return the server's variables as {name: value}. Raises ConfigError naming missing ones."""
    values, missing = {}, []
    for var in server['env']:
        value = (env.get(var['name']) or '').strip() or var.get('default', '')
        if not value and var['required']:
            missing.append(var['name'])
        values[var['name']] = value
    if missing:
        raise ConfigError(f'{server["id"]}: missing required variable(s) {", ".join(missing)} (set them in Michael/.env)')
    return values


def desired_connection(server, values, group_ids):
    """The Open WebUI connection object for a declared server, with its variables resolved."""
    sid = server['id']
    url = VAR_REF.sub(lambda m: values[m.group(1)], server['url']).strip()
    if not url.startswith(('http://', 'https://')):
        raise ConfigError(f'{sid}: url must resolve to an http(s) URL')
    auth, key = server['auth'], ''
    auth_type = 'none'
    if auth['type'] == 'bearer':
        key = values[auth['key_env']]
        if key:
            auth_type = 'bearer'
        elif not auth.get('optional'):
            raise ConfigError(f'{sid}: bearer key variable {auth["key_env"]} is empty (set it in Michael/.env)')
    access = server['access']
    if access['type'] == 'public':
        grants = [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]
    elif access['type'] == 'groups':
        grants = []
        for g in access['groups']:
            if g not in group_ids:
                raise ConfigError(f'{sid}: Open WebUI has no group named "{g}"')
            grants.append({'principal_type': 'group', 'principal_id': group_ids[g], 'permission': 'read'})
    else:
        grants = []  # no grants = visible to admins only
    conn = {
        'type': server['type'],
        'url': url,
        'path': '' if server['type'] == 'mcp' else server.get('spec_path', 'openapi.json'),
        'auth_type': auth_type,
        'key': key,
        'headers': server.get('headers') or None,
        'config': {
            'enable': server['enabled'],
            'function_name_filter_list': ','.join(server.get('function_name_filter_list') or []),
            'access_grants': grants,
        },
        'info': {'id': sid, 'name': server['name'], 'description': server['description']},
        'managed_by': MANAGED_BY,
    }
    if server['type'] == 'openapi':
        conn['spec_type'] = 'url'
    return conn


def conn_id(conn):
    return (conn.get('info') or {}).get('id')


def merge(existing, want):
    """Declared values win; keys Open WebUI or an admin added (and config/info extras) are kept."""
    return {
        **existing,
        **want,
        'config': {**(existing.get('config') or {}), **want['config']},
        'info': {**(existing.get('info') or {}), **want['info']},
    }


def plan(current, wanted, prune, declared_ids):
    """Return (new_list, actions) where actions is [(id, 'added'|'updated'|'unchanged'|'pruned')]."""
    out, actions = list(current), []
    index = {conn_id(c): i for i, c in enumerate(out) if conn_id(c)}
    for want in wanted:
        sid = conn_id(want)
        if sid in index:
            merged = merge(out[index[sid]], want)
            actions.append((sid, 'unchanged' if merged == out[index[sid]] else 'updated'))
            out[index[sid]] = merged
        else:
            out.append(want)
            actions.append((sid, 'added'))
    if prune:
        stale = [c for c in out if c.get('managed_by') == MANAGED_BY and conn_id(c) not in declared_ids]
        for c in stale:
            actions.append((conn_id(c) or '(no id)', 'pruned'))
        out = [c for c in out if c not in stale]
    return out, actions


# --- Open WebUI -----------------------------------------------------------------------------


def group_ids(base, token, servers):
    if not any(s['access']['type'] == 'groups' for s in servers):
        return {}
    groups = call(base, 'GET', '/api/v1/groups/', token) or []
    return {g.get('name'): g.get('id') for g in groups}


def tool_names(res, server_type):
    """Tool names a verify response lists."""
    if server_type == 'mcp':
        return {s.get('name') for s in (res or {}).get('specs') or []}
    names = set()
    for item in ((res or {}).get('paths') or {}).values():
        for op in item.values():
            if isinstance(op, dict) and op.get('operationId'):
                names.add(op['operationId'])
    return names


def verify(base, token, conn, expected):
    """Return the expected tool names the server does not list. Raises ApiError when unreachable."""
    res = call(base, 'POST', '/api/v1/configs/tool_servers/verify', token, conn)
    return sorted(set(expected) - tool_names(res, conn['type']))


def run(argv=None):
    ap = argparse.ArgumentParser(description='Register the external tool servers of mcp/mcp.json in Open WebUI.')
    ap.add_argument('--check', action='store_true', help='change nothing; exit 1 if the live list differs from mcp.json')
    ap.add_argument('--prune', action='store_true', help='remove connections this script registered whose id is no longer declared')
    ap.add_argument('--config', type=Path, default=MCP_JSON, help='inventory file (default: mcp/mcp.json)')
    ap.add_argument('--no-verify', action='store_true', help='skip the verify step')
    args = ap.parse_args(argv)

    ok = True

    def report(passed, msg):
        nonlocal ok
        ok = ok and passed
        print(f'[{"PASS" if passed else "FAIL"}] {msg}')

    env = load_env()
    base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')

    try:
        servers = load_servers(args.config)
        if any(s['id'] == 'qdts' and s['enabled'] for s in servers):
            case_safety.validate_env(env)
        report(True, f'mcp.json is valid ({len(servers)} server(s) declared)')
        # Resolve everything first: a missing variable must fail before anything is sent.
        resolved, problems = {}, []
        for s in servers:
            try:
                resolved[s['id']] = resolve(s, env)
            except ConfigError as e:
                if s['enabled']:
                    problems.append(str(e))
                else:
                    print(f'[SKIP] {s["id"]}: disabled and its variables are not set')
        if problems:
            raise ConfigError('; '.join(problems))
    except (ConfigError, ApiError) as e:
        print(f'[FAIL] {e}')
        print('RESULT: FAIL')
        return 1
    active = [s for s in servers if s['id'] in resolved]

    try:
        token = get_token(env, base)
        if any(s['id'] == 'qdts' and s['enabled'] for s in active):
            case_safety.validate_live(base, token, env)
        report(True, f'authenticated as admin at {base}')
        gids = group_ids(base, token, active)
        wanted = [desired_connection(s, resolved[s['id']], gids) for s in active]
        res = call(base, 'GET', '/api/v1/configs/tool_servers', token) or {}
        current = list(res.get('TOOL_SERVER_CONNECTIONS') or [])
        new_list, actions = plan(current, wanted, args.prune, {s['id'] for s in servers})
        changed = new_list != current
        for sid, what in actions:
            if what == 'unchanged':
                report(True, f'{sid}: connection already up to date')
            elif args.check:
                report(False, f'{sid}: connection would be {what} (run without --check)')
            else:
                report(True, f'{sid}: connection {what}')
        if changed and not args.check:
            call(base, 'POST', '/api/v1/configs/tool_servers', token, {'TOOL_SERVER_CONNECTIONS': new_list})
        elif not changed:
            report(True, f'connection list unchanged ({len(current)} connection(s), nothing written)')
        if not args.no_verify:
            for s, want in zip(active, wanted):
                sid = s['id']
                if not s['enabled']:
                    print(f'[SKIP] {sid}: disabled, not verified')
                    continue
                try:
                    missing = verify(base, token, want, s['tools'])
                    report(
                        not missing,
                        f'{sid}: verifies and lists '
                        + (
                            f'all {len(s["tools"])} declared tool(s)'
                            if not missing
                            else f'none of {len(missing)} declared tool(s): ' + ', '.join(missing)
                        ),
                    )
                except ApiError as e:
                    report(
                        False,
                        f'{sid}: verification failed ({e}). Open WebUI reports no detail; check that the URL is '
                        'reachable from the Open WebUI container (not localhost), the key, and the server logs.',
                    )
    except (ApiError, ConfigError) as e:
        report(False, str(e))

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(run())
