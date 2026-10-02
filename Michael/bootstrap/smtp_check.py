#!/usr/bin/env python3
"""Check the connection to the mail relay WITHOUT sending a message.

Reads SMTP_HOST, SMTP_PORT, SMTP_USE_TLS, SMTP_USERNAME, SMTP_PASSWORD and MAIL_SMTP_TLS_VERIFY
from Michael/.env (or $MICHAEL_ENV_FILE) and, in order: resolves the host name, opens the TCP
connection, says EHLO, upgrades with STARTTLS, authenticates, and quits. It never issues MAIL
FROM, RCPT TO or DATA, so no mail is created. Nothing secret is printed: the password and the
user name are never written, and error text from the server is reduced to a fixed phrase.

It runs on THIS machine, not in the mail-service container, so it proves the relay, the
credentials and the certificate handling, not Docker networking. MAIL_SMTP_TLS_VERIFY=false
skips certificate and host name verification here exactly as the mail service does (STARTTLS is
still required); see init_env.py for the risk.

Exit codes: 0 every step passed; 1 the relay answered and something is wrong (rejected
credentials, no STARTTLS, certificate not verified); 2 the relay could not be reached (name does
not resolve, timeout, refused): an environment limit such as being off the VPN, not a wiring
fault. provision.py reports 2 as a NOTE.

Standard library only.
"""

import smtplib
import socket
import ssl
import sys

from davy_connection import MICHAEL_DIR, load_env

TIMEOUT = 15
FALSE = ('0', 'false', 'no', 'off')


def flag(value, default):
    v = (value or '').strip().lower()
    return default if not v else v not in FALSE


def tls_context(verify, ca_file):
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False  # must be cleared before verify_mode
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=ca_file)


def host_ca_file(value):
    """MAIL_SMTP_CA_FILE is an in-container path (/certs/...); map it to Michael/runtime/certs."""
    value = (value or '').strip()
    if not value:
        return None
    if value.startswith('/certs/'):
        return str(MICHAEL_DIR / 'runtime' / 'certs' / value[len('/certs/'):])
    return value


def check(env, report, connect=smtplib.SMTP):
    """Run the steps; report(ok, message). Returns 0, 1 or 2 (see the module docstring)."""
    host = (env.get('SMTP_HOST') or '').strip()
    if not host:
        report(False, 'SMTP_HOST is not set in Michael/.env')
        return 1
    try:
        port = int((env.get('SMTP_PORT') or '587').strip())
    except ValueError:
        report(False, 'SMTP_PORT is not a number')
        return 1
    use_tls = flag(env.get('SMTP_USE_TLS'), True)
    verify = flag(env.get('MAIL_SMTP_TLS_VERIFY'), True)
    username = (env.get('SMTP_USERNAME') or '').strip()
    password = env.get('SMTP_PASSWORD') or ''

    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except OSError:
        report(False, f'cannot resolve {host}: the relay is probably only reachable on the company network or VPN')
        return 2
    report(True, f'{host} resolves ({len(addrs)} address(es))')

    smtp = None
    try:
        try:
            smtp = connect(host, port, timeout=TIMEOUT)
        except (OSError, smtplib.SMTPException):
            report(False, f'cannot connect to {host}:{port} (timeout or refused): off the VPN, or a wrong port')
            return 2
        report(True, f'connected to {host}:{port}')
        smtp.ehlo()
        if use_tls:
            if not smtp.has_extn('starttls'):
                report(False, 'the relay does not offer STARTTLS (the mail service refuses to continue without it)')
                return 1
            try:
                smtp.starttls(context=tls_context(verify, host_ca_file(env.get('MAIL_SMTP_CA_FILE'))))
            except ssl.SSLCertVerificationError:
                report(False, 'STARTTLS failed: the relay certificate could not be verified (set MAIL_SMTP_TLS_VERIFY=false to accept it, or MAIL_SMTP_CA_FILE to trust its authority)')
                return 1
            except (ssl.SSLError, smtplib.SMTPException, OSError):
                report(False, 'STARTTLS failed')
                return 1
            report(True, 'STARTTLS established, certificate ' + ('verified' if verify else 'NOT verified (MAIL_SMTP_TLS_VERIFY=false)'))
            smtp.ehlo()
        else:
            report(True, 'plain connection (SMTP_USE_TLS=false)')
        if username:
            try:
                smtp.login(username, password)
            except smtplib.SMTPAuthenticationError:
                report(False, 'the relay rejected SMTP_USERNAME / SMTP_PASSWORD')
                return 1
            except smtplib.SMTPException:
                report(False, 'authentication failed (the relay offers no login the client can use)')
                return 1
            report(True, 'authenticated')
        else:
            report(True, 'no SMTP_USERNAME set: authentication skipped')
        report(True, 'no message was sent (the check stops before MAIL FROM)')
        return 0
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except (smtplib.SMTPException, OSError):
                pass


def main():
    env = load_env()
    ok = True

    def report(passed, msg):
        nonlocal ok
        ok = ok and passed
        print(f'[{"PASS" if passed else "FAIL"}] {msg}')

    code = check(env, report)
    print('RESULT: ' + ('PASS' if code == 0 else 'FAIL' if code == 1 else 'UNREACHABLE'))
    return code


if __name__ == '__main__':
    sys.exit(main())
