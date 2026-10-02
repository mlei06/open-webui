"""Unit tests for bootstrap/presets.py and the files it reads (standard library only; no Open WebUI needed).

  python3 Michael/tests/test_presets.py
"""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / 'bootstrap'))
import presets as p  # noqa: E402

DOC, PRESETS = p.load_presets()
BY_ID = {x['id']: x for x in PRESETS}
FILTERS = DOC['filter_ids']


def want(pid, base='base-x'):
    return p.desired_model(BY_ID[pid], base, FILTERS)


def load(doc, prompts=None):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'presets.json').write_text(json.dumps(doc))
        for d in doc['presets']:
            (tmp / d['prompt']).write_text(prompts if prompts is not None else 'prompt')
        return p.load_presets(tmp / 'presets.json', tmp)


class DeclarationTests(unittest.TestCase):
    def test_four_presets_with_expected_ids(self):
        self.assertEqual([x['id'] for x in PRESETS], ['lenny', 'document-translator', 'web-searcher', 'office-agent'])

    def test_default_base_model_is_gemma_not_grok(self):
        self.assertEqual(DOC['base_model'], 'gemma-4-31b-it')

    def test_tools_are_scoped_per_preset(self):
        ids = {k: want(k)['meta']['toolIds'] for k in BY_ID}
        self.assertEqual(ids['document-translator'], ['server:mcp:doctranslator', 'document_translator'])
        self.assertEqual(ids['web-searcher'], [])
        self.assertEqual(ids['office-agent'], ['server:mcp:employee_directory', 'server:mcp:mail'])
        self.assertEqual(
            set(ids['lenny']),
            {'server:mcp:doctranslator', 'document_translator', 'server:mcp:employee_directory', 'server:mcp:mail'},
        )

    def test_only_search_presets_get_web_search(self):
        for k in BY_ID:
            m = want(k)['meta']
            on = k in ('lenny', 'web-searcher')
            self.assertEqual(m['capabilities']['web_search'], on, k)
            self.assertEqual(m['builtinTools']['web_search'], on, k)
            self.assertEqual(m['defaultFeatureIds'], ['web_search'] if on else [], k)

    def test_every_preset_attaches_the_user_context_filter_and_native_calling(self):
        for k in BY_ID:
            w = want(k)
            self.assertEqual(w['meta']['filterIds'], ['user_context'])
            self.assertEqual(w['params']['function_calling'], 'native')
            self.assertEqual(w['params']['system'], BY_ID[k]['system'])

    def test_translation_never_sees_file_contents(self):
        for k in ('lenny', 'document-translator'):
            self.assertFalse(want(k)['meta']['capabilities']['file_context'], k)

    def test_translator_has_no_builtin_tools(self):
        self.assertFalse(any(want('document-translator')['meta']['builtinTools'].values()))

    def test_only_mail_is_optional(self):
        opt = {r.get('server') for x in PRESETS for r in x['tools'] if r.get('optional')}
        self.assertEqual(opt, {'mail'})

    def test_servers_exist_in_mcp_json_except_mail(self):
        declared = {s['id'] for s in json.loads((HERE / 'mcp' / 'mcp.json').read_text())['servers']}
        for x in PRESETS:
            for r in x['tools']:
                if 'server' in r and r['server'] != 'mail':
                    self.assertIn(r['server'], declared)

    def test_user_context_config_covers_every_preset(self):
        cfg = json.loads((HERE / 'models' / 'user-context.json').read_text())['models']
        for k in BY_ID:
            self.assertIn(k, cfg)
        self.assertEqual(cfg['web-searcher'], ['name'])

    def test_prompts_mention_the_user_context_block_and_forbid_sending(self):
        for k in BY_ID:
            self.assertIn('<user_context>', BY_ID[k]['system'], k)
        for k in ('lenny', 'office-agent'):
            self.assertIn('cannot send', BY_ID[k]['system'], k)
        self.assertIn('never read, quote', BY_ID['document-translator']['system'].lower())

    def test_no_secrets_or_internal_hosts_in_prompts(self):
        for x in PRESETS:
            for bad in ('lenovo', 'password', 'http://', 'https://'):
                self.assertNotIn(bad, x['system'].lower(), x['id'])


class ValidationTests(unittest.TestCase):
    def test_bad_declarations_are_rejected(self):
        base = json.loads(p.PRESETS_JSON.read_text())
        cases = []
        d = copy.deepcopy(base); d['schemaVersion'] = 2; cases.append(d)
        d = copy.deepcopy(base); d['presets'][1]['id'] = 'lenny'; cases.append(d)
        d = copy.deepcopy(base); d['presets'][0]['tools'] = [{'server': 'a', 'tool': 'b'}]; cases.append(d)
        d = copy.deepcopy(base); d['presets'][0]['builtin_tools'] = ['nope']; cases.append(d)
        d = copy.deepcopy(base); d['presets'][3]['default_features'] = ['web_search']; cases.append(d)
        d = copy.deepcopy(base); d['presets'][0]['capabilities']['vision'] = 'yes'; cases.append(d)
        d = copy.deepcopy(base); d['base_model'] = ' '; cases.append(d)
        for doc in cases:
            with self.assertRaises(p.ConfigError, msg=json.dumps(doc)[:60]):
                load(doc)

    def test_empty_prompt_is_rejected(self):
        with self.assertRaises(p.ConfigError):
            load(json.loads(p.PRESETS_JSON.read_text()), prompts='  \n')


class MatchTests(unittest.TestCase):
    def live(self, pid='lenny'):
        w = want(pid)
        return json.loads(json.dumps({**w, 'meta': {**w['meta'], 'profile_image_url': 'x'}, 'params': {**w['params'], 'top_k': 5}}))

    def test_equal_model_matches_and_extra_keys_are_ignored(self):
        self.assertTrue(p.matches(self.live(), want('lenny')))

    def test_each_managed_field_is_detected(self):
        for mutate in (
            lambda m: m.update(base_model_id='other'),
            lambda m: m.update(is_active=False),
            lambda m: m.update(access_grants=[]),
            lambda m: m['meta'].update(toolIds=[]),
            lambda m: m['meta'].update(defaultFeatureIds=[]),
            lambda m: m['meta']['capabilities'].update(web_search=False),
            lambda m: m['params'].update(system='changed'),
        ):
            m = self.live()
            mutate(m)
            self.assertFalse(p.matches(m, want('lenny')))

    def test_missing_refs_notes_optional_mail(self):
        got = p.missing_refs(PRESETS, FILTERS, {'doctranslator', 'employee_directory'}, {'document_translator'}, {'user_context'})
        self.assertEqual({(a, c, d) for a, _, c, d in got}, {('lenny', 'mail', True), ('office-agent', 'mail', True)})
        got = p.missing_refs(PRESETS, FILTERS, {'mail'}, set(), set())
        self.assertTrue(any(k == 'filter function' for _, k, _, _ in got))
        self.assertTrue(any(k == 'workspace tool' and not o for _, k, _, o in got))


if __name__ == '__main__':
    unittest.main()
