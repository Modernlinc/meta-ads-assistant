"""Named, time-limited Meta system-user reporting with a separate app inspector."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from urllib.parse import urlencode

from . import client_workspace as workspace
from .connection import _debug, _get_connection_json, _MemoryBroker, valid_debug_data
from .credential_record import RecordGuard, read_record
from .http_read import GraphReadTransport, ReadBinding
from .journal import digest
from .native_credentials import NativeCredentialBroker
from .validation import fail


def references(directory, system=None):
    root = Path(directory)
    primary = system or root/'system-credential-record'
    secondary = root/'app-inspector-record'
    return primary, secondary


def credential_records(directory, primary, profile):
    locations = references(directory, primary)
    system, inspector = (read_record(location,existing_only=True) for location in locations)
    if (system['app_id'] != profile['app_id'] or inspector['app_id'] != profile['app_id']
            or system['principal_id'] == inspector['principal_id'] or system['reference'] == inspector['reference']):
        fail('META_SYSTEM_SEPARATE_APP_INSPECTOR_REQUIRED')
    checks = [
        RecordGuard(location,dict(app_id=row['app_id'],principal_id=row['principal_id'],
                                  credential_reference=row['reference']),digest(row))
        for row,location in zip((system,inspector),locations,strict=True)
    ]
    for check in checks:
        check.check(existing_only=True)
    return system, inspector, checks


def _debug_system_user(response, app_id, principal, now):
    data = valid_debug_data(response)
    if (data.get('app_id'),data.get('user_id'),data.get('profile_id')) != (app_id,principal,None):
        fail('META_SYSTEM_APP_OR_USER_SCOPE')
    timestamp = data.get('expires_at')
    if type(timestamp) is not int or not now.timestamp()<timestamp<=(now+timedelta(days=65)).timestamp():
        fail('META_SYSTEM_FINITE_EXPIRY_REQUIRED')
    if data.get('scopes') != ['ads_read']:
        fail('META_SYSTEM_ONLY_ADS_READ')
    if 'data_access_expires_at' in data and data['data_access_expires_at'] not in (None,0):
        access = data['data_access_expires_at']
        if type(access) is not int or not now.timestamp()<access<253402300799:
            fail('META_SYSTEM_FINITE_EXPIRY_REQUIRED')
        timestamp = min(timestamp,access)
    return datetime.fromtimestamp(timestamp,timezone.utc)


def _operational_scope(profile):
    return dict(workspace=workspace.workspace_identity(profile),route=(profile.get('connection_onboarding') or {}).get('route'),
                defaults=profile['defaults'],assets=profile['assets'])


@contextmanager
def scoped_session(directory, profile, account, plan_hash, phrase, record_directory, clock, *, discovery=False):
    if (profile.get('connection_onboarding', {}).get('route')!='system-user'
            or not profile['app_id'] or not profile['business_id']):
        fail('META_SYSTEM_ENTERPRISE_CONTEXT_REQUIRED')
    system, inspector, guards = credential_records(directory,record_directory,profile)
    primary_sha = digest(system)
    saved = profile['connection_onboarding'].get('discovery')
    if not discovery and (not saved or saved['record_sha256']!=primary_sha
            or not any(row['account_id']==account for row in saved['accounts'])):
        fail('META_SYSTEM_ACCOUNT_DISCOVERY_REQUIRED')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        fail('META_SYSTEM_TERMINAL_REQUIRED')
    phrase += f" SYSTEM USER {system['principal_id']} APP {profile['app_id']}"
    print('本次核对具名企业系统用户、同应用开发者检查权限和指定广告数据范围。')
    if input(f'确认请键入 {phrase}：')!=phrase:
        fail('META_SYSTEM_CHECK_NOT_CONFIRMED')
    started = clock()
    deadline = started+timedelta(minutes=5)
    scope = _operational_scope(profile)

    def checkpoint():
        if not started <= clock() < deadline:
            fail('CONNECTION_REVIEW_EXPIRED')
        for guard in guards:
            guard.check(existing_only=True)
        if _operational_scope(workspace.read(directory,existing_only=True))!=scope:
            fail('WORKSPACE_CHANGED_DURING_CHECK')

    def remaining():
        return (deadline-clock()).total_seconds()

    def lease(row):
        provisional = ReadBinding(plan_hash,row['app_id'],row['principal_id'],started.isoformat(),deadline.isoformat(),
                                  deadline.isoformat(),(),False,False,False)
        return NativeCredentialBroker(row['reference'],provisional,enabled=True).lease(provisional)

    with lease(system) as token:
        try:
            with lease(inspector) as inspector_token:
                issue = _debug(inspector_token,checkpoint,candidate=token,clock=clock,time_remaining=remaining)
                expires = _debug_system_user(issue,system['app_id'],system['principal_id'],clock())
                # Confirm the app-scoped principal within the exact declared business.
                path = '/v27.0/' + profile['business_id'] + '/system_users?' + urlencode(dict(fields='id,role',limit=50))
                portfolio = _get_connection_json(path,inspector_token,checkpoint,clock=clock,time_remaining=remaining)
                employees = portfolio.get('data')
                if (type(employees) is not list or len(employees)>50 or not any(
                        type(row) is dict and (row.get('id'),row.get('role'))==(system['principal_id'],'EMPLOYEE')
                        for row in employees)):
                    fail('META_SYSTEM_NAMED_EMPLOYEE_NOT_OBSERVED')
            inspector_token = None
            deadline = min(deadline,expires)

            def get(path):
                return _get_connection_json(path,token,checkpoint,clock=clock,time_remaining=remaining)

            actor = get('/v27.0/me?fields=id')
            if actor.get('id')!=system['principal_id']:
                fail('META_SYSTEM_ACTING_USER_MISMATCH')
            if discovery:
                yield get, primary_sha
                checkpoint()
                return
            row = get(f'/v27.0/act_{account}?fields=id,account_id,currency,timezone_name,account_status')
            if ((row.get('id'),row.get('account_id'),row.get('account_status'))!=('act_'+account,account,1)
                    or type(row.get('account_status')) is not int):
                fail('META_SYSTEM_ACCOUNT_SCOPE')
            currency, zone = row.get('currency'),row.get('timezone_name')
            workspace.pattern(currency,r'[A-Z]{3}','META_SYSTEM_ACCOUNT_SCOPE')
            workspace.pattern(zone,r'[A-Za-z0-9_+/.-]{1,100}','META_SYSTEM_ACCOUNT_SCOPE')

            class Guard:
                def check(self):
                    checkpoint()

            brokers = []

            def transport(plan):
                assertion = ReadBinding(digest(asdict(plan)),system['app_id'],system['principal_id'],started.isoformat(),
                                        expires.isoformat(),deadline.isoformat(),('ads_read',),True,True,True)
                broker = _MemoryBroker(token,assertion,Guard())
                brokers.append(broker)
                return GraphReadTransport(plan,assertion,broker,clock=clock,time_remaining=remaining)

            try:
                yield get,transport,digest([scope,primary_sha,digest(inspector)]),currency,zone
                checkpoint()
            finally:
                for broker in brokers:
                    broker._credential = None
        finally:
            token = None
            inspector_token = None
