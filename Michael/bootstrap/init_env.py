#!/usr/bin/env python3
"""Add the missing, non-secret or generated keys to the private Michael/.env.

Run it once on a new machine (or after pulling this repository) BEFORE `docker compose up`:

    python3 Michael/bootstrap/init_env.py [--mail-src PATH] [--employee-src PATH] [--env-file FILE]
    python3 Michael/bootstrap/provision.py --init-env ...        # the same thing

It only ever ADDS what is missing. It never overwrites a value that is already set, never
rewrites other lines, and never prints a value: the output names keys and says added, kept or
skipped. A key that is present but empty is filled in place; a key that is absent is appended.
The file is written with mode 600. What it manages:

  MAIL_SERVICE_SRC          the mail-service repository clone compose builds from. Taken from
                            --mail-src, else found next to the other projects (see find_source).
  EMPLOYEE_DIRECTORY_SRC    the same for the employee-directory clone (--employee-src).
  MAIL_PROVIDER             smtp when SMTP_HOST is set (the owner's relay), otherwise mock.
  MAIL_SMTP_TLS_VERIFY      false when the provider is smtp (see the risk below), otherwise nothing.
  WEBUI_SECRET_KEY          a generated random key (48 random bytes, urlsafe) that Open WebUI signs sessions with and
                            derives the tool-key encryption from. Without it every container restart makes a new one:
                            everyone is signed out and saved tool keys become unreadable. Never overwritten once set.
  MAIL_MCP_API_KEY          a generated random key (the one variable that both compose, for the
                            mail service, and mcp/mcp.json, for the Open WebUI registration, use).

The preset base model is NOT written: the committed default is Gemma and PRESETS_BASE_MODEL is an
optional switch (see .env.example).

MAIL_SMTP_TLS_VERIFY=false RISK. The mail relay's certificate cannot be verified (an internal
authority and no DNS name in it), so the owner chose to turn certificate checking off for it. The
connection stays encrypted (STARTTLS is still required, there is no plaintext fallback), but
anyone who can intercept the connection to the relay can pose as it and read the SMTP credentials
and every message. Only use it on a network you trust. Prefer MAIL_SMTP_CA_FILE with the issuing
authority's certificate whenever you can get it; then delete the false line.

Standard library only.
"""

import argparse
import os
import secrets
import sys
import tempfile
from pathlib import Path

from davy_connection import MICHAEL_DIR

REPO_ROOT = MICHAEL_DIR.parent
TLS_COMMENT = (
    '# Added by init_env.py. The mail relay certificate cannot be verified (internal authority, no DNS name in it), so\n'
    '# the owner chose to skip certificate checking for it. RISK: the connection stays encrypted (STARTTLS is required)\n'
    '# but anyone who can intercept it can pose as the relay and read the SMTP credentials and every message. Use it only\n'
    '# on a trusted network; prefer MAIL_SMTP_CA_FILE with the issuing authority certificate and then delete this line.\n'
)


SECRET_KEY_COMMENT = (
    '# Added by init_env.py: the key Open WebUI signs sessions with and derives the tool-key encryption from. Keep it stable and\n'
    '# private. If it changes, everyone is signed out (browsers need a one-time sign-out) and saved tool keys must be re-entered.\n'
)


def env_path(arg=None):
    return Path(arg or os.environ.get('MICHAEL_ENV_FILE') or MICHAEL_DIR / '.env')


