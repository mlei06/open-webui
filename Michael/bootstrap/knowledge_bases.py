#!/usr/bin/env python3
"""Idempotently create the Open WebUI knowledge bases declared in knowledge/manifest.json.

The committed Markdown files under knowledge/ are the seed. Through the authenticated admin
API this script:

  1. validates knowledge/manifest.json and reads every file it lists;
  2. creates each knowledge base that does not exist yet (matched by exact name) with a
     public read grant, so every user and every model preset can read it;
  3. uploads each seed file that is missing from the knowledge base and waits until Open
     WebUI has extracted and indexed it;
  4. reports seed files whose live content differs from the committed copy. Users may edit
     knowledge in the app (or through the Knowledge Base Manager), so a differing file is
     left alone unless --update is given; files and folders a user added are never touched,
     and nothing is ever deleted.

A knowledge base is identified by its name: renaming it in the app makes the next run create
a new one, so rename it in knowledge/manifest.json as well.

Options: --check changes nothing and exits 1 when something is missing; --update overwrites
live files that differ from the committed seed. Reuses the helpers of davy_connection.py.
Standard library only. Secrets are never printed: only names, counts and fixed status text.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid

from davy_connection import MICHAEL_DIR, ApiError, call, get_token, load_env

KNOWLEDGE_DIR = MICHAEL_DIR / 'knowledge'
MANIFEST = KNOWLEDGE_DIR / 'manifest.json'
PUBLIC_READ = [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]
UPLOAD_TIMEOUT = 600  # extraction and embedding run synchronously inside the upload request


class ConfigError(Exception):
    """A problem in knowledge/manifest.json or a file it lists; the message is secret-free."""


def load_manifest(path=MANIFEST, root=KNOWLEDGE_DIR):
    """Parse and validate the manifest; returns the knowledge bases, each with its files' text. Raises ConfigError."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        raise ConfigError(f'{path.name} is missing or not valid JSON') from None
    if not isinstance(doc, dict) or doc.get('schemaVersion') != 1:
        raise ConfigError(f'{path.name}: schemaVersion must be 1')
    kbs = doc.get('knowledge_bases')
    if not (isinstance(kbs, list) and kbs):
        raise ConfigError(f'{path.name}: knowledge_bases must be a non-empty list')
    errors, ids, names, out = [], set(), set(), []
    for i, kb in enumerate(kbs):
        kid = kb.get('id') if isinstance(kb, dict) else None
        where = f'knowledge_bases[{i}]' + (f' ({kid})' if isinstance(kid, str) else '')
        if not (isinstance(kid, str) and kid.strip()):
            errors.append(f'{where}: missing id')
            continue
        if kid in ids:
            errors.append(f'{where}: duplicate id')
        ids.add(kid)
        for key in ('name', 'description'):
            if not (isinstance(kb.get(key), str) and kb[key].strip()):
                errors.append(f'{where}: {key} must be a non-empty string')
        if kb.get('name') in names:
            errors.append(f'{where}: duplicate name')
        names.add(kb.get('name'))
        files = kb.get('files')
        if not (isinstance(files, list) and files and all(isinstance(f, str) and f for f in files)):
            errors.append(f'{where}: files must be a non-empty list of paths relative to knowledge/')
            continue
        texts, basenames = {}, set()
        for rel in files:
            target = (root / rel).resolve()
            if root.resolve() not in target.parents or target.suffix != '.md':
                errors.append(f'{where}: {rel} must be a .md file inside knowledge/')
                continue
            if target.name in basenames:
                errors.append(f'{where}: two files named {target.name}')
            basenames.add(target.name)
            try:
                text = target.read_text()
            except OSError:
                errors.append(f'{where}: cannot read {rel}')
                continue
            if not text.strip():
                errors.append(f'{where}: {rel} is empty')
            texts[target.name] = text
        out.append({**kb, 'texts': texts})
    if errors:
        raise ConfigError(f'{path.name}: ' + '; '.join(errors))
    return out


def grants_of(kb):
    return {(g.get('principal_type'), g.get('principal_id'), g.get('permission')) for g in (kb or {}).get('access_grants') or []}


def list_knowledge_bases(base, token):
    """Every knowledge base the admin can see, across pages."""
    out, page = [], 1
    while True:
        res = call(base, 'GET', f'/api/v1/knowledge/?page={page}', token) or {}
        items = res.get('items') or []
        out += items
        if not items or len(out) >= (res.get('total') or 0) or page > 1000:
            return out
        page += 1


def find_knowledge_base(base, token, name, kbs=None):
    """The one knowledge base called name, or None. Raises ApiError when the name is ambiguous."""
    found = [k for k in (kbs if kbs is not None else list_knowledge_bases(base, token)) if k.get('name') == name]
    if len(found) > 1:
        raise ApiError(f'{len(found)} knowledge bases are named "{name}"; rename or remove the extras')
    return found[0] if found else None


