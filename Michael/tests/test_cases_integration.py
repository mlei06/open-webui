"""Offline deployment safety/contracts. Run: python3 Michael/tests/test_cases_integration.py"""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / 'bootstrap'))
sys.path.insert(0, str(HERE / 'tests/fixtures'))
import cases_model
import case_safety
import init_env
import mcp_servers
import presets
import provision


class GuardTests(unittest.TestCase):
    ENV = {'OPENAI_API_BASE_URLS': 'https://davy.example.invalid/v1'}

    def test_plain_nemotron_allowed(self):
        case_safety.validate_env(self.ENV)
        with mock.patch.object(case_safety, 'call', return_value={'OPENAI_API_BASE_URLS': [self.ENV['OPENAI_API_BASE_URLS']], 'OPENAI_API_KEYS': ['synthetic-davy-key']}) as api:
            case_safety.validate_live('http://test', 'not-a-real-token', self.ENV)
            self.assertEqual(api.call_count, 1)

    def test_outside_key_models_alias_and_multiple_connections_denied_secret_free(self):
        secret = 'synthetic-test-value-not-a-credential'
        bad = [({'XAI_API_KEY': secret}, None), ({'PRESETS_BASE_MODEL': 'grok'}, None),
               ({'TRANSLATOR_BASE_MODEL': 'grok'}, None), ({}, 'grok'), ({}, 'gemma-4-31b-it'),
               ({'QDTS_CUSTOMER_NAMES': 'alias'}, None),
               ({'OPENAI_API_BASE_URLS': 'https://davy.example.invalid/v1;https://outside.example.invalid/v1'}, None)]
        for extra, override in bad:
            with self.subTest(extra=extra), self.assertRaises(case_safety.ApiError) as err:
                case_safety.validate_env({**self.ENV, **extra}, override)
            self.assertNotIn(secret, str(err.exception))

    def saved_connections(self):
        return {
            'OPENAI_API_BASE_URLS': ['https://api.openai.com/v1', self.ENV['OPENAI_API_BASE_URLS'], 'https://api.x.ai/v1'],
            'OPENAI_API_KEYS': ['', 'synthetic-davy-key', 'synthetic-outside-key'],
            'OPENAI_API_CONFIGS': {'1': {'enable': True}, '2': {'enable': False}},
        }

    def test_live_shape_with_keyless_and_disabled_outside_connections_allowed(self):
        with mock.patch.object(case_safety, 'call', return_value=self.saved_connections()) as api:
            case_safety.validate_live('http://test', 'token', self.ENV)
            api.assert_called_once_with('http://test', 'GET', '/openai/config', 'token')

    def test_outside_connection_requires_disabled_or_keyless(self):
        for config, key, allowed in [
            ({'enable': True}, 'synthetic-secret-key', False),
            ({'enable': False}, 'synthetic-secret-key', True),
            ({'enable': True}, '', True),
            (None, 'synthetic-secret-key', False),
            ({}, 'synthetic-secret-key', False),
            ({'enable': 0}, 'synthetic-secret-key', False),
        ]:
            cfg = self.saved_connections()
            cfg['OPENAI_API_BASE_URLS'][2] = 'https://synthetic-user:synthetic-password@outside.example.invalid/v1'
            cfg['OPENAI_API_KEYS'][2] = key
            if config is None:
                del cfg['OPENAI_API_CONFIGS']['2']
            else:
                cfg['OPENAI_API_CONFIGS']['2'] = config
            with self.subTest(config=config, key=key), mock.patch.object(case_safety, 'call', return_value=cfg):
                if allowed:
                    case_safety.validate_live('http://test', 'token', self.ENV)
                else:
                    with self.assertRaisesRegex(case_safety.ApiError, 'saved non-Davy') as err:
                        case_safety.validate_live('http://test', 'token', self.ENV)
                    for secret in ('synthetic-secret-key', 'synthetic-user', 'synthetic-password', 'outside.example.invalid'):
                        self.assertNotIn(secret, str(err.exception))

    def test_missing_approved_endpoint_refused(self):
        for urls, keys in [([], []), (['https://outside.example.invalid/v1'], [''])]:
            with self.subTest(urls=urls), mock.patch.object(case_safety, 'call', return_value={
                    'OPENAI_API_BASE_URLS': urls, 'OPENAI_API_KEYS': keys, 'OPENAI_API_CONFIGS': {}}):
                with self.assertRaisesRegex(case_safety.ApiError, 'approved Davy endpoint is missing'):
                    case_safety.validate_live('http://test', 'token', self.ENV)
        with self.assertRaisesRegex(case_safety.ApiError, 'approved Davy'):
            case_safety.validate_live('http://test', 'token', {})

    def test_misaligned_saved_connections_refused(self):
        for extra in [
            {'OPENAI_API_KEYS': ['synthetic-key']},
            {'OPENAI_API_KEYS': ['', 'synthetic-key', '', 'extra-key']},
            {'OPENAI_API_CONFIGS': {'3': {'enable': False}}},
            {'OPENAI_API_CONFIGS': {'outside': {'enable': False}}},
            {'OPENAI_API_CONFIGS': [{'enable': False}]},
            {'OPENAI_API_CONFIGS': {'2': None}},
            {'OPENAI_API_BASE_URLS': ['', self.ENV['OPENAI_API_BASE_URLS'], 'https://outside.example.invalid/v1']},
        ]:
            with self.subTest(extra=extra), mock.patch.object(case_safety, 'call', return_value={**self.saved_connections(), **extra}):
                with self.assertRaisesRegex(case_safety.ApiError, 'cannot be aligned') as err:
                    case_safety.validate_live('http://test', 'token', self.ENV)
                self.assertNotIn('synthetic-key', str(err.exception))

    def test_provision_and_standalone_presets_refuse_before_auth_or_write(self):
        env = {**self.ENV, 'XAI_API_KEY': 'synthetic-refused-value'}
        for mod in (provision, presets, mcp_servers):
            out = io.StringIO()
            with self.subTest(script=mod.__name__), mock.patch.object(mod, 'load_env', return_value=env), contextlib.redirect_stdout(out):
                # MCP env resolution happens after its guard, so no unrelated service keys needed.
                self.assertEqual((mod.main if mod is provision else mod.run)([]), 1)
            self.assertNotIn(env['XAI_API_KEY'], out.getvalue())
            self.assertIn('Plain QDTS', out.getvalue())


