"""Unit tests for bootstrap/init_env.py, smtp_check.py and the pure parts of provision.py (standard library only).

  python3 Michael/tests/test_provision.py
"""

import contextlib
import io
import json
import os
import smtplib
import ssl
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / 'bootstrap'))
import init_env  # noqa: E402
import provision  # noqa: E402
import smtp_check  # noqa: E402

SECRET = 'S3cret-Value-Do-Not-Print'


def fake_checkout(root, name, package):
    d = Path(root) / name
    (d / 'src' / package).mkdir(parents=True)
    (d / 'Dockerfile').write_text('FROM scratch\n')
    if name == 'mail-service':
        (d / 'src' / package / 'config.py').write_text('smtp_tls_verify: bool = True\n')
    return d


class InitEnvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = Path(self.tmp.name) / '.env'
        self.mail = fake_checkout(self.tmp.name, 'mail-service', 'mail_service')
        self.emp = fake_checkout(self.tmp.name, 'employee-directory', 'employee_directory')

    def run_init(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = init_env.run(['--env-file', str(self.env), '--mail-src', str(self.mail), '--employee-src', str(self.emp), *extra])
        return code, out.getvalue()

    def values(self):
        return {k: v[1] for k, v in init_env.parse(self.env.read_text().splitlines()).items()}

    def test_adds_only_missing_keys_and_never_prints_a_value(self):
        self.env.write_text(f'SMTP_HOST=relay.example\nSMTP_PASSWORD={SECRET}\nOPENAI_API_KEYS=abc\n')
        code, out = self.run_init()
        v = self.values()
        self.assertEqual(code, 0)
        self.assertEqual((v['MAIL_PROVIDER'], v['MAIL_SMTP_TLS_VERIFY'], v['MAIL_SERVICE_SRC']), ('smtp', 'false', str(self.mail)))
        self.assertEqual(v['EMPLOYEE_DIRECTORY_SRC'], str(self.emp))
        self.assertGreaterEqual(len(v['MAIL_MCP_API_KEY']), 32)
        self.assertEqual((v['SMTP_HOST'], v['SMTP_PASSWORD'], v['OPENAI_API_KEYS']), ('relay.example', SECRET, 'abc'))  # untouched
        for secret in (SECRET, v['MAIL_MCP_API_KEY'], str(self.mail), 'relay.example'):
            self.assertNotIn(secret, out)
        self.assertEqual(stat.S_IMODE(os.stat(self.env).st_mode), 0o600)
        self.assertIn('RISK', self.env.read_text())  # the unverified-certificate risk is written next to the line

    def test_mock_without_smtp_host_and_no_tls_line(self):
        self.env.write_text('OPENAI_API_KEYS=abc\n')
        self.run_init()
        v = self.values()
        self.assertEqual(v['MAIL_PROVIDER'], 'mock')
        self.assertNotIn('MAIL_SMTP_TLS_VERIFY', v)

    def test_never_overwrites_and_is_idempotent(self):
        self.env.write_text(f'SMTP_HOST=h\nMAIL_PROVIDER=mock\nWEBUI_SECRET_KEY={SECRET}\nMAIL_MCP_API_KEY={SECRET}\nMAIL_SERVICE_SRC=/elsewhere\nMAIL_SMTP_TLS_VERIFY=true\nEMPLOYEE_DIRECTORY_SRC=/e\nQDTS_CASES_SRC=/c\nQDTS_MCP_API_KEY={SECRET}\nQDTS_CUSTOMER_NAMES=plain\n')
        before = self.env.read_text()
        code, out = self.run_init()
        self.assertEqual(code, 0)
        self.assertEqual(self.env.read_text(), before)  # nothing at all changed
        self.assertIn('already set; it is never overwritten', out)  # --mail-src differs from the kept value
        self.env.write_text('SMTP_HOST=h\n')
        self.run_init()
        first = self.env.read_text()
        self.run_init()
        self.assertEqual(self.env.read_text(), first)

    def test_an_empty_value_is_filled_in_place(self):
        self.env.write_text('MAIL_MCP_API_KEY=\nSMTP_HOST=h\n')
        self.run_init()
        text = self.env.read_text()
        self.assertEqual(text.count('MAIL_MCP_API_KEY='), 1)
        self.assertGreaterEqual(len(self.values()['MAIL_MCP_API_KEY']), 32)

    def test_adds_a_long_random_secret_key_privately(self):
        self.env.write_text('SMTP_HOST=h\n')
        code, out = self.run_init()
        key = self.values()['WEBUI_SECRET_KEY']
        self.assertEqual(code, 0)
        self.assertGreaterEqual(len(key), 64)  # 48 random bytes, urlsafe base64
        self.assertRegex(key, r'^[A-Za-z0-9_-]+$')
        self.assertNotIn(key, out)
        self.assertIn('WEBUI_SECRET_KEY added', out)
        self.assertEqual(stat.S_IMODE(os.stat(self.env).st_mode), 0o600)
        self.run_init()
        self.assertEqual(self.values()['WEBUI_SECRET_KEY'], key)  # a re-run keeps it

    def test_an_empty_secret_key_is_filled_and_a_set_one_is_kept(self):
        self.env.write_text('WEBUI_SECRET_KEY=\nSMTP_HOST=h\n')
        self.run_init()
        self.assertEqual(self.env.read_text().count('WEBUI_SECRET_KEY='), 1)
        self.assertGreaterEqual(len(self.values()['WEBUI_SECRET_KEY']), 64)
        self.env.write_text('WEBUI_SECRET_KEY=mine\nSMTP_HOST=h\n')
        self.run_init()
        self.assertEqual(self.values()['WEBUI_SECRET_KEY'], 'mine')

    def test_check_mode_changes_nothing(self):
        self.env.write_text('SMTP_HOST=h\n')
        code, _ = self.run_init('--check')
        self.assertEqual(code, 1)
        self.assertEqual(self.env.read_text(), 'SMTP_HOST=h\n')

    def test_source_must_look_like_the_repository(self):
        bad = Path(self.tmp.name) / 'nope'
        bad.mkdir()
        path, why = init_env.find_source('mail-service', 'mail_service', str(bad))
        self.assertIsNone(path)
        self.assertIn('not a mail-service checkout', why)

    def test_old_mail_service_without_tls_verify_is_flagged(self):
        (self.mail / 'src' / 'mail_service' / 'config.py').write_text('x = 1\n')
        self.env.write_text('SMTP_HOST=h\n')
        _, out = self.run_init()
        self.assertIn('no MAIL_SMTP_TLS_VERIFY yet', out)


class FakeSmtp:
    """smtplib.SMTP stand-in recording the commands; mail commands are forbidden."""

    last = None

    def __init__(self, host, port, timeout=None, offer_tls=True, login_ok=True, tls_error=None):
        self.calls, self.offer_tls, self.login_ok, self.tls_error = [], offer_tls, login_ok, tls_error
        FakeSmtp.last = self

    def ehlo(self):
        self.calls.append('ehlo')

    def has_extn(self, name):
        return self.offer_tls

    def starttls(self, context=None):
        self.calls.append(('starttls', context.verify_mode, context.check_hostname))
        if self.tls_error:
            raise self.tls_error

    def login(self, user, password):
        self.calls.append('login')
        if not self.login_ok:
            raise smtplib.SMTPAuthenticationError(535, b'bad ' + password.encode())

    def quit(self):
        self.calls.append('quit')

    def __getattr__(self, name):
        if name in ('mail', 'rcpt', 'data', 'sendmail', 'send_message'):
            raise AssertionError(f'the check must never call {name}')
        raise AttributeError(name)


class SmtpCheckTests(unittest.TestCase):
    ENV = {'SMTP_HOST': 'relay.example', 'SMTP_PORT': '587', 'SMTP_USERNAME': 'user', 'SMTP_PASSWORD': SECRET, 'SMTP_USE_TLS': 'true'}

    def run_check(self, env=None, connect=FakeSmtp, resolves=True):
        msgs = []
        with mock.patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('192.0.2.1', 587))] if resolves else mock.DEFAULT, side_effect=None if resolves else OSError('nx')):
            code = smtp_check.check({**self.ENV, **(env or {})}, lambda ok, m: msgs.append((ok, m)), connect=connect)
        return code, msgs

    def test_passes_without_sending_and_verification_off_skips_the_certificate(self):
        code, msgs = self.run_check({'MAIL_SMTP_TLS_VERIFY': 'false'})
        self.assertEqual(code, 0)
        self.assertTrue(all(ok for ok, _ in msgs))
        self.assertEqual(FakeSmtp.last.calls, ['ehlo', ('starttls', ssl.CERT_NONE, False), 'ehlo', 'login', 'quit'])

    def test_verification_is_on_by_default(self):
        self.run_check()
        self.assertEqual(FakeSmtp.last.calls[1], ('starttls', ssl.CERT_REQUIRED, True))

    def test_unverifiable_certificate_is_a_failure_that_names_the_fix(self):
        code, msgs = self.run_check(connect=lambda h, p, timeout=None: FakeSmtp(h, p, tls_error=ssl.SSLCertVerificationError('x')))
        self.assertEqual(code, 1)
        self.assertIn('MAIL_SMTP_TLS_VERIFY=false', msgs[-1][1])

    def test_unresolvable_host_is_an_environment_limit_not_a_failure_of_the_wiring(self):
        code, msgs = self.run_check(resolves=False)
        self.assertEqual(code, 2)
        self.assertIn('company network or VPN', msgs[-1][1])

    def test_refused_connection_is_an_environment_limit(self):
        def refuse(*a, **k):
            raise ConnectionRefusedError()

        self.assertEqual(self.run_check(connect=refuse)[0], 2)

    def test_rejected_login_fails_and_never_echoes_the_password_or_user(self):
        code, msgs = self.run_check(connect=lambda h, p, timeout=None: FakeSmtp(h, p, login_ok=False))
        self.assertEqual(code, 1)
        for _, m in msgs:
            self.assertNotIn(SECRET, m)
            self.assertNotIn('user', m.replace('SMTP_USERNAME', ''))

    def test_no_starttls_offer_fails(self):
        self.assertEqual(self.run_check(connect=lambda h, p, timeout=None: FakeSmtp(h, p, offer_tls=False))[0], 1)

    def test_ca_file_path_maps_to_the_host_certs_folder(self):
        self.assertTrue(smtp_check.host_ca_file('/certs/ca-bundle.pem').endswith('runtime/certs/ca-bundle.pem'))


