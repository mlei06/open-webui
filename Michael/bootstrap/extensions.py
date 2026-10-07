#!/usr/bin/env python3
"""Reconcile Michael/extensions.json; --check reads only. Preserve unrelated resources."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from davy_connection import MICHAEL_DIR, ApiError, call, get_token, load_env
from office_tools import bundle_delivery_source

MANIFEST = MICHAEL_DIR / 'extensions.json'


def load_manifest():
    doc = json.loads(MANIFEST.read_text())
    if doc.get('schemaVersion') != 1:
        raise ValueError('Unsupported extensions manifest version')
    seen = set()
    for item in doc['managed']:
        key = (item['kind'], item['id'])
        path = (MICHAEL_DIR / item['file']).resolve()
        if item['kind'] not in ('tools', 'functions') or key in seen or not path.is_relative_to(MICHAEL_DIR.resolve()) or not path.is_file():
            raise ValueError('Invalid managed extension declaration')
        compile(path.read_text(), str(path), 'exec')
        seen.add(key)
    for kind, ids in doc['retired'].items():
        if kind not in ('tools', 'functions') or any((kind, id) in seen for id in ids):
            raise ValueError('Invalid or conflicting retirement declaration')
    return doc


def backup(kind, row, valves):
    directory = MICHAEL_DIR / 'runtime' / 'extension-backup'
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = directory / f'{kind}-{stamp}.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump({'kind': kind, 'resource': row, 'valves': valves}, out)


def reconcile(base, token, doc, apply=False):
    inventory = {kind: {r['id']: r for r in call(base, 'GET', f'/api/v1/{kind}/', token)} for kind in ('tools', 'functions')}
    changed = False
    for item in doc['managed']:
        kind, id = item['kind'], item['id']
        endpoint = f'/api/v1/{kind}/id/{id}'
        current = call(base, 'GET', endpoint, token) if id in inventory[kind] else None
        form = {'id': id, 'name': item['name'], 'content': bundle_delivery_source((MICHAEL_DIR / item['file']).read_text()), 'meta': (current or {}).get('meta') or {}}
        if kind == 'tools':
            form['access_grants'] = item['access_grants']
        source_drift = current is None or any(current.get(k) != form[k] for k in ('name', 'content'))
        if kind == 'tools' and current is not None:
            # APIs enrich grant rows with database IDs; compare only permission fields.
            grants = lambda rows: {(r.get('principal_type'), r.get('principal_id'), r.get('permission')) for r in rows or []}
            source_drift |= grants(current.get('access_grants')) != grants(form['access_grants'])
        flag_drift = kind == 'functions' and (current is None or current.get('is_active') != item['active'] or current.get('is_global') != item['global_filter'])
        drift = source_drift or flag_drift
        changed |= drift
        if apply and drift:
            if current:
                backup(kind, current, call(base, 'GET', endpoint + '/valves', token))
            if source_drift:
                call(base, 'POST', endpoint + '/update' if current else f'/api/v1/{kind}/create', token, form)
            if kind == 'functions':
                saved = call(base, 'GET', endpoint, token)
                for key, value, suffix in [('is_active', item['active'], '/toggle'), ('is_global', item['global_filter'], '/toggle/global')]:
                    if saved.get(key) != value:
                        call(base, 'POST', endpoint + suffix, token)
        print(f'[{"DRIFT" if drift and not apply else "PASS"}] {id}: ' + ('reconciled' if drift and apply else 'would update' if drift else 'up to date'))
    for kind, ids in doc['retired'].items():
        for id in ids:
            present = id in inventory[kind]
            changed |= present
            if apply and present:
                endpoint = f'/api/v1/{kind}/id/{id}'
                row = call(base, 'GET', endpoint, token)
                backup(kind, row, call(base, 'GET', endpoint + '/valves', token))
                if call(base, 'DELETE', endpoint + '/delete', token) is not True:
                    raise ApiError(f'Could not remove retired extension {id}')
            print(f'[{"DRIFT" if present and not apply else "PASS"}] {id}: ' + ('removed' if present and apply else 'would remove' if present else 'absent'))
    return changed


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    try:
        doc = load_manifest()
        env = load_env()
        base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
        token = get_token(env, base)
        changed = reconcile(base, token, doc, apply=not args.check)
        if not args.check:
            # Independent read-back proves installation, flags, and removal.
            changed = reconcile(base, token, doc, apply=False)
        return int(changed)
    except (ApiError, ValueError, OSError) as exc:
        print(f'[FAIL] {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(run())
