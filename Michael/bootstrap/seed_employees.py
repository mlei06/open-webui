#!/usr/bin/env python3
"""Load employees into the running employee-directory service from a JSON file.

Usage: seed_employees.py PATH_TO_EMPLOYEES.json [--dry-run] [--overwrite-manual]

The file is given at run time and is never committed, copied into the repository or baked
into an image: it is streamed over stdin into a temporary file inside the running container,
the service's own seed command (python -m employee_directory seed) reads it there, and the
temporary file is removed afterwards. The service's seed is idempotent (re-running reports
"unchanged"); records edited by hand are kept unless --overwrite-manual. The file is a list,
or an object with an "employees" list, of entries with id, name and optional aliases.

Needs docker compose and the employee-directory service running (docker-compose.yaml, same
Michael/.env). The compose project name follows COMPOSE_PROJECT_NAME (default michael).
Standard library only. The file's content is never printed by this script; the service
prints counts (and the id of any rejected row).
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from davy_connection import MICHAEL_DIR

SERVICE = 'employee-directory'
REMOTE = '/tmp/employee-seed-input.json'


def compose_cmd():
    env_file = Path(os.environ.get('MICHAEL_ENV_FILE') or MICHAEL_DIR / '.env')
    cmd = ['docker', 'compose']
    if env_file.is_file():
        cmd += ['--env-file', str(env_file)]
    return cmd + ['-f', str(MICHAEL_DIR / 'docker-compose.yaml')]


def exec_in(args, stdin=None):
    return subprocess.run(
        [*compose_cmd(), 'exec', '-T', SERVICE, *args], input=stdin, capture_output=True, check=False
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description='Seed the employee directory from a JSON file.')
    ap.add_argument('path', type=Path, help='employees JSON file (never copied into the repository)')
    ap.add_argument('--dry-run', action='store_true', help='report what would change, write nothing')
    ap.add_argument('--overwrite-manual', action='store_true', help='also overwrite records edited by hand')
    args = ap.parse_args(argv)

    def fail(msg):
        print(f'[FAIL] {msg}')
        print('RESULT: FAIL')
        return 1

    try:
        data = args.path.read_bytes()
        parsed = json.loads(data)
    except OSError:
        return fail(f'cannot read {args.path}')
    except ValueError:
        return fail(f'{args.path} is not valid JSON')
    rows = parsed.get('employees') if isinstance(parsed, dict) else parsed
    if not isinstance(rows, list):
        return fail('the file must be a list, or an object with an "employees" list')
    print(f'[PASS] read {len(rows)} record(s) from the file')

    if exec_in(['true']).returncode != 0:
        return fail(f'the {SERVICE} service is not running (docker compose up -d {SERVICE})')
    try:
        put = exec_in(['sh', '-c', f'umask 077; cat > {REMOTE}'], stdin=data)
        if put.returncode != 0:
            return fail('could not copy the file into the container')
        cmd = ['python', '-m', 'employee_directory', 'seed', '--from', REMOTE]
        if args.dry_run:
            cmd.append('--dry-run')
        if args.overwrite_manual:
            cmd.append('--overwrite-manual')
        res = exec_in(cmd)
        sys.stdout.write(res.stdout.decode(errors='replace'))
        if res.returncode != 0:
            sys.stdout.write(res.stderr.decode(errors='replace')[-1500:])
            return fail(f'the seed command exited with status {res.returncode}')
    finally:
        exec_in(['rm', '-f', REMOTE])
    print('[PASS] ' + ('dry run finished, nothing written' if args.dry_run else 'seed finished'))
    print('RESULT: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
