"""Persistent non-secret asset selection and task-scoped daily routing."""
from copy import deepcopy
from datetime import timedelta
from functools import partial
from hashlib import sha256
import re
import sys
from uuid import uuid4
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from . import WORKSPACE_RUNTIME_VERSIONS, __version__, business_profile
from .connection import blank_intent, check_connection, load_intent, preview, utcnow, validate_intent
from .credential_record import RecordGuard, read_record
from .http_read import ACCOUNT_SCOPE_ERRORS
from .journal import Journal, digest
from .local_paths import checked_path
from .monitor import MAX_PAGES_PER_SOURCE, NAMED_ACCOUNT_PERMISSION_DENIED
from .monitor_tools import new_directory
from .meta_write_mapping import _write_new
from .preparation_review import load_preparation
from .read_adapter import (CONVERSION_HIERARCHY_INSIGHTS, CONVERSION_INSIGHTS, GRAPH_AUTH_ERRORS, HIERARCHY_INSIGHTS,
                           INSIGHTS_MAX_DAYS, PLACEMENT_INSIGHTS, ReadPlan, positive_integer)
from .timezones import load_zone
from .validation import InputError, aware_time, fail, object_only, strict_json
from .workflow import workspace_followup


TASKS = {'ads-analysis': ('ACCOUNT', 'ads_read', 'ads_read'), 'ads-monitor': ('ACCOUNT', 'ads_read', 'monitor'),
         'ads-inventory': ('ACCOUNT', 'ads_read', 'ads_read'),
         'ads-placements': ('ACCOUNT', 'ads_read', 'ads_read'),
         'page-live': ('PAGE', 'pages_read_engagement', 'live_identify'),
         'page-read': ('PAGE', 'pages_read_engagement', 'connection'),
         'ad-draft': ('ACCOUNT', None, 'proposal'), 'ads-review': ('ACCOUNT', 'ads_read', 'review')}
DIAGNOSTIC_TASKS = tuple(task for task, (_, permission, _) in TASKS.items() if permission is not None)
ADS_REPORT_TASKS = ('ads-analysis','ads-review')
DATED_REPORT_TASKS = (*ADS_REPORT_TASKS, 'ads-monitor', 'ads-placements')
INSIGHTS_BY_OPTIONS = {(False, False): 'insights', (False, True): CONVERSION_INSIGHTS,
                       (True, False): HIERARCHY_INSIGHTS, (True, True): CONVERSION_HIERARCHY_INSIGHTS}
DEFAULT_ADS_REPORT_PAGES = 3
ADS_REPORT_PAGE_SIZE = 50
TTL = timedelta(minutes=5)
PROFILE_KEYS = ('schema','client_id','created_at','updated_at','app_id','business_id','assets','defaults',
                'binding','checks','adapter','skill_version')
PAGE_BINDING_KEYS = ('principal_id','secret_ref','record_hash','proof','role','role_evidence_ref')
PAGE_ROLES = ('FACEBOOK_ACCESS','TASK_ACCESS')
PAGE_GRANT_STATES = ('NAMED_PAGE_IN_DEBUG_TARGETS','NO_EXPLICIT_PAGE_TARGET_LIST')
PAGE_LIVE_PERMISSION_RECHECK = 'WORKSPACE_PAGE_LIVE_PERMISSION_RECHECK_REQUIRED'
ASSET_RECONFIRMATION_REQUIRED = 'WORKSPACE_ASSET_RECONFIRMATION_REQUIRED'
TARGET_ONLY_REVIEW_REQUIRED = 'TARGET_ONLY_REVIEW_REQUIRED'
AUTH_ERRORS = (*GRAPH_AUTH_ERRORS,'DEBUG_TOKEN_INVALID','DEBUG_EXPIRY_NOT_BOUNDED','CREDENTIAL_RECORD_DISABLED',
               'CONNECTION_EXPIRED_OR_CLOCK_SKEW','NATIVE_CREDENTIAL_UNAVAILABLE','DEBUG_APP_OR_PRINCIPAL_MISMATCH')
MONITOR_AUTH_FAILURES = (*GRAPH_AUTH_ERRORS,'DEBUG_TOKEN_INVALID','CONNECTION_EXPIRED_OR_CLOCK_SKEW')
ASSET_KEYS = ('kind','id','ownership_evidence_ref')
CHECK_KEYS = ('at','status','expires','permissions','currency','timezone')
HEALTH_STATES = ('HEALTHY','DEGRADED','AUTH_REQUIRED','PERMISSION_REQUIRED','ASSET_CHANGED','VERSION_MISMATCH','BLOCKED')
BACKUP_KEYS = ('client_id','app_id','business_id','assets','defaults')


class PlanPrerequisiteError(InputError):
    """A local plan is blocked by an already classified workspace state."""

    def __init__(self, code, route):
        super().__init__(code)
        self.route = {key: route[key] for key in ('mode', 'status', 'reason', 'workflow')}
        self.route['next_workflow'] = workspace_followup(route)


def pattern(value, regex, code):
    if type(value) is not str or not re.fullmatch(regex, value):
        fail(code)
    return value


def numeric(value):
    return pattern(value, r'[1-9][0-9]{0,39}', 'WORKSPACE_NUMERIC_ID')


def asset_key(kind, identifier):
    if kind not in ('ACCOUNT','PAGE'):
        fail('WORKSPACE_ASSET_KIND')
    return kind + ':' + numeric(identifier)


def asset_reconfirmation_pending(profile,key):
    return profile['checks'].get(key,{}).get('status')=='ASSET_CHANGED'


def validate_profile(p, *, version='client-profile-v2'):
    accepted = (*PROFILE_KEYS,'page_bindings','business_profiles','connection_onboarding','shopify_read_binding') if version=='client-profile-v2' else PROFILE_KEYS
    object_only(p, accepted, PROFILE_KEYS)
    if p['schema'] != version:
        fail('WORKSPACE_SCHEMA_MIGRATION_REQUIRED')
    pattern(p['client_id'], r'client_[0-9a-f]{32}', 'WORKSPACE_CLIENT_ID')
    if aware_time(p['updated_at']) < aware_time(p['created_at']):
        fail('WORKSPACE_TIME')
    for key in ('app_id','business_id'):
        if p[key] is not None:
            numeric(p[key])
    pattern(p['adapter'], r'v[0-9]{1,3}\.[0-9]{1,2}', 'WORKSPACE_VERSION')
    pattern(p['skill_version'], r'[0-9][0-9a-z.]{0,24}(?:\+[0-9a-z][0-9a-z.-]{0,32})?', 'WORKSPACE_VERSION')
    assets = p['assets']
    if type(assets) is not dict or len(assets)>100:
        fail('WORKSPACE_ASSET_LIMIT')
    for key, row in assets.items():
        object_only(row,ASSET_KEYS,ASSET_KEYS)
        if key != asset_key(row['kind'], row['id']):
            fail('WORKSPACE_ASSET_SCOPE')
        pattern(row['ownership_evidence_ref'],r'EV_[A-Z0-9_]{1,80}', 'WORKSPACE_OWNERSHIP_REFERENCE')
    object_only(p['defaults'], ('ACCOUNT','PAGE'), ('ACCOUNT','PAGE'))
    for kind, identifier in p['defaults'].items():
        if identifier is not None and asset_key(kind, identifier) not in assets:
            fail('WORKSPACE_DEFAULT_NOT_MAPPED')
    if 'shopify_read_binding' in p:
        from .shopify_transport import validate_binding
        validate_binding(p['shopify_read_binding'])
    if 'connection_onboarding' in p:
        from .workspace_connect import validate_state
        validate_state(p['connection_onboarding'])
    business_profiles = p.get('business_profiles',{})
    if type(business_profiles) is not dict or len(business_profiles)>100:
        fail('WORKSPACE_BUSINESS_PROFILE_SCOPE')
    for identifier, business in business_profiles.items():
        business_profile.validate(business)
        if (asset_key('ACCOUNT',identifier) not in assets or business['account_id']!=identifier
                or business['client_id']!=p['client_id']):
            fail('WORKSPACE_BUSINESS_PROFILE_SCOPE')
    binding = p['binding']
    if binding is not None:
        keys = ('principal_id','secret_ref','record_hash','proof','owner_confirmed','developer_confirmed','accounts','auth_status')
        if version == 'client-profile-v2':
            keys += ('expires_at',)
            if binding.get('expires_at') is not None:
                aware_time(binding['expires_at'])
        object_only(binding,keys,keys)
        numeric(binding['principal_id'])
        if (type(binding['accounts']) is not list or len(binding['accounts'])>100
                or any(asset_key('ACCOUNT',identifier) not in assets for identifier in binding['accounts'])
                or len(set(binding['accounts']))!=len(binding['accounts'])
                or binding['auth_status'] not in ('UNCHECKED','CURRENT','AUTH_REQUIRED','PERMISSION_REQUIRED')):
            fail('WORKSPACE_BINDING_SCOPE')
        pattern(binding['secret_ref'],r'conn_[a-f0-9]{32}','WORKSPACE_REFERENCE')
        pattern(binding['record_hash'],r'[a-f0-9]{64}','WORKSPACE_REFERENCE')
        if (binding['proof'] != 'NOT_REQUIRED_CONFIRMED' or binding['owner_confirmed'] is not True
                or binding['developer_confirmed'] is not True or p['app_id'] is None):
            fail('WORKSPACE_CONNECTION_REVIEW_REQUIRED')
    page_bindings = p.get('page_bindings',{})
    if type(page_bindings) is not dict or len(page_bindings)>100:
        fail('WORKSPACE_BINDING_SCOPE')
    for identifier, page_binding in page_bindings.items():
        if asset_key('PAGE',identifier) not in assets or p['app_id'] is None or p['business_id'] is None:
            fail('WORKSPACE_BINDING_SCOPE')
        object_only(page_binding,PAGE_BINDING_KEYS,PAGE_BINDING_KEYS)
        numeric(page_binding['principal_id'])
        pattern(page_binding['secret_ref'],r'conn_[a-f0-9]{32}','WORKSPACE_REFERENCE')
        pattern(page_binding['record_hash'],r'[a-f0-9]{64}','WORKSPACE_REFERENCE')
        pattern(page_binding['role_evidence_ref'],r'EV_[A-Z0-9_]{1,80}','WORKSPACE_ROLE_REFERENCE')
        if page_binding['proof']!='NOT_REQUIRED_CONFIRMED' or page_binding['role'] not in PAGE_ROLES:
            fail('WORKSPACE_PAGE_REVIEW_REQUIRED')
    if type(p['checks']) is not dict or not set(p['checks'])<=set(assets):
        fail('WORKSPACE_CHECK_SCOPE')
    for key, check in p['checks'].items():
        identifier = assets[key]['id']
        is_page = assets[key]['kind']=='PAGE'
        allowed = (*CHECK_KEYS,'grant_binding') if is_page else CHECK_KEYS
        object_only(check,allowed,CHECK_KEYS)
        aware_time(check['at'])
        permission = 'pages_read_engagement' if is_page else 'ads_read'
        if check['status']=='HEALTHY':
            if check['permissions'] != [permission] or not check['expires']:
                fail('WORKSPACE_CHECK_SCOPE')
            if is_page:
                if identifier not in page_bindings or check.get('grant_binding') not in PAGE_GRANT_STATES:
                    fail('WORKSPACE_CHECK_SCOPE')
            elif not binding or identifier not in binding['accounts'] or not check['timezone'] or not check['currency']:
                fail('WORKSPACE_CHECK_SCOPE')
        if check['status'] not in HEALTH_STATES:
            fail('WORKSPACE_STATUS')
        if check['expires'] is not None:
            aware_time(check['expires'])
        if check['permissions'] not in ([], [permission]):
            fail('WORKSPACE_PERMISSION_FORMAT')
        if is_page:
            if check.get('grant_binding') is not None and check['grant_binding'] not in PAGE_GRANT_STATES:
                fail('WORKSPACE_PAGE_CHECK_FIELDS')
            if check['currency'] is not None or check['timezone'] is not None:
                fail('WORKSPACE_PAGE_CHECK_FIELDS')
        else:
            if check['currency'] is not None:
                pattern(check['currency'],r'[A-Z]{3}','WORKSPACE_ACCOUNT_CONTEXT')
            if check['timezone'] is not None:
                pattern(check['timezone'],r'[A-Za-z0-9_+/.-]{1,100}','WORKSPACE_ACCOUNT_CONTEXT')
    return p


def blank(clock):
    now = clock().isoformat()
    return dict(schema='client-profile-v2', client_id='client_'+uuid4().hex, created_at=now, updated_at=now,
                app_id=None,business_id=None,assets={},defaults={'ACCOUNT':None,'PAGE':None},
                binding=None,checks={},page_bindings={},adapter='v27.0',skill_version=__version__)


def summary(p):
    mappings = []
    page_bindings = p.get('page_bindings',{})
    for key, row in sorted(p['assets'].items()):
        check = p['checks'].get(key) or {}
        identifier = row['id']
        mappings.append(dict(kind=row['kind'], id_hint='…'+identifier[-4:] if len(identifier)>4 else '…',
                             default=p['defaults'][row['kind']]==identifier,
                             last_health_check_at=check.get('at'), observed_status=check.get('status','UNCHECKED'),
                             validated_permissions=check.get('permissions',[])))
        if row['kind']=='PAGE':
            role = page_bindings.get(identifier,{}).get('role')
            mappings[-1]['owner_reported_role'] = role
            mappings[-1]['role_platform_verified'] = False
        else:
            mappings[-1]['business_profile_configured'] = identifier in p.get('business_profiles',{})
    return dict(client_id=p['client_id'], schema=p['schema'], mappings=mappings,
                assets={kind:sum(row['kind']==kind for row in p['assets'].values()) for kind in ('ACCOUNT','PAGE')},
                default_configured={k:v is not None for k,v in p['defaults'].items()},
                app_configured=p['app_id'] is not None, business_configured=p['business_id'] is not None,
                credential_reference_configured=p['binding'] is not None or bool(page_bindings), updated_at=p['updated_at'], dispatchable=False)


