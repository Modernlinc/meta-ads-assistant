"""Bounded cycle summaries for one pinned Page Live foreground run."""
from decimal import Decimal
from hashlib import sha256
import re

from .read_adapter import LIVE_END_STATES, LIVE_STATUSES, RECENT_LIVE_COMMENTS, numeric_string
from .validation import aware_time, fail, object_only

HISTORY_LIMIT = 24
RESULTS = ('NOT_SAMPLED', 'GAP', 'COMPLETE', 'EMPTY', 'ERROR')
VIEWER_RESULTS = ('NOT_SAMPLED', 'VALUE', 'EMPTY', 'MISSING', 'ERROR', 'AMBIGUOUS', 'NON_SCALAR')
COMMENT_SAMPLE_RESULTS = ('COMPLETE','EMPTY','GAP')
COMMENT_CHECKPOINT = 'page_live_comment_checkpoint'


def _single_page_comment_ids(source):
    if (source.get('pages') == 1 and source.get('result') in COMMENT_SAMPLE_RESULTS
            and source.get('plan',{}).get('operation') == RECENT_LIVE_COMMENTS):
        return {row['id'] for row in source['rows']}
    return None


def previous_comment_ids(state):
    history = state.get('live_cycle_history',[])
    if not history: return None
    previous = history[-1]
    source = state['sources'].get('comments',{})
    if (previous['cycle'] != state['cycle_count'] or previous['comment_pages'] != 1
            or previous['comment_result'] != source.get('result')):
        return None
    ids = _single_page_comment_ids(source)
    return ids if ids is not None and len(ids) == previous['comment_count'] else None


def comment_digests(ids):
    return sorted(sha256(('page-live-comment:'+item).encode('utf-8')).hexdigest() for item in ids)


def comment_checkpoint(state, row, sampled):
    if 'page_run' not in state or row['live_status']!='LIVE' or not {'page','live','comments'} <= sampled:
        return None
    ids = _single_page_comment_ids(state['sources']['comments'])
    if ids is None:
        return None
    return dict(cycle=row['cycle'],observed_at=row['observed_at'],id_digests=comment_digests(ids))


def validate_comment_checkpoint(state):
    error = 'MONITOR_PAGE_LIVE_COMMENT_CHECKPOINT'
    checkpoint = state[COMMENT_CHECKPOINT]
    keys = ('cycle','observed_at','id_digests')
    object_only(checkpoint,keys,keys)
    history = state.get('live_cycle_history',[])
    comments = state['sources'].get('comments',{})
    plan = comments.get('plan',{})
    digests = checkpoint['id_digests']
    if ('page_run' not in state or not history or plan.get('operation')!=RECENT_LIVE_COMMENTS
            or type(digests) is not list or len(digests)>plan['limit']
            or any(type(item) is not str or not re.fullmatch('[0-9a-f]{64}',item) for item in digests)
            or sorted(set(digests))!=digests):
        fail(error)
    last = history[-1]
    if (type(checkpoint['cycle']) is not int or (checkpoint['cycle'],checkpoint['observed_at'])!=
            (last['cycle'],last['observed_at']) or last['live_status']!='LIVE'
            or last['comment_pages']!=1 or last['comment_result'] not in COMMENT_SAMPLE_RESULTS
            or last['comment_count']!=len(digests)):
        fail(error)
    if last['cycle']==state['cycle_count'] and checkpoint!=comment_checkpoint(
            state,last,set(state.get('page_live_cycle_sources',[]))):
        fail(error)


def resume_comment_checkpoint(old, stamp):
    checkpoint = old.get(COMMENT_CHECKPOINT)
    run = old.get('page_run',{})
    if (checkpoint is None or len(run)!=1 or old['last_error']=='CLOCK_ROLLBACK'
            or aware_time(stamp) < aware_time(old['last_heartbeat'])
            or any(old['sources'][key]['result']=='ERROR' for key in ('page','live','comments'))):
        return None
    return dict(run_id=run['id'],**checkpoint)


