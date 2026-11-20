"""Durable mapped-action simulation, using the real draft/recheck contracts.

Numeric fixture IDs are not proof of real assets. This coordinator accepts only
synthetic baselines and the bundled synthetic service; it supplies no real
approval issuer or Meta writer. The default verifier and transport reject.
"""
from copy import deepcopy
from datetime import timedelta
import re
import time
from uuid import uuid4

from .activation import is_activation, is_budget_change, match_budget_baseline, validate_coverage
from .creation import ApprovalVerifier
from .journal import Journal, canonical, digest
from .meta_write_mapping import PROPOSAL_PACKET_REQUIRED, utcnow
from .validation import InputError, aware_time, fail, object_only, strict_json
from .write_snapshots import CapturedSnapshots, _CAPTURE_KEY, review_snapshots, validate_bundle
from .write_recheck import collect_approved_recheck, validate_approval_reference
from .materials import validate_material, material_findings, check_media_files
from .material_bundle import attach_material_packet, load_material_packet, validate_stored_material_evidence

STATES=('PROPOSED','APPROVED_SIM','RECHECKING','INVALIDATED','REVOKED_SIM','UNKNOWN','VERIFIED_SIM')
MATERIAL_PACKET_VERSION='mapped-synthetic-action-v5'
AD_MATERIAL_VERSIONS=('mapped-synthetic-action-v2',MATERIAL_PACKET_VERSION)
DUPLICATE_CREATE_REQUEST='MAPPED_DUPLICATE_CREATE_REQUEST'
MATERIAL_COMMIT_RECHECK_FAILED='MATERIAL_COMMIT_RECHECK_FAILED'
READBACK_SERVICE_ERRORS=frozenset(('MAPPED_NO_CORRELATED_RECEIPT','MAPPED_SERVICE_RECEIPT_CONFLICT',
    'MAPPED_SERVICE_READBACK_CHANGED','MAPPED_SERVICE_MATERIAL_CHANGED','MAPPED_SERVICE_REVIEW_SCOPE_CHANGED',
    'MAPPED_SERVICE_RECOVERY_REQUIRED','MAPPED_SERVICE_NOT_SEEDED','STATE_BUSY'))
SIM_BLOCKERS=frozenset(('V27_WRITE_PROFILE_NOT_FULLY_VERIFIED','TRUSTED_HUMAN_WRITE_APPROVAL_UNAVAILABLE',
    'REAL_WRITE_TRANSPORT_UNAVAILABLE','WRITE_GRANTS_AND_ASSET_BINDING_NOT_VERIFIED',
    'SNAPSHOT_IN_PROCESS_SYNTHETIC_NOT_WRITE_AUTHORITY','SNAPSHOT_FIXED_FIELDS_NOT_ATOMIC_RECHECK_REQUIRED_BEFORE_WRITE',
    'PARENT_OBJECTIVE_AND_TARGETING_RULES_REQUIRE_CURRENT_RECHECK','CURRENCY_BUDGET_UNIT_AND_PLATFORM_MINIMUM_REQUIRE_RECHECK',
    'PIXEL_ACCESS_AND_WEBSITE_EVENT_REQUIRE_CURRENT_RECHECK',
    'EXISTING_CREATIVE_PAGE_COPY_MEDIA_AND_RIGHTS_REQUIRE_RECHECK','DESCENDANT_DELIVERY_IMPACT_REQUIRES_SEPARATE_REVIEW',
    'CREATIVE_ACCOUNT_IMAGE_PAGE_RIGHTS_AND_PREVIEW_REQUIRE_RECHECK',
    'CREATIVE_ACTOR_ID_NOT_FULL_COPY_MEDIA_OR_RIGHTS_VERIFICATION'))


def _exact(value,keys): object_only(value,keys,keys)
def _copy(value): return strict_json(canonical(value))
def _action(value):
    if type(value) is not str or not re.fullmatch('SIM_ACTION_[A-Z0-9_]{1,60}',value): fail('MAPPED_ACTION_ID')
