"""Prepare and review a local first-launch plan for one selected account and Page."""
from pathlib import Path
from uuid import uuid4

from .client_workspace import asset_key, asset_reconfirmation_pending, draft_review_scope, journal_for, load
from .journal import digest
from .local_paths import checked_path
from .meta_write_mapping import (SUPPORTED_CURRENCIES, WEBSITE_CONVERSION_OBJECTIVES, _write_new, blank_draft,
                                 load_draft, review_draft, utcnow)
from .monitor_tools import new_directory
from .validation import fail, object_only, strict_json

SCHEMA = 'workspace-first-launch-plan-v1'
PREPARED = 'WORKSPACE_FIRST_LAUNCH_PLAN_PREPARED'
OPERATIONS = (
    ('CREATE_CAMPAIGN', 'campaign-draft.json', {}),
    ('CREATE_ADSET', 'adset-draft.json', {'campaign_id': 'CREATE_CAMPAIGN'}),
    ('CREATE_CREATIVE', 'creative-draft.json', {}),
    ('CREATE_AD', 'ad-draft.json', {'adset_id': 'CREATE_ADSET', 'creative': 'CREATE_CREATIVE'}),
)
PAGE_OPERATIONS = ('CREATE_CREATIVE', 'CREATE_AD')


def plan_directory(workspace, directory):
    return directory if directory is not None else Path(workspace) / 'first-launch-plan'


def _plan(profile, selection):
    object_only(selection, ('account_id', 'page_id'), ('account_id', 'page_id'))
    account_scope = draft_review_scope(profile, selection['account_id'])
    account = account_scope['account_id']
    page = selection['page_id'] if selection['page_id'] is not None else profile['defaults']['PAGE']
    if page is None:
        fail('FIRST_LAUNCH_SELECT_NAMED_PAGE')
    page_key = asset_key('PAGE', page)
    if page_key not in profile['assets']:
        fail('FIRST_LAUNCH_PAGE_NOT_MAPPED')
    if any(asset_reconfirmation_pending(profile, key) for key in (asset_key('ACCOUNT', account), page_key)):
        fail('WORKSPACE_ASSET_RECONFIRMATION_REQUIRED')
    business = account_scope['business_profile'] or {}
    context = account_scope['account_context']
    currency = context.get('currency')
    notes = []
    if currency is not None and currency not in SUPPORTED_CURRENCIES:
        notes.append('OBSERVED_CURRENCY_OUTSIDE_WRITE_REVIEW_PROFILE')
    result_type = business.get('result_type')
    if result_type in WEBSITE_CONVERSION_OBJECTIVES:
        notes.append('WEBSITE_PIXEL_AND_EVENT_REVIEW_REQUIRED')
    if result_type == 'QUALIFIED_LEAD':
        notes.append('QUALIFIED_LEAD_PROFILE_REVIEW_REQUIRED')
    return dict(schema=SCHEMA, selection=selection, account_id=account, page_id=page,
                app_id=profile['app_id'], business_id=profile['business_id'],
                workspace_scope_sha256=digest(dict(account=account_scope, page=profile['assets'][page_key])),
                previously_observed_account_context=context,
                declared_goal=business.get('goal'), declared_result_type=result_type,
                business_budget={key:business.get(key) for key in ('currency','maximum_test_spend','protected_daily_spend')},
                supported_adset_profiles=['WEBSITE_LINK_CLICKS', 'WEBSITE_PURCHASE', 'WEBSITE_LEAD'],
                notes=notes, dispatchable=False)


def _draft(plan, operation):
    draft = blank_draft(operation)
    draft['account_id'] = plan['account_id']
    context = plan['previously_observed_account_context']
    if context.get('currency') in SUPPORTED_CURRENCIES:
        draft['context']['currency'] = context['currency']
    draft['context']['timezone'] = context.get('timezone')
    if operation in PAGE_OPERATIONS:
        draft['context']['page_id'] = plan['page_id']
    if 'status' in draft['parameters']:
        draft['parameters']['status'] = 'PAUSED'
    result_type = plan['declared_result_type']
    if result_type in WEBSITE_CONVERSION_OBJECTIVES:
        params = draft['parameters']
        if operation == 'CREATE_CAMPAIGN':
            params['objective'] = WEBSITE_CONVERSION_OBJECTIVES[result_type]
        if operation == 'CREATE_ADSET':
            params.update(optimization_goal='OFFSITE_CONVERSIONS', destination_type='WEBSITE',
                          promoted_object={'pixel_id': None, 'custom_event_type': result_type})
    return draft


