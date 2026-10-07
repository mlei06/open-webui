"""One export tool for every visualization: pages are stored under an opaque id and screenshotted in the terminal.

  uv run --no-project --with fastapi --with pydantic --with pillow --with httpx --with aiohttp --with pyyaml --with lxml \\
     --with python-pptx python Michael/tests/test_visual_export.py
"""
import asyncio
import inspect
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bootstrap'))
sys.path.insert(0, str(ROOT / 'tools'))
import office_tools  # noqa: E402
import workspace_delivery as wd  # noqa: E402

try:
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    fastapi = None

VISUAL_ID = 'a' * 32


def load():
    module = types.ModuleType('visuals_export_test')
    module.__file__ = str(ROOT / 'tools' / 'visuals_toolkit_v4.py')
    sys.modules[module.__name__] = module
    exec(compile(office_tools.bundle_delivery_source((ROOT / 'tools' / 'visuals_toolkit_v4.py').read_text()), module.__file__, 'exec'), module.__dict__)
    return module


def fake_open_webui(uploads, owner=object()):
    """Stand-ins for the Open WebUI modules the tool imports when it stores or reads a page."""
    async def upload_file_handler(request, file, metadata, process, user):
        uploads.append((file.filename, file.file.read(), file.content_type))
        return {'id': 'file-' + str(len(uploads))}
    modules = {
        'open_webui': types.ModuleType('open_webui'),
        'open_webui.models': types.ModuleType('open_webui.models'),
        'open_webui.models.users': types.SimpleNamespace(Users=types.SimpleNamespace(get_user_by_id=AsyncMock(return_value=owner))),
        'open_webui.routers': types.ModuleType('open_webui.routers'),
        'open_webui.routers.files': types.SimpleNamespace(upload_file_handler=upload_file_handler),
    }
    return patch.dict(sys.modules, modules)


