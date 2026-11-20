"""Bounded Page identity and optional named Insights task preflight."""
import re
from urllib.parse import urlencode

from .connection import (_assert_window, _credential_format, _debug, _get_connection_json,
                         debug_expiries_and_scopes, page_grant_binding, valid_debug_data, validate_intent,
                         verified_metadata)
from .read_adapter import GRAPH_AUTH_ERRORS
from .validation import InputError, aware_time, fail


PAGE_PREFLIGHT_GETS = 3
ACTOR_PATH = '/v27.0/me'
ACCOUNTS_PATH = '/v27.0/me/accounts'
ROLE_PAGE_LIMIT = 25
ROLE_PAGES = 2
ROLE_OPTION = 'verify_page_insights_role'
ROLE_VERIFIED = 'NAMED_ANALYZE_TASK_VERIFIED'
ROLE_OBSERVATION_BASIS = 'SAME_APP_TOKEN_PREFLIGHT_AND_BOUNDED_USER_ACCOUNTS'
PREFLIGHT_ERRORS = frozenset({'PAGE_PREFLIGHT_REVIEW_REQUIRED', 'DISTINCT_PAGE_TOKEN_REQUIRED',
                              'DEBUG_PAGE_TOKEN_IDENTITY_MISMATCH', 'PAGE_TOKEN_ACTOR_MISMATCH'})
DEBUG_DEVELOPER = 'DEBUG_DEVELOPER_USER_TOKEN'
DEBUG_PAGE = 'DEBUG_PAGE_ACCESS_TOKEN'
PAGE_ACTOR = 'NAMED_PAGE_TOKEN_ACTOR'
PAGE_COLLECTOR_IDENTITY_MISMATCH = 'PAGE_COLLECTOR_IDENTITY_MISMATCH'
PAGE_IDENTITY_STAGES = {'DEBUG_PAGE_TOKEN_IDENTITY_MISMATCH':DEBUG_PAGE,
                        'PAGE_TOKEN_ACTOR_MISMATCH':PAGE_ACTOR,
                        PAGE_COLLECTOR_IDENTITY_MISMATCH:'PAGE_COLLECTOR'}
PAGE_ROLE_STAGES = tuple(f'NAMED_PAGE_ANALYZE_TASK_PAGE_{page}' for page in range(1,ROLE_PAGES+1))
PAGE_DEVELOPER_REJECTION_ERRORS = (*GRAPH_AUTH_ERRORS, 'DEBUG_TOKEN_INVALID')
PAGE_GRANT_REJECTION_ERRORS = ('DEBUG_PAGE_READ_NOT_GRANTED','DEBUG_PAGE_TARGET_NOT_GRANTED')


def page_permission_issue(error, stage):
    if error not in PAGE_GRANT_REJECTION_ERRORS or stage not in (DEBUG_DEVELOPER, DEBUG_PAGE):
        return None
    return dict(credential='DEVELOPER_USER' if stage==DEBUG_DEVELOPER else 'PAGE_TOKEN',stage=stage)


def page_authentication_issue(error, stage, preflight_complete, page_slot_attempted):
    """Identify the credential only when the failed step can distinguish it."""
    if error == 'NATIVE_CREDENTIAL_UNAVAILABLE':
        return dict(credential='PAGE_TOKEN' if page_slot_attempted else 'DEVELOPER_USER',
                    stage='LOCAL_CREDENTIAL_READ',next_action='CHECK_LOCAL_CREDENTIAL_SLOT')
    if preflight_complete:
        stage = 'PAGE_COLLECTOR'
    if error in PAGE_IDENTITY_STAGES and stage==PAGE_IDENTITY_STAGES[error]:
        return dict(credential='PAGE_TOKEN',stage=stage,next_action='RECHECK_NAMED_PAGE_TOKEN')
    if error not in (*PAGE_DEVELOPER_REJECTION_ERRORS,'CONNECTION_EXPIRED_OR_CLOCK_SKEW'):
        return None
    if stage == DEBUG_DEVELOPER or stage in PAGE_ROLE_STAGES:
        credential, action = 'DEVELOPER_USER', 'RECHECK_DEVELOPER_USER_TOKEN'
    elif stage in (PAGE_ACTOR,'PAGE_COLLECTOR') or stage == DEBUG_PAGE and error not in GRAPH_AUTH_ERRORS:
        credential, action = 'PAGE_TOKEN', 'RECHECK_NAMED_PAGE_TOKEN'
    else:
        credential, action = 'UNDETERMINED', 'VERIFY_BOTH_CREDENTIALS_SEPARATELY'
    if error == 'CONNECTION_EXPIRED_OR_CLOCK_SKEW':
        action = 'CHECK_LOCAL_CLOCK_AND_CREDENTIAL_EXPIRY'
    return dict(credential=credential,stage=stage,next_action=action)


def startup_get_limit(intent):
    return PAGE_PREFLIGHT_GETS + (ROLE_PAGES if intent.get(ROLE_OPTION) else 0)


