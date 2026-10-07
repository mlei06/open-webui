"""bootstrap/accounts.py: sign-up closed, default role, and the default user permissions we rely on.

  uv run --no-project python Michael/tests/test_accounts.py
"""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bootstrap'))
import accounts  # noqa: E402


class Server:
    def __init__(self, permissions=None, config=None):
        self.permissions = permissions or {'workspace': {'knowledge': False, 'tools': True}, 'features': {'automations': False, 'web_search': True}}
        self.config = config or {'ENABLE_SIGNUP': False, 'DEFAULT_USER_ROLE': 'user', 'OTHER': 1}
        self.posts = []

    def call(self, base, method, path, token=None, body=None):
        if method == 'POST':
            self.posts.append((path, copy.deepcopy(body)))
            if path == accounts.PERMISSIONS:
                self.permissions = copy.deepcopy(body)
            else:
                self.config = copy.deepcopy(body)
            return {}
        return copy.deepcopy(self.permissions if path == accounts.PERMISSIONS else self.config)


def run(server, *argv):
    with patch.object(accounts, 'call', new=server.call), patch.object(accounts, 'get_token', new=lambda *a: 'tok'), \
            patch.object(accounts, 'load_env', new=lambda: {}):
        return accounts.run(list(argv))


class AccountsTests(unittest.TestCase):
    def test_everyone_may_schedule_automations_and_own_knowledge_bases(self):
        server = Server()
        self.assertEqual(run(server), 0)
        self.assertTrue(server.permissions['features']['automations'])
        self.assertTrue(server.permissions['workspace']['knowledge'])

    def test_only_the_declared_permissions_change(self):
        server = Server()
        before = copy.deepcopy(server.permissions)
        run(server)
        for section, keys in before.items():
            for key, value in keys.items():
                if (section, key) not in accounts.PERMISSIONS_WANT:
                    self.assertEqual(server.permissions[section][key], value, (section, key))

    def test_a_second_run_changes_nothing(self):
        server = Server()
        run(server)
        server.posts.clear()
        self.assertEqual(run(server), 0)
        self.assertEqual(server.posts, [])

    def test_check_reports_drift_and_writes_nothing(self):
        server = Server()
        self.assertEqual(run(server, '--check'), 1)
        self.assertEqual(server.posts, [])
        self.assertFalse(server.permissions['features']['automations'])

    def test_signup_and_default_role_are_still_enforced(self):
        server = Server(config={'ENABLE_SIGNUP': True, 'DEFAULT_USER_ROLE': 'admin', 'OTHER': 1})
        run(server)
        self.assertEqual((server.config['ENABLE_SIGNUP'], server.config['DEFAULT_USER_ROLE'], server.config['OTHER']), (False, 'user', 1))


if __name__ == '__main__':
    unittest.main()