@unittest.skipIf(fastapi is None, 'fastapi is not installed')
class StoredVisuals(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mod = load()
        self.tool = self.mod.Tools()

    def test_every_render_tool_can_be_stored_but_keeps_its_visible_parameters(self):
        renderers = [n for n in dir(self.mod.Tools) if n.startswith('render_') and n != 'render_visualization']
        self.assertGreaterEqual(len(renderers), 17)
        for name in renderers:
            signature = inspect.signature(getattr(self.mod.Tools, name))
            self.assertIn('__request__', signature.parameters, name)
            self.assertIn('__user__', signature.parameters, name)
            self.assertTrue(inspect.iscoroutinefunction(getattr(self.mod.Tools, name)), name)
            self.assertTrue(getattr(self.mod.Tools, name).__doc__, name)
        chart = inspect.signature(self.mod.Tools.render_chart).parameters
        self.assertEqual([n for n in chart if not n.startswith('__') and n != 'self'], ['x', 'y', 'title', 'chart_type', 'mode', 'x_label', 'y_label'])

    async def test_an_embedded_visualization_is_stored_and_returns_an_opaque_id(self):
        uploads = []
        with fake_open_webui(uploads):
            shown, context = await self.tool.render_chart(['A', 'B'], [1, 2], title='Chart', chart_type='bar',
                                                           __request__=object(), __user__={'id': 'u1'})
        self.assertIn(b'Plotly', shown.body)
        self.assertEqual(context['visual_id'], 'file-1')
        self.assertEqual(context['tool'], 'render_chart')
        self.assertIn('export_visual', context['instructions'])
        name, content, kind = uploads[0]
        self.assertRegex(name, r'^visual-[0-9a-f]{32}\.html$')
        self.assertEqual(content, shown.body)
        self.assertEqual(kind, 'text/html')

    async def test_every_html_tool_family_is_stored(self):
        uploads = []
        who = {'__request__': object(), '__user__': {'id': 'u1'}}
        with fake_open_webui(uploads):
            for call in (self.tool.render_table([{'a': 1}], title='T', **who),
                         self.tool.render_pie_chart(['a', 'b'], [1, 2], title='P', **who),
                         self.tool.render_metrics_grid({'Users': '1'}, title='K', **who),
                         self.tool.render_flowchart(['Start', 'End'], title='F', **who)):
                shown, context = await call
                self.assertTrue(context['visual_id'])
        self.assertEqual(len(uploads), 4)

    async def test_text_mode_and_missing_identity_return_what_they_always_did(self):
        uploads = []
        with fake_open_webui(uploads):
            text = await self.tool.render_chart(['A'], [1], mode='text', __request__=object(), __user__={'id': 'u1'})
            plain = await self.tool.render_chart(['A'], [1], chart_type='bar')
            anonymous = await self.tool.render_chart(['A'], [1], chart_type='bar', __request__=object(), __user__=None)
        self.assertIsInstance(text, str)
        self.assertTrue(hasattr(plain, 'body') and hasattr(anonymous, 'body'))
        self.assertEqual(uploads, [])

    async def test_a_failed_store_still_shows_the_visualization(self):
        async def boom(**_):
            raise RuntimeError('storage down')
        with fake_open_webui([]):
            sys.modules['open_webui.routers.files'].upload_file_handler = boom
            shown = await self.tool.render_chart(['A'], [1], chart_type='bar', __request__=object(), __user__={'id': 'u1'})
        self.assertTrue(hasattr(shown, 'body'))

    async def test_a_flowchart_is_a_card_when_embedded_and_markdown_in_text_mode(self):
        embedded = await self.tool.render_flowchart(['Start', 'End'])
        self.assertIn(b'<pre class="vis">', embedded.body)
        text = await self.tool.render_flowchart(['Start', 'End'], mode='text')
        self.assertTrue(text.startswith('### Flowchart'))


class LightTheme(unittest.TestCase):
    def test_the_dark_card_becomes_white_and_nothing_is_replaced_twice(self):
        mod = load()
        page = ('--bg:#0b0f14;--panel:#0B0F14;--header:#111827;--text:#e5e7eb;--muted:#94a3b8;'
                '--border:#374151;--outer:#1f2937;"paper_bgcolor": "#0b0f14", "font": {"color": "#e5e7eb"}')
        light = mod._light_page(page)
        self.assertIn('--bg:#ffffff;--panel:#ffffff;--header:#f3f4f6;--text:#111827;--muted:#4b5563;', light)
        self.assertIn('"paper_bgcolor": "#ffffff", "font": {"color": "#111827"}', light)
        for dark in ('#0b0f14', '#94a3b8', '#374151', '#1f2937'):  # the others are also light-theme colours
            self.assertNotIn(dark, light.lower())


@unittest.skipIf(fastapi is None, 'fastapi is not installed')
class ExportVisual(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mod = load()
        self.mod.Tools._register_image_real = self.mod.Tools._register_image
        self.tool = self.mod.Tools()
        self.page = b'<html><head><title>Cases by product</title></head><body style="background:#0b0f14"></body></html>'
        self.record = types.SimpleNamespace(user_id='u1', filename='visual-' + VISUAL_ID + '.html', path='stored/path')
        self.render_context = AsyncMock(return_value=(('http://t', {}, {}), True))
        self.discard = AsyncMock()
        self.shot = AsyncMock(return_value={'workspace_path': '~/workspace/output/cases-by-product.png', 'size': 5, 'sha256': '',
                                           'width': 1280, 'height': 450, 'warnings': []})
        import hashlib
        self.shot.return_value['sha256'] = hashlib.sha256(b'12345').hexdigest()
        self.patches = [
            patch.dict(sys.modules, {
                'open_webui': types.ModuleType('open_webui'), 'open_webui.models': types.ModuleType('open_webui.models'),
                'open_webui.models.files': types.SimpleNamespace(Files=types.SimpleNamespace(get_file_by_id=AsyncMock(return_value=self.record))),
                'open_webui.storage': types.ModuleType('open_webui.storage'),
                'open_webui.storage.provider': types.SimpleNamespace(Storage=types.SimpleNamespace(get_file=lambda path: str(self.file))),
            }),
            patch.object(self.mod, '_render_context', new=self.render_context),
            patch.object(self.mod, '_terminal_discard', new=self.discard),
            patch.object(self.mod, '_terminal_screenshot', new=self.shot),
            patch.object(self.mod, '_terminal_image', new=AsyncMock(return_value=b'12345')),
            patch.object(self.mod.Tools, '_register_image', new=self.register),
        ]
        import tempfile
        self.dir = tempfile.TemporaryDirectory(); self.addCleanup(self.dir.cleanup)
        self.file = Path(self.dir.name) / 'page.html'; self.file.write_bytes(self.page)
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    @staticmethod
    async def register(self, raw, name, image_format, result, request, user, emitter):
        result.update(status='success', file_id='img-1', download_url='/api/v1/files/img-1/content')

    async def export(self, **kwargs):
        return await self.tool.export_visual(kwargs.pop('visual_id', VISUAL_ID), __request__=object(),
                                             __user__={'id': 'u1'}, __metadata__={'terminal_id': 'open-terminal'}, **kwargs)

    async def test_success_returns_both_links_and_names_the_image_after_the_title(self):
        result = await self.export()
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['workspace_path'], '~/workspace/output/cases-by-product.png')
        self.assertEqual(result['download_url'], '/api/v1/files/img-1/content')
        self.assertEqual(result['terminal_download_url'], '/api/v1/terminals/open-terminal/files/view?path=workspace%2Foutput%2Fcases-by-product.png')
        self.assertEqual(self.shot.await_args.kwargs['name'], 'cases-by-product')
        self.assertEqual((self.shot.await_args.kwargs['width'], self.shot.await_args.kwargs['format']), (1280, 'png'))
        self.assertIn('#0b0f14', self.shot.await_args.args[1])

    async def test_light_theme_and_options_reach_the_renderer(self):
        await self.export(theme='light', width=1600, format='jpeg', save_to='projects/report')
        page = self.shot.await_args.args[1]
        self.assertNotIn('#0b0f14', page)
        self.assertEqual((self.shot.await_args.kwargs['width'], self.shot.await_args.kwargs['format'], self.shot.await_args.kwargs['save_to']),
                         (1600, 'jpeg', 'projects/report'))

    async def test_only_the_owner_can_export_and_only_a_stored_visual(self):
        self.record.user_id = 'someone-else'
        result = await self.export()
        self.assertEqual(result['status'], 'error'); self.assertIn('No visualization', result['error'])
        self.record.user_id = 'u1'; self.record.filename = 'report.pdf'
        self.assertEqual((await self.export())['status'], 'error')
        self.shot.assert_not_awaited()

    async def test_bad_options_fail_before_any_work(self):
        for options in ({'theme': 'neon'}, {'width': 100}, {'width': True}, {'format': 'gif'}, {'save_to': 5}):
            result = await self.export(**options)
            self.assertEqual(result['status'], 'error', options)
        self.shot.assert_not_awaited()

    async def no_terminal(self, **kwargs):
        self.render_context.return_value = (('http://service', {}, {}), False)
        return await self.tool.export_visual(VISUAL_ID, __request__=object(), __user__={'id': 'u1'}, __metadata__={}, **kwargs)

    async def test_without_a_terminal_the_image_is_rendered_by_the_service_and_delivered_as_a_download(self):
        result = await self.no_terminal(theme='light')
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['download_url'], '/api/v1/files/img-1/content')
        self.assertIsNone(result['workspace_path']); self.assertIsNone(result['terminal_download_url']); self.assertFalse(result['terminal_saved'])
        self.assertEqual(self.shot.await_args.kwargs['folder'], self.mod.RENDER_FOLDER)
        self.discard.assert_awaited_once()                       # the working copy is removed again
        self.assertIn('image_file_id', result['instructions'])
        self.assertNotIn('#0b0f14', self.shot.await_args.args[1])  # the light theme works the same way

    async def test_with_a_terminal_the_image_goes_to_the_users_own_folder_and_nothing_is_discarded(self):
        await self.export()
        self.assertIsNone(self.shot.await_args.kwargs['folder'])
        self.discard.assert_not_awaited()

    async def test_save_to_without_a_terminal_fails_before_any_rendering(self):
        result = await self.no_terminal(save_to='projects/report')
        self.assertEqual(result['status'], 'error'); self.assertIn('Open Terminal', result['error'])
        self.shot.assert_not_awaited()

    async def test_a_failed_registration_without_a_terminal_copy_is_an_error_not_a_partial_success(self):
        real = type(self.tool)._register_image_real
        uploads = []
        with fake_open_webui(uploads):
            async def boom(**_):
                raise RuntimeError('storage down')
            sys.modules['open_webui.routers.files'].upload_file_handler = boom
            with patch.object(self.mod.Tools, '_register_image', new=real):
                result = await self.no_terminal()
        self.assertEqual(result['status'], 'error'); self.assertIn('could not be registered', result['error'])

    async def test_a_failed_terminal_registration_keeps_the_verified_terminal_copy(self):
        real = type(self.tool)._register_image_real
        with fake_open_webui([]):
            async def boom(**_):
                raise RuntimeError('storage down')
            sys.modules['open_webui.routers.files'].upload_file_handler = boom
            with patch.object(self.mod.Tools, '_register_image', new=real):
                result = await self.export()
        self.assertEqual(result['status'], 'partial_success'); self.assertTrue(result['terminal_saved'])

    async def test_a_failed_registration_keeps_the_verified_terminal_copy(self):
        async def partial(self, raw, name, image_format, result, request, user, emitter):
            result.update(status='partial_success'); result['warnings'].append('no link')
        with patch.object(self.mod.Tools, '_register_image', new=partial):
            result = await self.export()
        self.assertEqual(result['status'], 'partial_success')
        self.assertTrue(result['terminal_saved']); self.assertIsNone(result['download_url'])

    async def test_a_terminal_failure_is_reported_without_a_path(self):
        self.shot.side_effect = ValueError('The visualization did not finish rendering')
        result = await self.export()
        self.assertEqual(result['status'], 'error'); self.assertIsNone(result['workspace_path'])


