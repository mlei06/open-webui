"""Unit tests for functions/audit_log.py (needs pydantic, so run them where Open WebUI runs).

  docker cp Michael/functions/audit_log.py <container>:/tmp/audit_log.py
  docker exec -i <container> python3 - < Michael/tests/test_audit_log.py
"""

import asyncio
import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

PATH = os.environ.get('AUDIT_LOG_SOURCE', '/tmp/audit_log.py')
spec = importlib.util.spec_from_file_location('audit_log', PATH)
al = importlib.util.module_from_spec(spec)
spec.loader.exec_module(al)

EVENT = {
    'id': 'evt-1',
    'event': 'user.role_updated',
    'created_at': 1790909605,
    'source': 'api',
    'instance_id': 'inst-1',
    'actor': {'id': 'admin-1', 'name': 'Throwaway Admin', 'email': 'admin@example.com', 'role': 'admin'},
    'subject': {'type': 'user', 'id': 'user-1'},
    'data': {'role': 'user'},
}


def deliver(fn, event):
    asyncio.run(fn.event(event, __event_name__=event['event']))


class AuditLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fn = al.Event()
        self.fn.valves = self.fn.Valves(directory=self.tmp.name)

    def lines(self):
        return [json.loads(x) for p in sorted(Path(self.tmp.name).glob('*.jsonl')) for x in p.read_text().splitlines()]

    def test_matches(self):
        self.assertTrue(al.matches('auth.api_key.created', 'auth.*'))
        self.assertTrue(al.matches('user.created', 'config.*, user.created'))
        self.assertTrue(al.matches('anything.at.all', '*'))
        self.assertFalse(al.matches('chat.created', al.DEFAULT_EVENTS))
        self.assertFalse(al.matches('message.created', al.DEFAULT_EVENTS))
        self.assertTrue(al.matches('function.disable_started', al.DEFAULT_EVENTS))

    def test_record_keeps_metadata_and_drops_names(self):
        rec = al.build_record(EVENT)
        self.assertEqual(rec['actor'], {'id': 'admin-1', 'email': 'admin@example.com', 'role': 'admin'})
        self.assertNotIn('name', rec['actor'])
        self.assertEqual(rec['ts'], '2026-10-02T02:53:25+00:00')
        self.assertEqual(al.build_record(EVENT, include_email=False)['actor'], {'id': 'admin-1', 'role': 'admin'})
        self.assertIsNone(al.build_record({**EVENT, 'actor': None})['actor'])

    def test_writes_one_line_per_matching_event(self):
        deliver(self.fn, EVENT)
        deliver(self.fn, {**EVENT, 'id': 'evt-2', 'event': 'chat.created'})  # not in the default selection
        rows = self.lines()
        self.assertEqual([r['id'] for r in rows], ['evt-1'])
        self.assertEqual(rows[0]['subject'], {'type': 'user', 'id': 'user-1'})
        self.assertEqual(oct(next(Path(self.tmp.name).glob('*.jsonl')).stat().st_mode & 0o777), '0o600')

    def test_valve_selects_events(self):
        self.fn.valves = self.fn.Valves(directory=self.tmp.name, events='chat.*')
        deliver(self.fn, {**EVENT, 'event': 'chat.created'})
        deliver(self.fn, EVENT)
        self.assertEqual([r['event'] for r in self.lines()], ['chat.created'])

    def test_unwritable_folder_does_not_raise(self):
        blocker = Path(self.tmp.name) / 'file'
        blocker.write_text('x')
        self.fn.valves = self.fn.Valves(directory=str(blocker / 'sub'))
        with self.assertLogs('michael.audit', level='ERROR'):
            deliver(self.fn, EVENT)

    def test_prune_by_file_date(self):
        for name in ('events-2026-01-01.jsonl', 'events-2026-09-30.jsonl', 'other.jsonl'):
            (Path(self.tmp.name) / name).write_text('{}\n')
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.assertEqual(al.prune(self.tmp.name, 30, now), 1)
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), ['events-2026-09-30.jsonl', 'other.jsonl'])

    def test_retention_runs_on_startup_only(self):
        old = Path(self.tmp.name) / 'events-2020-01-01.jsonl'
        old.write_text('{}\n')
        self.fn.valves = self.fn.Valves(directory=self.tmp.name, retention_days=30)
        deliver(self.fn, EVENT)
        self.assertTrue(old.exists())
        deliver(self.fn, {**EVENT, 'event': 'system.startup.completed', 'id': 'evt-3'})
        self.assertFalse(old.exists())


if __name__ == '__main__':
    unittest.main(argv=['audit_log'], verbosity=1)
