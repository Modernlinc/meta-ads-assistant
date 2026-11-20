"""Account-day delivery grouped by the two returned placement dimensions."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from .analysis import delivery_totals
from .read_adapter import PLACEMENT_DIMENSIONS


def placement_delivery(rows, complete):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in PLACEMENT_DIMENSIONS)].append(row)
    coverage = 'COMPLETE' if complete else 'PARTIAL'
    with localcontext() as arithmetic:
        arithmetic.prec = 64
        arithmetic.rounding = ROUND_HALF_EVEN
        totals = delivery_totals(rows, coverage)
        account_spend = totals['spend']['value']
        denominator = Decimal(account_spend) if account_spend is not None else None
        placements = []
        for key, group in sorted(groups.items()):
            metrics = delivery_totals(group, coverage)
            spend = metrics['spend']['value']
            share = format(Decimal(spend) / denominator * 100, '.4f') if denominator and spend is not None else None
            placements.append(dict(zip(PLACEMENT_DIMENSIONS, key), observed_days=len(group),
                                   metrics=metrics, observed_spend_share_percent=share))
    return totals, placements


def placement_markdown(review):
    report = review['placements']
    since, until = report['date_range']
    lines = ['', '## 广告版位交付复盘', '',
             f"账户 `{report['account_id']}`，{since} 至 {until}，币种 {report['currency'] or '未知'}，"
             f"账户时区 {report['timezone'] or '未知'}。版位数据覆盖 `{report['coverage']}`。",
             '占比使用本次完整查询中已返回且花费字段齐备的分桶。查询不含购买、询盘或归因数据。']
    if report['reason']:
        lines.append(f"待核原因 `{report['reason']}`。")
    if report['totals'] is not None:
        lines.extend(['', '| 平台 | 版位 | 观察天数 | 花费 | 展示 | 点击 | 已观察花费占比 |',
                      '|---|---|---:|---:|---:|---:|---:|'])
        for row in report['placements']:
            metrics = row['metrics']
            share = row['observed_spend_share_percent']
            values = [metric_cell(metrics[key]) for key in ('spend','impressions','clicks')]
            cells = [row['publisher_platform'], row['platform_position'], str(row['observed_days']), *values,
                     share + '%' if share is not None else '未知']
            lines.append('| ' + ' | '.join(cells) + ' |')
        totals = [metric_cell(report['totals'][key]) for key in ('spend','impressions','clicks')]
        lines.append('| 本次查询合计 | | | ' + ' | '.join(totals) + ' | |')
    return lines


def metric_cell(metric):
    if metric['value'] is not None:
        return metric['value']
    if metric['known_subtotal'] is not None:
        return '已知小计 ' + metric['known_subtotal']
    return '未知'
