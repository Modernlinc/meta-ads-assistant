"""Write workbook and text report from the same reconciled commerce snapshot."""
import csv
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from html import escape
from io import StringIO
from zipfile import ZIP_DEFLATED, ZipFile

from .journal import digest
from .meta_write_mapping import _write_new
from .monitor_tools import new_directory
from .validation import fail, object_only

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
REPORT_LABELS = {
    'orders': '订单数', 'purchased_lines': '订单明细数', 'refunded_lines': '退款明细数',
    'net_sales_minor': '净销售额', 'refunds_minor': '退款',
    'observed_spend_minor': '已观察广告消耗', 'known_costs_minor': '已知成本',
    'missing_cost_lines': '缺成本明细数', 'spend_missing_days': '缺消耗天数',
    'contribution_after_ads_minor': '扣广告后贡献', 'mer': '全店净额与广告消耗比',
}

FUNNEL_FIELDS = ('sessions', 'checkout_sessions', 'purchase_sessions')
FUNNEL_LABELS = ('会话数', '发起结账的会话', '购买完成的会话')


def attach_funnel_export(result, document, timezone):
    """Keep imported store funnel counts distinct from orders and Meta results."""
    from .local_paths import checked_path
    from .operator_review import pattern, ratio

    keys = ('schema','store_id','timezone','since','until','source','file','sha256')
    object_only(document,keys,keys)
    identity = (document['schema'],document['store_id'],document['timezone'],document['since'],document['until'])
    if identity != ('commerce-funnel-export-v1',result['store_id'],timezone,result['since'],result['until']):
        fail('COMMERCE_FUNNEL_EXPORT_SCOPE')
    if document['source'] not in ('SHOPIFY_ANALYTICS_EXPORT','GA4_STORE_EXPORT'):
        fail('COMMERCE_FUNNEL_EXPORT_SCOPE')
    path = checked_path(document['file'],link_error='COMMERCE_FUNNEL_EXPORT_SCOPE',
                        volume_error='WORKSPACE_VOLUME_NOT_MOUNTED')
    if not path.is_file():
        fail('COMMERCE_FUNNEL_EXPORT_SCOPE')
    with path.open('rb') as stream:
        raw = stream.read(120_001)
    fingerprint = sha256(raw).hexdigest()
    if len(raw)>120_000 or fingerprint != document['sha256']:
        fail('COMMERCE_FUNNEL_EXPORT_SCOPE')
    reader = csv.DictReader(StringIO(raw.decode('utf-8-sig')),strict=True)
    if tuple(reader.fieldnames or ()) != ('date',*FUNNEL_FIELDS):
        fail('COMMERCE_FUNNEL_EXPORT_FIELDS')
    start, stop = date.fromisoformat(result['since']),date.fromisoformat(result['until'])
    by_day = {}
    for entry in reader:
        if len(by_day)>=365 or None in entry or any(value is None for value in entry.values()):
            fail('COMMERCE_FUNNEL_EXPORT_FIELDS')
        day = date.fromisoformat(entry['date'])
        if not start<=day<=stop or day in by_day or day.isoformat()!=entry['date']:
            fail('COMMERCE_FUNNEL_EXPORT_SCOPE')
        row = {key:int(pattern(entry[key],r'[0-9]{1,10}')) for key in FUNNEL_FIELDS}
        if not row['purchase_sessions'] <= row['checkout_sessions'] <= row['sessions']:
            fail('COMMERCE_FUNNEL_EXPORT_NON_NESTED_SESSIONS')
        by_day[day] = row
    missing = [(start+timedelta(days=index)).isoformat()
               for index in range((stop-start).days+1) if start+timedelta(days=index) not in by_day]
    counts = {key:sum(row[key] for row in by_day.values()) if not missing else None for key in FUNNEL_FIELDS}
    detail = dict(source=document['source'],source_sha256=fingerprint,
                  coverage='PARTIAL' if missing else 'COMPLETE',missing_days=missing,
                  counts=counts,daily={day.isoformat():row for day,row in sorted(by_day.items())},
                  checkout_per_session=ratio(counts['checkout_sessions'],counts['sessions']),
                  purchase_per_checkout_session=ratio(counts['purchase_sessions'],counts['checkout_sessions']))
    return dict(result, store_funnel=detail)


def _column(value):
    label = ''
    while value:
        value, index = divmod(value - 1, 26)
        label = chr(ord('A') + index) + label
    return label


def _cell(value, col, row):
    key = f'{_column(col)}{row}'
    if value is None:
        return f'<c r="{key}"/>'
    if type(value) in (int, Decimal):
        return f'<c r="{key}"><v>{value}</v></c>'
    # Inline strings cannot turn customer text into formulas.
    return f'<c r="{key}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def _sheet(rows):
    return (f'<worksheet xmlns="{NS}"><sheetData>' + ''.join(
        f'<row r="{number}">' + ''.join(_cell(value, col, number) for col, value in enumerate(row, 1)) + '</row>'
        for number, row in enumerate(rows, 1)
    ) + '</sheetData></worksheet>')