def page_token_preflight_plan(include_metrics, *, verify_role=False):
    result = {
        'state': 'PENDING_REAL_PAGE_TOKEN_FIXTURE',
        'minimum_scopes_by_credential': {'developer_user':['pages_read_engagement'],
                                         'page_token':['pages_read_engagement']},
        'requests': [
            dict(method='GET',path='/v27.0/debug_token',authentication='APP_DEVELOPER_USER_TOKEN',
                 inspected_credential='APP_DEVELOPER_USER_TOKEN'),
            dict(method='GET',path='/v27.0/debug_token',authentication='APP_DEVELOPER_USER_TOKEN',
                 inspected_credential='PAGE_ACCESS_TOKEN'),
            dict(method='GET',path=ACTOR_PATH,params={'fields':'id'},authentication='PAGE_ACCESS_TOKEN'),
        ],
        'insights_requirements': ['PAGE_ACCESS_TOKEN','pages_read_engagement','read_insights','ANALYZE_TASK']
        if include_metrics else [],
        'authorization_or_endpoint_coverage_verified': False,
    }
    if verify_role:
        result['optional_insights_role_lookup'] = dict(
            method='GET',path=ACCOUNTS_PATH,params={'fields':'id,tasks','limit':ROLE_PAGE_LIMIT},
            authentication='APP_DEVELOPER_USER_TOKEN',developer_scope='pages_show_list',maximum_gets=ROLE_PAGES,
            may_receive_other_page_rows_in_memory=True,retains_only_named_page_task_result=True,
        )
    return result


def _page_role_result(page_id, developer_data, credential, before, checkpoint, clock, on_rate_limit, remaining):
    if 'pages_show_list' not in developer_data['scopes']:
        return 'PAGE_ROLE_LIST_SCOPE_REQUIRED', 0
    try:
        page_grant_binding(developer_data,page_id,'pages_show_list')
    except InputError:
        return 'PAGE_ROLE_LIST_SCOPE_REQUIRED', 0

    params = {'fields':'id,tasks','limit':ROLE_PAGE_LIMIT}
    cursors = set()
    for page, stage in enumerate(PAGE_ROLE_STAGES,1):
        before(stage)
        try:
            body = _get_connection_json(ACCOUNTS_PATH+'?'+urlencode(params),credential,checkpoint,
                                        clock=clock,on_rate_limit=on_rate_limit,time_remaining=remaining)
        except InputError as exc:
            if str(exc) == 'PERMISSION': return 'PAGE_ROLE_LOOKUP_PERMISSION_REJECTED', page
            if str(exc) in ('OBJECT_OR_PARAMETER','SERVICE_UNAVAILABLE','UNCLASSIFIED_ERROR',
                            'HTTP_RESPONSE_INVALID_JSON','HTTP_RESPONSE_NOT_OBJECT'):
                return 'PAGE_ROLE_LOOKUP_UNAVAILABLE', page
            raise
        checkpoint()
        rows = body.get('data')
        if (type(rows) is not list or len(rows)>ROLE_PAGE_LIMIT or any(
                type(row) is not dict or type(row.get('id')) is not str or not re.fullmatch('[1-9][0-9]{0,39}',row['id'])
                for row in rows)):
            return 'PAGE_ROLE_LOOKUP_INVALID', page
        named = [row for row in rows if row['id']==page_id]
        if len(named)>1: return 'PAGE_ROLE_LOOKUP_INVALID', page
        if named:
            tasks = named[0].get('tasks')
            if (type(tasks) is not list or len(tasks)>64 or any(
                    type(task) is not str or not re.fullmatch('[A-Z][A-Z0-9_]{0,63}',task) for task in tasks)):
                return 'PAGE_ROLE_LOOKUP_INVALID', page
            if 'ANALYZE' in tasks: return ROLE_VERIFIED, page
            if 'PROFILE_PLUS_ANALYZE' in tasks: return 'PAGE_ANALYZE_ALIAS_REVIEW_REQUIRED', page
            return 'NAMED_ANALYZE_TASK_NOT_LISTED', page
        paging = body.get('paging',{})
        if type(paging) is not dict: return 'PAGE_ROLE_LOOKUP_INVALID', page
        if paging.get('next') is None: return 'NAMED_PAGE_TASK_NOT_OBSERVED', page
        cursor_info = paging.get('cursors')
        cursor = cursor_info.get('after') if type(cursor_info) is dict else None
        if type(cursor) is not str or not re.fullmatch('[A-Za-z0-9_=-]{1,2048}',cursor) or cursor in cursors:
            return 'PAGE_ROLE_LOOKUP_INVALID', page
        cursors.add(cursor)
        params['after'] = cursor
    return 'PAGE_ROLE_LOOKUP_INCOMPLETE', ROLE_PAGES


