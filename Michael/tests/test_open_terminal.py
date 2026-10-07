"""Synthetic terminal registration contracts; no Docker or real credentials."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bootstrap'))
import open_terminal as ot

class TerminalTests(unittest.TestCase):
    def test_private_default_and_secret_required(self):
        with self.assertRaises(ot.ApiError):ot.desired_connections([], {})
        target=ot.desired_connections([], {'OPEN_TERMINAL_API_KEY':'synthetic'})[0]
        self.assertEqual(target['config']['access_grants'], [])
        self.assertFalse(target['forward_cookies'])
        self.assertEqual(target['url'], 'http://open-terminal:8000')

    def test_preserve_other_connections_and_custom_access(self):
        current=[{'id':'other','key':'other-key'}, {'id':'open-terminal','config':{'access_grants':[{'principal_id':'group-example'}], 'chat_uploads':'filesystem'}}]
        before=copy.deepcopy(current)
        desired=ot.desired_connections(current, {'OPEN_TERMINAL_API_KEY':'synthetic'})
        self.assertEqual(current,before)
        self.assertEqual(desired[0],current[0])
        self.assertEqual(desired[1]['config'],current[1]['config'])
        self.assertEqual(ot.desired_connections(desired,{'OPEN_TERMINAL_API_KEY':'synthetic'}),desired)

    def test_duplicate_id_rejected(self):
        with self.assertRaises(ot.ApiError):ot.desired_connections([{'id':'open-terminal'}]*2,{'OPEN_TERMINAL_API_KEY':'synthetic'})

    def test_check_does_not_write(self):
        with patch.object(ot,'call',side_effect=[{'TERMINAL_SERVER_CONNECTIONS':[]},{'status':True,'type':'terminal'}]) as api, patch.object(ot,'backup_config') as backup:
            self.assertTrue(ot.reconcile('base','token',{'OPEN_TERMINAL_API_KEY':'synthetic'}))
            self.assertEqual([c.args[2] for c in api.call_args_list],[ot.API_PATH,ot.API_PATH+'/verify'])
            backup.assert_not_called()

    def test_concurrent_edit_prevents_write(self):
        with patch.object(ot,'call',side_effect=[{'TERMINAL_SERVER_CONNECTIONS':[]},{'status':True,'type':'terminal'},{'TERMINAL_SERVER_CONNECTIONS':[{'id':'new'}]}]), patch.object(ot,'backup_config') as backup:
            with self.assertRaises(ot.ApiError):ot.reconcile('base','token',{'OPEN_TERMINAL_API_KEY':'synthetic'},True)
            backup.assert_not_called()

    def test_update_backup_and_readback(self):
        env={'OPEN_TERMINAL_API_KEY':'synthetic'}; desired=ot.desired_connections([],env)
        with patch.object(ot,'call',side_effect=[{'TERMINAL_SERVER_CONNECTIONS':[]},{'status':True,'type':'terminal'},{'TERMINAL_SERVER_CONNECTIONS':[]},{},{'TERMINAL_SERVER_CONNECTIONS':desired}]) as api, patch.object(ot,'backup_config') as backup:
            self.assertTrue(ot.reconcile('base','token',env,True))
            backup.assert_called_once()
            self.assertEqual(api.call_args_list[3].args[4],{'TERMINAL_SERVER_CONNECTIONS':desired})

    def test_verification_failure_prevents_write(self):
        with patch.object(ot,'call',side_effect=[{'TERMINAL_SERVER_CONNECTIONS':[]},{'status':False}]) as api:
            with self.assertRaises(ot.ApiError):ot.reconcile('base','token',{'OPEN_TERMINAL_API_KEY':'synthetic'},True)
            self.assertEqual(api.call_count,2)

class AccessTests(unittest.TestCase):
    ENV = {'OPEN_TERMINAL_API_KEY': 'synthetic'}
    PUBLIC = {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}

    def grants(self, connections):
        return next(c for c in connections if c['id'] == 'open-terminal')['config']['access_grants']

    def test_keep_is_the_default_and_never_widens(self):
        registered = ot.desired_connections([], self.ENV)
        self.assertEqual(self.grants(registered), [])
        self.assertEqual(ot.desired_connections(registered, self.ENV, 'keep'), registered)

    def test_all_adds_the_public_grant_once_and_keeps_other_grants(self):
        group = {'principal_type': 'group', 'principal_id': 'g1', 'permission': 'read'}
        current = [{'id': 'open-terminal', 'config': {'access_grants': [group], 'chat_uploads': 'filesystem'}}]
        shared = ot.desired_connections(current, self.ENV, 'all')
        self.assertEqual(self.grants(shared), [group, self.PUBLIC])
        self.assertEqual(shared[0]['config']['chat_uploads'], 'filesystem')
        again = ot.desired_connections(shared, self.ENV, 'all')
        self.assertEqual(self.grants(again), [group, self.PUBLIC])  # idempotent
        enriched = copy.deepcopy(shared)  # the API adds database fields to grant rows
        enriched[0]['config']['access_grants'][1]['id'] = 'row-1'
        self.assertEqual(len(self.grants(ot.desired_connections(enriched, self.ENV, 'all'))), 2)

    def test_admin_removes_every_grant(self):
        shared = ot.desired_connections([], self.ENV, 'all')
        self.assertEqual(self.grants(ot.desired_connections(shared, self.ENV, 'admin')), [])

    def test_unknown_access_mode_is_rejected(self):
        with self.assertRaises(ot.ApiError):
            ot.desired_connections([], self.ENV, 'everyone')

    def api(self, current_calls):
        return patch.object(ot, 'call', side_effect=current_calls)

    def test_sharing_requires_the_file_api_to_deny_the_terminal_environment(self):
        private = ot.desired_connections([], self.ENV)
        calls = [{'TERMINAL_SERVER_CONNECTIONS': private}, {'status': True, 'type': 'terminal'}]
        with self.api(calls) as api, patch.object(ot, 'probe_confinement', return_value=200), patch.object(ot, 'backup_config') as backup:
            with self.assertRaises(ot.ApiError) as raised:
                ot.reconcile('base', 'token', self.ENV, True, 'all')
            self.assertIn('did not deny /proc/1/environ', str(raised.exception))
            self.assertEqual(api.call_count, 2)  # nothing was written
            backup.assert_not_called()

    def test_sharing_a_confined_terminal_writes_the_public_grant_and_reads_it_back(self):
        private = ot.desired_connections([], self.ENV)
        shared = ot.desired_connections(private, self.ENV, 'all')
        saved = copy.deepcopy(shared)
        saved[0]['config']['access_grants'][0]['id'] = 'row-1'  # the API enriches rows
        calls = [{'TERMINAL_SERVER_CONNECTIONS': private}, {'status': True, 'type': 'terminal'},
                 {'TERMINAL_SERVER_CONNECTIONS': private}, {}, {'TERMINAL_SERVER_CONNECTIONS': saved}]
        with self.api(calls) as api, patch.object(ot, 'probe_confinement', return_value=403), patch.object(ot, 'backup_config') as backup:
            self.assertTrue(ot.reconcile('base', 'token', self.ENV, True, 'all'))
            backup.assert_called_once()
            self.assertEqual(self.grants(api.call_args_list[3].args[4]['TERMINAL_SERVER_CONNECTIONS']), [self.PUBLIC])

    def test_check_mode_probes_but_never_writes(self):
        private = ot.desired_connections([], self.ENV)
        calls = [{'TERMINAL_SERVER_CONNECTIONS': private}, {'status': True, 'type': 'terminal'}]
        with self.api(calls) as api, patch.object(ot, 'probe_confinement', return_value=403) as probe, patch.object(ot, 'backup_config') as backup:
            self.assertTrue(ot.reconcile('base', 'token', self.ENV, False, 'all'))
            probe.assert_called_once()
            self.assertEqual(api.call_count, 2)
            backup.assert_not_called()

    def test_an_already_shared_terminal_needs_no_probe_or_write(self):
        shared = ot.desired_connections(ot.desired_connections([], self.ENV), self.ENV, 'all')
        calls = [{'TERMINAL_SERVER_CONNECTIONS': shared}, {'status': True, 'type': 'terminal'}]
        with self.api(calls), patch.object(ot, 'probe_confinement') as probe:
            self.assertFalse(ot.reconcile('base', 'token', self.ENV, True, 'all'))
            probe.assert_not_called()

    def test_a_new_connection_is_registered_private_then_shared_after_the_probe(self):
        private = ot.desired_connections([], self.ENV)
        shared = ot.desired_connections(private, self.ENV, 'all')
        calls = [{'TERMINAL_SERVER_CONNECTIONS': []}, {'status': True, 'type': 'terminal'},
                 {'TERMINAL_SERVER_CONNECTIONS': []}, {}, {'TERMINAL_SERVER_CONNECTIONS': private},
                 {'TERMINAL_SERVER_CONNECTIONS': private}, {'TERMINAL_SERVER_CONNECTIONS': private}, {}, {'TERMINAL_SERVER_CONNECTIONS': shared}]
        with self.api(calls) as api, patch.object(ot, 'probe_confinement', return_value=403), patch.object(ot, 'backup_config'):
            self.assertTrue(ot.reconcile('base', 'token', self.ENV, True, 'all'))
            writes = [c.args[4] for c in api.call_args_list if c.args[1] == 'POST' and c.args[2] == ot.API_PATH]
            self.assertEqual(self.grants(writes[0]['TERMINAL_SERVER_CONNECTIONS']), [])
            self.assertEqual(self.grants(writes[1]['TERMINAL_SERVER_CONNECTIONS']), [self.PUBLIC])

    def test_probe_reports_the_http_status_and_connection_failures(self):
        import io
        import urllib.error
        error = urllib.error.HTTPError('u', 403, 'Forbidden', {}, io.BytesIO(b''))
        with patch.object(ot.urllib.request, 'urlopen', side_effect=error):
            self.assertEqual(ot.probe_confinement('http://x', 't'), 403)
        with patch.object(ot.urllib.request, 'urlopen', side_effect=OSError('down')):
            with self.assertRaises(ot.ApiError):
                ot.probe_confinement('http://x', 't')


if __name__=='__main__':unittest.main()