class DeclarationTests(unittest.TestCase):
    def test_fixture_provider_tolerates_old_service_sort_schema(self):
        for sorts in (['created_newest'], ['created_newest', 'closed_newest']):
            response = cases_model.response({'messages': [
                {'role': 'system', 'content': 'id: fixtureone untrusted closed_newest'},
                {'role': 'user', 'content': 'TEST_CLOSED'}],
                'tools': [{'function': {'name': 'qdts_search_cases', 'parameters': {
                    'properties': {'sort': {'anyOf': [{'enum': sorts}]}}}}}]})
            args = json.loads(response['tool_calls'][0]['function']['arguments'])
            self.assertEqual(args['state'], 'closed')
            self.assertEqual(args['closed_after'], '2026-01-01')
            self.assertEqual(args.get('sort'), 'closed_newest' if 'closed_newest' in sorts else None)

    def test_shared_bearer_no_identity_no_writes(self):
        server = next(s for s in mcp_servers.load_servers() if s['id'] == 'qdts')
        connection = mcp_servers.desired_connection(server, mcp_servers.resolve(server, {'QDTS_MCP_API_KEY': 'synthetic'}), {})
        self.assertEqual(connection['url'], 'http://qdts-cases:8000/mcp')
        self.assertEqual(connection['auth_type'], 'bearer')
        self.assertIsNone(connection['headers'])
        self.assertEqual(set(server['tools']), {'search_cases','aggregate_cases','search_product','search_team','search_customer','get_case','get_case_notes','get_case_summary','get_case_status','get_case_slice','get_cases','get_case_filter_values','lookup_case_entities'})
        self.assertEqual(connection['config']['function_name_filter_list'], ','.join(server['tools']))
        self.assertEqual(server['tools'], server['function_name_filter_list'])
        self.assertEqual(server['access'], {'type': 'public'})

    def test_case_tools_only_and_identity(self):
        doc, rows = presets.load_presets()
        by_id = {p['id']: p for p in rows}
        for pid in ('lenny', 'case-assistant'):
            model = presets.desired_model(by_id[pid], doc['base_model'], doc['filter_ids'])
            self.assertIn('server:mcp:qdts', model['meta']['toolIds'])
            self.assertEqual(model['params']['system'], by_id[pid]['system'])
            self.assertEqual(model['params']['function_calling'], 'native')
            self.assertIn('user_context', model['meta']['filterIds'])
        ca = presets.desired_model(by_id['case-assistant'], doc['base_model'], doc['filter_ids'])
        self.assertEqual(ca['meta']['toolIds'], ['server:mcp:qdts', 'visuals_toolkit_v4'])
        self.assertEqual(by_id['case-assistant']['knowledge_bases'], [])
        self.assertFalse(ca['meta']['capabilities']['web_search'])
        self.assertFalse(ca['meta']['builtinTools']['knowledge'])
        cfg = json.loads((HERE / 'models/user-context.json').read_text())
        self.assertEqual(cfg['models']['case-assistant'], ['name', 'id'])
        self.assertTrue(ca['meta']['profile_image_url'].startswith('data:image/png;base64,'))

    def test_fixture_focused_routing_and_state_discovery(self):
        names = next(s for s in mcp_servers.load_servers() if s['id'] == 'qdts')['tools']
        tools = [{'function': {'name': 'qdts_' + name, 'parameters': {}}} for name in names]
        cases = {
            'STATUS': ('get_case_status', {'id': 'QDTS-26-000001'}),
            'SUMMARY': ('get_case_summary', {'id': 'QDTS-26-000001'}),
            'TASKS': ('get_case_slice', {'id': 'QDTS-26-000001', 'section': 'tasks', 'task_state': 'overdue'}),
            'BATCH': ('get_cases', {'ids': ['QDTS-26-000001', 'QDTS-26-000002'], 'view': 'status'}),
            'FILTERS': ('get_case_filter_values', {'field': 'state'}),
            'LOOKUP': ('lookup_case_entities', {'kind': 'people', 'query': 'Fixture One'}),
            'BLOCKED': ('get_case_filter_values', {'field': 'state'}),
            'PEOPLE': ('get_case_slice', {'id': 'QDTS-26-000001', 'section': 'people'}),
            'PRIVATE': ('get_case_notes', {'id': 'QDTS-26-000001'}),
        }
        for marker, (name, args) in cases.items():
            with self.subTest(marker=marker):
                messages = [{'role': 'system', 'content': 'id: fixtureone untrusted closed_newest'},
                            {'role': 'user', 'content': 'TEST_' + marker}]
                response = cases_model.response({'messages': messages, 'tools': tools})
                call = response['tool_calls'][0]['function']
                self.assertEqual(call['name'], 'qdts_' + name)
                self.assertEqual(json.loads(call['arguments']), args)
                if marker == 'BLOCKED':
                    messages += [response, {'role': 'tool', 'content': json.dumps({'values': [{'value': 'Hold', 'count': 1}]})}]
                    call = cases_model.response({'messages': messages, 'tools': tools})['tool_calls'][0]['function']
                    self.assertEqual(call['name'], 'qdts_search_cases')
                    self.assertEqual(json.loads(call['arguments']), {'state': 'Hold'})
                    messages[-1]['content'] = json.dumps({'values': [{'value': 'Open', 'count': 1}]})
                    with self.assertRaisesRegex(AssertionError, 'Hold not discovered'):
                        cases_model.response({'messages': messages, 'tools': tools})

    def test_env_generation_private_idempotent_no_source_data_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'source'
            (src / 'qdts_cases').mkdir(parents=True)
            (src / 'Dockerfile.cases').write_text('FROM scratch\n')
            path = Path(tmp) / '.env'
            path.write_text('MAIL_SERVICE_SRC=/mail\nEMPLOYEE_DIRECTORY_SRC=/emp\nMAIL_PROVIDER=mock\nWEBUI_SECRET_KEY=synthetic\nMAIL_MCP_API_KEY=synthetic\n')
            out = io.StringIO()
            args = ['--env-file', str(path), '--cases-src', str(src)]
            with contextlib.redirect_stdout(out):
                self.assertEqual(init_env.run(args), 0)
                first = path.read_text()
                self.assertEqual(init_env.run(args), 0)
                self.assertEqual(path.read_text(), first)
            env = {k: v[1] for k, v in init_env.parse(first.splitlines()).items()}
            self.assertEqual(env['QDTS_CASES_SRC'], str(src))
            self.assertEqual(env['QDTS_CUSTOMER_NAMES'], 'plain')
            self.assertGreaterEqual(len(env['QDTS_MCP_API_KEY']), 32)
            self.assertNotIn(env['QDTS_MCP_API_KEY'], out.getvalue())
            self.assertNotIn('QDTS_DEVQDTS_DATA', env)
            self.assertNotIn('QDTS_ALIAS_KEY', env)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


if __name__ == '__main__':
    unittest.main()
