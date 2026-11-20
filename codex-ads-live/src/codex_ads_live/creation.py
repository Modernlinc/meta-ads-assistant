"""PAUSED hierarchy creation contract and durable synthetic execution.

The two journals model an operator and a remote service separately. Neither
contains a real account, credential, network operation or human approval.
The default write/approval interfaces fail closed. Mock IDs cannot be used as
Meta payloads; real parameter mapping and identity verification remain absent.
"""
from copy import deepcopy
from datetime import timedelta

from .approval_ledger import bounded_map, exact, hashed, sim
from .journal import Journal, digest
from .validation import aware_time, fail

LEVELS = {'CAMPAIGN': None, 'ADSET': 'CAMPAIGN', 'AD': 'ADSET'}


def revision(value):
    if type(value) is not int or not 1 <= value <= 100000: fail('CREATE_REVISION')


def minor(value):
    if type(value) is not int or not 0 <= value <= 100000000: fail('CREATE_MINOR_UNIT_AMOUNT')


def validate_object(obj):
    exact(obj, {'account_id', 'object_id', 'level', 'parent_id', 'currency',
                'daily_budget_minor', 'creative_hash', 'status'})
    for key in ('account_id', 'object_id'): sim(obj[key])
    if type(obj['level']) is not str or obj['level'] not in LEVELS or obj['currency'] not in ('USD', 'EUR', 'JPY') or obj['status'] != 'PAUSED':
        fail('CREATE_SCOPE')
    if obj['level'] == 'CAMPAIGN':
        if obj['parent_id'] is not None: fail('CREATE_PARENT_LEVEL')
    else: sim(obj['parent_id'])
    if obj['level'] == 'ADSET':
        minor(obj['daily_budget_minor'])
        if obj['daily_budget_minor'] == 0: fail('CREATE_ADSET_BUDGET_REQUIRED')
    elif obj['daily_budget_minor'] is not None: fail('CREATE_BUDGET_LEVEL')
    if obj['level'] == 'AD': hashed(obj['creative_hash'])
    elif obj['creative_hash'] is not None: fail('CREATE_CREATIVE_LEVEL')


def validate_proposal(proposal):
    exact(proposal, {'plan', 'plan_hash', 'diff'})
    plan = proposal['plan']
    exact(plan, {'schema', 'mode', 'action_id', 'kind', 'object', 'account_revision',
                 'parent_revision', 'evidence_id', 'evidence_at'})
    if plan['schema'] != 'paused-create-v1' or plan['mode'] != 'MOCK' or plan['kind'] != 'SIM_CREATE_PAUSED':
        fail('CREATE_PLAN_SCOPE')
    sim(plan['action_id']); sim(plan['evidence_id']); aware_time(plan['evidence_at'])
    validate_object(plan['object']); revision(plan['account_revision'])
    if plan['object']['level'] == 'CAMPAIGN':
        if plan['parent_revision'] is not None: fail('CREATE_PARENT_REVISION')
    else: revision(plan['parent_revision'])
    if proposal['plan_hash'] != digest(plan) or proposal['diff'] != {'before': None, 'after': plan['object']}:
        fail('CREATE_PLAN_OR_DIFF_CHANGED')


def proposal(action_id, obj, account_revision, parent_revision, evidence_id, evidence_at):
    plan = dict(schema='paused-create-v1', mode='MOCK', action_id=action_id,
                kind='SIM_CREATE_PAUSED', object=deepcopy(obj), account_revision=account_revision,
                parent_revision=parent_revision, evidence_id=evidence_id, evidence_at=evidence_at)
    result = {'plan': plan, 'plan_hash': digest(plan), 'diff': {'before': None, 'after': deepcopy(obj)}}
    validate_proposal(result)
    return result


def validate_consent(consent, packet):
    exact(consent, {'plan_hash', 'reviewer', 'approved_at', 'expires_at', 'mode',
                    'human_identity_verified', 'max_daily_budget_minor'})
    if consent['mode'] != 'MOCK' or consent['reviewer'] != 'SIM_REVIEWER' or consent['human_identity_verified'] is not False:
        fail('CREATE_APPROVAL_IDENTITY')
    if consent['plan_hash'] != packet['plan_hash']: fail('CREATE_APPROVAL_PLAN_CHANGED')
    start = aware_time(consent['approved_at']); end = aware_time(consent['expires_at'])
    if not aware_time(packet['plan']['evidence_at']) <= start < end or end - start > timedelta(minutes=15):
        fail('CREATE_APPROVAL_WINDOW')
    minor(consent['max_daily_budget_minor'])
    amount = packet['plan']['object']['daily_budget_minor'] or 0
    if amount > consent['max_daily_budget_minor']: fail('CREATE_APPROVAL_AMOUNT')


