"""Standalone office-tool distribution and authenticated/terminal delivery regressions."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bootstrap'))
sys.path.insert(0, str(ROOT / 'tools'))
import office_tools


def load_tool(tool):
    # Execute exactly what bootstrap publishes, without the source helper on sys.path.
    module = type(sys)(tool['id'] + '_delivery_test')
    module.__file__ = str(tool['file'])
    sys.modules[module.__name__] = module
    exec(compile(office_tools.tool_source(tool), module.__file__, 'exec'), module.__dict__)
    return module


class DeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = [load_tool(t) for t in office_tools.TOOLS]

    def invoke(self, module, tool, *, selected=False, terminal_output=None, emitter=None, save_to_param=None, **extra):
        spec = {'title': 'Synthetic delivery', **extra}
        if terminal_output is not None:
            spec['terminal_output'] = terminal_output
        if hasattr(tool, 'generate_slides'):
            spec['slides'] = [{'layout': 'title_body', 'title': 'Synthetic', 'body': 'Test content'}]
            fn = tool.generate_slides
        else:
            spec['blocks'] = [{'type': 'paragraph', 'text': 'Test content'}]
            fn = tool.generate_document
        kwargs = {'save_to': save_to_param} if save_to_param is not None else {}
        return json.loads(asyncio.run(fn(json.dumps(spec), **kwargs, __event_emitter__=emitter,
                          __request__=object(), __user__={'id': 'synthetic-owner'},
                          __metadata__={'terminal_id': 'selected'} if selected else {})))

    def test_success_partial_failure_and_opt_out_for_both_formats(self):
        for module in self.modules:
            with self.subTest(module=module.__name__):
                tool = module.Tools()
                is_slides = hasattr(tool, 'generate_slides')
                save_name = '_save' if is_slides else '_save_docx'
                name = 'synthetic.pptx' if is_slides else 'synthetic.docx'
                url = '/api/v1/files/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/content'
                events = []
                async def emitter(event): events.append(event)
                save = AsyncMock(return_value=(name, url, None, 'file-id'))
                terminal = AsyncMock(return_value='~/workspace/output/' + name)
                with patch.object(tool, save_name, save), patch.object(module, '_terminal_context', AsyncMock(return_value=('url', {}, {}))), patch.object(module, '_terminal_save', terminal):
                    result = self.invoke(module, tool, selected=True, emitter=emitter)
                    self.assertEqual(result['status'], 'success')
                    self.assertEqual(result['file_id'], 'file-id')
                    self.assertEqual(result['download_url'], url)
                    self.assertTrue(result['terminal_saved'])
                    self.assertTrue(result['terminal_requested'])
                    self.assertEqual(result['workspace_path'], '~/workspace/output/' + name)
                    self.assertEqual(terminal.call_args.args[2], name)
                    attachment = next(e for e in events if e['type'] == 'files')['data']['files'][0]
                    self.assertEqual(attachment['url'], url)
                    self.assertEqual(attachment['id'], 'file-id')
                    terminal.side_effect = RuntimeError('synthetic copy failure')
                    result = self.invoke(module, tool, selected=True)
                    self.assertEqual(result['status'], 'partial_success')
                    self.assertEqual(result['download_url'], url)
                    self.assertFalse(result['terminal_saved'])
                    self.assertIsNone(result['workspace_path'])
                    self.assertTrue(result['warnings'])
                    terminal.reset_mock()
                    result = self.invoke(module, tool, selected=True, terminal_output=False)
                    self.assertEqual(result['status'], 'success')
                    self.assertFalse(result['terminal_requested'])
                    terminal.assert_not_called()

    def test_save_to_chooses_the_destination_and_the_result_links_to_it(self):
        for module in self.modules:
            with self.subTest(module=module.__name__):
                tool = module.Tools()
                is_slides = hasattr(tool, 'generate_slides')
                save_name = '_save' if is_slides else '_save_docx'
                name, extension = ('synthetic.pptx', '.pptx') if is_slides else ('synthetic.docx', '.docx')
                url = '/api/v1/files/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/content'
                save = AsyncMock(return_value=(name, url, None, 'file-id'))
                terminal = AsyncMock(return_value='~/Documents/board pack' + extension)
                with patch.object(tool, save_name, save), patch.object(module, '_terminal_context', AsyncMock(return_value=('url', {}, {}))), patch.object(module, '_terminal_save', terminal):
                    result = self.invoke(module, tool, selected=True, save_to='~/Documents/board pack' + extension)
                    self.assertEqual(terminal.call_args.kwargs['save_to'], '~/Documents/board pack' + extension)
                    self.assertEqual(terminal.call_args.kwargs['extension'], extension)
                    self.assertEqual(result['workspace_path'], '~/Documents/board pack' + extension)
                    self.assertEqual(result['terminal_download_url'], '/api/v1/terminals/selected/files/view?path=Documents%2Fboard%20pack' + extension)
                    self.assertNotIn(result['terminal_download_url'], result['message'])
                    self.assertIn(result['download_url'], result['message'])
                    self.assertEqual(result['download_url'], url)

    def test_save_to_is_a_real_parameter_and_wins_over_the_spec_key(self):
        for module in self.modules:
            with self.subTest(module=module.__name__):
                tool = module.Tools()
                is_slides = hasattr(tool, 'generate_slides')
                save_name = '_save' if is_slides else '_save_docx'
                name, extension = ('synthetic.pptx', '.pptx') if is_slides else ('synthetic.docx', '.docx')
                fn = tool.generate_slides if is_slides else tool.generate_document
                import inspect
                self.assertIn('save_to', inspect.signature(fn).parameters)
                self.assertIn(':param save_to:', fn.__doc__)
                url = '/api/v1/files/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/content'
                terminal = AsyncMock(return_value='~/Documents/board-pack/' + name)
                with patch.object(tool, save_name, AsyncMock(return_value=(name, url, None, 'file-id'))), patch.object(module, '_terminal_context', AsyncMock(return_value=('url', {}, {}))), patch.object(module, '_terminal_save', terminal):
                    result = self.invoke(module, tool, selected=True, save_to_param='~/Documents/board-pack', save_to='ignored/by-the-parameter')
                    self.assertEqual(terminal.call_args.kwargs['save_to'], '~/Documents/board-pack')
                    self.assertEqual(result['workspace_path'], '~/Documents/board-pack/' + name)
                    self.assertEqual(result['terminal_download_url'], '/api/v1/terminals/selected/files/view?path=Documents%2Fboard-pack%2F' + name)

    def test_delivery_options_in_markdown_frontmatter_are_not_dropped(self):
        """The Markdown parser keeps only whitelisted frontmatter keys; terminal_output and save_to must be on it."""
        module = next(m for m in self.modules if hasattr(m, '_parse_markdown'))
        spec = module._parse_markdown('---\ntitle: T\nsave_to: "~/Documents/board-pack"\nterminal_output: true\n---\n\n# Heading\n\nBody\n')
        self.assertEqual(spec.get('save_to'), '~/Documents/board-pack')
        self.assertIs(spec.get('terminal_output'), True)
        self.assertEqual(spec.get('title'), 'T')

    def test_a_bad_save_to_stops_before_anything_is_rendered_or_saved(self):
        for module in self.modules:
            with self.subTest(module=module.__name__):
                tool = module.Tools()
                save_name = '_save' if hasattr(tool, 'generate_slides') else '_save_docx'
                with patch.object(tool, save_name, AsyncMock()) as save, patch.object(module, '_terminal_context', AsyncMock(return_value=('url', {}, {}))), patch.object(module, '_terminal_save', AsyncMock()) as terminal:
                    for bad in ('../x', '/etc/passwd', 'a\\b', 5):
                        result = self.invoke(module, tool, selected=True, save_to=bad)
                        self.assertEqual(result['status'], 'error', bad)
                    save.assert_not_called()
                    terminal.assert_not_called()

    def test_save_to_without_a_terminal_is_an_explicit_request_and_fails(self):
        for module in self.modules:
            with self.subTest(module=module.__name__):
                tool = module.Tools()
                save_name = '_save' if hasattr(tool, 'generate_slides') else '_save_docx'
                no_terminal = ValueError('Select an authorized Open Terminal in this chat first')
                with patch.object(tool, save_name, AsyncMock()) as save, patch.object(module, '_terminal_context', AsyncMock(side_effect=no_terminal)):
                    result = self.invoke(module, tool, selected=False, save_to='projects/report')
                    self.assertEqual(result['status'], 'error')
                    self.assertIn('Open Terminal', result['error'])
                    save.assert_not_called()

    def test_terminal_access_denial_stops_registration(self):
        for module in self.modules:
            tool = module.Tools()
            save_name = '_save' if hasattr(tool, 'generate_slides') else '_save_docx'
            with patch.object(module, '_terminal_context', AsyncMock(side_effect=ValueError('access denied'))), patch.object(tool, save_name, AsyncMock()) as save:
                result = self.invoke(module, tool, selected=True)
                self.assertEqual(result['status'], 'error')
                self.assertIsNone(result['download_url'])
                save.assert_not_called()

    def test_registration_failure_never_writes_cache_or_terminal(self):
        for module in self.modules:
            tool = module.Tools()
            is_slides = hasattr(tool, 'generate_slides')
            save_name = '_save' if is_slides else '_save_docx'
            with tempfile.TemporaryDirectory() as directory:
                if is_slides: tool.valves.pptx_export_dir = directory
                else: tool.valves.docx_export_dir = directory
                with patch.object(module, '_HAS_OWUI_FILES', False):
                    saved = asyncio.run(getattr(tool, save_name)(b'PK', title='Synthetic', request=object(), user_dict={'id': 'owner'}))
                self.assertIsNone(saved[1])
                self.assertIn('registration failed', saved[2])
                self.assertEqual(list(Path(directory).iterdir()), [])
                users = SimpleNamespace(get_user_by_id=AsyncMock(return_value=object()))
                with patch.object(module, '_HAS_OWUI_FILES', True), patch.object(module, 'Users', users, create=True), patch.object(module, 'upload_file_handler', AsyncMock(side_effect=RuntimeError('synthetic registration failure')), create=True):
                    rejected = asyncio.run(getattr(tool, save_name)(b'PK', title='Synthetic', request=object(), user_dict={'id': 'owner'}))
                self.assertIsNone(rejected[1])
                self.assertEqual(list(Path(directory).iterdir()), [])
                with patch.object(module, '_terminal_context', AsyncMock(return_value=('url', {}, {}))), patch.object(tool, save_name, AsyncMock(return_value=saved)), patch.object(module, '_terminal_save', AsyncMock()) as terminal:
                    result = self.invoke(module, tool, selected=True)
                    self.assertEqual(result['status'], 'error')
                    terminal.assert_not_called()


class OfficeResultTerminalUrlTest(unittest.TestCase):
    def test_the_result_carries_a_terminal_download_url_only_when_the_copy_succeeded(self):
        import workspace_delivery
        ok = json.loads(workspace_delivery._office_result('a.docx', '/api/v1/files/f/content', 'f', workspace_path='~/workspace/output/document-x.docx', terminal_requested=True, terminal_id='open-terminal'))
        self.assertEqual(ok['terminal_download_url'], '/api/v1/terminals/open-terminal/files/view?path=workspace%2Foutput%2Fdocument-x.docx')
        self.assertNotIn(ok['terminal_download_url'], ok['message'])
        self.assertIn(ok['download_url'], ok['message'])
        self.assertIn('/api/v1/files/f/content', ok['message'])
        failed = json.loads(workspace_delivery._office_result('a.docx', '/api/v1/files/f/content', 'f', terminal_requested=True, warning='copy failed', terminal_id='open-terminal'))
        self.assertIsNone(failed['terminal_download_url'])
        self.assertEqual(failed['status'], 'partial_success')
        plain = json.loads(workspace_delivery._office_result('a.docx', '/api/v1/files/f/content', 'f'))
        self.assertIsNone(plain['terminal_download_url'])
        self.assertEqual(plain['message'], '[a.docx](/api/v1/files/f/content)')


if __name__ == '__main__':
    unittest.main()
