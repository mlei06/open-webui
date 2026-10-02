#!/usr/bin/env python3
"""Idempotently provision the Audit Log event function in a running Open WebUI.

The function (functions/audit_log.py) writes one JSON line per administrative or
security event to <data volume>/audit/events-YYYY-MM-DD.jsonl. Through the
authenticated admin API this script:

  1. creates or updates the function (source compared byte for byte);
  2. enables it. Event functions have no global or per-model switch; an active
     one receives every event.

Valves are left alone, so values an admin set in the UI survive a re-run; a new
function starts with the defaults declared in its source. Reuses the helpers of
davy_connection.py. Standard library only; nothing secret is printed.
"""

import sys

from davy_connection import MICHAEL_DIR, ApiError, call, get_token, load_env

FUNCTION_ID = 'audit_log'
FUNCTION_FILE = MICHAEL_DIR / 'functions' / 'audit_log.py'


def get_function(base, token):
    try:
        return call(base, 'GET', f'/api/v1/functions/id/{FUNCTION_ID}', token)
    except ApiError as e:
        # Open WebUI answers 401 (not 404) for an unknown function id.
        if 'HTTP 401' in str(e) or 'HTTP 404' in str(e):
            return None
        raise


def upsert_function(base, token):
    """Create or update the source. Returns True when something changed."""
    source = FUNCTION_FILE.read_text()
    form = {
        'id': FUNCTION_ID,
        'name': 'Audit Log',
        'content': source,
        'meta': {'description': 'Appends an audit record for every administrative or security event.'},
    }
    current = get_function(base, token)
    if current is None:
        call(base, 'POST', '/api/v1/functions/create', token, form)
        return True
    if current.get('content') == source and current.get('type') == 'event':
        return False
    call(base, 'POST', f'/api/v1/functions/id/{FUNCTION_ID}/update', token, form)
    return True


def ensure_active(base, token):
    """The toggle endpoint flips the flag, so read first. Returns True when it enabled the function."""
    if (get_function(base, token) or {}).get('is_active'):
        return False
    call(base, 'POST', f'/api/v1/functions/id/{FUNCTION_ID}/toggle', token)
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
        token = get_token(env, base)
        report(True, f'authenticated as admin at {base}')
        report(True, 'event function ' + ('created/updated' if upsert_function(base, token) else 'already up to date'))
        report(True, 'event function ' + ('enabled' if ensure_active(base, token) else 'already enabled'))
        final = get_function(base, token) or {}
        report(final.get('type') == 'event' and final.get('is_active') is True, 'event function is active')
    except ApiError as e:
        report(False, str(e))

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
