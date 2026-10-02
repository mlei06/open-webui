"""Unit tests for knowledge/, bootstrap/knowledge_bases.py, bootstrap/kb_manager_tool.py and the committed
tool export (standard library only; no Open WebUI needed).

  python3 Michael/tests/test_knowledge_bases.py
"""

import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / 'bootstrap'))
import kb_manager_tool as t  # noqa: E402
import knowledge_bases as k  # noqa: E402

KBS = k.load_manifest()
SOPS = next(x for x in KBS if x['id'] == 'sops')
TOOL = t.load_tool()


def load(doc, files=None):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'manifest.json').write_text(json.dumps(doc))
        for name, text in (files if files is not None else {'a.md': 'text'}).items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        return k.load_manifest(root / 'manifest.json', root)


def doc(**over):
    kb = {'id': 'x', 'name': 'X', 'description': 'd', 'files': ['a.md'], **over}
    return {'schemaVersion': 1, 'knowledge_bases': [kb]}


class SeedContentTests(unittest.TestCase):
    def test_sops_manifest_lists_both_decks(self):
        self.assertEqual(SOPS['name'], 'SOPs')
        self.assertEqual(set(SOPS['texts']), {'path-sop.md', 'ai-video-workflow.md'})

    def test_path_sop_answers_who_to_ask_and_has_every_procedure(self):
        text = SOPS['texts']['path-sop.md']
        for needle in ('package tracking system', 'Who to ask', 'Michael', 'Contact Michael if setup is blocked',
                       'Badge setup and sign-in', 'Notification preferences', 'Picking up a package',
                       'Check in all', 'Phone setup', 'Speaker notes'):
            self.assertTrue(needle in text, needle)
        self.assertEqual(len(re.findall(r'^## Slide \d+:', text, re.M)), 16)

    def test_ai_video_workflow_has_the_tools_and_steps(self):
        text = SOPS['texts']['ai-video-workflow.md']
        for needle in ('HyperFrames', 'ComfyUI', 'ElevenLabs', 'Build the storyboard', 'The spec for each clip',
                       'Create an API key', 'who to ask', 'Michael', 'Speaker notes'):
            self.assertTrue(needle in text, needle)
        self.assertEqual(len(re.findall(r'^## Slide \d+:', text, re.M)), 13)

    def test_no_binary_decks_or_credentials_are_committed(self):
        self.assertEqual([p.name for p in (HERE / 'knowledge').rglob('*') if p.suffix in ('.pptx', '.mp4', '.png', '.jpg', '.gif')], [])
        for text in SOPS['texts'].values():
            self.assertNotRegex(text, r'(?i)(sk-[a-z0-9]{20,}|api[_-]?key\s*=\s*[a-z0-9]{16,})')


class ManifestValidationTests(unittest.TestCase):
    def test_valid_manifest_loads_with_file_text(self):
        self.assertEqual(load(doc())[0]['texts'], {'a.md': 'text'})

    def test_bad_manifests_are_rejected(self):
        bad = [
            {'schemaVersion': 2, 'knowledge_bases': doc()['knowledge_bases']},
            {'schemaVersion': 1, 'knowledge_bases': []},
            doc(id=''),
            doc(name=' '),
            doc(files=[]),
            doc(files=['missing.md']),
            doc(files=['../outside.md']),
            doc(files=['a.txt']),
        ]
        for d in bad:
            with self.assertRaises(k.ConfigError, msg=json.dumps(d)[:80]):
                load(d, {'a.md': 'text', 'a.txt': 'text'})

    def test_duplicates_and_empty_files_are_rejected(self):
        d = doc()
        d['knowledge_bases'].append(copy.deepcopy(d['knowledge_bases'][0]))
        with self.assertRaises(k.ConfigError):
            load(d)
        with self.assertRaises(k.ConfigError):
            load(doc(files=['a.md', 'sub/a.md']), {'a.md': 'x', 'sub/a.md': 'y'})
        with self.assertRaises(k.ConfigError):
            load(doc(), {'a.md': '  \n'})


class ComparisonTests(unittest.TestCase):
    def test_same_ignores_outer_whitespace_and_open_webui_quote_straightening(self):
        self.assertTrue(k.same('everyone\'s "note"\n', 'everyone’s “note”'))
        self.assertFalse(k.same('everyone', 'someone'))

    def test_extra_user_text_is_a_difference(self):
        self.assertFalse(k.same('seed\n\nUser addition', 'seed'))

    def test_find_knowledge_base_matches_exact_name_and_rejects_duplicates(self):
        kbs = [{'id': '1', 'name': 'SOPs'}, {'id': '2', 'name': 'Other'}]
        self.assertEqual(k.find_knowledge_base(None, None, 'SOPs', kbs)['id'], '1')
        self.assertIsNone(k.find_knowledge_base(None, None, 'sops', kbs))
        with self.assertRaises(k.ApiError):
            k.find_knowledge_base(None, None, 'SOPs', kbs + [{'id': '3', 'name': 'SOPs'}])


class ToolExportTests(unittest.TestCase):
    def test_export_loads_as_a_public_read_tool_form(self):
        self.assertEqual(TOOL['id'], 'knowledge_base_manager')
        self.assertEqual(TOOL['access_grants'], t.PUBLIC_READ)
        compile(TOOL['content'], 'knowledge_base_manager', 'exec')

    def test_bad_exports_are_rejected(self):
        for payload in ('not json', '[]', json.dumps([{'id': 'a', 'name': 'b', 'content': 'x = 1'}])):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'tool.json'
                path.write_text(payload)
                with self.assertRaises(t.ApiError):
                    t.load_tool(path)

    def test_every_delete_needs_confirmation_and_requests_use_the_callers_own_auth(self):
        src = TOOL['content']
        for name in re.findall(r'async def (delete_knowledge_(?:file|file_at_path|base))\(', src):
            body = src.split(f'async def {name}(')[1].split('    async def ')[0]
            self.assertIn('confirm is not True', body, name)
        self.assertIn('request.headers.get("authorization")', src)
        self.assertIn('request.cookies', src)
        self.assertNotIn('print(', src)
        self.assertNotIn('logging', src)
        self.assertEqual(set(re.findall(r'https?://[^\s"\']+', src)), {'http://127.0.0.1:8080/api/v1'})

    def test_state_compares_content_name_description_and_grant(self):
        form = TOOL
        live = {'content': form['content'], 'name': form['name'], 'meta': form['meta'], 'access_grants': t.PUBLIC_READ}
        calls = []

        def fake(base, method, path, token, body=None):
            calls.append(path)
            return [{'id': form['id']}] if path == '/api/v1/tools/' else live

        orig = t.call
        t.call = fake
        try:
            self.assertEqual(t.state('b', 't', form), 'current')
            live['access_grants'] = []
            self.assertEqual(t.state('b', 't', form), 'differs')
            live['access_grants'] = t.PUBLIC_READ
            live['content'] += '#'
            self.assertEqual(t.state('b', 't', form), 'differs')
        finally:
            t.call = orig


if __name__ == '__main__':
    unittest.main()
