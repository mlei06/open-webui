"""Standard-library tests for managed extension installation and retirement."""
import contextlib
from copy import deepcopy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bootstrap'))
import extensions


class ExtensionsTests(unittest.TestCase):
    def test_create_retire_preserve_and_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'tool.py').write_text('class Tools: pass\n')
            (root / 'function.py').write_text('class Filter: pass\n')
            doc = {'managed': [
                {'kind': 'tools', 'id': 'visual', 'name': 'Visual', 'file': 'tool.py', 'access_grants': []},
                {'kind': 'functions', 'id': 'ui', 'name': 'UI', 'file': 'function.py', 'active': True, 'global_filter': False}],
                'retired': {'tools': ['old'], 'functions': ['trace']}}
            db = {'tools': {'old': {'id': 'old'}, 'personal': {'id': 'personal'}},
                  'functions': {'trace': {'id': 'trace'}, 'readable_generation_info': {'id': 'readable_generation_info'}}}
            writes = []
            def api(base, method, path, token, body=None):
                parts = path.strip('/').split('/'); kind = parts[2]
                if method == 'GET' and len(parts) == 3:return deepcopy(list(db[kind].values()))
                if method == 'POST' and parts[-1] == 'create':
                    writes.append(path);db[kind][body['id']] = {**body, 'is_active': False, 'is_global': False};return body
                id = parts[4]
                if method == 'GET':return {} if parts[-1] == 'valves' else deepcopy(db[kind][id])
                writes.append(path)
                if method == 'DELETE':del db[kind][id];return True
                if parts[-1] == 'update':db[kind][id].update(body)
                else:
                    flag = 'is_global' if parts[-1] == 'global' else 'is_active'
                    db[kind][id][flag] = not db[kind][id][flag]
                return deepcopy(db[kind][id])
            with patch.object(extensions, 'MICHAEL_DIR', root), patch.object(extensions, 'call', api), patch.object(extensions, 'backup') as backup, contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(extensions.reconcile('', '', doc))
                self.assertEqual(writes, [])
                self.assertTrue(extensions.reconcile('', '', doc, apply=True))
                self.assertEqual(backup.call_count, 2)
                self.assertTrue(db['functions']['ui']['is_active'])
                self.assertFalse(db['functions']['ui']['is_global'])
                self.assertIn('personal', db['tools'])
                self.assertIn('readable_generation_info', db['functions'])
                count = len(writes)
                self.assertFalse(extensions.reconcile('', '', doc, apply=True))
                self.assertEqual(count, len(writes))

    def test_manifest_sources_compile_and_retired_do_not_overlap(self):
        doc = extensions.load_manifest()
        self.assertEqual({x['id'] for x in doc['managed']}, {'visuals_toolkit_v4', 'delegate_agents', 'interface_toggles', 'collapsed_sidebar_pinned_models'})


if __name__ == '__main__':unittest.main()
