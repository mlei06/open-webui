#!/usr/bin/env python3
"""One provisioning command for the whole stack, against a running Open WebUI.

    python3 Michael/bootstrap/provision.py --init-env      # BEFORE `docker compose up`: add missing keys to .env
    python3 Michael/bootstrap/provision.py                 # after the stack is up: provision everything
    python3 Michael/bootstrap/provision.py --check         # change nothing; exit 1 if anything differs

Restart recipe (from the repository root):

    python3 Michael/bootstrap/provision.py --init-env
    docker compose --env-file Michael/.env -f Michael/docker-compose.yaml up -d --build
    python3 Michael/bootstrap/provision.py

It runs the existing bootstrap scripts, unchanged, in dependency order, and is idempotent: a re-run
changes nothing. Each script keeps its own conventions (PASS/FAIL lines, standard library only,
secrets never printed); this command only orders them, hands them one admin session, and adds the
two things no single script owns: first-run admin creation and the final access audit.

  1. wait          the stack answers /health
  1b. session key  NOTE when the running container has no WEBUI_SECRET_KEY (restarts would sign everyone out)
  2. admin         sign in with OPEN_WEBUI_ADMIN_EMAIL / OPEN_WEBUI_ADMIN_PASSWORD; on a fresh volume
                   (no account yet) create that account, which Open WebUI makes the admin
  2b. accounts     close self sign-up, default role user (accounts.py): only an admin creates accounts
  3. davy          model connection (davy_connection.py). An unreachable host is a NOTE, not a failure
  4. xai           model connection (xai_connection.py; skipped when XAI_API_KEY is empty)
  5. tool servers  translator gateway, employee directory, mail (mcp_servers.py, from mcp/mcp.json)
  6. filter        user context filter (user_context.py)
  7. audit         audit event function (audit_log.py)
  8. translator    Document Translator tool and base model (translator_tool.py)
  9. kb tool       Knowledge Base Manager tool (kb_manager_tool.py)
 10. office tools  slide and Word generator tools (office_tools.py)
 10b. extensions   managed visual/UI extensions and explicit retirements (extensions.py)
 10c. skills       the task guides in skills/ (skills.py): qdts, visualization, delegation, ...
 10d. terminal     the Open Terminal connection (open_terminal.py --access all, only if its file API is confined)
 11. knowledge     knowledge bases and their seed files (knowledge_bases.py)
 12. presets       the presets, the Review and send email action, web search, knowledge attachment
                   (presets.py)
 13. branding      Lenovo theme (branding.py; a NOTE when the plugin file or logo is not present)
 14. employees     only with --employees FILE: loads the directory data (seed_employees.py)
 15. mail relay    SMTP connection check without sending (smtp_check.py); only with MAIL_PROVIDER=smtp
 16. access        read-only audit that every preset, tool, tool server and knowledge base is usable
                   by all users (access.py)

Options: --check; --init-env (and --mail-src / --employee-src PATH); --only a,b and --skip a,b (step
names above: wait admin accounts davy xai mcp filter audit translator kbtool office knowledge presets branding
employees smtp access extensions); --employees FILE; --update-knowledge (overwrite seed files edited in the app);
--env-file FILE (default $MICHAEL_ENV_FILE, else Michael/.env).

Exit 0 when nothing FAILed (NOTEs are listed at the end and do not fail the run). Standard library
only. Secrets are never printed: the output of each script is secret-free by contract and is
masked again here for every secret value of the environment.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import access
import case_safety
import init_env
import smtp_check
import office_tools
from davy_connection import MICHAEL_DIR, ApiError, call, load_env

BOOTSTRAP = Path(__file__).resolve().parent
SECRET_HINTS = ('KEY', 'PASSWORD', 'TOKEN', 'SECRET')
HEALTH_WAIT = 300

# (name, title, script, soft, extra args when checking or None for "no --check", applies(env))
STEPS = [
    ('accounts', 'Sign-up closed, default role user', 'accounts.py', False, ['--check']),
    ('davy', 'Davy model connection', 'davy_connection.py', True, None),
    ('xai', 'xAI model connection', 'xai_connection.py', False, None),
    ('mcp', 'Tool servers (translator gateway, employee directory, mail, cases)', 'mcp_servers.py', False, ['--check']),
    ('filter', 'User context filter', 'user_context.py', False, None),
    ('audit', 'Audit event function', 'audit_log.py', False, None),
    ('translator', 'Document Translator tool', 'translator_tool.py', False, None),
    ('kbtool', 'Knowledge Base Manager tool', 'kb_manager_tool.py', False, ['--check']),
    ('office', 'Office document generator tools', 'office_tools.py', False, ['--check']),
    ('extensions', 'Managed visual/UI extensions and retired plugins', 'extensions.py', False, ['--check']),
    ('skills', 'Skills (task guides the presets load on demand)', 'skills.py', False, ['--check']),
    ('knowledge', 'Knowledge bases', 'knowledge_bases.py', False, ['--check']),
    ('terminal', 'Open Terminal connection (shared once its file API is confined)', 'open_terminal.py', False, ['--check']),
    ('presets', 'Presets, action, web search', 'presets.py', False, ['--check']),
    ('branding', 'Lenovo branding', 'branding.py', False, ['--check']),
]


class Run:
    """Collects the result: failures fail the run, notes are listed at the end."""

    def __init__(self, secrets):
        self.failed, self.notes, self.secrets = [], [], secrets

    def mask(self, text):
        for s in self.secrets:
            text = text.replace(s, '***')
        return text

    def line(self, kind, msg):
        print(f'  [{kind}] {self.mask(msg)}')

    def ok(self, msg):
        self.line('PASS', msg)

    def fail(self, step, msg):
        self.line('FAIL', msg)
        self.failed.append(step)

    def note(self, step, msg):
        self.line('NOTE', msg)
        self.notes.append(f'{step}: {msg}')


def secret_values(env):
    return sorted({v for k, v in env.items() if any(h in k for h in SECRET_HINTS) and len(v) >= 6}, key=len, reverse=True)


def base_url(env):
    return (env.get('OPEN_WEBUI_URL') or f'http://localhost:{env.get("OPEN_WEBUI_PORT") or 3000}').rstrip('/')


def wait_healthy(base, seconds):
    deadline = time.time() + seconds
    while True:
        try:
            with urllib.request.urlopen(base + '/health', timeout=5) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        if time.time() >= deadline:
            return False
        time.sleep(3)


def ensure_admin(env, base, check):
    """Return the admin session token, creating the first account on a fresh volume. Raises ApiError."""
    email, password = env.get('OPEN_WEBUI_ADMIN_EMAIL', ''), env.get('OPEN_WEBUI_ADMIN_PASSWORD', '')
    if not (email and password):
        raise ApiError('OPEN_WEBUI_ADMIN_EMAIL and OPEN_WEBUI_ADMIN_PASSWORD are required in Michael/.env (the account is created from them on a fresh volume)')
    cfg = call(base, 'GET', '/api/config') or {}
    if cfg.get('onboarding'):
        if check:
            raise ApiError('no account exists yet (run without --check to create the admin)')
        res = call(base, 'POST', '/api/v1/auths/signup', body={'name': 'Administrator', 'email': email, 'password': password, 'profile_image_url': '/user.png'})
        created = True
    else:
        try:
            res = call(base, 'POST', '/api/v1/auths/signin', body={'email': email, 'password': password})
        except ApiError:
            raise ApiError('sign-in failed: OPEN_WEBUI_ADMIN_EMAIL / OPEN_WEBUI_ADMIN_PASSWORD do not match the existing admin account') from None
        created = False
    if not isinstance(res, dict) or not res.get('token'):
        raise ApiError('sign-in returned no token')
    if res.get('role') != 'admin':
        raise ApiError('the account in OPEN_WEBUI_ADMIN_EMAIL is not an administrator')
    return res['token'], created


def run_script(run, step, title, script, args, child_env, soft, check):
    """Run one bootstrap script; print its lines; fail or note according to the exit code."""
    print(f'== {title}')
    proc = subprocess.run([sys.executable, str(BOOTSTRAP / script), *args], env=child_env, capture_output=True, text=True, cwd=str(BOOTSTRAP))
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip() and not ln.startswith('RESULT:')]
    for ln in lines:
        print('  ' + run.mask(ln))
    if proc.returncode == 0:
        return True
    problems = [run.mask(ln) for ln in lines if ln.startswith('[FAIL]')] or ['exit code ' + str(proc.returncode)]
    if soft:
        run.note(step, f'{title} did not complete ({problems[0][7:].strip() if problems[0].startswith("[FAIL]") else problems[0]}); continuing, re-run when it is reachable')
    else:
        run.fail(step, f'{title} failed' + (' (differs from the declaration)' if check else ''))
    return False


def container_secret_key(base, run_cmd=subprocess.run):
    """'set', 'missing' or None (cannot tell) for WEBUI_SECRET_KEY in the running Open WebUI container.

    The container is the compose open-webui service publishing the port of `base`. Docker missing, no such
    container or an unreadable answer is None: the check then says nothing rather than guessing. The value is
    never kept; only whether it is non-empty.
    """
    port = urllib.parse.urlparse(base).port
    if not port:
        return None

    def docker(*args):
        try:
            r = run_cmd(['docker', *args], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    ids = (docker('ps', '-q', '--filter', f'publish={port}', '--filter', 'label=com.docker.compose.service=open-webui') or '').split()
    if len(ids) != 1:
        return None
    try:
        entries = json.loads(docker('inspect', '--format', '{{json .Config.Env}}', ids[0]) or '')
    except ValueError:
        return None
    value = next((e.split('=', 1)[1] for e in entries if e.startswith('WEBUI_SECRET_KEY=')), '')
    return 'set' if value.strip() else 'missing'


SECRET_KEY_NOTE = (
    'the running container has no WEBUI_SECRET_KEY, so every restart can generate a new one: that signs everyone out '
    '(browsers stuck on the loading screen) and makes saved tool keys, such as the translator key, unreadable. Run '
    'provision.py --init-env, then recreate the container (docker compose up -d); browsers need a one-time sign-out afterwards'
)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Provision the whole stack against a running Open WebUI (idempotent).', formatter_class=argparse.RawDescriptionHelpFormatter, epilog='Restart recipe and the step list are in the module docstring (see the file header).')
    ap.add_argument('--init-env', action='store_true', help='only add the missing non-secret or generated keys (including WEBUI_SECRET_KEY) to .env, then stop (run before docker compose up)')
    ap.add_argument('--mail-src', help='with --init-env: the mail-service clone')
    ap.add_argument('--employee-src', help='with --init-env: the employee-directory clone')
    ap.add_argument('--cases-src', help='with --init-env: the devqdts cases checkout')
    ap.add_argument('--check', action='store_true', help='change nothing; exit 1 if anything differs')
    ap.add_argument('--only', default='', help='comma-separated step names to run')
    ap.add_argument('--skip', default='', help='comma-separated step names to skip')
    ap.add_argument('--employees', help='employees JSON file to load into the directory (stays outside the repository)')
    ap.add_argument('--update-knowledge', action='store_true', help='overwrite knowledge seed files that were edited in the app')
    ap.add_argument('--env-file', help='private env file (default $MICHAEL_ENV_FILE, else Michael/.env)')
    ap.add_argument('--starter-file', help='validate and stage the approved PowerPoint starter before provisioning')
    args = ap.parse_args(argv)

    if args.env_file:
        args.env_file = str(Path(args.env_file).expanduser().resolve())
        os.environ['MICHAEL_ENV_FILE'] = args.env_file
    if args.init_env:
        extra = [a for flag, v in (('--mail-src', args.mail_src), ('--employee-src', args.employee_src), ('--cases-src', args.cases_src)) if v for a in (flag, v)]
        return init_env.run(extra + (['--env-file', args.env_file] if args.env_file else []) + (['--check'] if args.check else []))

    env = load_env()
    try:
        case_safety.validate_env(env)
    except ApiError as e:
        print(f'[FAIL] {e}\nRESULT: FAIL')
        return 1
    base = base_url(env)
    only = {s for s in args.only.split(',') if s}
    skip = {s for s in args.skip.split(',') if s}
    known = {s[0] for s in STEPS} | {'wait', 'admin', 'employees', 'smtp', 'access'}
    if (only | skip) - known:
        ap.error('Unknown step(s): ' + ', '.join(sorted((only | skip) - known)))
    want = lambda name: (not only or name in only) and name not in skip  # noqa: E731
    if args.starter_file and not want('office'):
        ap.error('--starter-file requires the office step')
    run = Run(secret_values(env))
    child_env = {**os.environ}
    if args.env_file:
        child_env['MICHAEL_ENV_FILE'] = args.env_file

    # Fail before creating accounts or changing server configuration when required
    # artwork is absent or differs from the checked-in declaration.
    if want('office'):
        try:
            office_tools.configured_starter(args.starter_file, args.check)
        except (ValueError, OSError) as exc:
            run.fail('office', str(exc))
            print('RESULT: FAIL')
            return 1

    print(f'Provisioning {base}' + (' (check only: nothing is changed)' if args.check else ''))
    print('== Open WebUI is up')
    if not wait_healthy(base, 10 if args.check else HEALTH_WAIT):
        run.fail('wait', f'{base} does not answer /health (start the stack first)')
        print('RESULT: FAIL')
        return 1
    run.ok('answers /health')

    print('== Session key')
    key_state = container_secret_key(base)
    if key_state == 'set':
        run.ok('the container has a WEBUI_SECRET_KEY (sessions and saved tool keys survive restarts)')
    elif key_state == 'missing':
        run.note('secret-key', SECRET_KEY_NOTE)
    else:
        print('  [SKIP] could not inspect the Open WebUI container (docker or the container is not reachable from here)')

    print('== Admin account')
    try:
        token, created = ensure_admin(env, base, args.check)
        case_safety.validate_live(base, token, env)
        run.ok('first admin account created from OPEN_WEBUI_ADMIN_EMAIL (fresh volume)' if created else 'signed in as admin')
    except ApiError as e:
        run.fail('admin', str(e))
        print('RESULT: FAIL')
        return 1
    child_env['OPEN_WEBUI_ADMIN_TOKEN'] = token
    run.secrets.append(token)

    for step, title, script, soft, check_args in STEPS:
        if not want(step):
            continue
        if step == 'xai' and not env.get('XAI_API_KEY', '').strip():
            print(f'== {title}\n  [SKIP] XAI_API_KEY is empty')
            continue
        if step == 'translator' and not (env.get('TRANSLATOR_GATEWAY_URL') and env.get('TRANSLATOR_API_KEY')):
            print(f'== {title}')
            run.note(step, 'TRANSLATOR_GATEWAY_URL / TRANSLATOR_API_KEY are not set: translator tool skipped')
            continue
        if step == 'branding':
            missing = [n for n, p in (('tools/theme_designer_pro.py', MICHAEL_DIR / 'tools' / 'theme_designer_pro.py'), ('runtime/brand/lenovo-logo.svg', MICHAEL_DIR / 'runtime' / 'brand' / 'lenovo-logo.svg')) if not p.is_file()]
            if missing and not (MICHAEL_DIR / 'runtime' / 'brand' / 'lenovo-logo.png').is_file():
                print(f'== {title}')
                run.note(step, 'branding not applied, missing ' + ', '.join(missing) + ' (private files kept outside git; see docs/branding.md)')
                continue
        if args.check:
            if check_args is None:
                if step in ('davy', 'xai', 'filter', 'audit', 'translator'):
                    print(f'== {title}\n  [SKIP] no read-only mode; the access audit below checks its result')
                continue
            args_ = list(check_args)
        else:
            args_ = ['--update'] if step == 'knowledge' and args.update_knowledge else []
        if step == 'terminal':
            args_ += ['--access', 'all']  # refuses to share a terminal whose file API is not confined
        if step == 'office' and args.starter_file:
            args_ += ['--starter-file', str(Path(args.starter_file).expanduser().resolve())]
        run_script(run, step, title, script, args_, child_env, soft, args.check)

    if want('employees') and args.employees:
        run_script(run, 'employees', 'Employee directory data', 'seed_employees.py', [args.employees] + (['--dry-run'] if args.check else []), child_env, False, args.check)

    if want('smtp') and env.get('MAIL_PROVIDER', '').strip() == 'smtp':
        print('== Mail relay connection (no message is sent)')
        results = []
        code = smtp_check.check(env, lambda ok, msg: results.append((ok, msg)))
        for ok, msg in results:
            if ok:
                run.ok(msg)
        if code == 1:
            run.fail('smtp', results[-1][1])
        elif code == 2:
            run.note('smtp', results[-1][1] + ' (environment limit, not a wiring fault)')
    elif want('smtp'):
        print('== Mail relay connection\n  [SKIP] MAIL_PROVIDER is not smtp (mock provider: nothing is sent)')

    if want('access'):
        print('== Access audit (every preset, tool, tool server and knowledge base usable by all users)')
        failures = []

        def report(ok, msg):
            run.line('PASS' if ok else 'FAIL', msg)
            if not ok:
                failures.append(msg)

        try:
            access.audit(base, token, env, report)
        except ApiError as e:
            report(False, str(e))
        if failures:
            run.failed.append('access')

    print()
    if run.notes:
        print(f'{len(run.notes)} note(s), not failures:')
        for n in run.notes:
            print('  - ' + run.mask(n))
    if run.failed:
        print('FAILED steps: ' + ', '.join(dict.fromkeys(run.failed)))
        print('RESULT: FAIL')
        return 1
    print('RESULT: PASS' + (f' (with {len(run.notes)} note(s))' if run.notes else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
