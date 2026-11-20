"""Readable proposal review over existing MOCK engines; no real write route.

Terminal decisions are simulated scope confirmations, never trusted identity.
Case/decision hashes detect accidental changes, not a malicious local writer.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from uuid import uuid4

from .approval_ledger import DurableApprovalSimulation, exact, hashed, sim
from .connection import _write
from .creation import (DurablePausedCreation, MockCreationTransport, SimulatedApprovalVerifier,
                       LEVELS, proposal as create_proposal, validate_object)
from .journal import Journal, canonical, digest
from .monitor_tools import new_directory
from .read_adapter import numeric_string, positive_integer
from .validation import InputError, aware_time, fail, strict_json

PACKAGE = Path(__file__).resolve().parents[2]
SOURCE = 'examples/synthetic-planning/followup-v2.json'
COMMON = {'action_id', 'kind', 'evidence_id', 'rationale'}
MATERIAL_KEYS = {'primary_text', 'headline', 'cta_text', 'destination_url', 'media_state'}
REVIEW_KEYS = {'schema', 'mode', 'action_id', 'kind', 'case_sha256', 'proposal', 'material', 'rationale', 'source',
               'amount_display', 'unit', 'current_context', 'execution_state', 'blockers', 'human_identity_verified',
               'live_action_authorized', 'meta_parameter_mapping_verified', 'actual_media_available'}


def utcnow(): return datetime.now(timezone.utc)
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def _text(value, limit=5000):
    if type(value) is not str or not value.strip() or len(value) > limit: fail('DESK_TEXT_REQUIRED')
    if re.search(r'\bEAA[A-Za-z0-9]{50,}\b|\bsk-proj-[A-Za-z0-9_-]{30,}\b|PRIVATE KEY-----', value):
        fail('DESK_SECRET_PATTERN_REFUSED')


def validate_case(value):
    exact(value, {'schema', 'mode', 'synthetic', 'account_id', 'currency', 'source', 'budget_object', 'actions'})
    if value['schema'] != 'proposal-case-v1' or value['mode'] != 'MOCK' or value['synthetic'] is not True:
        fail('DESK_SYNTHETIC_ONLY')
    sim(value['account_id'])
    if value['currency'] not in ('USD', 'EUR', 'JPY'): fail('DESK_CURRENCY')
    source = value['source']; exact(source, {'path', 'sha256', 'scope'}); hashed(source['sha256'])
    if source['path'] != SOURCE or source['scope'] != 'COPY_REFERENCE_ONLY_NOT_BUDGET_OR_META_MAPPING': fail('DESK_SOURCE_SCOPE')
    if sha(PACKAGE / SOURCE) != source['sha256']: fail('DESK_SOURCE_VERSION_CHANGED')
    budget = value['budget_object']; exact(budget, {'object_id', 'budget_major'})
    sim(budget['object_id']); numeric_string(budget['budget_major'])
    actions = value['actions']
    if type(actions) is not list or not 1 <= len(actions) <= 20: fail('DESK_ACTION_BUDGET')
    ids = set(); objects = {}
    for action in actions:
        if type(action) is not dict: fail('DESK_ACTION')
        kind = action.get('kind')
        if kind == 'CREATE_PAUSED':
            exact(action, COMMON | {'object', 'material'}); obj = action['object']; validate_object(obj)
            if obj['account_id'] != value['account_id'] or obj['currency'] != value['currency']: fail('DESK_ACCOUNT_CONTEXT')
            if obj['object_id'] in objects or obj['object_id'] == budget['object_id']: fail('DESK_OBJECT_DUPLICATE')
            objects[obj['object_id']] = obj
            material = action['material']
            if obj['level'] == 'AD':
                exact(material, MATERIAL_KEYS)
                for key in MATERIAL_KEYS: _text(material[key])
                try:
                    url = urlsplit(material['destination_url'])
                    if (url.scheme != 'https' or url.hostname != 'example.invalid' or url.username is not None
                            or url.password is not None or url.port is not None or url.fragment): fail('DESK_RESERVED_URL_REQUIRED')
                except ValueError: raise InputError('DESK_RESERVED_URL_REQUIRED') from None
                if material['media_state'] != 'TEXT_ONLY_NO_MEDIA_NOT_READY_FOR_REAL_USE': fail('DESK_MEDIA_SCOPE')
                if obj['creative_hash'] != digest(material): fail('DESK_CREATIVE_CONTENT_CHANGED')
            elif material is not None: fail('DESK_UNUSED_MATERIAL')
        elif kind == 'SET_BUDGET':
            exact(action, COMMON | {'object_id', 'after_major'}); sim(action['object_id']); numeric_string(action['after_major'])
            if action['object_id'] != budget['object_id']: fail('DESK_BUDGET_OBJECT_SCOPE')
        else: fail('DESK_ACTION_KIND')
        sim(action['action_id']); sim(action['evidence_id']); _text(action['rationale'], 2000)
        if action['action_id'] in ids: fail('DESK_ACTION_DUPLICATE')
        ids.add(action['action_id'])
    for obj in objects.values():
        if obj['level'] != 'CAMPAIGN':
            parent = objects.get(obj['parent_id'])
            if parent is None or parent['level'] != LEVELS[obj['level']]: fail('DESK_PARENT_PLAN_REQUIRED')
    if len(canonical(value).encode()) > 64000: fail('DESK_CASE_TOO_LARGE')


def _read(path, maximum=64000):
    from .local_paths import checked_path
    path = checked_path(path, link_error='DESK_PATH_SYMLINK', volume_error='DESK_VOLUME_NOT_MOUNTED')
    if not path.is_file() or path.stat().st_size > maximum: fail('DESK_FILE_REQUIRED')
    return strict_json(path.read_text(encoding='utf-8'))


def load_case(path):
    value = _read(path); validate_case(value); return value


def example_case():
    source = strict_json((PACKAGE / SOURCE).read_text())['draft']['objects'][2]
    material = {**source['current_copy'], 'destination_url': source['destination_url'],
                'media_state': 'TEXT_ONLY_NO_MEDIA_NOT_READY_FOR_REAL_USE'}
    actions = []
    for level, parent in [('CAMPAIGN', None), ('ADSET', 'SIM_REVIEW_CAMPAIGN'), ('AD', 'SIM_REVIEW_ADSET')]:
        obj = dict(account_id='SIM_REVIEW_ACCOUNT', object_id='SIM_REVIEW_' + level, level=level,
                   parent_id=parent, currency='USD', daily_budget_minor=1000 if level == 'ADSET' else None,
                   creative_hash=digest(material) if level == 'AD' else None, status='PAUSED')
        actions.append(dict(action_id='SIM_REVIEW_CREATE_' + level, kind='CREATE_PAUSED', object=obj,
            material=deepcopy(material) if level == 'AD' else None, evidence_id='SIM_REVIEW_EVIDENCE',
            rationale='固定工程模拟：逐对象创建并保持PAUSED。1000 minor units只作门禁测试，不是原文字案例首批额度的换算或投放建议。'))
    actions.append(dict(action_id='SIM_REVIEW_BUDGET', kind='SET_BUDGET', object_id='SIM_EXISTING_ADSET',
        after_major='12.00', evidence_id='SIM_REVIEW_EVIDENCE',
        rationale='独立既有模拟对象从10.00改为12.00 major units，保持SIM_PAUSED；仅检查差异/版本/撤回，不改变上述新建对象的预算。'))
    value = dict(schema='proposal-case-v1', mode='MOCK', synthetic=True, account_id='SIM_REVIEW_ACCOUNT', currency='USD',
        source=dict(path=SOURCE, sha256=sha(PACKAGE / SOURCE), scope='COPY_REFERENCE_ONLY_NOT_BUDGET_OR_META_MAPPING'),
        budget_object=dict(object_id='SIM_EXISTING_ADSET', budget_major='10.00'), actions=actions)
    validate_case(value); return value


def prepare_case(directory):
    value = example_case(); root = new_directory(directory); _write(root, 'case.json', value)
    return dict(status='SYNTHETIC_REVIEW_CASE_CREATED', mode='MOCK', real_actions_authorized=False,
                case=str(root / 'case.json'), source_scope=value['source']['scope'])


def validate_control(data):
    exact(data, {'schema', 'stopped', 'decisions'})
    if data['schema'] != 'simulation-review-control-v1' or type(data['stopped']) is not bool: fail('DESK_CONTROL_SCHEMA')
    if type(data['decisions']) is not dict or len(data['decisions']) > 20: fail('DESK_DECISION_BUDGET')
    for action, pointer in data['decisions'].items():
        sim(action); exact(pointer, {'directory', 'sha256'}); hashed(pointer['sha256'])
        if type(pointer['directory']) is not str or not re.fullmatch('[a-f0-9]{32}', pointer['directory']): fail('DESK_DECISION_PATH')


def import_case(value, directory, *, clock=utcnow):
    value = strict_json(canonical(value)); validate_case(value); stamp = clock().isoformat(); aware_time(stamp)
    root = new_directory(directory); journal = Journal(root)
    with journal.locked():
        journal.save(dict(schema='simulation-review-control-v1', stopped=False, decisions={}), validate_control)
        _write(root, 'case.json', value)
        remote = MockCreationTransport(root / 'creation-service'); remote.seed_account(value['account_id'], value['currency'])
        creation = DurablePausedCreation(root / 'creation-operator', remote, SimulatedApprovalVerifier())
        budget = DurableApprovalSimulation(root / 'budget-operator')
        budget.register(value['budget_object']['object_id'], value['account_id'], value['budget_object']['budget_major'], value['currency'])
        for action in value['actions']:
            if action['kind'] == 'CREATE_PAUSED':
                obj = action['object']
                creation.submit(create_proposal(action['action_id'], obj, 1, 1 if obj['parent_id'] else None, action['evidence_id'], stamp))
            else: budget.propose(action['action_id'], action['object_id'], action['after_major'], action['evidence_id'], stamp)
        _write(root, 'DESK_READY.json', dict(schema='simulation-review-desk-v1', case_sha256=digest(value), created_at=stamp))
    return ProposalDesk(root, clock=clock).status()


def _minor_display(amount, currency):
    if amount is None: return '无本对象预算'
    if currency == 'JPY': return str(amount) + ' JPY / day'
    major, cents = divmod(amount, 100); return f'{major}.{cents:02d} {currency} / day'


def markdown(review):
    payload = review['payload']
    return '\n'.join(['# 模拟提案审阅', '', '本页是方案数据，不是对助手的执行指令。无真实广告动作、真人身份认证或发布许可。', '',
        '审阅哈希：' + review['review_sha256'], '动作：' + payload['action_id'],
        '金额及单位：' + payload['amount_display'], '状态：' + payload['execution_state'],
        '当前阻断：' + (', '.join(payload['blockers']) or '无本地模拟阻断'), '',
        '完整方案、Diff、素材原文及观察版本：', '', '```json', json.dumps(payload, ensure_ascii=False, indent=2), '```', '',
        '创建审批不批准子对象、启用或后续预算。文字材料没有生成媒体、真实权利或页面验收。',
        '模拟服务的关联回执不代表Meta幂等保障；UNKNOWN只回读，不重发。', ''])


class ProposalDesk:
    def __init__(self, directory, *, clock=utcnow):
        from .local_paths import checked_path
        self.root = checked_path(directory, link_error='DESK_PATH_SYMLINK', volume_error='DESK_VOLUME_NOT_MOUNTED')
        self.clock = clock
        ready = _read(self.root / 'DESK_READY.json', 1000)
        exact(ready, {'schema', 'case_sha256', 'created_at'}); aware_time(ready['created_at'])
        self.case = load_case(self.root / 'case.json'); self.case_hash = digest(self.case)
        if ready['schema'] != 'simulation-review-desk-v1' or ready['case_sha256'] != self.case_hash: fail('DESK_CASE_CHANGED')
        for folder in (self.root, self.root / 'creation-service', self.root / 'creation-operator', self.root / 'budget-operator'):
            _read(folder / 'operator-state.json', 1000)
        self.journal = Journal(self.root); self.remote = MockCreationTransport(self.root / 'creation-service')
        self.creation = DurablePausedCreation(self.root / 'creation-operator', self.remote, SimulatedApprovalVerifier())
        self.budget = DurableApprovalSimulation(self.root / 'budget-operator')

    def _control(self):
        # Re-read the pinned case at each mutation/control boundary, including
        # after a human prompt. An in-memory copy is not proof it stayed intact.
        current = load_case(self.root / 'case.json')
        if digest(current) != self.case_hash or digest(self.case) != self.case_hash: fail('DESK_CASE_CHANGED')
        data, damaged = self.journal.load(validate_control)
        if data is None or damaged: fail('DESK_RECOVERY_REVIEW_REQUIRED')
        if not set(data['decisions']) <= {a['action_id'] for a in self.case['actions']}: fail('DESK_DECISION_SCOPE')
        return data

    def _action(self, action):
        sim(action)
        row = next((row for row in self.case['actions'] if row['action_id'] == action), None)
        if row is None: fail('DESK_ACTION_NOT_FOUND')
        return row

    def _inspect(self, action):
        row = self._action(action)
        if row['kind'] == 'CREATE_PAUSED':
            state = self.creation.status(); item = state['actions'].get(action)
            if item is None or item['proposal']['plan']['object'] != row['object']: fail('DESK_CASE_ENGINE_MISMATCH')
            remote = self.remote.state(); obj = row['object']
            return dict(proposal=item['proposal'], approval=item['consent'], status=item['status'],
                        runtime='STOPPED' if state['stopped'] else 'RUNNING',
                        context=dict(account=remote['accounts'].get(obj['account_id']),
                                     parent=remote['objects'].get(obj['parent_id']), target=remote['objects'].get(obj['object_id'])))
        item = self.budget.inspect(action)
        plan = item['proposal']['plan']
        if (plan['object_id'], plan['account_id'], plan['currency'], plan['after']) != (
                row['object_id'], self.case['account_id'], self.case['currency'], row['after_major']): fail('DESK_CASE_ENGINE_MISMATCH')
        return {**item, 'context': item['object']}

    def _review(self, action, control):
        row = self._action(action); item = self._inspect(action); blockers = []
        if control['stopped'] or item['runtime'] == 'STOPPED': blockers.append('DESK_STOPPED')
        if item['status'] != 'PROPOSED': blockers.append('DESK_NOT_PROPOSED')
        if row['kind'] == 'CREATE_PAUSED':
            try: self.remote.recheck(item['proposal'])
            except InputError as exc: blockers.append(str(exc))
            parent = row['object']['parent_id']
            if parent is not None:
                parent_action = next(a['action_id'] for a in self.case['actions'] if a['kind'] == 'CREATE_PAUSED' and a['object']['object_id'] == parent)
                if self.creation.status()['actions'][parent_action]['status'] != 'VERIFIED_SIM': blockers.append('CREATE_PARENT_EXECUTION_UNRESOLVED')
            amount = _minor_display(row['object']['daily_budget_minor'], self.case['currency'])
            unit = 'INTEGER_MINOR_CURRENCY_UNITS'; material = row['material']
        else:
            plan = item['proposal']['plan']; obj = item['object']
            if (obj['account_id'], obj['currency'], obj['budget'], obj['state_version'], obj['status']) != (
                    plan['account_id'], plan['currency'], plan['before'], plan['state_version'], plan['expected_status']): blockers.append('STATE_VERSION_OR_CONTEXT_CHANGED')
            with localcontext() as context:
                context.prec = 64; delta = str(Decimal(plan['after']) - Decimal(plan['before']))
            amount = plan['before'] + ' → ' + plan['after'] + ' ' + plan['currency'] + ' (delta ' + delta + ')'
            unit = 'MAJOR_CURRENCY_SIMULATION_ONLY'; material = None
        payload = dict(schema='simulation-review-v1', mode='MOCK', action_id=action, kind=row['kind'], case_sha256=self.case_hash,
            proposal=item['proposal'], material=material, rationale=row['rationale'], source=self.case['source'],
            amount_display=amount, unit=unit, current_context=item['context'], execution_state=item['status'],
            blockers=blockers, human_identity_verified=False, live_action_authorized=False,
            meta_parameter_mapping_verified=False, actual_media_available=False)
        return dict(status='REVIEW_BLOCKED' if blockers else 'REVIEW_READY', observed_at=self.clock().isoformat(),
                    review_sha256=digest(payload), payload=deepcopy(payload))

    def review(self, action, output=None):
        with self.journal.locked(): view = self._review(action, self._control())
        if output is not None:
            root = new_directory(output); _write(root, 'REVIEW.json', view)
            with (root / 'REVIEW.md').open('x', encoding='utf-8') as handle:
                handle.write(markdown(view)); handle.flush(); os.fsync(handle.fileno())
            _write(root, 'RECEIPT.json', dict(mode='MOCK', files=[dict(path=p.name, sha256=sha(p)) for p in sorted(root.iterdir())],
                  human_identity_verified=False, live_action_authorized=False))
        return view

    def _correlated(self, action, control, item):
        pointer = control['decisions'].get(action)
        if pointer is None or item['approval'] is None: return False
        path = self.root / 'decisions' / pointer['directory'] / 'INTENT.json'
        decision = _read(path, 128000)
        if sha(path) != pointer['sha256']: fail('DESK_DECISION_CHANGED')
        exact(decision, {'schema', 'review', 'review_sha256', 'approval'})
        exact(decision['review'], REVIEW_KEYS); hashed(decision['review_sha256'])
        if (decision['schema'] != 'simulation-review-decision-v1' or digest(decision['review']) != decision['review_sha256']
                or decision['review']['case_sha256'] != self.case_hash or decision['review']['action_id'] != action
                or decision['review']['proposal'] != item['proposal'] or decision['approval'] != item['approval']):
            fail('DESK_DECISION_ENGINE_MISMATCH')
        return True

    def approve_sim(self, action, review_hash, ttl_seconds=600):
        hashed(review_hash); positive_integer(ttl_seconds, 900)
        view = self.review(action)
        if view['review_sha256'] != review_hash: fail('DESK_REVIEW_CHANGED')
        if view['payload']['blockers']: fail('DESK_REVIEW_BLOCKED')
        if not sys.stdin.isatty() or not sys.stdout.isatty(): fail('DESK_INTERACTIVE_REVIEW_REQUIRED')
        approved = self.clock(); expires = approved + timedelta(seconds=ttl_seconds)
        plan = view['payload']['proposal']['plan']; row = self._action(action)
        consent = dict(plan_hash=view['payload']['proposal']['plan_hash'], reviewer='SIM_REVIEWER',
                       approved_at=approved.isoformat(), expires_at=expires.isoformat(), mode='MOCK', human_identity_verified=False)
        if row['kind'] == 'CREATE_PAUSED': consent['max_daily_budget_minor'] = row['object']['daily_budget_minor'] or 0
        else: consent['max_budget'] = row['after_major']
        print(markdown(view)); print(canonical(consent))
        phrase = 'SIM APPROVE ' + review_hash + ' UNTIL ' + consent['expires_at']
        if input('仅确认本地模拟，请输入 ' + phrase + '：') != phrase: fail('DESK_REVIEW_NOT_CONFIRMED')
        if not approved <= self.clock() < min(expires, approved + timedelta(minutes=5)): fail('DESK_REVIEW_EXPIRED')
        with self.journal.locked():
            control = self._control(); current = self._review(action, control)
            if current['review_sha256'] != review_hash: fail('DESK_REVIEW_CHANGED')
            directory = uuid4().hex; audit = new_directory(self.root / 'decisions' / directory)
            _write(audit, 'INTENT.json', dict(schema='simulation-review-decision-v1', review=view['payload'],
                   review_sha256=review_hash, approval=consent))
            control['decisions'][action] = dict(directory=directory, sha256=sha(audit / 'INTENT.json'))
            self.journal.save(control, validate_control)
            if not approved <= self.clock() < min(expires, approved + timedelta(minutes=5)): fail('DESK_REVIEW_EXPIRED')
            if row['kind'] == 'CREATE_PAUSED':
                actual = self.creation.simulated_approval(action, consent['approved_at'], consent['expires_at'], consent['max_daily_budget_minor'])
            else: actual = self.budget.approve(action, consent['approved_at'], consent['expires_at'], consent['max_budget'])
            if actual != consent: fail('DESK_DECISION_ENGINE_MISMATCH')
            _write(audit, 'RESULT.json', dict(status='APPROVED_SIM', consent=actual, review_sha256=review_hash,
                                           human_identity_verified=False, live_action_authorized=False))
            return dict(status='APPROVED_SIM', action_id=action, review_sha256=review_hash, consent=actual,
                        human_identity_verified=False, live_action_authorized=False)

    def execute_sim(self, action):
        with self.journal.locked():
            control = self._control()
            if control['stopped']: fail('DESK_STOPPED')
            item = self._inspect(action)
            if item['status'] == 'REVOKED_SIM': fail('DESK_APPROVAL_REVOKED')
            if not self._correlated(action, control, item): fail('DESK_CORRELATED_REVIEW_REQUIRED')
            engine = self.creation if self._action(action)['kind'] == 'CREATE_PAUSED' else self.budget
            return {**engine.execute(action, self.clock().isoformat()), 'human_identity_verified': False,
                    'live_action_authorized': False}

    def reconcile_sim(self, action):
        with self.journal.locked():
            control = self._control(); item = self._inspect(action)
            if item['status'] not in ('UNKNOWN', 'VERIFIED_SIM'): fail('DESK_NO_EXECUTION_TO_RECONCILE')
            if not self._correlated(action, control, item): fail('DESK_CORRELATED_REVIEW_REQUIRED')
            engine = self.creation if self._action(action)['kind'] == 'CREATE_PAUSED' else self.budget
            return {**engine.reconcile(action), 'human_identity_verified': False, 'live_action_authorized': False}

    def revoke_sim(self, action):
        with self.journal.locked():
            self._control(); item = self._inspect(action)
            if item['status'] == 'REVOKED_SIM':
                return dict(status='REVOKED_SIM', human_identity_verified=False, live_action_authorized=False)
            if item['status'] not in ('PROPOSED', 'APPROVED_SIM'): fail('DESK_EXECUTION_ALREADY_STARTED')
            if self._action(action)['kind'] == 'CREATE_PAUSED': self.creation.revoke_simulated_approval(action)
            else: self.budget.revoke(action, self.clock().isoformat())
            return dict(status='REVOKED_SIM', human_identity_verified=False, live_action_authorized=False)

    def stop(self):
        with self.journal.locked():
            control = self._control(); control['stopped'] = True; self.journal.save(control, validate_control)
            self.creation.stop(); self.budget.stop()
        return self.status()

    def status(self):
        with self.journal.locked():
            control = self._control(); rows = {}
            for row in self.case['actions']:
                action = row['action_id']; item = self._inspect(action)
                rows[action] = dict(kind=row['kind'], status=item['status'], approval=item['approval'],
                                    review_correlated=self._correlated(action, control, item),
                                    blockers=self._review(action, control)['payload']['blockers'])
            return dict(schema='simulation-review-status-v1', mode='MOCK', stopped=control['stopped'],
                        case_sha256=self.case_hash, actions=rows, human_identity_verified=False, live_action_authorized=False)
