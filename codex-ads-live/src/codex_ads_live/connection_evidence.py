"""Offline readback of a finalized named account or Page connection check."""
from datetime import timedelta
from hashlib import sha256
import re

from .connection import (CHECK_ERRORS, connection_plan, connection_requests, recorded_ads_scope_is_minimal,
                         valid_scope_name, validate_intent)
from .credential_record import public_record
from .journal import digest
from .local_paths import checked_path
from .read_adapter import normalize_row
from .validation import InputError, aware_time, fail, object_only, strict_json


LIMITS = {'PLAN.json':32000,'CONSENT.json':4000,'RESULT.json':32000,'ATTEMPT-1.json':4000,'ATTEMPT-2.json':4000}
SCOPE_ERROR = 'CONNECTION_EVIDENCE_SCOPE_MISMATCH'
TIME_ERROR = 'CONNECTION_EVIDENCE_TIMING_MISMATCH'
READ_ERROR = 'CONNECTION_EVIDENCE_OUTCOME_MISMATCH'


def _read(root, name, limit):
    path = checked_path(root/name,link_error='CONNECTION_EVIDENCE_LINK',volume_error='CONNECTION_EVIDENCE_VOLUME')
    if not path.is_file():
        fail('CONNECTION_EVIDENCE_FILE_REQUIRED')
    with path.open('rb') as stream:
        raw = stream.read(limit+1)
    if len(raw)>limit:
        fail('CONNECTION_EVIDENCE_FILE_TOO_LARGE')
    return raw


def _load_run(root):
    raw = _read(root,'RECEIPT.json',16000)
    receipt = strict_json(raw.decode('utf-8'))
    fixed = ('PLAN.json','CONSENT.json','RESULT.json')
    if (type(receipt) is not dict or type(receipt.get('files')) is not list or not 3<=len(receipt['files'])<=5
            or any(receipt.get(key) is not False for key in ('live_action_authorized','credential_material_in_artifacts',
                                                              'authorization_reusable'))):
        fail('CONNECTION_EVIDENCE_RECEIPT_INVALID')
    files = {}
    for entry in receipt['files']:
        object_only(entry,('path','bytes','sha256'),('path','bytes','sha256'))
        name = entry['path']
        if type(name) is not str or name not in LIMITS or name in files:
            fail('CONNECTION_EVIDENCE_RECEIPT_INVALID')
        content = _read(root,name,LIMITS[name])
        if type(entry['bytes']) is not int or entry['bytes']!=len(content) or entry['sha256']!=sha256(content).hexdigest():
            fail('CONNECTION_EVIDENCE_FILE_HASH_MISMATCH')
        value = strict_json(content.decode('utf-8'))
        if type(value) is not dict:
            fail('CONNECTION_EVIDENCE_RECEIPT_INVALID')
        files[name] = value
    if not set(fixed)<=files.keys():
        fail('CONNECTION_EVIDENCE_RECEIPT_INVALID')
    return receipt, sha256(raw).hexdigest(), files


def _scope(plan, consent, result, expected):
    intent = plan.get('intent')
    page = type(intent) is dict and intent.get('schema')=='page-connection-intent-v1'
    try:
        validate_intent(intent,page=page)
    except InputError:
        fail(SCOPE_ERROR)
    intent_hash = digest(intent)
    if expected is not None and intent_hash!=expected:
        fail('CONNECTION_EVIDENCE_EXPECTED_SCOPE_MISMATCH')
    native = intent['credential_source']=='OS_REFERENCE'
    if (plan.get('schema')!='connection-preview-v1' or plan.get('status')!='READY_FOR_LOCAL_SCOPE_CONFIRMATION'
            or plan.get('blockers')!=[] or type(plan.get('maximum_get_attempts')) is not int
            or plan['maximum_get_attempts']!=2 or plan.get('requests')!=connection_requests(intent,page)
            or any(item.get('intent_sha256')!=intent_hash for item in (plan,consent,result))
            or plan.get('native_access') is not native or not intent['asset_owner_confirmed']
            or not intent['app_developer_confirmed'] or intent['appsecret_proof_requirement']!='NOT_REQUIRED_CONFIRMED'
            or any(plan.get(key) is not False for key in ('credential_storage_write','oauth_or_refresh','live_read','writes',
                                                          'real_connection_verified','metadata_flags_are_grant_proof'))
            or consent.get('method')!='LOCAL_TERMINAL_SCOPE_CONFIRMATION'
            or consent.get('independently_authenticated_human') is not False
            or consent.get('remote_write_authorized') is not False):
        fail(SCOPE_ERROR)
    record = plan.get('credential_record')
    if native:
        if type(record) is not dict or type(record.get('record')) is not dict:
            fail(SCOPE_ERROR)
        metadata = record['record']
        if (record!=public_record(metadata) or metadata['state']!='PREPARED'
                or (metadata['app_id'],metadata['principal_id'],metadata['reference']) !=
                (intent['app_id'],intent['principal_id'],intent['credential_reference'])):
            fail(SCOPE_ERROR)
    if (not native and record is not None or result.get('credential_record_sha256') !=
            (record['record_sha256'] if native else None)):
        fail(SCOPE_ERROR)
    return intent,page,intent_hash