class TerminalProgram(unittest.TestCase):
    def test_the_screenshot_program_is_valid_python_with_one_payload_slot(self):
        program = wd._TERMINAL_SCREENSHOT_PROGRAM
        compile(program.replace('PAYLOAD', repr('e30=')), 'program', 'exec')
        self.assertEqual(len(re.findall('PAYLOAD', program)), 1)

    def test_it_blocks_the_network_and_never_follows_links_when_writing(self):
        program = wd._TERMINAL_SCREENSHOT_PROGRAM
        self.assertIn('--host-resolver-rules=MAP * ~NOTFOUND', program)
        self.assertIn("default-src 'none'", program)
        self.assertIn('O_NOFOLLOW', program)
        self.assertIn('.michael-render', program)

    def test_both_image_programs_share_one_writer(self):
        self.assertIn(wd._TERMINAL_WRITE_IMAGE, wd._TERMINAL_PLOTLY_PROGRAM)
        self.assertIn(wd._TERMINAL_WRITE_IMAGE, wd._TERMINAL_SCREENSHOT_PROGRAM)

    def test_the_page_is_uploaded_to_a_scratch_folder_and_only_its_name_is_sent(self):
        saved = AsyncMock(return_value='~/.michael-render/x.html')
        run = AsyncMock(return_value={'workspace_path': '~/workspace/output/v.png'})
        with patch.object(wd, '_terminal_save', new=saved), patch.object(wd, '_terminal_execute_json', new=run):
            asyncio.run(wd._terminal_screenshot(('http://t', {}, {}), '<html>' + 'x' * 500_000 + '</html>', name='v'))
        self.assertEqual(saved.await_args.kwargs['save_to'], '~/.michael-render')
        payload = run.await_args.args[2]
        self.assertTrue(payload['html'].startswith('.michael-render/') and payload['html'].endswith('.html'))
        self.assertLess(len(str(payload)), 1000)


if __name__ == '__main__':
    unittest.main()
