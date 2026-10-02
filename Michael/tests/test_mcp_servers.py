"""Unit tests for bootstrap/mcp_servers.py (standard library only; no Open WebUI needed).

  python3 Michael/tests/test_mcp_servers.py
"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bootstrap'))
import mcp_servers as m  # noqa: E402

ENV = {'TRANSLATOR_GATEWAY_URL': 'http://gw.test/mcp', 'TRANSLATOR_API_KEY': 'k-translator'}
BY_ID = {s['id']: s for s in m.load_servers()}


def want(sid, env=ENV, groups=None):
    s = BY_ID[sid]
    return m.desired_connection(s, m.resolve(s, env), groups or {})


def write_doc(tmp, doc):
    path = Path(tmp) / 'mcp.json'
    path.write_text(json.dumps(doc))
    return path


class InventoryTests(unittest.TestCase):
    def test_shipped_inventory_is_valid(self):
        self.assertEqual(set(BY_ID), {'doctranslator', 'employee_directory', 'mail', 'employee_directory_write'})
        self.assertFalse(BY_ID['employee_directory_write']['enabled'])
        self.assertEqual(BY_ID['employee_directory_write']['access'], {'type': 'admin'})

    def test_schema_rejects_bad_documents(self):
        import tempfile

        base = json.loads(m.MCP_JSON.read_text())
        cases = []
        d = copy.deepcopy(base); d['servers'][0]['bogus'] = 1; cases.append(d)
        d = copy.deepcopy(base); d['servers'][0]['id'] = 'Bad-Id'; cases.append(d)
        d = copy.deepcopy(base); d['servers'][0]['auth'] = {'type': 'bearer'}; cases.append(d)
        d = copy.deepcopy(base); d['servers'][0]['type'] = 'mcp'; d['servers'][0]['transport'] = 'openapi-http'; cases.append(d)
        d = copy.deepcopy(base); d['servers'][1]['access'] = {'type': 'groups'}; cases.append(d)
        d = copy.deepcopy(base); d['servers'][0]['env'][1]['secret'] = False; cases.append(d)
        d = copy.deepcopy(base); d['servers'][0]['env'][1]['default'] = 'x'; cases.append(d)
        d = copy.deepcopy(base); d['servers'][1]['function_name_filter_list'] = ['search_employees']; cases.append(d)
        d = copy.deepcopy(base); d['servers'].append(copy.deepcopy(d['servers'][0])); cases.append(d)
        d = copy.deepcopy(base); d['schemaVersion'] = 2; cases.append(d)
        for doc in cases:
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(m.ConfigError, msg=json.dumps(doc)[:80]):
                m.load_servers(write_doc(tmp, doc))

    def test_filter_suffix_semantics(self):
        self.assertTrue(m.name_allowed('search_employees', ['search_employees']))
        self.assertFalse(m.name_allowed('create_employee', ['search_employees']))
        self.assertFalse(m.name_allowed('search_employees', ['!employees']))


class ResolveTests(unittest.TestCase):
    def test_missing_required_variable_is_named(self):
        with self.assertRaisesRegex(m.ConfigError, 'TRANSLATOR_API_KEY'):
            m.resolve(BY_ID['doctranslator'], {'TRANSLATOR_GATEWAY_URL': 'http://gw.test/mcp', 'TRANSLATOR_API_KEY': '  '})

    def test_default_used_and_empty_counts_as_unset(self):
        values = m.resolve(BY_ID['employee_directory'], {'EMPLOYEE_DIRECTORY_MCP_URL': ''})
        self.assertEqual(values['EMPLOYEE_DIRECTORY_MCP_URL'], 'http://employee-directory:8000/mcp')

    def test_non_http_url_rejected(self):
        with self.assertRaisesRegex(m.ConfigError, 'http'):
            want('doctranslator', {**ENV, 'TRANSLATOR_GATEWAY_URL': 'ftp://x'})


class ConnectionTests(unittest.TestCase):
    def test_translator_connection(self):
        c = want('doctranslator')
        self.assertEqual((c['type'], c['auth_type'], c['key'], c['url']), ('mcp', 'bearer', 'k-translator', 'http://gw.test/mcp'))
        self.assertEqual(c['config']['function_name_filter_list'], 'translation_capabilities,get_translation_status,cancel_translation')
        self.assertEqual(c['config']['access_grants'], [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}])
        self.assertEqual(c['info']['id'], 'doctranslator')
        self.assertEqual(c['managed_by'], m.MANAGED_BY)

    def test_optional_bearer_key(self):
        self.assertEqual((want('employee_directory')['auth_type'], want('employee_directory')['key']), ('none', ''))
        c = want('employee_directory', {'EMPLOYEE_MCP_API_KEY': 'k2'})
        self.assertEqual((c['auth_type'], c['key']), ('bearer', 'k2'))

    def test_mail_connection_sends_identity_headers_and_key(self):
        c = want('mail', {'MAIL_MCP_API_KEY': 'k-mail'})
        self.assertEqual((c['url'], c['auth_type'], c['key']), ('http://mail-service:8000/mcp', 'bearer', 'k-mail'))
        self.assertEqual(c['headers'], {'X-User-Email': '{{USER_EMAIL}}', 'X-Chat-Id': '{{CHAT_ID}}', 'X-Message-Id': '{{MESSAGE_ID}}'})
        self.assertEqual(c['config']['function_name_filter_list'], 'create_draft,update_draft,get_draft,list_drafts,discard_draft')
        self.assertNotIn('send', ','.join(BY_ID['mail']['tools']))
        with self.assertRaisesRegex(m.ConfigError, 'MAIL_MCP_API_KEY'):
            want('mail', {})

    def test_servers_without_headers_send_none(self):
        self.assertIsNone(want('doctranslator')['headers'])

    def test_admin_access_has_no_grants(self):
        c = want('employee_directory_write', {'EMPLOYEE_WRITE_MCP_API_KEY': 'k3'})
        self.assertEqual(c['config']['access_grants'], [])
        self.assertFalse(c['config']['enable'])

    def test_group_access_resolves_names(self):
        s = copy.deepcopy(BY_ID['employee_directory'])
        s['access'] = {'type': 'groups', 'groups': ['Staff']}
        c = m.desired_connection(s, m.resolve(s, {}), {'Staff': 'gid-1'})
        self.assertEqual(c['config']['access_grants'], [{'principal_type': 'group', 'principal_id': 'gid-1', 'permission': 'read'}])
        with self.assertRaisesRegex(m.ConfigError, 'Staff'):
            m.desired_connection(s, m.resolve(s, {}), {})


class PlanTests(unittest.TestCase):
    def test_add_then_unchanged(self):
        w = [want('doctranslator'), want('employee_directory')]
        first, actions = m.plan([], w, False, {'doctranslator', 'employee_directory'})
        self.assertEqual([a for _, a in actions], ['added', 'added'])
        again, actions = m.plan(first, w, False, {'doctranslator', 'employee_directory'})
        self.assertEqual(again, first)
        self.assertEqual([a for _, a in actions], ['unchanged', 'unchanged'])

    def test_unmanaged_connection_untouched_and_extras_kept(self):
        manual = {'type': 'openapi', 'url': 'http://x', 'path': 'openapi.json', 'auth_type': 'none', 'key': '',
                  'config': {'enable': True}, 'info': {'id': 'manual', 'name': 'M'}}
        old = want('doctranslator')
        old = {**old, 'forward_cookies': True, 'config': {**old['config'], 'extra': 1}, 'url': 'http://stale'}
        out, actions = m.plan([manual, old], [want('doctranslator')], True, {'doctranslator'})
        self.assertEqual(out[0], manual)
        self.assertEqual(out[1]['url'], 'http://gw.test/mcp')
        self.assertTrue(out[1]['forward_cookies'] and out[1]['config']['extra'] == 1)
        self.assertEqual(actions, [('doctranslator', 'updated')])

    def test_adopts_unmanaged_connection_with_same_id(self):
        old = {k: v for k, v in want('doctranslator').items() if k != 'managed_by'}
        out, actions = m.plan([old], [want('doctranslator')], False, set())
        self.assertEqual(actions, [('doctranslator', 'updated')])
        self.assertEqual(out[0]['managed_by'], m.MANAGED_BY)

    def test_prune_only_managed_and_undeclared(self):
        stale = {**want('employee_directory')}
        manual = {'url': 'http://x', 'info': {'id': 'manual'}, 'config': {}}
        w = want('doctranslator')
        out, actions = m.plan([stale, manual], [w], False, {'doctranslator'})
        self.assertEqual(len(out), 3)
        out, actions = m.plan([stale, manual], [w], True, {'doctranslator'})
        self.assertEqual([c['info']['id'] for c in out], ['manual', 'doctranslator'])
        self.assertIn(('employee_directory', 'pruned'), actions)


class VerifyTests(unittest.TestCase):
    def test_tool_names(self):
        self.assertEqual(m.tool_names({'specs': [{'name': 'a'}, {'name': 'b'}]}, 'mcp'), {'a', 'b'})
        spec = {'paths': {'/x': {'get': {'operationId': 'op1'}, 'parameters': []}}}
        self.assertEqual(m.tool_names(spec, 'openapi'), {'op1'})


if __name__ == '__main__':
    unittest.main()
