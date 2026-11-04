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
# Terminal states are known outcomes; an unknown state must not advertise a
# completion event id to the client.
_TERMINAL_STATES = {'completed', 'failed', 'cancelled', 'interrupted'}


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
            # An entry that exists but carries no status field must not inherit the
            # delegation's state: a `completed` delegation with a child record whose
            # status is missing would otherwise report success and emit a completion
            # event id. Treat the absence as unknown and never invent a terminal.
            has_status_field = isinstance(entry, dict) and 'status' in entry
            if has_status_field:
                status_value = entry.get('status')
                if status_value == 'finalizing':
                    normalized = 'running'
                elif isinstance(status_value, str) and status_value in _STATES:
                    normalized = status_value
                else:
                    normalized = 'unknown'
            elif entry:
                # Entry exists but status field is absent: do not trust the parent.
                normalized = 'unknown'
            else:
                # No per-child entry: fall back to the delegation state if it is
                # known; otherwise unknown. An entry-less `completed` delegation
                # cannot claim a child outcome on its own. `finalizing` is a
                # transitional delegation state and is reported as `running` so the
                # client never treats a still-finalizing child as terminal.
                if state == 'finalizing':
                    normalized = 'running'
                elif state == 'completed' or state not in _STATES:
                    normalized = 'unknown'
                else:
                    normalized = state
            wid = f'{did}:{i}'
            # `unknown` is not a verified terminal outcome: do not emit a
            # notification-bearing event id and do not advertise a completion
            # to the client. Only known terminal states get an event id.
            terminal = normalized in _TERMINAL_STATES
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
