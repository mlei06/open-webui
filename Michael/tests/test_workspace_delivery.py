"""tools/workspace_delivery.py: the one delivery layer every tool uses. A fake Open Terminal over real HTTP.

  uv run --no-project --with aiohttp --with httpx --with pydantic --with fastapi --with pillow python Michael/tests/test_workspace_delivery.py
"""
import asyncio
import base64
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))

try:
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    import workspace_delivery as wd
except ImportError:  # pragma: no cover
    web = None


def stub_open_webui():
    mod = types.ModuleType('open_webui.env')
    mod.AIOHTTP_CLIENT_SESSION_SSL = None
    sys.modules.setdefault('open_webui', types.ModuleType('open_webui'))
    sys.modules['open_webui.env'] = mod


class PathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if web is None:
            raise unittest.SkipTest('aiohttp is not installed')

    def test_home_paths_follow_one_rule(self):
        cases = {
            'output/report.pptx': ('workspace/output/report.pptx', '~/workspace/output/report.pptx'),
            'workspace/output/report.pptx': ('workspace/output/report.pptx', '~/workspace/output/report.pptx'),
            '~/workspace/output/report.pptx': ('workspace/output/report.pptx', '~/workspace/output/report.pptx'),
            '~/Documents/a.pdf': ('Documents/a.pdf', '~/Documents/a.pdf'),
            'workspace': ('workspace', '~/workspace'),
            './a//b/': ('workspace/a/b', '~/workspace/a/b'),
        }
        for given, expected in cases.items():
            self.assertEqual(wd._home_path(given), expected, given)

    def test_absolute_paths_are_for_reading_only(self):
        self.assertEqual(wd._home_path('/shared/templates/form.docx', allow_absolute=True), ('/shared/templates/form.docx', '/shared/templates/form.docx'))
        with self.assertRaises(ValueError):
            wd._home_path('/shared/x')
        with self.assertRaises(ValueError):
            wd._home_path('/shared/../etc/passwd', allow_absolute=True)

    def test_a_dot_means_the_workspace_itself(self):
        self.assertEqual(wd._home_path('.'), ('workspace', '~/workspace'))

    def test_unsafe_paths_are_refused(self):
        for bad in ('', '   ', None, '..', 'a/../../b', '~/../x', 'a\\b', 'x\x00y', 'a' * 2000, '~', '~/'):
            with self.assertRaises(ValueError, msg=repr(bad)[:30]):
                wd._home_path(bad)

    def test_destinations(self):
        self.assertEqual(wd._destination(None, 'Report (v2).docx', '.docx'), ('workspace/output', 'Report (v2).docx'))
        self.assertEqual(wd._destination('projects/q3', 'a.docx', '.docx'), ('workspace/projects/q3', 'a.docx'))  # a folder
        self.assertEqual(wd._destination('~/Documents', 'a.docx', '.docx'), ('Documents', 'a.docx'))
        self.assertEqual(wd._destination('projects/final.docx', 'a.docx', '.docx'), ('workspace/projects', 'final.docx'))  # a file path
        self.assertEqual(wd._destination('~/final.DOCX', 'a.docx', '.docx'), ('', 'final.DOCX'))  # directly in the home
        self.assertEqual(wd._destination('final.docx', 'a.docx', '.docx'), ('workspace', 'final.docx'))
        self.assertEqual(wd._destination('notes.pptx', 'a.docx', '.docx'), ('workspace/notes.pptx', 'a.docx'))  # a different extension is a folder name
        self.assertEqual(wd._destination('out', '../../etc/x.docx', '.docx'), ('workspace/out', 'x.docx'))
        with self.assertRaises(ValueError):
            wd._destination('../x', 'a.docx', '.docx')

    def test_names(self):
        self.assertEqual(wd._safe_name('..hidden'), 'hidden')
        self.assertEqual(wd._safe_name(''), 'file')
        self.assertEqual(wd._safe_name('a:b?.txt'), 'a-b-.txt')
        self.assertEqual(wd._unique('a.txt', set()), 'a.txt')
        self.assertRegex(wd._unique('a.tar.gz', {'a.tar.gz'}), r'^a\.tar-[0-9a-f]{8}\.gz$')
        self.assertRegex(wd._unique('README', {'README'}), r'^README-[0-9a-f]{8}$')

    def test_links(self):
        self.assertEqual(wd._proxy_url('open-terminal', 'workspace/out/a b.pdf'), '/api/v1/terminals/open-terminal/files/view?path=workspace%2Fout%2Fa%20b.pdf')
        self.assertEqual(wd._terminal_download_url('open-terminal', '~/workspace/output/a.pptx'), '/api/v1/terminals/open-terminal/files/view?path=workspace%2Foutput%2Fa.pptx')
        for bad in ('/etc/passwd', '~/../x', '~/', '', None, 'workspace/x'):
            self.assertIsNone(wd._terminal_download_url('open-terminal', bad), bad)
        self.assertIsNone(wd._terminal_download_url(None, '~/workspace/a'))

    def test_the_result_carries_both_links_and_the_size(self):
        ok = json.loads(wd._office_result('a.docx', '/api/v1/files/f/content', 'f', workspace_path='~/workspace/output/a.docx', terminal_requested=True, terminal_id='open-terminal', size=7))
        self.assertEqual(ok['size'], 7)
        self.assertNotIn(ok['terminal_download_url'], ok['message'])
        self.assertEqual(ok['message'], '[a.docx](/api/v1/files/f/content)')
        self.assertNotIn(ok['workspace_path'], ok['message'])
        terminal = json.loads(wd._office_result('a.docx', workspace_path='~/workspace/output/a.docx', terminal_id='open-terminal', warning='Files unavailable'))
        self.assertEqual(terminal['message'], '[a.docx](' + terminal['terminal_download_url'] + ')')
        self.assertEqual(terminal['status'], 'partial_success')
        empty = json.loads(wd._office_result('a.docx', error='Generation failed'))
        self.assertEqual(empty['message'], 'Generation failed')
        self.assertNotIn('](', empty['message'])
        self.assertIn('terminal_download_url', ok['instructions'])
        failed = json.loads(wd._office_result('a.docx', '/api/v1/files/f/content', 'f', terminal_requested=True, warning='copy failed', terminal_id='open-terminal'))
        self.assertIsNone(failed['terminal_download_url'])
        self.assertEqual(failed['status'], 'partial_success')


