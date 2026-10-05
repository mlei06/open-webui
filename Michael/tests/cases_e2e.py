#!/usr/bin/env python3
"""Own fresh Docker project/port/volumes; synthetic cases and local deterministic model only.

python3 Michael/tests/cases_e2e.py --cases-src /approved/devqdts/checkout
No private env, case DB, provider credentials or CA files are read. Never drives live services.
"""
import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MICHAEL = HERE.parent
sys.path.insert(0, str(MICHAEL / 'bootstrap'))
sys.path.insert(0, str(HERE / 'fixtures'))
from cases_source import build
from davy_connection import call, get_token
from provision import ensure_admin
from stack_e2e import Stack, make_user


def cmd(argv, **kw):
    p = subprocess.run(argv, capture_output=True, text=True, **kw)
    if p.returncode:
        # No captured bodies/config are ever emitted; some commands carry credentials.
        raise RuntimeError(f'command failed ({p.returncode}): {argv[0]} {argv[1]}')
    return p.stdout


def live_identity():
    # Only inspect exact live WebUI name; no lifecycle commands.
    rows = cmd(['docker', 'ps', '--no-trunc', '--filter', 'name=^/michael-open-webui-1$', '--format', '{{.ID}} {{.Status}} {{.Ports}}']).strip()
    if not rows or ':3000->' not in rows:
        raise RuntimeError('live WebUI is absent or not on expected port; refuse validation')
    return rows.split()[0]