class ApprovalVerifier:
    def verify(self, packet, consent, at): fail('HUMAN_APPROVAL_VERIFIER_UNAVAILABLE')


class SimulatedApprovalVerifier(ApprovalVerifier):
    def verify(self, packet, consent, at):
        validate_proposal(packet); validate_consent(consent, packet)
        if not aware_time(consent['approved_at']) <= aware_time(at) < aware_time(consent['expires_at']):
            fail('CREATE_APPROVAL_EXPIRED_OR_EARLY')


class WriteTransport:
    """A separate write interface; no real implementation or network fallback."""
    def recheck(self, packet): fail('REAL_WRITE_TRANSPORT_UNAVAILABLE')
    def create_paused(self, packet): fail('REAL_WRITE_TRANSPORT_UNAVAILABLE')
    def readback(self, packet): fail('REAL_WRITE_TRANSPORT_UNAVAILABLE')


def validate_remote(data):
    exact(data, {'schema', 'accounts', 'objects', 'receipts'})
    if data['schema'] != 'synthetic-create-service-v1': fail('CREATE_REMOTE_SCHEMA')
    for key in ('accounts', 'objects', 'receipts'): bounded_map(data[key])
    for key, account in data['accounts'].items():
        sim(key); exact(account, {'currency', 'revision'}); revision(account['revision'])
        if account['currency'] not in ('USD', 'EUR', 'JPY'): fail('CREATE_ACCOUNT_CURRENCY')
    for key, row in data['objects'].items():
        exact(row, {'object', 'revision'}); revision(row['revision']); obj = row['object']; validate_object(obj)
        if obj['object_id'] != key or obj['account_id'] not in data['accounts']: fail('CREATE_REMOTE_RELATION')
        if obj['currency'] != data['accounts'][obj['account_id']]['currency']: fail('CREATE_REMOTE_CURRENCY')
        if obj['level'] != 'CAMPAIGN':
            parent = data['objects'].get(obj['parent_id'], {}).get('object')
            if parent is None or parent['level'] != LEVELS[obj['level']] or parent['account_id'] != obj['account_id']:
                fail('CREATE_REMOTE_PARENT')
    for key, receipt in data['receipts'].items():
        sim(key); exact(receipt, {'plan_hash', 'object'}); hashed(receipt['plan_hash']); validate_object(receipt['object'])
        if receipt['object']['object_id'] not in data['objects']: fail('CREATE_RECEIPT_OBJECT_MISSING')