def summarize_cycle(state, sampled, previous_comments=None, resumed_checkpoint=None):
    sources = state['sources']
    live = sources['live'] if 'live' in sampled else {}
    comments = sources['comments'] if 'comments' in sampled else {}
    metric = sources['live_current_viewers'] if 'live_current_viewers' in sampled else {}
    pages = comments.get('pages', 0)
    viewers = dict(result=metric.get('result', 'NOT_SAMPLED'), value=None, period=None, bucket_end_time=None)
    if viewers['result'] == 'COMPLETE':
        rows = metric['rows']
        if len(rows) != 1:
            viewers['result'] = 'AMBIGUOUS'
        else:
            sample = rows[0]
            if type(sample['value']) is dict:
                viewers['result'] = 'NON_SCALAR'
            else:
                for field in ('value', 'period', 'bucket_end_time'):
                    viewers[field] = sample[field]
                viewers['result'] = sample['state']
    summary = dict(
        cycle=state['cycle_count'], observed_at=state['last_heartbeat'],
        live_result=live.get('result', 'NOT_SAMPLED'),
        live_status=live['rows'][0].get('status') if live.get('result') == 'COMPLETE' else None,
        live_error=live.get('last_error'),
        comment_result=comments.get('result', 'NOT_SAMPLED'), comment_pages=pages,
        comment_count=len(comments['rows']) if pages else None, viewers=viewers,
    )
    current = _single_page_comment_ids(comments)
    if previous_comments is not None and current is not None:
        summary['comment_sample_overlap'] = dict(previous_count=len(previous_comments),
                                                shared_count=len(previous_comments & current),
                                                current_only_count=len(current - previous_comments))
    if (resumed_checkpoint is not None and current is not None and summary['live_status']=='LIVE'
            and {'page','live','comments'} <= sampled):
        before = set(resumed_checkpoint['id_digests'])
        after = set(comment_digests(current))
        summary['resumed_comment_sample_overlap'] = dict(
            from_run_id=resumed_checkpoint['run_id'],from_cycle=resumed_checkpoint['cycle'],
            from_observed_at=resumed_checkpoint['observed_at'],previous_count=len(before),
            shared_count=len(before & after),current_only_count=len(after - before))
    return summary


