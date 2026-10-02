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
    def test_five_presets_with_expected_ids(self):
        self.assertEqual(
            [x['id'] for x in PRESETS],
            ['lenny', 'document-translator', 'web-searcher', 'office-agent', 'knowledge-base-manager'],
        )

    def test_default_base_model_is_gemma_not_grok(self):
        self.assertEqual(DOC['base_model'], 'gemma-4-31b-it')

    def test_tools_are_scoped_per_preset(self):
        ids = {k: want(k)['meta']['toolIds'] for k in BY_ID}
        self.assertEqual(ids['document-translator'], ['server:mcp:doctranslator', 'document_translator'])
        self.assertEqual(ids['web-searcher'], [])
        self.assertEqual(ids['knowledge-base-manager'], ['knowledge_base_manager'])
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

    def test_translator_has_only_the_knowledge_builtin_tool(self):
        on = {k for k, v in want('document-translator')['meta']['builtinTools'].items() if v}
        self.assertEqual(on, {'knowledge'})

    def test_manager_reads_attached_files_in_full_and_never_searches_the_web(self):
        m = want('knowledge-base-manager')['meta']
        self.assertTrue(m['capabilities']['file_upload'])
        self.assertFalse(m['capabilities']['file_context'])
        self.assertTrue(m['builtinTools']['files'])
        self.assertFalse(m['builtinTools']['web_search'])

    def test_servers_exist_in_mcp_json(self):
        declared = {s['id'] for s in json.loads((HERE / 'mcp' / 'mcp.json').read_text())['servers']}
        for x in PRESETS:
            for r in x['tools']:
                if 'server' in r:
                    self.assertIn(r['server'], declared)

    def test_mail_action_only_on_lenny_and_office(self):
        for k in BY_ID:
            on = k in ('lenny', 'office-agent')
            self.assertEqual(want(k)['meta']['actionIds'], ['mail_review'] if on else [], k)
            self.assertEqual('server:mcp:mail' in want(k)['meta']['toolIds'], on, k)

    def test_mail_action_is_declared_and_loaded(self):
        self.assertEqual([a['id'] for a in DOC['actions']], ['mail_review'])
        self.assertIn('class Action', DOC['actions'][0]['content'])

    def test_mail_prompts_use_the_button_and_attachment_ids(self):
        for k in ('lenny', 'office-agent'):
            text = BY_ID[k]['system']
            self.assertIn('Review and send email', text, k)
            self.assertIn('suggested_attachment_ids', text, k)

    def test_user_context_config_covers_every_preset(self):
        cfg = json.loads((HERE / 'models' / 'user-context.json').read_text())['models']
        for k in BY_ID:
            self.assertIn(k, cfg)
        self.assertEqual(cfg['web-searcher'], ['name'])
        self.assertEqual(cfg['knowledge-base-manager'], ['name'])

    def test_prompts_mention_the_user_context_block_and_forbid_sending(self):
        for k in BY_ID:
            self.assertIn('<user_context>', BY_ID[k]['system'], k)
        for k in ('lenny', 'office-agent'):
            self.assertIn('cannot send', BY_ID[k]['system'], k)
        self.assertIn('never read, quote', BY_ID['document-translator']['system'].lower())

    def test_manager_prompt_confirms_before_overwriting_or_deleting_and_describes_results(self):
        text = BY_ID['knowledge-base-manager']['system'].lower()
        for needle in ('<attached_files>', 'confirm', 'overwrite', 'delete', 'indexed', 'duplicate', 'markdown'):
            self.assertIn(needle, text, needle)

    def test_no_secrets_or_internal_hosts_in_prompts(self):
        for x in PRESETS:
            for bad in ('lenovo', 'password', 'http://', 'https://'):
                self.assertNotIn(bad, x['system'].lower(), x['id'])


KB = {'id': 'kb-1', 'name': 'SOPs', 'description': 'seed'}


