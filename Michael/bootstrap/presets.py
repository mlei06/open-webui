#!/usr/bin/env python3
"""Idempotently create the Open WebUI model presets declared in models/presets.json.

Each preset (Lenny, Document Translator, Web Searcher, Office Agent, Knowledge Base Manager, Office Documents)
is a custom model with its system prompt (prompts/<name>.md), the tool servers and workspace tools it may
reach, capabilities, built-in tools, default features and the user-context filter
already filled out. Through the authenticated admin API this script:

  1. validates models/presets.json and reads every prompt file;
  2. registers the base model with a public read grant (a non-admin user cannot use a
     preset whose base model has no registered row);
  3. creates or updates each preset, touching only the settings the file declares and
     writing nothing when the live model already matches;
  4. installs the Action functions declared under "actions" (functions/<file>) and
     activates them, never globally: a preset gets one only through its "actions" list
     (meta.actionIds). Today that is the "Review and send email" button of Lenny and
     the Office Agent;
  5. pushes the web search settings (web search on, engine perplexity_search, key from
     PERPLEXITY_API_KEY in Michael/.env) when they are not already saved: Open WebUI keeps
     saved settings in its database, which override the environment compose passes;
  6. attaches the knowledge bases of knowledge/manifest.json named by "knowledge_bases" in
     presets.json (a top-level default for every preset, overridable per preset) and turns on
     the built-in knowledge tool so models can search them. Knowledge a user attached in the
     app is kept; a base that is not created yet (knowledge_bases.py) is a NOTE;
  7. sets each preset's profile image from its "icon" (an SVG of branding/icons/, rasterised to PNG by
     icons.py because Open WebUI refuses SVG profile images). A preset with no "icon" keeps Open WebUI's
     default image and prints a NOTE; an image a user changed in the app is put back, like any other
     declared setting. --check reports icon drift;
  8. checks that the tool servers and tools the presets name are registered. An
     unregistered one (for example the mail server before mail-service is up) is a NOTE,
     not a failure: registration belongs to mcp_servers.py and translator_tool.py, and
     the preset works as soon as the connection exists.

Base model: --base-model, else PRESETS_BASE_MODEL, else TRANSLATOR_BASE_MODEL from
Michael/.env, else "base_model" in models/presets.json (nemotron-3-ultra).

Options: --check changes nothing and exits 1 when a preset differs; --base-model ID.
--prompts-only updates existing presets' system text and refreshes the model cache,
preserving their other live settings. It requires native calling and the approved base.
Reuses the helpers of davy_connection.py. Standard library only. Secrets are never
printed: only ids, counts and fixed status text are written.
"""

import argparse
import functools
import hashlib
import json
import sys
import time

import icons
import case_safety
from davy_connection import MICHAEL_DIR, ApiError, base_model_of, call, get_token, load_env
from knowledge_bases import ConfigError as ManifestError
from knowledge_bases import find_knowledge_base, list_knowledge_bases, load_manifest

PRESETS_JSON = MICHAEL_DIR / 'models' / 'presets.json'
PROMPT_DIR = MICHAEL_DIR / 'prompts'
FUNCTION_DIR = MICHAEL_DIR / 'functions'
ICON_DIR = icons.ICON_DIR
SEARCH_ENGINE = 'perplexity_search'
SEARCH_KEY_ENV = 'PERPLEXITY_API_KEY'
PUBLIC_READ = [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]
# Every built-in tool category Open WebUI knows; a preset enables only the ones it lists.
BUILTIN_CATEGORIES = (
    'time', 'user_input', 'memory', 'chats', 'notes', 'knowledge', 'files', 'channels', 'notifications',
    'web_search', 'image_generation', 'code_interpreter', 'tasks', 'automations', 'calendar', 'subagents',
)  # fmt: skip
FEATURES = ('web_search', 'image_generation', 'code_interpreter')
# meta/params keys this script owns; every other key of a live model is left alone.
META_KEYS = ('description', 'capabilities', 'builtinTools', 'toolIds', 'actionIds', 'filterIds', 'defaultFeatureIds')
# meta.knowledge is merged rather than owned: references a user added in the app are kept.


class ConfigError(Exception):
    """A problem in models/presets.json or a prompt file; the message is secret-free."""


def tool_id(ref):
    """The Open WebUI tool id for a {"server": id} or {"tool": id} reference."""
    return f'server:mcp:{ref["server"]}' if 'server' in ref else ref['tool']


