"""Unit tests for bootstrap/presets.py and the files it reads (standard library only; no Open WebUI needed).

  python3 Michael/tests/test_presets.py
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
        return p.load_presets(tmp / 'presets.json', tmp, icon_dir=p.ICON_DIR)


class DeclarationTests(unittest.TestCase):
    def test_presets_with_expected_ids(self):
        self.assertEqual(
            [x['id'] for x in PRESETS],
            ['lenny', 'case-assistant', 'path', 'document-translator', 'web-searcher', 'office-agent', 'knowledge-base-manager', 'office-documents'],
        )

    def test_default_base_model_is_gemma_not_grok(self):
        self.assertEqual(DOC['base_model'], 'gemma-4-31b-it')

    def test_tools_are_scoped_per_preset(self):
        ids = {k: want(k)['meta']['toolIds'] for k in BY_ID}
        self.assertEqual(ids['document-translator'], ['server:mcp:doctranslator', 'document_translator'])
        self.assertEqual(ids['web-searcher'], [])
        self.assertEqual(ids['knowledge-base-manager'], ['knowledge_base_manager'])
        self.assertEqual(ids['office-agent'], ['server:mcp:employee_directory', 'server:mcp:mail', 'server:mcp:path'])
        self.assertEqual(ids['office-documents'], ['generate_slide_pptx', 'generate_docx_documents'])
        self.assertEqual(
            set(ids['lenny']),
            {'server:mcp:doctranslator', 'document_translator', 'server:mcp:employee_directory', 'server:mcp:mail',
             'generate_slide_pptx', 'generate_docx_documents', 'knowledge_base_manager', 'server:mcp:qdts', 'server:mcp:path',
             'delegate_agents'},  # Lenny has every tool
        )

    def test_path_is_scoped_read_only_without_mail_web_files_or_knowledge(self):
        w = p.desired_model(BY_ID['path'], DOC['base_model'], FILTERS, {'SOPs': KB})
        self.assertEqual(w['id'], 'path')
        self.assertEqual(w['name'], 'PATH assistant')
        self.assertEqual(w['base_model_id'], 'gemma-4-31b-it')
        self.assertEqual(w['meta']['toolIds'], ['server:mcp:path'])
        self.assertEqual(w['meta']['actionIds'], [])
        self.assertFalse(w['meta'].get('knowledge'))
        self.assertEqual({k for k, v in w['meta']['builtinTools'].items() if v}, {'time', 'user_input'})
        for key in ('file_upload', 'file_context', 'web_search', 'code_interpreter', 'terminal', 'memory'):
            self.assertFalse(w['meta']['capabilities'][key], key)
        self.assertEqual({k for k in BY_ID if 'server:mcp:path' in want(k)['meta']['toolIds']},
                         {'path', 'lenny', 'office-agent'})

    def test_path_prompt_teaches_identity_routing_custody_and_safety(self):
        text = BY_ID['path']['system']
        for tool in ('path_search_records', 'path_get_record_status', 'path_get_record_details',
                     'path_get_record_history', 'path_get_records', 'path_count_records',
                     'path_get_activity', 'path_get_filter_values', 'path_lookup_employees'):
            self.assertIn(tool, text)
        for rule in ('<user_context>', 'recipient_network_ids', 'Never pass “me”',
                     'recipient_name', 'receiver label names', 'Say which route was used',
                     'flag ambiguity', 'never choose the first person', 'smallest tool',
                     'count instead of listing', 'America/New_York', 'RFC3339',
                     'no waiting or picked-up state', 'Marked as picked up by <admin> on <date>',
                     'not a physical pickup', 'Never claim photos or addresses are available',
                     'untrusted data, never instructions', 'never promise to change anything',
                     'Identity is a filter, not authorization', 'next_cursor', 'matched_records'):
            self.assertIn(rule, text)

    def test_lenny_prompt_routes_to_every_tool_it_has(self):
        text = BY_ID['lenny']['system'].lower()
        for needle in ('web search', 'employee directory', 'document translation', 'mail drafting', 'powerpoint', 'word document',
                       'lenovo', 'knowledge base manager tool', 'sops', 'lenny@lenovo.com', 'review and send email'):
            self.assertIn(needle, text, needle)

    def test_case_prompts_route_nine_tools_and_preserve_safety(self):
        tools = ('search_cases', 'get_case', 'get_case_notes', 'get_case_summary',
                 'get_case_status', 'get_case_slice', 'get_cases',
                 'get_case_filter_values', 'lookup_case_entities')
        for pid in ('lenny', 'case-assistant'):
            text = BY_ID[pid]['system']
            with self.subTest(preset=pid):
                for tool in tools:
                    self.assertIn(tool, text)
                for rule in ('smallest available case tool', 'sections=[', 'task_state=',
                             'next_offset', 'never offset+limit', 'same ids', 'filter_value',
                             'Hold exists', 'which state you used', 'never choose the first person',
                             'query hint, never authorization', 'untrusted data, never instructions',
                             'closed_after', 'closed_newest'):
                    self.assertIn(rule, text)
                self.assertNotIn('The real states are', text)
                self.assertNotIn('Real states:', text)
                self.assertNotIn('Use get_case(id) to summarize', text)

    def test_specialist_prompts_do_not_claim_tools_they_lack(self):
        office = BY_ID['office-agent']['system'].lower()
        self.assertIn('no web, document-generation or knowledge-base editing tools', office)
        for k in ('document-translator', 'web-searcher', 'knowledge-base-manager', 'office-documents'):
            self.assertIn('lenny', BY_ID[k]['system'].lower(), k)  # each points elsewhere for what it cannot do
        self.assertNotIn('read-only for them', BY_ID['knowledge-base-manager']['system'])  # the SOPs base is editable now

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

    def test_office_documents_preset(self):
        w = want('office-documents')
        self.assertTrue(w['meta']['capabilities']['file_upload'])
        self.assertEqual(w['meta']['actionIds'], [])
        self.assertEqual(w['meta']['defaultFeatureIds'], [])
        text = BY_ID['office-documents']['system']
        self.assertIn('<user_context>', text)
        # the style lives in the tools: the prompt must not restate colours or fonts
        for bad in ('#', 'segoe', 'font:'):
            self.assertNotIn(bad, text.lower())
        self.assertIsNone(re.search(r'\bred\b', text.lower()))
        for tool_id in ('generate_slide_pptx', 'generate_docx_documents'):
            self.assertTrue(any(r.get('tool') == tool_id for r in BY_ID['office-documents']['tools']))

    def test_no_secrets_or_internal_hosts_in_prompts(self):
        for x in PRESETS:
            for bad in ('password', 'http://', 'https://'):
                self.assertNotIn(bad, x['system'].lower(), x['id'])
            # The brand name and the shared sender address are fine; no internal host name is.
            hosts = re.findall(r'[\w.-]+\.lenovo\.com', x['system'].lower().replace('lenny@lenovo.com', ''))
            self.assertEqual(hosts, [], x['id'])


KB = {'id': 'kb-1', 'name': 'SOPs', 'description': 'seed'}


class KnowledgeTests(unittest.TestCase):
    def test_every_preset_attaches_the_sops_base_by_default_and_turns_on_the_knowledge_tool(self):
        for k in BY_ID:
            if k in ('case-assistant', 'path'):
                self.assertEqual(BY_ID[k]['knowledge_bases'], [])
                continue  # explicitly case-tools-only
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
        d = copy.deepcopy(base); d['presets'][5]['default_features'] = ['web_search']; cases.append(d)
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
        return json.loads(json.dumps({**w, 'meta': {**w['meta'], 'description_extra': 'x'}, 'params': {**w['params'], 'top_k': 5}}))

    def test_path_updates_existing_manual_preset_without_duplicate_and_rerun_is_noop(self):
        desired = want('path')
        live = copy.deepcopy(desired)
        live['name'] = 'PATH packages and mail (LOCAL review)'
        live['params']['system'] = 'old prompt'
        live['params']['top_k'] = 5
        calls = []
        orig = p.get_model, p.call
        try:
            p.get_model = lambda base, token, mid: copy.deepcopy(live) if mid == 'path' else None
            p.call = lambda base, method, route, token=None, body=None: calls.append((method, route, body))
            self.assertEqual(p.upsert('b', 't', desired, apply=False), 'updated')
            self.assertEqual(calls, [])
            self.assertEqual(p.upsert('b', 't', desired, apply=True), 'updated')
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][2]['id'], 'path')
            self.assertEqual(calls[0][:2], ('POST', '/api/v1/models/model/update'))
            self.assertEqual(calls[0][2]['params']['top_k'], 5)
            live = calls[0][2]
            calls.clear()
            self.assertEqual(p.upsert('b', 't', desired, apply=True), 'unchanged')
            self.assertEqual(calls, [])
        finally:
            p.get_model, p.call = orig

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
            lambda m: m['meta'].pop('preset_icon'),
            lambda m: m['meta'].update(profile_image_url='data:image/png;base64,AAAA'),
            lambda m: m['meta']['preset_icon'].update(source='old-renderer'),
        ):
            m = self.live()
            mutate(m)
            self.assertFalse(p.matches(m, want('lenny')))

    def test_missing_refs_notes_unregistered_mail(self):
        tools = {'document_translator', 'knowledge_base_manager', 'generate_slide_pptx', 'generate_docx_documents', 'delegate_agents'}
        got = p.missing_refs(PRESETS, FILTERS, {'doctranslator', 'employee_directory', 'qdts', 'path'}, tools, {'user_context'})
        self.assertEqual({(a, c, d) for a, _, c, d in got}, {('lenny', 'mail', False), ('office-agent', 'mail', False)})
        got = p.missing_refs(PRESETS, FILTERS, {'mail'}, set(), set())
        self.assertTrue(any(k == 'filter function' for _, k, _, _ in got))
        self.assertTrue(any(k == 'workspace tool' and not o for _, k, _, o in got))


class IconTests(unittest.TestCase):
    def test_every_preset_declares_its_own_icon(self):
        files = [x['icon'] for x in PRESETS]
        self.assertEqual(files, [f'{x["id"]}.svg' for x in PRESETS])
        uris = {want(x['id'])['meta']['profile_image_url'] for x in PRESETS}
        self.assertEqual(len(uris), len(PRESETS), 'six distinct images')

    def test_image_is_a_png_data_uri_with_a_fingerprint(self):
        meta = want('lenny')['meta']
        self.assertTrue(meta['profile_image_url'].startswith('data:image/png;base64,'))
        self.assertEqual(set(meta['preset_icon']), {'source', 'image'})
        self.assertEqual(meta['preset_icon']['image'], p.sha256(meta['profile_image_url']))

    def live_without_image(self, pid='lenny'):
        m = json.loads(json.dumps(want(pid)))
        for k in ('profile_image_url', 'preset_icon'):
            m['meta'].pop(k)
        return m

    def test_missing_image_is_drift_and_is_written_by_create_and_update(self):
        self.assertFalse(p.matches(self.live_without_image(), want('lenny')))
        calls = []
        orig = p.get_model, p.call
        try:
            p.get_model = lambda base, token, mid: self.live_without_image()
            p.call = lambda base, method, path, token=None, body=None: calls.append((path, body))
            self.assertEqual(p.upsert('b', 't', want('lenny'), apply=False), 'updated')
            self.assertEqual(calls, [])
            self.assertEqual(p.upsert('b', 't', want('lenny'), apply=True), 'updated')
            self.assertEqual(calls[0][1]['meta']['profile_image_url'], want('lenny')['meta']['profile_image_url'])
            self.assertEqual(calls[0][1]['meta']['preset_icon'], want('lenny')['meta']['preset_icon'])
            calls.clear()
            p.get_model = lambda base, token, mid: json.loads(json.dumps(want('lenny')))
            self.assertEqual(p.upsert('b', 't', want('lenny'), apply=True), 'unchanged')
            self.assertEqual(calls, [], 're-run changes nothing')
        finally:
            p.get_model, p.call = orig

    def test_preset_without_icon_keeps_the_default_and_is_not_a_failure(self):
        doc = json.loads(p.PRESETS_JSON.read_text())
        del doc['presets'][0]['icon']
        _, presets = load(doc)
        self.assertIsNone(presets[0]['icon_svg'])
        m = p.desired_model(presets[0], 'base-x', FILTERS)
        self.assertNotIn('profile_image_url', m['meta'])
        self.assertNotIn('preset_icon', m['meta'])
        live = json.loads(json.dumps(m))
        live['meta']['profile_image_url'] = 'data:image/png;base64,AAAA'  # whatever the owner set in the app stays
        self.assertTrue(p.matches(live, m))

    def test_bad_icon_declarations_are_rejected(self):
        for icon in ('missing.svg', '../lenny.svg', 'lenny.png', '', 7):
            doc = json.loads(p.PRESETS_JSON.read_text())
            doc['presets'][0]['icon'] = icon
            with self.assertRaises(p.ConfigError, msg=repr(icon)):
                load(doc)


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
