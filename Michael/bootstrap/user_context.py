#!/usr/bin/env python3
"""Idempotently provision the User Context filter in a running Open WebUI.

The filter (functions/user_context.py) adds a short "who you are talking to"
block (name, id, email of the signed-in user) to the system message of every
chat request. Which of those fields each model receives comes from
models/user-context.json. Through the authenticated admin API this script:

  1. creates or updates the filter function (source compared byte for byte);
  2. enables it and makes it global (it then runs for every model, no per-model
     attachment needed);
  3. stores models/user-context.json in the filter's valves, because the filter
     runs inside the container and cannot read repository files.

Reuses the helpers of davy_connection.py. Standard library only. Secrets are
never printed: only fixed status text is written to the terminal. Needs only
the admin credentials from Michael/.env (see .env.example).
"""

import json
import sys

from davy_connection import MICHAEL_DIR, ApiError, call, get_token, load_env

FUNCTION_ID = 'user_context'
FUNCTION_FILE = MICHAEL_DIR / 'functions' / 'user_context.py'
CONFIG_FILE = MICHAEL_DIR / 'models' / 'user-context.json'
VALVE = 'models_config_json'
FIELDS = ('name', 'id', 'email')


def load_config():
    """Read and validate the model config; returns the parsed dict."""
    try:
        config = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        raise ApiError('models/user-context.json is missing or not valid JSON') from None

    def valid(fields):
        return isinstance(fields, list) and all(f in FIELDS for f in fields) and len(set(fields)) == len(fields)

    models = config.get('models', {}) if isinstance(config, dict) else None
    if not (isinstance(config, dict) and valid(config.get('default')) and isinstance(models, dict)):
        raise ApiError(f'models/user-context.json needs "default" and "models"; fields are {", ".join(FIELDS)}')
    bad = sorted(k for k, v in models.items() if not valid(v))
    if bad:
        raise ApiError(f'models/user-context.json: invalid field list for {", ".join(bad)} (allowed: {", ".join(FIELDS)})')
    return config


def get_function(base, token):
    try:
        return call(base, 'GET', f'/api/v1/functions/id/{FUNCTION_ID}', token)
    except ApiError as e:
        # Open WebUI answers 401 (not 404) for an unknown function id.
        if 'HTTP 401' in str(e) or 'HTTP 404' in str(e):
            return None
        raise


def upsert_function(base, token):
    """Create or update the filter source. Returns True when something changed."""
    source = FUNCTION_FILE.read_text()
    form = {
        'id': FUNCTION_ID,
        'name': 'User Context',
        'content': source,
        'meta': {'description': 'Tells every model the signed-in user\'s name, id and email through the system message.'},
    }
    current = get_function(base, token)
    if current is None:
        call(base, 'POST', '/api/v1/functions/create', token, form)
        return True
    if current.get('content') == source and current.get('type') == 'filter':
        return False
    call(base, 'POST', f'/api/v1/functions/id/{FUNCTION_ID}/update', token, form)
    return True


def ensure_flags(base, token):
    """The toggle endpoints flip the flag, so read first. Returns (activated, made_global)."""
    current = get_function(base, token) or {}
    activated = made_global = False
    if not current.get('is_active'):
        call(base, 'POST', f'/api/v1/functions/id/{FUNCTION_ID}/toggle', token)
        activated = True
    if not current.get('is_global'):
        call(base, 'POST', f'/api/v1/functions/id/{FUNCTION_ID}/toggle/global', token)
        made_global = True
    return activated, made_global


def ensure_valves(base, token, config):
    """Store the config in the valves. Returns True when they changed."""
    valves = call(base, 'GET', f'/api/v1/functions/id/{FUNCTION_ID}/valves', token) or {}
    try:
        stored = json.loads(valves.get(VALVE) or 'null')
    except ValueError:
        stored = None
    if stored == config:
        return False
    call(
        base,
        'POST',
        f'/api/v1/functions/id/{FUNCTION_ID}/valves/update',
        token,
        {**valves, VALVE: json.dumps(config)},
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
        config = load_config()
        report(True, f'models/user-context.json valid ({len(config.get("models") or {})} model entries plus default)')
    except ApiError as e:
        print(f'[FAIL] {e}')
        print('RESULT: FAIL')
        return 1

    try:
        token = get_token(env, base)
        report(True, f'authenticated as admin at {base}')

        report(True, 'filter function ' + ('created/updated' if upsert_function(base, token) else 'already up to date'))
        activated, made_global = ensure_flags(base, token)
        report(True, 'filter ' + ('enabled' if activated else 'already enabled'))
        report(True, 'filter ' + ('made global' if made_global else 'already global'))
        report(True, 'model config in valves ' + ('updated' if ensure_valves(base, token, config) else 'already up to date'))

        final = get_function(base, token) or {}
        report(
            final.get('type') == 'filter' and final.get('is_active') is True and final.get('is_global') is True,
            'filter is active and global',
        )
    except ApiError as e:
        report(False, str(e))

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