def merge_preparation(p,form):
    for key,name in [('app_id','app'),('business_id','business_portfolio')]:
        identifier = form['assets'][name]['id']
        if identifier is not None:
            if p[key] not in (None,identifier):
                fail('WORKSPACE_EXISTING_MAPPING_CHANGED')
            p[key] = identifier
    for kind,name in [('ACCOUNT','ad_account'),('PAGE','facebook_page')]:
        asset = form['assets'][name]
        if kind=='PAGE' and form['page_identity_type']!='FACEBOOK_PAGE':
            continue
        if asset['id'] is not None and asset['ownership_evidence_ref']:
            key = asset_key(kind,asset['id'])
            row = dict(kind=kind,**asset)
            if key in p['assets'] and p['assets'][key]!=row:
                fail('WORKSPACE_EXISTING_MAPPING_CHANGED')
            p['assets'][key] = row


def initialize(directory, preparation=None, *, clock=utcnow):
    p = blank(clock)
    if preparation is not None:
        merge_preparation(p,load_preparation(preparation))
    return create(directory,p)


def create(directory,p):
    validate_profile(p)
    journal = Journal(new_directory(directory),mode='READ_ONLY')
    with journal.locked():
        journal.save(p,validate_profile)
    return dict(mode='ONBOARDING', **summary(p))


def journal_for(directory, *, existing_only=False):
    root = checked_path(directory,link_error='WORKSPACE_PATH_LINK',volume_error='WORKSPACE_VOLUME')
    if not (root/'operator-state.json').is_file():
        fail('WORKSPACE_NOT_FOUND')
    return Journal(root,mode='READ_ONLY',existing_only=existing_only)


def load(journal, *, allow_legacy=False):
    def check_version(value):
        if type(value) is not dict or not isinstance(value.get('schema'),str) or not value['schema'].startswith('client-profile-v'):
            fail('WORKSPACE_RECORD_FORMAT')
        if value['schema'] == 'client-profile-v2':
            validate_profile(value)
        elif allow_legacy and value['schema']=='client-profile-v1':
            validate_profile(value,version='client-profile-v1')
    p, damaged = journal.load(check_version)
    if p is None or damaged:
        fail('WORKSPACE_RECOVERY_REQUIRED')
    if (p['schema']!='client-profile-v2'
            and not (allow_legacy and p['schema']=='client-profile-v1')):
        fail('WORKSPACE_SCHEMA_MIGRATION_REQUIRED')
    return p


def read(directory, *, existing_only=False):
    journal = journal_for(directory,existing_only=existing_only)
    with journal.locked(existing_only=existing_only):
        return load(journal)


def update(directory, change, clock=utcnow):
    journal = journal_for(directory)
    with journal.locked():
        p = load(journal)
        before = digest(p)
        change(p)
        if before != digest(p):
            now = clock()
            if now < aware_time(p['updated_at']):
                fail('WORKSPACE_CLOCK_REVERSED')
            p['updated_at'] = now.isoformat()
            journal.save(p,validate_profile)
        return summary(p)


def map_asset(directory, kind, identifier, evidence):
    key = asset_key(kind,identifier)
    row = dict(kind=kind,id=identifier,ownership_evidence_ref=evidence)
    def change(p):
        if key in p['assets'] and p['assets'][key] != row:
            fail('WORKSPACE_EXISTING_MAPPING_CHANGED')
        p['assets'][key] = row
    return update(directory,change)


def default_asset(directory,kind,identifier):
    key = asset_key(kind,identifier)
    def change(p):
        if key not in p['assets']:
            fail('WORKSPACE_DEFAULT_NOT_MAPPED')
        p['defaults'][kind] = identifier
    return update(directory,change)


def unmap(directory,kind,identifier):
    key = asset_key(kind,identifier)
    def change(p):
        if key not in p['assets']:
            fail('WORKSPACE_ASSET_NOT_MAPPED')
        p['assets'].pop(key)
        p['checks'].pop(key,None)
        if kind=='PAGE':
            p.get('page_bindings',{}).pop(identifier,None)
        else:
            p.get('business_profiles',{}).pop(identifier,None)
            if p['binding'] and identifier in p['binding']['accounts']:
                p['binding']['accounts'].remove(identifier)
        if p['defaults'][kind] == identifier:
            p['defaults'][kind] = None
    return update(directory,change)


def prepare_asset_connection(directory, output, *, kind='ACCOUNT', identifier=None, record_directory=None,
                             app_developer_confirmed=False, asset_owner_confirmed=False, proof='UNKNOWN',
                             role=None, role_evidence=None):
    """Preview one mapped asset without enrolling a credential or changing the mapping."""
    if kind not in ('ACCOUNT','PAGE'):
        fail('WORKSPACE_ASSET_KIND')
    page = kind=='PAGE'
    if not page and (role is not None or role_evidence is not None):
        fail('WORKSPACE_CONNECTION_PAGE_ROLE_REQUIRES_PAGE')
    if role is not None and role not in PAGE_ROLES:
        fail('WORKSPACE_PAGE_REVIEW_REQUIRED')
    if role_evidence is not None:
        pattern(role_evidence,r'EV_[A-Z0-9_]{1,80}','WORKSPACE_ROLE_REFERENCE')
    asset_field = 'page_id' if page else 'account_id'
    journal = journal_for(directory,existing_only=True)
    record_directory = record_directory or journal.directory/'credential-record'
    with journal.locked(existing_only=True):
        profile = load(journal)
        if (not profile['app_id'] or not profile['business_id'] or profile['adapter']!='v27.0'
                or profile['skill_version'] not in WORKSPACE_RUNTIME_VERSIONS):
            fail('WORKSPACE_CONNECTION_PROFILE_REQUIRED')
        target = identifier if identifier is not None else profile['defaults'][kind]
        if target is None:
            fail(f'WORKSPACE_CONNECTION_SELECT_{kind}')
        mapping = profile['assets'].get(asset_key(kind,target))
        if mapping is None:
            fail(f'WORKSPACE_CONNECTION_{kind}_NOT_MAPPED')
        record = read_record(record_directory,existing_only=True)
        if record['app_id']!=profile['app_id']:
            fail('WORKSPACE_CONNECTION_RECORD_APP_MISMATCH')
        intent = dict(blank_intent(),app_id=profile['app_id'],principal_id=record['principal_id'],
                      credential_source='OS_REFERENCE',credential_reference=record['reference'],
                      app_developer_confirmed=app_developer_confirmed,asset_owner_confirmed=asset_owner_confirmed,
                      appsecret_proof_requirement=proof)
        if page:
            intent.pop('account_id')
            intent['schema'] = 'page-connection-intent-v1'
        intent[asset_field] = target
        record_hash = digest(record)
        view = preview(intent,record_directory=record_directory,page=page,record_existing_only=True)
        if view['credential_record']['record_sha256']!=record_hash:
            fail('CREDENTIAL_RECORD_CHANGED')
        RecordGuard(record_directory,intent,record_hash).check(existing_only=True)
        if page:
            view.update(page_access=dict(owner_reported_role=role,role_evidence_ref=role_evidence,
                                         ownership_evidence_ref=mapping['ownership_evidence_ref'],
                                         page_role_platform_verified=False))
            if role is None:
                view['blockers'].append('WORKSPACE_PAGE_ROLE_REQUIRED')
            if role_evidence is None:
                view['blockers'].append('WORKSPACE_PAGE_ROLE_EVIDENCE_REQUIRED')
            if view['blockers']:
                view['status'] = 'PREPARATION_BLOCKED'
        root = new_directory(output)
        files = [_write_new(root,'intent.json',intent),_write_new(root,'PREVIEW.json',view)]
        next_command = 'workspace-page-bind' if page else 'workspace-bind'
        receipt = dict(status=f'WORKSPACE_{kind}_CONNECTION_PREPARED',input_file='intent.json',preview_file='PREVIEW.json',
                       preview_status=view['status'],blockers=view['blockers'],
                       intent_sha256=view['intent_sha256'],credential_record_sha256=record_hash,files=files,
                       next_command='workspace-connection-prepare' if view['blockers'] else next_command,
                       binding_applied=False,dispatchable=False,network_used=False,native_credential_accessed=False,
                       real_connection_verified=False)
        receipt[asset_field] = target
        _write_new(root,'RECEIPT.json',receipt)
    return receipt


def bind(directory, intent_path, record_directory):
    intent = load_intent(intent_path)
    account_key = asset_key('ACCOUNT',intent['account_id'])

    def scope_identity(profile):
        binding = profile['binding'] or {}
        return (profile['client_id'],profile['app_id'],profile['business_id'],profile['assets'].get(account_key),
                profile['checks'].get(account_key),binding.get('record_hash'))

    observed = scope_identity(read(directory))
    record = read_record(record_directory)
    if (intent['credential_source']!='OS_REFERENCE' or not intent['asset_owner_confirmed']
            or not intent['app_developer_confirmed'] or intent['appsecret_proof_requirement']!='NOT_REQUIRED_CONFIRMED'
            or record['state']!='PREPARED' or (intent['app_id'],intent['principal_id'],intent['credential_reference']) !=
            (record['app_id'],record['principal_id'],record['reference'])):
        fail('WORKSPACE_CONNECTION_REVIEW_REQUIRED')
    record_hash = digest(record)
    guard = RecordGuard(record_directory,intent,record_hash)

    def change(p):
        if p['app_id'] != intent['app_id'] or account_key not in p['assets']:
            fail('WORKSPACE_BINDING_SCOPE')
        if scope_identity(p)!=observed:
            fail('WORKSPACE_CHANGED_DURING_CHECK')
        guard.check()
        previous = p['binding']
        same = previous and previous['record_hash'] == record_hash
        accounts = set(previous['accounts']) if same else set()
        accounts.add(intent['account_id'])
        # An unchanged enrollment keeps established failures and currency for other assets.
        if same:
            previous['accounts'] = sorted(accounts)
            if asset_reconfirmation_pending(p,account_key):
                p['checks'].pop(account_key)
            return
        p['binding'] = dict(accounts=sorted(accounts),auth_status='UNCHECKED',expires_at=None,principal_id=record['principal_id'],secret_ref=record['reference'],
                            record_hash=record_hash,proof=intent['appsecret_proof_requirement'],
                            owner_confirmed=True,developer_confirmed=True)
        invalidate_account_checks(p,'UNCHECKED')
        p['checks'].pop(account_key,None)
    return update(directory,change)


def page_intent(p, identifier, binding):
    intent = blank_intent()
    intent.pop('account_id')
    intent.update(schema='page-connection-intent-v1', app_id=p['app_id'], principal_id=binding['principal_id'],
                  page_id=identifier, credential_source='OS_REFERENCE', credential_reference=binding['secret_ref'],
                  asset_owner_confirmed=True, app_developer_confirmed=True, appsecret_proof_requirement=binding['proof'])
    return validate_intent(intent,page=True)


def bind_page(directory, identifier, record_directory, role, evidence, *, app_developer_confirmed, proof_not_required):
    key = asset_key('PAGE',identifier)
    if app_developer_confirmed is not True or proof_not_required is not True or role not in PAGE_ROLES:
        fail('WORKSPACE_PAGE_REVIEW_REQUIRED')
    pattern(evidence,r'EV_[A-Z0-9_]{1,80}','WORKSPACE_ROLE_REFERENCE')
    profile = read(directory)
    if key not in profile['assets'] or not profile['app_id'] or not profile['business_id']:
        fail('WORKSPACE_BINDING_SCOPE')
    record = read_record(record_directory)
    binding = dict(principal_id=record['principal_id'],secret_ref=record['reference'],record_hash=digest(record),
                   proof='NOT_REQUIRED_CONFIRMED',role=role,role_evidence_ref=evidence)
    guard = RecordGuard(record_directory,page_intent(profile,identifier,binding),binding['record_hash'])

    def change(current):
        if (current['assets'].get(key),current['app_id'],current['business_id']) != (
                profile['assets'][key],profile['app_id'],profile['business_id']):
            fail('WORKSPACE_CHANGED_DURING_CHECK')
        guard.check()
        pages = current.setdefault('page_bindings',{})
        if (pages.get(identifier) != profile.get('page_bindings',{}).get(identifier)
                or current['checks'].get(key)!=profile['checks'].get(key)):
            fail('WORKSPACE_CHANGED_DURING_CHECK')
        if pages.get(identifier) != binding or asset_reconfirmation_pending(current,key):
            pages[identifier] = binding
            current['checks'].pop(key,None)

    return update(directory,change)


def page_health(directory, profile, identifier, task, result, clock, record_directory, *, existing_only=False):
    binding = profile.get('page_bindings',{}).get(identifier)
    if binding is None:
        return result('DAILY_OPERATION','DEGRADED','VERIFY_PAGE_ROLES_AND_ACCESS')
    check = profile['checks'].get('PAGE:'+identifier)
    record_directory = record_directory or journal_for(directory,existing_only=existing_only).directory/'credential-record'
    try:
        RecordGuard(record_directory,page_intent(profile,identifier,binding),binding['record_hash']).check(existing_only=existing_only)
    except InputError:
        return result('RECONFIGURATION','AUTH_REQUIRED','CHECK_NAMED_OS_REFERENCE')
    if check is None:
        return result('DAILY_OPERATION','DEGRADED','CHECK_THIS_PAGE')
    if check['status']=='PERMISSION_REQUIRED':
        return result('RECONFIGURATION','PERMISSION_REQUIRED','REVIEW_NAMED_PAGE_ACCESS')
    if check['status']!='HEALTHY':
        return result('RECONFIGURATION',check['status'],'REPAIR_THIS_SCOPE')
    now = clock()
    if now>=aware_time(check['expires']):
        return result('RECONFIGURATION','AUTH_REQUIRED','REAUTHORIZE_EXISTING_APP')
    if not timedelta(0)<=now-aware_time(check['at'])<TTL:
        return result('DAILY_OPERATION','DEGRADED','REFRESH_THIS_ASSET')
    if task=='page-live':
        response = result('DAILY_OPERATION','DEGRADED','LIVE_CONTRACT_PENDING')
    else:
        response = result('DAILY_OPERATION','HEALTHY','RECENT_NAMED_PAGE_READ')
    return dict(response,access_observation='RECENT_NAMED_PAGE_READ',page_grant_binding=check['grant_binding'],
                owner_reported_role=binding['role'],page_role_platform_verified=False)


