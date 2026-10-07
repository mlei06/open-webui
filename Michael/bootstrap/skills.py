#!/usr/bin/env python3
"""Install the skills in Michael/skills/ into Open WebUI; --check reads only.

One folder per skill: skills/<id>/SKILL.md with a frontmatter block (name, description) and the
instructions as Markdown. The id is the folder name. Each skill gets a public read grant, because Open WebUI
lists every skill a user can read to every model that has built-in tools: a prompt cannot hide one from a
preset, so the presets' system prompts say which skill to load for what. Skills that are not declared here
(a user's own, or ones made in the app) are never touched; ids in skills/retired.json are removed.
Idempotent: a second run changes nothing.
"""
import argparse
import json
import re
from pathlib import Path

from davy_connection import MICHAEL_DIR, ApiError, call, get_token, load_env

SKILLS_DIR = MICHAEL_DIR / 'skills'
PUBLIC_READ = [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]
FRONTMATTER = re.compile(r'\A---\n(.*?)\n---\n(.*)\Z', re.S)
SLUG = re.compile(r'[a-z0-9_-]+')


def parse(path):
    """(name, description, content) of one SKILL.md."""
    match = FRONTMATTER.match(path.read_text())
    if not match:
        raise ValueError(f'{path.parent.name}: SKILL.md needs a frontmatter block with name and description')
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(':')
        if sep:
            fields[key.strip()] = value.strip()
    name, description, content = fields.get('name'), fields.get('description'), match.group(2).strip()
    if not name or not description or not content:
        raise ValueError(f'{path.parent.name}: name, description and instructions are all required')
    return name, description, content + '\n'


def load():
    """{id: form} for every declared skill, and the list of retired ids."""
    declared = {}
    for folder in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        if not SLUG.fullmatch(folder.name) or not (folder / 'SKILL.md').is_file():
            raise ValueError(f'{folder.name}: a skill folder is a lowercase slug holding SKILL.md')
        name, description, content = parse(folder / 'SKILL.md')
        declared[folder.name] = {'id': folder.name, 'name': name, 'description': description, 'content': content,
                                 'meta': {'tags': []}, 'is_active': True, 'access_grants': PUBLIC_READ}
    names = [f['name'] for f in declared.values()]
    if len(set(names)) != len(names):
        raise ValueError('skill names must be unique')
    retired_file = SKILLS_DIR / 'retired.json'
    retired = json.loads(retired_file.read_text()) if retired_file.is_file() else []
    if set(retired) & set(declared):
        raise ValueError('a skill cannot be both declared and retired')
    return declared, retired


def grants(rows):
    return {(r.get('principal_type'), r.get('principal_id'), r.get('permission')) for r in rows or []}


def current(base, token, skill_id):
    try:
        return call(base, 'GET', f'/api/v1/skills/id/{skill_id}', token)
    except ApiError as exc:
        if 'HTTP 404' in str(exc) or 'HTTP 401' in str(exc):  # Open WebUI answers 401 for a skill it does not know
            return None
        raise


def drifts(live, form):
    return live is None or any(live.get(k) != form[k] for k in ('name', 'description', 'content', 'is_active')) \
        or grants(live.get('access_grants')) != grants(form['access_grants'])


def reconcile(base, token, declared, retired, apply):
    changed = False
    for skill_id, form in declared.items():
        live = current(base, token, skill_id)
        drift = drifts(live, form)
        changed |= drift
        if apply and drift:
            call(base, 'POST', f'/api/v1/skills/id/{skill_id}/update' if live else '/api/v1/skills/create', token, form)
        print(f'[{"DRIFT" if drift and not apply else "PASS"}] skill {skill_id}: '
              + ('installed' if drift and apply else 'would install' if drift else 'up to date'))
    for skill_id in retired:
        live = current(base, token, skill_id)
        changed |= live is not None
        if apply and live is not None and call(base, 'DELETE', f'/api/v1/skills/id/{skill_id}/delete', token) is not True:
            raise ApiError(f'Could not remove retired skill {skill_id}')
        print(f'[{"DRIFT" if live and not apply else "PASS"}] skill {skill_id}: '
              + ('removed' if live and apply else 'would remove' if live else 'absent'))
    return changed


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    try:
        declared, retired = load()
        env = load_env()
        base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
        token = get_token(env, base)
        changed = reconcile(base, token, declared, retired, apply=not args.check)
        if not args.check:
            changed = reconcile(base, token, declared, retired, apply=False)  # read-back proves the install
        return int(changed)
    except (ApiError, ValueError, OSError) as exc:
        print(f'[FAIL] {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(run())