class FakeDocker:
    """Stands in for subprocess.run: one open-webui container publishing the port, with the given Config.Env."""

    def __init__(self, env, containers='abc123\n'):
        self.env, self.containers, self.calls = env, containers, []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        out = self.containers if cmd[1] == 'ps' else json.dumps(self.env)
        return mock.Mock(returncode=0, stdout=out)


class ProvisionTests(unittest.TestCase):
    def test_container_secret_key_states(self):
        base = 'http://localhost:3999'
        self.assertEqual(provision.container_secret_key(base, FakeDocker(['A=1', f'WEBUI_SECRET_KEY={SECRET}'])), 'set')
        self.assertEqual(provision.container_secret_key(base, FakeDocker(['A=1'])), 'missing')
        self.assertEqual(provision.container_secret_key(base, FakeDocker(['WEBUI_SECRET_KEY='])), 'missing')
        fake = FakeDocker(['A=1'])
        provision.container_secret_key(base, fake)
        self.assertIn('publish=3999', fake.calls[0])

    def test_container_secret_key_unknown_is_not_a_warning(self):
        base = 'http://localhost:3999'
        self.assertIsNone(provision.container_secret_key(base, FakeDocker([], containers='')))
        self.assertIsNone(provision.container_secret_key(base, FakeDocker([], containers='a\nb\n')))
        self.assertIsNone(provision.container_secret_key('http://example.test', FakeDocker([])))  # no port to look up

        def no_docker(cmd, **kwargs):
            raise FileNotFoundError('docker')
        self.assertIsNone(provision.container_secret_key(base, no_docker))

    def test_missing_key_note_explains_the_consequence_and_is_a_note(self):
        run = provision.Run([])
        with contextlib.redirect_stdout(io.StringIO()):
            run.note('secret-key', provision.SECRET_KEY_NOTE)
        self.assertEqual(run.failed, [])
        self.assertIn('signs everyone out', provision.SECRET_KEY_NOTE)
        self.assertIn('saved tool keys', provision.SECRET_KEY_NOTE)


    def test_secret_values_are_masked_in_output(self):
        env = {'SMTP_PASSWORD': SECRET, 'MAIL_MCP_API_KEY': 'k' * 20, 'OPEN_WEBUI_URL': 'http://x', 'XAI_API_KEY': 'abc'}
        run = provision.Run(provision.secret_values(env))
        self.assertEqual(run.mask(f'x {SECRET} y {"k" * 20}'), 'x *** y ***')
        self.assertNotIn('abc', provision.secret_values(env))  # too short to be a useful mask

    def test_steps_follow_dependency_order(self):
        names = [s[0] for s in provision.STEPS]
        order = ['accounts', 'davy', 'xai', 'mcp', 'filter', 'audit', 'translator', 'kbtool', 'office', 'knowledge', 'presets', 'branding']
        self.assertEqual(names, order)  # tools and knowledge exist before the presets that attach them

    def test_path_is_resolved_by_existing_mcp_stage_before_context_and_presets(self):
        import mcp_servers
        import presets
        servers = {s['id']: s for s in mcp_servers.load_servers()}
        conn = mcp_servers.desired_connection(servers['path'], mcp_servers.resolve(servers['path'], {}), {})
        doc, models = presets.load_presets()
        path = next(m for m in models if m['id'] == 'path')
        desired = presets.desired_model(path, doc['base_model'], doc['filter_ids'])
        self.assertEqual(conn['info']['id'], 'path')
        self.assertEqual(desired['meta']['toolIds'], ['server:mcp:path'])
        names = [s[0] for s in provision.STEPS]
        self.assertLess(names.index('mcp'), names.index('filter'))
        self.assertLess(names.index('filter'), names.index('presets'))
        self.assertIn('PATH_MCP_API_KEY', (HERE / '.env.example').read_text())
        self.assertIn('synthetic-path-secret', provision.secret_values({'PATH_MCP_API_KEY': 'synthetic-path-secret'}))

    def test_every_step_script_exists(self):
        for _, _, script, _, _ in provision.STEPS:
            self.assertTrue((provision.BOOTSTRAP / script).is_file(), script)

    def test_davy_is_the_only_soft_step(self):
        self.assertEqual([s[0] for s in provision.STEPS if s[3]], ['davy'])


if __name__ == '__main__':
    unittest.main()