def _hash(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{64}',value): fail('MAPPED_ACTION_HASH')


def same_create_request(draft, other):
    """Compare validated creation parameters independently of review freshness."""
    if not draft['operation'].startswith('CREATE_') or any(
            draft[key]!=other[key] for key in ('operation','account_id','api_version')):
        return False
    if draft['operation']=='CREATE_CREATIVE' and draft['context']['page_id']!=other['context']['page_id']:
        return False
    def parameters(value):
        return {key:item for key,item in value['parameters'].items() if key!='promoted_object' or item is not None}
    return parameters(draft)==parameters(other)


def _baseline(packet):
    # Rehydration is deliberately restricted to synthetic evidence. Persisted
    # Graph files must never regain an in-process capture capability here.
    bundle=packet['bundle']
    if bundle.get('source_mode')!='SYNTHETIC' or bundle.get('read_identity') is not None:
        fail('MAPPED_SYNTHETIC_BASELINE_ONLY')
    validate_bundle(packet['draft'],bundle,clock=lambda:aware_time(bundle['ended_at']))
    return CapturedSnapshots(canonical(bundle),_CAPTURE_KEY)


def packet_review(packet):
    if type(packet) is not dict: fail('MAPPED_PACKET_SCHEMA')
    version=packet.get('schema')
    keys = ('schema', 'draft', 'bundle', 'review_sha256')
    if version in (*AD_MATERIAL_VERSIONS,'mapped-synthetic-action-v3','mapped-synthetic-action-v4'):
        keys += ('material',)
    if version in ('mapped-synthetic-action-v3', 'mapped-synthetic-action-v4'):
        keys += ('coverage',)
    if version==MATERIAL_PACKET_VERSION:
        keys += ('material_evidence',)
    _exact(packet, keys)
    if version not in ('mapped-synthetic-action-v1',*AD_MATERIAL_VERSIONS,'mapped-synthetic-action-v3','mapped-synthetic-action-v4'):
        fail('MAPPED_PACKET_SCHEMA')
    baseline=_baseline(packet)
    review=review_snapshots(packet['draft'],baseline,clock=lambda:aware_time(packet['bundle']['ended_at']))
    if version!=MATERIAL_PACKET_VERSION and review['review_sha256']!=packet['review_sha256']:
        fail('MAPPED_REVIEW_CHANGED')
    known_blockers = SIM_BLOCKERS
    if PROPOSAL_PACKET_REQUIRED in review['blockers']:
        if version!=MATERIAL_PACKET_VERSION: fail(PROPOSAL_PACKET_REQUIRED)
        known_blockers = SIM_BLOCKERS | {PROPOSAL_PACKET_REQUIRED}
    if review['missing_fields'] or set(review['blockers'])-known_blockers: fail('MAPPED_PROFILE_BLOCKED')
    if version in AD_MATERIAL_VERSIONS:
        material=packet['material']
        if packet['draft']['operation']=='CREATE_AD':
            validate_material(material)
            if material['source_mode']!='SYNTHETIC': fail('MAPPED_SYNTHETIC_MATERIAL_ONLY')
            if (material['account_id'],material['creative_id'],material['page_id'])!=(packet['draft']['account_id'],packet['draft']['parameters']['creative']['creative_id'],packet['draft']['context']['page_id']): fail('MAPPED_MATERIAL_SCOPE')
            if digest(material)!=packet['draft']['context']['material_sha256']: fail('MAPPED_MATERIAL_CHANGED')
            if material_findings(material,clock=lambda:aware_time(packet['bundle']['ended_at']))[1]: fail('MAPPED_MATERIAL_INCOMPLETE')
        elif material is not None: fail('MAPPED_UNUSED_MATERIAL')
    if version==MATERIAL_PACKET_VERSION:
        if packet['draft']['operation']!='CREATE_AD': fail('MAPPED_MATERIAL_PACKET_AD_REQUIRED')
        evidence=packet['material_evidence']
        validate_stored_material_evidence(evidence,packet['draft'],packet['material'])
        attach_material_packet(review,(deepcopy(evidence),[]))
        if review['review_sha256']!=packet['review_sha256']: fail('MAPPED_REVIEW_CHANGED')
    if version in ('mapped-synthetic-action-v3', 'mapped-synthetic-action-v4'):
        budget = version == 'mapped-synthetic-action-v4'
        matches_operation = is_budget_change(packet['draft']) if budget else is_activation(packet['draft'])
        if not matches_operation or packet['material'] is not None:
            fail('ACTIVATION_UNEXPECTED_PACKET')
        coverage = packet['coverage']
        if not coverage:
            fail('BUDGET_REVIEW_COVERAGE_REQUIRED' if budget else 'ACTIVATION_REVIEW_CHANGED')
        if digest(coverage) != packet['draft']['context']['material_sha256']:
            fail('BUDGET_REVIEW_CHANGED' if budget else 'ACTIVATION_REVIEW_CHANGED')
        validate_coverage(packet['draft'], coverage, lambda: aware_time(packet['bundle']['ended_at']), historical=True)
        if budget:
            match_budget_baseline(coverage, packet['bundle'])
        review['budget_coverage' if budget else 'activation_coverage'] = deepcopy(coverage)
    return review


def _validate_state(data):
    _exact(data,('schema','mode','stopped','actions'))
    if data['schema']!='mapped-actions-ledger-v1' or data['mode']!='MOCK' or type(data['stopped']) is not bool:
        fail('MAPPED_LEDGER_SCHEMA')
    if type(data['actions']) is not dict or len(data['actions'])>20 or len(canonical(data).encode())>1500000:
        fail('MAPPED_LEDGER_LIMIT')
    for action,row in data['actions'].items():
        _action(action)
        _exact(row,('packet','state','approval','execution_id','recheck','receipt','last_error','updated_at'))
        review=packet_review(row['packet']); aware_time(row['updated_at'])
        if row['state'] not in STATES: fail('MAPPED_LEDGER_STATE')
        if row['approval'] is not None:
            approved,_=validate_approval_reference(row['approval'],review['review_sha256'],aware_time(row['packet']['bundle']['ended_at']))
            if approved-aware_time(row['packet']['bundle']['started_at'])>timedelta(minutes=5): fail('MAPPED_APPROVAL_STALE_EVIDENCE')
        if row['state'] in ('APPROVED_SIM','RECHECKING','UNKNOWN','VERIFIED_SIM') and row['approval'] is None:
            fail('MAPPED_APPROVAL_MISSING')
        if row['execution_id'] is not None and (type(row['execution_id']) is not str or not re.fullmatch('SIM_EXEC_[A-F0-9]{32}',row['execution_id'])):
            fail('MAPPED_EXECUTION_ID')
        if row['state'] in ('RECHECKING','UNKNOWN','VERIFIED_SIM') and row['execution_id'] is None: fail('MAPPED_EXECUTION_ID')
        if row['last_error'] not in (None,'RECHECK_FAILED','STATE_CHANGED_REAPPROVAL_REQUIRED','RECHECK_INTERRUPTED',
                'WRITE_OUTCOME_UNKNOWN','READBACK_UNVERIFIED','APPROVAL_WINDOW_INVALID','STOPPED_OR_REVOKED',
                'MATERIAL_RECHECK_FAILED','BUDGET_SCOPE_REAPPROVAL_REQUIRED',DUPLICATE_CREATE_REQUEST,
                MATERIAL_COMMIT_RECHECK_FAILED):
            fail('MAPPED_ERROR_CODE')
        if row['recheck'] is not None:
            _exact(row['recheck'],('review_sha256','recheck_sha256','after_bundle_sha256','after_state_sha256','checked_at','status','binding'))
            for key in ('review_sha256','recheck_sha256','after_bundle_sha256','after_state_sha256'): _hash(row['recheck'][key])
            if row['recheck']['review_sha256']!=review['review_sha256'] or row['recheck']['status'] not in ('RECHECK_MATCHED_EXECUTION_BLOCKED','REAPPROVAL_REQUIRED'):
                fail('MAPPED_RECHECK_BINDING')
            aware_time(row['recheck']['checked_at'])
            binding=row['recheck']['binding']
            fields=('review_sha256','draft_sha256','before_bundle_sha256','after_bundle_sha256','before_state_sha256',
                    'after_state_sha256','current_review_sha256','changes','decision','profile_error','checked_at')
            if row['packet']['schema']==MATERIAL_PACKET_VERSION:
                fields+=('material_recheck_findings',)
                if type(binding) is not dict or binding.get('material_recheck_findings') != []:
                    fail('MAPPED_RECHECK_BINDING')
            _exact(binding,fields)
            if digest(binding)!=row['recheck']['recheck_sha256']: fail('MAPPED_RECHECK_HASH')
            expected=dict(review_sha256=review['review_sha256'],draft_sha256=digest(row['packet']['draft']),
                before_bundle_sha256=row['packet']['bundle']['bundle_sha256'],before_state_sha256=review['snapshot_evidence']['state_sha256'],
                after_bundle_sha256=row['recheck']['after_bundle_sha256'],after_state_sha256=row['recheck']['after_state_sha256'],
                checked_at=row['recheck']['checked_at'],decision=row['recheck']['status'])
            if any(binding[k]!=v for k,v in expected.items()): fail('MAPPED_RECHECK_BINDING')
            if type(binding['changes']) is not list or len(binding['changes'])>200: fail('MAPPED_RECHECK_DIFF')
            for change in binding['changes']:
                _exact(change,('path','before_present','after_present','before','after'))
                if type(change['path']) is not str or not change['path'].startswith('/') or any(type(change[k]) is not bool for k in ('before_present','after_present')):
                    fail('MAPPED_RECHECK_DIFF')
            if binding['current_review_sha256'] is not None: _hash(binding['current_review_sha256'])
            if binding['profile_error'] not in (None,'RECHECK_CURRENT_PROFILE_UNSUPPORTED'): fail('MAPPED_RECHECK_PROFILE')
            matched=not binding['changes'] and binding['profile_error'] is None
            if matched!=(binding['decision']=='RECHECK_MATCHED_EXECUTION_BLOCKED'): fail('MAPPED_RECHECK_DECISION')
            if matched and (binding['before_state_sha256']!=binding['after_state_sha256'] or binding['current_review_sha256'] is None):
                fail('MAPPED_RECHECK_STATE')
        if row['state'] in ('UNKNOWN','VERIFIED_SIM') and (row['recheck'] is None or row['recheck']['status']!='RECHECK_MATCHED_EXECUTION_BLOCKED'):
            fail('MAPPED_RECHECK_MISSING')
        if row['receipt'] is not None: validate_receipt(row['receipt'],row)
        if (row['state']=='VERIFIED_SIM') != (row['receipt'] is not None): fail('MAPPED_RECEIPT_STATE')


def validate_receipt(value,row):
    _exact(value,('schema','execution_id','review_sha256','request_sha256','account_id','object_id',
                  'operation','parameters','configured_status','after_data_sha256','after_configuration_sha256'))
    review=packet_review(row['packet']); draft=row['packet']['draft']; op=draft['operation']
    if value['schema']!='mapped-synthetic-receipt-v1': fail('MAPPED_RECEIPT_SCHEMA')
    expected=dict(execution_id=row['execution_id'],review_sha256=review['review_sha256'],
        request_sha256=digest(review['approval_binding']['request']),account_id=draft['account_id'],
        operation=op,parameters=draft['parameters'],configured_status=draft['parameters'].get('status') or review['draft']['before']['configured_status'])
    if any(value[k]!=v for k,v in expected.items()): fail('MAPPED_RECEIPT_BINDING')
    if type(value['object_id']) is not str or not re.fullmatch('[1-9][0-9]{0,39}',value['object_id']): fail('MAPPED_RECEIPT_OBJECT')
    if not op.startswith('CREATE_') and value['object_id']!=draft['target_id']: fail('MAPPED_RECEIPT_OBJECT')
    for key in ('after_data_sha256','after_configuration_sha256'): _hash(value[key])


class MappedWriteTransport:
    """The independent write/readback interface; no network implementation."""
    def preflight(self,packet): fail('MAPPED_WRITE_TRANSPORT_UNAVAILABLE')
    def snapshot_factory(self,plan): fail('MAPPED_WRITE_TRANSPORT_UNAVAILABLE')
    def send(self,packet,execution_id,*,before_commit=None): fail('MAPPED_WRITE_TRANSPORT_UNAVAILABLE')
    def readback(self,packet,execution_id): fail('MAPPED_WRITE_TRANSPORT_UNAVAILABLE')
    def budget_inventory(self,draft,allowed): fail('MAPPED_WRITE_TRANSPORT_UNAVAILABLE')
    def activation_inventory(self,draft,allowed): fail('MAPPED_WRITE_TRANSPORT_UNAVAILABLE')
    def material_content(self,creative_id): fail('MAPPED_WRITE_TRANSPORT_UNAVAILABLE')


class _LedgerVerifier(ApprovalVerifier):
    def __init__(self,ledger,action,execution): self.ledger=ledger;self.action=action;self.execution=execution
    def verify(self,review,reference,at):
        with self.ledger.journal.locked(wait_seconds=.5):
            data=self.ledger._load();row=self.ledger._row(data,self.action)
            if (data['stopped'] or row['state']!='RECHECKING' or row['execution_id']!=self.execution
                    or row['approval']!=reference or row['packet']['review_sha256']!=review['review_sha256']):
                fail('MAPPED_APPROVAL_NOT_CURRENT')
            self.ledger._window(row,aware_time(at))
            self.ledger._material_window(row['packet'],aware_time(at))


class MappedActionLedger:
    def __init__(self,directory,transport=None,*,simulation=False,material_root=None,material_packet_directory=None,
                 clock=utcnow,monotonic=time.monotonic,existing_only=False):
        from .mapped_service import SyntheticMappedService
        self.journal=Journal(directory,existing_only=existing_only)
        self.owner=Journal(self.journal.directory/'execution-owner',existing_only=existing_only)
        self.existing_only=existing_only
        self.transport=transport if transport is not None else MappedWriteTransport()
        if type(self.transport) not in (MappedWriteTransport,SyntheticMappedService): fail('MAPPED_UNREVIEWED_TRANSPORT')
        if type(simulation) is not bool: fail('MAPPED_SIMULATION_FLAG')
        if type(self.transport) is SyntheticMappedService:
            a=self.journal.directory;b=self.transport.journal.directory
            if a==b or a.is_relative_to(b) or b.is_relative_to(a): fail('MAPPED_JOURNALS_MUST_BE_SEPARATE')
        self.simulation=simulation;self.clock=clock;self.monotonic=monotonic;self.material_root=material_root
        self.material_packet_directory=material_packet_directory

    @staticmethod
    def _material_window(packet,now):
        if is_activation(packet['draft']):
            if packet['schema'] != 'mapped-synthetic-action-v3':
                fail('ACTIVATION_LEGACY_REPROPOSAL_REQUIRED')
            validate_coverage(packet['draft'], packet['coverage'], lambda: now)
            return
        if is_budget_change(packet['draft']):
            if packet['schema'] != 'mapped-synthetic-action-v4':
                fail('BUDGET_LEGACY_REPROPOSAL_REQUIRED')
            validate_coverage(packet['draft'], packet['coverage'], lambda: now)
            return
        if packet['draft']['operation']!='CREATE_AD': return
        if packet['schema'] not in AD_MATERIAL_VERSIONS: fail('MAPPED_LEGACY_AD_REPROPOSAL_REQUIRED')
        if material_findings(packet['material'],clock=lambda:now)[1]: fail('MAPPED_MATERIAL_INCOMPLETE')

    def _check_local_material(self,packet):
        # The final commit callback runs with the service journal already locked.
        # Keep this check independent of service inventory and content reads.
        draft = packet['draft']
        if is_activation(draft) or is_budget_change(draft):
            for item in packet['coverage']['materials'].values():
                check_media_files(item, self.material_root)
            return
        if draft['operation']!='CREATE_AD': return
        if packet['schema']==MATERIAL_PACKET_VERSION:
            if self.material_packet_directory is None: fail('MAPPED_MATERIAL_PACKET_REQUIRED')
            observed,findings=load_material_packet(self.material_packet_directory,draft,clock=self.clock)
            if findings or observed!=packet['material_evidence']: fail('MAPPED_MATERIAL_PACKET_CHANGED')
        else: check_media_files(packet['material'],self.material_root)

    def _check_material(self,packet,*,check_budget_scope=True):
        self._material_window(packet,self.clock())
        draft = packet['draft']
        allowed = packet['bundle']['allowlist']
        if is_activation(draft):
            expected = packet['coverage']['inventory']
            if self.transport.activation_inventory(draft,allowed)!=expected:
                fail('ACTIVATION_INVENTORY_CHANGED')
            self._check_local_material(packet)
            if self.transport.activation_inventory(draft,allowed)!=expected:
                fail('ACTIVATION_INVENTORY_CHANGED')
        elif is_budget_change(draft):
            self._check_local_material(packet)
            if check_budget_scope:
                try:
                    observed = self.transport.budget_inventory(draft,allowed)
                except InputError:
                    fail('BUDGET_SCOPE_REAPPROVAL_REQUIRED')
                if observed!=packet['coverage']['inventory']:
                    fail('BUDGET_SCOPE_REAPPROVAL_REQUIRED')
        elif draft['operation']=='CREATE_AD':
            self._check_local_material(packet)
            material = packet['material']
            if self.transport.material_content(material['creative_id'])!=material['creative']:
                fail('MAPPED_MATERIAL_SOURCE_CHANGED')
        else:
            return
        self._material_window(packet,self.clock())

    def _load(self):
        data,damaged=self.journal.load(_validate_state)
        if damaged: fail('MAPPED_LEDGER_RECOVERY_REQUIRED')
        return data or dict(schema='mapped-actions-ledger-v1',mode='MOCK',stopped=False,actions={})

    @staticmethod
    def _row(data,action):
        _action(action)
        if action not in data['actions']: fail('MAPPED_ACTION_NOT_FOUND')
        return data['actions'][action]

    def _stamp(self,row=None):
        now=self.clock();aware_time(now.isoformat())
        if row is not None and now<aware_time(row['updated_at']): fail('MAPPED_CLOCK_ROLLBACK')
        return now

    @staticmethod
    def _window(row,now):
        ref=row['approval']
        if ref is None or not aware_time(ref['approved_at'])<=now<aware_time(ref['expires_at']): fail('MAPPED_APPROVAL_WINDOW')

    def _require_dispatch_window(self,row,checked_at,started):
        now=self._stamp(row)
        self._window(row,now)
        if not timedelta(0)<=now-checked_at<=timedelta(seconds=5) or not 0<=self.monotonic()-started<=65:
            fail('MAPPED_DISPATCH_WINDOW')

    def _save(self,data,row=None):
        if row is not None: row['updated_at']=self._stamp(row).isoformat()
        self.journal.save(data,_validate_state)

    @staticmethod
    def _result(row):
        result=dict(state=row['state'],review_sha256=row['packet']['review_sha256'],execution_id=row['execution_id'],
            recheck=deepcopy(row['recheck']),receipt=deepcopy(row['receipt']),last_error=row['last_error'],
            mode='MOCK',human_identity_verified=False,real_write_authorized=False,real_meta_requests=0)
        if row['packet']['schema']==MATERIAL_PACKET_VERSION:
            result['material_archive_receipt_sha256']=row['packet']['material_evidence']['receipt_sha256']
        return result

    def submit(self,action,draft,baseline,*,material=None,coverage=None):
        _action(action)
        if type(baseline) is not CapturedSnapshots: fail('MAPPED_IN_PROCESS_SYNTHETIC_REQUIRED')
        review=review_snapshots(draft,baseline,clock=self.clock)
        if review['snapshot_evidence']['provenance']!='IN_PROCESS_SYNTHETIC': fail('MAPPED_SYNTHETIC_BASELINE_ONLY')
        packet=dict(schema='mapped-synthetic-action-v2',draft=_copy(draft),bundle=strict_json(baseline.payload),review_sha256=review['review_sha256'],material=_copy(material))
        if is_activation(draft):
            packet.update(schema='mapped-synthetic-action-v3', coverage=_copy(coverage))
        elif is_budget_change(draft):
            packet.update(schema='mapped-synthetic-action-v4', coverage=_copy(coverage))
        elif coverage is not None:
            fail('ACTIVATION_UNUSED_COVERAGE')
        if self.material_packet_directory is not None and draft['operation']=='CREATE_AD':
            validate_material(material)
            if material['source_mode']!='SYNTHETIC': fail('MAPPED_SYNTHETIC_MATERIAL_ONLY')
            evidence,findings=load_material_packet(self.material_packet_directory,draft,clock=self.clock)
            if findings: fail('MAPPED_MATERIAL_INCOMPLETE')
            attach_material_packet(review,(evidence,findings))
            packet.update(schema=MATERIAL_PACKET_VERSION,material_evidence=evidence,review_sha256=review['review_sha256'])
        review = packet_review(packet)
        self._check_material(packet)
        with self.journal.locked():
            data=self._load()
            if data['stopped']: fail('MAPPED_STOPPED')
            if action in data['actions']: fail('MAPPED_ACTION_ALREADY_EXISTS')
            for old in data['actions'].values():
                if old['state']=='UNKNOWN' and old['packet']['draft']['account_id']==draft['account_id']: fail('MAPPED_ACCOUNT_HAS_UNKNOWN_ACTION')
                if old['state'] in ('INVALIDATED','REVOKED_SIM'): continue
                if digest(old['packet'])==digest(packet): fail('MAPPED_DUPLICATE_REVIEW')
                if same_create_request(packet['draft'],old['packet']['draft']):
                    fail(DUPLICATE_CREATE_REQUEST)
            data['actions'][action]=dict(packet=packet,state='PROPOSED',approval=None,execution_id=None,recheck=None,receipt=None,
                                        last_error=None,updated_at=self._stamp().isoformat())
            self._save(data);return deepcopy(review)

    def approve_simulated(self,action,expected_review_sha256,expires_at):
        if not self.simulation: fail('MAPPED_REAL_APPROVAL_PROVIDER_UNAVAILABLE')
        with self.journal.locked():
            data=self._load();original=deepcopy(self._row(data,action))
            if (original['packet']['schema']=='mapped-synthetic-action-v4'
                    and (data['stopped'] or original['state']!='PROPOSED')):
                fail('MAPPED_APPROVAL_NOT_AVAILABLE')
        self._check_material(original['packet'])
        with self.journal.locked():
            data=self._load();row=self._row(data,action);now=self._stamp(row)
            if row!=original: fail('MAPPED_ACTION_CONFLICT')
            if data['stopped'] or row['state']!='PROPOSED': fail('MAPPED_APPROVAL_NOT_AVAILABLE')
            if expected_review_sha256!=row['packet']['review_sha256']: fail('MAPPED_REVIEW_CHANGED')
            reference=dict(schema='write-recheck-approval-reference-v1',review_sha256=expected_review_sha256,
                approval_reference='APPROVAL_'+uuid4().hex.upper(),approved_at=now.isoformat(),expires_at=expires_at)
            validate_approval_reference(reference,expected_review_sha256,aware_time(row['packet']['bundle']['ended_at']))
            if now-aware_time(row['packet']['bundle']['started_at'])>timedelta(minutes=5): fail('MAPPED_APPROVAL_STALE_EVIDENCE')
            row.update(approval=reference,state='APPROVED_SIM');self._save(data,row);return deepcopy(reference)

    def inspect(self,action):
        with self.journal.locked(existing_only=self.existing_only): return deepcopy(self._row(self._load(),action))

    def _status_view(self,data):
        return dict(mode='MOCK',stopped=data['stopped'],actions={k:self._result(v) for k,v in data['actions'].items()},
                    human_identity_verified=False,real_write_authorized=False)

    def status(self):
        with self.journal.locked():
            return self._status_view(self._load())

    def recorded_exit_actions(self):
        """Inspect persisted simulation states using only the existing state lock."""
        with self.journal.locked(existing_only=True):
            if not self.journal.snapshots():
                fail('MAPPED_LEDGER_SNAPSHOT_REQUIRED')
            data = self._load()
            actions = [dict(action_id=action,state=row['state'],execution_id=row['execution_id'],
                            last_error=row['last_error']) for action,row in sorted(data['actions'].items())]
            return dict(stopped=data['stopped'],actions=actions)

    def revoke(self,action):
        with self.journal.locked():
            data=self._load();row=self._row(data,action)
            if row['state'] in ('UNKNOWN','VERIFIED_SIM'): fail('MAPPED_EXECUTION_ALREADY_STARTED')
            row.update(state='REVOKED_SIM',last_error='STOPPED_OR_REVOKED');self._save(data,row)

    def stop(self):
        with self.journal.locked():
            data=self._load();data['stopped']=True
            for row in data['actions'].values():
                if row['state'] in ('PROPOSED','APPROVED_SIM','RECHECKING'):
                    row.update(state='REVOKED_SIM',last_error='STOPPED_OR_REVOKED',updated_at=self._stamp(row).isoformat())
            self._save(data)
            return self._status_view(data)

    def _invalidate(self,action,error):
        with self.journal.locked(wait_seconds=.5):
            data=self._load();row=self._row(data,action)
            if row['state']=='RECHECKING':
                row.update(state='INVALIDATED',last_error=error);self._save(data,row)
            return self._result(row)

    def execute_simulated(self,action,*,failure=None,before_request=None):
        if failure not in (None,'BEFORE_SEND','AFTER_SEND'): fail('MAPPED_FAILURE_MODE')
        with self.owner.locked():
            with self.journal.locked():
                data=self._load();row=self._row(data,action)
                if row['state'] in ('UNKNOWN','VERIFIED_SIM'): return self._result(row)
                if row['state']=='RECHECKING':
                    row.update(state='INVALIDATED',last_error='RECHECK_INTERRUPTED');self._save(data,row);return self._result(row)
                if data['stopped'] or row['state']!='APPROVED_SIM': fail('MAPPED_APPROVAL_NOT_CURRENT')
                if not self.simulation: fail('MAPPED_REAL_APPROVAL_PROVIDER_UNAVAILABLE')
                if any(x['state']=='UNKNOWN' and x['packet']['draft']['account_id']==row['packet']['draft']['account_id'] for x in data['actions'].values()):
                    fail('MAPPED_ACCOUNT_HAS_UNKNOWN_ACTION')
                now=self._stamp(row)
                try: self._window(row,now)
                except InputError:
                    row.update(state='INVALIDATED',last_error='APPROVAL_WINDOW_INVALID');self._save(data,row);return self._result(row)
                try: self.transport.preflight(row['packet'])
                except InputError as exc:
                    if str(exc)!=DUPLICATE_CREATE_REQUEST: raise
                    row.update(state='INVALIDATED',last_error=DUPLICATE_CREATE_REQUEST)
                    self._save(data,row);return self._result(row)
                row.update(state='RECHECKING',execution_id='SIM_EXEC_'+uuid4().hex.upper())
                self._save(data,row);packet=deepcopy(row['packet']);reference=deepcopy(row['approval']);execution=row['execution_id']
            tick=self.monotonic()
            try: self._check_material(packet,check_budget_scope=False)
            except InputError as exc:
                error = 'BUDGET_SCOPE_REAPPROVAL_REQUIRED' if str(exc)=='BUDGET_LEGACY_REPROPOSAL_REQUIRED' else 'MATERIAL_RECHECK_FAILED'
                return self._invalidate(action,error)
            except Exception: return self._invalidate(action,'MATERIAL_RECHECK_FAILED')
            try:
                report=collect_approved_recheck(packet['draft'],_baseline(packet),reference,self.transport.snapshot_factory,
                    verifier=_LedgerVerifier(self,action,execution),clock=self.clock,monotonic=self.monotonic,
                    before_request=before_request,synthetic_material_evidence=packet.get('material_evidence'))
            except Exception: return self._invalidate(action,'RECHECK_FAILED')
            if not is_budget_change(packet['draft']) or report['status']=='RECHECK_MATCHED_EXECUTION_BLOCKED':
                try: self._check_material(packet)
                except InputError as exc:
                    error = 'BUDGET_SCOPE_REAPPROVAL_REQUIRED' if str(exc)=='BUDGET_SCOPE_REAPPROVAL_REQUIRED' else 'MATERIAL_RECHECK_FAILED'
                    return self._invalidate(action,error)
                except Exception: return self._invalidate(action,'MATERIAL_RECHECK_FAILED')
            checked_at=aware_time(report['binding']['checked_at'])
            with self.journal.locked():
                data=self._load();row=self._row(data,action)
                if data['stopped'] or row['state']!='RECHECKING' or row['execution_id']!=execution: return self._result(row)
                row['recheck']=dict(review_sha256=packet['review_sha256'],recheck_sha256=report['recheck_sha256'],
                    after_bundle_sha256=report['binding']['after_bundle_sha256'],after_state_sha256=report['binding']['after_state_sha256'],
                    checked_at=report['binding']['checked_at'],status=report['status'],binding=deepcopy(report['binding']))
                if report['status']!='RECHECK_MATCHED_EXECUTION_BLOCKED':
                    row.update(state='INVALIDATED',last_error='STATE_CHANGED_REAPPROVAL_REQUIRED');self._save(data,row);return self._result(row)
                try:
                    self._require_dispatch_window(row,checked_at,tick)
                except InputError:
                    row.update(state='INVALIDATED',last_error='APPROVAL_WINDOW_INVALID');self._save(data,row);return self._result(row)
                row['state']='UNKNOWN';self._save(data,row)
            if failure=='BEFORE_SEND': return self._result(row)
            try: self._check_material(packet)
            except Exception:
                with self.journal.locked():
                    data=self._load();row=self._row(data,action);row['last_error']='WRITE_OUTCOME_UNKNOWN';self._save(data,row)
                    return self._result(row)
            # UNKNOWN is already durably consumed. A slow fsync / concurrent
            # stop may prevent dispatch, but must never restore the approval.
            with self.journal.locked():
                data=self._load();row=self._row(data,action)
                def check_window():
                    self._require_dispatch_window(row,checked_at,tick)

                def check_commit():
                    check_window()
                    try:
                        self._check_local_material(packet)
                        self._material_window(packet,self.clock())
                    except (OSError,ValueError,TypeError,KeyError):
                        fail(MATERIAL_COMMIT_RECHECK_FAILED)
                    check_window()

                try:
                    if data['stopped'] or row['state']!='UNKNOWN' or row['execution_id']!=execution:
                        fail('MAPPED_STOPPED_OR_CHANGED')
                    check_window()
                except InputError:
                    row['last_error']='WRITE_OUTCOME_UNKNOWN';self._save(data,row);return self._result(row)
                # The local simulator commits under its own journal lock.
                # Keep the action ledger locked so stop cannot acknowledge before that commit.
                try: self.transport.send(packet,execution,before_commit=check_commit)
                except Exception as exc:
                    code = str(exc) if type(exc) is InputError else None
                    if code in ('MAPPED_APPROVAL_WINDOW','MAPPED_DISPATCH_WINDOW'):
                        row['last_error']='APPROVAL_WINDOW_INVALID'
                    elif code==MATERIAL_COMMIT_RECHECK_FAILED:
                        row['last_error']=code
                    else:
                        row['last_error']='WRITE_OUTCOME_UNKNOWN'
                    self._save(data,row)
                    return self._result(row)
            if failure=='AFTER_SEND': return self._result(row)
            return self._reconcile(action)

    def _reconcile(self,action):
        with self.journal.locked(existing_only=self.existing_only):
            data=self._load();row=deepcopy(self._row(data,action))
            if row['state']=='VERIFIED_SIM' and not self.existing_only: return self._result(row)
            if row['state'] not in ('UNKNOWN','VERIFIED_SIM'): fail('MAPPED_NOTHING_TO_RECONCILE')
        readback_reason=None
        try:
            receipt=self.transport.readback(row['packet'],row['execution_id'])
            if receipt is None: fail('MAPPED_NO_CORRELATED_RECEIPT')
        except InputError as exc:
            receipt=None
            code=str(exc)
            if code=='NO_VALID_STATE': code='MAPPED_SERVICE_RECOVERY_REQUIRED'
            readback_reason=code if code in READBACK_SERVICE_ERRORS else 'MAPPED_SERVICE_READBACK_UNAVAILABLE'
        except Exception:
            receipt=None
            readback_reason='MAPPED_SERVICE_READBACK_UNAVAILABLE'
        else:
            try: validate_receipt(receipt,row)
            except (ValueError,TypeError,KeyError):
                receipt=None
                readback_reason='MAPPED_SERVICE_RECEIPT_CONFLICT'
        with self.journal.locked(existing_only=self.existing_only):
            data=self._load();current=self._row(data,action)
            if current['state']!=row['state'] or current['execution_id']!=row['execution_id']: fail('MAPPED_ACTION_CONFLICT')
            if self.existing_only and (current['state']=='VERIFIED_SIM' or receipt is None):
                if receipt is not None and receipt==current['receipt']:
                    return dict(self._result(current),readback_outcome='VERIFIED_SIM')
                return dict(self._result(current),readback_outcome='UNVERIFIED',
                            readback_reason=readback_reason or 'MAPPED_SERVICE_RECEIPT_CONFLICT')
            if receipt is None:
                if current['last_error']=='READBACK_UNVERIFIED': return self._result(current)
                current['last_error']='READBACK_UNVERIFIED'
            else: current.update(state='VERIFIED_SIM',receipt=_copy(receipt),last_error=None)
            self._save(data,current)
            result=self._result(current)
            if self.existing_only: result['readback_outcome']='VERIFIED_SIM'
            return result

    def reconcile(self,action):
        with self.owner.locked(existing_only=self.existing_only): return self._reconcile(action)


def reconcile_saved_simulation(state_directory, service_directory, action):
    from .mapped_service import SyntheticMappedService

    service = SyntheticMappedService(service_directory,existing_only=True)
    ledger = MappedActionLedger(state_directory,service,existing_only=True)
    if ledger.inspect(action)['state'] not in ('UNKNOWN','VERIFIED_SIM'):
        fail('MAPPED_NOTHING_TO_RECONCILE')
    return dict(ledger.reconcile(action),action_id=action,readback_only=True)