def parse(lines):
    """{key: (line index, value)} of the KEY=VALUE lines; the last occurrence wins, like compose."""
    out = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            continue
        key, value = stripped.split('=', 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        out[key.strip()] = (i, value)
    return out


def candidate_dirs(name, extra_roots=()):
    """Places a clone of project `name` can be: the firstmate projects layout, then the usual spots."""
    home = Path.home()
    roots = [Path(p) for p in (os.environ.get('FIRSTMATE_HOME'),) if p]
    roots += [home / 'github' / 'firstmate', home / 'firstmate']
    found = [r / 'projects' / name for r in roots]
    found += [Path(r) / name for r in extra_roots]
    found += [REPO_ROOT.parent / name, home / 'projects' / name, home / name]
    return found


def looks_like(path, package):
    return (path / 'Dockerfile').is_file() and (path / 'src' / package).is_dir()


def find_source(name, package, explicit=None):
    """The clone of `name` (a path with a Dockerfile and src/<package>), or None. Returns (path, why)."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return (path, 'argument') if looks_like(path, package) else (None, f'{path} is not a {name} checkout (no Dockerfile or src/{package})')
    for cand in candidate_dirs(name):
        cand = cand.resolve()
        if looks_like(cand, package):
            return cand, 'found'
    return None, f'no {name} checkout found; pass --{"mail" if name == "mail-service" else "employee"}-src PATH'


def mail_source_supports_tls_verify(path):
    """True when the mail-service checkout has MAIL_SMTP_TLS_VERIFY (a later change of that repository)."""
    try:
        return 'smtp_tls_verify' in (path / 'src' / 'mail_service' / 'config.py').read_text()
    except OSError:
        return False


def plan(env, mail_src=None, employee_src=None):
    """What to add: a list of (key, value-or-callable, comment-or-None). `env` is the parsed {key: (i, value)}."""
    have = lambda k: bool((env.get(k) or (0, ''))[1].strip())  # noqa: E731
    notes, wanted = [], []

    for key, name, package, arg in (
        ('MAIL_SERVICE_SRC', 'mail-service', 'mail_service', mail_src),
        ('EMPLOYEE_DIRECTORY_SRC', 'employee-directory', 'employee_directory', employee_src),
    ):
        if have(key):
            if arg and str(Path(arg).expanduser().resolve()) != env[key][1].strip():
                notes.append(f'{key} is already set; it is never overwritten (edit it by hand to change it)')
            continue
        path, why = find_source(name, package, arg)
        if path is None:
            notes.append(f'{key}: {why}')
        else:
            if key == 'MAIL_SERVICE_SRC' and not mail_source_supports_tls_verify(path):
                notes.append('MAIL_SERVICE_SRC: this checkout has no MAIL_SMTP_TLS_VERIFY yet; update it before building (the relay certificate cannot be verified without it)')
            wanted.append((key, str(path), None))

    smtp = have('SMTP_HOST')
    if not have('MAIL_PROVIDER'):
        wanted.append(('MAIL_PROVIDER', 'smtp' if smtp else 'mock', None))
    provider = (env.get('MAIL_PROVIDER') or (0, ''))[1].strip() or ('smtp' if smtp else 'mock')
    if provider == 'smtp' and not have('MAIL_SMTP_TLS_VERIFY'):
        wanted.append(('MAIL_SMTP_TLS_VERIFY', 'false', TLS_COMMENT))
    if not have('WEBUI_SECRET_KEY'):
        wanted.append(('WEBUI_SECRET_KEY', lambda: secrets.token_urlsafe(48), SECRET_KEY_COMMENT))
    if not have('MAIL_MCP_API_KEY'):
        wanted.append(('MAIL_MCP_API_KEY', lambda: secrets.token_urlsafe(32), None))
    return wanted, notes


def apply(lines, env, wanted):
    """Return (new lines, [(key, 'added'|'filled')]). A value that is already set is never touched."""
    lines = list(lines)
    actions = []
    for key, value, comment in wanted:
        value = value() if callable(value) else value
        if key in env and env[key][1].strip() == '':
            lines[env[key][0]] = f'{key}={value}'
            actions.append((key, 'filled'))
        else:
            if lines and lines[-1].strip():
                lines.append('')
            if comment:
                lines.extend(comment.rstrip('\n').split('\n'))
            lines.append(f'{key}={value}')
            actions.append((key, 'added'))
    return lines, actions


def write_private(path, lines):
    """Atomic write with mode 600 (the file holds secrets)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.env-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write('\n'.join(lines).rstrip('\n') + '\n')
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)


def run(argv=None):
    ap = argparse.ArgumentParser(description='Add the missing non-secret or generated keys to Michael/.env (never overwrites or prints a value).')
    ap.add_argument('--env-file', help='the private env file (default: $MICHAEL_ENV_FILE, else Michael/.env)')
    ap.add_argument('--mail-src', help='path of the mail-service repository clone (default: found next to the other projects)')
    ap.add_argument('--employee-src', help='path of the employee-directory repository clone')
    ap.add_argument('--check', action='store_true', help='change nothing; exit 1 when something would be added')
    args = ap.parse_args(argv)

    path = env_path(args.env_file)
    if not path.exists():
        if args.check:
            print(f'[FAIL] {path} does not exist (copy .env.example to it and supply the private values)')
            print('RESULT: FAIL')
            return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('')
        os.chmod(path, 0o600)
        print(f'[NOTE] created an empty {path.name}; supply the private values (see .env.example)')
    lines = path.read_text().splitlines()
    env = parse(lines)
    wanted, notes = plan(env, args.mail_src, args.employee_src)
    for note in notes:
        print(f'[NOTE] {note}')
    if not wanted:
        print(f'[PASS] {path.name} already has every key init-env manages')
        print('RESULT: PASS')
        return 0
    if args.check:
        for key, *_ in wanted:
            print(f'[FAIL] {key} is missing (run without --check)')
        print('RESULT: FAIL')
        return 1
    lines, actions = apply(lines, env, wanted)
    write_private(path, lines)
    for key, what in actions:
        print(f'[PASS] {key} {what} in {path.name}')
    print(f'[PASS] {path.name} written with mode 600; nothing else was changed')
    print('RESULT: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(run())