def page_context(directory, identifier, record_directory):
    profile = read(directory)
    key = asset_key('PAGE',identifier)
    if asset_reconfirmation_pending(profile,key):
        fail(ASSET_RECONFIRMATION_REQUIRED)
    binding = profile.get('page_bindings',{}).get(identifier)
    if binding is None or key not in profile['assets'] or profile['adapter']!='v27.0' or profile['skill_version'] not in WORKSPACE_RUNTIME_VERSIONS:
        fail('WORKSPACE_PAGE_REVIEW_REQUIRED')
    record_directory = record_directory or journal_for(directory).directory/'credential-record'
    intent = page_intent(profile,identifier,binding)
    return profile,key,binding,record_directory,intent


def preview_page(directory, identifier, record_directory):
    _,_,binding,record_directory,intent = page_context(directory,identifier,record_directory)
    result = preview(intent,record_directory=record_directory,page=True)
    record = result['credential_record']['record_sha256']
    if record!=binding['record_hash']:
        result['blockers'].append('WORKSPACE_REFERENCE_CHANGED')
        result['status'] = 'PREPARATION_BLOCKED'
    return dict(result,owner_reported_role=binding['role'],page_role_platform_verified=False)


def invalidate_pages_sharing_reference(profile, record_hash, stamp):
    for page_id, binding in profile.get('page_bindings',{}).items():
        key = 'PAGE:'+page_id
        if binding['record_hash']==record_hash and not asset_reconfirmation_pending(profile,key):
            profile['checks'][key] = dict(at=stamp,status='AUTH_REQUIRED',expires=None,
                                          permissions=[],currency=None,timezone=None)


def invalidate_account_checks(profile, status):
    profile['binding']['auth_status'] = status
    profile['checks'] = {key: row for key,row in profile['checks'].items()
                         if key.startswith('PAGE:') or row['status']=='ASSET_CHANGED'}


def _shared_access_keys(profile, record_hash, *, include_pages=True):
    binding = profile['binding'] or {}
    keys = {'ACCOUNT:'+account for account in binding['accounts']} if binding.get('record_hash')==record_hash else set()
    if include_pages:
        keys.update('PAGE:'+page for page,row in profile.get('page_bindings',{}).items() if row['record_hash']==record_hash)
    return keys


def _apply_shared_access_failure(profile, previous, record_hash, status, stamp):
    """Return false when another check changed during the failing request."""
    include_pages = status=='AUTH_REQUIRED'
    keys = _shared_access_keys(profile,record_hash,include_pages=include_pages)
    if any(profile['checks'].get(key)!=previous['checks'].get(key) for key in keys):
        return False
    if (profile['binding'] or {}).get('record_hash')==record_hash:
        invalidate_account_checks(profile,status)
    if include_pages:
        invalidate_pages_sharing_reference(profile,record_hash,stamp)
    return True


def refresh_page(directory, identifier, output, *, record_directory=None, clock=utcnow):
    profile,key,binding,record_directory,intent = page_context(directory,identifier,record_directory)
    guard = RecordGuard(record_directory,intent,binding['record_hash'])
    guard.check()

    def scope_identity(p):
        return (p['client_id'],p['app_id'],p['business_id'],p['adapter'],p['skill_version'],p['assets'].get(key),
                p.get('page_bindings',{}).get(identifier),p['checks'].get(key))

    def still_mapped():
        if scope_identity(read(directory))!=scope_identity(profile):
            fail('WORKSPACE_CHANGED_DURING_CHECK')
        guard.check()

    observed = check_connection(intent, output, clock=clock, record_directory=record_directory, scope_check=still_mapped, page=True)
    check = dict(at=clock().isoformat(),status='DEGRADED',expires=None,permissions=[],currency=None,timezone=None)
    if observed['status']=='NAMED_PAGE_READ_PASSED':
        meta = observed['metadata']
        if (observed['page']!={'id':identifier} or observed['intent_sha256']!=digest(intent)
                or observed['credential_record_sha256']!=binding['record_hash']
                or (meta['app_id'],meta['principal_id'])!=(profile['app_id'],binding['principal_id'])
                or meta['scopes']!=['pages_read_engagement'] or meta['page_grant_binding'] not in PAGE_GRANT_STATES):
            fail('WORKSPACE_HEALTH_SCOPE_CHANGED')
        expiry = min(aware_time(meta['token_expires_at']),aware_time(meta['data_access_expires_at']))
        check.update(at=observed['page_observed_at'],status='HEALTHY',expires=expiry.isoformat(),
                     permissions=['pages_read_engagement'],grant_binding=meta['page_grant_binding'])
    else:
        code = observed['error_code']
        if code in AUTH_ERRORS:
            check['status'] = 'AUTH_REQUIRED'
        elif code in ('PERMISSION','DEBUG_PAGE_READ_NOT_GRANTED','DEBUG_PAGE_TARGET_NOT_GRANTED'):
            check['status'] = 'PERMISSION_REQUIRED'
        elif code=='RETURNED_OBJECT_MISMATCH':
            check['status'] = 'ASSET_CHANGED'

    target_only = False

    def change(current):
        nonlocal target_only
        if scope_identity(current)!=scope_identity(profile):
            fail('WORKSPACE_CHANGED_DURING_CHECK')
        guard.check()
        if check['status']=='AUTH_REQUIRED':
            target_only = not _apply_shared_access_failure(current,profile,binding['record_hash'],'AUTH_REQUIRED',check['at'])
        current['checks'][key] = check

    update(directory,change,clock)
    update_status = {'workspace_health_update':TARGET_ONLY_REVIEW_REQUIRED} if target_only else {}
    return dict(status=check['status'],workflow='connection',scope='NAMED_PAGE',page_id=identifier,
                grant_binding=check.get('grant_binding'),error_code=observed['error_code'],dispatchable=False,
                page_role_platform_verified=False,live_action_authorized=False,**update_status)


def health_check(directory,task,identifier=None,*,clock=utcnow,record_directory=None,monitor_directory=None,
                 existing_only=False,since=None,until=None,last_complete_days=None,website_conversions=False,hierarchy=False):
    if task not in TASKS:
        fail('WORKSPACE_UNKNOWN_TASK')
    dated = since is not None or until is not None or last_complete_days is not None
    if dated or website_conversions or hierarchy:
        if task not in DATED_REPORT_TASKS:
            fail('WORKSPACE_REPORT_SCOPE_REQUIRES_DATED_TASK')
        if last_complete_days is not None and (since is not None or until is not None):
            fail('WORKSPACE_DATE_SELECTION_CONFLICT')
        if last_complete_days is None and (since is None or until is None):
            fail('INVALID_DATE_RANGE')
        if task == 'ads-placements' and (website_conversions or hierarchy):
            fail('WORKSPACE_PLACEMENT_REPORT_OPTIONS')
    kind,permission,workflow = TASKS[task]
    def result(mode,state,reason):
        return dict(mode=mode,status=state,reason=reason,workflow=workflow,task=task,target_kind=kind,
                    target_id=identifier,required_permissions=[permission] if permission else [],dispatchable=False,
                    cache_ttl_seconds=int(TTL.total_seconds()),real_time_check_required=permission is not None,
                    access_observation='UNCHECKED', data_freshness='NOT_MEASURED',
                    monitor_process_verified=False,network_used=False)
    try:
        p = read(directory,existing_only=existing_only)
    except InputError as exc:
        if str(exc) != 'WORKSPACE_SCHEMA_MIGRATION_REQUIRED':
            raise
        return result('RECONFIGURATION','VERSION_MISMATCH','REVIEW_PROFILE_MIGRATION')
    identifier = identifier if identifier is not None else p['defaults'][kind]
    if p['adapter']!='v27.0' or p['skill_version'] not in WORKSPACE_RUNTIME_VERSIONS:
        return result('RECONFIGURATION','VERSION_MISMATCH','REVIEW_ADAPTER_OR_PROFILE_VERSION')
    if not p['app_id'] or not p['business_id']:
        return result('ONBOARDING','BLOCKED','COMPLETE_APP_AND_BUSINESS_MAPPING')
    if identifier is None:
        return result('RECONFIGURATION','BLOCKED','SELECT_NAMED_ASSET')
    key = asset_key(kind,identifier)
    if key not in p['assets']:
        return result('RECONFIGURATION','ASSET_CHANGED','CONFIRM_NAMED_MAPPING')
    if asset_reconfirmation_pending(p,key):
        return result('RECONFIGURATION','ASSET_CHANGED','RECONFIRM_TARGET_AND_ACCESS')
    if permission is None:
        return result('DAILY_OPERATION','HEALTHY','LOCAL_DRAFT_NO_REMOTE_ACCESS')
    if kind=='PAGE':
        response = page_health(directory,p,identifier,task,result,clock,record_directory,existing_only=existing_only)
        if task == 'page-live' and monitor_directory is not None:
            from .workspace_runner import scoped_page_live_observation
            current_access = response['access_observation'] == 'RECENT_NAMED_PAGE_READ'
            response['monitor_observation'] = scoped_page_live_observation(monitor_directory,identifier,p,clock,
                                                                           current_access=current_access)
            response['monitor_process_verified'] = response['monitor_observation']['monitor_process_verified']
        return response
    b = p['binding']
    if b is None:
        return result('RECONFIGURATION','AUTH_REQUIRED','CONNECT_EXISTING_APP')
    if b['auth_status']=='AUTH_REQUIRED' or (b['expires_at'] and clock() >= aware_time(b['expires_at'])):
        return result('RECONFIGURATION','AUTH_REQUIRED','REAUTHORIZE_EXISTING_APP')
    if permission=='ads_read' and b['auth_status']=='PERMISSION_REQUIRED':
        return result('RECONFIGURATION','PERMISSION_REQUIRED','REVIEW_REQUIRED_PERMISSION')
    if kind=='ACCOUNT' and identifier not in b['accounts']:
        return result('RECONFIGURATION','DEGRADED','VERIFY_NEW_ACCOUNT_ONLY')
    if record_directory is None:
        record_directory = journal_for(directory,existing_only=existing_only).directory/'credential-record'
    try:
        recorded = read_record(record_directory,existing_only=existing_only)
        if (recorded['state']!='PREPARED' or digest(recorded)!=b['record_hash']
                or recorded['platform'] != {'darwin':'macos','win32':'windows'}.get(sys.platform)):
            return result('RECONFIGURATION','AUTH_REQUIRED','CHECK_NAMED_OS_REFERENCE')
    except InputError:
        return result('RECONFIGURATION','AUTH_REQUIRED','CHECK_NAMED_OS_REFERENCE')
    check = p['checks'].get(key)
    if check is None:
        return result('DAILY_OPERATION','DEGRADED','CHECK_THIS_ASSET')
    if check['status']=='PERMISSION_REQUIRED':
        return result('RECONFIGURATION','PERMISSION_REQUIRED','REVIEW_NAMED_ACCOUNT_ACCESS')
    if check['status']!='HEALTHY':
        return result('RECONFIGURATION',check['status'],'REPAIR_THIS_SCOPE')
    now = clock()
    if now >= aware_time(check['expires']):
        return result('RECONFIGURATION','AUTH_REQUIRED','REAUTHORIZE_EXISTING_APP')
    if permission not in check['permissions']:
        return result('RECONFIGURATION','PERMISSION_REQUIRED','REVIEW_REQUIRED_PERMISSION')
    if not timedelta(0)<=now-aware_time(check['at'])<TTL:
        return result('DAILY_OPERATION','DEGRADED','REFRESH_THIS_ASSET')
    observation = {'ads-inventory':'CHECK_INVENTORY_OBSERVATION',
                   'ads-placements':'CHECK_PLACEMENT_OBSERVATION'}.get(task,'CHECK_INSIGHTS_OBSERVATION')
    out = result('DAILY_OPERATION','DEGRADED',observation)
    out['access_observation'] = 'RECENT_NAMED_ACCOUNT_READ'
    if task == 'ads-monitor':
        out.update(status='DEGRADED', reason='RUNNER_NOT_CHECKED')
    requested_query = None
    if dated:
        if last_complete_days is not None:
            selection = closed_account_window(last_complete_days,check['timezone'],now)
            since, until = selection['since'], selection['until']
        operation = PLACEMENT_INSIGHTS if task == 'ads-placements' else INSIGHTS_BY_OPTIONS[hierarchy, website_conversions]
        requested = ReadPlan(operation,identifier,account_id=identifier,since=since,until=until)
        requested_query = dict(since=requested.since,until=requested.until,operation=requested.operation)
        out.update(requested_query=dict(requested_query,account_timezone=check['timezone']), query_scope_match='NOT_CHECKED')
    if monitor_directory is not None:
        from .workspace_runner import scoped_observation
        out.update(scoped_observation(monitor_directory, identifier, p, task, clock, requested_query=requested_query))
    return out


