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

if __name__=='__main__':unittest.main()