def _goal_conflicts(plan, operation, params):
    result_type = plan['declared_result_type']
    if result_type not in WEBSITE_CONVERSION_OBJECTIVES:
        return []
    if operation == 'CREATE_CAMPAIGN' and params['objective'] not in (None, WEBSITE_CONVERSION_OBJECTIVES[result_type]):
        return ['CAMPAIGN_OBJECTIVE_CONFLICTS_WITH_DECLARED_WEBSITE_RESULT']
    if operation == 'CREATE_ADSET':
        promoted = params.get('promoted_object') or {}
        if params['optimization_goal'] not in (None, 'OFFSITE_CONVERSIONS') or promoted.get('custom_event_type') not in (None, result_type):
            return ['ADSET_OPTIMIZATION_CONFLICTS_WITH_DECLARED_WEBSITE_RESULT']
    return []


def prepare(workspace, output=None, *, account_id=None, page_id=None):
    journal = journal_for(workspace, existing_only=True)
    with journal.locked(existing_only=True):
        plan = _plan(load(journal), dict(account_id=account_id, page_id=page_id))
        plan['plan_id'] = uuid4().hex
        root = new_directory(plan_directory(workspace, output))
        files = [_write_new(root, 'first-launch-plan.json', plan)]
        for operation, filename, _ in OPERATIONS:
            files.append(_write_new(root, filename, _draft(plan, operation)))
        result = dict(status=PREPARED, plan_file='first-launch-plan.json',
                      draft_files=[row['path'] for row in files[1:]], notes=plan['notes'],
                      network_used=False, dispatchable=False)
        _write_new(root, 'RECEIPT.json', dict(result, files=files, plan_sha256=digest(plan)))
    return dict(result, plan_directory=str(root), plan_sha256=digest(plan))


def _read_plan_json(path):
    path = checked_path(path, link_error='FIRST_LAUNCH_PLAN_PATH_LINK', volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')
    if not path.is_file() or path.stat().st_size > 16_000:
        fail('FIRST_LAUNCH_PLAN_FILES_REQUIRED')
    with path.open('rb') as source:
        data = source.read(16_001)
    if len(data) > 16_000:
        fail('FIRST_LAUNCH_PLAN_FILES_REQUIRED')
    return strict_json(data.decode('utf-8'))


def current_plan(profile, root):
    plan = _read_plan_json(root/'first-launch-plan.json')
    receipt = _read_plan_json(root/'RECEIPT.json')
    if (type(plan) is not dict or plan.get('schema') != SCHEMA or type(receipt) is not dict
            or receipt.get('status') != PREPARED or receipt.get('plan_sha256') != digest(plan)):
        fail('FIRST_LAUNCH_PLAN_RECEIPT_MISMATCH')
    current = _plan(profile, plan.get('selection'))
    stable_fields = ('account_id', 'page_id', 'workspace_scope_sha256')
    if any(current[key] != plan.get(key) for key in stable_fields):
        fail('FIRST_LAUNCH_WORKSPACE_SCOPE_CHANGED')
    return current, receipt['plan_sha256']


def scoped_draft(root, current, operation, filename):
    draft = load_draft(root/filename)
    expected = _draft(current, operation)
    if (draft['operation'], draft['account_id'], draft['context']['page_id']) != (
            operation, expected['account_id'], expected['context']['page_id']):
        fail('FIRST_LAUNCH_DRAFT_SCOPE_CHANGED')
    currency = current['previously_observed_account_context'].get('currency')
    if currency is not None and draft['context']['currency'] not in (None, currency):
        fail('FIRST_LAUNCH_ACCOUNT_CURRENCY_CHANGED')
    timezone = current['previously_observed_account_context'].get('timezone')
    if timezone is not None and draft['context']['timezone'] not in (None, timezone):
        fail('FIRST_LAUNCH_ACCOUNT_TIMEZONE_CHANGED')
    return draft


def check(workspace, directory=None, *, clock=utcnow):
    root = checked_path(plan_directory(workspace, directory), link_error='FIRST_LAUNCH_PLAN_PATH_LINK',
                        volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')
    journal = journal_for(workspace, existing_only=True)
    stages = {}
    with journal.locked(existing_only=True):
        current, _ = current_plan(load(journal), root)
        check_at = clock()
        for operation, filename, dependencies in OPERATIONS:
            draft = scoped_draft(root, current, operation, filename)
            report = review_draft(draft, clock=lambda: check_at)
            conflicts = _goal_conflicts(current, operation, draft['parameters'])
            stages[operation] = dict(file=filename, status=report['status'], review_sha256=report['review_sha256'],
                missing_fields=report['missing_fields'], blockers=[*report['blockers'], *conflicts], dependencies={
                    field: dict(operation=predecessor, state='NOT_PROVIDED' if draft['parameters'][field] is None
                                else 'DECLARED_ID_REQUIRES_VERIFIED_READBACK')
                    for field, predecessor in dependencies.items()})
    return dict(status='WORKSPACE_FIRST_LAUNCH_DRAFTS_CHECKED', stages=stages, notes=current['notes'],
                confirmed_native_dependencies=False, network_used=False, dispatchable=False)
