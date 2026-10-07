"""Terminal copy and result envelope of tools/document_translator.py, using the source bootstrap publishes.

  uv run --no-project --with mcp==1.27.2 --with httpx --with pydantic --with python-pptx --with python-docx \\
     --with pillow --with pyyaml --with lxml --with markdown-it-py --with mdit-py-plugins python Michael/tests/test_document_translator.py
"""
import asyncio
import base64
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'bootstrap'))
import office_tools  # noqa: E402

try:
    import mcp  # noqa: F401
except ImportError:  # pragma: no cover
    mcp = None


def load():
    module = type(sys)('document_translator_test')
    module.__file__ = str(ROOT / 'tools' / 'document_translator.py')
    sys.modules[module.__name__] = module
    source = office_tools.bundle_delivery_source((ROOT / 'tools' / 'document_translator.py').read_text())
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    return module


def fetched(content=b'translated bytes', name='deck.zh.pptx'):
    return {
        'job_id': 'j1',
        'file': {
            'content_base64': base64.b64encode(content).decode(),
            'content_sha256': hashlib.sha256(content).hexdigest(),
            'content_type': 'application/octet-stream',
            'content_disposition': f'attachment; filename="{name}"',
        },
    }


@unittest.skipIf(mcp is None, 'the mcp package is not installed')
class TranslatorTerminalTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mod = load()
        self.tool = self.mod.Tools()
        self.cfg = {'owui': 'http://x'}
        self.store = patch.object(self.mod.Tools, '_store', new=AsyncMock(return_value='file-1'))
        self.store.start()
        self.addCleanup(self.store.stop)

    async def deliver(self, terminal):
        return await self.tool._deliver(self.cfg, fetched(), 'deck.pptx', 'zh', None, None, terminal)

    async def test_without_a_terminal_the_result_is_a_plain_download(self):
        result = await self.deliver(None)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['download_url'], '/api/v1/files/file-1/content?attachment=true')
        self.assertFalse(result['terminal_saved'])
        self.assertIsNone(result['workspace_path'])
        self.assertIsNone(result['terminal_download_url'])
        self.assertNotIn('saved at', result['message'])

    async def test_with_a_terminal_the_copy_and_both_links_are_reported(self):
        saved = AsyncMock(return_value='~/workspace/output/translation-deck.zh-1a2b3c4d.pptx')
        with patch.object(self.mod, '_terminal_save', new=saved):
            result = await self.deliver({'requested': True, 'id': 'open-terminal', 'context': ('u', {}, {}), 'warning': None})
        self.assertEqual(result['status'], 'succeeded')
        self.assertTrue(result['terminal_saved'])
        self.assertEqual(result['workspace_path'], '~/workspace/output/translation-deck.zh-1a2b3c4d.pptx')
        self.assertEqual(
            result['terminal_download_url'],
            '/api/v1/terminals/open-terminal/files/view?path=workspace%2Foutput%2Ftranslation-deck.zh-1a2b3c4d.pptx',
        )
        self.assertIn('/api/v1/files/file-1/content?attachment=true', result['message'])
        self.assertNotIn(result['terminal_download_url'], result['message'])
        self.assertNotIn(result['workspace_path'], result['message'])
        self.assertEqual(result['message'].count(']('), 1)
        self.assertEqual(saved.await_args.args[1], b'translated bytes')
        self.assertEqual(saved.await_args.args[2], 'deck.zh.pptx')
        self.assertEqual(saved.await_args.kwargs['extension'], '.pptx')
        self.assertIsNone(saved.await_args.kwargs['save_to'])
        self.assertEqual(result['size'], len(b'translated bytes'))

    async def test_a_failed_terminal_copy_keeps_the_download_and_claims_no_path(self):
        with patch.object(self.mod, '_terminal_save', new=AsyncMock(side_effect=ValueError('boom'))):
            result = await self.deliver({'requested': True, 'id': 'open-terminal', 'context': ('u', {}, {}), 'warning': None})
        self.assertEqual(result['status'], 'partial_success')
        self.assertFalse(result['terminal_saved'])
        self.assertIsNone(result['workspace_path'])
        self.assertIsNone(result['terminal_download_url'])
        self.assertEqual(result['download_url'], '/api/v1/files/file-1/content?attachment=true')
        self.assertTrue(result['warnings'])
        self.assertNotIn('boom', json.dumps(result))  # internal error text is not shown to the model

    async def test_an_unavailable_implicit_terminal_falls_back_to_download_only(self):
        with patch.object(self.mod, '_terminal_context', new=AsyncMock(side_effect=ValueError('access denied'))):
            state = await self.tool._terminal(None, None, {'id': 'u'}, {'terminal_id': 'open-terminal'})
            self.assertTrue(state['requested'])
            self.assertIsNone(state['context'])
            self.assertIn('unavailable', state['warning'])
            result = await self.deliver(state)
        self.assertEqual(result['status'], 'partial_success')
        self.assertEqual(result['download_url'], '/api/v1/files/file-1/content?attachment=true')

    async def test_an_explicit_request_that_cannot_be_met_fails_before_any_translation(self):
        with patch.object(self.mod, '_terminal_context', new=AsyncMock(side_effect=ValueError('no terminal'))):
            with self.assertRaises(self.mod.ToolError):
                await self.tool._terminal(True, None, {'id': 'u'}, {})

    async def test_no_terminal_selected_means_no_terminal_work(self):
        context = AsyncMock()
        with patch.object(self.mod, '_terminal_context', new=context):
            state = await self.tool._terminal(None, None, {'id': 'u'}, {})
            off = await self.tool._terminal(False, None, {'id': 'u'}, {'terminal_id': 'open-terminal'})
        self.assertFalse(state['requested'])
        self.assertFalse(off['requested'])
        context.assert_not_awaited()