def validate_history(state, safe_errors):
    history = state['live_cycle_history']
    sources = state['sources']
    live = sources.get('live', {}).get('plan', {})
    identity_only = set(sources) == {'live', 'page'} and live.get('page_id') == sources['page']['plan']['object_id']
    if (type(history) is not list or len(history) > HISTORY_LIMIT
            or ('expected_live_video_id' not in live and not identity_only)):
        fail('MONITOR_LIVE_HISTORY')
    fields = {'cycle', 'observed_at', 'live_result', 'live_status', 'live_error',
              'comment_result', 'comment_pages', 'comment_count', 'viewers'}
    previous_cycle = 0
    previous_time = None
    previous_row = None
    heartbeat = aware_time(state['last_heartbeat'])
    for row in history:
        object_only(row, fields | {'comment_sample_overlap','resumed_comment_sample_overlap'}, fields)
        cycle = row['cycle']
        stamp = aware_time(row['observed_at'])
        live_error = row['live_error']
        if (type(cycle) is not int or not previous_cycle < cycle <= state['cycle_count']
                or stamp > heartbeat or previous_time is not None and stamp < previous_time
                or row['live_result'] not in RESULTS or row['comment_result'] not in RESULTS
                or row['live_status'] not in (None, *LIVE_STATUSES)
                or row['live_status'] is not None and row['live_result'] != 'COMPLETE'
                or live_error is not None and (type(live_error) is not str or live_error not in safe_errors
                                               or row['live_result'] != 'ERROR')):
            fail('MONITOR_LIVE_HISTORY')
        pages, count = row['comment_pages'], row['comment_count']
        if type(pages) is not int or not 0 <= pages <= 1000:
            fail('MONITOR_LIVE_HISTORY')
        if (pages and (type(count) is not int or not 0 <= count <= 1000)
                or not pages and count is not None):
            fail('MONITOR_LIVE_HISTORY')
        if 'comment_sample_overlap' in row:
            overlap = row['comment_sample_overlap']
            keys = ('previous_count','shared_count','current_only_count')
            object_only(overlap,keys,keys)
            if (sources.get('comments',{}).get('plan',{}).get('operation') != RECENT_LIVE_COMMENTS
                    or pages != 1 or row['comment_result'] not in COMMENT_SAMPLE_RESULTS
                    or any(type(overlap[key]) is not int or not 0<=overlap[key]<=1000 for key in keys)
                    or overlap['shared_count'] > overlap['previous_count']
                    or overlap['shared_count'] + overlap['current_only_count'] != count):
                fail('MONITOR_LIVE_COMMENT_OVERLAP')
            if previous_row is None:
                if cycle == 1 or len(history) != HISTORY_LIMIT: fail('MONITOR_LIVE_COMMENT_OVERLAP')
            elif (cycle != previous_cycle+1 or previous_row['comment_pages'] != 1
                  or previous_row['comment_result'] not in COMMENT_SAMPLE_RESULTS
                  or previous_row['comment_count'] != overlap['previous_count']):
                fail('MONITOR_LIVE_COMMENT_OVERLAP')
        if 'resumed_comment_sample_overlap' in row:
            overlap = row['resumed_comment_sample_overlap']
            counts = ('previous_count','shared_count','current_only_count')
            keys = (*counts,'from_run_id','from_cycle','from_observed_at')
            object_only(overlap,keys,keys)
            run_id = overlap['from_run_id']
            run = state.get('page_run',{})
            if (not run or type(run_id) is not str or not re.fullmatch('[0-9a-f]{32}',run_id)
                    or run['id']==run_id or cycle!=1 or row['live_status']!='LIVE'
                    or sources.get('comments',{}).get('plan',{}).get('operation')!=RECENT_LIVE_COMMENTS
                    or pages!=1 or row['comment_result'] not in COMMENT_SAMPLE_RESULTS
                    or type(overlap['from_cycle']) is not int or not 1<=overlap['from_cycle']<=1000000
                    or aware_time(overlap['from_observed_at'])>stamp
                    or any(type(overlap[key]) is not int or not 0<=overlap[key]<=1000 for key in counts)
                    or overlap['shared_count']>overlap['previous_count']
                    or overlap['shared_count']+overlap['current_only_count']!=count):
                fail('MONITOR_LIVE_RESUMED_COMMENT_OVERLAP')
        viewer_fields = {'result', 'value', 'period', 'bucket_end_time'}
        viewers = row['viewers']
        object_only(viewers, viewer_fields, viewer_fields)
        result, value, period = viewers['result'], viewers['value'], viewers['period']
        if (result not in VIEWER_RESULTS or (result == 'VALUE') != (value is not None)
                or result == 'VALUE' and period is None
                or period is not None and (type(period) is not str or not re.fullmatch('[a-z_]{1,32}', period))):
            fail('MONITOR_LIVE_HISTORY')
        if value is not None:
            numeric_string(value, integer=True)
        if viewers['bucket_end_time'] is not None:
            aware_time(viewers['bucket_end_time'])
        previous_cycle, previous_time, previous_row = cycle, stamp, row


def viewer_change(rows, entered):
    if len(rows) < 2 or not rows[-1]['cycle'] == entered == rows[-2]['cycle'] + 1:
        return None,'TWO_ADJACENT_COMPLETED_CYCLES_REQUIRED'
    before, after = rows[-2:]
    statuses = (before['live_status'], after['live_status'])
    if any(state in LIVE_END_STATES for state in statuses):
        return None,'LIVE_END_STATE_PRESENT'
    if statuses != ('LIVE','LIVE'):
        return None,'TWO_LIVE_STATES_REQUIRED'
    old, new = before['viewers'], after['viewers']
    if old['result'] != 'VALUE' or new['result'] != 'VALUE':
        return None,'TWO_VIEWER_VALUES_REQUIRED'
    old_end, new_end = (aware_time(x['bucket_end_time']) if x['bucket_end_time'] else None for x in (old, new))
    if old['period'] != new['period'] or old_end != new_end:
        return None,'VIEWER_BUCKET_MISMATCH'
    if old_end is None and old['period'] != 'lifetime':
        return None,'VIEWER_BUCKET_UNVERIFIED'
    return dict(from_cycle=before['cycle'],to_cycle=after['cycle'],
                difference=int(Decimal(new['value']))-int(Decimal(old['value']))),'MATCHED_LIVE_OBSERVATIONS'


