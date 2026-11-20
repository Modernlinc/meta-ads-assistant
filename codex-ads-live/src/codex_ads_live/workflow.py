"""Evidence gates for the offline supervisor; states never confer permission."""
from pathlib import Path
from .validation import fail, object_only, strict_json

GATES={
    "onboarding":("target_declared","owner_declared","scope_recorded"),
    "asset_inventory":("scope_recorded","canonical_id_verified","ownership_verified"),
    "app_setup":("app_binding_verified","use_cases_reviewed","review_requirements_recorded"),
    "connection":("scope_recorded","app_binding_verified","authentication_current","grants_verified","ownership_verified"),
    "ads_read":("connection_verified","canonical_id_verified","report_context_recorded"),
    "live_identify":("connection_verified","page_owner_verified","existing_live_available","live_contract_verified"),
    "comments_read":("live_identity_verified","comment_contract_verified"),
    "live_metrics":("live_identity_verified","metric_period_verified","source_time_policy_recorded"),
    "proposal":("business_material_admitted","method_source_verified","evidence_labeled"),
    "approval_simulation":("synthetic_fixture","proposal_diff_reviewed","revision_current"),
    "monitor":("runner_installed","collection_scope_recorded","stop_method_verified"),
    "review":("observations_available","comparison_context_verified"),
    "exit":("local_runner_stopped","retention_choice_recorded"),
}
OPERATOR_WORKFLOWS = ('onboarding','asset_inventory','app_setup','authorization','connection','ads_read',
                      'live_identify','comments_read','live_metrics','proposal','approval_simulation','monitor',
                      'review','troubleshooting','exit')
GUIDE_FIELDS = ('title','inputs','dependencies','steps','human_gate','failure','retry','stop','resume','audit','references')
WORKSPACE_REASON_GUIDES = {
    'COMPLETE_APP_AND_BUSINESS_MAPPING': 'onboarding',
    'SELECT_NAMED_ASSET': 'asset_inventory',
    'CONNECT_EXISTING_APP': 'connection',
    'VERIFY_PAGE_ROLES_AND_ACCESS': 'connection',
    'VERIFY_NEW_ACCOUNT_ONLY': 'connection',
    'CHECK_THIS_ASSET': 'connection',
    'CHECK_THIS_PAGE': 'connection',
    'REFRESH_THIS_ASSET': 'connection',
    'REPAIR_THIS_SCOPE': 'troubleshooting',
    'MONITOR_SCOPE_CHANGED': 'troubleshooting',
    'MONITOR_SCOPE_UNVERIFIED': 'troubleshooting',
    'CHECK_INSIGHTS_ANALYSIS_CONTEXT': 'troubleshooting',
    'ACCOUNT_DAILY_ROWS_INCOMPLETE': 'review',
}
WORKSPACE_STATUS_GUIDES = {
    'AUTH_REQUIRED': 'authorization',
    'PERMISSION_REQUIRED': 'authorization',
    'ASSET_CHANGED': 'asset_inventory',
    'VERSION_MISMATCH': 'troubleshooting',
}


def workspace_followup(route):
    """Choose the customer guide for the current task prerequisite."""
    if route['reason'] == 'CONNECT_EXISTING_APP':
        return WORKSPACE_REASON_GUIDES[route['reason']]
    return (WORKSPACE_STATUS_GUIDES.get(route['status']) or WORKSPACE_REASON_GUIDES.get(route['reason'])
            or route['workflow'])


def operator_guide(workflow=None):
    path = Path(__file__).resolve().parents[2]/'references'/'OPERATOR_WORKFLOWS.json'
    with path.open(encoding='utf-8') as stream:
        contents = strict_json(stream.read(100001),max_chars=100000)
    object_only(contents,('schema','workflows'),('schema','workflows'))
    declared = contents['workflows']
    if contents['schema']!='operator-workflows-v1': fail('WORKFLOW_GUIDE_SCHEMA')
    object_only(declared,OPERATOR_WORKFLOWS,OPERATOR_WORKFLOWS)
    base = dict(schema=contents['schema'],mode='GUIDANCE_ONLY',network_used=False,read_dispatchable=False,
                live_action_authorized=False,real_connection_verified=False,
                state_model=dict(advance='REVIEW_CURRENT_EVIDENCE',missing_evidence='NEEDS_EVIDENCE',
                                 external_dependency='WAITING_EXTERNAL',stopped='STOPPED',resume='EXPLICIT_RECHECK'))
    if workflow is None:
        return dict(base,workflows=[dict(workflow=name,title=declared[name]['title']) for name in OPERATOR_WORKFLOWS])
    if workflow not in OPERATOR_WORKFLOWS: fail('UNKNOWN_WORKFLOW')
    value = declared[workflow]
    object_only(value,GUIDE_FIELDS,GUIDE_FIELDS)
    return dict(base,workflow=workflow,**value)


def gate(workflow,evidence,mode="MOCK"):
    if type(workflow) is not str or workflow not in GATES:fail("UNKNOWN_WORKFLOW")
    if mode not in ("MOCK","REAL_READ_PLANNING"):fail("REAL_EXECUTION_UNAVAILABLE")
    object_only(evidence,GATES[workflow])
    for value in evidence.values():
        if value is not None and type(value) is not bool:fail("EVIDENCE_STATUS_REQUIRES_BOOLEAN_OR_NULL")
    missing=[key for key in GATES[workflow] if evidence.get(key) is not True]
    return {"workflow":workflow,"mode":mode,"stage":"NEEDS_EVIDENCE" if missing else "READY_FOR_REVIEW",
            "missing_evidence":missing,"next_action":"补齐对应证据；其他独立本地步骤可继续" if missing else "核输入来源与当轮授权后选择具名步骤",
            "live_action_authorized":False,"real_connection_verified":False,
            "retry_policy":"No automatic retries; auth/permission/identity failure stops the affected step"}


HANDOFF_LISTS=("evidence_observed","evidence_labels","unknowns","check_result_fields","stop_conditions")
HANDOFF_TEXT=("runtime_status","decision","earliest_credible_breakpoint","one_minimum_validation_action","maximum_loss","rollback","safe_stop")
HANDOFF_FIELDS=(*HANDOFF_LISTS,*HANDOFF_TEXT,"causal_claim_allowed","live_action_authorized")


def validate_business_handoff(handoff):
    object_only(handoff,HANDOFF_FIELDS,HANDOFF_FIELDS)
    for key in HANDOFF_TEXT:
        if type(handoff[key]) is not str or not handoff[key].strip() or len(handoff[key])>4000:fail("HANDOFF_TEXT_REQUIRED")
    for key in HANDOFF_LISTS:
        if type(handoff[key]) is not list or len(handoff[key])>100:fail("HANDOFF_LIST_REQUIRED")
        if any(type(value) is not str or not value.strip() or len(value)>4000 for value in handoff[key]):fail("HANDOFF_LIST_ITEM_REQUIRED")
    if handoff["causal_claim_allowed"] is not False or handoff["live_action_authorized"] is not False:
        fail("LEGACY_METHOD_CANNOT_AUTHORIZE_LIVE_ACTION")
    if not handoff["check_result_fields"] or not handoff["stop_conditions"]:fail("ACTIONABLE_HANDOFF_REQUIRED")
    return {"status":"STRUCTURE_VALID_NOT_BUSINESS_ACCEPTANCE","field_count":14,"live_action_authorized":False}