class MockCreationTransport(WriteTransport):
    def __init__(self, directory): self.journal = Journal(directory)

    def _load(self):
        data, damaged = self.journal.load(validate_remote)
        if damaged: fail('CREATE_REMOTE_RECOVERY_REQUIRED')
        return data or dict(schema='synthetic-create-service-v1', accounts={}, objects={}, receipts={})

    def seed_account(self, account_id, currency='USD'):
        sim(account_id)
        with self.journal.locked():
            data = self._load()
            if account_id in data['accounts']: fail('CREATE_ACCOUNT_EXISTS')
            data['accounts'][account_id] = {'currency': currency, 'revision': 1}
            self.journal.save(data, validate_remote)

    def _recheck(self, data, packet):
        validate_proposal(packet); plan = packet['plan']; obj = plan['object']
        account = data['accounts'].get(obj['account_id'])
        if account != {'currency': obj['currency'], 'revision': plan['account_revision']}: fail('CREATE_ACCOUNT_CHANGED')
        if obj['object_id'] in data['objects']: fail('CREATE_OBJECT_ALREADY_EXISTS')
        if obj['level'] != 'CAMPAIGN':
            parent = data['objects'].get(obj['parent_id'])
            if parent is None: fail('CREATE_PARENT_MISSING')
            actual = parent['object']
            if actual['account_id'] != obj['account_id'] or actual['level'] != LEVELS[obj['level']] or actual['status'] != 'PAUSED':
                fail('CREATE_PARENT_SCOPE')
            if parent['revision'] != plan['parent_revision']: fail('CREATE_PARENT_CHANGED')

    def recheck(self, packet):
        with self.journal.locked(): self._recheck(self._load(), packet)

    def create_paused(self, packet):
        with self.journal.locked():
            data = self._load(); plan = packet['plan']; action = plan['action_id']
            # This synthetic service supports correlated receipt lookup. No
            # claim is made that Meta implements this idempotency contract.
            if action in data['receipts']: fail('CREATE_SERVICE_DUPLICATE_REFUSED')
            self._recheck(data, packet)
            data['objects'][plan['object']['object_id']] = {'object': deepcopy(plan['object']), 'revision': 1}
            data['receipts'][action] = {'plan_hash': packet['plan_hash'], 'object': deepcopy(plan['object'])}
            self.journal.save(data, validate_remote)

    def readback(self, packet):
        validate_proposal(packet)
        with self.journal.locked():
            data = self._load(); plan = packet['plan']; receipt = data['receipts'].get(plan['action_id'])
            if receipt is None: return 'UNKNOWN'
            expected = {'plan_hash': packet['plan_hash'], 'object': plan['object']}
            if receipt != expected or data['objects'].get(plan['object']['object_id']) != {'object': plan['object'], 'revision': 1}:
                fail('CREATE_READBACK_CONFLICT')
            return 'VERIFIED_SIM'

    def state(self):
        with self.journal.locked(): return deepcopy(self._load())

    def simulate_account_revision(self, account_id):
        with self.journal.locked():
            data = self._load()
            if account_id not in data['accounts']: fail('CREATE_ACCOUNT_MISSING')
            data['accounts'][account_id]['revision'] += 1; self.journal.save(data, validate_remote)

    def simulate_object_revision(self, object_id):
        with self.journal.locked():
            data = self._load()
            if object_id not in data['objects']: fail('CREATE_OBJECT_MISSING')
            data['objects'][object_id]['revision'] += 1; self.journal.save(data, validate_remote)


def validate_operator(data):
    exact(data, {'schema', 'stopped', 'actions'})
    if data['schema'] != 'synthetic-create-operator-v1' or type(data['stopped']) is not bool: fail('CREATE_OPERATOR_SCOPE')
    bounded_map(data['actions'])
    for action, row in data['actions'].items():
        exact(row, {'proposal', 'consent', 'status'})
        validate_proposal(row['proposal'])
        if action != row['proposal']['plan']['action_id']: fail('CREATE_ACTION_CONFLICT')
        if row['consent'] is not None: validate_consent(row['consent'], row['proposal'])
        if row['status'] not in ('PROPOSED', 'APPROVED_SIM', 'REVOKED_SIM', 'UNKNOWN', 'VERIFIED_SIM'): fail('CREATE_ACTION_STATUS')
        if row['status'] in ('APPROVED_SIM', 'UNKNOWN', 'VERIFIED_SIM') and row['consent'] is None: fail('CREATE_APPROVAL_REQUIRED')


