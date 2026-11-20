"""Bounded range replay with sanitized snapshots, never opaque cursor storage.

Restarting explicitly creates a new generation and re-reads from page one.
Old and new generations are never combined into a false complete total.
"""
from dataclasses import asdict
from datetime import datetime, timezone
from .journal import Journal, canonical
from .read_adapter import ReadPlan, ReadSession, MockTransport, normalize_row, positive_integer, row_identity
from .validation import aware_time, fail, object_only
import re


def validate(value):
    keys={'schema','mode','plan','generation','state','rows','pages','last_error','observed_at'}
    object_only(value,keys,keys)
    if value['schema']!='range-replay-v1' or value['mode'] not in ('MOCK','READ_ONLY'): fail('ARCHIVE_SCHEMA')
    object_only(value['plan'],ReadPlan.__dataclass_fields__,ReadPlan.__dataclass_fields__)
    plan=ReadPlan(**value['plan'])
    if type(value['generation']) is not int or not 1<=value['generation']<=100000: fail('ARCHIVE_GENERATION')
    if value['state'] not in ('RUNNING','GAP','COMPLETE','EMPTY','ERROR','STOPPED'): fail('ARCHIVE_STATE')
    if type(value['pages']) is not int or not 0<=value['pages']<=1000: fail('ARCHIVE_PAGE_BUDGET')
    if value['last_error'] is not None and (type(value['last_error']) is not str or not re.fullmatch('[A-Z_]{1,80}',value['last_error'])): fail('ARCHIVE_ERROR')
    aware_time(value['observed_at'])
    if type(value['rows']) is not list or len(value['rows'])>1000: fail('ARCHIVE_ROW_BUDGET')
    seen=set()
    for row in value['rows']:
        if normalize_row(plan,row)!=row: fail('ARCHIVE_UNSANITIZED_ROW')
        key=row_identity(plan,row)
        if key in seen: fail('ARCHIVE_DUPLICATE_ROW')
        seen.add(key)
    if value['state']=='EMPTY' and value['rows']: fail('ARCHIVE_EMPTY_CONFLICT')
    if value['state']=='COMPLETE' and not value['rows']: fail('ARCHIVE_COMPLETE_CONFLICT')
    if len(canonical(value).encode('utf-8'))>900000: fail('ARCHIVE_SIZE_BUDGET')


class RangeReplayRead:
    def __init__(self,plan,transport,directory):
        self.session=ReadSession(plan,transport)
        self.plan=plan;self.transport=transport;self.generation=None
        self.journal=Journal(directory,mode=self.session.mode)

    def _load(self):
        value,damaged=self.journal.load(validate)
        if damaged:fail('ARCHIVE_RECOVERY_REVIEW_REQUIRED')
        if value is not None and (value['plan']!=asdict(self.plan) or value['mode']!=self.session.mode):
            fail('ARCHIVE_QUERY_CHANGED')
        return value

    def _snapshot(self,state):
        report=self.session.report()
        return {'schema':'range-replay-v1','mode':self.session.mode,'plan':asdict(self.plan),
                'generation':self.generation,'state':state,'rows':report['rows'],'pages':report['pages'],
                'last_error':report['last_error'],'observed_at':datetime.now(timezone.utc).isoformat()}

    def collect(self,page_budget=2,restart_from_first_page=False):
        positive_integer(page_budget,20)
        if type(restart_from_first_page) is not bool:fail('REPLAY_FLAG')
        with self.journal.locked():
            existing=self._load()
            if restart_from_first_page or existing is None:
                self.generation=(existing['generation']+1) if existing else 1
                self.session=ReadSession(self.plan,self.transport)
                self.journal.save(self._snapshot('RUNNING'),validate)
            elif self.generation!=existing['generation']:
                fail('EXPLICIT_RANGE_REPLAY_REQUIRED')
            elif existing['state'] in ('STOPPED','ERROR'):
                fail('EXPLICIT_RANGE_REPLAY_REQUIRED')
            elif existing['state'] in ('COMPLETE','EMPTY'):
                return self._public(existing)
        for _ in range(page_budget):
            with self.journal.locked():
                existing=self._load()
                if existing['generation']!=self.generation or existing['state'] not in ('RUNNING','GAP'):fail('ARCHIVE_RUN_SUPERSEDED')
                if existing['pages']>=1000:fail('ARCHIVE_PAGE_BUDGET')
            result=self.session.collect(1)  # Network is outside the state lock.
            with self.journal.locked():
                current=self._load()
                if current['generation']!=self.generation or current['state'] not in ('RUNNING','GAP'):
                    fail('ARCHIVE_RUN_SUPERSEDED')  # Discard stale in-flight response.
                value=self._snapshot(result['state']);self.journal.save(value,validate)
            if result['state']!='GAP':break
        return self._public(value)

    @staticmethod
    def _public(value):
        return {**value,'restart_strategy':'EXPLICIT_REPLAY_FROM_FIRST_PAGE','opaque_cursor_persisted':False,
                'coverage':'CURRENT_GENERATION_BOUNDED_QUERY_NOT_GLOBAL_TOTAL','real_connection_verified':False,
                'cross_page_point_in_time_consistency':'NOT_GUARANTEED'}

    def status(self):
        with self.journal.locked():
            value=self._load()
            if value is None:return {'state':'NOT_RUN','real_connection_verified':False}
            return self._public(value)

    def stop(self):
        with self.journal.locked():
            value=self._load()
            if value is None:fail('ARCHIVE_NOT_STARTED')
            value['state']='STOPPED';self.journal.save(value,validate)
