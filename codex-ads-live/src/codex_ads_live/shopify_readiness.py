"""Preview the smallest read-only Shopify order scope, without making requests."""
from datetime import date, timedelta

from .commerce_csv import day
from .operator_review import pattern
from .validation import fail, object_only


def preview(document):
    fields = ("schema", "store", "since", "until", "checked_on", "reported_scopes")
    object_only(document, fields, fields)
    if document["schema"] != "shopify-order-readiness-v1":
        fail("SHOPIFY_READINESS_SCHEMA")
    store = pattern(document["store"], r"[a-z0-9][a-z0-9-]{0,61}\.myshopify\.com")
    start, end, checked = (date.fromisoformat(day(document[key])) for key in ("since", "until", "checked_on"))
    if not start <= end <= checked or (end - start).days > 366:
        fail("SHOPIFY_ORDER_WINDOW_INVALID")
    scopes = document["reported_scopes"]
    if type(scopes) is not list or len(scopes) > 100:
        fail("SHOPIFY_SCOPE_LIST_INVALID")
    for grant in scopes:
        pattern(grant, r"[a-z][a-z0-9_]{1,60}")
    old = (checked - start).days > 60
    required = {"read_orders", "read_all_orders"} if old else {"read_orders"}
    missing = required.difference(scopes)
    candidate_start = max(start, checked - timedelta(days=60))
    feasible = "read_orders" in scopes and candidate_start <= end
    return {
        "status": "SHOPIFY_READ_PREVIEW",
        "store": store,
        "requested_window": {"since": start.isoformat(), "until": end.isoformat()},
        "required_scopes": sorted(required),
        "missing_reported_scopes": sorted(missing),
        "window_with_reported_scopes": (
            {"since": start.isoformat(), "until": end.isoformat()} if not missing else
            {"since": candidate_start.isoformat(), "until": end.isoformat()} if feasible else None
        ),
        "available_source": "USER_SUPPLIED_ORDER_LINE_AND_REFUND_CSV",
        "scope_verified_by_platform": False,
        "protected_customer_data_access": "UNVERIFIED",
        "minimum_data": ["order_id", "line_id", "date", "currency", "amount", "refund_id"],
        "excluded_data": ["contact", "address", "customer_profile", "payment_credentials", "inventory"],
        "live_shopify_status": "DEFERRED_NOT_RUN",
        "network_used": False,
    }
