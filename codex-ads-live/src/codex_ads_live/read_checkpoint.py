"""Atomic synthetic read checkpoints. Real opaque cursors are not stored here."""
from dataclasses import asdict
import re
from .journal import Journal, canonical
from .read_adapter import ReadPlan, ReadSession, MockTransport, normalize_row, positive_integer, row_identity
from .validation import fail, object_only


def synthetic(value):
    if type(value) is not str or not re.fullmatch(r'SIM_[A-Z0-9_]{1,60}', value): fail('SYNTHETIC_CHECKPOINT_ONLY')


def validate_checkpoint(value):
    keys = {'schema', 'plan', 'rows', 'cursor', 'seen_cursors', 'pages', 'state', 'last_error'}
    object_only(value, keys, keys)
    if value['schema'] != 'synthetic-read-v1': fail('CHECKPOINT_SCHEMA')
    p = value['plan']
    object_only(p, ReadPlan.__dataclass_fields__, ReadPlan.__dataclass_fields__)
    plan = ReadPlan(**p)
    for key in ('object_id', 'account_id', 'page_id'):
        if p[key] is not None: synthetic(p[key])
    if value['state'] not in ('NOT_RUN', 'GAP', 'COMPLETE', 'EMPTY', 'ERROR'): fail('CHECKPOINT_STATE')
    if type(value['pages']) is not int or not 0 <= value['pages'] <= 1000: fail('CHECKPOINT_PAGE_BUDGET')
    if value['last_error'] is not None and (type(value['last_error']) is not str or not re.fullmatch(r'[A-Z_]{1,80}', value['last_error'])):
        fail('CHECKPOINT_ERROR_CODE')
    rows = value['rows']
    if type(rows) is not list or len(rows) > 1000: fail('CHECKPOINT_ROW_BUDGET')
    seen = set()
    for row in rows:
        if normalize_row(plan, row) != row: fail('CHECKPOINT_UNSANITIZED_ROW')
        for key, field in row.items():
            if key in ('id', 'account_id', 'ad_id', 'campaign_id', 'adset_id'): synthetic(field.removeprefix('act_'))
            if key in ('from', 'video'): synthetic(field['id'])
        key = row_identity(plan,row)
        if key in seen: fail('CHECKPOINT_DUPLICATE_ROW')
        seen.add(key)
    cursors = value['seen_cursors']
    if type(cursors) is not list or len(cursors) > 1000: fail('CHECKPOINT_CURSOR_BUDGET')
    for cursor in [*cursors, value['cursor']]:
        if cursor is not None and (type(cursor) is not str or not re.fullmatch(r'SIM_CURSOR_[A-Z0-9_]{1,50}', cursor)):
            fail('REAL_CURSOR_STORAGE_NOT_IMPLEMENTED')
    if len(set(cursors)) != len(cursors): fail('CHECKPOINT_CURSOR_DUPLICATE')
    if value['cursor'] is not None and value['cursor'] not in cursors: fail('CHECKPOINT_CURSOR_RELATION')
    if value['state'] == 'GAP' and value['cursor'] is None: fail('CHECKPOINT_CURSOR_MISSING')
    if value['state'] in ('EMPTY', 'COMPLETE') and value['cursor'] is not None: fail('CHECKPOINT_CURSOR_UNEXPECTED')
    if len(canonical(value).encode('utf-8')) > 900_000: fail('CHECKPOINT_SIZE_BUDGET')


class DurableMockReadSession:
    def __init__(self, plan, transport, directory):
        if type(transport) is not MockTransport: fail('SYNTHETIC_CHECKPOINT_ONLY')
        self.plan = plan; self.transport = transport
        self._payload(ReadSession(plan, transport))  # validates scope before directory creation
        self.journal = Journal(directory)

    @staticmethod
    def _payload(session):
        value = {'schema': 'synthetic-read-v1', 'plan': asdict(session.plan),
                 'rows': session.report()['rows'], 'cursor': session.cursor, 'seen_cursors': sorted(session.cursors),
                 'pages': session.pages, 'state': session.state, 'last_error': session.last_error}
        validate_checkpoint(value)
        return value

    def _load(self):
        data, damaged = self.journal.load(validate_checkpoint)
        if damaged: fail('READ_CHECKPOINT_REVIEW_REQUIRED')
        session = ReadSession(self.plan, self.transport)
        if data is not None:
            if data['plan'] != asdict(self.plan): fail('CHECKPOINT_QUERY_CHANGED')
            for row in data['rows']:
                key = row_identity(self.plan,row)
                session.rows[key] = row
            session.cursor = data['cursor']; session.cursors = set(data['seen_cursors'])
            session.pages = data['pages']; session.state = data['state']; session.last_error = data['last_error']
        return session

    def collect(self, page_budget=2):
        positive_integer(page_budget, 20)
        with self.journal.locked():
            session = self._load()
            if session.state in ('COMPLETE', 'EMPTY'): return session.report()
            for _ in range(page_budget):
                if session.pages >= 1000: fail('CHECKPOINT_PAGE_BUDGET')
                session.collect(1)
                self.journal.save(self._payload(session), validate_checkpoint)
                if session.state != 'GAP': break
            return session.report()

    def status(self):
        with self.journal.locked(): return self._load().report()
