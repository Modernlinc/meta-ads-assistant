"""Offline reconciliation of saved delivery rows and a locally supplied export."""
import csv
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal, localcontext
from hashlib import sha256
import io
import re

from .analysis import AD_TOTAL_METRICS, WEBSITE_ATTRIBUTION
from .journal import digest
from .local_paths import checked_path
from .monitor import (ACCOUNT_CYCLE_PAGINATION, ACCOUNT_CYCLE_PAGINATION_UNVERIFIED, CONTROL_ERRORS, SAFE_ERRORS,
                      account_cycle_pagination_unverified, validate_relations, validate_source)
from .monitor_tools import validate_report_cycle_coverage, write_report
from .rate_limit import read_archived_hold
from .read_adapter import INSIGHTS_MAX_DAYS, PLACEMENT_DIMENSIONS, identifier, numeric_string, placement_dimension
from .review import ads_analysis, placement_analysis
from .timezones import load_zone
from .validation import InputError, aware_time, fail, object_only, strict_json


SCOPE_FIELDS = ('account_id','currency','timezone','date_start','date_stop','level','time_increment','breakdowns','clicks_kind')
FORMAT_FIELDS = ('delimiter','decimal_mark','group_separator','date_format')
DELIMITERS = {'COMMA':',','SEMICOLON':';','TAB':'\t'}
DATES = {'ISO':'%Y-%m-%d','YMD_SLASH':'%Y/%m/%d','DMY_SLASH':'%d/%m/%Y','MDY_SLASH':'%m/%d/%Y'}
CONVERSIONS = ('website_purchases','website_purchase_value','website_leads')
DELIVERY_COLUMNS = ('day',*AD_TOTAL_METRICS)
AD_DIMENSIONS = ('ad_id',)
OPTIONAL_COLUMNS = ('account_id','currency','date_stop',*CONVERSIONS)
SETTINGS_SCHEMA = 'ads-manager-export-settings-v1'
MAX_EXPORT_ROWS = 1000
RUN_RECEIPT_FIELD = 'api_run_receipt_sha256'
SNAPSHOT_FIELD = 'api_snapshot'
RUN_REPORT_FIELDS = dict(plan='query', phase='collector_phase', result='state', generation='generation',
                         pages='valid_pages', rows='rows', observed_at='observed_at',
                         last_successful_sync='last_successful_sync', last_error='last_error')


def _iso_date(value):
    if type(value) is not str or not re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}',value):
        fail('EXPORT_SCOPE_DATE_INVALID')
    try:
        return date.fromisoformat(value)
    except ValueError:
        fail('EXPORT_SCOPE_DATE_INVALID')


def _snapshot_reference(settings):
    if SNAPSHOT_FIELD not in settings:
        return None
    if RUN_RECEIPT_FIELD in settings: fail('EXPORT_API_SOURCE_BINDING_CONFLICT')
    reference = settings[SNAPSHOT_FIELD]
    if type(reference) is not dict: fail('MONITOR_REVIEW_SNAPSHOT_REFERENCE')
    return reference


def _validate_settings(data):
    """Validate the supplied settings and resolve any declared placement labels."""
    keys = ('schema','scope','format','columns')
    object_only(data,(*keys,'conversions','placement_labels',RUN_RECEIPT_FIELD,SNAPSHOT_FIELD),keys)
    if data['schema']!=SETTINGS_SCHEMA: fail('EXPORT_SETTINGS_SCHEMA')
    if RUN_RECEIPT_FIELD in data and not _is_digest(data[RUN_RECEIPT_FIELD]):
        fail('EXPORT_API_RUN_RECEIPT_INVALID')
    _snapshot_reference(data)
    scope = data['scope']; scope_keys = (*SCOPE_FIELDS,'observed_at')
    object_only(scope,scope_keys,scope_keys)
    identifier(scope['account_id'])
    if (type(scope['currency']) is not str or not re.fullmatch('[A-Z]{3}',scope['currency'])
            or type(scope['timezone']) is not str or not re.fullmatch('[A-Za-z0-9_+/-]{1,80}',scope['timezone'])
            or scope['level'] not in ('ad','adset','campaign','account')
            or type(scope['time_increment']) is not int or not 1<=scope['time_increment']<=31
            or scope['clicks_kind'] not in ('ALL_CLICKS','LINK_CLICKS','OTHER_OR_UNKNOWN')
            or type(scope['breakdowns']) is not list or len(scope['breakdowns'])>16
            or any(type(item) is not str or not re.fullmatch('[a-z][a-z0-9_]{0,63}',item) for item in scope['breakdowns'])):
        fail('EXPORT_SCOPE_INVALID')
    if _iso_date(scope['date_start'])>_iso_date(scope['date_stop']): fail('EXPORT_SCOPE_DATE_INVALID')
    aware_time(scope['observed_at'])
    fmt = data['format']
    object_only(fmt,FORMAT_FIELDS,FORMAT_FIELDS)
    if (fmt['delimiter'] not in tuple(DELIMITERS) or fmt['date_format'] not in tuple(DATES)
            or fmt['decimal_mark'] not in ('.',',') or fmt['group_separator'] not in (None,'.',',',' ')
            or fmt['group_separator']==fmt['decimal_mark']):
        fail('EXPORT_FORMAT_INVALID')
    columns = data['columns']
    object_only(columns,(*AD_DIMENSIONS,*PLACEMENT_DIMENSIONS,*DELIVERY_COLUMNS,*OPTIONAL_COLUMNS),DELIVERY_COLUMNS)
    dimensions = set(columns) & {*AD_DIMENSIONS,*PLACEMENT_DIMENSIONS}
    if dimensions not in (set(AD_DIMENSIONS),set(PLACEMENT_DIMENSIONS)):
        fail('EXPORT_GRAIN_COLUMNS_MISMATCH')
    if (any(type(label) is not str or not label.strip() or len(label)>160 for label in columns.values())
            or len({label.strip() for label in columns.values()})!=len(columns)):
        fail('EXPORT_COLUMN_MAP_INVALID')
    if 'conversions' in data:
        settings = data['conversions']; keys = ('attribution','action_report_time')
        object_only(settings,keys,keys)
        if (settings['attribution'] not in (WEBSITE_ATTRIBUTION,'UNIFIED_ADSET','OTHER_OR_UNKNOWN')
                or settings['action_report_time'] not in ('impression','conversion','mixed','UNKNOWN')):
            fail('EXPORT_CONVERSION_SCOPE_INVALID')
    if bool(set(CONVERSIONS)&columns.keys()) != ('conversions' in data): fail('EXPORT_CONVERSION_COLUMNS_AND_SCOPE_REQUIRED')
    if dimensions == set(PLACEMENT_DIMENSIONS) and 'conversions' in data:
        fail('EXPORT_PLACEMENT_CONVERSIONS_UNSUPPORTED')
    if 'placement_labels' in data:
        if dimensions != set(PLACEMENT_DIMENSIONS):
            fail('EXPORT_GRAIN_COLUMNS_MISMATCH')
        return _placement_labels(data['placement_labels'])
    return None


