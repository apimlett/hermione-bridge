"""Opt-in read-only adapter for Hermes's existing async_delegations ledger.

Not a Hermes HTTP endpoint. Never imports/runs Hermes, claims deliveries, reads
transcripts, or exposes task/result free text. A ledger update is NOT a heartbeat.
"""
from __future__ import annotations
import json
import math
import os
import re
import sqlite3
import stat
from pathlib import Path

from .gateway import profile_home

_ID = re.compile(r'[A-Za-z0-9_-]{1,160}')
_STATES = {'running', 'completed', 'failed', 'cancelled', 'interrupted', 'unknown'}


def _id(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _number(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 else None


def _object(value):
    try:
        obj = json.loads(value or '{}')
        return obj if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        return {}


def snapshot(root: Path, profile: str) -> dict:
    unavailable = {'capability': 'unavailable', 'workers': [], 'hasMore': False, 'push': 'blocked'}
    root = root.expanduser().resolve()
    home = profile_home(root, profile)
    path = home / 'state.db'
    try:
        # No cross-profile symlink traversal. Never create a database or profile.
        if home.resolve() != home or path.is_symlink():
            return unavailable
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            return unavailable
        conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
        try:
            conn.execute('PRAGMA query_only=ON')
            rows = conn.execute('''SELECT delegation_id, parent_session_id, state,
                dispatched_at, updated_at, completed_at, delivery_state, task_json, result_json
                FROM async_delegations ORDER BY dispatched_at DESC, delegation_id LIMIT 101''').fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return unavailable
    workers = []
    has_more = len(rows) > 100
    for did, parent, state, started, updated, ended, delivery, task_raw, result_raw in rows[:100]:
        if not _id(did):
            continue
        task, result = _object(task_raw), _object(result_raw)
        indexes = task.get('task_indexes')
        if not isinstance(indexes, list):
            goals = task.get('goals')
            count = len(goals) if isinstance(goals, list) and goals else 1
            has_more |= bool(task.get('is_batch') and count > 100)
            indexes = list(range(min(count, 100))) if task.get('is_batch') else [0]
        indexes = sorted({i for i in indexes if type(i) is int and 0 <= i < 1000000})
        has_more |= len(indexes) > 100
        indexes = indexes[:100]
        entries = result.get('results') or [] if isinstance(result.get('results'), list) else [result]
        finished = {e.get('task_index', 0): e for e in entries if isinstance(e, dict) and type(e.get('task_index', 0)) is int}
        for i in indexes:
            entry = finished.get(i, {})
            recorded = entry.get('status') or state
            normalized = recorded if isinstance(recorded, str) and recorded in _STATES else 'unknown'
            if recorded == 'finalizing':
                normalized = 'running'
            # A unit can be completed while a child result is missing: no invented success.
            if state == 'completed' and not entry:
                normalized = 'unknown'
            wid = f'{did}:{i}'
            terminal = normalized != 'running'
            event = f'{wid}:{normalized}' if terminal else None
            workers.append({'id': wid, 'delegationId': did, 'taskIndex': i, 'profile': profile,
                'parentSessionId': _id(parent), 'parentRunId': None, 'parentJobId': None,
                'label': f'Delegated task {i + 1}', 'state': normalized, 'recordedState': normalized,
                'activity': 'Outcome unknown; attention required' if normalized == 'unknown' else 'Terminal result recorded' if terminal else 'Running at last ledger update; live activity unavailable',
                'startedAt': _number(started), 'updatedAt': _number(updated), 'completedAt': _number(ended) if len(indexes) == 1 else None,
                'liveness': 'unknown', 'review': 'unknown',
                'delivery': delivery if delivery in {'pending', 'claimed', 'delivered', 'dropped'} else 'unknown',
                'eventId': event})
    return {'capability': 'ledger-v1', 'workers': workers, 'hasMore': has_more, 'push': 'blocked'}
