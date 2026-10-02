#!/usr/bin/env python3
"""Read-only audit: is everything the presets depend on usable by ALL users?

Run on its own (`python3 Michael/bootstrap/access.py`) or as the last step of provision.py. For each
object type it reads the live grants through the admin API and reports PASS or FAIL. It changes
nothing: the grants themselves are written by the script that owns each object (presets.py for
models, translator_tool.py / kb_manager_tool.py / office_tools.py for tools, mcp_servers.py for the
tool server connections, knowledge_bases.py for knowledge bases), so a FAIL here means "run
provision.py again" (or somebody edited a grant in the app).

What "all users" means per object type in Open WebUI:

  models (presets, base model)     public read grant: listed in every user's model selector and usable
  workspace tools                  public read grant: usable by every user on a model that lists them
  tool server connections (MCP)    public read grant in the connection's config: usable by every user
  knowledge bases                  public read (everyone can search) and public write (everyone can add,
                                   edit and remove files; nothing here ever deletes a user's addition)
  Action functions                 functions have no grants; an Action is shown to the users of a model
                                   when it is active, not global and listed in the model's actionIds
  Filter functions                 the user context filter is active and global, so it runs for everyone

Every grant type can be given to all users except one limit worth knowing: a user can only ever change
or delete their OWN private objects; the audit says nothing about those.

Standard library only. Nothing secret is printed.
"""

import sys

from davy_connection import ApiError, base_model_of, call, get_token, load_env
from knowledge_bases import ConfigError as ManifestError
from knowledge_bases import find_knowledge_base, grants_of, list_knowledge_bases, load_manifest
from mcp_servers import ConfigError as McpConfigError
from mcp_servers import load_servers
from presets import ConfigError as PresetConfigError
from presets import load_presets, tool_id

PUBLIC_READ = ('user', '*', 'read')
PUBLIC_WRITE = ('user', '*', 'write')


def grant_set(obj):
    return {(g.get('principal_type'), g.get('principal_id'), g.get('permission')) for g in (obj or {}).get('access_grants') or []}


def get_or_none(base, token, path):
    try:
        return call(base, 'GET', path, token)
    except ApiError as e:
        if 'HTTP 404' in str(e) or 'HTTP 401' in str(e):
            return None
        raise


def audit(base, token, env, report):
    """Report every access fact; returns nothing (report records failures)."""
    try:
        doc, presets = load_presets()
        servers = load_servers()
        kbs = load_manifest()
    except (PresetConfigError, McpConfigError, ManifestError) as e:
        report(False, f'cannot read the declarations: {e}')
        return
    base_model = base_model_of(env, None, doc['base_model'])

    # Models: the base model row and every preset need a public read grant.
    for mid, label in [(base_model, 'base model')] + [(p['id'], f'preset {p["name"]}') for p in presets]:
        model = get_or_none(base, token, f'/api/v1/models/model?id={mid}')
        report(PUBLIC_READ in grant_set(model), f'{label} ({mid}): ' + ('readable by all users' if PUBLIC_READ in grant_set(model) else 'NOT readable by all users'))

    # Workspace tools named by the presets, and the tool server connections.
    tool_ids = sorted({r['tool'] for p in presets for r in p['tools'] if 'tool' in r})
    for tid in tool_ids:
        tool = get_or_none(base, token, f'/api/v1/tools/id/{tid}')
        if tool is None:
            report(False, f'tool {tid}: not installed')
        else:
            report(PUBLIC_READ in grant_set(tool), f'tool {tid}: ' + ('usable by all users' if PUBLIC_READ in grant_set(tool) else 'NOT usable by all users'))
    used = {r['server'] for p in presets for r in p['tools'] if 'server' in r}
    live = {(c.get('info') or {}).get('id'): c for c in (call(base, 'GET', '/api/v1/configs/tool_servers', token) or {}).get('TOOL_SERVER_CONNECTIONS') or []}
    for s in servers:
        if s['id'] not in used:
            continue
        conn = live.get(s['id'])
        if conn is None:
            report(False, f'tool server {s["id"]}: not registered')
            continue
        grants = {(g.get('principal_type'), g.get('principal_id'), g.get('permission')) for g in (conn.get('config') or {}).get('access_grants') or []}
        ok = s['access']['type'] != 'public' or PUBLIC_READ in grants
        report(ok and (conn.get('config') or {}).get('enable') is True, f'tool server {s["id"]}: ' + ('enabled and usable by all users' if ok else 'NOT usable by all users'))

    # Knowledge bases: public read and public write.
    live_kbs = list_knowledge_bases(base, token)
    for want in kbs:
        kb = find_knowledge_base(base, token, want['name'], live_kbs)
        if kb is None:
            report(False, f'knowledge base {want["name"]}: does not exist')
            continue
        g = grants_of(kb)
        report(PUBLIC_READ in g and PUBLIC_WRITE in g, f'knowledge base {want["name"]}: ' + ('readable and editable by all users' if PUBLIC_READ in g and PUBLIC_WRITE in g else 'NOT readable and editable by all users'))

    # Actions: active, not global, and attached to the models that declare them.
    for a in doc.get('actions', []):
        fn = get_or_none(base, token, f'/api/v1/functions/id/{a["id"]}')
        ok = bool(fn) and fn.get('is_active') is True and fn.get('type') == 'action' and not fn.get('is_global')
        report(ok, f'action {a["id"]}: ' + ('active, shown on the models that list it' if ok else 'not installed, inactive or global'))
        for p in presets:
            if a['id'] in p.get('actions', []):
                model = get_or_none(base, token, f'/api/v1/models/model?id={p["id"]}') or {}
                report(a['id'] in ((model.get('meta') or {}).get('actionIds') or []), f'action {a["id"]} is attached to {p["id"]}')

    # The user context filter runs for everyone.
    for fid in doc['filter_ids']:
        fn = get_or_none(base, token, f'/api/v1/functions/id/{fid}')
        ok = bool(fn) and fn.get('is_active') is True and fn.get('is_global') is True
        report(ok, f'filter {fid}: ' + ('active and global (runs for every user)' if ok else 'not installed, inactive or not global'))


def main():
    env = load_env()
    base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
    ok = True

    def report(passed, msg):
        nonlocal ok
        ok = ok and passed
        print(f'[{"PASS" if passed else "FAIL"}] {msg}')

    try:
        token = get_token(env, base)
        report(True, f'authenticated as admin at {base}')
        audit(base, token, env, report)
    except ApiError as e:
        report(False, str(e))
    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
