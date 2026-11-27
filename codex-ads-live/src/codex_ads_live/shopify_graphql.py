"""Pinned Shopify read operations and a transport-independent response collector."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import re
from zoneinfo import ZoneInfoNotFoundError

from .operator_review import pattern
from .shopify_readiness import preview
from .timezones import load_zone
from .validation import aware_time, fail, object_only


VERSION = "2026-10"
CSV_FALLBACK = "commerce-reconcile"
SCOPES = ("schema", "store", "since", "until", "checked_on", "reported_scopes")
INVENTORY_SCOPES = {"read_products", "read_inventory"}
MONEY = "{ shopMoney { amount currencyCode } }"
REFUND_ROWS = f"{{ nodes {{ id quantity lineItem {{ id }} subtotalSet {MONEY} }} pageInfo {{ hasNextPage endCursor }} }}"
SHOP = "shop { id myshopifyDomain }"
PAGE = "pageInfo { hasNextPage endCursor }"
MAX_ATTEMPTS = 80
REFUND_LIMIT = 250


def gid(value, kind):
    return pattern(value, rf"gid://shopify/{kind}/[1-9][0-9]{{0,39}}")


def mapping(value):
    if type(value) is not dict:
        fail("SHOPIFY_RESPONSE_OBJECT")
    return value


def rows(value, maximum):
    if type(value) is not list or len(value) > maximum:
        fail("SHOPIFY_RESPONSE_ROW_LIMIT_OR_TYPE")
    return value


def numeric(value):
    if type(value) is not int or not 0 <= value <= 10**6:
        fail("SHOPIFY_QUANTITY_INVALID")
    return value


def money(value, currency, *, bag=True):
    if bag:
        value = mapping(value).get("shopMoney")
    data = mapping(value)
    if data.get("currencyCode") != currency:
        fail("SHOPIFY_MONEY_CURRENCY_MISMATCH")
    return Decimal(pattern(data.get("amount"), r"[0-9]{1,14}(?:\.[0-9]{1,6})?"))


def observe(parent, key, errors, path, decode):
    status = next((code for prefix, code in errors if prefix and path[:len(prefix)] == prefix), None)
    if status is not None:
        return {"state": status, "value": None}
    if key not in parent:
        return {"state": "FIELD_ABSENT", "value": None}
    if parent[key] is None:
        return {"state": "NULL", "value": None}
    return {"state": "PRESENT", "value": decode(parent[key])}


def errors_from(body):
    output = []
    for error in rows(body.get("errors", []), 100):
        error = mapping(error)
        ext = error.get("extensions") or {}
        raw_path = error.get("path") or []
        path = tuple(rows(raw_path, 30))
        if any(type(p) not in (str, int) for p in path):
            fail("SHOPIFY_ERROR_PATH_INVALID")
        code = mapping(ext).get("code")
        state = {"ACCESS_DENIED": "UNAUTHORIZED", "FORBIDDEN": "UNAUTHORIZED",
                 "REDACTED": "REDACTED", "THROTTLED": "THROTTLED"}.get(
                     code if type(code) is str else None, "GRAPHQL_ERROR")
        output.append((path, state))
    return output


def query(operation, inventory, *, refund_transactions=False):
    line_fields = f"id quantity originalTotalSet {MONEY} discountAllocations {{ allocatedAmountSet {MONEY} }}"
    if inventory:
        line_fields += " variant { inventoryQuantity inventoryItem { id tracked unitCost { amount currencyCode } } }"
    lines = f"{{ nodes {{ {line_fields} }} {PAGE} }}"
    transaction_rows = f"{{ nodes {{ id kind status processedAt gateway amountSet {MONEY} }} {PAGE} }}"
    refund_selection = f"transactions(first: 30) {transaction_rows} " if refund_transactions else ""
    if operation == "CrossWindowRefunds":
        return (f"query CrossWindowRefunds($filter: String!, $after: String) {{ {SHOP} "
                f"orders(first: 10, after: $after, sortKey: UPDATED_AT, query: $filter) "
                f"{{ {PAGE} nodes {{ id createdAt updatedAt refunds {{ id "
                f"{refund_selection} }} }} }} }}")
    if operation == "Orders":
        return (f"query Orders($filter: String!, $after: String) {{ {SHOP} "
                f"orders(first: 10, after: $after, sortKey: CREATED_AT, query: $filter) "
                f"{{ {PAGE} nodes {{ id createdAt displayFulfillmentStatus "
                f"lineItems(first: 30) {lines} refunds {{ id processedAt totalRefundedSet {MONEY} "
                f"{refund_selection}refundLineItems(first: 30) {REFUND_ROWS} }} }} }} }}")
    node_type, connection, content = {
        "OrderLines": ("Order", "lineItems", lines),
        "RefundLines": ("Refund", "refundLineItems", REFUND_ROWS),
        "RefundTransactions": ("Refund", "transactions", transaction_rows),
    }[operation]
    return (f"query {operation}($id: ID!, $after: String) {{ {SHOP} "
            f"node(id: $id) {{ ... on {node_type} {{ id {connection}(first: 30, after: $after) {content} }} }} }}")


def deduplicate(target, identifier, row):
    prior = target.setdefault(identifier, row)
    if prior != row:
        fail("SHOPIFY_DUPLICATE_ID_CONFLICT")


class Collector:
    def __init__(self, document, exchange, *, retry_wait=None, refund_transactions=False, cross_window_refunds=False):
        self.shop = document["store"]
        self.shop_id = gid(document["shop_id"], "Shop")
        self.currency = pattern(document["currency"], r"[A-Z]{3}")
        try:
            self.zone = load_zone(document["timezone"])
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
            fail("SHOPIFY_TIMEZONE_INVALID")
        self.first = date.fromisoformat(document["since"])
        self.last = date.fromisoformat(document["until"])
        if self.first.year < 1970 or self.last == date.max:
            fail("SHOPIFY_ORDER_WINDOW_INVALID")
        self.inventory_wanted = document["inventory"]
        if type(self.inventory_wanted) is not bool:
            fail("SHOPIFY_INVENTORY_OPTION_INVALID")
        self.inventory = self.inventory_wanted and INVENTORY_SCOPES.issubset(document["reported_scopes"])
        self.exchange = exchange
        self.retry_wait = retry_wait
        self.refund_transactions = refund_transactions
        self.cross_window_refunds = cross_window_refunds
        self.cash_scan = dict(requested=cross_window_refunds, scanned_orders=0, gaps=[])
        self.transaction_rows = {}
        self.transaction_coverage = True
        self.attempts = []
        self.gaps = set()
        self.orders = {}
        self.lines = {}
        self.refunds = {}
        self.refund_lines = {}
        self.identity_seen = False

    def packet(self, operation, *, after=None, object_id=None):
        def midnight(day):
            return datetime.combine(day, time(), self.zone).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        variables = {"after": after}
        if operation == "Orders":
            variables["filter"] = f"created_at:>='{midnight(self.first)}' created_at:<'{midnight(self.last + timedelta(days=1))}'"
        elif operation == "CrossWindowRefunds":
            variables["filter"] = f"created_at:<'{midnight(self.first)}' updated_at:>='{midnight(self.first)}'"
        else:
            variables["id"] = object_id
        return {"operation": operation, "shop": self.shop, "method": "POST",
                "path": f"/admin/api/{VERSION}/graphql.json", "query": query(operation, self.inventory, refund_transactions=self.refund_transactions),
                "variables": variables}

    def request(self, operation, *, after=None, object_id=None):
        packet = self.packet(operation, after=after, object_id=object_id)
        for retry in range(3):
            if len(self.attempts) >= MAX_ATTEMPTS:
                self.gaps.add("REQUEST_LIMIT")
                return None, []
            try:
                status, body = self.exchange(packet)
            except (ConnectionError, TimeoutError, OSError):
                status, body = "INTERRUPTED", None
            if status == "INTERRUPTED":
                state = "NETWORK_INTERRUPTED"
            elif status == 429:
                state = "HTTP_THROTTLED"
            elif type(status) is int and 500 <= status <= 599:
                state = "HTTP_TEMPORARY"
            elif status in (401, 403):
                self.attempts.append("UNAUTHORIZED")
                self.gaps.add("UNAUTHORIZED")
                return None, []
            elif status == 200:
                body = mapping(body)
                errors = errors_from(body)
                state = "GRAPHQL_THROTTLED" if (
                    body.get("data") is None and any(c == "THROTTLED" for _, c in errors)
                ) else None
                if state is None:
                    self.attempts.append("RESPONSE")
                    if body.get("data") is None:
                        self.gaps.update(code for _, code in errors)
                        self.gaps.add("NO_GRAPHQL_DATA")
                        return None, errors
                    data = mapping(body["data"])
                    shop = mapping(data.get("shop"))
                    if shop.get("id") != self.shop_id or shop.get("myshopifyDomain") != self.shop:
                        fail("SHOPIFY_STORE_MISMATCH")
                    self.identity_seen = True
                    return data, errors
            else:
                self.attempts.append("HTTP_REJECTED")
                self.gaps.add("HTTP_REJECTED")
                return None, []
            self.attempts.append(state)
            if retry < 2 and self.retry_wait is not None:
                self.retry_wait()
        self.gaps.add(state)
        return None, []

    def page(self, connection, size):
        connection = mapping(connection)
        nodes = rows(connection.get("nodes"), size)
        info = mapping(connection.get("pageInfo"))
        more = info.get("hasNextPage")
        if type(more) is not bool:
            fail("SHOPIFY_PAGE_INFO_INVALID")
        cursor = info.get("endCursor") if more else None
        if more and (type(cursor) is not str or not re.fullmatch(r"[A-Za-z0-9_+.=/:-]{1,512}", cursor)):
            self.gaps.add("CURSOR_MISSING_OR_INVALID")
        return nodes, cursor if more and isinstance(cursor, str) and re.fullmatch(r"[A-Za-z0-9_+.=/:-]{1,512}", cursor) else None

    def child_pages(self, node, connection, kind, operation, errors, path):
        parent = gid(node.get("id"), kind)
        current = node.get(connection)
        if current is None:
            self.gaps.add("CHILD_CONNECTION_UNAVAILABLE")
            return
        seen = set()
        while True:
            children, after = self.page(current, 30)
            for index, child in enumerate(children):
                yield mapping(child), errors, (*path, connection, "nodes", index)
            if after is None:
                if current["pageInfo"]["hasNextPage"]:
                    self.gaps.add("CHILD_COLLECTION_INCOMPLETE")
                break
            if after in seen:
                self.gaps.add("CURSOR_REPEATED")
                self.gaps.add("CHILD_COLLECTION_INCOMPLETE")
                break
            seen.add(after)
            data, errors = self.request(operation, after=after, object_id=parent)
            if data is None:
                self.gaps.add("CHILD_COLLECTION_INCOMPLETE")
                break
            returned = mapping(data.get("node"))
            if returned.get("id") != parent:
                fail("SHOPIFY_CHILD_IDENTITY_MISMATCH")
            current = returned.get(connection)
            if current is None:
                self.gaps.add("CHILD_CONNECTION_UNAVAILABLE")
                break
            path = ("node",)

    def stock(self, line, errors, path):
        if not self.inventory:
            return {"state": "UNAUTHORIZED" if self.inventory_wanted else "NOT_REQUESTED", "value": None}
        variant_path = (*path, "variant")
        item_path = (*variant_path, "inventoryItem")
        variant = observe(line, "variant", errors, variant_path, mapping)
        if variant["state"] != "PRESENT":
            return variant
        item = observe(variant["value"], "inventoryItem", errors, item_path, mapping)
        if item["state"] != "PRESENT":
            return item
        item = item["value"]
        def strict_bool(value):
            if type(value) is not bool:
                fail("SHOPIFY_INVENTORY_TRACKED_INVALID")
            return value
        def inventory_quantity(value):
            if type(value) is not int or not -10**9 <= value <= 10**9:
                fail("SHOPIFY_INVENTORY_QUANTITY_INVALID")
            return value
        tracked = observe(item, "tracked", errors, (*item_path, "tracked"), strict_bool)
        available = observe(variant["value"], "inventoryQuantity", errors, (*variant_path, "inventoryQuantity"),
                            inventory_quantity)
        cost = observe(item, "unitCost", errors, (*item_path, "unitCost"),
                       lambda value: str(money(value, self.currency, bag=False)))
        return dict(state="PRESENT", inventory_id=gid(item.get("id"), "InventoryItem"), tracked=tracked,
                    variant_quantity=available,
                    unit_cost=cost, cost_basis="CURRENT_INVENTORY_ITEM_ONLY")

    def refund_payments(self, refund, refund_id, errors, path):
        if refund.get("transactions") is None:
            self.transaction_coverage = False
            return
        for item, _, _ in self.child_pages(refund, "transactions", "Refund", "RefundTransactions", errors, path):
            identifier = gid(item.get("id"), "OrderTransaction")
            status = item.get("status")
            kind = item.get("kind")
            if type(status) is not str or type(kind) is not str:
                self.transaction_coverage = False
                continue
            stamp = item.get("processedAt")
            if stamp is None and status == "SUCCESS":
                self.transaction_coverage = False
                continue
            processed_on = aware_time(stamp).astimezone(self.zone).date().isoformat() if stamp else None
            gateway = item.get("gateway")
            if type(gateway) is not str or not re.fullmatch(r"[A-Za-z0-9_ .-]{1,80}", gateway):
                gateway = "UNKNOWN"
            deduplicate(self.transaction_rows, identifier, dict(
                transaction_id=identifier, refund_id=refund_id, status=pattern(status, r"[A-Z_]{1,40}"),
                kind=pattern(kind, r"[A-Z_]{1,40}"), gateway=gateway, date=processed_on,
                amount=str(money(item.get("amountSet"), self.currency)) if item.get("amountSet") is not None else None))
            if status == "SUCCESS" and self.transaction_rows[identifier]["amount"] is None:
                self.transaction_coverage = False

    def order(self, item, errors, path):
        identity = gid(item.get("id"), "Order")
        day = aware_time(item.get("createdAt")).astimezone(self.zone).date()
        if not self.first <= day <= self.last:
            fail("SHOPIFY_ORDER_OUTSIDE_WINDOW")
        fulfillment = observe(item, "displayFulfillmentStatus", errors, (*path, "displayFulfillmentStatus"),
                              lambda v: pattern(v, r"[A-Z_]{1,40}"))
        deduplicate(self.orders, identity, dict(order_id=identity, date=day.isoformat(), fulfillment=fulfillment))
        for line, line_errors, line_path in self.child_pages(item, "lineItems", "Order", "OrderLines", errors, path):
            line_id = gid(line.get("id"), "LineItem")
            original = money(line.get("originalTotalSet"), self.currency)
            discounts = sum((money(mapping(row).get("allocatedAmountSet"), self.currency)
                             for row in rows(line.get("discountAllocations"), 300)), Decimal(0))
            if discounts > original:
                fail("SHOPIFY_DISCOUNT_EXCEEDS_LINE")
            deduplicate(self.lines, line_id, dict(order_id=identity, line_id=line_id, date=day.isoformat(),
                        quantity=numeric(line.get("quantity")), gross=str(original),
                        discounts=str(discounts), net=str(original - discounts),
                        inventory=self.stock(line, line_errors, line_path)))
        if item.get("refunds") is None:
            self.gaps.add("REFUNDS_UNAVAILABLE")
            return
        for index, refund in enumerate(rows(item["refunds"], REFUND_LIMIT)):
            refund = mapping(refund)
            refund_id = gid(refund.get("id"), "Refund")
            stamp = aware_time(refund.get("processedAt")).astimezone(self.zone).date()
            if stamp > self.last:
                continue
            if stamp < day:
                fail("SHOPIFY_REFUND_PRECEDES_ORDER")
            total = money(refund.get("totalRefundedSet"), self.currency)
            record = dict(refund_id=refund_id, order_id=identity, date=stamp.isoformat(), all_in=str(total))
            deduplicate(self.refunds, refund_id, record)
            if self.refund_transactions:
                self.refund_payments(refund, refund_id, errors, (*path, "refunds", index))
            for child, _, _ in self.child_pages(refund, "refundLineItems", "Refund", "RefundLines", errors,
                                               (*path, "refunds", index)):
                if child.get("id") is None:
                    self.gaps.add("REFUND_LINE_ID_UNAVAILABLE")
                    continue
                cid = gid(child["id"], "RefundLineItem")
                lid = gid(mapping(child.get("lineItem")).get("id"), "LineItem")
                if lid not in self.lines or self.lines[lid]["order_id"] != identity:
                    if self.gaps.intersection({"CHILD_CONNECTION_UNAVAILABLE", "CHILD_COLLECTION_INCOMPLETE",
                                               "REQUEST_LIMIT"}):
                        self.gaps.add("REFUND_LINE_PARENT_UNAVAILABLE")
                        continue
                    fail("SHOPIFY_REFUND_LINE_MISMATCH")
                amount = money(child.get("subtotalSet"), self.currency)
                deduplicate(self.refund_lines, cid, dict(order_id=identity, refund_id=refund_id,
                            refund_line_id=cid, line_id=lid, amount=str(amount),
                            quantity=numeric(child.get("quantity"))))

    def cash_from_earlier_orders(self):
        after, seen = None, set()
        for _ in range(12):
            data, errors = self.request('CrossWindowRefunds', after=after)
            if data is None or data.get('orders') is None:
                self.gaps.update(code for _, code in errors)
                self.gaps.add('CROSS_WINDOW_ORDERS_UNAVAILABLE')
                break
            nodes, after = self.page(data['orders'], 10)
            self.cash_scan['scanned_orders'] += len(nodes)
            for index, node in enumerate(nodes):
                order = mapping(node)
                gid(order.get('id'), 'Order')
                created = aware_time(order.get('createdAt')).astimezone(self.zone).date()
                updated = aware_time(order.get('updatedAt')).astimezone(self.zone).date()
                if created >= self.first or updated < self.first:
                    fail('SHOPIFY_CROSS_WINDOW_ORDER_SCOPE')
                if order.get('refunds') is None:
                    self.gaps.add('CROSS_WINDOW_REFUNDS_UNAVAILABLE')
                    continue
                for refund_index, raw in enumerate(rows(order['refunds'], REFUND_LIMIT)):
                    refund = mapping(raw)
                    ident = gid(refund.get('id'), 'Refund')
                    self.refund_payments(refund, ident, errors, ('orders', 'nodes', index, 'refunds', refund_index))
            if self.gaps or after is None:
                break
            if after in seen:
                self.gaps.add('CURSOR_REPEATED')
                break
            seen.add(after)
        else:
            self.gaps.add('CROSS_WINDOW_PAGE_BUDGET')
        self.cash_scan['gaps'] = sorted(self.gaps)

    def run(self, *, resume=None, page_committed=None):
        tables = ("orders", "lines", "refunds", "refund_lines", "transaction_rows")
        seen = set(resume["seen"]) if resume else set()
        after = resume["after"] if resume else None
        if resume:
            for table in tables:
                setattr(self, table, resume[table].copy())
            self.identity_seen = True
            self.transaction_coverage = resume["transaction_coverage"]
        while True:
            data, errors = self.request("Orders", after=after)
            if data is None:
                break
            if data.get("orders") is None:
                self.gaps.update(code for _, code in errors)
                self.gaps.add("ORDERS_UNAVAILABLE")
                break
            nodes, after = self.page(data["orders"], 10)
            for index, node in enumerate(nodes):
                self.order(mapping(node), errors, ("orders", "nodes", index))
            if after is None:
                break
            if after in seen:
                self.gaps.add("CURSOR_REPEATED")
                break
            seen.add(after)
            if self.gaps:
                # Resume from the preceding complete parent page. Incomplete
                # nested pages do not advance the durable cursor.
                if page_committed is not None:
                    break
            elif page_committed is not None:
                page_committed(dict(after=after, seen=sorted(seen),
                    transaction_coverage=self.transaction_coverage, **{key: getattr(self, key) for key in tables}))
        if self.gaps or not self.identity_seen:
            totals = None
        else:
            deductions = sum((Decimal(row["amount"]) for row in self.refund_lines.values()), Decimal(0))
            income = sum((Decimal(row["net"]) for row in self.lines.values()), Decimal(0))
            totals = {"merchandise_after_line_refunds": str(income - deductions),
                      "line_refunds": str(deductions), "historical_product_cost": None,
                      "basis": "ORDER_CREATED_WINDOW_REFUNDS_PROCESSED_THROUGH_END"}
        if self.cross_window_refunds and not self.gaps and self.identity_seen:
            self.cash_from_earlier_orders()
        result = dict(status="SHOPIFY_READ_COMPLETE" if totals is not None and not self.gaps else
                    "SHOPIFY_READ_PARTIAL" if self.identity_seen else "SHOPIFY_READ_UNAVAILABLE",
                    api_version=VERSION, date_basis="SHOP_TIMEZONE", store=self.shop, shop_id=self.shop_id,
                    since=self.first.isoformat(), until=self.last.isoformat(), currency=self.currency,
                    orders=list(self.orders.values()), lines=list(self.lines.values()),
                    refund_lines=list(self.refund_lines.values()), refunds=list(self.refunds.values()),
                    profile_identity_matched=self.identity_seen, totals=totals, gaps=sorted(self.gaps),
                    attempts=self.attempts, fallback=CSV_FALLBACK)
        if self.refund_transactions:
            complete = self.transaction_coverage and not self.gaps and self.identity_seen
            succeeded = [
                row for row in self.transaction_rows.values()
                if row["status"] == "SUCCESS" and row["kind"] == "REFUND" and row["date"]
                and self.first.isoformat() <= row["date"] <= self.last.isoformat() and row["amount"] is not None
            ]
            result["refund_transaction_evidence"] = dict(
                state="COMPLETE" if complete else "PARTIAL",
                basis=("SUCCESSFUL_REFUND_TRANSACTIONS_IN_ORDER_COHORT_AND_EARLIER_UPDATED_ORDERS"
                       if self.cross_window_refunds else "SUCCESSFUL_REFUND_TRANSACTIONS_FOR_ORDER_COHORT_BY_PROCESSED_DATE"),
                scan=self.cash_scan,
                successful_amount=str(sum((Decimal(row["amount"]) for row in succeeded), Decimal(0))) if complete else None,
                successful_ids=[row["transaction_id"] for row in succeeded],
                transactions=list(self.transaction_rows.values()))
        return result


def request_plan(document):
    fields = (*SCOPES, "shop_id", "timezone", "currency", "inventory")
    object_only(document, fields, fields)
    if document["schema"] != "shopify-read-plan-v1":
        fail("SHOPIFY_PLAN_SCHEMA")
    checked = preview({key: document[key] for key in SCOPES} | {"schema": "shopify-order-readiness-v1"})
    collector = Collector(document, None)
    return dict(status="SHOPIFY_GRAPHQL_READ_PLAN", api_version=VERSION, **{
                    key: checked[key] for key in ("store", "requested_window", "required_scopes",
                                                   "missing_reported_scopes", "protected_customer_data_access")
                }, order_packet=collector.packet("Orders") if not checked["missing_reported_scopes"] else None,
                child_operations=["OrderLines", "RefundLines"], attempts_max=MAX_ATTEMPTS,
                optional_inventory_scopes=sorted(INVENTORY_SCOPES) if collector.inventory_wanted else [],
                inventory_fields_selected=collector.inventory, fallback=CSV_FALLBACK,
                live_shopify_status="DEFERRED_NOT_RUN", scope_verified_by_platform=False, network_used=False,
                sources=["https://shopify.dev/docs/api/usage/versioning",
                         "https://shopify.dev/docs/api/usage/access-scopes",
                         "https://shopify.dev/docs/api/admin-graphql/2026-10/queries/orders"])


def review(document):
    extra = ("shop_id", "timezone", "currency", "inventory", "exchanges")
    object_only(document, (*SCOPES, *extra), (*SCOPES, *extra))
    if document["schema"] != "shopify-read-review-v1":
        fail("SHOPIFY_REVIEW_SCHEMA")
    declared = preview({**{key: document[key] for key in SCOPES}, "schema": "shopify-order-readiness-v1"})
    transcript = iter(rows(document["exchanges"], MAX_ATTEMPTS))

    def stub(request):
        entry = next(transcript, None)
        if entry is None:
            return "INTERRUPTED", None
        object_only(entry, ("operation", "after", "object_id", "status", "body"), ("operation", "after", "status"))
        variables = request["variables"]
        if (entry["operation"], entry["after"], entry.get("object_id")) != (
                request["operation"], variables["after"], variables.get("id")):
            fail("SHOPIFY_EXCHANGE_SCOPE_MISMATCH")
        return entry["status"], entry.get("body")

    outcome = {"status": "SHOPIFY_REVIEW_SCOPE_REQUIRED", "fallback": CSV_FALLBACK}
    if not declared["missing_reported_scopes"]:
        outcome = Collector(document, stub).run()
        if next(transcript, None) is not None:
            fail("SHOPIFY_UNUSED_EXCHANGE")
    return dict(outcome, api_version=VERSION, required_order_scopes=declared["required_scopes"],
                missing_order_scopes=declared["missing_reported_scopes"],
                inventory_scopes=sorted(INVENTORY_SCOPES) if document["inventory"] is True else [],
                network_used=False, source="LOCAL_RESPONSE_TRANSCRIPT", live_shopify_status="DEFERRED_NOT_RUN")


def _historical_line_costs(filename, shop_id, currency, orders):
    from .journal import digest
    from .operator_review import load_input

    doc = load_input(filename)
    keys = ('schema', 'shop_id', 'currency', 'version', 'observed_at', 'rows')
    object_only(doc, keys, keys)
    if (doc['schema'], doc['shop_id'], doc['currency']) != ('shopify-line-costs-v1', shop_id, currency):
        fail('SHOPIFY_HISTORICAL_COST_SCOPE')
    pattern(doc['version'], r'[A-Za-z0-9_.-]{1,80}')
    aware_time(doc['observed_at'])
    seen = {}
    cost_fields = ('product_cost_minor', 'fulfillment_minor', 'payment_minor')
    for row in rows(doc['rows'], 3000):
        fields = ('order_id', 'line_id', 'applies_on', 'source_ref', *cost_fields)
        object_only(row, fields, fields)
        identity = (gid(row['order_id'], 'Order').rsplit('/',1)[-1],
                    gid(row['line_id'], 'LineItem').rsplit('/',1)[-1])
        if identity not in orders or row['applies_on'] != orders[identity]['date']:
            fail('SHOPIFY_HISTORICAL_COST_LINE_DATE_MISMATCH')
        pattern(row['source_ref'], r'EV_[A-Z0-9_]{1,80}')
        if any(row[key] is not None and (type(row[key]) is not int or not 0 <= row[key] <= 10**12)
               for key in cost_fields):
            fail('SHOPIFY_HISTORICAL_COST_AMOUNT')
        deduplicate(seen, identity, row)
    for identity, row in seen.items():
        orders[identity].update({key: str(row[key]) if row[key] is not None else '' for key in cost_fields})
    return dict(version=doc['version'], observed_at=doc['observed_at'], digest=digest(doc),
                matched_lines=len(seen), basis='MERCHANT_SUPPLIED_TIME_MATCHED_LINE_COSTS')


def reconcile_spend(document, result, path, account_id, spend_timezone, digits, *, native_workspace=None, cost_path=None):
    from .commerce_csv import SPEND_KEYS, load_csv, reconcile_rows

    account = pattern(account_id, r"[1-9][0-9]{0,39}")
    if spend_timezone != document["timezone"]:
        fail("SHOPIFY_SPEND_TIMEZONE_MISMATCH")
    if type(digits) is not int or not 0 <= digits <= 4:
        fail("SHOPIFY_SPEND_MINOR_DIGITS_INVALID")
    if result["status"] != "SHOPIFY_READ_COMPLETE":
        return {"status": "SHOPIFY_RECONCILIATION_NO_COMPLETE_WINDOW", "fallback": CSV_FALLBACK}
    store = result["store"].split(".")[0]
    currency, since, until = (result[key] for key in ("currency", "since", "until"))

    def in_minor(value):
        amount = Decimal(value).scaleb(digits)
        if amount != amount.to_integral_value() or not 0 <= amount <= 10**12:
            fail("SHOPIFY_SPEND_MINOR_CONVERSION")
        return str(int(amount))

    orders = {}
    for row in result["lines"]:
        keys = (row["order_id"].rsplit("/", 1)[-1], row["line_id"].rsplit("/", 1)[-1])
        orders[keys] = dict(order_id=keys[0], date=row["date"], gross_minor=in_minor(row["gross"]),
                            discount_minor=in_minor(row["discounts"]), product_cost_minor="",
                            fulfillment_minor="", payment_minor="")
    cost_evidence = (_historical_line_costs(cost_path, result['shop_id'], currency, orders)
                     if cost_path is not None else None)
    days = {row["refund_id"]: row["date"] for row in result["refunds"]}
    returns = {}
    for row in result["refund_lines"]:
        key = tuple(row[k].rsplit("/", 1)[-1] for k in ("refund_line_id", "order_id", "line_id"))
        returns[key] = dict(order_id=key[1], line_id=key[2], date=days[row["refund_id"]],
                            refund_minor=in_minor(row["amount"]))
    native_spend = native_workspace is not None
    spend_hash = None
    if native_spend:
        from .native_insights import commerce_spend

        spend, spend_hash = commerce_spend(native_workspace, path, account, currency, spend_timezone, since, until, digits)
        duplicates = 0
    else:
        spend, duplicates = load_csv(path, SPEND_KEYS, ("date",), "account_id", account, currency)
    ledger = reconcile_rows(orders, returns, spend, store, account, currency, since, until, "order",
                            duplicates=dict(orders=0, refunds=0, spend=duplicates))
    return dict(ledger, source_scope=("SHOPIFY_MERCHANDISE_AND_META_NATIVE_SPEND" if native_spend
                                      else "SHOPIFY_MERCHANDISE_AND_SUPPLIED_AD_SPEND"),
                limitations=["MERCHANDISE_LINE_REFUNDS", "HISTORICAL_COST_NOT_INFERRED", "EXPLICIT_SAME_TIMEZONE"],
                native_ad_spend_snapshot_sha256=spend_hash,
                shopify_source=result.get("source"), historical_cost_evidence=cost_evidence,
                cash_refund_evidence=result.get("refund_transaction_evidence"),
                live_shopify_status="NOT_RUN" if not result.get("network_used") else "HTTPS_READ_COMPLETE")
