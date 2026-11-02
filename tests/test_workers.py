"""Deterministic regression at durable delegation -> authenticated mobile bridge seam."""
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hermes_bridge.app import Bridge
from hermes_bridge.config import Config


class WorkersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.db = self.home / 'state.db'
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('CREATE TABLE async_delegations (delegation_id TEXT, parent_session_id TEXT, state TEXT, dispatched_at REAL, updated_at REAL, completed_at REAL, delivery_state TEXT, task_json TEXT, result_json TEXT)')
            c.execute('INSERT INTO async_delegations VALUES (?,?,?,?,?,?,?,?,?)', ('deleg_fixture', 'parent-session', 'running', 10, 10, None, 'pending', json.dumps({'is_batch': True, 'goals': ['SECRET TASK', 'SECRET TASK 2']}), None))
        self.db.chmod(0o600)
        self.cfg = Config('127.0.0.1', 0, 'http://localhost', self.home/'token', self.home/'key', hermes_home=self.home, allowed_profiles=('default',))
        self.auth = patch.object(Config, 'bridge_key', return_value='synthetic-worker-key')
        self.auth.start()
        self.addCleanup(self.auth.stop)

    def request(self, cfg=None, profile='default', headers=None):
        return Bridge(cfg or self.cfg).handle('GET', f'/v1/profiles/{profile}/workers', {}, headers if headers is not None else {'authorization': 'Bearer synthetic-worker-key'}, b'')

    def test_children_outlive_parent_and_terminal_result_replays_after_restart(self):
        # Parent HTTP request ended. The only source is the still-running durable ledger.
        cfg = self.cfg
        object.__setattr__(cfg, 'worker_observability', True)
        code, body = self.request(cfg)
        self.assertEqual(code, 200)
        self.assertEqual([w['state'] for w in body['workers']], ['running', 'running'])
        self.assertEqual(body['workers'][0]['parentSessionId'], 'parent-session')
        self.assertNotIn('SECRET', json.dumps(body))
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('UPDATE async_delegations SET state=?, updated_at=?, completed_at=?, result_json=?', ('completed', 20, 20, json.dumps({'results': [{'task_index': 0, 'status': 'completed', 'summary': 'SECRET RESULT'}, {'task_index': 1, 'status': 'failed', 'error': 'SECRET ERROR'}]})))
        _, done = self.request(cfg)  # a new bridge object: no in-memory event dependency
        self.assertEqual([w['state'] for w in done['workers']], ['completed', 'failed'])
        self.assertEqual(done['workers'][0]['review'], 'unknown')
        self.assertEqual(done, self.request(cfg)[1])
        self.assertNotIn('SECRET', json.dumps(done))
        self.assertIsNone(done['workers'][0]['completedAt'], 'unit join time is not an individual child completion time')

    def test_opt_in_is_strict_and_default_disabled(self):
        from hermes_bridge.config import load, ConfigError
        base = {'HERMES_BRIDGE_KEY_FILE': '/unused/key', 'HERMES_BRIDGE_DASHBOARD_TOKEN_FILE': '/unused/token'}
        self.assertEqual(self.request()[1]['capability'], 'disabled')
        self.assertTrue(load(environ={**base, 'HERMES_BRIDGE_WORKER_OBSERVABILITY': 'true'}).worker_observability)
        for value in ('yes', '1', '', 'TRUE'):
            with self.assertRaises(ConfigError):
                load(environ={**base, 'HERMES_BRIDGE_WORKER_OBSERVABILITY': value})

    def test_scope_and_auth_precede_disk_access(self):
        with patch('hermes_bridge.workers.snapshot', side_effect=AssertionError('disk access')):
            self.assertEqual(self.request(profile='private')[0], 403)
            self.assertEqual(self.request(headers={})[0], 401)

    def test_authorized_request_reaches_snapshot_path_before_disk(self):
        # Capability on + correct key/scope means the adapter actually reads the
        # store: an early-return that swallowed the read could not be detected
        # by the negative-only test above.
        object.__setattr__(self.cfg, 'worker_observability', True)
        with patch('hermes_bridge.workers.snapshot', return_value={
            'capability': 'ledger-v1', 'workers': [], 'hasMore': False, 'push': 'blocked'
        }) as mock:
            code, body = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(body, {'capability': 'ledger-v1', 'workers': [], 'hasMore': False, 'push': 'blocked'})
        mock.assert_called_once_with(self.home, 'default')

    def test_partial_child_result_does_not_settle_sibling(self):
        object.__setattr__(self.cfg, 'worker_observability', True)
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('UPDATE async_delegations SET result_json=?', (json.dumps({'partial': True, 'results': [{'task_index': 0, 'status': 'interrupted'}]}),))
        _, body = self.request()
        self.assertEqual([w['state'] for w in body['workers']], ['interrupted', 'running'])

    def test_partial_terminal_notification_id_does_not_change_when_unit_finishes(self):
        object.__setattr__(self.cfg, 'worker_observability', True)
        result = json.dumps({'partial': True, 'results': [{'task_index': 0, 'status': 'completed'}]})
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('UPDATE async_delegations SET result_json=?', (result,))
        before = self.request()[1]['workers'][0]['eventId']
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('UPDATE async_delegations SET state=?, completed_at=?', ('completed', 30))
        after = self.request()[1]['workers'][0]['eventId']
        self.assertEqual(before, after, 'unit finalization must not re-notify an already completed child')

    def test_unknown_states_missing_child_and_malformed_metadata_never_invent_success(self):
        object.__setattr__(self.cfg, 'worker_observability', True)
        for state, result, expected in [
            # No results at all: missing-or-invalid child state -> unknown.
            ('completed', {}, 'unknown'),
            ('future-state', {}, 'unknown'),
            ('interrupted', {}, 'interrupted'),
            # Status field missing from a non-empty entry: do not inherit the
            # delegation's state, including 'completed' which would otherwise invent
            # success.
            ('completed', {'results': [{'task_index': 0}]}, 'unknown'),
            ('completed', {'results': [{'task_index': 0, 'summary': 'SECRET'}]}, 'unknown'),
            ('running', {'results': [{'task_index': 0}]}, 'unknown'),
            # Existing malformed-status case and the finalizing override.
            ('running', {'results': [{'task_index': 0, 'status': ['invalid']}]}, 'unknown'),
            ('finalizing', {}, 'running'),
        ]:
            with self.subTest(state=state, result=result):
                with closing(sqlite3.connect(self.db)) as c, c:
                    c.execute('UPDATE async_delegations SET state=?, task_json=?, result_json=?', (state, json.dumps({'is_batch': False}), json.dumps(result)))
                code, body = self.request()
                self.assertEqual(code, 200)
                self.assertEqual(body['workers'][0]['state'], expected)
                if expected == 'unknown':
                    self.assertIn('unknown', body['workers'][0]['activity'].lower())
                # No invented success ever carries a real completion event id.
                entries = result.get('results') if isinstance(result.get('results'), list) else []
                first = entries[0] if entries else {}
                if state == 'completed' and 'status' not in first:
                    self.assertIsNone(body['workers'][0]['eventId'], 'missing child status must not produce a completion event id')

    def test_truncation_is_explicit_for_task_indexes_beyond_snapshot_bound(self):
        self.cfg = replace(self.cfg, worker_observability=True)
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('UPDATE async_delegations SET task_json=?', (json.dumps({'task_indexes': list(range(101))}),))
        code, body = self.request()
        self.assertEqual(code, 200)
        self.assertTrue(body['hasMore'])

    def test_missing_unsafe_or_incompatible_store_is_not_idle(self):
        object.__setattr__(self.cfg, 'worker_observability', True)
        self.db.chmod(0o644)
        self.assertEqual(self.request()[1]['capability'], 'unavailable')
        self.db.chmod(0o600)
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('DROP TABLE async_delegations')
        self.assertEqual(self.request()[1]['capability'], 'unavailable')
        self.db.unlink()
        self.assertEqual(self.request()[1]['capability'], 'unavailable')
        self.assertFalse(self.db.exists())
