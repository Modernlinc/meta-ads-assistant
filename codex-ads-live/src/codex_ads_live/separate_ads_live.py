"""Review separately collected ads and Page Live observations locally."""
from hashlib import sha256
from pathlib import Path

from .journal import digest
from .live_timeline import timeline_markdown
from .local_paths import checked_path
from .metric_adapter import METRIC_CHANNELS
from .monitor import ReadMonitor, account_cycle_basis, validate_relations
from .monitor_tools import cycle_coverage_markdown, live_metrics_markdown, report_data, write_report
from .rate_limit import resume_time
from .read_adapter import INSIGHTS_OPERATIONS
from .review import build_review, evidence, markdown as review_markdown
from .validation import fail, object_only, strict_json


REPORT_SCHEMA = 'bounded-separate-ads-live-review-v1'
REPORT_STATUS = 'SEPARATE_ADS_LIVE_REVIEW_WRITTEN'
REPORT_MEMBERS = ('ADS_LIVE_REVIEW.json','ADS_LIVE_REVIEW.md')
REPORT_LIMIT = 8_000_000


def _review_member(path, limit):
    path = checked_path(path,link_error='SEPARATE_REVIEW_RECEIPT_LINK',volume_error='SEPARATE_REVIEW_VOLUME_NOT_MOUNTED')
    if not path.is_file(): fail('SEPARATE_REVIEW_RECEIPT_FILE_REQUIRED')
    with path.open('rb') as handle:
        raw = handle.read(limit+1)
    if len(raw)>limit: fail('SEPARATE_REVIEW_RECEIPT_FILE_TOO_LARGE')
    return raw


def _read_review_receipt(path, *, input_field='input_snapshots'):
    path = Path(path)
    raw = _review_member(path,16000)
    receipt = strict_json(raw.decode('utf-8'))
    if type(receipt) is not dict or receipt.get('status')!=REPORT_STATUS or receipt.get('live_action_authorized') is not False:
        fail('SEPARATE_REVIEW_RECEIPT_INVALID')
    if input_field not in receipt:
        if input_field=='input_runs':
            fail('SEPARATE_REVIEW_RUN_REFERENCES_REQUIRED')
        fail('SEPARATE_REVIEW_SNAPSHOT_REFERENCES_REQUIRED')
    references = receipt[input_field]
    object_only(references,('ads','live'),('ads','live'))
    if any(type(ref) is not dict for ref in references.values()):
        fail('SEPARATE_REVIEW_RECEIPT_INVALID')
    files = receipt.get('files')
    if type(files) is not list or len(files)!=len(REPORT_MEMBERS):
        fail('SEPARATE_REVIEW_RECEIPT_INVALID')
    contents = {}
    for name in REPORT_MEMBERS:
        file_raw = _review_member(path.parent/name,REPORT_LIMIT)
        expected = dict(path=name,bytes=len(file_raw),sha256=sha256(file_raw).hexdigest())
        if expected not in files: fail('SEPARATE_REVIEW_REPORT_HASH_MISMATCH')
        contents[name] = file_raw
    report = strict_json(contents[REPORT_MEMBERS[0]].decode('utf-8'),max_chars=REPORT_LIMIT)
    if (type(report) is not dict or report.get('schema')!=REPORT_SCHEMA
            or report.get(input_field)!=references
            or report.get('mode')!=receipt.get('mode')
            or digest(report.get('input_evidence'))!=report.get('input_evidence_sha256')
            or report['input_evidence_sha256']!=receipt.get('input_evidence_sha256')):
        fail('SEPARATE_REVIEW_RECEIPT_INVALID')
    return receipt, sha256(raw).hexdigest()


def save_separate_review(ads_monitor, live_monitor, directory, *, from_review_receipt=None):
    if type(ads_monitor) is not ReadMonitor or type(live_monitor) is not ReadMonitor:
        fail('MONITOR_REPORT_REQUIRES_VALIDATED_STATE_READER')
    if ads_monitor.journal.directory == live_monitor.journal.directory:
        fail('SEPARATE_REVIEW_REQUIRES_TWO_DIRECTORIES')
    if ads_monitor.mode != live_monitor.mode:
        fail('SEPARATE_REVIEW_MODE_MISMATCH')
    prior_receipt, replayed_from = ({},None) if from_review_receipt is None else _read_review_receipt(from_review_receipt)
    references = prior_receipt.get('input_snapshots',{})
    ads, ads_reference = ads_monitor.review_snapshot(references.get('ads'))
    live, live_reference = live_monitor.review_snapshot(references.get('live'))
    return _write_separate_review(ads,live,directory,'input_snapshots',dict(ads=ads_reference,live=live_reference),
                                  prior_receipt,replayed_from)