@unittest.skipIf(web is None, 'aiohttp is not installed')
class TerminalSaveTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        stub_open_webui()
        self.dirs = {'workspace/output': {'taken.docx': b'old'}}
        self.seen = []
        self.mode = 'ok'

        async def listing(request):
            self.seen.append(dict(request.headers))
            directory = request.query['directory'].strip('/')
            if directory not in self.dirs:
                return web.json_response({'detail': 'not found'}, status=404)
            return web.json_response({'entries': [{'name': n} for n in self.dirs[directory]]})

        async def upload(request):
            self.seen.append(dict(request.headers))
            if self.mode == 'forbidden':
                return web.json_response({'detail': 'Access denied'}, status=403)
            directory = request.query['directory'].strip('/')
            part = await (await request.multipart()).next()
            data = bytearray()
            while chunk := await part.read_chunk(1 << 20):
                data.extend(chunk)
            if self.mode == 'short':
                data = data[:-1]
            self.dirs.setdefault(directory, {})[part.filename] = bytes(data)
            saved = f'/home/u1/other/{part.filename}' if self.mode == 'elsewhere' else f'/home/u1/{directory}/{part.filename}'
            return web.json_response({'path': saved, 'size': len(data)})

        async def view(request):
            data = self.dirs.get(request.query['path'].rpartition('/')[0], {}).get(request.query['path'].rpartition('/')[2])
            return web.Response(body=data, content_type='application/pdf') if data is not None else web.json_response({}, status=404)

        app = web.Application(client_max_size=1 << 40)
        app.add_routes([web.get('/files/list', listing), web.post('/files/upload', upload), web.get('/files/view', view)])
        self.server = TestServer(app)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.context = (str(self.server.make_url('')).rstrip('/'), {'X-User-Id': 'u1', 'X-Session-Id': 'chat-1'}, {})

    async def test_the_default_destination_is_the_output_folder_with_the_tools_own_name(self):
        path = await wd._terminal_save(self.context, b'DOCX', 'Quarterly Review (final).docx', extension='.docx')
        self.assertEqual(path, '~/workspace/output/Quarterly Review (final).docx')
        self.assertEqual(self.dirs['workspace/output']['Quarterly Review (final).docx'], b'DOCX')

    async def test_save_to_chooses_a_folder_or_a_full_file_path(self):
        self.assertEqual(await wd._terminal_save(self.context, b'1', 'a.docx', save_to='projects/q3', extension='.docx'), '~/workspace/projects/q3/a.docx')
        self.assertEqual(await wd._terminal_save(self.context, b'2', 'a.docx', save_to='~/Documents', extension='.docx'), '~/Documents/a.docx')
        self.assertEqual(await wd._terminal_save(self.context, b'3', 'a.docx', save_to='~/Documents/board pack.docx', extension='.docx'), '~/Documents/board pack.docx')
        self.assertEqual(self.dirs['Documents']['board pack.docx'], b'3')

    async def test_nothing_is_overwritten(self):
        path = await wd._terminal_save(self.context, b'new', 'taken.docx', extension='.docx')
        self.assertRegex(path, r'^~/workspace/output/taken-[0-9a-f]{8}\.docx$')
        self.assertEqual(self.dirs['workspace/output']['taken.docx'], b'old')
        again = await wd._terminal_save(self.context, b'new', 'taken.docx', extension='.docx')
        self.assertNotEqual(path, again)

    async def test_files_of_any_size_stream_from_bytes_or_from_a_path(self):
        data = os.urandom(20 * 1024 * 1024)
        self.assertEqual(await wd._terminal_save(self.context, data, 'big.bin'), '~/workspace/output/big.bin')
        self.assertEqual(self.dirs['workspace/output']['big.bin'], data)
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(data[:5_000_000]); handle.flush()
            await wd._terminal_save(self.context, Path(handle.name), 'from-disk.bin')
        self.assertEqual(self.dirs['workspace/output']['from-disk.bin'], data[:5_000_000])

    async def test_names_with_spaces_and_non_ascii_are_stored_as_written(self):
        for name in ('Plan Q3 (final).pdf', 'Informe - año.docx', '年度报告.pptx'):
            self.assertEqual(await wd._terminal_save(self.context, b'x', name), '~/workspace/output/' + name)
            self.assertEqual(self.dirs['workspace/output'][name], b'x')

    async def test_a_file_can_go_directly_in_the_home(self):
        self.assertEqual(await wd._terminal_save(self.context, b'x', 'a.docx', save_to='~/final.docx', extension='.docx'), '~/final.docx')
        self.assertEqual(self.dirs['.']['final.docx'], b'x')

    async def test_a_hostile_name_is_reduced_to_a_basename(self):
        path = await wd._terminal_save(self.context, b'x', '../../etc/cron.d/evil')
        self.assertEqual(path, '~/workspace/output/evil')

    async def test_a_bad_destination_fails_before_anything_is_sent(self):
        for bad in ('../x', '/etc', 'a\\b'):
            with self.assertRaises(ValueError, msg=bad):
                await wd._terminal_save(self.context, b'x', 'a.docx', save_to=bad)
        self.assertEqual(self.seen, [])

    async def test_a_size_mismatch_or_unexpected_location_is_never_claimed(self):
        self.mode = 'short'
        with self.assertRaisesRegex(ValueError, 'different size'):
            await wd._terminal_save(self.context, b'abcdef', 'a.txt')
        self.mode = 'elsewhere'
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            await wd._terminal_save(self.context, b'abcdef', 'b.txt')

    async def test_a_refused_upload_says_so_without_leaking_internals(self):
        self.mode = 'forbidden'
        with self.assertRaisesRegex(ValueError, 'not available to you'):
            await wd._terminal_save(self.context, b'x', 'a.txt', save_to='~/other')

    async def test_calls_use_the_users_identity_and_not_the_chat_shell_directory(self):
        await wd._terminal_save(self.context, b'x', 'a.txt')
        self.assertTrue(self.seen)
        for headers in self.seen:
            self.assertEqual(headers.get('X-User-Id'), 'u1')
            self.assertNotIn('X-Session-Id', headers)

    async def test_stat_reports_size_and_type_without_reading_the_body(self):
        self.dirs['workspace/output']['p.pdf'] = b'%PDF-1.4 ' + b'x' * 1000
        self.assertEqual(await wd._terminal_stat(self.context, 'workspace/output/p.pdf'), (1009, 'application/pdf'))
        with self.assertRaisesRegex(ValueError, 'not found'):
            await wd._terminal_stat(self.context, 'workspace/output/nope.pdf')


class TerminalImageErrorTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_missing_image_names_the_path_and_the_fix(self):
        gone = AsyncMock(side_effect=ValueError('Terminal operation failed; inspect the current process before retrying'))
        with patch.object(wd, '_terminal_file_call', gone):
            with self.assertRaises(ValueError) as caught:
                await wd._terminal_image(None, 'xseries_monthly_trend.png')
        text = str(caught.exception)
        self.assertIn("'xseries_monthly_trend.png'", text)
        self.assertIn('workspace_path', text)
        self.assertNotIn('inspect the current process', text)

    async def test_other_failures_keep_their_own_message(self):
        broken = AsyncMock(side_effect=ValueError('Terminal image must be a regular file'))
        with patch.object(wd, '_terminal_file_call', broken):
            with self.assertRaisesRegex(ValueError, 'regular file'):
                await wd._terminal_image(None, 'assets/pipe')


class ReadProgramTest(unittest.TestCase):
    """The terminal-side program that reads caller-owned files for slide images."""

    def run_program(self, home, payload):
        code = wd._TERMINAL_FILE_PROGRAM.replace('PAYLOAD', repr(base64.b64encode(json.dumps(payload).encode()).decode()))
        code = code.replace("os.path.expanduser('~')", repr(str(home)))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(compile(code, '<terminal fixture>', 'exec'), {})
        return json.loads(out.getvalue())

    def test_workspace_and_home_relative_reads_and_refusals(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / 'workspace/assets').mkdir(parents=True)
            (home / 'Documents').mkdir()
            (home / 'workspace/assets/image.png').write_bytes(b'x' * 110_000)
            (home / 'Documents/a.png').write_bytes(b'doc')
            self.assertEqual(self.run_program(home, {'op': 'stat', 'path': 'assets/image.png'})['size'], 110_000)
            self.assertEqual(self.run_program(home, {'op': 'stat', 'path': 'Documents/a.png', 'home': True})['size'], 3)
            (home / 'workspace/assets/link.png').symlink_to(home / 'workspace/assets/image.png')
            with self.assertRaises(OSError):
                self.run_program(home, {'op': 'stat', 'path': 'assets/link.png'})
            os.mkfifo(home / 'workspace/assets/pipe')
            with self.assertRaisesRegex(ValueError, 'regular'):
                self.run_program(home, {'op': 'stat', 'path': 'assets/pipe'})
            for bad in ('/etc/passwd', '../x', 'a/../../b'):
                with self.assertRaises(ValueError):
                    self.run_program(home, {'op': 'stat', 'path': bad})

    def test_the_program_no_longer_writes(self):
        self.assertNotIn("'create'", wd._TERMINAL_FILE_PROGRAM)
        self.assertNotIn('os.link', wd._TERMINAL_FILE_PROGRAM)


