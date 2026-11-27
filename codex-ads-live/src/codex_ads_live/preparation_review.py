"""Assess the existing non-secret preparation form; declarations never grant access."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfoNotFoundError
from .timezones import load_zone

from .connection import _write
from .journal import canonical, digest
from .monitor_tools import new_directory
from .onboarding import (PLATFORM_ACCESS_LEVELS, PLATFORM_PERMISSION_ITEMS, PLATFORM_REVIEW_STATES,
                         blank_preparation, preflight)
from .validation import aware_time, fail, object_only, strict_json
from .workflow import gate

ASSETS=('business_portfolio','app','ad_account','facebook_page')
FLAGS=('scope_approved','identity_and_asset_binding_verified','actual_grants_verified')
PAGE_TYPES=('FACEBOOK_PAGE','PERSONAL_PROFILE','PROFESSIONAL_PROFILE','INSTAGRAM_LIVE','UNKNOWN')


def utcnow():return datetime.now(timezone.utc)


def nullable_pattern(value,pattern,code):
    if value is not None and (type(value) is not str or not re.fullmatch(pattern,value)):fail(code)


def validate_preparation(value):
    template=blank_preparation();object_only(value,template,template.keys()-{'platform_review'})
    for key in ('schema','mode','authority'):
        if value[key]!=template[key]:fail('PREPARATION_SCHEMA_OR_AUTHORITY')
    if value['real_connection_enabled'] is not False or value['real_write_enabled'] is not False:
        fail('PREPARATION_CANNOT_ENABLE_ACTIONS')
    object_only(value['assets'],ASSETS,ASSETS)
    for asset in value['assets'].values():
        object_only(asset,('id','ownership_evidence_ref'),('id','ownership_evidence_ref'))
        nullable_pattern(asset['id'],r'[0-9]{1,40}','PREPARATION_ID_FORMAT')
        nullable_pattern(asset['ownership_evidence_ref'],r'EV_[A-Z0-9_]{1,80}','PREPARATION_EVIDENCE_REF_FORMAT')
    if value['page_identity_type'] not in PAGE_TYPES:fail('PREPARATION_PAGE_TYPE')
    object_only(value['account_context'],('currency','timezone'),('currency','timezone'))
    nullable_pattern(value['account_context']['currency'],r'[A-Z]{3}','PREPARATION_CURRENCY_FORMAT')
    zone=value['account_context']['timezone']
    nullable_pattern(zone,r'[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*','PREPARATION_TIMEZONE_FORMAT')
    if zone is not None and len(zone)>100:fail('PREPARATION_TIMEZONE_FORMAT')
    object_only(value['requested_work'],('ads_read','page_live_read'),('ads_read','page_live_read'))
    if any(type(flag) is not bool for flag in value['requested_work'].values()):fail('PREPARATION_WORK_FLAGS')
    nullable_pattern(value['existing_live_id'],r'[0-9]{1,40}','PREPARATION_ID_FORMAT')
    review=value['review'];object_only(review,(*FLAGS,'reviewed_at','expires_at'),(*FLAGS,'reviewed_at','expires_at'))
    if any(type(review[key]) is not bool for key in FLAGS):fail('PREPARATION_REVIEW_FLAGS')
    for key in ('reviewed_at','expires_at'):
        if review[key] is not None:aware_time(review[key])
    if review['reviewed_at'] is not None and review['expires_at'] is not None:
        if aware_time(review['expires_at'])<=aware_time(review['reviewed_at']):fail('PREPARATION_REVIEW_TIME_ORDER')
    if 'platform_review' in value:
        platform=value['platform_review']
        keys=template['platform_review']
        object_only(platform,keys,keys)
        for key in ('app_id','business_portfolio_id'):
            nullable_pattern(platform[key],r'[0-9]{1,40}','PREPARATION_ID_FORMAT')
        if platform['observed_at'] is not None:aware_time(platform['observed_at'])
        object_only(platform['items'],PLATFORM_REVIEW_STATES,PLATFORM_REVIEW_STATES)
        for key,row in platform['items'].items():
            fields=('state','evidence_ref')
            allowed=(*fields,'current_access_level') if key in PLATFORM_PERMISSION_ITEMS else fields
            object_only(row,allowed,fields)
            if row['state'] not in PLATFORM_REVIEW_STATES[key]:fail('PREPARATION_PLATFORM_REVIEW_STATE')
            if 'current_access_level' in row and row['current_access_level'] not in PLATFORM_ACCESS_LEVELS:
                fail('PREPARATION_PLATFORM_ACCESS_LEVEL')
            nullable_pattern(row['evidence_ref'],r'EV_[A-Z0-9_]{1,80}','PREPARATION_EVIDENCE_REF_FORMAT')
    if len(canonical(value).encode('utf-8'))>16000:fail('PREPARATION_INPUT_TOO_LARGE')
    return value


def safe_path(path):
    from .local_paths import checked_path
    return checked_path(path, link_error='PREPARATION_PATH_LINK', volume_error='PREPARATION_VOLUME_NOT_MOUNTED')


def load_preparation(path):
    path=safe_path(path)
    if not path.is_file() or path.stat().st_size>16000:fail('PREPARATION_INPUT_FILE')
    raw=path.read_bytes()
    if len(raw)>16000:fail('PREPARATION_INPUT_TOO_LARGE')
    return validate_preparation(strict_json(raw.decode('utf-8-sig')))


def platform_review_plan(value,supported,now):
    """Route operator declarations without treating them as platform verification."""
    report=dict(state='NOT_REQUESTED',records={},platform_status_verified=False)
    if not supported: return report
    record=value.get('platform_review')
    if record is None:
        return dict(report,state='NOT_RECORDED',next_action='可在新版准备表登记本轮 App 页面观察，原表仍可使用。')
    app_id=value['assets']['app']['id']
    if app_id is None or record['app_id']!=app_id:
        return dict(report,state='CHECK_SAME_APP',
                    next_action='从目标 App 面板核对编号，填写与具名 App 一致的页面记录。')
    stamp=record['observed_at']
    if stamp is None or aware_time(stamp)>now:
        return dict(report,state='CHECK_OBSERVATION_TIME',next_action='填写本人查看这些页面时的实际时间，并带时区。')

    actions={
        'UNKNOWN':'核对目标面板实际显示的要求和状态。',
        'NOT_STARTED':'页面确认要求办理后，由本人按显示的步骤准备。',
        'DRAFT':'先核草稿及必填项。本人核对后决定是否提交。',
        'SUBMITTED':'保留提交回执和时间，按当前页面查看结果。',
        'NEEDS_INFORMATION':'按页面列出的缺项补材料，由本人核对并提交。',
        'REJECTED':'逐项记录拒绝原因，调整说明或材料后由本人复查。',
        'NOT_PUBLISHED':'先核本次用途的发布要求及当前缺项。',
        'PUBLISHED':'记录发布状态。分别核对本轮使用的权限。',
        'NO_PUBLICATION_STEP_FOR_APP_TYPE':'保留当前 App 类型与面板依据，继续核对本轮权限的访问级别及审核状态。',
        'NOT_REQUIRED':'保留该应用、用途和访问范围的页面依据。',
        'VERIFIED':'保留该业务组合的公司验证结果，继续核应用及权限。',
        'APPROVED':'保留对应权限和访问级别。后续单独核对实际授权与具名读取。',
    }
    selected={'app_publication':supported,'business_verification':supported}
    if 'ads_read' in supported: selected['ads_read']=['ads_read']
    if 'page_live_read' in supported:
        selected.update(pages_read_engagement=['page_live_read'],read_insights=['page_live_read'])
    business_id=value['assets']['business_portfolio']['id']
    for key,affected in selected.items():
        item=record['items'][key]
        state=item['state']
        level=item.get('current_access_level','UNKNOWN') if key in PLATFORM_PERMISSION_ITEMS else None
        company_record=key=='business_verification' and state!='UNKNOWN'
        if company_record and (record['business_portfolio_id']!=business_id
                               or (business_id is None and state!='NOT_REQUIRED')):
            action='核对公司验证使用的是本轮业务组合，然后更新页面记录。'
            state,label='UNKNOWN','CHECK_SAME_BUSINESS'
        elif (state!='UNKNOWN' or level not in (None,'UNKNOWN')) and item['evidence_ref'] is None:
            action='保留页面原件，在准备表补交证据别名后复查。'
            label='ADD_EVIDENCE_REFERENCE'
        elif company_record and business_id is None:
            action='只保留当前 App 页面显示的要求。确定本轮业务组合后重新核对。'
            label='APP_CONTEXT_ONLY'
        else:
            action=actions[state]
            label='DASHBOARD_CHECK_NEEDED' if state=='UNKNOWN' and level in (None,'UNKNOWN') else 'OPERATOR_DECLARED_WITH_REFERENCE'
            if level=='STANDARD_ACCESS':
                action+=' 核对当前操作者在该 App 中的角色及具名资产权限。'
        if key=='read_insights':
            action='如需帖子指标，'+action
        row=dict(declared_state=state,evidence_state=label,affects=affected,next_action=action)
        if level is not None:
            row['declared_current_access_level']=level
        report['records'][key]=row
    return dict(report,state='OPERATOR_DASHBOARD_RECORD_REVIEWED',observed_at=stamp,sop_section=4)


def assess_preparation(value,*,clock=utcnow):
    # Snapshot caller data. No reference or URL in this form is dereferenced.
    value=strict_json(canonical(value));validate_preparation(value)
    now=clock();aware_time(now.isoformat());local=preflight();issues=[]
    requested=value['requested_work'];lanes=[key for key in ('ads_read','page_live_read') if requested[key]]
    live_supported=requested['page_live_read'] and value['page_identity_type']=='FACEBOOK_PAGE'
    supported=[key for key in lanes if key=='ads_read' or live_supported]
    def add(code,fields,owner,section,action,evidence,affected=None):
        issues.append(dict(code=code,fields=fields,owner=owner,sop='references/CUSTOMER_SOP.md',sop_section=section,
                           action=action,completion_evidence=evidence,affects=supported if affected is None else affected))
    local_expected={'python':'PASS','operating_system':'IN_SCOPE','timezone_data':'PASS','package_integrity':'PASS'}
    for key,expected in local_expected.items():
        if local['checks'].get(key)!=expected:
            add('LOCAL_'+key.upper()+'_REVIEW',['local_preflight.checks.'+key],'IMPLEMENTER',2,
                '按分平台安装说明修复该本机条件，保留失败记录后重新运行preflight。','当前机器该检查项通过的preflight回执。',lanes)
    if not lanes:
        add('WORK_SCOPE_NOT_SELECTED',['requested_work'],'CUSTOMER',1,
            '选择本轮需要广告读取、Page直播读取或两者；不因填表自动连接。','本人确认的本轮目标和负责人记录。',[])
    if requested['page_live_read'] and not live_supported:
        add('PAGE_IDENTITY_UNSUPPORTED_OR_UNKNOWN',['page_identity_type'],'CUSTOMER',1,
            '确认对象是Facebook公共主页；个人/专业模式主页与Instagram直播不在本产品范围。',
            'Facebook Page身份记录；不改用其他身份绕过。',['page_live_read'])
    required=['app'] if supported else []
    if requested['ads_read']:required.append('ad_account')
    if live_supported:required.append('facebook_page')
    def asset_lanes(name):return supported if name=='app' else ['ads_read'] if name=='ad_account' else ['page_live_read']
    for name in required:
        if value['assets'][name]['id'] is None:
            add(name.upper()+'_ID_MISSING',['assets.'+name+'.id'],'CUSTOMER_WITH_CODEX',3,
                '在本人官方资产页面核对这个具名对象的规范数字编号，仅填编号，不粘贴完整URL或凭据。',
                '具名对象编号与所属关系的非秘密核对记录。',asset_lanes(name))
    context=value['account_context'];zone_state='NOT_PROVIDED'
    if context['timezone'] is not None:
        try:load_zone(context['timezone']);zone_state='LOCAL_IANA_NAME_AVAILABLE'
        except (ValueError,ZoneInfoNotFoundError):zone_state='UNRESOLVED_NAME_OR_LOCAL_DATABASE'
    if requested['ads_read']:
        for key in ('currency','timezone'):
            if context[key] is None or (key=='timezone' and zone_state!='LOCAL_IANA_NAME_AVAILABLE'):
                add('ACCOUNT_'+key.upper()+'_REQUIRED',['account_context.'+key],'CUSTOMER_WITH_CODEX',7,
                    '核对广告账户币种/IANA时区；时区无法识别时同时检查名称与本机时区数据库，不猜测替代值。',
                    '账户实际币种/时区记录，后续与API返回再核对。',['ads_read'])
    for name in required:
        if value['assets'][name]['ownership_evidence_ref'] is None:
            add(name.upper()+'_OWNERSHIP_REFERENCE_MISSING',['assets.'+name+'.ownership_evidence_ref'],'CUSTOMER_WITH_CODEX',3,
                '记录归属/角色证据的EV_别名，原件由本人保管；本命令不打开或验证该引用。',
                '可回接本人保存原件的证据别名及后续人工核对。',asset_lanes(name))
    if live_supported and value['existing_live_id'] is None:
        add('EXISTING_LIVE_NOT_AVAILABLE',['existing_live_id'],'CUSTOMER',8,
            '有现成自有Page直播时提供具名数字ID；没有就保留待测，不创建直播补证据。',
            '现成Live对象线索，后续单独核Page/Video关系。',['page_live_read'])
    review=value['review']
    flag_tasks={
        'scope_approved':('SCOPE_REVIEW_REQUIRED',1,'本人确认本轮任务与允许动作，保留具体范围；本表勾选不转移授权。'),
        'identity_and_asset_binding_verified':('IDENTITY_BINDING_REVIEW_REQUIRED',3,'本人/实施方核App、账号、具名资产与角色关系，记录各自证据。'),
        'actual_grants_verified':('GRANT_EVIDENCE_REVIEW_REQUIRED',5,'本人完成官方授权后核实际grant、App/主体与期限；不要提供Token或用App发布状态代替grant。'),
    }
    for key,(code,section,action) in flag_tasks.items():
        if supported and not review[key]:add(code,['review.'+key],'CUSTOMER_WITH_CODEX',section,action,'当轮具名核对记录，原始授权仍由本人管理。')
    window='NOT_RECORDED'
    if review['reviewed_at'] is not None and review['expires_at'] is not None:
        start=aware_time(review['reviewed_at']);end=aware_time(review['expires_at'])
        window='DECLARED_IN_WINDOW' if start<=now<end else 'DECLARED_EXPIRED' if now>=end else 'DECLARED_IN_FUTURE'
    if supported and window!='DECLARED_IN_WINDOW':
        add('REVIEW_WINDOW_'+window,['review.reviewed_at','review.expires_at'],'CUSTOMER_WITH_CODEX',5,
            '重新核当轮审查时间与有效期限；日期只是填报，不证明实际Token期限或允许续期。','带时区且当前有效的审查记录，后续与实际认证元数据比较。')
    open_before_evidence=len(issues)
    if supported:
        add('DECLARATIONS_REQUIRE_EVIDENCE_REVIEW',['review','assets'],'CUSTOMER_WITH_CODEX',4,
            '逐项核App用例/发布/验证/审核要求、资产关系与实际授权原件。准备表不证明这些步骤已通过；连接前另核principal、proof要求和具名请求。',
            '分别记录App要求、本人授权、实际grant、具名端点结果；连接回执不是本表生成。')
    lane_reports={}
    for name in ('ads_read','page_live_read'):
        applicable=[row['code'] for row in issues if name in row['affects']]
        lane_reports[name]=dict(requested=requested[name],stage=(applicable[0] if applicable else 'NOT_REQUESTED'),
                                open_checks=applicable,real_connection_verified=False)
    # Existing gate contract remains deliberately unproven; declarations cannot fill it.
    gates={'connection':gate('connection',{},mode='REAL_READ_PLANNING')} if supported else {}
    for name,flow in (('ads_read','ads_read'),('page_live_read','live_identify')):
        if name in supported:gates[name]=gate(flow,{},mode='REAL_READ_PLANNING')
    return dict(schema='customer-preparation-review-v1',status='PREPARATION_ASSESSED',observed_at=now.isoformat(),
        input_sha256=digest(value),assessment_scope='LOCAL_PREFLIGHT_AND_DECLARATIONS_ONLY',
        stage=issues[0]['code'] if issues else 'WORK_SCOPE_NOT_SELECTED',
        form_state='DECLARED_READY_FOR_EVIDENCE_REVIEW' if lanes and open_before_evidence==0 else 'PREPARATION_ACTION_REQUIRED',
        lanes=lane_reports,issues=issues,local_preflight=local,
        declared_assets={name:dict(id_recorded=asset['id'] is not None,evidence_reference_recorded=asset['ownership_evidence_ref'] is not None,
                                  required_for_selected_read=name in required,verified=False) for name,asset in value['assets'].items()},
        declared_account_context={**context,'timezone_name_state':zone_state,'matches_api_account':'NOT_VERIFIED'},
        declared_review={key:review[key] for key in FLAGS},review_window=window,
        evidence_references_followed=False,app_review_status='NOT_ASSESSED_BY_THIS_FORM',
        business_verification_required='NOT_INFERRED_FROM_BUSINESS_ID',workflow_gates=gates,
        platform_review=platform_review_plan(value,supported,now),
        independent_local_work=['阅读客户SOP与分平台安装指南','运行本地条件满足后的合成演示','按允许材料制作离线方案'],
        real_connection_verified=False,live_action_authorized=False,credential_storage_accessed=False,network_used=False,
        source_preparation_modified=False,workflow_state_modified=False)


def markdown(report):
    lines=['# 客户准备阶段检查','','本报告只核本机条件和填报信息，不证明身份、权限或连接。',
        '输入hash：'+report['input_sha256'],'观察时间：'+report['observed_at'],'当前阶段：'+report['stage'],
        '表单状态：'+report['form_state'],'','| 分支 | 当前阶段 |','|---|---|']
    lines.extend('| '+name+' | '+row['stage']+' |' for name,row in report['lanes'].items())
    lines+=['','## 缺项和下一步','']
    for item in report['issues']:
        lines += ['### '+item['code'],'','字段：'+', '.join(item['fields']),
            '负责人：'+item['owner']+'；客户SOP第'+str(item['sop_section'])+'步。',item['action'],
            '完成证据：'+item['completion_evidence'],'']
    platform=report.get('platform_review')
    if platform and platform['state']!='NOT_REQUESTED':
        lines+=['## 第 4 步 · 本人核对的 Meta 页面信息','',
                '该部分是本人页面记录，审核通过、实际授权、资产角色和端点读取分别取证。']
        if 'next_action' in platform:lines+=['',platform['next_action']]
        if platform['records']:
            lines+=['',f"本人观察时间 {platform['observed_at']}。",'',
                    '| 核对项 | 本人填写状态 | 当前访问级别 | 证据状态 | 下一步 |','|---|---|---|---|---|']
            names={'app_publication':'App 发布','business_verification':'公司验证',
                   'ads_read':'权限审核 · ads_read','pages_read_engagement':'权限审核 · pages_read_engagement',
                   'read_insights':'权限审核 · read_insights（帖子指标可选）'}
            for key,row in platform['records'].items():
                level=row.get('declared_current_access_level','不适用')
                lines.append(f"| {names[key]} | {row['declared_state']} | {level} | {row['evidence_state']} | {row['next_action']} |")
        lines.append('')
    lines += ['资产编号和证据别名不在本报告回显，仍保留在原准备表。即使所有声明为true，实际连接关口仍为NEEDS_EVIDENCE。',
        '广告与直播分开处理；没有Live样本不阻止独立广告准备。未选择分支不自动扩展范围。','']
    return '\n'.join(lines)


def review_preparation(path,output=None,*,clock=utcnow):
    value=load_preparation(path);report=assess_preparation(value,clock=clock)
    if output is not None:
        root=new_directory(safe_path(output));_write(root,'ASSESSMENT.json',report)
        with (root/'NEXT_STEPS.md').open('x',encoding='utf-8') as handle:
            handle.write(markdown(report));handle.flush();os.fsync(handle.fileno())
        files=[dict(path=p.name,bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(root.iterdir())]
        _write(root,'RECEIPT.json',dict(status='PREPARATION_ASSESSED',input_sha256=report['input_sha256'],files=files,
            real_connection_verified=False,live_action_authorized=False,credential_storage_accessed=False,network_used=False))
    return report