@functools.lru_cache(maxsize=None)
def render_icon(svg_text):
    """The PNG data URI of an SVG icon (cached: the render is deterministic per source). Raises icons.IconError."""
    return icons.render_data_uri(svg_text)


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def load_icon(name, icon_dir):
    """(svg text, error) for a preset's "icon" file name; the error is None when it reads and renders."""
    if not (isinstance(name, str) and name.endswith('.svg') and '/' not in name and '\\' not in name and not name.startswith('.')):
        return None, 'icon must be the file name of an .svg in branding/icons/'
    try:
        text = (icon_dir / name).read_text()
    except OSError:
        return None, f'cannot read icon file {name}'
    try:
        render_icon(text)
    except icons.IconError as e:
        return None, f'icon {name}: {e}'
    return text, None


def load_presets(path=PRESETS_JSON, prompt_dir=PROMPT_DIR, function_dir=FUNCTION_DIR, icon_dir=ICON_DIR):
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
    actions = doc.get('actions', [])
    action_ids = set()
    if not isinstance(actions, list):
        errors.append('actions must be a list')
        actions = []
    for i, a in enumerate(actions):
        aid = a.get('id') if isinstance(a, dict) else None
        if not (isinstance(aid, str) and aid and all(isinstance(a.get(k), str) and a[k].strip() for k in ('name', 'description', 'file'))):
            errors.append(f'actions[{i}]: needs id, name, description and file (all non-empty strings)')
            continue
        if aid in action_ids:
            errors.append(f'actions[{i}] ({aid}): duplicate id')
        action_ids.add(aid)
        try:
            a['content'] = (function_dir / a['file']).read_text()
        except OSError:
            errors.append(f'actions[{i}] ({aid}): cannot read function file {a["file"]}')
    presets = doc.get('presets')
    if not (isinstance(presets, list) and presets):
        raise ConfigError(f'{path.name}: presets must be a non-empty list')
    try:
        kb_names = {k['id']: k['name'] for k in load_manifest()}
    except ManifestError as e:
        raise ConfigError(str(e)) from None
    default_kbs = doc.get('knowledge_bases', [])
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
        used = p.get('actions', [])
        if not (isinstance(used, list) and all(a in action_ids for a in used)):
            errors.append(f'{where}: actions must be a list of ids declared under the top-level "actions"')
        if not isinstance(p.get('params'), dict):
            errors.append(f'{where}: params must be an object')
        icon_svg = None
        if p.get('icon') is not None:
            icon_svg, problem = load_icon(p['icon'], icon_dir)
            if problem:
                errors.append(f'{where}: {problem}')
        kbs = p.get('knowledge_bases', default_kbs)
        if not (isinstance(kbs, list) and all(k in kb_names for k in kbs)):
            errors.append(f'{where}: knowledge_bases must be a list of ids from knowledge/manifest.json ({", ".join(kb_names)})')
            kbs = []
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
        out.append({**p, 'system': text, 'icon_svg': icon_svg, 'knowledge_bases': [kb_names[k] for k in kbs]})
    if errors:
        raise ConfigError(f'{path.name}: ' + '; '.join(errors))
    return doc, out


def knowledge_refs(preset, kbs):
    """meta.knowledge entries for the preset's knowledge bases; kbs maps a name to its live record."""
    return [
        {'id': kbs[n]['id'], 'name': n, 'type': 'collection', 'description': kbs[n].get('description') or ''}
        for n in preset['knowledge_bases']
        if n in kbs
    ]


def icon_meta(svg_text):
    """meta keys of a preset's icon: the PNG data URI and its fingerprint ({} when the preset has no icon).

    preset_icon.source identifies the SVG and renderer the image came from; preset_icon.image is the hash of
    the data URI written, so an image replaced in the app is noticed. The PNG bytes are never compared with a
    fresh render, which a different zlib could encode differently.
    """
    if not svg_text:
        return {}
    uri = render_icon(svg_text)
    return {'profile_image_url': uri, 'preset_icon': {'source': icons.fingerprint(svg_text), 'image': sha256(uri)}}


def icon_ok(meta, want_meta):
    """True when the live image is the declared icon (or no icon is declared)."""
    wanted = want_meta.get('preset_icon')
    if not wanted:
        return True
    have = meta.get('preset_icon') or {}
    return have.get('source') == wanted['source'] and have.get('image') == sha256(meta.get('profile_image_url') or '')