PROBE = r'''
import asyncio, json, sys
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
key = json.load(sys.stdin)['key']
async def main():
    url = 'http://qdts-cases:8000/mcp'
    async with httpx.AsyncClient() as c:
        r = await c.post(url, json={})
        assert r.status_code == 401
        r = await c.post(url, headers={'Authorization': 'Bearer wrong-invented-key'}, json={})
        assert r.status_code == 401
    async with streamablehttp_client(url, headers={'Authorization': 'Bearer ' + key}) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert {'search_cases','get_case','get_case_notes'} <= {t.name for t in tools}
            # Extra tools in newer backward-compatible services are intentionally ignored.
            schema = next(t.inputSchema for t in tools if t.name == 'search_cases')
            result = await session.call_tool('search_cases', {'query':'privatequartz','include_notes':True})
            assert not result.isError
            data = result.structuredContent
            if not data:
                data = json.loads(result.content[0].text)
            if 'result' in data: data = data['result']
            assert data['total'] == 5, data.keys()
            result = await session.call_tool('get_case_notes', {'id':'QDTS-26-000001'})
            assert not result.isError and 'privatequartz' in str(result)
            print('PASS auth, discovery, private-note search and lookup; closure sort supported=' + str('closed_newest' in json.dumps(schema)))
asyncio.run(main())
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cases-src', required=True)
    ap.add_argument('--port', type=int, default=31933)
    args = ap.parse_args()
    src = Path(args.cases_src).resolve()
    if args.port == 3000 or not (1024 <= args.port <= 65535):
        raise ValueError('distinct unprivileged test port required')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', args.port))  # refuse a port already in use
    before = live_identity()
    print('PASS live WebUI present on port 3000; isolated validation starting', flush=True)
    tag = secrets.token_hex(4)
    project = 'qdts-m33-test-' + tag
    volume = project + '-webui'
    image = project + '-cases:local'
    runtime = MICHAEL / 'runtime'
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cases-e2e-', dir=runtime) as temp:
        root = Path(temp).resolve()
        fixture = root / 'source'
        build(fixture, src)
        fixture.chmod(0o755)
        certs = root / 'certs'
        certs.mkdir()
        shutil.copyfile('/etc/ssl/certs/ca-certificates.crt', certs / 'ca-bundle.pem')
        env = {'COMPOSE_PROJECT_NAME': project, 'OPEN_WEBUI_PORT': str(args.port), 'OPEN_WEBUI_VOLUME': volume,
               'OPENAI_API_BASE_URLS': 'http://model-fixture:8000/v1', 'OPENAI_API_KEYS': 'synthetic-not-a-real-key',
               'OPEN_WEBUI_ADMIN_EMAIL': 'admin@example.invalid', 'OPEN_WEBUI_ADMIN_PASSWORD': secrets.token_urlsafe(24),
               'WEBUI_SECRET_KEY': secrets.token_urlsafe(48), 'ENABLE_WEB_SEARCH': 'false', 'XAI_API_KEY': '',
               'QDTS_CASES_SRC': str(src), 'QDTS_CASES_IMAGE': image, 'QDTS_DEVQDTS_DATA': str(fixture),
               'QDTS_MCP_API_KEY': secrets.token_urlsafe(32), 'QDTS_CUSTOMER_NAMES': 'plain',
               'OPEN_WEBUI_URL': f'http://127.0.0.1:{args.port}', 'MAIL_PROVIDER': 'mock'}
        envpath = root / 'test.env'
        envpath.write_text(''.join(f'{k}={v}\n' for k, v in env.items()))
        envpath.chmod(0o600)
        overlay = root / 'compose.yaml'
        overlay.write_text('services:\n  open-webui:\n    env_file: !override [' + json.dumps(str(envpath)) + ']\n'
                           '    volumes:\n      - ' + json.dumps(str(certs) + ':/certs:ro') + '\n'
                           '  model-fixture:\n    image: python:3.12-slim\n    command: [python, /fixture.py]\n'
                           '    volumes:\n      - ' + json.dumps(str(HERE / 'fixtures/cases_model.py') + ':/fixture.py:ro') + '\n')
        config = root / 'mcp.json'
        doc = json.loads((MICHAEL / 'mcp/mcp.json').read_text())
        doc['servers'] = [s for s in doc['servers'] if s['id'] == 'qdts']
        config.write_text(json.dumps(doc))
        compose = ['docker', 'compose', '-p', project, '--env-file', str(envpath), '-f', str(MICHAEL / 'docker-compose.yaml'), '-f', str(overlay)]
        # Drop ambient provider/admin/model env overrides. No .env is ever loaded.
        child_env = {k: v for k, v in os.environ.items() if not (k.startswith(('OPENAI_', 'OPEN_WEBUI_', 'PRESETS_', 'TRANSLATOR_', 'XAI_', 'QDTS_', 'PERPLEXITY_')) or k == 'MICHAEL_ENV_FILE')}
        child_env.update(env)
        child_env['MICHAEL_ENV_FILE'] = str(envpath)
        started = False
        created = False
        try:
            # Required primary devqdts source context only; no writes into it.
            cmd(['docker', 'build', '-f', str(src / 'Dockerfile.cases'), '-t', image, str(src)], timeout=600)
            print('PASS cases image built from read-only source checkout', flush=True)
            cmd(['docker', 'volume', 'create', volume])
            created = True
            started = True  # cleanup even after partial compose startup
            cmd(compose + ['--profile', 'index', 'run', '--rm', '--no-deps', 'qdts-indexer'], env=child_env, timeout=120)
            print('PASS compose indexer built invented cases as uid 10001', flush=True)
            cmd(compose + ['up', '-d', '--no-build', 'open-webui', 'qdts-cases', 'model-fixture'], env=child_env, timeout=180)
            stack = Stack(env)
            for _ in range(150):
                try:
                    if call(stack.base, 'GET', '/health'):
                        break
                except Exception:
                    pass
                time.sleep(2)
            else:
                raise RuntimeError('throwaway WebUI health timeout')
            cmd(['docker', 'exec', stack.container, 'test', '-s', '/certs/ca-bundle.pem'])
            print('PASS cert bundle visible inside throwaway WebUI', flush=True)
            token, _ = ensure_admin(env, stack.base, False)
            child_env['OPEN_WEBUI_ADMIN_TOKEN'] = token
            # Persist the fresh local-only test model config; no production provider is contacted.
            call(stack.base, 'POST', '/openai/config/update', token, {'ENABLE_OPENAI_API': True,
                 'OPENAI_API_BASE_URLS': [env['OPENAI_API_BASE_URLS']], 'OPENAI_API_KEYS': [env['OPENAI_API_KEYS']],
                 'OPENAI_API_CONFIGS': {}})
            for _ in range(2):
                cmd([sys.executable, str(MICHAEL / 'bootstrap/mcp_servers.py'), '--config', str(config)], env=child_env, timeout=120)
                cmd([sys.executable, str(MICHAEL / 'bootstrap/provision.py'), '--only', 'filter,presets'], env=child_env, timeout=180)
            cmd([sys.executable, str(MICHAEL / 'bootstrap/mcp_servers.py'), '--config', str(config), '--check'], env=child_env, timeout=120)
            cmd([sys.executable, str(MICHAEL / 'bootstrap/presets.py'), '--check'], env=child_env, timeout=120)
            print('PASS provisioning twice and read-only drift checks', flush=True)
            print(cmd(['docker', 'exec', '-i', stack.container, 'python', '-c', PROBE], input=json.dumps({'key': env['QDTS_MCP_API_KEY']}), timeout=120).strip(), flush=True)
            user = make_user(stack, token, 'Fixture One', 'fixtureone@example.invalid')
            models = {m['id']: m for m in call(stack.base, 'GET', '/api/models?refresh=true', user['token'])['data']}
            tools = {t['id'] for t in call(stack.base, 'GET', '/api/v1/tools/', user['token'])}
            assert 'server:mcp:qdts' in tools
            for model in ('lenny', 'case-assistant'):
                assert model in models
                ids = models[model]['info']['meta']['toolIds']
                assert 'server:mcp:qdts' in ids
                if model == 'case-assistant':
                    assert ids == ['server:mcp:qdts']
                for marker in ('MY', 'BLOCKED', 'CLOSED', 'PEOPLE', 'PRIVATE'):
                    res = stack.chat(user, model, 'TEST_' + marker, wait=60, tries=1)
                    assert not res['timeout'] and res['text'], f'{model}/{marker} did not finish'
                    assert any(t.endswith('search_cases' if marker in ('MY', 'BLOCKED', 'CLOSED') else 'get_case_notes') for t in res['tools']), f'{model}/{marker} missing tool'
                    print(f'PASS ordinary-user native tool loop {model}/{marker}', flush=True)
            # Saved outside connection must fail closed even with no XAI env key.
            call(stack.base, 'POST', '/openai/config/update', token, {'ENABLE_OPENAI_API': True,
                 'OPENAI_API_BASE_URLS': ['https://outside.example.invalid/v1'], 'OPENAI_API_KEYS': ['synthetic'], 'OPENAI_API_CONFIGS': {}})
            refused = subprocess.run([sys.executable, str(MICHAEL / 'bootstrap/mcp_servers.py'), '--config', str(config)], env=child_env, capture_output=True, text=True, timeout=60)
            assert refused.returncode == 1 and 'saved non-Davy' in refused.stdout
            print('PASS saved outside connection refused', flush=True)
        finally:
            if started:
                cmd(compose + ['down', '-v'], env=child_env, timeout=120)
            if created:
                cmd(['docker', 'volume', 'rm', volume])
            after = live_identity()
            assert after == before, 'live WebUI identity changed during validation'
            print('PASS live WebUI unchanged; own project and fresh volumes removed', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
