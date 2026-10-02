#!/usr/bin/env python3
"""Build runtime/certs/ca-bundle.pem: the system CA bundle plus extra CAs.

AIOHTTP_CLIENT_SSL_CERT_FILE makes Open WebUI trust ONLY the file it names, so
a private CA bundle (for example the Davy one) alone cannot verify public
services such as the xAI API. This script concatenates the host's system CA
bundle with every other .pem in runtime/certs/ (except its own output) into
ca-bundle.pem, which compose mounts at /certs/ca-bundle.pem.

Idempotent: the output is rewritten only when its content would change, and
re-running with the same inputs leaves the file byte-identical. Standard
library only. Prints only file names and counts, then PASS or FAIL.
"""

import os
import re
import ssl
import sys
from pathlib import Path

MICHAEL_DIR = Path(__file__).resolve().parent.parent
CERT_DIR = MICHAEL_DIR / 'runtime' / 'certs'
OUTPUT = CERT_DIR / 'ca-bundle.pem'
SYSTEM_CANDIDATES = (
    '/etc/ssl/certs/ca-certificates.crt',
    '/etc/pki/tls/certs/ca-bundle.crt',
    '/etc/ssl/cert.pem',
)
CERT_RE = re.compile(r'-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----', re.S)


def system_bundle():
    paths = ssl.get_default_verify_paths()
    for cand in (os.environ.get('SSL_CERT_FILE'), paths.cafile, paths.openssl_cafile, *SYSTEM_CANDIDATES):
        if cand and Path(cand).is_file():
            return Path(cand)
    return None


def certs_in(path):
    """PEM certificate blocks of a file, normalised to LF and no padding."""
    text = path.read_text(errors='ignore').replace('\r\n', '\n')
    return [m.group(0).strip() for m in CERT_RE.finditer(text)]


def main():
    ok = True

    def report(passed, msg):
        nonlocal ok
        ok = ok and passed
        print(f'[{"PASS" if passed else "FAIL"}] {msg}')

    sysca = system_bundle()
    if sysca is None:
        report(False, 'no system CA bundle found on this host (set SSL_CERT_FILE to one)')
        print('RESULT: FAIL')
        return 1
    certs = certs_in(sysca)
    report(bool(certs), f'system CA bundle {sysca}: {len(certs)} certificate(s)')

    extras = sorted(p for p in CERT_DIR.glob('*.pem') if p.name != OUTPUT.name) if CERT_DIR.is_dir() else []
    if not extras:
        print(f'[INFO] no extra .pem files in Michael/runtime/certs/ ({OUTPUT.name} will hold the system CAs only)')
    for p in extras:
        found = certs_in(p)
        report(bool(found), f'{p.name}: {len(found)} certificate(s)')
        certs += found

    # Drop exact duplicates, keep order.
    unique = list(dict.fromkeys(certs))
    data = ('\n'.join(unique) + '\n').encode()

    if not ok:
        print('RESULT: FAIL')
        return 1
    try:
        CERT_DIR.mkdir(parents=True, exist_ok=True)
        if OUTPUT.is_file() and OUTPUT.read_bytes() == data:
            report(True, f'{OUTPUT.name} already up to date ({len(unique)} certificates)')
        else:
            OUTPUT.write_bytes(data)
            report(True, f'wrote Michael/runtime/certs/{OUTPUT.name} ({len(unique)} certificates)')
        ssl.create_default_context(cafile=str(OUTPUT))
        report(True, f'{OUTPUT.name} loads as a TLS trust store')
    except (OSError, ssl.SSLError) as e:
        report(False, f'cannot write or load {OUTPUT.name}: {type(e).__name__}')

    print('RESULT: ' + ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