def _attempts(files, consent, result, kind, success):
    count = result.get('get_attempts_reserved')
    if type(count) is not int or not 0<=count<=2:
        fail(READ_ERROR)
    names = ('ATTEMPT-1.json','ATTEMPT-2.json')
    if tuple(name for name in names if name in files)!=names[:count]:
        fail(READ_ERROR)
    approved, deadline = (aware_time(consent.get(key)) for key in ('observed_at','expires_at'))
    if deadline-approved>timedelta(minutes=5) or success and deadline<=approved:
        fail(TIME_ERROR)
    stamps = []
    for name, stage in zip(names[:count],('DEBUG_TOKEN',f'NAMED_{kind}_GET')):
        marker = files[name]
        if marker.get('stage')!=stage or marker.get('may_have_reached_server') is not True:
            fail(SCOPE_ERROR)
        stamp = aware_time(marker.get('reserved_at'))
        if success and not (stamps[-1] if stamps else approved)<=stamp<deadline:
            fail(TIME_ERROR)
        stamps.append(stamp)
    return stamps,deadline


def _confirmed_metadata(metadata, intent, page, stamps, deadline, success):
    if type(metadata) is not dict or not stamps:
        fail(READ_ERROR)
    scopes = metadata.get('scopes')
    permission = 'pages_read_engagement' if page else 'ads_read'
    if (metadata.get('app_id')!=intent['app_id'] or metadata.get('principal_id')!=intent['principal_id']
            or type(scopes) is not list or not 1<=len(scopes)<=64
            or not all(map(valid_scope_name, scopes))
            or scopes!=sorted(set(scopes)) or permission not in scopes):
        fail(SCOPE_ERROR)
    if page and (scopes!=[permission] or metadata.get('page_grant_binding') not in
                 ('NAMED_PAGE_IN_DEBUG_TARGETS','NO_EXPLICIT_PAGE_TARGET_LIST')):
        fail(SCOPE_ERROR)
    if not page and metadata.get('reported_scopes_exactly_ads_read') is not (scopes==['ads_read']):
        fail(SCOPE_ERROR)
    if not page and 'granular_scope_observation' in metadata:
        granular = metadata['granular_scope_observation']
        if type(granular) is not dict or set(granular) != {'state','names'}:
            fail(SCOPE_ERROR)
        names = granular['names']
        if granular['state'] == 'REPORTED_NAMES':
            if (type(names) is not list or len(names) > 64 or not all(map(valid_scope_name, names))
                    or names != sorted(set(names))):
                fail(SCOPE_ERROR)
        elif granular['state'] not in ('NOT_RETURNED','UNVERIFIED') or names is not None:
            fail(SCOPE_ERROR)
    debug_at, effective, token, access = (aware_time(metadata.get(key)) for key in
        ('observed_at','effective_read_expires_at','token_expires_at','data_access_expires_at'))
    if (debug_at>=effective or effective!=min(deadline,token,access)
            or success and (len(stamps)!=2 or not stamps[0]<=debug_at<=stamps[1]<effective)):
        fail(TIME_ERROR)
    return effective


