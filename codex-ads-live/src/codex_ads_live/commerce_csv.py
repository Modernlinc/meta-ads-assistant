"""Scoped, deduplicated reconciliation of local commerce and ad exports."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import csv
import re

from .local_paths import checked_path
from .operator_review import minor, pattern, ratio
from .validation import fail


ORDER_KEYS = ("store_id", "order_id", "line_id", "date", "currency", "gross_minor", "discount_minor",
              "product_cost_minor", "fulfillment_minor", "payment_minor")
REFUND_KEYS = ("store_id", "refund_id", "order_id", "line_id", "date", "currency", "refund_minor")
SPEND_KEYS = ("account_id", "date", "currency", "spend_minor")
ID = r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}"


def local_file(path):
    file = checked_path(path, link_error="COMMERCE_UNSAFE_LINK", volume_error="COMMERCE_VOLUME_NOT_MOUNTED")
    if not file.is_file() or not 0 < file.stat().st_size <= 8 * 1024 * 1024:
        fail("COMMERCE_CSV_FILE_LIMIT")
    return file


def day(value):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        fail("COMMERCE_DATE_INVALID")
    if parsed.isoformat() != value:
        fail("COMMERCE_DATE_INVALID")
    return value


def amount(text, *, optional=False):
    if optional and not text:
        return None
    if not re.fullmatch(r"[0-9]{1,13}", text):
        fail("COMMERCE_AMOUNT_FORMAT")
    return minor(int(text))


def load_csv(path, columns, keys, scope_key, scope, currency):
    records = {}
    duplicate = 0
    with local_file(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream)
        if rows.fieldnames != list(columns):
            fail("COMMERCE_CSV_SCHEMA")
        try:
            for index, row in enumerate(rows, 1):
                if index > 50_000 or None in row or None in row.values():
                    fail("COMMERCE_CSV_ROW_LIMIT_OR_SHAPE")
                if row[scope_key] != scope or row["currency"] != currency:
                    fail("COMMERCE_SCOPE_OR_CURRENCY_MISMATCH")
                for key in keys:
                    if key != "date":
                        pattern(row[key], ID)
                day(row["date"])
                identity = tuple(row[k] for k in keys)
                if identity in records:
                    if row != records[identity]:
                        fail("COMMERCE_DUPLICATE_CONFLICT")
                    duplicate += 1
                else:
                    records[identity] = row
        except csv.Error:
            fail("COMMERCE_CSV_SCHEMA")
    return records, duplicate


@dataclass
class Totals:
    sales: int = 0
    refunds: int = 0
    spend: int = 0
    costs: int = 0
    lines_missing_cost: int = 0
    missing_spend_days: int = 1
    orders: set = field(default_factory=set)
    purchased_lines: int = 0
    refunded_lines: int = 0

    def as_report(self):
        net = self.sales - self.refunds
        complete = self.lines_missing_cost == 0 and self.missing_spend_days == 0
        return dict(
            net_sales_minor=net, refunds_minor=self.refunds, observed_spend_minor=self.spend,
            known_costs_minor=self.costs, orders=len(self.orders), purchased_lines=self.purchased_lines,
            refunded_lines=self.refunded_lines, missing_cost_lines=self.lines_missing_cost,
            spend_missing_days=self.missing_spend_days,
            contribution_after_ads_minor=net - self.costs - self.spend if complete else None,
            mer=ratio(net, self.spend) if self.missing_spend_days == 0 else None,
        )


def reconcile(orders, refunds, spend, store_id, account_id, currency, since, until, basis):
    pattern(store_id, ID)
    pattern(account_id, r"[1-9][0-9]{0,39}")
    pattern(currency, r"[A-Z]{3}")
    since, until = day(since), day(until)
    start, end = map(date.fromisoformat, (since, until))
    span = (end - start).days
    if not 0 <= span <= 366 or basis not in ("event", "order"):
        fail("COMMERCE_PERIOD_OR_BASIS")
    orders, od = load_csv(orders, ORDER_KEYS, ("order_id", "line_id"), "store_id", store_id, currency)
    refunds, rd = load_csv(refunds, REFUND_KEYS, ("refund_id", "order_id", "line_id"), "store_id", store_id, currency)
    spend, sd = load_csv(spend, SPEND_KEYS, ("date",), "account_id", account_id, currency)
    return reconcile_rows(orders, refunds, spend, store_id, account_id, currency, since, until, basis,
                          duplicates=dict(orders=od, refunds=rd, spend=sd))


def reconcile_rows(orders, refunds, spend, store_id, account_id, currency, since, until, basis, *, duplicates):
    span = (date.fromisoformat(until) - date.fromisoformat(since)).days
    totals = Totals(missing_spend_days=span + 1)
    days = defaultdict(Totals)
    prior_sales = {}
    for identity, row in orders.items():
        gross, discount = amount(row["gross_minor"]), amount(row["discount_minor"])
        if gross < discount:
            fail("COMMERCE_DISCOUNT_EXCEEDS_GROSS")
        recent = since <= row["date"] <= until
        prior_sales[identity] = gross - discount
        costs = [amount(row[k], optional=True) for k in ("product_cost_minor", "fulfillment_minor", "payment_minor")]
        if recent:
            for bucket in (totals, days[row["date"]]):
                bucket.sales += gross - discount
                bucket.costs += sum(value for value in costs if value is not None)
                bucket.lines_missing_cost += None in costs
                bucket.orders.add(row["order_id"])
                bucket.purchased_lines += 1
    checked_refunds = defaultdict(int)
    for row in refunds.values():
        identity = (row["order_id"], row["line_id"])
        parent = orders.get(identity)
        if parent is None:
            fail("COMMERCE_REFUND_ORDER_UNMATCHED")
        if row["date"] < parent["date"]:
            fail("COMMERCE_REFUND_PRECEDES_ORDER")
        money = amount(row["refund_minor"])
        checked_refunds[identity] += money
        if checked_refunds[identity] > prior_sales[identity]:
            fail("COMMERCE_REFUND_EXCEEDS_NET_SALE")
        target_day = parent["date"] if basis == "order" else row["date"]
        if since <= target_day <= until:
            for bucket in (totals, days[target_day]):
                bucket.refunds += money
                bucket.refunded_lines += 1
    for row in spend.values():
        paid = amount(row["spend_minor"])
        if since <= row["date"] <= until:
            for bucket in (totals, days[row["date"]]):
                bucket.spend += paid
                bucket.missing_spend_days -= 1
    return dict(
        status="LOCAL_COMMERCE_RECONCILED", store_id=store_id, account_id=account_id,
        since=since, until=until, currency=currency, refund_basis=basis,
        period_totals=totals.as_report(), daily={k: v.as_report() for k, v in sorted(days.items())},
        duplicate_rows=duplicates,
        source_scope="USER_SUPPLIED_EXPORTS", platform_attribution="NOT_INFERRED_FROM_ORDER_EXPORT",
        live_shopify_status="DEFERRED_NOT_RUN", actions_authorized=False,
    )