def verified_page_metadata(body, intent, now, deadline, *, include_metrics):
    data = valid_debug_data(body)
    if (data.get('profile_id')!=intent['page_id'] or
            data.get('type','PAGE')!='PAGE'):
        fail('DEBUG_PAGE_TOKEN_IDENTITY_MISMATCH')
    if data.get('app_id')!=intent['app_id'] or data.get('user_id')!=intent['principal_id']:
        fail('DEBUG_APP_OR_PRINCIPAL_MISMATCH')
    times, scopes = debug_expiries_and_scopes(data,now)
    if 'pages_read_engagement' not in scopes:
        fail('DEBUG_PAGE_READ_NOT_GRANTED')
    binding = page_grant_binding(data,intent['page_id'])
    insights = 'NOT_REQUESTED'
    insight_grant = None
    if include_metrics:
        if 'read_insights' not in scopes:
            insights = 'READ_INSIGHTS_SCOPE_REQUIRED'
        else:
            try:
                insight_grant = page_grant_binding(data,intent['page_id'],'read_insights')
            except InputError as exc:
                insights = str(exc)
            else:
                insights = 'NAMED_ANALYZE_TASK_EVIDENCE_REQUIRED'
    return dict(app_id=intent['app_id'],page_id=intent['page_id'],principal_id=intent['principal_id'],
                page_grant_binding=binding,read_insights_grant_binding=insight_grant,insights_state=insights,
                token_expires_at=times[0].isoformat(),data_access_expires_at=times[1].isoformat(),
                effective_read_expires_at=min(*times,deadline).isoformat(),
                observed_at=now.isoformat())


def verify_page_credentials(intent, developer_credential, page_credential, *, include_metrics, clock, deadline, guard,
                            reserve=None, on_rate_limit=None, verify_role=False, time_remaining=None):
    """Verify the Page Live startup pair in memory after authorization checks."""
    validate_intent(intent,page=True)
    if (not intent['app_developer_confirmed'] or not intent['asset_owner_confirmed']
            or intent['appsecret_proof_requirement']!='NOT_REQUIRED_CONFIRMED' or not callable(guard)
            or type(include_metrics) is not bool or type(verify_role) is not bool or (verify_role and not include_metrics)
            or (reserve is not None and not callable(reserve))):
        fail('PAGE_PREFLIGHT_REVIEW_REQUIRED')
    _credential_format(developer_credential)
    _credential_format(page_credential)
    if developer_credential == page_credential:
        fail('DISTINCT_PAGE_TOKEN_REQUIRED')
    started = clock()
    until = deadline

    def checkpoint():
        guard()
        return _assert_window(started,until,clock)

    def remaining():
        seconds = (until-clock()).total_seconds()
        return seconds if time_remaining is None else min(seconds,time_remaining())

    def before(stage):
        checkpoint()
        if reserve is not None:
            reserve(stage)

    before(DEBUG_DEVELOPER)
    developer_debug = _debug(developer_credential,checkpoint,clock=clock,on_rate_limit=on_rate_limit,time_remaining=remaining)
    developer = verified_metadata(developer_debug,intent,checkpoint(),until,page=True)
    until = aware_time(developer['effective_read_expires_at'])
    before(DEBUG_PAGE)
    page_debug = _debug(developer_credential,checkpoint,candidate=page_credential,clock=clock,on_rate_limit=on_rate_limit,
                        time_remaining=remaining)
    page = verified_page_metadata(page_debug,intent,checkpoint(),until,include_metrics=include_metrics)
    until = aware_time(page['effective_read_expires_at'])
    before(PAGE_ACTOR)
    actor = _get_connection_json(ACTOR_PATH+'?fields=id',page_credential,checkpoint,clock=clock,on_rate_limit=on_rate_limit,
                                 time_remaining=remaining)
    observed_at = checkpoint()
    if actor.get('id')!=intent['page_id']:
        fail('PAGE_TOKEN_ACTOR_MISMATCH')
    result = dict(status='NAMED_PAGE_TOKEN_PREFLIGHT_PASSED',page=page,actor_observed_at=observed_at.isoformat(),
                  actor_binding='DEBUG_PROFILE_ID_AND_PAGE_TOKEN_ME_MATCH',get_attempts=PAGE_PREFLIGHT_GETS,
                  live_endpoint_verified=False,comment_grant_verified=False,insights_role_verified=False,
                  credentials_persisted=False,authorization_reusable=False)
    if verify_role:
        role = page['insights_state']
        attempts = 0
        if role=='NAMED_ANALYZE_TASK_EVIDENCE_REQUIRED':
            role, attempts = _page_role_result(intent['page_id'],valid_debug_data(developer_debug),developer_credential,
                                               before,checkpoint,clock,on_rate_limit,remaining)
            page['insights_state'] = role
        result['get_attempts'] += attempts
        result['insights_role_verified'] = role == ROLE_VERIFIED
        result['insights_role_observation'] = dict(state=role,page_id=intent['page_id'],app_id=intent['app_id'],
                                                   principal_id=intent['principal_id'],get_attempts=attempts,
                                                   basis=ROLE_OBSERVATION_BASIS,
                                                   observed_at=checkpoint().isoformat())
    return result
