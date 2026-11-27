"""Public prerequisite checklist for a named Meta use case; no account verification."""
from .operator_review import structured
from .validation import fail

CHECKED_ON = '2026-11-26'
SOURCES = {
    'marketing_api_feature': 'https://developers.facebook.com/documentation/ads-commerce/marketing-api/get-started/authorization',
    'marketing_api': 'https://developers.facebook.com/docs/marketing-api/reference/v27.0/',
    'business_assets': 'https://developers.facebook.com/docs/business-management-apis/business-manager/get-started/',
    'access_levels': 'https://developers.facebook.com/documentation/development/graph-api/overview/access-levels',
    'app_review': 'https://developers.facebook.com/documentation/resp-plat-initiatives/individual-processes/app-review',
    'business_verification': 'https://developers.facebook.com/documentation/development/release/business-verification',
    'ad_review': 'https://secure.facebook.com/business/learn/video/advertising-guidelines',
    'permissions': 'https://developers.facebook.com/docs/permissions/reference/',
    'ads_archive': 'https://developers.facebook.com/docs/graph-api/reference/ads_archive/',
    'live_video_comments': 'https://developers.facebook.com/docs/graph-api/reference/live-video/comments/',
    'conversions_api': 'https://developers.facebook.com/documentation/ads-commerce/conversions-api/deduplicate-pixel-and-server-events',
}
TASKS = {
    'ADS_REPORT': ('ads_read',),
    'ADS_CHANGE': ('ads_management',),
    'BUSINESS_ASSETS': ('business_management',),
    'PAGE_LIVE': ('pages_read_engagement',),
    'PUBLIC_LIBRARY': (),
    'LOCAL_TRACKING_AUDIT': (),
}


def review(doc):
    structured(doc, 'meta-access-readiness-v1', ('task', 'app_user_role', 'asset_role', 'grants'))
    task, app_role, asset_role, grants = (doc[key] for key in ('task', 'app_user_role', 'asset_role', 'grants'))
    if not isinstance(task, str) or task not in TASKS or app_role not in ('ON_APP', 'EXTERNAL', 'UNKNOWN'):
        fail('META_ACCESS_USE_CASE_INVALID')
    if asset_role not in ('REPORTED_ASSIGNED', 'UNASSIGNED', 'UNKNOWN'):
        fail('META_ACCESS_ASSET_ROLE_INVALID')
    if not isinstance(grants, dict) or any(
            k not in ('ads_read', 'ads_management', 'business_management', 'pages_read_engagement')
            or v not in ('STANDARD', 'ADVANCED', 'UNKNOWN') for k, v in grants.items()):
        fail('META_ACCESS_GRANTS_INVALID')
    required = TASKS[task]
    blockers = []
    if required and asset_role != 'REPORTED_ASSIGNED':
        blockers.append('CONFIRM_ACCESS_TO_NAMED_ASSET_IN_BUSINESS_SETTINGS')
    if required and app_role == 'UNKNOWN':
        blockers.append('IDENTIFY_APP_USER_ROLE_AND_USE_CASE')
    for grant in required:
        access = grants.get(grant, 'UNKNOWN')
        if access == 'UNKNOWN':
            blockers.append('CHECK_GRANTED_SCOPE_AND_ACCESS_LEVEL')
        elif app_role == 'EXTERNAL' and access != 'ADVANCED':
            blockers.append('CHECK_ADVANCED_ACCESS_APP_REVIEW_AND_BUSINESS_VERIFICATION')
    if task == 'PUBLIC_LIBRARY':
        blockers.append('VERIFY_AD_LIBRARY_ELIGIBILITY_AND_REGIONAL_COVERAGE_SEPARATELY')
    if task == 'PAGE_LIVE':
        blockers.append('CHECK_PAGE_ACCESS_AND_LIVE_VIDEO_EDGE_FOR_THIS_PAGE')
    return dict(status='META_PREREQUISITES_REVIEW',
                task=task, checked_on=CHECKED_ON,
                reference_api_version='v27.0', declared_access_requirements=list(required),
                conditions=list(dict.fromkeys(blockers)), sources=SOURCES,
                marketing_api_feature={
                    'status': 'CHECK_APP_DASHBOARD_FOR_NAMED_USE_CASE',
                    'label': 'Marketing API Access Tier (Limited / Full)',
                    'basis': 'MARKETING_API_FEATURE_TIER_SEPARATE_FROM_PERMISSION_ACCESS_LEVEL',
                },
                approval_types={
                    'app_review': '针对应用请求的权限和功能',
                    'business_verification': '核实业务主体，按应用控制台显示的要求办理',
                    'advertising_review': '平台审核投放内容，继续受广告政策和投递状态约束',
                },
                charges={
                    'request_pricing': 'UNVERIFIED_FOR_CUSTOMER',
                    'spend': '仅在具名账户核定预算、币种和账单安排后进入独立投放审批',
                    'external_services': '按实际供应商报价和客户决定',
                },
                on_account_verified=False, network_used=False, actions_authorized=False)