@unittest.skipIf(mcp is None, 'the mcp package is not installed')
class TranslatorSaveToTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mod = load()
        self.tool = self.mod.Tools()
        store = patch.object(self.mod.Tools, '_store', new=AsyncMock(return_value='file-1'))
        store.start()
        self.addCleanup(store.stop)

    async def test_save_to_is_passed_to_the_shared_save_and_implies_a_terminal_copy(self):
        with patch.object(self.mod, '_terminal_context', new=AsyncMock(return_value=('u', {}, {}))):
            state = await self.tool._terminal(None, None, {'id': 'u'}, {'terminal_id': 'open-terminal'}, 'projects/report')
        self.assertTrue(state['requested'])
        saved = AsyncMock(return_value='~/workspace/projects/report/deck.zh.pptx')
        with patch.object(self.mod, '_terminal_save', new=saved):
            result = await self.tool._deliver({'owui': 'http://x'}, fetched(), 'deck.pptx', 'zh', None, None, state)
        self.assertEqual(saved.await_args.kwargs['save_to'], 'projects/report')
        self.assertEqual(result['workspace_path'], '~/workspace/projects/report/deck.zh.pptx')
        self.assertIn('projects%2Freport', result['terminal_download_url'])

    async def test_save_to_without_a_selected_terminal_is_an_explicit_request_and_fails_early(self):
        with patch.object(self.mod, '_terminal_context', new=AsyncMock(side_effect=ValueError('Select an authorized Open Terminal in this chat first'))):
            with self.assertRaises(self.mod.ToolError):
                await self.tool._terminal(None, None, {'id': 'u'}, {}, '~/Documents')

    async def test_an_unusable_save_to_is_refused_before_any_translation(self):
        for bad in ('../x', '/etc', 'a\\b', 5):
            with self.assertRaises(self.mod.ToolError, msg=repr(bad)):
                await self.tool._terminal(None, None, {'id': 'u'}, {'terminal_id': 'open-terminal'}, bad)


if __name__ == '__main__':
    unittest.main()
