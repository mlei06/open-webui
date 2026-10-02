#!/usr/bin/env python3
"""Idempotently provision the xAI (Grok) connection in a running Open WebUI.

Adds or updates ONE OpenAI-compatible connection (XAI_API_BASE_URL with
XAI_API_KEY as bearer key) beside the existing ones, through the same admin
API as davy_connection.py. The connection is matched by URL, so re-runs never
duplicate it, and every other connection (the Davy one included) is kept as it
is. A no-op when XAI_API_KEY is empty.

xAI is an OUTSIDE service: send it only synthetic or approved data. Open WebUI
trusts only the CA file named by AIOHTTP_CLIENT_SSL_CERT_FILE, so the Davy
bundle alone cannot verify api.x.ai; run bootstrap/build_ca_bundle.py first.

Standard library only. The key is never printed: every message that may carry
response text is masked, and only status codes and counts are written.
"""

import sys

from davy_connection import ApiError, call, get_token, load_env, model_ids, norm, upsert_openai

DEFAULT_URL = 'https://api.x.ai/v1'


def mask(text, secrets):
    for s in secrets:
        if s:
            text = text.replace(s, '***')
    return text


def masked_call(base, method, path, token, secrets, body=None):
    """Like davy_connection.call, but keep a masked, short error detail."""
    try:
        return call(base, method, path, token, body)
    except ApiError as e:
        raise ApiError(mask(str(e), secrets)) from None


def main():
    env = load_env()
    key = env.get('XAI_API_KEY', '').strip()
    if not key:
        print('[SKIP] XAI_API_KEY is empty; nothing to do')
        print('RESULT: PASS')
        return 0
    url = norm(env.get('XAI_API_BASE_URL') or DEFAULT_URL)
    base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
    secrets = [key, env.get('OPEN_WEBUI_ADMIN_API_KEY', ''), env.get('OPEN_WEBUI_ADMIN_PASSWORD', '')]
    ok = True

    def report(passed, msg):
        nonlocal ok
        ok = ok and passed
        print(f'[{"PASS" if passed else "FAIL"}] {mask(msg, secrets)}')

    if not url.startswith(('http://', 'https://')):
        report(False, 'XAI_API_BASE_URL must be an http(s) URL')
        print('RESULT: FAIL')
        return 1

    try:
        token = get_token(env, base)
        report(True, f'authenticated as admin at {base}')

        indexes, configs, changed = upsert_openai(base, token, [(url, key)])
        idx = indexes[0]
        report(True, f'xAI connection {"added/updated" if changed else "already up to date"} (index {idx})')

        try:
            v = masked_call(base, 'POST', '/openai/verify', token, secrets, {'url': url, 'key': key, 'config': configs.get(str(idx), {})})
            n = len(model_ids(v))
            report(n > 0, f'xAI connection {idx} verifies, {n} model(s) listed by provider')
            listed = model_ids(masked_call(base, 'GET', f'/openai/models/{idx}', token, secrets))
            report(bool(listed), f'Open WebUI lists {len(listed)} model(s) for connection {idx}')
            if listed:
                print('models: ' + ', '.join(sorted(listed)))
        except ApiError as e:
            report(
                False,
                f'xAI connection {idx} verification failed: {e} '
                '(wrong XAI_API_KEY, or the CA bundle lacks the public CAs: run bootstrap/build_ca_bundle.py and restart the stack)',
            )
    except ApiError as e:
        report(False, str(e))

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