def desired_model(preset, base_model, filter_ids, kbs=None):
    """The model form Open WebUI should hold for a preset. kbs maps knowledge base names to live records."""
    knowledge = knowledge_refs(preset, kbs or {})
    # Native function calling reaches attached knowledge only through the built-in knowledge tool.
    builtin = set(preset['builtin_tools']) | ({'knowledge'} if preset['knowledge_bases'] else set())
    icon = icon_meta(preset.get('icon_svg'))
    return {
        'id': preset['id'],
        'base_model_id': base_model,
        'name': preset['name'],
        'is_active': True,
        'access_grants': PUBLIC_READ,
        'meta': {
            'description': preset['description'],
            'capabilities': preset['capabilities'],
            'builtinTools': {c: c in builtin for c in BUILTIN_CATEGORIES},
            'toolIds': [tool_id(r) for r in preset['tools']],
            'actionIds': list(preset.get('actions', [])),
            'filterIds': list(filter_ids),
            'defaultFeatureIds': list(preset['default_features']),
            **({'knowledge': knowledge} if knowledge else {}),
            **icon,
        },
        'params': {'function_calling': 'native', 'system': preset['system'], **preset['params']},
    }


def grants_of(model):
    return {
        (g.get('principal_type'), g.get('principal_id'), g.get('permission'))
        for g in (model or {}).get('access_grants') or []
    }


def merge_knowledge(live, want):
    """Wanted references (current name and description) first-class, any other live reference kept."""
    wanted = {k['id'] for k in want}
    return [*want, *[k for k in live or [] if k.get('id') not in wanted]]


def knowledge_ok(live, want, managed=frozenset()):
    """Every wanted reference is present as declared, and no managed base the preset no longer declares is attached."""
    by_id = {k.get('id'): k for k in live or []}
    wanted = {k['id'] for k in want}
    return all(by_id.get(k['id']) == k for k in want) and not (set(by_id) & set(managed) - wanted)