def refresh_account(directory, identifier, output, *, clock=utcnow, record_directory=None):
    p = read(directory)
    b = p['binding']
    key = asset_key('ACCOUNT',identifier)
    if asset_reconfirmation_pending(p,key):
        fail(ASSET_RECONFIRMATION_REQUIRED)
    if not b or identifier not in b['accounts'] or b['auth_status']=='AUTH_REQUIRED' or key not in p['assets'] or p['adapter']!='v27.0' or p['skill_version'] not in WORKSPACE_RUNTIME_VERSIONS:
        fail('WORKSPACE_BINDING_SCOPE')
    intent = blank_intent()
    intent.update(app_id=p['app_id'],principal_id=b['principal_id'],account_id=identifier,
                  credential_source='OS_REFERENCE',credential_reference=b['secret_ref'],app_developer_confirmed=True,
                  asset_owner_confirmed=True,appsecret_proof_requirement=b['proof'])
    validate_intent(intent)
    if record_directory is None:
        record_directory = journal_for(directory).directory/'credential-record'
    if b['expires_at'] and clock() >= aware_time(b['expires_at']):
        fail('WORKSPACE_REAUTHORIZE_EXISTING_APP')
    if digest(read_record(record_directory))!=b['record_hash']:
        fail('WORKSPACE_REFERENCE_CHANGED')
    def scope_identity(profile):
        binding = profile['binding'] or {}
        return (profile['client_id'],profile['app_id'],profile['business_id'],profile['adapter'],profile['skill_version'],
                profile['assets'].get(key),profile['checks'].get(key),identifier in binding.get('accounts',[]),
                binding.get('record_hash'),binding.get('principal_id'),binding.get('auth_status'),binding.get('expires_at'))
    def still_mapped():
        if scope_identity(read(directory)) != scope_identity(p):
            fail('WORKSPACE_CHANGED_DURING_CHECK')
    result = check_connection(intent,output,clock=clock,record_directory=record_directory,scope_check=still_mapped)
    check = dict(at=clock().isoformat(),status='DEGRADED',expires=None,permissions=[],currency=None,timezone=None)
    if result['status']=='NAMED_ACCOUNT_READ_PASSED':
        row = result['account']
        meta = result['metadata']
        if (row['id'] not in (identifier,'act_'+identifier) or row.get('account_id') != identifier
                or result['intent_sha256'] != digest(intent) or meta['app_id']!=p['app_id']
                or meta['principal_id']!=b['principal_id'] or result['credential_record_sha256']!=b['record_hash']
                or 'ads_read' not in meta['scopes']):
            fail('WORKSPACE_HEALTH_SCOPE_CHANGED')
        check.update(status='HEALTHY' if row['account_status']==1 else 'BLOCKED',permissions=['ads_read'],
                     expires=min(aware_time(meta['token_expires_at']),aware_time(meta['data_access_expires_at'])).isoformat(),
                     currency=row['currency'],timezone=row['timezone_name'],at=result['account_observed_at'])
    else:
        code = result['error_code']
        if code in AUTH_ERRORS:
            check['status']='AUTH_REQUIRED'
        elif code in ('PERMISSION','DEBUG_ADS_READ_NOT_GRANTED'):
            check['status']='PERMISSION_REQUIRED'
        elif code in ('RETURNED_ACCOUNT_MISMATCH','RETURNED_OBJECT_MISMATCH'):
            check['status']='ASSET_CHANGED'
    target_only = False

    def change(current):
        nonlocal target_only
        if scope_identity(current)!=scope_identity(p) or digest(read_record(record_directory))!=b['record_hash']:
            fail('WORKSPACE_CHANGED_DURING_CHECK')
        if check['expires']:
            current['binding']['expires_at'] = check['expires']
        if check['status']=='AUTH_REQUIRED' or result['error_code']=='DEBUG_ADS_READ_NOT_GRANTED':
            target_only = not _apply_shared_access_failure(current,p,b['record_hash'],check['status'],check['at'])
        elif check['status']=='HEALTHY':
            current['binding']['auth_status'] = 'CURRENT'
        current['checks'][key] = check
    update(directory,change,clock)
    update_status = {'workspace_health_update':TARGET_ONLY_REVIEW_REQUIRED} if target_only else {}
    return dict(status=check['status'],workflow='connection',scope='NAMED_ACCOUNT',
                error_code=result['error_code'],dispatchable=False,**update_status)


def backup(directory,output):
    return export_config(read(directory),output)


def export_config(p,output):
    envelope = portable_config(p)
    root = new_directory(output)
    file = _write_new(root,'config.json',envelope)
    _write_new(root,'RECEIPT.json',dict(files=[file],credentials_included=False))
    return dict(status='NON_SECRET_BACKUP_CREATED', credentials_included=False)


def portable_config(p):
    data = {key:deepcopy(p[key]) for key in BACKUP_KEYS}
    schema = 'client-config-backup-v1'
    if p.get('business_profiles'):
        data['business_profiles'] = deepcopy(p['business_profiles'])
        schema = 'client-config-backup-v2'
    rechecks = {key:check['at'] for key,check in p['checks'].items() if check['status']=='ASSET_CHANGED'}
    if rechecks:
        data['asset_rechecks'] = rechecks
        schema = 'client-config-backup-v3'
    return dict(schema=schema,config=data,sha256=digest(data))


def _read_backup_file(path, limit, error):
    if not path.is_file():
        fail(error)
    with path.open('rb') as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        fail(error)
    return raw


def _backup_receipt_sha(path, file_entry):
    receipt_path = path.with_name('RECEIPT.json')
    if path.name != 'config.json' or not (receipt_path.exists() or receipt_path.is_symlink()):
        return None
    error = 'WORKSPACE_BACKUP_RECEIPT_MISMATCH'
    try:
        receipt_path = checked_path(receipt_path, link_error=error, volume_error='WORKSPACE_VOLUME')
        raw = _read_backup_file(receipt_path, 32_000, error)
        receipt = strict_json(raw.decode('utf-8'))
        if (type(receipt) is dict and receipt.get('credentials_included') is False
                and receipt.get('files') == [file_entry]):
            return sha256(raw).hexdigest()
    except (OSError, ValueError):
        fail(error)
    fail(error)


def restore(path,directory,*,preview=False,expected_plan_sha256=None):
    path = checked_path(path,link_error='WORKSPACE_PATH_LINK',volume_error='WORKSPACE_VOLUME')
    raw = _read_backup_file(path, 512_000, 'WORKSPACE_BACKUP_FILE')
    value = strict_json(raw.decode('utf-8'))
    object_only(value, ('schema','config','sha256'), ('schema','config','sha256'))
    data = value['config']
    schema = value['schema']
    if schema=='client-config-backup-v1':
        object_only(data,BACKUP_KEYS,BACKUP_KEYS)
    elif schema=='client-config-backup-v2':
        keys = (*BACKUP_KEYS,'business_profiles')
        object_only(data,keys,keys)
    elif schema=='client-config-backup-v3':
        object_only(data,(*BACKUP_KEYS,'business_profiles','asset_rechecks'),(*BACKUP_KEYS,'asset_rechecks'))
    else:
        fail('WORKSPACE_BACKUP_INVALID')
    if digest(data)!=value['sha256']:
        fail('WORKSPACE_BACKUP_INVALID')
    p = blank(utcnow)
    p.update({key:item for key,item in data.items() if key!='asset_rechecks'})
    if schema=='client-config-backup-v3':
        rechecks = data['asset_rechecks']
        if (type(rechecks) is not dict or not 1<=len(rechecks)<=100
                or type(p['assets']) is not dict or not set(rechecks)<=set(p['assets'])):
            fail('WORKSPACE_BACKUP_INVALID')
        p['checks'] = {key:asset_change_check(stamp) for key,stamp in rechecks.items()}
    file_entry = dict(path=path.name,bytes=len(raw),sha256=sha256(raw).hexdigest())
    receipt_sha = _backup_receipt_sha(path, file_entry)
    target = checked_path(directory,link_error='MONITOR_OUTPUT_SYMLINK',volume_error='MONITOR_VOLUME_NOT_MOUNTED')
    if target.exists():
        fail('MONITOR_OUTPUT_REQUIRES_NEW_DIRECTORY')
    plan_sha = digest(dict(backup_file=str(path),workspace_path=str(target),
                           backup_file_sha256=file_entry['sha256'],backup_receipt_sha256=receipt_sha))
    if expected_plan_sha256 is not None and expected_plan_sha256 != plan_sha:
        fail('WORKSPACE_RESTORE_PLAN_CHANGED')
    review = dict(workspace_path=str(target),backup_schema=schema,backup_file_sha256=file_entry['sha256'],
                  restore_plan_sha256=plan_sha,backup_receipt_status='MATCHED' if receipt_sha else 'STANDALONE_CHECKSUM_ONLY',
                  network_used=False,authorizations_carried_forward=False)
    if preview:
        validate_profile(p)
        profile = summary(p)
        profile.pop('updated_at')
        return dict(status='WORKSPACE_RESTORE_PREVIEW_READY',**profile,**review)
    return dict(create(target,p),**review)


def migrate(source, destination, backup_directory):
    source_path, target, archive = (checked_path(x, link_error='WORKSPACE_PATH_LINK',volume_error='WORKSPACE_VOLUME')
                                    for x in (source,destination,backup_directory))
    for a,b in ((source_path,target),(source_path,archive),(target,archive)):
        if a.is_relative_to(b) or b.is_relative_to(a):
            fail('WORKSPACE_MIGRATION_PATHS_OVERLAP')
    if target.exists() or archive.exists():
        fail('WORKSPACE_MIGRATION_NEW_PATHS_REQUIRED')
    journal = journal_for(source_path)
    with journal.locked():
        old, damaged = journal.load(lambda value: validate_profile(value,version='client-profile-v1'))
        if not old or damaged:
            fail('WORKSPACE_RECOVERY_REQUIRED')
        export_config(old, archive)
        restore(archive/'config.json', target)
    return dict(status='MIGRATED_TO_V2', credentials_restored=False, fresh_check_required=True)


def removal_plan(directory):
    journal = journal_for(directory)
    with journal.locked():
        return removal_scope(journal,load(journal))


def removal_scope(journal, profile):
    owned = {path.name for path in journal.snapshots()} | {'operator.lock','operator-state.json'}
    extras = any(path.name not in owned for path in journal.directory.iterdir())
    return dict(status='REVIEW_SEPARATE_CONTENTS' if extras else 'READY_FOR_OWNER_REVIEW',
                local_profile_files=len(owned),other_entries_present=extras,
                os_reference_configured=profile['binding'] is not None or bool(profile.get('page_bindings')),
                local_files_removed=0,os_items_removed=0,meta_revoked=False,
                separate_decisions=['LOCAL_PROFILE','OS_CREDENTIAL','META_GRANT'])


def diagnostics(directory,output):
    profile = read(directory)
    safe = summary(profile)
    del safe['client_id']
    for mapping in safe['mappings']:
        del mapping['id_hint']
    safe.update(skill_version=profile['skill_version'], adapter=profile['adapter'],
                last_recorded_connection=(profile['binding'] or {}).get('auth_status','UNCHECKED'),
                network_used=False, native_credential_accessed=False)
    root = new_directory(output)
    item = _write_new(root,'diagnostics.json',safe)
    _write_new(root,'RECEIPT.json',dict(files=[item],scope='LOCAL_RECORDED_STATES'))
    return dict(status='REDACTED_DIAGNOSTICS_EXPORTED')


def asset_change_check(stamp):
    return dict(at=stamp,status='ASSET_CHANGED',expires=None,permissions=[],currency=None,timezone=None)


def invalidate(directory,kind,identifier):
    key = asset_key(kind,identifier)
    def change(p):
        if key not in p['assets']:
            fail('WORKSPACE_ASSET_NOT_MAPPED')
        p['checks'][key] = asset_change_check(utcnow().isoformat())
        if kind=='ACCOUNT' and p['binding'] and identifier in p['binding']['accounts']:
            p['binding']['accounts'].remove(identifier)
        if kind=='PAGE':
            p.get('page_bindings',{}).pop(identifier,None)
    return update(directory,change)


def monitor_scope(profile,connection,clock):
    account = connection['account_id']
    key = asset_key('ACCOUNT',account)
    record = profile['binding'] or {}
    check = profile['checks'].get(key) or {}
    if (profile['skill_version'] not in WORKSPACE_RUNTIME_VERSIONS or profile['adapter']!='v27.0'
            or profile['app_id']!=connection['app_id'] or account not in record.get('accounts',[])
            or record.get('auth_status')!='CURRENT' or record['principal_id']!=connection['principal_id']
            or connection['credential_source']!='OS_REFERENCE' or record['secret_ref']!=connection['credential_reference']
            or key not in profile['assets'] or check.get('status')!='HEALTHY'
            or check.get('permissions')!=['ads_read'] or not record['expires_at']
            or clock()>=min(aware_time(check['expires']),aware_time(record['expires_at']))):
        fail('WORKSPACE_MONITOR_SCOPE_CHANGED')
    return digest(dict(client=profile['client_id'],app=profile['app_id'],business=profile['business_id'],
                       asset=profile['assets'][key],check=check,record=record['record_hash'],
                       adapter=profile['adapter'],skill=profile['skill_version']))


def workspace_identity(profile):
    return digest({key:profile[key] for key in ('client_id','app_id','business_id')})


def checked_account_workspace(directory,intent,clock):
    if directory is None:
        fail('WORKSPACE_MONITOR_PROFILE_REQUIRED')
    try:
        profile = read(directory)
        current = monitor_scope(profile,intent['connection'],clock)
    except (InputError,OSError,UnicodeError):
        fail('WORKSPACE_MONITOR_SCOPE_CHANGED')
    if intent['workspace_scope_sha256']!=current:
        fail('WORKSPACE_MONITOR_SCOPE_CHANGED')
    return profile


def monitor_workspace_account_context(directory,intent,clock):
    profile = checked_account_workspace(directory,intent,clock)
    account_key = asset_key('ACCOUNT',intent['connection']['account_id'])
    check = profile['checks'][account_key]
    return check['timezone'],check['currency']


def record_monitor_account_failure(directory,intent,record,error,clock,*,initial_profile):
    """Expire positive evidence only when the failed run still matches this workspace."""
    if error in MONITOR_AUTH_FAILURES:
        state = 'AUTH_REQUIRED'
    elif error == 'DEBUG_ADS_READ_NOT_GRANTED':
        state = 'PERMISSION_REQUIRED'
    elif error == NAMED_ACCOUNT_PERMISSION_DENIED:
        state = 'NAMED_ACCOUNT_PERMISSION_REQUIRED'
    elif error == 'RETURNED_ACCOUNT_MISMATCH':
        state = 'ASSET_CHANGED'
    elif error in ACCOUNT_SCOPE_ERRORS:
        state = 'ACCOUNT_RECHECK_REQUIRED'
    else:
        return 'NOT_APPLICABLE'
    stamp = clock()
    target_only = False

    def change(profile):
        nonlocal target_only
        connection = intent['connection']
        if intent['workspace_scope_sha256'] != monitor_scope(profile,connection,lambda:stamp):
            fail('WORKSPACE_MONITOR_SCOPE_CHANGED')
        record.check()
        key = asset_key('ACCOUNT',connection['account_id'])
        if state == 'ACCOUNT_RECHECK_REQUIRED':
            profile['checks'].pop(key)
        elif state == 'ASSET_CHANGED':
            profile['checks'][key] = asset_change_check(stamp.isoformat())
        elif state == 'NAMED_ACCOUNT_PERMISSION_REQUIRED':
            profile['checks'][key] = dict(at=stamp.isoformat(),status='PERMISSION_REQUIRED',expires=None,
                                          permissions=[],currency=None,timezone=None)
        else:
            target_only = not _apply_shared_access_failure(profile,initial_profile,record.expected_hash,state,stamp.isoformat())
            if target_only:
                profile['checks'][key] = dict(at=stamp.isoformat(),status=state,expires=None,
                                              permissions=[],currency=None,timezone=None)

    try:
        update(directory,change,lambda:stamp)
    except (InputError,OSError,UnicodeError):
        return 'CURRENT_WORKSPACE_UPDATE_UNVERIFIED'
    return TARGET_ONLY_REVIEW_REQUIRED if target_only else state


