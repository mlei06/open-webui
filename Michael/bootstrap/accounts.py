#!/usr/bin/env python3
"""Close self sign-up, make 'user' the default role and set the default user permissions we rely on.

Accounts are created only by an administrator (Admin Panel > Users > Add User, or
POST /api/v1/auths/add). Open WebUI keeps saved settings in its database and they override the
ENABLE_SIGNUP / DEFAULT_USER_ROLE environment variables compose passes, so on an existing volume
this script saves the two settings through the admin API:

  ENABLE_SIGNUP = false      anonymous visitors cannot register
  DEFAULT_USER_ROLE = user   an account made without an explicit role is never an admin
  workspace.knowledge = on   the default user permission to create their OWN knowledge bases. The Knowledge
                             Base Manager tool needs it (a user could not otherwise turn an attached SOP
                             into a knowledge base). It grants nothing else: other users' knowledge bases
                             stay private.
  features.automations = on  every user may schedule automations (Lenny's built-in automation tools, and the
                             Automations page). An automation runs as its owner with the preset's tools, so it
                             can use the same data and send mail as that user; the optional AUTOMATION_MAX_COUNT
                             and AUTOMATION_MIN_INTERVAL admin settings limit how many and how often.

Only the permissions listed in PERMISSIONS_WANT are changed; every other key of the permission block is kept.

The FIRST account on a fresh volume is not affected: Open WebUI lets the first sign-up through
whatever ENABLE_SIGNUP says and makes it the admin (provision.py creates it from
OPEN_WEBUI_ADMIN_EMAIL / OPEN_WEBUI_ADMIN_PASSWORD, then runs this script). Every later sign-up
is refused with 403.

The save replaces the whole admin config block, so the live block is read first and only the two
keys change. --check changes nothing and exits 1 when a setting differs. Standard library only.
"""

import argparse
import sys

from davy_connection import ApiError, call, get_token, load_env

WANT = {'ENABLE_SIGNUP': False, 'DEFAULT_USER_ROLE': 'user'}
PERMISSIONS = '/api/v1/users/default/permissions'
# (section, key) -> wanted value in the default user permissions
PERMISSIONS_WANT = {('workspace', 'knowledge'): True, ('features', 'automations'): True}


def run(argv=None):
    ap = argparse.ArgumentParser(description='Close sign-up and set the default role to user.')
    ap.add_argument('--check', action='store_true', help='change nothing; exit 1 if a setting differs')
    args = ap.parse_args(argv)
    ok = True

    def report(passed, msg):
        nonlocal ok
        ok = ok and passed
        print(f'[{"PASS" if passed else "FAIL"}] {msg}')

    env = load_env()
    base = (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')
    try:
        token = get_token(env, base)
        report(True, f'authenticated as admin at {base}')
        cfg = call(base, 'GET', '/api/v1/auths/admin/config', token) or {}
        diff = {k: v for k, v in WANT.items() if cfg.get(k) != v}
        if not diff:
            report(True, 'sign-up is closed and the default role is user')
        elif args.check:
            report(False, 'differs: ' + ', '.join(f'{k} should be {v}' for k, v in diff.items()) + ' (run without --check)')
        else:
            call(base, 'POST', '/api/v1/auths/admin/config', token, {**cfg, **WANT})
            now = call(base, 'GET', '/api/v1/auths/admin/config', token) or {}
            report(all(now.get(k) == v for k, v in WANT.items()), 'sign-up closed and default role set to user (' + ', '.join(diff) + ')')

        perms = call(base, 'GET', PERMISSIONS, token) or {}
        wrong = {k: v for k, v in PERMISSIONS_WANT.items() if (perms.get(k[0]) or {}).get(k[1]) is not v}
        if not wrong:
            report(True, 'default user permissions are as declared: ' + ', '.join('.'.join(k) for k in PERMISSIONS_WANT))
        elif args.check:
            report(False, 'default user permissions differ: ' + ', '.join(f'{".".join(k)} should be {v}' for k, v in wrong.items()) + ' (run without --check)')
        else:
            for (section, key), value in wrong.items():
                perms[section] = {**(perms.get(section) or {}), key: value}
            call(base, 'POST', PERMISSIONS, token, perms)
            now = call(base, 'GET', PERMISSIONS, token) or {}
            report(all((now.get(k[0]) or {}).get(k[1]) is v for k, v in PERMISSIONS_WANT.items()), 'default user permissions set: ' + ', '.join('.'.join(k) for k in wrong))
    except ApiError as e:
        report(False, str(e))
    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(run())