def matches(current, want, managed=frozenset()):
    """True when the live model already carries everything the declaration sets.

    managed: ids of the knowledge bases this repository provisions. A preset that does not declare one of them
    must not hold it (a user's own attachments are never in this set, so they are kept).
    """
    meta, params = current.get('meta') or {}, current.get('params') or {}
    return (
        knowledge_ok(meta.get('knowledge'), want['meta'].get('knowledge') or [], managed)
        and icon_ok(meta, want['meta'])
        and current.get('base_model_id') == want['base_model_id']
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


def knowledge_meta(meta, want, managed=frozenset()):
    wanted = want['meta'].get('knowledge') or []
    merged = [k for k in merge_knowledge(meta.get('knowledge'), wanted) if k.get('id') in {w['id'] for w in wanted} or k.get('id') not in managed]
    return {'knowledge': merged} if merged else {'knowledge': []} if meta.get('knowledge') else {}


def upsert(base, token, want, apply, managed=frozenset()):
    """Return 'created', 'updated' or 'unchanged' (and write it when apply is true)."""
    current = get_model(base, token, want['id'])
    if current is None:
        if apply:
            call(base, 'POST', '/api/v1/models/create', token, want)
        return 'created'
    if matches(current, want, managed):
        return 'unchanged'
    if apply:
        meta, params = current.get('meta') or {}, current.get('params') or {}
        call(
            base,
            'POST',
            '/api/v1/models/model/update',
            token,
            {**want, 'meta': {**meta, **want['meta'], **knowledge_meta(meta, want, managed)}, 'params': {**params, **want['params']}},
        )
    return 'updated'


def get_function(base, token, function_id):
    try:
        return call(base, 'GET', f'/api/v1/functions/id/{function_id}', token)
    except ApiError as e:
        # Open WebUI answers 401 (not 404) for an unknown function id.
        if 'HTTP 401' in str(e) or 'HTTP 404' in str(e):
            return None
        raise


def refresh_prompts(base, token, presets, base_model, apply, update_base=False):
    """Preflight every existing preset, then update only system text with read-back checks.

    This is intentionally separate from full reconciliation: prompt edits must not
    reset live capability overrides, actions, search configuration or access grants.
    The model update API accepts a full form, not a params-only PATCH.
    """
    pending = []
    for preset in presets:
        current = get_model(base, token, preset['id'])
        if current is None:
            raise ApiError(f'{preset["id"]}: missing preset; run full provisioning first')
        if ((not update_base and current.get('base_model_id') != base_model)
                or (current.get('params') or {}).get('function_calling') != 'native'):
            raise ApiError(f'{preset["id"]}: prompt refresh requires the approved base and native calling')
        pending.append((preset, current))

    results = []
    for preset, current in pending:
        changed = current['params'].get('system') != preset['system'] or (
            update_base and (current.get('base_model_id') != base_model
                             or current.get('meta', {}).get('description') != preset['description'])
        )
        if changed and apply:
            # Access grants are omitted deliberately: the API preserves them.
            form = {k: current[k] for k in ('id', 'name', 'base_model_id', 'meta', 'is_active')}
            form['params'] = {**current['params'], 'system': preset['system']}
            if update_base:
                form['base_model_id'] = base_model
                form['meta'] = {**form['meta'], 'description': preset['description']}
            # Detect edits made since preflight rather than overwrite them.
            if get_model(base, token, preset['id']) != current:
                raise ApiError(f'{preset["id"]}: changed during preflight; retry after edits finish')
            call(base, 'POST', '/api/v1/models/model/update', token, form)
            saved = get_model(base, token, preset['id']) or {}
            if any(saved.get(k) != v for k, v in form.items()) or grants_of(saved) != grants_of(current):
                raise ApiError(f'{preset["id"]}: prompt read-back or preservation check failed')
        results.append((preset['id'], changed))

    if apply:
        listed = call(base, 'GET', '/api/models?refresh=true', token) or {}
        ids = {m.get('id') for m in listed.get('data') or []}
        if any(preset['id'] not in ids for preset in presets):
            raise ApiError('Prompt refresh saved, but not all presets are visible after cache refresh')
    return results


def upsert_action(base, token, action, apply):
    """Create/update an Action function and activate it (never global). Returns a list of what changed."""
    form = {
        'id': action['id'],
        'name': action['name'],
        'content': action['content'],
        'meta': {'description': action['description']},
    }
    current = get_function(base, token, action['id'])
    changed = []
    if current is None or current.get('content') != action['content'] or current.get('type') != 'action':
        changed.append('created' if current is None else 'updated')
        if apply:
            path = '/api/v1/functions/create' if current is None else f'/api/v1/functions/id/{action["id"]}/update'
            call(base, 'POST', path, token, form)
            current = get_function(base, token, action['id']) or {}
    if not (current or {}).get('is_active'):
        changed.append('activated')
        if apply:
            call(base, 'POST', f'/api/v1/functions/id/{action["id"]}/toggle', token)
    if (current or {}).get('is_global'):
        changed.append('made non-global')
        if apply:
            call(base, 'POST', f'/api/v1/functions/id/{action["id"]}/toggle/global', token)
    return changed


def web_search_form(cfg, key):
    """The web block to save, or None when Perplexity web search is already saved as wanted."""
    web = (cfg or {}).get('web') or {}
    want = {'ENABLE_WEB_SEARCH': True, 'WEB_SEARCH_ENGINE': SEARCH_ENGINE, 'PERPLEXITY_API_KEY': key}
    if all(web.get(k) == v for k, v in want.items()):
        return None
    return {**web, **want}


def ensure_web_search(base, token, key, apply):
    """Returns 'unchanged' or 'updated'. The save replaces the whole web block, so it is merged first."""
    form = web_search_form(call(base, 'GET', '/api/v1/retrieval/config', token), key)
    if form is None:
        return 'unchanged'
    if apply:
        call(base, 'POST', '/api/v1/retrieval/config/update', token, {'web': form})
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
    ap.add_argument('--prompts-only', action='store_true', help='update only existing system prompts, preserving other live settings')
    ap.add_argument('--update-base-model', action='store_true', help='with --prompts-only, also migrate the base model and descriptions')
    ap.add_argument('--base-model', help='base model id (default: PRESETS_BASE_MODEL, TRANSLATOR_BASE_MODEL, then presets.json)')
    args = ap.parse_args(argv)
    if args.update_base_model and not args.prompts_only:
        ap.error('--update-base-model requires --prompts-only')
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
    base_model = base_model_of(env, args.base_model, doc['base_model'])
    try:
        case_safety.validate_env(env, base_model)
    except ApiError as e:
        print(f'[FAIL] {e}\nRESULT: FAIL')
        return 1
    report(True, f'presets.json is valid ({len(presets)} preset(s)); base model {base_model}')

    try:
        token = get_token(env, base)
        case_safety.validate_live(base, token, env)
        report(True, f'authenticated as admin at {base}')

        if args.prompts_only:
            if args.update_base_model:
                listed = call(base, 'GET', '/api/models?refresh=true', token) or {}
                if base_model not in {m.get('id') for m in listed.get('data') or []}:
                    raise ApiError('Target base model is not available; no presets changed')
                if not base_model_state(base, token, base_model):
                    raise ApiError('Target base model needs a public read grant before migration')
            for pid, changed in refresh_prompts(base, token, presets, base_model, not args.check, args.update_base_model):
                report(not (args.check and changed), f'{pid}: ' + ('prompt/base/description ' if args.update_base_model else 'prompt ') + (
                    'would be updated' if args.check and changed else 'updated' if changed else 'already up to date'
                ))
            print('RESULT: ' + ('PASS' if ok else 'FAIL'))
            return 0 if ok else 1

        if base_model_state(base, token, base_model):
            report(True, 'base model already registered with public read grant')
        elif args.check:
            report(False, 'base model has no public read grant (run without --check)')
        else:
            register_base_model(base, token, base_model)
            report(True, 'base model registered with public read grant')

        for a in doc.get('actions', []):
            changed = upsert_action(base, token, a, not args.check)
            if not changed:
                report(True, f'{a["id"]}: action already installed and active')
            elif args.check:
                report(False, f'{a["id"]}: action would be {" and ".join(changed)} (run without --check)')
            else:
                report(True, f'{a["id"]}: action {" and ".join(changed)}')

        key = (env.get(SEARCH_KEY_ENV) or '').strip()
        if not key:
            print(f'[NOTE] {SEARCH_KEY_ENV} is not set in Michael/.env: web search settings are left as they are')
        else:
            what = ensure_web_search(base, token, key, not args.check)
            if what == 'unchanged':
                report(True, f'web search already uses {SEARCH_ENGINE}')
            elif args.check:
                report(False, f'web search would be switched to {SEARCH_ENGINE} (run without --check)')
            else:
                report(True, f'web search enabled with {SEARCH_ENGINE}')

        kbs, live_kbs = {}, list_knowledge_bases(base, token)
        for name in sorted({n for p in presets for n in p['knowledge_bases']}):
            kb = find_knowledge_base(base, token, name, live_kbs)
            if kb is None:
                print(f'[NOTE] knowledge base "{name}" does not exist yet: run bootstrap/knowledge_bases.py, then re-run this script to attach it')
            else:
                kbs[name] = kb

        managed = {kb['id'] for kb in live_kbs if kb.get('name') in {n for q in presets for n in q['knowledge_bases']}}
        for p in presets:
            what = upsert(base, token, desired_model(p, base_model, doc['filter_ids'], kbs), not args.check, managed)
            if what == 'unchanged':
                report(True, f'{p["id"]}: preset already up to date')
            elif args.check:
                report(False, f'{p["id"]}: preset would be {what} (run without --check)')
            else:
                report(True, f'{p["id"]}: preset {what}')
            if not p.get('icon_svg'):
                print(f'[NOTE] {p["id"]}: no "icon" declared in presets.json: it keeps the default Open WebUI image')

        for pid, kind, ref, optional in missing_refs(presets, doc['filter_ids'], *registered_ids(base, token)):
            if kind == 'filter function':
                print(f'[NOTE] {pid}: filter "{ref}" is not installed or not active: run bootstrap/user_context.py')
            elif optional:
                print(f'[NOTE] {pid}: optional {kind} "{ref}" is not registered yet; the preset uses it once it is (mcp_servers.py)')
            else:
                print(f'[NOTE] {pid}: {kind} "{ref}" is not registered yet: run bootstrap/mcp_servers.py, translator_tool.py and office_tools.py')

        if any('web_search' in p['default_features'] or p['capabilities'].get('web_search') for p in presets):
            state = web_search_enabled(base, token)
            if state is False and not key:
                print('[NOTE] web search is not configured in Open WebUI (ENABLE_WEB_SEARCH and WEB_SEARCH_ENGINE): '
                      f'set {SEARCH_KEY_ENV} in Michael/.env and run this script again')

        if not args.check:
            gone = visible(base, token, [p['id'] for p in presets])
            report(not gone, 'all presets are listed by Open WebUI' if not gone else 'not listed: ' + ', '.join(gone))
    except ApiError as e:
        report(False, str(e))

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(run())