def business_account(profile, identifier):
    identifier = identifier if identifier is not None else profile['defaults']['ACCOUNT']
    if identifier is None or asset_key('ACCOUNT',identifier) not in profile['assets']:
        fail('WORKSPACE_BUSINESS_ACCOUNT_REQUIRED')
    return identifier


def business_context(profile, identifier, clock):
    check = profile['checks'].get(asset_key('ACCOUNT',identifier),{})
    now = clock()
    currency = None
    if (check.get('status')=='HEALTHY' and now<aware_time(check['expires'])
            and timedelta(0)<=now-aware_time(check['at'])<TTL):
        currency = check['currency']
    result = business_profile.describe(profile.get('business_profiles',{}).get(identifier),currency)
    return dict(account_id=identifier,dispatchable=False,**result)


def draft_review_scope(profile, account, *, compatible_versions=(), host_draft=False):
    account = business_account(profile,account)
    route = profile.get('connection_onboarding') or {}
    host_account = host_draft and route.get('route')=='host-connector' and any(
        row['account_id']==account for row in ((route.get('discovery') or {}).get('accounts') or []))
    if (profile['adapter']!='v27.0' or profile['skill_version'] not in (*WORKSPACE_RUNTIME_VERSIONS, *compatible_versions)
            or (not host_account and (not profile['app_id'] or not profile['business_id']))):
        fail('WORKSPACE_DRAFT_REVIEW_PROFILE_REQUIRED')
    key = asset_key('ACCOUNT',account)
    recorded = profile['checks'].get(key,{})
    return dict(account_id=account,workspace_identity=workspace_identity(profile),asset=profile['assets'][key],
                business_profile=profile.get('business_profiles',{}).get(account),
                account_context={key:recorded[key] for key in ('currency','timezone')}
                if recorded.get('status')=='HEALTHY' else {})


def draft_review_context(scope, review_directory, *, run_receipts=None):
    from .draft_evidence import load_draft_context
    context = load_draft_context(review_directory,scope['account_id'],scope['business_profile'],
                                 workspace_identity=scope['workspace_identity'],run_receipts=run_receipts)
    if any(value!=context['proposal_basis'][key] for key,value in scope['account_context'].items()):
        fail('DRAFT_REVIEW_ACCOUNT_CONTEXT_CHANGED')
    return context


def prepare_business(directory, identifier, output):
    profile = read(directory)
    identifier = business_account(profile,identifier)
    root = new_directory(output)
    _write_new(root,'business-profile.json',business_profile.blank(profile['client_id'],identifier))
    return dict(status='BUSINESS_PROFILE_TEMPLATE_CREATED',input_file='business-profile.json',dispatchable=False)


def set_business(directory, input_path, previous_sha256=None):
    path = checked_path(input_path,link_error='WORKSPACE_PATH_LINK',volume_error='WORKSPACE_VOLUME')
    if not path.is_file():
        fail('BUSINESS_PROFILE_INPUT_FILE')
    with path.open('rb') as handle:
        raw = handle.read(16001)
    if len(raw)>16000:
        fail('BUSINESS_PROFILE_INPUT_FILE')
    value = business_profile.validate(strict_json(raw.decode('utf-8-sig')))
    if previous_sha256 is not None:
        pattern(previous_sha256,r'[0-9a-f]{64}','BUSINESS_PROFILE_PREVIOUS_HASH')
    def change(profile):
        identifier = business_account(profile,value['account_id'])
        if profile['client_id']!=value['client_id']:
            fail('WORKSPACE_BUSINESS_PROFILE_SCOPE')
        profiles = profile.setdefault('business_profiles',{})
        current = profiles.get(identifier)
        if current == value:
            return
        if previous_sha256 != (digest(current) if current is not None else None):
            fail('BUSINESS_PROFILE_CHANGED_REVIEW_REQUIRED')
        profiles[identifier] = value
    update(directory,change)
    return dict(status='BUSINESS_PROFILE_RECORDED',profile_sha256=digest(value),dispatchable=False)


def closed_account_window(days, account_timezone, stamp):
    if type(days) is not int or not 1 <= days <= INSIGHTS_MAX_DAYS:
        fail('WORKSPACE_COMPLETE_DAYS_OUTSIDE_BUDGET')
    resolved_at = stamp.isoformat()
    stamp = aware_time(resolved_at)
    try:
        zone = load_zone(account_timezone)
    except ZoneInfoNotFoundError:
        fail('WORKSPACE_IANA_ACCOUNT_TIMEZONE_REQUIRED')
    try:
        today = stamp.astimezone(zone).date()
        since = (today - timedelta(days=days)).isoformat()
        until = (today - timedelta(days=1)).isoformat()
    except OverflowError:
        fail('WORKSPACE_DATE_OUTSIDE_CALENDAR')
    return dict(basis='CLOSED_ACCOUNT_CALENDAR_DAYS',days=days,timezone=account_timezone,
                resolved_at=resolved_at,since=since,until=until)


def account_monitor_connection(profile, account):
    binding = profile['binding']
    return dict(blank_intent(),app_id=profile['app_id'],principal_id=binding['principal_id'],account_id=account,
                credential_source='OS_REFERENCE',credential_reference=binding['secret_ref'],
                app_developer_confirmed=True,asset_owner_confirmed=True,appsecret_proof_requirement=binding['proof'])


def replan_monitor(directory, monitor_directory, output, *, record_directory=None,
                   from_comment_checkpoint=False, clock=utcnow):
    """Carry forward only collection settings from a verified run in this workspace."""
    from .monitor_runner import PAGE_LIVE_SCHEMA, load_monitor_intent, validate_monitor_intent
    from .workspace_runner import _read_runner, verify_recorded_intent
    profile = read(directory)
    old_root = checked_path(monitor_directory,link_error='MONITOR_STATE_SYMLINK',volume_error='MONITOR_VOLUME_NOT_MOUNTED')
    old = load_monitor_intent(old_root/'runner-intent.json')
    is_live = old['schema']==PAGE_LIVE_SCHEMA
    if not is_live and old['schema']!='workspace-monitor-intent-v1':
        fail('WORKSPACE_PREVIOUS_NAMED_RUN_REQUIRED')
    if from_comment_checkpoint and (not is_live or old['pages_per_source']!=1 or 'comments' not in old['sources']):
        fail('WORKSPACE_COMMENT_CHECKPOINT_REQUIRES_PAGE_LIVE_SAMPLE')
    state, owner_locked = _read_runner(old_root,old,profile,clock,include_comment_resume=from_comment_checkpoint)
    if state.get('rate_limit_pending'): fail('RATE_LIMIT_WAIT_REQUIRED')
    comment_resume = state.get('comment_resume_evidence')
    if from_comment_checkpoint:
        if owner_locked: fail('WORKSPACE_COMMENT_CHECKPOINT_OWNER_HELD')
        if comment_resume is None: fail('WORKSPACE_COMMENT_CHECKPOINT_UNAVAILABLE')
    previous_scope_evidence = verify_recorded_intent(old_root,old,run_id=state.get('confirmed_run_id'),
                                                    comment_resume=comment_resume)

    sources = old['sources']
    if is_live:
        page = old['page_id']
        route = health_check(directory,'page-live',page,record_directory=record_directory,clock=clock)
        if route['access_observation']!='RECENT_NAMED_PAGE_READ':
            raise PlanPrerequisiteError('WORKSPACE_FRESH_PAGE_CHECK_REQUIRED',route)
        binding = profile.get('page_bindings',{}).get(page)
        if binding is None: fail('WORKSPACE_CHANGED_DURING_CHECK')
        connection = page_intent(profile,page,binding)
        current_scope = partial(page_live_scope,connection=connection)
        details = dict(input_file='page-live-monitor-intent.json',live_scope=dict(
            page_id=page,live_id=sources['live']['object_id'],video_id=old['expected_live_video_id'],
            page_post_id=sources.get('live_post',{}).get('object_id')))
    else:
        account = old['connection']['account_id']
        route = health_check(directory,'ads-monitor',account,record_directory=record_directory,clock=clock)
        if route['access_observation']!='RECENT_NAMED_ACCOUNT_READ':
            raise PlanPrerequisiteError('WORKSPACE_FRESH_ACCOUNT_CHECK_REQUIRED',route)
        binding = profile['binding']
        if binding is None: fail('WORKSPACE_CHANGED_DURING_CHECK')
        connection = account_monitor_connection(profile,account)
        current_scope = partial(monitor_scope,connection=connection,clock=clock)
        check = profile['checks'].get(asset_key('ACCOUNT',account))
        if check is None: fail('WORKSPACE_CHANGED_DURING_CHECK')
        delivery = sources.get('ads') or sources.get('placements')
        dates = {name:delivery[name] for name in ('since','until')} if delivery is not None else None
        details = dict(input_file='monitor-intent.json',dates=dates,current_timezone=check['timezone'],
                       current_currency=check['currency'])
    scope = current_scope(profile)
    intent = dict(old,connection=connection,workspace_scope_sha256=scope)
    if comment_resume is not None:
        intent['sources'] = dict(sources, comments=dict(sources['comments'],since=comment_resume['since']))
        details['comment_resume'] = comment_resume
    validate_monitor_intent(intent)
    journal = journal_for(directory)
    record_directory = record_directory or journal.directory/'credential-record'
    guard = RecordGuard(record_directory,connection,binding['record_hash'])
    result = dict(status='PREPARED',next_command='monitor-preview',
                  previous_intent_sha256=digest(old),new_intent_sha256=digest(intent),previous_run_phase=state['phase'],
                  previous_scope_evidence=previous_scope_evidence,
                  previous_owner_lock_held=owner_locked,requires_new_state_dir=True,fresh_approval_required=True,
                  network_used=False,dispatchable=False,**details)
    with journal.locked():
        if current_scope(load(journal))!=scope: fail('WORKSPACE_CHANGED_DURING_CHECK')
        guard.check()
        root = new_directory(output)
        file = _write_new(root,result['input_file'],intent)
        _write_new(root,'RECEIPT.json',dict(result,files=[file]))
    return result


