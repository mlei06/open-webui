"""Tests for terminal/confine/michael_confine.py against the real Open Terminal UserFS.

Needs the pinned upstream package; it is skipped without it:

  uv run --no-project --with open-terminal==0.14.0 python Michael/tests/test_terminal_confine.py

Everything runs on temporary directories and real symlinks. Nothing needs sudo or a second OS user:
writes are exercised with the ownership fix-ups stubbed, since only the path decision is under test.
"""

import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE = Path(__file__).resolve().parent.parent / 'terminal' / 'confine' / 'michael_confine.py'

try:
    from open_terminal.utils import fs as upstream_fs
except ImportError:  # pragma: no cover
    upstream_fs = None


@unittest.skipIf(upstream_fs is None, 'open-terminal is not installed')
class ConfineTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.TemporaryDirectory()
        base = Path(cls.root.name).resolve()
        cls.alice, cls.bob, cls.shared, cls.server = base / 'home' / 'alice', base / 'home' / 'bob', base / 'shared', base / 'home' / 'user'
        for directory in (cls.alice / 'workspace' / 'output', cls.bob / 'workspace', cls.shared, cls.server):
            directory.mkdir(parents=True)
        (cls.alice / 'workspace' / 'output' / 'a.txt').write_text('alice file')
        (cls.bob / 'workspace' / 'secret.txt').write_text('bob secret')
        (cls.shared / 'guide.txt').write_text('shared guide')
        (cls.server / '.bashrc').write_text('server only')
        os.symlink(cls.bob / 'workspace' / 'secret.txt', cls.alice / 'workspace' / 'to_bob')
        os.symlink(cls.bob / 'workspace', cls.alice / 'workspace' / 'bob_dir')
        os.symlink(cls.server / '.bashrc', cls.alice / 'workspace' / 'to_server')
        os.symlink('/etc/passwd', cls.alice / 'workspace' / 'to_etc')
        os.symlink(cls.shared / 'guide.txt', cls.alice / 'workspace' / 'to_shared')
        os.symlink(cls.alice / 'workspace' / 'output' / 'a.txt', cls.alice / 'workspace' / 'own_link')
        cls.env = mock.patch.dict(os.environ, {'MICHAEL_TERMINAL_SHARED_ROOT': str(cls.shared)})
        cls.env.start()
        # Load a private copy so the patch is applied to the real UserFS exactly once for this process.
        spec = importlib.util.spec_from_file_location('michael_confine', MODULE)
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules['michael_confine'] = cls.module
        spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.root.cleanup()

    def fs(self, who='alice'):
        home = getattr(self, who)
        return upstream_fs.UserFS(username=who, home=str(home))

    def allowed(self, path, who='alice'):
        return self.fs(who).is_path_allowed(str(path))

    def test_own_home_and_shared_are_readable(self):
        self.assertTrue(self.allowed(self.alice / 'workspace' / 'output' / 'a.txt'))
        self.assertTrue(self.allowed(self.alice / 'workspace' / 'new-file-not-yet-created.docx'))
        self.assertTrue(self.allowed(self.shared / 'guide.txt'))

    def test_other_users_and_system_paths_are_denied(self):
        for path in (self.bob / 'workspace' / 'secret.txt', self.server / '.bashrc', '/etc/passwd', '/proc/1/environ', '/proc/self/environ', '/'):
            self.assertFalse(self.allowed(path), path)

    def test_traversal_does_not_escape(self):
        self.assertFalse(self.allowed(f'{self.alice}/workspace/../../bob/workspace/secret.txt'))
        self.assertFalse(self.allowed(f'{self.alice}/../user/.bashrc'))

    def test_symlinks_that_leave_the_home_are_denied(self):
        for name in ('to_bob', 'bob_dir', 'to_server', 'to_etc'):
            self.assertFalse(self.allowed(self.alice / 'workspace' / name), name)
        self.assertFalse(self.allowed(self.alice / 'workspace' / 'bob_dir' / 'secret.txt'))

    def test_symlinks_that_stay_inside_are_allowed(self):
        self.assertTrue(self.allowed(self.alice / 'workspace' / 'own_link'))
        self.assertTrue(self.allowed(self.alice / 'workspace' / 'to_shared'))  # shared is readable

    def test_a_sibling_directory_with_the_same_prefix_is_not_the_home(self):
        lookalike = self.alice.parent / 'alice2'
        lookalike.mkdir(exist_ok=True)
        self.assertFalse(self.allowed(lookalike / 'x'))

    def test_single_user_mode_is_unchanged(self):
        self.assertTrue(upstream_fs.UserFS().is_path_allowed('/etc/passwd'))

    async def test_read_returns_own_and_shared_and_refuses_everything_else(self):
        fs = self.fs()
        self.assertEqual(await fs.read(str(self.alice / 'workspace' / 'output' / 'a.txt')), b'alice file')
        self.assertEqual(await fs.read(str(self.shared / 'guide.txt')), b'shared guide')
        self.assertEqual(await fs.read_text(str(self.alice / 'workspace' / 'own_link')), 'alice file')
        for path in (self.bob / 'workspace' / 'secret.txt', self.alice / 'workspace' / 'to_bob', self.alice / 'workspace' / 'to_server', '/etc/passwd'):
            with self.assertRaises(PermissionError, msg=str(path)):
                await fs.read(str(path))

    async def test_read_is_verified_on_the_open_file_even_if_the_check_was_raced(self):
        fs = self.fs()
        with mock.patch.object(upstream_fs.UserFS, '_check_path', lambda self, path: None):
            with self.assertRaises(PermissionError):
                await fs.read(str(self.alice / 'workspace' / 'to_bob'))
            with self.assertRaises(PermissionError):
                await fs.read_text(str(self.alice / 'workspace' / 'to_server'))

    async def test_listing_hides_entries_that_lead_outside(self):
        names = {entry['name'] for entry in await self.fs().listdir(str(self.alice / 'workspace'))}
        self.assertIn('output', names)
        self.assertIn('own_link', names)
        self.assertNotIn('to_bob', names)
        self.assertNotIn('to_etc', names)
        self.assertNotIn('bob_dir', names)

    async def test_writes_are_limited_to_the_home(self):
        fs = self.fs()
        quiet = [mock.patch.object(upstream_fs.UserFS, name, new=mock.AsyncMock()) for name in ('_chown', '_ensure_parents')]
        for patch in quiet:
            patch.start()
        try:
            await fs.write_bytes(str(self.alice / 'workspace' / 'output' / 'ok.bin'), b'x')
            await fs.write(str(self.alice / 'workspace' / 'output' / 'ok.txt'), 'x')
            self.assertEqual((self.alice / 'workspace' / 'output' / 'ok.bin').read_bytes(), b'x')
            for target in (
                self.shared / 'new.txt',  # shared is read-only, even for the file API
                self.shared / 'guide.txt',
                self.bob / 'workspace' / 'new.txt',
                self.alice / 'workspace' / 'to_bob',  # writing through a link into another home
                self.alice / 'workspace' / 'bob_dir' / 'planted.txt',
                '/etc/cron.d/x',
            ):
                with self.assertRaises(PermissionError, msg=str(target)):
                    await fs.write_bytes(str(target), b'x')
                with self.assertRaises(PermissionError, msg=str(target)):
                    await fs.write(str(target), 'x')
            self.assertEqual((self.bob / 'workspace' / 'secret.txt').read_text(), 'bob secret')
            self.assertEqual((self.shared / 'guide.txt').read_text(), 'shared guide')
        finally:
            for patch in quiet:
                patch.stop()

    async def test_remove_and_move_cannot_touch_other_areas(self):
        fs = self.fs()
        with self.assertRaises(PermissionError):
            await fs.remove(str(self.shared / 'guide.txt'))
        with self.assertRaises(PermissionError):
            await fs.remove(str(self.bob / 'workspace' / 'secret.txt'))
        with self.assertRaises(PermissionError):
            await fs.move(str(self.shared / 'guide.txt'), str(self.alice / 'workspace' / 'stolen.txt'))
        with self.assertRaises(PermissionError):
            await fs.move(str(self.alice / 'workspace' / 'output' / 'a.txt'), str(self.bob / 'workspace' / 'a.txt'))
        with self.assertRaises(PermissionError):
            await fs.mkdir(str(self.shared / 'newdir'))
        self.assertTrue((self.shared / 'guide.txt').exists())

    def test_the_write_flag_does_not_leak_between_calls(self):
        async def scenario():
            fs = self.fs()
            with self.assertRaises(PermissionError):
                await fs.write_bytes(str(self.shared / 'x'), b'x')
            return fs.is_path_allowed(str(self.shared / 'guide.txt'))

        self.assertTrue(asyncio.run(scenario()))

    def test_the_module_refuses_to_load_if_upstream_changed(self):
        with mock.patch.object(upstream_fs.UserFS, 'write_bytes', new=lambda self, *a: None):
            spec = importlib.util.spec_from_file_location('michael_confine_changed', MODULE)
            module = importlib.util.module_from_spec(spec)
            with self.assertRaises(RuntimeError):
                spec.loader.exec_module(module)


