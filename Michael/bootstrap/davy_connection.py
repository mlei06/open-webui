#!/usr/bin/env python3
"""Idempotently provision the Davy connection in a running Open WebUI.

Open WebUI persists connection settings in its database after first start and
those saved values override the environment, so on an existing data volume
OPENAI_API_BASE_URLS / OPENAI_API_KEYS alone may not take effect. This script
pushes them through the authenticated admin API:

  GET  /openai/config, POST /openai/config/update   (add/update connection)
  GET  /ollama/config, POST /ollama/config/update   (disable Ollama)
  POST /openai/verify, GET /openai/models/{idx}     (verification)

Standard library only. Secrets are never printed: only counts, indexes, and
fixed status text are written to the terminal.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

MICHAEL_DIR = Path(__file__).resolve().parent.parent
TIMEOUT = 60


def load_env():
    """Merge Michael/.env (or $MICHAEL_ENV_FILE) under the process environment."""
    env = {}
    path = Path(os.environ.get('MICHAEL_ENV_FILE') or MICHAEL_DIR / '.env')
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                v = v[1:-1]
            env[k.strip()] = v
    env.update({k: v for k, v in os.environ.items() if v != ''})
    return env


class ApiError(Exception):
    """Carries a status and a short, key-free description."""


def call(base, method, path, token=None, body=None):
    headers = {'Accept': 'application/json'}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read() or b'null')
    except urllib.error.HTTPError as e:
        # Response bodies may echo secrets; report the status code only.
        raise ApiError(f'{method} {path} -> HTTP {e.code}') from None
    except (urllib.error.URLError, OSError) as e:
        raise ApiError(f'{method} {path} -> {type(e).__name__}') from None


def norm(url):
    return url.strip().rstrip('/')


def get_token(env, base):
    if env.get('OPEN_WEBUI_ADMIN_API_KEY'):
        return env['OPEN_WEBUI_ADMIN_API_KEY']
    email, pw = env.get('OPEN_WEBUI_ADMIN_EMAIL'), env.get('OPEN_WEBUI_ADMIN_PASSWORD')
    if not (email and pw):
        raise ApiError('no admin credentials: set OPEN_WEBUI_ADMIN_API_KEY, or OPEN_WEBUI_ADMIN_EMAIL and OPEN_WEBUI_ADMIN_PASSWORD')
    res = call(base, 'POST', '/api/v1/auths/signin', body={'email': email, 'password': pw})
    if not isinstance(res, dict) or not res.get('token'):
        raise ApiError('sign-in returned no token')
    return res['token']


def desired_connections(env):
    urls = [norm(u) for u in env.get('OPENAI_API_BASE_URLS', '').split(';') if u.strip()]
    keys = [k.strip() for k in env.get('OPENAI_API_KEYS', '').split(';')]
    if not urls:
        raise ApiError('OPENAI_API_BASE_URLS is empty')
    keys += [''] * (len(urls) - len(keys))
    if not urls[0].startswith(('http://', 'https://')):
        raise ApiError('OPENAI_API_BASE_URLS must be http(s) URLs')
    return list(zip(urls, keys))


def upsert_openai(base, token, wanted):
    """Add/update each wanted connection by URL; leave other connections alone."""
    cfg = call(base, 'GET', '/openai/config', token)
    urls = list(cfg.get('OPENAI_API_BASE_URLS') or [])
    keys = list(cfg.get('OPENAI_API_KEYS') or [])
    keys += [''] * (len(urls) - len(keys))
    configs = dict(cfg.get('OPENAI_API_CONFIGS') or {})
    enabled = cfg.get('ENABLE_OPENAI_API')

    # Collapse duplicates of a wanted URL left by earlier runs or manual edits.
    wanted_urls = {u for u, _ in wanted}
    seen, keep = set(), []
    for i, u in enumerate(urls):
        if norm(u) in wanted_urls:
            if norm(u) in seen:
                continue
            seen.add(norm(u))
        keep.append(i)
    changed = len(keep) != len(urls)
    old_cfgs = {i: configs.get(str(i), {}) for i in keep}
    urls, keys = [urls[i] for i in keep], [keys[i] for i in keep]
    configs = {str(n): old_cfgs[i] for n, i in enumerate(keep) if old_cfgs[i]}

    indexes = []
    for url, key in wanted:
        idx = next((i for i, u in enumerate(urls) if norm(u) == url), None)
        if idx is None:
            urls.append(url)
            keys.append(key)
            idx = len(urls) - 1
            changed = True
        elif keys[idx] != key or urls[idx] != url:
            urls[idx], keys[idx] = url, key
            changed = True
        want = {**configs.get(str(idx), {}), 'enable': True, 'connection_type': 'external', 'auth_type': 'bearer'}
        if configs.get(str(idx)) != want:
            configs[str(idx)] = want
            changed = True
        indexes.append(idx)

    if enabled is not True:
        changed = True
    if changed:
        call(
            base,
            'POST',
            '/openai/config/update',
            token,
            {
                'ENABLE_OPENAI_API': True,
                'OPENAI_API_BASE_URLS': urls,
                'OPENAI_API_KEYS': keys,
                'OPENAI_API_CONFIGS': configs,
            },
        )
    return indexes, configs, changed


def disable_ollama(base, token):
    cfg = call(base, 'GET', '/ollama/config', token)
    if cfg.get('ENABLE_OLLAMA_API') is False:
        return False
    call(
        base,
        'POST',
        '/ollama/config/update',
        token,
        {
            'ENABLE_OLLAMA_API': False,
            'OLLAMA_BASE_URLS': cfg.get('OLLAMA_BASE_URLS') or [],
            'OLLAMA_API_CONFIGS': cfg.get('OLLAMA_API_CONFIGS') or {},
        },
    )
    return True


def model_ids(res):
    data = res.get('data') if isinstance(res, dict) else None
    return [m.get('id') for m in data or [] if isinstance(m, dict) and m.get('id')]


def main():
    env = load_env()
    base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
    ok = True

    def report(passed, msg):
        nonlocal ok
        ok = ok and passed
        print(f'[{"PASS" if passed else "FAIL"}] {msg}')

    try:
        wanted = desired_connections(env)
    except ApiError as e:
        print(f'[FAIL] {e}')
        return 1

    # The path is the in-container one; compose mounts runtime/certs at /certs.
    ca = env.get('AIOHTTP_CLIENT_SSL_CERT_FILE', '')
    if ca.startswith('/certs/'):
        host_ca = MICHAEL_DIR / 'runtime' / 'certs' / ca[len('/certs/'):]
        if not host_ca.is_file():
            report(False, f'CA bundle missing on host: Michael/runtime/certs/{host_ca.name} (container path {ca})')
        else:
            report(True, f'CA bundle present for container path {ca}')
    else:
        print(f'[SKIP] CA bundle check: AIOHTTP_CLIENT_SSL_CERT_FILE is not under /certs/ ({"unset" if not ca else "custom path"})')

    try:
        token = get_token(env, base)
        report(True, f'authenticated as admin at {base}')

        indexes, configs, changed = upsert_openai(base, token, wanted)
        report(True, 'OpenAI connection ' + ('added/updated' if changed else 'already up to date') + f' (index {", ".join(map(str, indexes))})')

        report(True, 'Ollama API ' + ('disabled' if disable_ollama(base, token) else 'already disabled'))

        for idx, (url, key) in zip(indexes, wanted):
            try:
                v = call(base, 'POST', '/openai/verify', token, {'url': url, 'key': key, 'config': configs.get(str(idx), {})})
                n = len(model_ids(v))
                report(n > 0, f'connection {idx} verifies, {n} model(s) listed by provider')
                listed = model_ids(call(base, 'GET', f'/openai/models/{idx}', token))
                report(bool(listed), f'Open WebUI lists {len(listed)} model(s) for connection {idx}')
            except ApiError as e:
                hint = ' (check the CA bundle and key; see README)' if 'HTTP 5' in str(e) or 'HTTP 4' in str(e) else ''
                report(False, f'connection {idx} verification failed: {e}{hint}')
    except ApiError as e:
        report(False, str(e))

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