def prepare_task(directory,task,identifier,output,*,since=None,until=None,last_complete_days=None,record_directory=None,
                 website_conversions=False,hierarchy=False,include_live_links=False,report_pages=None,
                 cycles=None,interval_seconds=None,duration_seconds=None,pages_per_cycle=None,
                 review_directory=None,source_run_receipts=None,clock=utcnow):
    """Build native workflow inputs locally. A plan never starts a collector."""
    from dataclasses import asdict
    from .meta_write_mapping import PROPOSAL_COMPARISON, blank_draft, validate_draft
    from .monitor import AD_OBJECT_SOURCES
    from .monitor_runner import (PLACEMENT_GET_LIMIT, PLACEMENT_PAGE_LIMIT, PLACEMENT_PAGES,
                                 blank_monitor_intent, validate_monitor_intent)
    if review_directory is not None and task != 'ad-draft':
        fail('WORKSPACE_REVIEW_INPUT_REQUIRES_DRAFT_TASK')
    if source_run_receipts is not None and review_directory is None:
        fail('DRAFT_REVIEW_SOURCE_RECEIPTS_REQUIRE_COMPARISON')
    profile = read(directory)
    route = health_check(directory,task,identifier,record_directory=record_directory,clock=clock)
    account = route['target_id']
    if TASKS[task][0]=='PAGE':
        fail('WORKSPACE_TASK_REVIEW_REQUIRED')
    single_report = task in ADS_REPORT_TASKS
    inventory = task == 'ads-inventory'
    if include_live_links and not single_report:
        fail('WORKSPACE_LIVE_LINKS_REQUIRE_SINGLE_ADS_REVIEW')
    if report_pages is not None:
        if not (single_report or inventory): fail('WORKSPACE_REPORT_PAGES_REQUIRE_SINGLE_ADS_REVIEW')
        positive_integer(report_pages,MAX_PAGES_PER_SOURCE)
    cadence = {key:value for key,value in dict(cycles=cycles,interval_seconds=interval_seconds,
                                              duration_seconds=duration_seconds,
                                              pages_per_source=pages_per_cycle).items() if value is not None}
    if cadence and task!='ads-monitor': fail('WORKSPACE_ADS_CADENCE_REQUIRES_MONITOR')
    if any(type(value) is not int for value in cadence.values()): fail('INVALID_BUDGET')
    if route['mode'] != 'DAILY_OPERATION':
        raise PlanPrerequisiteError('WORKSPACE_TASK_REVIEW_REQUIRED', route)
    date_selection = None
    proposal_context = None
    if task in ('ad-draft','ads-inventory','ads-placements'):
        if website_conversions:
            fail('WORKSPACE_WEBSITE_CONVERSIONS_REQUIRE_INSIGHTS_TASK')
        if hierarchy:
            fail('WORKSPACE_HIERARCHY_REQUIRES_INSIGHTS_TASK')
        if task != 'ads-placements' and (since is not None or until is not None or last_complete_days is not None):
            fail('WORKSPACE_UNUSED_DATES')
    if task == 'ad-draft':
        if review_directory is not None:
            proposal_context = draft_review_context(draft_review_scope(profile,account),review_directory,
                                                    run_receipts=source_run_receipts)
        item = blank_draft('CREATE_AD')
        item.update(account_id=account)
        if proposal_context is not None:
            item['context'][PROPOSAL_COMPARISON] = proposal_context['comparison_sha256']
        validate_draft(item)
        filename, next_command = 'draft.json', 'meta-write-review'
    else:
        b = profile['binding']
        if route['access_observation'] != 'RECENT_NAMED_ACCOUNT_READ' or not b:
            raise PlanPrerequisiteError('WORKSPACE_FRESH_ACCOUNT_CHECK_REQUIRED', route)
        if last_complete_days is not None:
            if since is not None or until is not None:
                fail('WORKSPACE_DATE_SELECTION_CONFLICT')
            account_timezone = profile['checks'][asset_key('ACCOUNT',account)]['timezone']
            date_selection = closed_account_window(last_complete_days,account_timezone,clock())
            since, until = date_selection['since'], date_selection['until']
        item = blank_monitor_intent()
        item['connection'] = account_monitor_connection(profile,account)
        item['sources'] = {'account': asdict(ReadPlan('account',account,account_id=account))}
        if inventory:
            for channel, operation in AD_OBJECT_SOURCES.items():
                item['sources'][channel] = asdict(ReadPlan(operation,account,account_id=account,limit=50))
        elif task=='ads-placements':
            placements = ReadPlan(PLACEMENT_INSIGHTS,account,account_id=account,since=since,until=until,
                                  limit=PLACEMENT_PAGE_LIMIT)
            item['sources']['placements'] = asdict(placements)
            item.update(cycles=1,pages_per_source=PLACEMENT_PAGES,request_budget=PLACEMENT_GET_LIMIT)
        else:
            operation = INSIGHTS_BY_OPTIONS[hierarchy, website_conversions]
            ads_plan = ReadPlan(operation,account,account_id=account,since=since,until=until,limit=ADS_REPORT_PAGE_SIZE)
            item['sources']['ads'] = asdict(ads_plan)
            if include_live_links:
                item['sources']['ad_links'] = asdict(ReadPlan('ad_videos',account,account_id=account,limit=ADS_REPORT_PAGE_SIZE))
            if not single_report:
                if hierarchy:
                    item['pages_per_source'] = DEFAULT_ADS_REPORT_PAGES
                item.update(cadence)
                # Every round refreshes the account and can spend its page limit.
                item['request_budget'] = 2+item['cycles']*(1+item['pages_per_source'])
        if single_report or inventory:
            pages = DEFAULT_ADS_REPORT_PAGES if report_pages is None else report_pages
            # Two startup GETs, one current account read and each paged source.
            item.update(cycles=1,pages_per_source=pages,request_budget=3+pages*(len(item['sources'])-1))
        item.update(schema='workspace-monitor-intent-v1',workspace_scope_sha256=monitor_scope(profile,item['connection'],clock))
        validate_monitor_intent(item)
        if task=='ads-monitor' and (item['cycles']-1)*item['interval_seconds'] >= item['duration_seconds']:
            fail('WORKSPACE_ADS_MONITOR_INTERVALS_EXCEED_DURATION')
        filename, next_command = 'monitor-intent.json', 'monitor-preview'
    journal = journal_for(directory)
    context = business_context(profile,account,clock) if task not in ('ads-monitor','ads-inventory','ads-placements') else None
    with journal.locked():
        if digest(load(journal))!=digest(profile):
            fail('WORKSPACE_CHANGED_DURING_CHECK')
        root = new_directory(output)
        files = [_write_new(root,filename,item)]
        result = dict(status='PREPARED',input_file=filename,next_command=next_command,network_used=False,dispatchable=False)
        receipt = dict(files=files,task=task,network_used=False,dispatchable=False)
        if date_selection is not None:
            result['date_selection'] = date_selection
            receipt['date_selection'] = date_selection
        if context is not None:
            result['business_profile_status'] = context['status']
            if context['status']=='DECLARED_BUSINESS_PROFILE':
                files.append(_write_new(root,'business-context.json',context))
                result['business_context_file'] = 'business-context.json'
        if proposal_context is not None:
            basis = proposal_context['proposal_basis']
            files.append(_write_new(root,'proposal-context.json',proposal_context))
            result['proposal_context_file'] = 'proposal-context.json'
            result['proposal_decision'] = basis['decision']
            if 'evidence_basis' in basis:
                result['proposal_evidence_basis'] = basis['evidence_basis']
                receipt['proposal_evidence_basis'] = basis['evidence_basis']
            if 'input_runs_verification' in proposal_context:
                result['input_runs_verification'] = proposal_context['input_runs_verification']
                receipt['input_runs_verification'] = result['input_runs_verification']
        _write_new(root,'RECEIPT.json',receipt)
    return result


def preview_live(directory,page_id,live_id,video_id=None,page_post_id=None,*,record_directory=None,output=None,
                 live_url=None,live_state_directory=None,previous_page_run=False):
    from .live_contract import planned_queries
    from .live_reference import select_live_reference
    from .read_adapter import post_identifier
    route = health_check(directory,'page-live',page_id,record_directory=record_directory)
    if route['target_id'] is None or route['status'] in ('BLOCKED','ASSET_CHANGED','VERSION_MISMATCH'):
        fail('WORKSPACE_PAGE_REVIEW_REQUIRED')
    page = numeric(route['target_id'])
    profile = read(directory) if live_state_directory is not None else None
    live_id, video_id, reference = select_live_reference(profile,page,live_id,video_id,live_url,live_state_directory,
                                                         previous_page_run=previous_page_run)
    relation = dict(page_id=page,live_id=live_id,video_id=video_id)
    if page_post_id is not None:
        post_identifier(page_post_id,page)
    plan = planned_queries(relation,page_post_id) if live_id is not None else None
    preview = dict(scope=relation, declared_page_post=page_post_id, page_route=route, plan=plan,
                   relation_verified=False, dispatchable=False, real_live_verified=False)
    if reference is not None:
        preview['live_reference'] = reference
        if live_id is None:
            preview.update(status='LIVE_OBJECT_ID_REQUIRED',next_check='PROVIDE_LIVE_ID_OR_MATCHING_SAVED_RUN')
            preview['live_id_inputs'] = {
                'live_state_dir': ['MATCHING_SAVED_PAGE_LIVE_RUN'],
                'live_id': ['SAME_PAGE_LIVE_CREATION_RESULT', 'EXISTING_SAME_PAGE_LIVE_STATUS_EVENT'],
            }
    if output is not None:
        root = new_directory(output)
        file = _write_new(root,'LIVE_PREVIEW.json',preview)
        _write_new(root,'RECEIPT.json',dict(files=[file],network_used=False,dispatchable=False))
    return preview


def page_live_scope(profile, connection, *, include_check=True):
    page_id = connection['page_id']
    key = asset_key('PAGE',page_id)
    binding = profile.get('page_bindings',{}).get(page_id)
    if (binding is None or key not in profile['assets'] or profile['adapter']!='v27.0'
            or profile['skill_version'] not in WORKSPACE_RUNTIME_VERSIONS or connection!=page_intent(profile,page_id,binding)):
        fail('WORKSPACE_PAGE_LIVE_SCOPE_CHANGED')
    scope = dict(client=profile['client_id'],business=profile['business_id'],asset=profile['assets'][key],binding=binding,
                 adapter=profile['adapter'],skill=profile['skill_version'])
    if include_check:
        scope['check'] = profile['checks'].get(key)
    return digest(scope)


def check_page_live_workspace(directory, intent, record_sha256):
    if directory is None:
        fail('WORKSPACE_MONITOR_PROFILE_REQUIRED')
    try:
        profile = read(directory)
        current = page_live_scope(profile,intent['connection'])
    except (InputError,OSError,UnicodeError):
        fail('WORKSPACE_PAGE_LIVE_SCOPE_CHANGED')
    if current!=intent['workspace_scope_sha256']:
        fail('WORKSPACE_PAGE_LIVE_SCOPE_CHANGED')
    if record_sha256 is not None and record_sha256!=profile['page_bindings'][intent['page_id']]['record_hash']:
        fail('WORKSPACE_PAGE_LIVE_SCOPE_CHANGED')
    key = asset_key('PAGE',intent['page_id'])
    if asset_reconfirmation_pending(profile,key):
        fail(ASSET_RECONFIRMATION_REQUIRED)
    if profile['checks'].get(key,{}).get('status')=='PERMISSION_REQUIRED':
        fail(PAGE_LIVE_PERMISSION_RECHECK)
    return profile


def record_page_live_developer_rejection(directory, intent, record, approved_at, clock, *, named_permission=False):
    """Record confirmed Developer User rejection without replacing newer observations."""
    stamp = clock()

    def change(profile):
        connection = intent['connection']
        binding = profile.get('page_bindings',{}).get(connection['page_id'])
        if (page_live_scope(profile,connection)!=intent['workspace_scope_sha256']
                or record is None or binding['record_hash']!=record.expected_hash):
            fail('WORKSPACE_PAGE_LIVE_SCOPE_CHANGED')
        record.check()
        record_hash = record.expected_hash
        account = profile['binding']
        shared_account = account is not None and account['record_hash']==record_hash
        page_key = asset_key('PAGE',connection['page_id'])
        keys = {page_key} if named_permission else _shared_access_keys(profile,record_hash)
        if any(key in profile['checks'] and aware_time(profile['checks'][key]['at']) >= approved_at for key in keys):
            fail('WORKSPACE_CHANGED_DURING_CHECK')
        if named_permission:
            profile['checks'][page_key] = dict(at=stamp.isoformat(),status='PERMISSION_REQUIRED',expires=None,
                                               permissions=[],currency=None,timezone=None)
        else:
            invalidate_pages_sharing_reference(profile,record_hash,stamp.isoformat())
            if shared_account:
                invalidate_account_checks(profile,'AUTH_REQUIRED')

    try:
        update(directory,change,lambda:stamp)
    except (InputError,OSError,UnicodeError):
        return 'CURRENT_WORKSPACE_UPDATE_UNVERIFIED'
    return 'PERMISSION_REQUIRED' if named_permission else 'AUTH_REQUIRED'


def prepare_live_monitor(directory,page_id,live_id,video_id,page_post_id,output,*,record_directory=None,
                         verify_insights_role=False,live_url=None,live_state_directory=None,previous_page_run=False,cycles=1,
                         interval_seconds=60,duration_seconds=300):
    from dataclasses import asdict
    from .live_reference import select_live_reference
    from .metric_adapter import METRIC_CHANNELS, PostMetricReadPlan
    from .monitor_runner import PAGE_LIVE_SCHEMA, blank_monitor_intent, validate_monitor_intent
    from .page_live_credentials import ROLE_OPTION, startup_get_limit
    from .read_adapter import RECENT_LIVE_COMMENTS, PageLiveReadPlan, ReadPlan, positive_integer
    if type(verify_insights_role) is not bool: fail('PAGE_INSIGHTS_ROLE_OPTION_INVALID')
    if verify_insights_role and page_post_id is None: fail('PAGE_INSIGHTS_ROLE_REQUIRES_METRICS')
    positive_integer(cycles,10000)
    route = health_check(directory,'page-live',page_id,record_directory=record_directory)
    page = route['target_id']
    if route['status']=='PERMISSION_REQUIRED':
        fail(PAGE_LIVE_PERMISSION_RECHECK)
    if page is None or route['status'] in ('BLOCKED','ASSET_CHANGED','VERSION_MISMATCH','AUTH_REQUIRED'):
        fail('WORKSPACE_PAGE_REVIEW_REQUIRED')
    profile,_,binding,record_directory,connection = page_context(directory,page,record_directory)
    live_id, video_id, reference = select_live_reference(profile,page,live_id,video_id,live_url,live_state_directory,
                                                         previous_page_run=previous_page_run)
    if live_id is None:
        fail('WORKSPACE_LIVE_OBJECT_ID_REQUIRED')
    RecordGuard(record_directory,connection,binding['record_hash']).check()
    plans = {
        'page': ReadPlan('page',page,page_id=page),
        'live': ReadPlan('live',live_id,page_id=page,reviewed_live_contract=True),
    }
    if video_id is not None:
        plans['live'] = PageLiveReadPlan(**asdict(plans['live']), expected_live_video_id=video_id)
        plans['comments'] = ReadPlan(RECENT_LIVE_COMMENTS,live_id,page_id=page,reviewed_live_contract=True)
    if page_post_id is not None:
        if video_id is None:
            fail('WORKSPACE_LIVE_VIDEO_REQUIRED')
        plans['live_post'] = ReadPlan('live_post',page_post_id,page_id=page,reviewed_live_contract=True)
        plans.update({name:PostMetricReadPlan(object_id=page_post_id,page_id=page,live_id=live_id,video_id=video_id,metric=metric)
                      for name,metric in METRIC_CHANNELS.items()})
    scope = page_live_scope(profile,connection)
    intent = blank_monitor_intent()
    intent.update(schema=PAGE_LIVE_SCHEMA,connection=connection,page_id=page,expected_live_video_id=video_id,
                  sources={key:asdict(plan) for key,plan in plans.items()},
                  workspace_scope_sha256=scope,cycles=cycles,interval_seconds=interval_seconds,
                  duration_seconds=duration_seconds)
    if verify_insights_role:
        intent[ROLE_OPTION] = True
    intent['request_budget'] = startup_get_limit(intent)+cycles*len(plans)
    validate_monitor_intent(intent)
    journal = journal_for(directory)
    with journal.locked():
        if scope!=page_live_scope(load(journal),connection):
            fail('WORKSPACE_PAGE_LIVE_SCOPE_CHANGED')
        root = new_directory(output)
        file = _write_new(root,'page-live-monitor-intent.json',intent)
        receipt = dict(files=[file],task='page-live',network_used=False,dispatchable=False)
        if reference is not None:
            receipt['live_reference'] = reference
        _write_new(root,'RECEIPT.json',receipt)
    return dict(status='PREPARED',input_file=file['path'],next_command='monitor-preview',
                page_check=route['access_observation'],network_used=False,dispatchable=False)