def timeline_data(status):
    rows = status['live_cycle_history']
    entered = status['cycle_count']
    change, reason = viewer_change(rows, entered)
    last = next((row for row in reversed(rows) if row['live_status']=='LIVE' and row['viewers']['result']=='VALUE'),None)
    last_observation = None
    if last is not None:
        last_observation = dict(cycle=last['cycle'],observed_at=last['observed_at'],
                                **{name:last['viewers'][name] for name in ('value','period','bucket_end_time')})
    return dict(entered_cycles=entered, retained_cycles=len(rows), retention_limit=HISTORY_LIMIT,
                latest_completed_cycle=rows[-1]['cycle'] if rows else None, cycles=rows,
                observed_viewer_change=change, viewer_change_reason=reason,
                last_live_viewer_observation=last_observation, source_update_time='UNKNOWN')


def timeline_markdown(timeline):
    if timeline is None:
        return []
    lines = ['', '### Page Live 逐轮观察', '',
             f"已进入 {timeline['entered_cycles']} 轮，保留最近 {timeline['retained_cycles']} 轮摘要，"
             f"最多保留 {timeline['retention_limit']} 轮。"
             f"最近走完本轮采集流程的轮次为 {timeline['latest_completed_cycle'] or '无'}。", '',
             '| 轮次 | 本地观察时间 | Live 状态 | 当轮顶层评论样本 | 评论样本比较 | 当前观看数返回 |',
             '|---:|---|---|---|---|---|']
    resume_source = None
    for row in timeline['cycles']:
        live = row['live_status'] or row['live_error'] or row['live_result']
        comments = f"{row['comment_count']} 条 / {row['comment_pages']} 页" if row['comment_pages'] else '未知'
        overlap = row.get('comment_sample_overlap')
        comparison = '无可比记录'
        if overlap is not None:
            comparison = (f"上轮样本 {overlap['previous_count']} 条，重合 {overlap['shared_count']} 条，"
                          f"本轮样本独有 {overlap['current_only_count']} 条")
        resumed = row.get('resumed_comment_sample_overlap')
        if resumed is not None:
            resume_source = resumed
            comparison = (f"恢复前样本 {resumed['previous_count']} 条，重合 {resumed['shared_count']} 条，"
                          f"本轮样本独有 {resumed['current_only_count']} 条")
        viewers = row['viewers']
        value = viewers['value'] if viewers['result'] == 'VALUE' else viewers['result']
        if viewers['result']=='VALUE' and row['live_status'] in LIVE_END_STATES:
            value = '直播结束后返回 ' + value
        lines.append(f"| {row['cycle']} | {row['observed_at']} | {live} | "
                     f"{row['comment_result']} / {comments} | {comparison} | {value} |")
    if resume_source is not None:
        lines += ['', f"恢复前评论样本来自运行 `{resume_source['from_run_id']}` 第 {resume_source['from_cycle']} 轮，"
                      f"本地观察时间 {resume_source['from_observed_at']}。"]
    change = timeline['observed_viewer_change']
    if change is not None:
        lines += ['', f"第 {change['from_cycle']} 至第 {change['to_cycle']} 轮均处于直播中，当前观看数返回值相差 {change['difference']:+d}。"]
    elif timeline['viewer_change_reason']=='LIVE_END_STATE_PRESENT':
        lines += ['', '其中一轮已观察到直播结束。保留平台返回值，跨结束状态的当前观看数差值留空。']
    else:
        lines += ['', f"当前观看数差值待核，原因 `{timeline['viewer_change_reason']}`。"]
    last = timeline['last_live_viewer_observation']
    if last is not None:
        lines.append(f"最近一条直播中取得的当前观看数为 {last['value']}，第 {last['cycle']} 轮，观察时间 {last['observed_at']}。")
    lines += ['', '平台更新时间未知。首屏样本可能受排名和过滤影响，恢复前后的对照无法验证中断期间的覆盖。']
    return lines