@unittest.skipIf(upstream_fs is None, 'open-terminal is not installed')
class HardenDownloadsTest(unittest.TestCase):
    """The middleware that stops a clicked link from running a served file as a page."""

    @classmethod
    def setUpClass(cls):
        try:
            from starlette.applications import Starlette
            from starlette.responses import Response
            from starlette.routing import Route
            from starlette.testclient import TestClient
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest('starlette/httpx are not installed')
        spec = importlib.util.spec_from_file_location('michael_confine_serving', MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.confine = module
        types = {'a.html': 'text/html; charset=utf-8', 'a.svg': 'image/svg+xml', 'a.xml': 'application/xml', 'a.js': 'text/javascript',
                 'a.txt': 'text/plain; charset=utf-8', 'a.pdf': 'application/pdf', 'a.png': 'image/png', 'a.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}

        async def view(request):
            name = request.query_params['path'].rsplit('/', 1)[-1]
            known = 'a.' + name.rsplit('.', 1)[-1]
            return Response(b'x', media_type=types[known]) if known in types else Response(b'', status_code=404)

        async def serve(request):
            return Response(b'x', media_type='text/html')

        async def other(request):
            return Response(b'x', media_type='text/html')

        app = Starlette(routes=[Route('/files/view', view), Route('/files/serve/{rest:path}', serve), Route('/files/list', other)])
        app.add_middleware(module.HardenDownloads)
        cls.client = TestClient(app)

    def get(self, path):
        return self.client.get(path)

    def test_a_clicked_view_link_to_an_active_type_downloads_and_is_sandboxed(self):
        for name in ('a.html', 'a.svg', 'a.xml', 'a.js'):
            r = self.get(f'/files/view?path=workspace/output/{name}')
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.headers['content-disposition'].startswith("attachment; filename*=UTF-8''"), name)
            self.assertIn(name, r.headers['content-disposition'])
            self.assertEqual(r.headers['content-security-policy'], 'sandbox allow-scripts')
            self.assertEqual(r.headers['x-content-type-options'], 'nosniff')

    def test_the_serve_alias_stays_renderable_for_the_file_browser_but_is_sandboxed(self):
        r = self.get('/files/serve/workspace/output/page.html')
        self.assertNotIn('content-disposition', r.headers)  # the UI's iframe preview needs to render it
        self.assertEqual(r.headers['content-security-policy'], 'sandbox allow-scripts')  # but it cannot reach the app
        self.assertEqual(r.headers['x-content-type-options'], 'nosniff')

    def test_safe_types_stay_inline_and_documents_are_untouched(self):
        for name in ('a.txt', 'a.pdf', 'a.png', 'a.docx'):
            r = self.get(f'/files/view?path={name}')
            self.assertNotIn('content-disposition', r.headers, name)
            self.assertNotIn('content-security-policy', r.headers, name)
            self.assertEqual(r.headers['x-content-type-options'], 'nosniff')

    def test_the_result_does_not_depend_on_headers_the_proxy_drops(self):
        for dest in ({}, {'Sec-Fetch-Dest': 'iframe'}, {'Sec-Fetch-Dest': 'document'}):
            r = self.client.get('/files/view?path=a.html', headers=dest)
            self.assertIn('attachment', r.headers['content-disposition'], dest)

    def test_filenames_in_the_disposition_are_safe(self):
        r = self.get('/files/view?path=workspace%2Foutput%2F..%2F%22quoted%22.html')
        self.assertNotIn('"', r.headers['content-disposition'].split("UTF-8''")[1])
        r = self.get('/files/view?path=workspace/output/page%20one.html')
        self.assertIn("filename*=UTF-8''page%20one.html", r.headers['content-disposition'])

    def test_other_endpoints_and_errors_are_not_touched(self):
        self.assertNotIn('x-content-type-options', self.get('/files/list').headers)
        missing = self.get('/files/view?path=nope.bin')
        self.assertEqual(missing.status_code, 404)
        self.assertNotIn('content-disposition', missing.headers)
        self.assertNotIn('content-security-policy', missing.headers)

    def test_it_attaches_to_the_server_right_before_it_starts_and_refuses_an_unknown_start(self):
        import uvicorn

        started = []
        with mock.patch.object(uvicorn, 'run', new=lambda *a, **k: started.append(a)):
            spec = importlib.util.spec_from_file_location('michael_confine_hook', MODULE)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertTrue(getattr(uvicorn.run, '_michael_hardened', False))
            with self.assertRaises(RuntimeError):
                uvicorn.run('open_terminal.other:app')
            uvicorn.run('somebody.else:app')  # unrelated callers pass through
            self.assertEqual(started, [('somebody.else:app',)])


if __name__ == '__main__':
    unittest.main()