class DurablePausedCreation:
    def __init__(self, directory, transport=None, verifier=None):
        self.journal = Journal(directory)
        self.transport = transport if transport is not None else WriteTransport()
        self.verifier = verifier if verifier is not None else ApprovalVerifier()
        if type(self.transport) not in (WriteTransport, MockCreationTransport) or type(self.verifier) not in (ApprovalVerifier, SimulatedApprovalVerifier):
            fail('CREATE_UNREVIEWED_INTERFACE')
        if type(self.transport) is MockCreationTransport and self.transport.journal.directory == self.journal.directory:
            fail('CREATE_JOURNALS_MUST_BE_SEPARATE')

    def _load(self):
        data, damaged = self.journal.load(validate_operator)
        if damaged: fail('CREATE_OPERATOR_RECOVERY_REQUIRED')
        return data or dict(schema='synthetic-create-operator-v1', stopped=False, actions={})

    def submit(self, packet):
        validate_proposal(packet)
        with self.journal.locked():
            data = self._load(); action = packet['plan']['action_id']
            if data['stopped']: fail('CREATE_STOPPED')
            if action in data['actions']: fail('CREATE_ACTION_ALREADY_EXISTS')
            if any(old['proposal']['plan']['object']['object_id'] == packet['plan']['object']['object_id']
                   for old in data['actions'].values()): fail('CREATE_OBJECT_REFERENCE_ALREADY_USED')
            data['actions'][action] = {'proposal': deepcopy(packet), 'consent': None, 'status': 'PROPOSED'}
            self.journal.save(data, validate_operator)

    def simulated_approval(self, action, approved_at, expires_at, max_daily_budget_minor=0):
        with self.journal.locked():
            data = self._load(); row = self._row(data, action)
            if data['stopped']: fail('CREATE_STOPPED')
            if row['status'] != 'PROPOSED': fail('CREATE_APPROVAL_ALREADY_DECIDED')
            consent = dict(plan_hash=row['proposal']['plan_hash'], reviewer='SIM_REVIEWER',
                           approved_at=approved_at, expires_at=expires_at, mode='MOCK',
                           human_identity_verified=False, max_daily_budget_minor=max_daily_budget_minor)
            validate_consent(consent, row['proposal'])
            row.update(consent=consent, status='APPROVED_SIM'); self.journal.save(data, validate_operator)
            return deepcopy(consent)

    @staticmethod
    def _row(data, action):
        sim(action)
        if action not in data['actions']: fail('CREATE_ACTION_NOT_FOUND')
        return data['actions'][action]

    def execute(self, action, at, failure=None):
        if failure not in (None, 'BEFORE_COMMIT', 'AFTER_COMMIT'): fail('CREATE_FAILURE_MODE')
        with self.journal.locked():
            data = self._load(); row = self._row(data, action)
            if row['status'] in ('UNKNOWN', 'VERIFIED_SIM'): return self._result(row['status'])
            if data['stopped']: fail('CREATE_STOPPED')
            if row['status'] != 'APPROVED_SIM': fail('CREATE_APPROVAL_REQUIRED')
            parent_id = row['proposal']['plan']['object']['parent_id']
            if parent_id is not None and any(
                    prior['proposal']['plan']['object']['object_id'] == parent_id and prior['status'] != 'VERIFIED_SIM'
                    for prior in data['actions'].values()):
                fail('CREATE_PARENT_EXECUTION_UNRESOLVED')
            self.verifier.verify(row['proposal'], row['consent'], at)
            self.transport.recheck(row['proposal'])
            row['status'] = 'UNKNOWN'; self.journal.save(data, validate_operator)
            if failure == 'BEFORE_COMMIT': return self._result('UNKNOWN')
            # Any service failure leaves the durable UNKNOWN checkpoint intact.
            # Never echo service exceptions or silently resend this action.
            try: self.transport.create_paused(row['proposal'])
            except Exception: return self._result('UNKNOWN')
            if failure == 'AFTER_COMMIT': return self._result('UNKNOWN')
            return self._reconcile(data, row)

    @staticmethod
    def _result(status):
        return dict(status=status, mode='MOCK', human_identity_verified=False,
                    live_action_authorized=False, real_objects_created=0)

    def _reconcile(self, data, row):
        status = self.transport.readback(row['proposal'])
        if status == 'VERIFIED_SIM':
            row['status'] = status; self.journal.save(data, validate_operator)
        return self._result(status)

    def reconcile(self, action):
        with self.journal.locked():
            data = self._load(); row = self._row(data, action)
            if row['status'] not in ('UNKNOWN', 'VERIFIED_SIM'): fail('CREATE_NO_EXECUTION_TO_RECONCILE')
            return self._reconcile(data, row)

    def revoke_simulated_approval(self, action):
        with self.journal.locked():
            data = self._load(); row = self._row(data, action)
            if row['status'] not in ('PROPOSED', 'APPROVED_SIM'): fail('CREATE_EXECUTION_ALREADY_STARTED')
            row['status'] = 'REVOKED_SIM'; self.journal.save(data, validate_operator)

    def stop(self):
        with self.journal.locked():
            data = self._load(); data['stopped'] = True; self.journal.save(data, validate_operator)

    def status(self):
        with self.journal.locked():
            data = self._load()
            return dict(mode='MOCK', human_identity_verified=False, live_action_authorized=False, **data)