def review_connection(directory, *, expected_intent_sha256=None):
    if expected_intent_sha256 is not None and (type(expected_intent_sha256) is not str
            or not re.fullmatch('[a-f0-9]{64}',expected_intent_sha256)):
        fail('CONNECTION_EVIDENCE_EXPECTED_DIGEST_INVALID')
    root = checked_path(directory,link_error='CONNECTION_EVIDENCE_LINK',volume_error='CONNECTION_EVIDENCE_VOLUME')
    receipt, receipt_hash, files = _load_run(root)
    plan, consent, result = (files[name] for name in ('PLAN.json','CONSENT.json','RESULT.json'))
    intent,page,intent_hash = _scope(plan,consent,result,expected_intent_sha256)
    kind = 'PAGE' if page else 'ACCOUNT'
    row_key = kind.lower()
    status, error = result.get('status'), result.get('error_code')
    success = status==f'NAMED_{kind}_READ_PASSED'
    native = intent['credential_source']=='OS_REFERENCE'
    attempted = result.get('native_access_attempted')
    if (receipt.get('status')!=status or result.get(f'real_{row_key}_read_verified') is not success
            or result.get('scope')!=f'THIS_SINGLE_{kind}_CHECK_ONLY_NOT_REUSABLE_AUTHORIZATION'
            or result.get('request_count_semantics')!='RESERVED_ATTEMPTS_NOT_PROOF_OF_TRANSMISSION'
            or type(attempted) is not bool or attempted and not native
            or any(result.get(key) is not False for key in ('oauth_performed','credential_stored','live_action_authorized',
                                                            'independent_human_identity_verified','background_monitoring_enabled'))):
        fail(READ_ERROR)
    stamps, deadline = _attempts(files,consent,result,kind,success)
    metadata = result.get('metadata')
    effective = _confirmed_metadata(metadata,intent,page,stamps,deadline,success) if metadata is not None else None
    row = result.get(row_key)
    if success:
        if (len(stamps)!=2 or metadata is None or error is not None or attempted is not native
                or page and result.get('page_role_platform_verified') is not False
                or type(row) is not dict or not page and not {'currency','timezone_name','account_status'}<=row.keys()):
            fail(READ_ERROR)
        try:
            normalized = normalize_row(connection_plan(intent,page),row)
        except InputError:
            fail(READ_ERROR)
        if normalized!=row:
            fail(READ_ERROR)
        if not stamps[1]<=aware_time(result.get(f'{row_key}_observed_at'))<effective:
            fail(TIME_ERROR)
    elif (row is not None or result.get(f'{row_key}_observed_at') is not None or type(error) is not str
          or status=='CONNECTION_CHECK_FAILED' and error not in CHECK_ERRORS
          or status=='INTERRUPTED' and error!='USER_INTERRUPT'
          or status not in ('CONNECTION_CHECK_FAILED','INTERRUPTED')):
        fail(READ_ERROR)
    if len(stamps)==2 and metadata is None:
        fail(READ_ERROR)
    minimal = ('DEBUG_METADATA_AND_THIS_ACCOUNT_GET_ONLY' if success and not page and recorded_ads_scope_is_minimal(metadata)
               else 'NOT_ESTABLISHED')
    legacy = metadata is not None and not page and 'granular_scope_observation' not in metadata
    original_minimal = ('DEBUG_METADATA_AND_THIS_ACCOUNT_GET_ONLY' if legacy and success and metadata['scopes']==['ads_read']
                        else minimal)
    if result.get('minimal_scope_evidence')!=original_minimal:
        fail(READ_ERROR)
    reported_scopes = None
    granular = None
    if metadata is None:
        scope_basis = 'NOT_RECORDED'
    elif page:
        scope_basis = 'NAMED_PAGE_PERMISSION_PROJECTION'
    else:
        scope_basis = 'DEBUG_REPORTED_SCOPES'
        reported_scopes = metadata['scopes']
        granular = dict(state='LEGACY_NOT_RECORDED', names=None) if legacy else metadata['granular_scope_observation']
    scope_evidence = dict(basis=scope_basis, reported_scope_names=reported_scopes, minimal_scope_evidence=minimal,
                          granular_scope_observation=granular)
    return dict(status='CONNECTION_EVIDENCE_CHECKED',asset_kind=kind,recorded_status=status,recorded_error_code=error,
                recorded_named_read=success,intent_sha256=intent_hash,receipt_sha256=receipt_hash,
                get_attempts_reserved=len(stamps),independent_runtime_verified=False,
                authorization_reusable=False,network_used=False,native_credential_accessed=False,
                scope_evidence=scope_evidence)