class KnowledgeTests(unittest.TestCase):
    def test_every_preset_attaches_the_sops_base_by_default_and_turns_on_the_knowledge_tool(self):
        for k in BY_ID:
            self.assertEqual(BY_ID[k]['knowledge_bases'], ['SOPs'], k)
            w = p.desired_model(BY_ID[k], 'base-x', FILTERS, {'SOPs': KB})
            self.assertEqual(w['meta']['knowledge'], [{'id': 'kb-1', 'name': 'SOPs', 'type': 'collection', 'description': 'seed'}], k)
            self.assertTrue(w['meta']['builtinTools']['knowledge'], k)

    def test_base_that_does_not_exist_yet_is_not_attached_but_the_tool_is_on(self):
        w = want('lenny')
        self.assertNotIn('knowledge', w['meta'])
        self.assertTrue(w['meta']['builtinTools']['knowledge'])

    def test_user_added_knowledge_is_kept_and_a_missing_attachment_is_detected(self):
        w = p.desired_model(BY_ID['lenny'], 'base-x', FILTERS, {'SOPs': KB})
        live = json.loads(json.dumps(w))
        self.assertTrue(p.matches(live, w))
        live['meta']['knowledge'].append({'id': 'mine', 'name': 'Mine', 'type': 'collection'})
        self.assertTrue(p.matches(live, w))
        merged = p.merge_knowledge(live['meta']['knowledge'], w['meta']['knowledge'])
        self.assertEqual([k['id'] for k in merged], ['kb-1', 'mine'])
        del live['meta']['knowledge'][0]
        self.assertFalse(p.matches(live, w))
        live['meta']['knowledge'] = [{**w['meta']['knowledge'][0], 'name': 'Old name'}]
        self.assertFalse(p.matches(live, w))

    def test_unknown_knowledge_base_id_is_rejected(self):
        doc = json.loads(p.PRESETS_JSON.read_text())
        doc['knowledge_bases'] = ['nope']
        with self.assertRaises(p.ConfigError):
            load(doc)
        doc = json.loads(p.PRESETS_JSON.read_text())
        doc['presets'][2]['knowledge_bases'] = []
        self.assertEqual(load(doc)[1][2]['knowledge_bases'], [])


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
        d = copy.deepcopy(base); d['presets'][0]['actions'] = ['nope']; cases.append(d)
        d = copy.deepcopy(base); d['actions'][0]['file'] = 'missing.py'; cases.append(d)
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

    def test_missing_refs_notes_unregistered_mail(self):
        got = p.missing_refs(
            PRESETS, FILTERS, {'doctranslator', 'employee_directory'}, {'document_translator', 'knowledge_base_manager'}, {'user_context'}
        )
        self.assertEqual({(a, c, d) for a, _, c, d in got}, {('lenny', 'mail', False), ('office-agent', 'mail', False)})
        got = p.missing_refs(PRESETS, FILTERS, {'mail'}, set(), set())
        self.assertTrue(any(k == 'filter function' for _, k, _, _ in got))
        self.assertTrue(any(k == 'workspace tool' and not o for _, k, _, o in got))


class WebSearchTests(unittest.TestCase):
    def test_unchanged_when_perplexity_is_saved(self):
        web = {'ENABLE_WEB_SEARCH': True, 'WEB_SEARCH_ENGINE': 'perplexity_search', 'PERPLEXITY_API_KEY': 'k'}
        self.assertIsNone(p.web_search_form({'web': web}, 'k'))

    def test_merges_into_the_existing_web_block(self):
        cfg = {'web': {'ENABLE_WEB_SEARCH': False, 'WEB_SEARCH_ENGINE': '', 'PERPLEXITY_API_KEY': '', 'WEB_SEARCH_RESULT_COUNT': 3}}
        form = p.web_search_form(cfg, 'k')
        self.assertEqual(form['WEB_SEARCH_RESULT_COUNT'], 3)
        self.assertEqual((form['ENABLE_WEB_SEARCH'], form['WEB_SEARCH_ENGINE'], form['PERPLEXITY_API_KEY']), (True, 'perplexity_search', 'k'))
        self.assertIsNotNone(p.web_search_form({}, 'k'))

    def test_changed_key_is_detected(self):
        web = {'ENABLE_WEB_SEARCH': True, 'WEB_SEARCH_ENGINE': 'perplexity_search', 'PERPLEXITY_API_KEY': 'old'}
        self.assertIsNotNone(p.web_search_form({'web': web}, 'new'))


if __name__ == '__main__':
    unittest.main()