def knowledge_files(base, token, kb_id):
    """Root-level files of a knowledge base, as {filename: file record}."""
    out, page = {}, 1
    while True:
        res = call(base, 'GET', f'/api/v1/knowledge/{kb_id}/files?directory_id=&page={page}', token) or {}
        items = res.get('items') or []
        for item in items:
            f = item.get('file', item)
            out.setdefault(f.get('filename'), f)
        if not items or len(out) >= (res.get('total') or 0) or page > 1000:
            return out
        page += 1


def file_text(base, token, file_id):
    return (call(base, 'GET', f'/api/v1/files/{file_id}/data/content', token) or {}).get('content') or ''


QUOTES = str.maketrans({'\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"'})


def same(live, seed):
    """Open WebUI's text extraction straightens curly quotes, so compare with them straightened."""
    return live.translate(QUOTES).strip() == seed.translate(QUOTES).strip()


def upload(base, token, kb_id, filename, text):
    """Upload a Markdown file into a knowledge base and wait for extraction and indexing. Returns the file id."""
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: text/markdown\r\n\r\n'.encode(),
        text.encode(),
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n'.encode(),
        json.dumps({'knowledge_id': kb_id}).encode(),
        f'\r\n--{boundary}--\r\n'.encode(),
    ]
    req = urllib.request.Request(
        base + '/api/v1/files/?process=true&process_in_background=false',
        data=b''.join(parts),
        method='POST',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': f'multipart/form-data; boundary={boundary}', 'Accept': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT) as r:
            res = json.loads(r.read() or b'null') or {}
    except urllib.error.HTTPError as e:
        raise ApiError(f'upload of {filename} -> HTTP {e.code}') from None
    except (urllib.error.URLError, OSError) as e:
        raise ApiError(f'upload of {filename} -> {type(e).__name__}') from None
    return res.get('id')


def run(argv=None):
    ap = argparse.ArgumentParser(description='Create the knowledge bases of knowledge/manifest.json in Open WebUI.')
    ap.add_argument('--check', action='store_true', help='change nothing; exit 1 if a knowledge base or seed file is missing')
    ap.add_argument('--update', action='store_true', help='overwrite live files that differ from the committed seed')
    args = ap.parse_args(argv)
    ok = True

    def report(passed, msg):
        nonlocal ok
        ok = ok and passed
        print(f'[{"PASS" if passed else "FAIL"}] {msg}')

    env = load_env()
    base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
    try:
        kbs = load_manifest()
    except ConfigError as e:
        print(f'[FAIL] {e}')
        print('RESULT: FAIL')
        return 1
    report(True, f'manifest is valid ({len(kbs)} knowledge base(s), {sum(len(k["texts"]) for k in kbs)} file(s))')

    try:
        token = get_token(env, base)
        report(True, f'authenticated as admin at {base}')
        existing = list_knowledge_bases(base, token)
        for want in kbs:
            live = find_knowledge_base(base, token, want['name'], existing)
            if live is None:
                if args.check:
                    report(False, f'{want["name"]}: knowledge base is missing (run without --check)')
                    continue
                live = call(base, 'POST', '/api/v1/knowledge/create', token,
                            {'name': want['name'], 'description': want['description'], 'access_grants': PUBLIC_READ})
                report(True, f'{want["name"]}: knowledge base created')
                files = {}
            else:
                if ('user', '*', 'read') in grants_of(live):
                    report(True, f'{want["name"]}: knowledge base exists with public read')
                elif args.check:
                    report(False, f'{want["name"]}: knowledge base has no public read grant (run without --check)')
                else:
                    call(base, 'POST', f'/api/v1/knowledge/{live["id"]}/access/update', token,
                         {'access_grants': [*(live.get('access_grants') or []), *PUBLIC_READ]})
                    report(True, f'{want["name"]}: public read grant added')
                files = knowledge_files(base, token, live['id'])

            for filename, text in want['texts'].items():
                current = files.get(filename)
                if current is None:
                    if args.check:
                        report(False, f'{want["name"]}/{filename}: seed file is missing (run without --check)')
                        continue
                    upload(base, token, live['id'], filename, text)
                    created = knowledge_files(base, token, live['id']).get(filename)
                    ready = created is not None and same(file_text(base, token, created['id']), text)
                    report(ready, f'{want["name"]}/{filename}: ' + ('uploaded and indexed' if ready else 'uploaded but its text was not extracted'))
                elif same(file_text(base, token, current['id']), text):
                    report(True, f'{want["name"]}/{filename}: already up to date')
                elif args.update and not args.check:
                    call(base, 'POST', f'/api/v1/files/{current["id"]}/data/content/update', token, {'content': text})
                    done = same(file_text(base, token, current['id']), text)
                    report(done, f'{want["name"]}/{filename}: ' + ('replaced with the committed seed' if done else 'update did not apply'))
                else:
                    print(f'[NOTE] {want["name"]}/{filename}: differs from the committed seed (edited in the app, or the seed changed); '
                          'kept as is, use --update to overwrite it')
    except (ApiError, ConfigError) as e:
        report(False, str(e))

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(run())