def _placement_labels(entries):
    if type(entries) is not list or not 1 <= len(entries) <= 150:
        fail('EXPORT_PLACEMENT_LABELS_INVALID')
    resolved = {}
    for item in entries:
        object_only(item,('export','api'),('export','api'))
        original, canonical = item['export'], item['api']
        if (type(original) is not list or len(original) != 2
                or any(type(label) is not str or len(label)>160 or not label.strip() for label in original)
                or type(canonical) is not list or len(canonical) != 2):
            fail('EXPORT_PLACEMENT_LABELS_INVALID')
        label_pair = tuple(label.strip() for label in original)
        if label_pair in resolved:
            fail('EXPORT_PLACEMENT_LABELS_DUPLICATE')
        resolved[label_pair] = tuple(placement_dimension(value) for value in canonical)
    return resolved


def _read_bytes(path,limit):
    path = checked_path(path,link_error='EXPORT_INPUT_LINK_REFUSED',volume_error='EXPORT_VOLUME_NOT_MOUNTED')
    if not path.is_file(): fail('EXPORT_INPUT_FILE_REQUIRED')
    with path.open('rb') as stream:
        data = stream.read(limit+1)
    if len(data)>limit: fail('EXPORT_INPUT_TOO_LARGE')
    return data


def _is_digest(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def _run_member_entry(files, name, limit, *, optional=False):
    entries = [item for item in files if type(item) is dict and type(item.get('path')) is str
               and item['path'].replace('\\','/')==name]
    if not entries and optional:
        return None
    if len(entries)!=1: fail('EXPORT_API_RUN_FILE_UNLISTED_OR_AMBIGUOUS')
    entry = entries[0]
    if not _is_digest(entry.get('sha256')) or type(entry.get('bytes')) is not int or not 0<entry['bytes']<=limit:
        fail('EXPORT_API_RUN_RECEIPT_INVALID')
    return entry


def _verified_run_member(directory, files, name, limit, *, optional=False):
    entry = _run_member_entry(files,name,limit,optional=optional)
    if entry is None:
        return None, None
    raw = _read_bytes(directory/name,limit)
    if len(raw)!=entry['bytes'] or sha256(raw).hexdigest()!=entry['sha256']:
        fail('EXPORT_API_RUN_FILE_HASH_MISMATCH')
    return strict_json(_decode_text(raw),max_chars=limit), entry['sha256']


def _run_report_binding(directory, files, result, *, workspace_identity=None):
    stored, stored_hash = _verified_run_member(directory,files,'report/RECEIPT.json',32000,optional=True)
    if workspace_identity is not None and (
            type(stored) is not dict or stored.get('workspace_identity_sha256')!=workspace_identity
            or 'report' not in result):
        fail('PERIOD_REVIEW_ARCHIVE_WORKSPACE_BINDING_INVALID')
    if 'report' not in result and stored_hash is None:
        return 'OUTER_RECEIPT_ONLY'
    proof = result.get('report',stored)
    if (type(proof) is not dict or proof.get('status')!='BOUNDED_OBSERVATION_REPORT_WRITTEN'
            or proof.get('mode')!='READ_ONLY' or proof.get('live_action_authorized') is not False
            or type(proof.get('files')) is not list or len(proof['files'])!=2
            or stored_hash is not None and proof!=stored):
        fail('EXPORT_API_RUN_REPORT_RECEIPT_MISMATCH')
    for name in ('REPORT.json','REPORT.md'):
        inner = _run_member_entry(proof['files'],name,8_000_000)
        outer = _run_member_entry(files,'report/'+name,8_000_000,optional=name=='REPORT.md')
        if outer is not None and (inner['bytes'],inner['sha256'])!=(outer['bytes'],outer['sha256']):
            fail('EXPORT_API_RUN_REPORT_RECEIPT_MISMATCH')
    return 'RUN_REPORT_RECEIPT_MATCHED'


def _run_scope_binding(directory, files, sources, report, result, *, workspace_insights=False):
    plan, plan_hash = _verified_run_member(directory,files,'PLAN.json',128000,optional=True)
    consent, consent_hash = _verified_run_member(directory,files,'CONSENT.json',4000,optional=True)
    if plan_hash is None and consent_hash is None:
        if workspace_insights: fail('PERIOD_REVIEW_ARCHIVE_WORKSPACE_PLAN_REQUIRED')
        return 'NOT_RECORDED'
    from .monitor_runner import recorded_monitor_intent, validate_monitor_intent
    try:
        intent = recorded_monitor_intent(plan,consent)
        plans = validate_monitor_intent(intent)
    except (InputError, KeyError, TypeError):
        fail('EXPORT_API_RUN_SCOPE_INVALID')
    if workspace_insights and (intent['schema']!='workspace-monitor-intent-v1' or plans.keys()!=sources.keys()):
        fail('PERIOD_REVIEW_ARCHIVE_WORKSPACE_PLAN_REQUIRED')
    if ('workspace_identity_sha256' in consent
            and (not _is_digest(consent['workspace_identity_sha256'])
                 or consent['workspace_identity_sha256']!=report.get('workspace_identity_sha256'))):
        fail('PERIOD_REVIEW_ARCHIVE_WORKSPACE_BINDING_INVALID')
    if (plan.get('source_requests')!={key: request.request() for key,request in plans.items()}
            or any(channel not in plans or asdict(plans[channel])!=row['plan'] for channel,row in sources.items())):
        fail('EXPORT_API_RUN_QUERY_SCOPE_MISMATCH')
    cycles = report.get('collection_cycles')
    if cycles is not None and cycles > intent['cycles']:
        fail('EXPORT_API_RUN_CYCLE_SCOPE_MISMATCH')
    page_limit = intent['pages_per_source']
    # A legacy multi-round query may hold cumulative pages. It is already
    # ineligible for totals when the pagination basis is missing.
    current_pages = cycles==1 or report.get('account_cycle_pagination')==ACCOUNT_CYCLE_PAGINATION
    for channel,row in sources.items():
        if channel=='account':
            pages_exceeded = row['pages']>1
            max_rows = row['pages']
        else:
            pages_exceeded = current_pages and row['pages']>page_limit
            max_rows = row['pages']*plans[channel].limit
        if pages_exceeded or len(row['rows'])>max_rows:
            fail('EXPORT_API_RUN_PAGE_SCOPE_MISMATCH')
    _validate_run_request_budget(plan,intent,report,result,sources)
    return 'PLAN_CONSENT_QUERY_MATCHED'


def _validate_run_request_budget(plan, intent, report, result, sources):
    total_limit = intent['request_budget']
    startup_limit = 2  # Account runs reserve Debug Token and the named account probe.
    collector_limit = total_limit-startup_limit
    preview_counts = (plan.get('initial_get_attempts'),plan.get('collector_attempt_limit'),plan.get('total_get_attempt_limit'))
    initial, collector, total = (result.get(name) for name in ('initial_get_attempts_reserved',
                              'collector_attempts_reserved','total_get_attempts_reserved'))
    reported_attempts, reported_budget = report.get('request_attempts_reserved'),report.get('request_budget')
    error = 'EXPORT_API_RUN_REQUEST_BUDGET_MISMATCH'
    rate_limit = read_archived_hold(report,result,collector,error)
    retries = 0 if rate_limit is None else rate_limit['automatic_retries_reserved']
    pending = 'uncommitted_request' in (report.get('account_cycle_coverage') or {})
    minimum_collector = sum(row['pages'] for row in sources.values())+pending+retries
    cycles = report.get('collection_cycles')
    # Each round allows one account GET plus bounded page requests.
    maximum_for_rounds = (cycles*(1+(len(intent['sources'])-1)*intent['pages_per_source']) + retries
                          if cycles is not None else collector_limit)
    if (any(type(count) is not int for count in (*preview_counts,initial,collector,total,reported_attempts,reported_budget))
            or preview_counts!=(startup_limit,collector_limit,total_limit)
            or reported_budget!=collector_limit or reported_attempts!=collector
            or not 0<=initial<=startup_limit or collector and (initial!=startup_limit or cycles==0)
            or not minimum_collector<=collector<=min(collector_limit,maximum_for_rounds)
            or total!=initial+collector or result.get('request_count_semantics')!='RESERVED_NOT_PROOF_OF_TRANSMISSION'):
        fail(error)


def read_run_observations(path, *, workspace_insights=False):
    path = checked_path(path,link_error='EXPORT_INPUT_LINK_REFUSED',volume_error='EXPORT_VOLUME_NOT_MOUNTED')
    run_id = path.parent.name
    if path.name!='RECEIPT.json' or not re.fullmatch('[0-9a-f]{32}',run_id):
        fail('EXPORT_API_RUN_RECEIPT_REQUIRED')
    raw = _read_bytes(path,64000)
    receipt = strict_json(_decode_text(raw))
    if (type(receipt) is not dict or receipt.get('authorization_reusable') is not False
            or receipt.get('status') not in ('MONITOR_RUN_ENDED','MONITOR_REVIEW_REQUIRED','MONITOR_RUN_FAILED','INTERRUPTED')
            or type(receipt.get('files')) is not list or not 2<=len(receipt['files'])<=64):
        fail('EXPORT_API_RUN_RECEIPT_INVALID')
    result,_ = _verified_run_member(path.parent,receipt['files'],'RESULT.json',128000)
    report, report_sha = _verified_run_member(path.parent,receipt['files'],'report/REPORT.json',8_000_000)
    if (type(report) is not dict or report.get('schema')!='bounded-monitor-report-v1' or report.get('mode')!='READ_ONLY'
            or type(report.get('observations')) is not dict or type(result) is not dict
            or result.get('remote_write_authorized') is not False or result.get('status')!=receipt['status']
            or result.get('run_id',run_id)!=run_id or result.get('monitor_phase')!=report.get('monitor_phase')):
        fail('EXPORT_API_RUN_REPORT_INVALID')
    sources = {}
    channels = ('account','ads','ad_links') if workspace_insights else ('account','ads','placements')
    for channel in channels:
        observed = report['observations'].get(channel,{'state':'NOT_CONFIGURED'})
        if type(observed) is not dict: fail('EXPORT_API_RUN_REPORT_INVALID')
        if observed.get('state')=='NOT_CONFIGURED': continue
        if not set(RUN_REPORT_FIELDS.values()) <= observed.keys(): fail('EXPORT_API_RUN_REPORT_INVALID')
        row = {key:observed[value] for key,value in RUN_REPORT_FIELDS.items()}
        try:
            validate_source(channel,row)
        except (KeyError,TypeError):
            fail('EXPORT_API_RUN_REPORT_INVALID')
        if row['result'] in ('COMPLETE','EMPTY') and (not row['pages'] or row['observed_at'] is None):
            fail('EXPORT_API_RUN_REPORT_INVALID')
        sources[channel] = row
    identity = None
    if workspace_insights:
        if not {'account','ads'} <= sources.keys():
            fail('PERIOD_REVIEW_ARCHIVE_INSIGHTS_REQUIRED')
        configured = {key for key,row in report['observations'].items()
                      if type(row) is dict and row.get('state')!='NOT_CONFIGURED'}
        if (configured!=sources.keys() or type(result.get('source_results')) is not dict
                or result['source_results'].keys()!=sources.keys() or result.get('run_id')!=run_id):
            fail('EXPORT_API_RUN_REPORT_INVALID')
        identity = report.get('workspace_identity_sha256')
        if identity is None:
            fail('PERIOD_REVIEW_ARCHIVE_WORKSPACE_BINDING_REQUIRED')
        if not _is_digest(identity) or identity!=result.get('workspace_identity_sha256'):
            fail('PERIOD_REVIEW_ARCHIVE_WORKSPACE_BINDING_INVALID')
    elif 'account' not in sources or len(sources)!=2:
        fail('EXPORT_API_RUN_ADS_OBSERVATIONS_REQUIRED')
    validate_relations(sources)
    report_binding = _run_report_binding(path.parent,receipt['files'],result,workspace_identity=identity)
    if 'source_results' in result and (type(result['source_results']) is not dict
            or any(result['source_results'].get(channel)!=row['result'] for channel,row in sources.items())):
        fail('EXPORT_API_RUN_REPORT_INVALID')
    coverage = report.get('account_cycle_coverage')
    pagination = report.get('account_cycle_pagination')
    cycles = report.get('collection_cycles')
    if coverage != result.get('account_cycle_coverage') or pagination != result.get('account_cycle_pagination'):
        fail('EXPORT_API_RUN_REPORT_INVALID')
    observations = dict(mode=report['mode'],sources=sources)
    if 'collection_cycles' in report:
        if type(cycles) is not int or not 0<=cycles<=1_000_000:
            fail('EXPORT_API_RUN_REPORT_INVALID')
        observations['cycle_count'] = cycles
    if coverage is not None:
        if (type(coverage) is not dict or type(coverage.get('cycle')) is not int or coverage['cycle'] < 0
                or coverage['cycle']!=cycles
                or pagination not in (None,'UNRECORDED',ACCOUNT_CYCLE_PAGINATION)):
            fail('EXPORT_API_RUN_REPORT_INVALID')
        validate_report_cycle_coverage(report['observations'],coverage,error='EXPORT_API_RUN_CYCLE_COVERAGE_INVALID')
        observations.update(account_cycle_coverage=coverage,account_cycle_pagination=pagination)
    elif pagination is not None:
        fail('EXPORT_API_RUN_REPORT_INVALID')
    if workspace_insights:
        if coverage is None: fail('PERIOD_REVIEW_ARCHIVE_CYCLE_EVIDENCE_REQUIRED')
        phase, monitor_error = report.get('monitor_phase'), report.get('monitor_error')
        if (phase not in ('STOPPED','PAUSED','INTERRUPTED') or monitor_error!=result.get('monitor_last_error')
                or (monitor_error is not None and type(monitor_error) is not str) or monitor_error not in CONTROL_ERRORS):
            fail('EXPORT_API_RUN_REPORT_INVALID')
        observations.update(workspace_identity_sha256=identity,phase=phase,last_error=monitor_error)
    scope_binding = _run_scope_binding(path.parent,receipt['files'],sources,report,result,workspace_insights=workspace_insights)
    if workspace_insights:
        from .monitor_runner import ERRORS
        error = result.get('error_code')
        if error is not None:
            if type(error) is not str or error not in ERRORS|SAFE_ERRORS|CONTROL_ERRORS|{'USER_INTERRUPT'}:
                fail('EXPORT_API_RUN_REPORT_INVALID')
            observations['recorded_run_error_code'] = error
        if report.get('rate_limit') is not None:
            observations['rate_limit'] = report['rate_limit']
    api_run = dict(run_id=run_id, receipt_sha256=sha256(raw).hexdigest(), report_sha256=report_sha,
                   status=receipt['status'], report_binding=report_binding, query_scope_binding=scope_binding)
    return observations, api_run


def _decode_text(data):
    try:
        encoding = 'utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'
        text = data.decode(encoding)
    except UnicodeError:
        fail('EXPORT_ENCODING_INVALID')
    if '\x00' in text: fail('EXPORT_ENCODING_INVALID')
    return text


def _number(value,fmt,integer):
    value = value.strip().replace('\u00a0',' ').replace('\u202f',' ')
    if value in ('','-','–','—'): return None
    whole,mark,fraction = value.partition(fmt['decimal_mark'])
    group = fmt['group_separator']
    if group is not None and group in whole:
        if not re.fullmatch(r'[0-9]{1,3}(?:'+re.escape(group)+r'[0-9]{3})+',whole):
            fail('EXPORT_NUMBER_INVALID')
        whole = whole.replace(group,'')
    candidate = whole+'.'+fraction if mark else whole
    try:
        return numeric_string(candidate,integer=integer)
    except InputError:
        fail('EXPORT_NUMBER_INVALID')


def _day(value,pattern):
    try:
        return datetime.strptime(value.strip(),pattern).date().isoformat()
    except ValueError:
        fail('EXPORT_DAILY_DATE_INVALID')


def _export_rows(raw,settings,dimensions,labels):
    scope,fmt = settings['scope'],settings['format']
    if not set(dimensions) <= settings['columns'].keys():
        fail('EXPORT_GRAIN_COLUMNS_MISMATCH')
    stream = csv.reader(io.StringIO(_decode_text(raw),newline=''),delimiter=DELIMITERS[fmt['delimiter']],strict=True)
    try:
        header = [column.strip() for column in next(stream,[])]
        if not header or len(header)>250 or len(set(header))!=len(header): fail('EXPORT_HEADER_INVALID')
        positions = {value:column for column,value in enumerate(header)}
        if any(name.strip() not in positions for name in settings['columns'].values()): fail('EXPORT_MAPPED_COLUMN_MISSING')
        columns = {field:positions[name.strip()] for field,name in settings['columns'].items()}
        rows = {}
        for row in stream:
            if not any(cell.strip() for cell in row): continue
            if len(row)!=len(header): fail('EXPORT_ROW_WIDTH')
            if len(rows)>=MAX_EXPORT_ROWS: fail('EXPORT_ROW_LIMIT')
            entry = {field:row[index].strip() for field,index in columns.items()}
            day = _day(entry['day'],DATES[fmt['date_format']])
            if ('date_stop' in entry and _day(entry['date_stop'],DATES[fmt['date_format']])!=day
                    or not scope['date_start']<=day<=scope['date_stop']):
                fail('EXPORT_ROW_DATE_SCOPE')
            if ('account_id' in entry and entry['account_id'].removeprefix('act_')!=scope['account_id']
                    or 'currency' in entry and entry['currency']!=scope['currency']):
                fail('EXPORT_ROW_ACCOUNT_OR_CURRENCY_MISMATCH')
            if dimensions == AD_DIMENSIONS:
                bucket = (identifier(entry['ad_id']),)
            else:
                pair = tuple(entry[name] for name in dimensions)
                if labels is None:
                    try:
                        bucket = tuple(placement_dimension(value) for value in pair)
                    except InputError:
                        fail('EXPORT_PLACEMENT_LABEL_UNMAPPED')
                else:
                    bucket = labels.get(pair)
                    if bucket is None: fail('EXPORT_PLACEMENT_LABEL_UNMAPPED')
            key = (day,*bucket)
            if key in rows:
                fail('EXPORT_DUPLICATE_AD_DAY' if dimensions==AD_DIMENSIONS else 'EXPORT_DUPLICATE_PLACEMENT_DAY')
            rows[key] = {metric:_number(entry[metric],fmt,metric in ('impressions','clicks'))
                         for metric in (*AD_TOTAL_METRICS,*CONVERSIONS) if metric in entry}
        return rows
    except csv.Error:
        fail('EXPORT_CSV_INVALID')


def _expected_scope(context):
    return {key:context[key] for key in SCOPE_FIELDS if key!='clicks_kind'} | {'clicks_kind':'ALL_CLICKS'}


def _api_delivery(status):
    sources = status['sources']
    channel,dimensions = ('placements',PLACEMENT_DIMENSIONS) if 'placements' in sources else ('ads',AD_DIMENSIONS)
    if account_cycle_pagination_unverified(status):
        return dict(state='NOT_COMPUTED',reason=ACCOUNT_CYCLE_PAGINATION_UNVERIFIED),channel,dimensions
    if 'placements' not in sources:
        return ads_analysis(sources),channel,dimensions
    report = placement_analysis(sources)
    if report['coverage'] == 'NOT_OBSERVED':
        return dict(state='NOT_COMPUTED',reason=report['reason'],evidence=report['evidence']),'placements',PLACEMENT_DIMENSIONS
    context = {name:report[name] for name in ('account_id','currency','timezone','level','time_increment','breakdowns')}
    context.update(zip(('date_start','date_stop'),report['date_range']))
    context['api_version'] = sources['placements']['plan']['version']
    coverage = 'COMPLETE' if report['coverage']=='BOUNDED_COMPLETE' else 'PARTIAL'
    return dict(state='COMPUTED',context=context,evidence=report['evidence'],
                analysis={'coverage':coverage},action_reporting=None),'placements',PLACEMENT_DIMENSIONS


def prepare_reconciliation(monitor,directory,*,run_receipt=None):
    api_run = api_snapshot = None
    if run_receipt is None:
        status, api_snapshot = monitor.review_snapshot()
    else:
        status, api_run = read_run_observations(run_receipt)
    ads,_,dimensions = _api_delivery(status)
    if ads['state']!='COMPUTED': fail(ads['reason'])
    template = dict(schema=SETTINGS_SCHEMA,scope={key:None for key in (*SCOPE_FIELDS,'observed_at')},
                    format=dict(delimiter='COMMA',decimal_mark='.',group_separator=None,date_format='ISO'),
                    columns={column:None for column in (*dimensions,*DELIVERY_COLUMNS)})
    report = dict(mode=status['mode'],expected_scope=_expected_scope(ads['context']),
                  api_evidence=ads['evidence'],api_action_reporting=ads['action_reporting'],settings_to_fill=template)
    if api_run is not None:
        template[RUN_RECEIPT_FIELD] = api_run['receipt_sha256']
        report['api_run'] = api_run
    else:
        template[SNAPSHOT_FIELD] = report[SNAPSHOT_FIELD] = api_snapshot
    grain = '账户级逐日版位' if dimensions==PLACEMENT_DIMENSIONS else '广告级逐日'
    columns = '平台、版位' if dimensions==PLACEMENT_DIMENSIONS else '广告 ID'
    lines = ['# 准备广告后台对账','',
             '`PREPARATION.json` 列出本次 API 期望口径。另存 `settings_to_fill` 为 JSON，按实际后台导出填写。',
             f'提供{grain}明细 CSV，选择{columns}、日期、花费、展示和全部点击。',
             '填写实际账户、币种、时区、起止日期、导出时间和列名。表头语言可自行映射。']
    if api_run is not None:
        lines.append(f"归档报告核对 `{api_run['report_binding']}`，计划范围核对 `{api_run['query_scope_binding']}`。")
    else:
        lines.append(f"本次准备选中快照 `{api_snapshot['snapshot']}`。填写设置时保留 `api_snapshot`。")
    if dimensions==PLACEMENT_DIMENSIONS:
        lines.append('如果平台和版位使用后台显示名称，在设置中提供对应 API 编码的 `placement_labels`。')
    return write_report(directory,'PREPARATION',report,lines,'ADS_RECONCILIATION_PREPARED')


def _cell(api,export):
    if api is None or export is None:
        return dict(api=api,export=export,delta=None,state='MISSING_VALUE')
    delta = Decimal(export)-Decimal(api)
    return dict(api=api,export=export,delta=format(delta,'f'),state='EQUAL' if delta==0 else 'DIFFERENT')


def _date_coverage(start_day, end_day, api_dates, export_dates):
    if (end_day-start_day).days >= INSIGHTS_MAX_DAYS:
        fail('EXPORT_SCOPE_DATE_INVALID')
    presence = {(True, True): 'BOTH', (True, False): 'API_ONLY',
                (False, True): 'EXPORT_ONLY', (False, False): 'NEITHER'}
    days = (date.fromordinal(day).isoformat() for day in range(start_day.toordinal(),end_day.toordinal()+1))
    by_day = {day:presence[day in api_dates,day in export_dates] for day in days}
    complete = all(state=='BOTH' for state in by_day.values())
    return dict(state='BOTH_SIDES_HAVE_ROWS_EACH_DAY' if complete else 'MISSING_DATES_ON_AT_LEAST_ONE_SIDE',
                by_day=by_day)


def build_reconciliation(status,settings,raw,*,api_run=None,api_snapshot=None):
    labels = _validate_settings(settings)
    if api_run is not None:
        if settings.get(RUN_RECEIPT_FIELD)!=api_run['receipt_sha256']: fail('EXPORT_API_RUN_RECEIPT_MISMATCH')
    elif RUN_RECEIPT_FIELD in settings:
        fail('EXPORT_API_RUN_RECEIPT_REQUIRED')
    if SNAPSHOT_FIELD in settings and settings[SNAPSHOT_FIELD]!=api_snapshot:
        fail('EXPORT_API_SNAPSHOT_MISMATCH')
    scope = settings['scope']
    sources = status.get('sources',{})
    ads,channel,dimensions = _api_delivery(status)
    placement = dimensions==PLACEMENT_DIMENSIONS
    schema = 'ads-manager-placement-reconciliation-v1' if placement else 'ads-manager-delivery-reconciliation-v1'
    report = dict(schema=schema,mode=status['mode'],state='NOT_COMPARABLE',reason=None,
                  api_evidence=ads.get('evidence'),export_sha256=sha256(raw).hexdigest(),export_settings_sha256=digest(settings),
                  export_settings_basis='OWNER_DECLARED',
                  export_observed_at=scope['observed_at'],context=ads.get('context'),scope_differences=[],rows=[],
                  conversion_diagnostic=None,real_connection_verified=False,remote_write_authorized=False)
    if api_run is not None:
        report['api_run'] = api_run
    elif api_snapshot is not None:
        report[SNAPSHOT_FIELD] = api_snapshot
        report['api_observation_binding'] = ('PREPARED_STATE_SNAPSHOT' if SNAPSHOT_FIELD in settings else 'LATEST_STATE_UNBOUND')
    if placement:
        report['export_placement_labels_basis'] = 'OWNER_DECLARED' if 'placement_labels' in settings else 'CANONICAL_CODES'

    def blocked(reason): return dict(report,reason=reason)

    if ads['state']!='COMPUTED': return blocked(ads['reason'])
    context = ads['context']
    observed_scope = scope|{'breakdowns':sorted(scope['breakdowns'])} if placement else scope
    expected_scope = _expected_scope(context)
    if placement:
        expected_scope['breakdowns'] = sorted(expected_scope['breakdowns'])
    differences = [key for key,value in expected_scope.items() if value!=observed_scope[key]]
    if differences:
        report['scope_differences'] = differences
        return blocked('EXPORT_SCOPE_MISMATCH')
    if ads['analysis']['coverage']!='COMPLETE': return blocked('COMPLETE_INSIGHTS_REQUIRED')
    exported = _export_rows(raw,settings,dimensions,labels)
    api = {(row['date_start'],*(row[name] for name in dimensions)):row for row in sources[channel]['rows']}
    start_day, end_day = (_iso_date(context[name]) for name in ('date_start','date_stop'))
    api_dates = {key[0] for key in api}
    export_dates = {key[0] for key in exported}
    report.update(date_coverage=_date_coverage(start_day,end_day,api_dates,export_dates),
                  observed_days=len(api_dates | export_dates),declared_days=(end_day-start_day).days+1)
    if not api or not exported:
        return dict(report,reason='TWO_NONEMPTY_SOURCES_REQUIRED',api_rows=len(api),export_rows=len(exported))
    missing_rows = different = incomplete = 0
    with localcontext() as arithmetic:
        arithmetic.prec = 64
        for key in sorted(api.keys() | exported.keys()):
            left,right = api.get(key),exported.get(key)
            presence = 'BOTH' if left is not None and right is not None else 'API_ONLY' if left is not None else 'EXPORT_ONLY'
            fields = {name:_cell((left or {}).get(name),(right or {}).get(name)) for name in AD_TOTAL_METRICS}
            missing_rows += presence!='BOTH'
            different += any(item['state']=='DIFFERENT' for item in fields.values())
            incomplete += presence=='BOTH' and any(item['state']=='MISSING_VALUE' for item in fields.values())
            report['rows'].append(dict(day=key[0],**dict(zip(dimensions,key[1:])),presence=presence,delivery=fields))
        if 'conversions' in settings:
            totals = {}
            for metric in (name for name in CONVERSIONS if name in settings['columns']):
                values = [row[metric] for row in exported.values()]
                totals[metric] = format(sum(map(Decimal,values)),'f') if None not in values else None
            api_metrics = ads['analysis']['metrics']
            report['conversion_diagnostic'] = dict(state='EFFECTIVE_API_ACTION_TIME_UNVERIFIED',
                api_requested_attribution=context['attribution'],api_requested_action_report_time=context['action_report_time'],
                export_declared=settings['conversions'],export_totals=totals,
                api_observed_totals={metric:api_metrics.get(metric,{}).get('value') for metric in totals})
    zone = load_zone(context['timezone'])
    api_at = ads['evidence']['insights']['observed_at']
    closed = api_at is not None and all(aware_time(stamp).astimezone(zone).date()>end_day for stamp in (api_at,scope['observed_at']))
    state = 'DIFFERENCES' if missing_rows or different else 'INCOMPLETE_FIELDS' if incomplete else 'MATCHED_OBSERVED_DELIVERY'
    reason = None if closed else 'ACCOUNT_DATE_WINDOW_NOT_CLOSED_AT_OBSERVATION'
    return dict(report,state=state if closed else 'NEEDS_REVIEW',reason=reason,api_rows=len(api),export_rows=len(exported),
                missing_rows=missing_rows,differing_rows=different,incomplete_rows=incomplete)


def markdown(report):
    placement = report['schema']=='ads-manager-placement-reconciliation-v1'
    headline = '广告后台版位逐日对账' if placement else '广告后台逐日报表对账'
    lines = [f'# {headline}','',f"交付指标结果为 `{report['state']}`。"]
    if 'api_run' in report:
        run = report['api_run']
        lines += [f"API 报告来自归档运行 `{run['run_id']}`，运行状态 `{run['status']}`。",
                  f"归档报告核对 `{run['report_binding']}`，计划范围核对 `{run['query_scope_binding']}`。"]
    elif SNAPSHOT_FIELD in report:
        snapshot = report[SNAPSHOT_FIELD]
        lines.append(f"API 观察快照 `{snapshot['snapshot']}`，对账依据 `{report['api_observation_binding']}`。")
        if report['api_observation_binding']=='LATEST_STATE_UNBOUND':
            lines.append('本次使用读取时的最新状态。需要固定采集批次时，按对账准备生成的设置或运行回执核对。')
    if report['reason']:
        lines.append(f"复查原因 `{report['reason']}`。")
    if report['scope_differences']:
        lines.append('先核对导出的口径字段 '+ '、'.join(report['scope_differences'])+'。')
    if 'date_coverage' in report:
        by_day = report['date_coverage']['by_day']
        both_days = sum(state=='BOTH' for state in by_day.values())
        lines.append(f"声明 {report['declared_days']} 天，至少一侧有行 {report['observed_days']} 天，两侧均有行 {both_days} 天。")
        for state, label in (('NEITHER','两侧均无记录'),('API_ONLY','仅 API 有记录'),('EXPORT_ONLY','仅后台有记录')):
            days = [day for day, presence in by_day.items() if presence==state]
            if days:
                lines.append(f"{label}：{'、'.join(days)}。")
        if both_days < report['declared_days']:
            lines.append('缺日期留待核对当天交付和后台筛选条件。')
    if report['rows']:
        lines += [f"API {report['api_rows']} 行，导出 {report['export_rows']} 行。单侧缺行 {report['missing_rows']}，"
                  f"有数值差异的行 {report['differing_rows']}，双方有行但缺值 {report['incomplete_rows']}。",
                  'JSON 逐日列出 API 值、导出值和导出减 API 的差额。空值和未出现的行保留待核。']
        differences = [(row,name,cell) for row in report['rows'] for name,cell in row['delivery'].items()
                       if cell['state']!='EQUAL']
        if differences:
            labels = ('平台','版位') if placement else ('广告 ID',)
            dimensions = PLACEMENT_DIMENSIONS if placement else AD_DIMENSIONS
            lines += ['', '| 日期 | '+' | '.join(labels)+' | 行覆盖 | 指标 | API | 后台 | 差额 |',
                      '|---|'+'---|'*len(labels)+'---|---|---:|---:|---:|']
            for row,name,cell in differences[:30]:
                values = [cell[key] if cell[key] is not None else '未知' for key in ('api','export','delta')]
                bucket = tuple(row[key] for key in dimensions)
                lines.append('| '+' | '.join((row['day'],*bucket,row['presence'],name,*values))+' |')
            if len(differences)>30:
                lines.append(f'共 {len(differences)} 项数值差异或缺值，其余见 JSON。')
    if report['conversion_diagnostic'] is not None:
        diagnostic = report['conversion_diagnostic']
        lines += ['', '网站转化按两侧观察值并列。API 实际动作归日仍待取证。', '',
                  '| 指标 | API 返回分桶 | 后台导出值 |', '|---|---:|---:|']
        for name,value in diagnostic['export_totals'].items():
            api = diagnostic['api_observed_totals'][name]
            lines.append(f"| {name} | {api if api is not None else '未知'} | {value if value is not None else '未知'} |")
    return lines+['', '导出设置由提供方填写。两侧各自的观察时间见 JSON，用于复查数据回补。']


def reconcile(monitor,export_path,settings_path,directory,*,run_receipt=None):
    settings = strict_json(_decode_text(_read_bytes(settings_path,64000)))
    if type(settings) is not dict: fail('EXPECTED_OBJECT')
    api_run = api_snapshot = None
    snapshot_reference = _snapshot_reference(settings)
    if snapshot_reference is not None and run_receipt is not None:
        fail('EXPORT_API_SNAPSHOT_REQUIRES_STATE_DIR')
    if run_receipt is None:
        status, api_snapshot = monitor.review_snapshot(snapshot_reference)
    else:
        status, api_run = read_run_observations(run_receipt)
    export = _read_bytes(export_path,2_000_000)
    report = build_reconciliation(status,settings,export,api_run=api_run,api_snapshot=api_snapshot)
    evidence = {key:report[key] for key in ('api_observation_binding', SNAPSHOT_FIELD) if key in report}
    return write_report(directory,'RECONCILIATION',report,markdown(report),'ADS_RECONCILIATION_WRITTEN',
                        reconciliation_state=report['state'],reason=report['reason'],
                        date_coverage_state=report.get('date_coverage',{}).get('state','NOT_CHECKED'),**evidence)