def save_archived_separate_review(ads_receipt, live_receipt, directory, *, from_review_receipt=None):
    from .ads_reconciliation import read_run_observations
    from .page_contract_evidence import read_page_run_observations

    if Path(live_receipt).name!='RECEIPT.json':
        fail('SEPARATE_REVIEW_LIVE_RUN_RECEIPT_REQUIRED')
    previous, replayed_from = ({},None) if from_review_receipt is None else _read_review_receipt(
        from_review_receipt,input_field='input_runs')
    ads, ads_reference = read_run_observations(ads_receipt,workspace_insights=True)
    live, live_evidence = read_page_run_observations(Path(live_receipt).parent)
    live_reference = {key:live_evidence[key] for key in ('run_id','intent_sha256','report_sha256','receipt_sha256')}
    live_reference['status'] = live_evidence['recorded_status']
    references = dict(ads=ads_reference,live=live_reference)
    if previous and previous['input_runs']!=references:
        fail('SEPARATE_REVIEW_ARCHIVE_REFERENCES_MISMATCH')
    return _write_separate_review(ads,live,directory,'input_runs',references,previous,replayed_from)


def _write_separate_review(ads, live, directory, input_field, references, prior_receipt, replayed_from):
    if ads['mode'] == 'READ_ONLY':
        identity = ads.get('workspace_identity_sha256')
        if not identity or identity != live.get('workspace_identity_sha256'):
            fail('SEPARATE_REVIEW_WORKSPACE_UNVERIFIED_OR_DIFFERENT')
    ad_sources = ads.get('sources',{})
    if (set(ad_sources) not in ({'account','ads'}, {'account','ads','ad_links'})
            or ad_sources['ads']['plan']['operation'] not in INSIGHTS_OPERATIONS):
        fail('SEPARATE_REVIEW_REQUIRES_ADS_INSIGHTS_STATE')
    live_sources = live.get('sources',{})
    if not {'page','live'} <= set(live_sources) <= {'page','live','comments','live_post',*METRIC_CHANNELS}:
        fail('SEPARATE_REVIEW_REQUIRES_PAGE_LIVE_STATE')
    if live_sources['page']['plan']['page_id'] != live_sources['live']['plan']['page_id']:
        fail('SEPARATE_REVIEW_PAGE_SCOPE')
    validate_relations(live_sources,strict=True)

    review = build_review(dict(ads,sources=ad_sources|live_sources))
    live_report = report_data(live)
    coverage = {label:dict(phase=state['phase'], last_error=state['last_error'],
                           source_evidence={name:evidence(row) for name,row in state['sources'].items()})
                for label,state in (('ads',ads),('live',live))}
    diagnostics = {}
    for label,state in (('ads',ads),('live',live)):
        batch = {key:state[key] for key in ('rate_limit','recorded_run_error_code') if key in state}
        if batch:
            diagnostics[label] = batch
    report = dict(schema=REPORT_SCHEMA,mode=ads['mode'],
                  account_id=ad_sources['account']['plan']['account_id'],
                  page_id=live_sources['page']['plan']['page_id'], live_id=live_sources['live']['plan']['object_id'],
                  input_evidence=coverage, business_review=review,
                  live_end=live_report['live_end'], live_metrics=live_report['four_live_metrics'],
                  recent_comment_sample=live_report.get('live_comment_sample'),
                  live_video_binding=live_report.get('live_video_binding'),
                  input_scope='SAME_RECORDED_WORKSPACE' if ads['mode']=='READ_ONLY' else 'SYNTHETIC_SEPARATE_RUNS',
                  causal_claim_allowed=False, network_used=False, native_credential_accessed=False,
                  real_connection_verified=False, live_action_authorized=False)
    report[input_field] = references
    if diagnostics:
        report['input_diagnostics'] = diagnostics
    timeline = live_report.get('live_cycle_timeline')
    if timeline is not None:
        report['live_cycle_timeline'] = timeline
        coverage['live']['cycle_timeline_sha256'] = digest(timeline)
    live_coverage = live_report.get('page_live_cycle_coverage')
    if live_coverage is not None:
        report['page_live_cycle_coverage'] = live_coverage
        coverage['live']['cycle_coverage_sha256'] = digest(live_coverage)
    ads_coverage = ads.get('account_cycle_coverage')
    ads_cycle = account_cycle_basis(ads)
    if ads_coverage is not None:
        report['account_cycle_coverage'] = ads_coverage
        report['account_cycle_pagination'] = ads['account_cycle_pagination']
        coverage['ads']['cycle_coverage_sha256'] = digest(ads_coverage)
        coverage['ads']['cycle_pagination'] = ads['account_cycle_pagination']
    elif ads_cycle is not None:
        coverage['ads'].update(entered_cycles=ads_cycle['cycle'],cycle_pagination=ads_cycle['pagination'])
    # Diagnostics derive from the same pinned inputs. Keep the coverage digest
    # compatible with receipts created before diagnostics were displayed.
    report['input_evidence_sha256'] = digest(coverage)
    if prior_receipt and (prior_receipt['mode'],prior_receipt['input_evidence_sha256'])!=(
            report['mode'],report['input_evidence_sha256']):
        fail('SEPARATE_REVIEW_SNAPSHOT_PROJECTION_CHANGED')
    archive = input_field=='input_runs'
    lines = ['# 广告与 Page Live 双归档复盘' if archive else '# 广告与 Page Live 双目录复盘', '',
             f"账户 `{report['account_id']}`。Page `{report['page_id']}`。Live `{report['live_id']}`。",
             f"证据模式 `{report['mode']}`。输入范围 `{report['input_scope']}`。"]
    for name,label in (('ads','广告'),('live','直播')):
        reference = references[name]
        if archive:
            lines.append(f"{label}归档运行 `{reference['run_id']}`，记录状态 `{reference['status']}`。")
        else:
            lines.append(f"{label}快照 `{reference['snapshot']}`，本地状态观察于 {reference['phase_observed_at']}。")
        batch = coverage[name]
        lines.append(f"{label}批次阶段 `{batch['phase']}`，批次错误 `{batch['last_error'] or '无'}`。")
        details = diagnostics.get(name,{})
        error = details.get('recorded_run_error_code')
        if error is not None and error!=batch['last_error']:
            lines.append(f"{label}归档运行错误 `{error}`。")
        hold = details.get('rate_limit')
        if hold is not None:
            lines.append(f"{label}批次限流记录的最早恢复时间为 {resume_time(hold).isoformat()}，依据 `{hold['basis']}`。"
                         f"该批次已预留 {hold['automatic_retries_reserved']} 次自动补试。")
    lines += ['', '| 批次 | 来源 | 结果 | 有效页 | 观察时间 | 来源错误 |', '|---|---|---|---:|---|---|']
    for label, batch in coverage.items():
        for name, row in batch['source_evidence'].items():
            lines.append(f"| {label} | {name} | {row['result']} | {row['valid_pages']} | "
                         f"{row['observed_at'] or '未知'} | {row['last_error'] or '无'} |")
    if ads_cycle is not None:
        lines += ['', f"广告轮次 {ads_cycle['cycle']}，分页方式 `{ads_cycle['pagination']}`。"]
        lines += cycle_coverage_markdown(ads_coverage)
    lines += ['', '## 直播观察', '', f"直播结束证据 `{report['live_end']}`。"]
    lines += cycle_coverage_markdown(live_coverage)
    if report['recent_comment_sample'] is not None:
        comments = report['recent_comment_sample']
        count = comments['observed_comment_count']
        lines.append(f"顶层评论已观察到 {count if count is not None else '未知'} 条，覆盖 `{comments['coverage']}`。")
    lines += timeline_markdown(timeline)
    lines += live_metrics_markdown(report['live_metrics'])
    lines += review_markdown(review)
    replay = dict(replayed_from_receipt_sha256=replayed_from) if replayed_from is not None else {}
    return write_report(directory,'ADS_LIVE_REVIEW',report,lines,REPORT_STATUS,
                        relationship=review['object_relationship']['state'],network_used=False,native_credential_accessed=False,
                        input_evidence_sha256=report['input_evidence_sha256'],**{input_field:references},**replay)
