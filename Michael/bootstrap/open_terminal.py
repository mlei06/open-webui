#!/usr/bin/env python3
"""Register the managed Open Terminal connection through the admin API.

Run after starting the Compose service. --check is read-only. Existing connection
access grants and custom settings are retained; a new connection is admin-only.
This uses the dedicated terminal integration, not MCP tool-server registration.

--access all  makes the terminal available to every user (keeps other grants). It first asks
              the terminal's file API for /proc/1/environ and refuses unless that is denied,
              because an unconfined upstream terminal lets any user read other users' files
              and the terminal key. Build the image from Michael/terminal/Dockerfile.
--access admin  removes all grants (admin only).
--access keep   (default) leaves the grants as they are.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from davy_connection import MICHAEL_DIR, ApiError, call, get_token, load_env

CONNECTION_ID = 'open-terminal'
API_PATH = '/api/v1/configs/terminal_servers'
PUBLIC_READ = {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}
ACCESS_MODES = ('keep', 'all', 'admin')
PUBLIC_READ_KEY = ('user', '*', 'read')


def _grant_key(grant):
    return (grant.get('principal_type'), grant.get('principal_id'), grant.get('permission'))


def desired_connections(current, env, access='keep'):
    key = env.get('OPEN_TERMINAL_API_KEY', '').strip()
    if not key:
        raise ApiError('OPEN_TERMINAL_API_KEY is missing; initialize the private environment first')
    if not isinstance(current, list):
        raise ApiError('Terminal connection configuration is not a list; refusing to replace it')
    result = deepcopy(current)
    matches = [c for c in result if c.get('id') == CONNECTION_ID]
    if len(matches) > 1:
        raise ApiError('Duplicate open-terminal connection IDs; resolve them before registration')
    if matches:
        target = matches[0]
    else:
        target = {'id': CONNECTION_ID, 'config': {'access_grants': []}}
        result.append(target)
    target.update({'name': 'Open Terminal', 'url': 'http://open-terminal:8000',
                   'path': '/openapi.json', 'key': key, 'auth_type': 'bearer',
                   'enabled': True, 'forward_cookies': False, 'server_type': 'terminal'})
    # These orchestrator-only selectors must not redirect a plain terminal.
    target['policy_id'] = None
    if access not in ACCESS_MODES:
        raise ApiError('--access must be one of: ' + ', '.join(ACCESS_MODES))
    if access == 'all':
        config = target.setdefault('config', {})
        grants = list(config.get('access_grants') or [])
        if PUBLIC_READ_KEY not in {_grant_key(g) for g in grants}:
            grants.append(dict(PUBLIC_READ))
        config['access_grants'] = grants
    elif access == 'admin':
        target.setdefault('config', {})['access_grants'] = []
    return result


def probe_confinement(base, token):
    """HTTP status of the terminal file API serving /proc/1/environ to the administrator, through
    Open WebUI. A confined terminal answers 403; 200 means any user could read the terminal's key."""
    url = f'{base}/api/v1/terminals/{CONNECTION_ID}/files/view?path=%2Fproc%2F1%2Fenviron'
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read(1)
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError as exc:
        raise ApiError(f'Could not probe the terminal file API: {exc}') from None


def require_confinement(base, token):
    status = probe_confinement(base, token)
    if status != 403:
        raise ApiError('Refusing to share the terminal: its file API did not deny /proc/1/environ '
                       f'(HTTP {status}). Rebuild it from Michael/terminal/Dockerfile (file confinement), '
                       'recreate the terminal and retry.')


def backup_config(config):
    directory = MICHAEL_DIR / 'runtime'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ('terminal-connections-before-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(config, stream)
    return path


def _normalized(connection):
    """Compare grants by what they grant, since the API adds database fields to each row."""
    result = deepcopy(connection)
    config = result.get('config')
    if isinstance(config, dict) and isinstance(config.get('access_grants'), list):
        config['access_grants'] = sorted(_grant_key(g) for g in config['access_grants'])
    return result


def _differs(saved, wanted):
    saved, wanted = _normalized(saved), _normalized(wanted)
    for key, value in wanted.items():
        if key == 'config' and isinstance(value, dict):
            if any(saved.get(key, {}).get(k) != v for k, v in value.items()):
                return True
        elif saved.get(key) != value:
            return True
    return False


def _write(base, token, before, desired):
    # Avoid overwriting concurrent admin edits to any terminal connection.
    if call(base, 'GET', API_PATH, token) != before:
        raise ApiError('Terminal connections changed during preflight; retry after edits finish')
    backup_config(before)
    call(base, 'POST', API_PATH, token, {'TERMINAL_SERVER_CONNECTIONS': desired})
    saved = call(base, 'GET', API_PATH, token).get('TERMINAL_SERVER_CONNECTIONS', [])
    # The API may populate optional defaults. Require all supplied values,
    # exact ID order, and retention of unrelated configuration.
    if len(saved) != len(desired) or any(_differs(a, b) for a, b in zip(saved, desired)):
        raise ApiError('Terminal connection read-back did not preserve the intended configuration')


def reconcile(base, token, env, apply=False, access='keep'):
    before = call(base, 'GET', API_PATH, token)
    current = before.get('TERMINAL_SERVER_CONNECTIONS')
    if current is None:
        current = []
    registered = any(c.get('id') == CONNECTION_ID for c in current)
    # A new connection is always registered admin-only first; sharing happens after the probe.
    desired = desired_connections(current, env, access if registered else 'keep')
    target = next(c for c in desired if c['id'] == CONNECTION_ID)
    verification = call(base, 'POST', API_PATH + '/verify', token, target)
    if verification.get('status') is not True or verification.get('type') != 'terminal':
        raise ApiError('Open WebUI could not verify the Open Terminal service')
    changed = _differs_list(current, desired)
    if access == 'all':
        widening = (not registered) or any(_differs(a, b) for a, b in zip(current, desired))
        if widening and registered:
            require_confinement(base, token)
    if apply and changed:
        _write(base, token, before, desired)
    if access == 'all' and not registered:
        # Registered just now (or would be): share it only if its file API is confined.
        if not apply:
            return True
        after = call(base, 'GET', API_PATH, token)
        require_confinement(base, token)
        shared = desired_connections(after.get('TERMINAL_SERVER_CONNECTIONS') or [], env, 'all')
        _write(base, token, after, shared)
        return True
    return changed


def _differs_list(current, desired):
    return len(current) != len(desired) or any(_differs(a, b) or _normalized(a) != _normalized(b) for a, b in zip(current, desired))


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--access', choices=ACCESS_MODES, default='keep', help='who may use the terminal (default: keep the current grants)')
    args = parser.parse_args(argv)
    env = load_env()
    base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
    try:
        # Validate before authentication; never generate or display a key here.
        desired_connections([], env)
        token = get_token(env, base)
        changed = reconcile(base, token, env, apply=not args.check, access=args.access)
        print('Open Terminal: ' + ('would update' if args.check and changed else 'updated' if changed else 'up to date'))
        return int(args.check and changed)
    except ApiError as exc:
        print('FAIL:', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(run())