class PlotlyProgramTest(unittest.TestCase):
    """The chart renderer's terminal program, with plotly and PIL replaced by stand-ins."""

    def run_program(self, home, **overrides):
        payload = {'figure': {'layout': {'font': {}, 'title': {'font': {}}}}, 'theme': {'font': 'Arial'}, 'width': 10, 'height': 10,
                   'format': 'png', 'filename': 'chart.png', 'dirs': ['workspace', 'output'], **overrides}

        class Figure:
            def __init__(self, figure): self.figure = figure
            def to_dict(self): return self.figure
            def to_image(self, **kw): return b'\x89PNG\r\n\x1a\n' + b'0' * 100

        class Image:
            @staticmethod
            @contextlib.contextmanager
            def open(stream):
                yield types.SimpleNamespace(verify=lambda: None)

        modules = {
            'plotly': types.ModuleType('plotly'), 'plotly.graph_objects': types.SimpleNamespace(Figure=Figure),
            'plotly.io': types.SimpleNamespace(defaults=types.SimpleNamespace(), templates=types.SimpleNamespace()),
            'PIL': types.SimpleNamespace(Image=Image),
        }
        sys.modules.update(modules)
        sys.modules['plotly'].graph_objects = modules['plotly.graph_objects']
        sys.modules['plotly'].io = modules['plotly.io']
        import subprocess
        real = subprocess.check_output
        subprocess.check_output = lambda *a, **k: 'Arial'
        try:
            code = wd._TERMINAL_PLOTLY_PROGRAM.replace('PAYLOAD', repr(base64.b64encode(json.dumps(payload).encode()).decode()))
            code = code.replace("os.path.expanduser('~')", repr(str(home)))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                exec(compile(code, '<plotly fixture>', 'exec'), {})
        finally:
            subprocess.check_output = real
            for name in modules:
                sys.modules.pop(name, None)
        return json.loads(out.getvalue())

    def test_default_folder_modes_and_never_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            os.umask(0o077)
            first = self.run_program(home)
            self.assertEqual(first['workspace_path'], '~/workspace/output/chart.png')
            self.assertEqual(stat.S_IMODE((home / 'workspace/output/chart.png').stat().st_mode), 0o640)
            for folder in ('workspace', 'workspace/output'):
                self.assertEqual(stat.S_IMODE((home / folder).stat().st_mode), 0o2770, folder)
            second = self.run_program(home)
            self.assertRegex(second['workspace_path'], r'^~/workspace/output/chart-[0-9a-f]{8}\.png$')
            self.assertEqual(sorted(p.name for p in (home / 'workspace/output').iterdir() if p.name.endswith('.part')), [])

    def test_a_chosen_folder_anywhere_in_the_home(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            result = self.run_program(home, dirs=['Documents', 'reports', 'q3'], filename='revenue.png')
            self.assertEqual(result['workspace_path'], '~/Documents/reports/q3/revenue.png')
            self.assertTrue((home / 'Documents/reports/q3/revenue.png').exists())

    def test_a_chart_can_go_directly_in_the_home(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_program(Path(directory), dirs=[], filename='top.png')
            self.assertEqual(result['workspace_path'], '~/top.png')
            self.assertTrue((Path(directory) / 'top.png').exists())

    def test_unsafe_names_and_folders_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            for overrides in ({'filename': '../x.png'}, {'filename': 'x.exe'}, {'dirs': ['..', 'x']}, {'dirs': ['a/b']}, {'dirs': ['']}):
                with self.assertRaises(ValueError, msg=str(overrides)):
                    self.run_program(Path(directory), **overrides)
            self.assertEqual(list(Path(directory).iterdir()), [])


class ChartPayloadTest(unittest.IsolatedAsyncioTestCase):
    """What the chart renderer asks the terminal to do for each save_to."""

    async def payload(self, **kwargs):
        sent = {}

        async def fake(context, program, payload, **options):
            sent.update(payload)
            return {}

        original = wd._terminal_execute_json
        wd._terminal_execute_json = fake
        try:
            await wd._terminal_plotly(None, {}, {}, 10, 10, **kwargs)
        finally:
            wd._terminal_execute_json = original
        return sent

    async def test_default_destination_and_readable_name(self):
        sent = await self.payload(name='quarterly-revenue')
        self.assertEqual((sent['dirs'], sent['filename']), (['workspace', 'output'], 'quarterly-revenue.png'))
        sent = await self.payload(format='jpeg', name='q')
        self.assertEqual(sent['filename'], 'q.jpg')

    async def test_save_to_folder_file_and_home(self):
        self.assertEqual((await self.payload(save_to='projects/q3'))['dirs'], ['workspace', 'projects', 'q3'])
        sent = await self.payload(save_to='~/Documents/rev.png')
        self.assertEqual((sent['dirs'], sent['filename']), (['Documents'], 'rev.png'))
        sent = await self.payload(save_to='~/top.png')
        self.assertEqual((sent['dirs'], sent['filename']), ([], 'top.png'))

    async def test_a_bad_save_to_is_refused(self):
        for bad in ('../x', '/etc', 'a\\b'):
            with self.assertRaises(ValueError, msg=bad):
                await self.payload(save_to=bad)



class RenderServiceTests(unittest.IsolatedAsyncioTestCase):
    """Rendering needs a terminal, not the user's: with none selected a fixed service identity is used."""

    def stubs(self, connections):
        calls = []

        class User:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        async def info(request, user, metadata, extra=None):
            calls.append((user, metadata))
            return ('http://terminal', {'X-User-Id': user.id}, {})

        config = types.SimpleNamespace(Config=types.SimpleNamespace(get=AsyncMock(return_value=connections)))
        modules = {'open_webui.models.config': config, 'open_webui.models.users': types.SimpleNamespace(UserModel=User),
                   'open_webui.utils.terminals': types.SimpleNamespace(get_terminal_request_info=info)}
        return patch.dict(sys.modules, modules), calls

    async def test_a_selected_terminal_is_the_callers_own(self):
        with patch.object(wd, '_terminal_context', new=AsyncMock(return_value=('u', {}, {}))):
            context, own = await wd._render_context(object(), {'id': 'u1'}, {'terminal_id': 'open-terminal'})
        self.assertTrue(own); self.assertEqual(context, ('u', {}, {}))

    async def test_without_one_the_first_enabled_registered_terminal_is_used_as_the_service_identity(self):
        patcher, calls = self.stubs([{'id': 'off', 'enabled': False}, {'id': 'open-terminal'}])
        with patcher:
            context, own = await wd._render_context(object(), {'id': 'u1'}, {})
        self.assertFalse(own)
        user, metadata = calls[0]
        self.assertEqual((user.id, user.role, metadata), (wd.RENDER_SERVICE_USER, 'admin', {'terminal_id': 'open-terminal'}))
        self.assertEqual(context[1]['X-User-Id'], wd.RENDER_SERVICE_USER)  # never the caller's id, so no home of theirs is touched

    async def test_no_registered_terminal_or_no_user_is_a_clear_error(self):
        patcher, _ = self.stubs([])
        with patcher:
            with self.assertRaisesRegex(ValueError, 'no Open Terminal is registered'):
                await wd._render_context(object(), {'id': 'u1'}, {})
        with self.assertRaisesRegex(ValueError, 'Sign in'):
            await wd._render_context(object(), None, {})

    async def test_discard_only_touches_the_render_folder(self):
        run = AsyncMock(return_value={'discarded': True})
        with patch.object(wd, '_terminal_execute_json', new=run):
            await wd._terminal_discard(('u', {}, {}), '~/.michael-render/out/a.png')
        self.assertEqual(run.await_args.args[2], {'path': '.michael-render/out/a.png'})
        compile(wd._TERMINAL_DISCARD_PROGRAM.replace('PAYLOAD', repr('e30=')), 'p', 'exec')
        self.assertIn("parts[0]!='.michael-render'", wd._TERMINAL_DISCARD_PROGRAM)
        run.reset_mock()
        with patch.object(wd, '_terminal_execute_json', new=AsyncMock(side_effect=ValueError('x'))):
            await wd._terminal_discard(('u', {}, {}), '~/.michael-render/out/a.png')  # best effort: never raises

    async def test_the_folder_override_reaches_the_programs(self):
        run = AsyncMock(return_value={})
        saved = AsyncMock(return_value='~/.michael-render/page.html')
        with patch.object(wd, '_terminal_execute_json', new=run), patch.object(wd, '_terminal_save', new=saved):
            await wd._terminal_plotly(('u', {}, {}), {}, {}, 800, 400, 'png', name='x', folder='.michael-render/out')
            await wd._terminal_screenshot(('u', {}, {}), '<html></html>', name='x', folder='.michael-render/out')
        self.assertEqual([c.args[2]['dirs'] for c in run.await_args_list], [['.michael-render', 'out']] * 2)

if __name__ == '__main__':
    unittest.main()