def _write_workbook(path, sheets):
    with path.open('xb') as output, ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        types = [f'<Types xmlns="{CT}">',
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
                 '<Default Extension="xml" ContentType="application/xml"/>',
                 '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>']
        links = [f'<Relationships xmlns="{PKG}">']
        body = [f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>']
        for index, (title, values) in enumerate(sheets, 1):
            path = f'worksheets/sheet{index}.xml'
            links.append(f'<Relationship Id="rId{index}" Type="{REL}/worksheet" Target="{path}"/>')
            types.append(
                f'<Override PartName="/xl/{path}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
            body.append(f'<sheet name="{escape(title, quote=True)}" sheetId="{index}" r:id="rId{index}"/>')
            archive.writestr('xl/' + path, _sheet(values))
        archive.writestr('[Content_Types].xml', ''.join((*types, '</Types>')))
        archive.writestr('_rels/.rels', (f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" '
                                         f'Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'))
        archive.writestr('xl/_rels/workbook.xml.rels', ''.join((*links, '</Relationships>')))
        archive.writestr('xl/workbook.xml', ''.join((*body, '</sheets></workbook>')))


def export(result, output, *, review=None):
    if result['status'] != 'LOCAL_COMMERCE_RECONCILED':
        fail('COMMERCE_REPORT_RECONCILIATION_REQUIRED')
    totals = result['period_totals']
    keys = list(totals)
    window = f"{result['since']} 至 {result['until']}"
    caption = dict(币种=result['currency'], 日期=window, 退款归集=result['refund_basis'],
                   数据范围=result['source_scope'])
    cash = result.get('cash_refund_evidence')
    if cash is not None:
        caption.update(退款到账数据范围=cash['basis'], 已确认退款到账额=cash['successful_amount'],
                       退款到账覆盖=cash['state'])
    summary = [('范围', '值'), *caption.items(), ('指标（金额为最小货币单位）', '值')]
    summary.extend((REPORT_LABELS.get(key, key), totals[key]) for key in keys)
    links = dict(店铺来源=result.get('shopify_source'), 消耗快照摘要=result.get('native_ad_spend_snapshot_sha256'))
    costs = result.get('historical_cost_evidence')
    if costs is not None:
        links.update(历史成本版本=costs['version'], 历史成本摘要=costs['digest'], 匹配订单明细=costs['matched_lines'])
    funnel = result.get('store_funnel')
    if funnel is not None:
        links.update(漏斗数据源=funnel['source'], 漏斗摘要=funnel['source_sha256'], 漏斗覆盖=funnel['coverage'])
    if review is not None:
        links.update(广告任务=review['case'], 广告编号=review['ad_id'], 复盘摘要=review['sha256'])
        if review.get('next_review'):
            links['下一步'] = review['next_review']['next_step']
    sources = [('范围', '值'), *caption.items(), *((key, value) for key, value in links.items() if value is not None)]
    daily = [('日期', *(REPORT_LABELS.get(key, key) for key in keys))]
    daily.extend((day, *(values[key] for key in keys)) for day, values in result['daily'].items())
    root = new_directory(output)
    order_keys = ('orders', 'purchased_lines', 'refunded_lines', 'net_sales_minor', 'refunds_minor')
    ad_keys = ('observed_spend_minor', 'spend_missing_days', 'mer')
    order_sheet = [('日期', *(REPORT_LABELS[key] for key in order_keys))]
    ad_sheet = [('日期', *(REPORT_LABELS[key] for key in ad_keys))]
    for day, values in result['daily'].items():
        order_sheet.append((day, *(values[key] for key in order_keys)))
        ad_sheet.append((day, *(values[key] for key in ad_keys)))
    sheets = [('汇总',summary),('逐日',daily),('订单与退款',order_sheet),('广告消耗',ad_sheet),('证据与下一步',sources)]
    if funnel is not None:
        sheets.append(('店铺会话', [('日期',*FUNNEL_LABELS), *(
            (day,*(row[key] for key in FUNNEL_FIELDS)) for day,row in funnel['daily'].items())]))
    _write_workbook(root/'commerce-report.xlsx',sheets)
    lines = ['# 店铺与广告对账', '', f"{window}。币种 {result['currency']}。金额使用最小货币单位。",
             f"退款按 {result['refund_basis']} 归集，来源 {result['source_scope']}。", '',
             '| 指标 | 值 |', '| --- | --- |']
    for key in keys:
        value = totals[key]
        lines.append(f"| {REPORT_LABELS.get(key, key)} | {'未知' if value is None else value} |")
    lines += ['', '观察窗的经营数据和平台归因分开核对。缺成本或缺消耗时应补证，再比较贡献。']
    if cash is not None:
        value = cash['successful_amount'] if cash['successful_amount'] is not None else '未知'
        lines.append(f"退款到账按交易日期统计，来源范围 {cash['basis']}。已确认退款额 {value}（币种单位），覆盖状态 {cash['state']}。")
    if funnel is not None:
        lines.append(f"店铺会话来源 {funnel['source']}，覆盖状态 {funnel['coverage']}。")
        lines.extend(f"- {label} {funnel['counts'][key] if funnel['counts'][key] is not None else '未知'}"
                     for key,label in zip(FUNNEL_FIELDS,FUNNEL_LABELS,strict=True))
    if '下一步' in links:
        lines.extend(('', f"原广告任务 {review['case']} 的下一步为 `{links['下一步']}`。"))
    with (root / 'commerce-report.md').open('x', encoding='utf-8') as doc:
        doc.write('\n'.join(lines) + '\n')
    data = _write_new(root, 'COMMERCE-SNAPSHOT.json', result)
    receipt = dict(snapshot_sha256=digest(result), snapshot=data, spreadsheet='commerce-report.xlsx',
                   report='commerce-report.md', status='COMMERCE_REPORT_EXPORTED', actions_authorized=False)
    if review is not None:
        receipt['linked_review'] = review
    _write_new(root, 'RECEIPT.json', receipt)
    return receipt