def add_commands(sub):
    commands = {}
    help_text = {
        'init':'初始化非敏感工作区，可复用现有准备表', 'onboard':'补全已存在工作区的具名准备资料', 'show':'显示脱敏配置摘要',
        'onboarding-report':'导出指定范围的本地接入报告与待核事项',
        'connection-prepare':'按已映射广告账户和非秘密登记准备具名连接意图',
        'exit-report':'导出工作区退出交接的仅本机核对证据',
        'add':'按归属声明添加单一映射，访问能力需另行验证', 'default':'明确指定默认资产',
        'unmap':'移除单个本地映射', 'route':'按任务检查目标范围，返回当前工作流',
        'page-bind':'核对角色与具名系统登记，仅绑定一个公共主页',
        'page-preview':'展示单个公共主页的只读请求及阻断项',
        'page-refresh':'本人交互确认后核验一个公共主页的读取',
        'live-preview':'根据具名直播、Video 与 Page Post 展示待核对的固定只读请求',
        'live-plan':'为已绑定的 Page 输出独立直播读取计划，供本地预览',
        'business-prepare':'为具名广告账户生成本地业务档案空表',
        'business-set':'保存填好的本地业务档案',
        'business-show':'查看具名广告账户的业务档案和当前版本摘要',
        'first-launch-plan':'按一个已选账户与主页准备首投的四份本地草案',
        'creative-research':'在原工作区保留品牌学习、来源、选题和素材评语',
        'public-ad-observations':'在原工作区规范化公开广告样本',
        'first-launch-check':'核对首投草案仍对应原工作区及已选账户、主页',
        'first-launch-media':'审阅受管素材上传或只读核对原上传',
        'first-launch-update':'审阅、执行或恢复已记录和已读取的具名广告调整',
        'first-launch-host':'导出原计划的本机受控连接范围，或核对已运行的本机批准服务',
        'first-launch-handoff':'从普通安装接续同一首投的客户后台操作与回读',
        'first-launch-progress':'沿同一首投草案留存具名对象回读并准备下一步',
        'first-launch-preview':'预审或读取原首投对象的移动动态预览',
        'first-launch-activation-review':'用具名预览、预算和时窗准备独立启用审阅',
        'first-launch-activation':'预审同一 Ad 的启用范围或回读原尝试',
        'first-launch-delivery':'用既有读取归档复盘本次首投的同一广告',
        'first-launch-evidence':'审阅同一广告的创意与账户活动读取范围',
        'bind':'绑定非敏感 OS 引用记录和具名账户意图', 'refresh':'本人交互确认后核对一个账户的连接',
        'plan':'用已保存的资产准备单一日常任务输入', 'replan':'按同一工作区的历史运行与当前具名接入准备新读取计划',
        'removal-plan':'预览退出范围及需单独确认的操作', 'diagnostics':'导出脱敏的本地状态', 'migrate':'备份旧工作区并在新目录迁移',
        'invalidate':'标记单一资产的共享或角色有变', 'backup':'备份可移植配置', 'restore':'在全新目录恢复配置'}
    for name, help_line in help_text.items():
        command = sub.add_parser('workspace-'+name, help=help_line)
        command.add_argument('--workspace', required=True, type=Path)
        commands[name] = command
    commands['init'].add_argument('--preparation',type=Path)
    for name in ('creative-research','public-ad-observations'):
        commands[name].add_argument('--id',help='具名广告账户，省略时使用项目默认账户')
        commands[name].add_argument('--input',type=Path)
    commands['creative-research'].add_argument('--public-observations', type=Path)
    commands['creative-research'].add_argument('--product', help='公开观察关联到自己的具名商品')
    first_launch = commands['first-launch-plan']
    first_launch.add_argument('--account-id')
    first_launch.add_argument('--page-id')
    first_launch.add_argument('--select-creative', action='append', nargs=3, metavar=('CONCEPT', 'VARIANT', 'ASSET_SHA256'),
                              help='将素材评语和创意选择摘要冻结到首投计划')
    first_launch.add_argument('--selection-file', action='append', type=Path, default=[],
                              help='导入创意对照页中保存的选择')
    first_launch.add_argument('--output',type=Path,help='默认保存在工作区的 first-launch-plan 目录')
    commands['first-launch-check'].add_argument('--plan-dir',type=Path)
    media = commands['first-launch-media']
    media.add_argument('--plan-dir', type=Path)
    media.add_argument('--file', type=Path)
    media.add_argument('--kind', choices=('IMAGE','VIDEO'))
    media.add_argument('--packet', type=Path)
    media.add_argument('--mode', choices=('plan', 'dispatch', 'recover', 'continue', 'reuse'), default='plan',
                       help='recover 核对原尝试；仅封存未发送后 continue；复用已上传素材用 reuse')
    update = commands['first-launch-update']
    from .first_launch_update import ACTIONS
    update.add_argument('--action', choices=ACTIONS)
    update.add_argument('--plan-dir', type=Path)
    update.add_argument('--existing-ad-snapshot', type=Path, help='存量广告的完整 native-inventory get ad 回执')
    update.add_argument('--existing-page')
    update.add_argument('--affected-ads-snapshot', type=Path, help='广告组或系列的完整 native-inventory list ad 回执')
    update.add_argument('--budget-minor', type=int)
    update.add_argument('--change-file', type=Path, help='具名出价、受众、停止时间或已审阅 Creative 的明确变更值')
    update.add_argument('--packet', type=Path)
    update.add_argument('--mode', choices=('plan','observe','dispatch','recover'), default='plan')
    host_command = commands['first-launch-host']
    host_command.add_argument('--plan-dir',type=Path)
    host_command.add_argument('--status',action='store_true',help='只核对具名本机批准服务')
    handoff = commands['first-launch-handoff']
    handoff.add_argument('--plan-dir',type=Path)
    handoff.add_argument('--image-observation',type=Path)
    handoff.add_argument('--review',type=Path,help='同一 Ad 的启用审阅')
    handoff.add_argument('--before-parents',type=Path,help='两级父对象与影响范围的操作前观察')
    handoff.add_argument('--after-parents',type=Path,help='两级父对象与影响范围的操作后观察')
    handoff.add_argument('--ad-observation',type=Path,help='原 Ad 启用尝试或对象回读')
    handoff.add_argument('--reserve-manual', action='store_true',
                         help='仅首次已批准且尚未操作时，在原账本预留人工启用；重复调用仅供恢复')
    handoff.add_argument('--output',type=Path,help='在新目录保存脱敏接续回执')
    launch_progress = commands['first-launch-progress']
    launch_progress.add_argument('--plan-dir',type=Path)
    launch_update = launch_progress.add_mutually_exclusive_group()
    launch_update.add_argument('--reserve',action='store_true',help='记录一个待核的本机步骤，不发送请求')
    launch_update.add_argument('--readback',type=Path,help='登记当前步骤的脱敏对象观察')
    launch_update.add_argument('--checkpoint',type=Path,help='登记客户本次决定和下次检查日期')
    launch_update.add_argument('--dispatch-approved',metavar='STAGE_SHA256',help='由已接入的可信宿主处理该步骤的一次暂停创建')
    launch_update.add_argument('--recover-host',metavar='STAGE_SHA256',help='由可信宿主只读恢复原尝试')
    launch_preview = commands['first-launch-preview']
    launch_preview.add_argument('--plan-dir',type=Path)
    launch_preview.add_argument('--creative-fallback',action='store_true',help='明确选择同一对象链的 Creative 预览')
    launch_preview.add_argument('--fetch',action='store_true',help='由独立授权的可信宿主提交一次预览读取')
    launch_preview.add_argument('--output',type=Path,help='在新目录保存脱敏观察回执')
    activation = commands['first-launch-activation-review']
    activation.add_argument('--plan-dir',type=Path)
    activation.add_argument('--preview-observation',type=Path,required=True)
    activation.add_argument('--start-at',required=True)
    activation.add_argument('--end-at',required=True)
    activation.add_argument('--maximum-spend-minor',type=int,required=True)
    activation.add_argument('--parent-budget', nargs=3, metavar=('OWNER', 'SCHEDULE', 'AMOUNT_MINOR'),
                            help='已有父级的具名预算范围，经受管服务再次核对')
    activation.add_argument('--output',type=Path)
    activation_action = commands['first-launch-activation']
    activation_action.add_argument('--plan-dir',type=Path)
    activation_action.add_argument('--review',type=Path,help='独立启用审阅文件；恢复有原审核的账本时可省略')
    activation_mode = activation_action.add_mutually_exclusive_group()
    activation_mode.add_argument('--execute-approved',action='store_const',const='dispatch',dest='mode',
                                 help='由已接入的可信宿主执行一次独立获准的单 Ad 启用')
    activation_mode.add_argument('--recover',action='store_const',const='recover',dest='mode',
                                 help='由可信宿主只读恢复本次启用尝试')
    activation_mode.add_argument('--host-approval',action='store_const',const='host-approval',dest='mode',
                                 help='在独立只读批准下为原 Ad 取实际控制证据，生成待批准范围')
    activation_action.set_defaults(mode='plan')
    launch_delivery = commands['first-launch-delivery']
    launch_delivery.add_argument('--plan-dir',type=Path)
    launch_delivery.add_argument('--run-receipt',type=Path,required=True)
    launch_delivery.add_argument('--since',required=True)
    launch_delivery.add_argument('--until',required=True)
    launch_delivery.add_argument('--activation-review',type=Path)
    launch_delivery.add_argument('--manual-before',type=Path,help='A1 原父级操作前观察，可沿原计划自动发现')
    launch_delivery.add_argument('--manual-after',type=Path,help='A1 原父级操作后观察，可沿原计划自动发现')
    launch_delivery.add_argument('--manual-ad',type=Path,help='A1 原 Ad 尝试观察，可沿原计划自动发现')
    launch_delivery.add_argument('--evidence',type=Path,help='同一首投、广告和观察窗口的创意及活动回执')
    launch_evidence = commands['first-launch-evidence']
    launch_evidence.add_argument('--plan-dir', type=Path)
    launch_evidence.add_argument('--since', required=True)
    launch_evidence.add_argument('--until', required=True)
    launch_evidence.add_argument('--execute-read', action='store_true')
    for selection in ('first-launch-progress', 'first-launch-preview', 'first-launch-activation-review',
                      'first-launch-activation', 'first-launch-delivery', 'first-launch-evidence', 'first-launch-update'):
        commands[selection].add_argument('--ad-id', help='选择同一批次中已有回读的广告；省略时沿原入口')
    commands['onboard'].add_argument('--preparation',type=Path,required=True)
    from .workspace_onboarding import SCOPES
    onboarding = commands['onboarding-report']
    onboarding.add_argument('--scope',required=True,choices=SCOPES)
    onboarding.add_argument('--account-id')
    onboarding.add_argument('--page-id')
    onboarding.add_argument('--record-dir',type=Path)
    onboarding.add_argument('--page-record-dir',type=Path)
    onboarding.add_argument('--output',required=True,type=Path)
    connection = commands['connection-prepare']
    connection.add_argument('--kind',choices=('ACCOUNT','PAGE'),default='ACCOUNT')
    connection.add_argument('--id',help='具名资产 ID，省略时使用同类别的已选默认项')
    connection.add_argument('--record-dir',type=Path)
    connection.add_argument('--role',choices=PAGE_ROLES,help='仅用于公共主页')
    connection.add_argument('--role-evidence-ref',help='公共主页的本地角色证据编号')
    connection.add_argument('--output',required=True,type=Path)
    connection.add_argument('--app-developer-confirmed',action='store_true')
    connection.add_argument('--asset-owner-confirmed',action='store_true')
    connection.add_argument('--appsecret-proof',choices=('UNKNOWN','REQUIRED','NOT_REQUIRED_CONFIRMED'),default='UNKNOWN')
    exit_report = commands['exit-report']
    exit_report.add_argument('--monitor-dir',action='append',type=Path,default=[])
    exit_report.add_argument('--record-dir',action='append',type=Path,default=[])
    exit_report.add_argument('--mapped-ledger-dir',action='append',type=Path,default=[],
                             help='核对明确选定的本地模拟审批账本，可重复提供')
    exit_report.add_argument('--output',required=True,type=Path)
    for name in ('add','default','unmap','invalidate'):
        commands[name].add_argument('--kind',required=True,choices=('ACCOUNT','PAGE'))
        commands[name].add_argument('--id',required=True)
    commands['add'].add_argument('--evidence-ref',required=True)
    commands['bind'].add_argument('--intent',required=True,type=Path)
    for name in ('bind','refresh','route','plan','replan','page-bind','page-preview','page-refresh','live-preview','live-plan'):
        commands[name].add_argument('--record-dir',type=Path)
    for name in ('route','plan'):
        commands[name].add_argument('--task', choices=TASKS, required=True)
        commands[name].add_argument('--id')
    for name in ('live-preview','live-plan'):
        commands[name].add_argument('--id')
        commands[name].add_argument('--live-id',help='具名 Live 对象 ID')
        commands[name].add_argument('--live-url',help='公开 Facebook 播放链接，仅读取 Video ID 提示')
        commands[name].add_argument('--live-state-dir',type=Path,help='与播放链接一起使用的同工作区历史直播状态')
        commands[name].add_argument('--previous-page-run',action='store_true',help='从同目录上一次具名运行读取已保存的对象关系')
        commands[name].add_argument('--video-id',help='可省略，先只预览具名直播对象的核对请求')
        commands[name].add_argument('--page-post-id')
        commands[name].add_argument('--output',type=Path,required=name=='live-plan')
    commands['live-plan'].add_argument('--verify-insights-role',action='store_true',
                                      help='在指标计划中预览具名任务查询，最多两页，仅请求主页 ID 与任务')
    commands['live-plan'].add_argument('--cycles',type=int,default=1,help='本次最多采集轮数')
    commands['live-plan'].add_argument('--interval-seconds',type=int,default=60,help='两轮之间的等待秒数')
    commands['live-plan'].add_argument('--duration-seconds',type=int,default=300,help='本次前台运行时长上限，含启动核验')
    for name in ('route','plan'):
        commands[name].add_argument('--since')
        commands[name].add_argument('--until')
        commands[name].add_argument('--last-complete-days',type=int,
                                    help=f'按已核验账户时区取最近已结束的1至{INSIGHTS_MAX_DAYS}个自然日')
        commands[name].add_argument('--website-conversions',action='store_true',
                                    help='选择显式7天点击归因的站点购买和线索口径')
        commands[name].add_argument('--hierarchy',action='store_true',
                                    help='选择包含广告系列和广告组的 Insights 口径')
    commands['plan'].add_argument('--include-live-links',action='store_true',
                                  help='单轮报告另读取广告创意的直接 Video ID')
    commands['plan'].add_argument('--report-pages',type=int,
                                  help=f'单轮广告报告或盘点，每个分页来源的页数上限 (1–{MAX_PAGES_PER_SOURCE})')
    commands['plan'].add_argument('--cycles',type=int,help='广告周期监控的轮数上限')
    commands['plan'].add_argument('--interval-seconds',type=int,help='广告监控两轮之间的等待秒数')
    commands['plan'].add_argument('--duration-seconds',type=int,help='广告监控时长上限，含启动核验')
    commands['plan'].add_argument('--pages-per-cycle',type=int,
                                  help=f'广告监控单轮 Insights 页数上限 (1–{MAX_PAGES_PER_SOURCE})')
    commands['plan'].add_argument('--review-dir',type=Path,help='草案可引用已保存的有效询盘分期复盘目录')
    commands['plan'].add_argument('--source-run-receipts',nargs=2,type=Path,metavar=('BEFORE','AFTER'),
                                  help='用前期和后期原始采集回执复核草案依据')
    for name in ('business-prepare','business-show'):
        commands[name].add_argument('--id')
    commands['business-set'].add_argument('--input',required=True,type=Path)
    commands['business-set'].add_argument('--replace-profile-sha256')
    commands['route'].add_argument('--monitor-dir',type=Path)
    scoped_diagnostics = commands['diagnostics']
    scoped_diagnostics.add_argument('--task',choices=DIAGNOSTIC_TASKS,help='仅导出所选任务的脱敏状态')
    scoped_diagnostics.add_argument('--id',help='所选目标，省略时使用工作区明确登记的默认项')
    scoped_diagnostics.add_argument('--record-dir',type=Path)
    scoped_diagnostics.add_argument('--monitor-dir',type=Path)
    commands['replan'].add_argument('--monitor-dir',required=True,type=Path)
    commands['replan'].add_argument('--from-comment-checkpoint',action='store_true',
                                     help='从原具名直播的已保存评论样本生成带固定时间下界的新计划')
    commands['migrate'].add_argument('--source',required=True,type=Path)
    commands['migrate'].add_argument('--backup-dir',required=True,type=Path)
    for name in ('refresh','page-bind','page-preview','page-refresh'):
        commands[name].add_argument('--id',required=True)
    commands['page-bind'].add_argument('--role',choices=PAGE_ROLES,required=True)
    commands['page-bind'].add_argument('--role-evidence-ref',required=True)
    commands['page-bind'].add_argument('--app-developer-confirmed',action='store_true')
    commands['page-bind'].add_argument('--proof-not-required-confirmed',action='store_true')
    for name in ('backup','refresh','diagnostics','plan','replan','page-refresh','business-prepare'):
        commands[name].add_argument('--output',required=True,type=Path)
    commands['restore'].add_argument('--backup-file',type=Path,required=True)
    restore_gate = commands['restore'].add_mutually_exclusive_group()
    restore_gate.add_argument('--preview', action='store_true', help='查看待恢复的档案及目标，预览不写文件')
    restore_gate.add_argument('--restore-plan-sha256', help='按预览的配置、回执与目标恢复')


