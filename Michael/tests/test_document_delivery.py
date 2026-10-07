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

    def invoke(self, module, tool, *, selected=False, terminal_output=None, emitter=None):
        spec = {'title': 'Synthetic delivery'}
        if terminal_output is not None:
            spec['terminal_output'] = terminal_output
        if hasattr(tool, 'generate_slides'):
            spec['slides'] = [{'layout': 'title_body', 'title': 'Synthetic', 'body': 'Test content'}]
            fn = tool.generate_slides
        else:
            spec['blocks'] = [{'type': 'paragraph', 'text': 'Test content'}]
            fn = tool.generate_document
        return json.loads(asyncio.run(fn(json.dumps(spec), __event_emitter__=emitter,
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


if __name__ == '__main__':
    unittest.main()