def run_command(args, *, first_launch_provider=None, first_launch_preview_provider=None, first_launch_activation_provider=None):
    name = args.command.removeprefix('workspace-')
    root = args.workspace
    if name=='creative-research':
        from .creative_research import run_research
        return run_research(root,args.id,args.input,public_path=args.public_observations,product=args.product)
    if name=='public-ad-observations':
        from .creative_research import public_observations
        return public_observations(root,args.id,args.input)
    if name=='init':
        return initialize(root,args.preparation)
    if name=='onboard':
        form = load_preparation(args.preparation)
        return update(root,lambda p:merge_preparation(p,form))
    if name=='onboarding-report':
        from .workspace_onboarding import save_report
        return save_report(root,args.output,args.scope,account_id=args.account_id,page_id=args.page_id,
                           record_directory=args.record_dir,page_record_directory=args.page_record_dir)
    if name=='connection-prepare':
        return prepare_asset_connection(root,args.output,kind=args.kind,identifier=args.id,record_directory=args.record_dir,
                                        app_developer_confirmed=args.app_developer_confirmed,role=args.role,
                                        asset_owner_confirmed=args.asset_owner_confirmed,proof=args.appsecret_proof,
                                        role_evidence=args.role_evidence_ref)
    if name=='exit-report':
        from .workspace_exit import save_report
        return save_report(root,args.output,monitor_directories=args.monitor_dir,record_directories=args.record_dir,
                           mapped_ledger_directories=args.mapped_ledger_dir)
    if name=='invalidate':
        return invalidate(root,args.kind,args.id)
    if name=='migrate':
        return migrate(args.source,root,args.backup_dir)
    if name=='removal-plan':
        return removal_plan(root)
    if name=='diagnostics':
        if args.task is not None:
            from .workspace_diagnostics import save_task_diagnostics
            return save_task_diagnostics(root,args.output,args.task,args.id,
                                         record_directory=args.record_dir,monitor_directory=args.monitor_dir)
        if args.id is not None or args.record_dir is not None or args.monitor_dir is not None:
            fail('WORKSPACE_DIAGNOSTICS_TASK_REQUIRED')
        return diagnostics(root,args.output)
    if name=='show':
        return summary(read(root))
    if name=='business-prepare':
        return prepare_business(root,args.id,args.output)
    if name=='business-set':
        return set_business(root,args.input,args.replace_profile_sha256)
    if name=='business-show':
        profile = read(root)
        return business_context(profile,business_account(profile,args.id),utcnow)
    if name in ('first-launch-plan','first-launch-check'):
        from . import first_launch_plan
        if name=='first-launch-plan':
            return first_launch_plan.prepare(root,args.output,account_id=args.account_id,page_id=args.page_id,
                                             creative_selection=args.select_creative, selection_files=args.selection_file)
        return first_launch_plan.check(root,args.plan_dir)
    if name=='first-launch-progress':
        from .first_launch_progress import progress
        return progress(root,args.plan_dir,reserve=args.reserve,readback=args.readback,checkpoint=args.checkpoint,
                        dispatch_stage=args.dispatch_approved,recover_stage=args.recover_host,
                        provider=first_launch_provider,ad_id=args.ad_id)
    if name=='first-launch-media':
        from .first_launch_media import run
        return run(root,args.plan_dir,args.file,args.kind,args.packet,args.mode)
    if name=='first-launch-update':
        from .first_launch_update import run
        from .operator_review import load_input
        change = load_input(args.change_file) if args.change_file is not None else None
        return run(root,args.plan_dir,args.action,args.budget_minor,args.packet,args.mode,ad_id=args.ad_id,change=change,
                   existing_snapshot=args.existing_ad_snapshot,existing_page=args.existing_page,
                   affected_snapshot=args.affected_ads_snapshot)
    if name=='first-launch-host':
        from .first_launch_local_host import LocalFirstLaunchHost, scope_from_launch
        from .first_launch_progress import progress
        launch = progress(root,args.plan_dir)
        identity = scope_from_launch(launch)
        if args.status:
            host = LocalFirstLaunchHost(identity)
            return dict(status='LOCAL_AUTHORITY_SCOPE_MATCHED', identity=host.identity,
                        meta_requests=0, credentials_accessed=False, write_authorized=False)
        return dict(status='LOCAL_AUTHORITY_SETUP_SCOPE', identity=identity,
                    meta_requests=0, credentials_accessed=False, write_authorized=False)
    if name=='first-launch-handoff':
        from .first_launch_handoff import handoff
        return handoff(root,args.plan_dir,image_path=args.image_observation,review_path=args.review,
                       before_path=args.before_parents,after_path=args.after_parents,
                       ad_path=args.ad_observation,output=args.output,reserve_manual=getattr(args,'reserve_manual',False))
    if name=='first-launch-preview':
        from .first_launch_preview import preview
        return preview(root,args.plan_dir,creative_fallback=args.creative_fallback,fetch=args.fetch,
                       output=args.output,provider=first_launch_preview_provider,ad_id=args.ad_id)
    if name=='first-launch-activation-review':
        from .first_launch_activation import prepare
        parent_budget = None
        if args.parent_budget is not None:
            owner, schedule, amount = args.parent_budget
            if not re.fullmatch(r'[1-9][0-9]{0,8}', amount):
                fail('META_WRITE_MINOR_UNITS')
            parent_budget = dict(owner=owner, schedule=schedule, budget_minor=int(amount))
        return prepare(root,args.plan_dir,args.preview_observation,args.start_at,args.end_at,
                       args.maximum_spend_minor,args.output,ad_id=args.ad_id,parent_budget=parent_budget)
    if name=='first-launch-activation':
        from .first_launch_progress import progress
        if args.review is None and args.mode != 'recover':
            fail('FIRST_LAUNCH_ACTIVATION_REVIEW_REQUIRED')
        if args.mode in ('host-approval', 'dispatch'):
            from .first_launch_handoff import activation_paths
            if activation_paths(root,args.plan_dir)[-1] is not None:
                fail('FIRST_LAUNCH_ACTIVATION_MANUAL_ATTEMPT_REQUIRES_RECOVERY')
        if args.mode == 'host-approval':
            from .first_launch_activation import read_review
            from .first_launch_activation_host import _guard_valid
            from .first_launch_local_host import LocalFirstLaunchHost, scope_from_launch
            launch = progress(root,args.plan_dir,ad_id=args.ad_id)
            if launch.get('manual_activation_attempt') is not None:
                fail('FIRST_LAUNCH_ACTIVATION_MANUAL_ATTEMPT_REQUIRES_RECOVERY')
            if launch.get('activation_attempt') is not None:
                fail('FIRST_LAUNCH_ACTIVATION_RECOVER_ORIGINAL_ONLY')
            review = read_review(launch,args.review)
            if review['review_blockers'] or review['preview_kind'] != 'AD':
                fail('FIRST_LAUNCH_ACTIVATION_REVIEW_BLOCKED')
            host = LocalFirstLaunchHost(scope_from_launch(launch))
            guard = host.current_guard(review)
            _guard_valid(review, guard, utcnow())
            latest = progress(root,args.plan_dir,ad_id=args.ad_id)
            if latest.get('manual_activation_attempt') is not None or latest.get('activation_attempt') is not None:
                fail('FIRST_LAUNCH_ACTIVATION_RECOVER_ORIGINAL_ONLY')
            if read_review(latest,args.review) != review:
                fail('FIRST_LAUNCH_ACTIVATION_REVIEW_CHANGED')
            from dataclasses import asdict
            return dict(status='LOCAL_ACTIVATION_AWAITING_INDEPENDENT_APPROVAL',
                        review=review, guard=asdict(guard), dispatchable=False, spend_authorized=False)
        return progress(root, args.plan_dir, activation_review=args.review, activation_mode=args.mode,
                        activation_provider=first_launch_activation_provider,ad_id=args.ad_id)
    if name=='first-launch-delivery':
        from .first_launch_delivery import delivery_review
        return delivery_review(root,args.plan_dir,args.run_receipt,args.since,args.until,
                               activation_review_path=args.activation_review, manual_before=args.manual_before,
                               manual_after=args.manual_after, manual_ad=args.manual_ad,
                               evidence_path=args.evidence,ad_id=args.ad_id)
    if name=='first-launch-evidence':
        from .first_launch_evidence import run
        return run(root,args.plan_dir,args.since,args.until,args.execute_read,ad_id=args.ad_id)
    if name=='add':
        return map_asset(root,args.kind,args.id,args.evidence_ref)
    if name=='default':
        return default_asset(root,args.kind,args.id)
    if name=='unmap':
        return unmap(root,args.kind,args.id)
    if name in ('plan','replan'):
        try:
            if name=='replan':
                return replan_monitor(root,args.monitor_dir,args.output,record_directory=args.record_dir,
                                      from_comment_checkpoint=args.from_comment_checkpoint)
            return prepare_task(root,args.task,args.id,args.output,since=args.since,until=args.until,
                                last_complete_days=args.last_complete_days,
                                record_directory=args.record_dir,website_conversions=args.website_conversions,
                                hierarchy=args.hierarchy,include_live_links=args.include_live_links,
                                report_pages=args.report_pages,
                                cycles=args.cycles,interval_seconds=args.interval_seconds,
                                duration_seconds=args.duration_seconds,pages_per_cycle=args.pages_per_cycle,
                                review_directory=args.review_dir,source_run_receipts=args.source_run_receipts)
        except PlanPrerequisiteError as exc:
            return dict(status='REJECTED', error_code=str(exc), workspace_route=exc.route,
                        dispatchable=False, network_used=False)
    if name=='route':
        route = health_check(root,args.task,args.id,record_directory=args.record_dir,monitor_directory=args.monitor_dir,
                             since=args.since,until=args.until,last_complete_days=args.last_complete_days,
                             website_conversions=args.website_conversions,hierarchy=args.hierarchy)
        return dict(route,next_workflow=workspace_followup(route))
    if name=='bind':
        return bind(root,args.intent,args.record_dir or root/'credential-record')
    if name=='page-bind':
        return bind_page(root,args.id,args.record_dir or root/'credential-record',args.role,args.role_evidence_ref,
                         app_developer_confirmed=args.app_developer_confirmed,proof_not_required=args.proof_not_required_confirmed)
    if name=='page-preview':
        return preview_page(root,args.id,args.record_dir)
    if name=='live-preview':
        return preview_live(root,args.id,args.live_id,args.video_id,args.page_post_id,
                            record_directory=args.record_dir,output=args.output,
                            live_url=args.live_url,live_state_directory=args.live_state_dir,previous_page_run=args.previous_page_run)
    if name=='live-plan':
        return prepare_live_monitor(root,args.id,args.live_id,args.video_id,args.page_post_id,args.output,
                                    record_directory=args.record_dir,verify_insights_role=args.verify_insights_role,
                                    live_url=args.live_url,live_state_directory=args.live_state_dir,
                                    previous_page_run=args.previous_page_run,cycles=args.cycles,
                                    interval_seconds=args.interval_seconds,duration_seconds=args.duration_seconds)
    if name=='page-refresh':
        return refresh_page(root,args.id,args.output,record_directory=args.record_dir)
    if name=='refresh':
        return refresh_account(root,args.id,args.output,record_directory=args.record_dir)
    if name=='backup':
        return backup(root,args.output)
    if name=='restore':
        return restore(args.backup_file,root,preview=args.preview,expected_plan_sha256=args.restore_plan_sha256)
    fail('WORKSPACE_UNKNOWN_COMMAND')
