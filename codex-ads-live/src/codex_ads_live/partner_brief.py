"""Validate local evidence handed over from adjacent commerce work."""
from .operator_review import evidence_text, pattern
from .validation import aware_time, fail, object_only


FIELDS_BY_ROLE = {
    "META_AD_PLANNING": ("test_hypothesis", "measurement_requirement", "creative_brief"),
    "CREATIVE_METHOD": ("creative_brief", "material_version", "usage_right"),
    "COMMERCE_ANALYTICS": ("settlement_basis", "refund_definition", "fulfillment_cost"),
    "MEASUREMENT_METHOD": ("measurement_requirement", "event_semantics", "attribution_scope"),
    "BRAND_POSITIONING": ("product_fact", "landing_page_fact", "objection_hypothesis"),
    "PRODUCT_SELECTION": ("product_fact", "demand_signal", "supply_constraint"),
    "STOREFRONT": ("landing_page_fact", "offer", "fulfillment"),
    "BACKOFFICE": ("refund_definition", "settlement_basis", "fulfillment_cost"),
    "WORKSPACE_OPERATIONS": ("revision", "source_change", "handoff_owner"),
    "SEARCH_ADS": ("measurement_requirement", "test_hypothesis"),
    "SHORT_VIDEO": ("purchase_question", "creative_brief"),
    "SOCIAL_CONTENT": ("creative_brief", "material_version"),
    "CREATOR_PARTNERS": ("usage_right", "creator_deliverable"),
    "MARKETPLACE": ("product_fact", "return_definition"),
    "SEARCH_CONTENT": ("variant_fact", "landing_page_fact"),
    "COMMUNITY_RESEARCH": ("purchase_question", "objection_hypothesis"),
    "PRODUCT_CATALOG": ("catalog_fact", "creative_brief"),
    "CUSTOMER_SUPPORT": ("purchase_question", "fulfillment"),
    "SALES_INQUIRY": ("quote_condition", "sample_evidence", "handoff_owner"),
}

ROUTES = {
    "refund_definition": "commerce-reconcile", "settlement_basis": "commerce-reconcile",
    "fulfillment_cost": "commerce-reconcile", "return_definition": "commerce-reconcile",
    "measurement_requirement": "tracking-audit", "landing_page_fact": "strategy-review",
    "event_semantics": "tracking-audit", "attribution_scope": "media-buyer review",
    "revision": "workspace-first-launch-handoff", "source_change": "workspace-first-launch-handoff",
    "handoff_owner": "workspace-first-launch-handoff",
}


def review(document):
    fields = ("schema", "role", "product", "market", "items")
    object_only(document, fields, fields)
    if document["schema"] != "partner-evidence-brief-v1":
        fail("PARTNER_BRIEF_SCHEMA")
    role = pattern(document["role"], r"[A-Z_]{2,40}")
    allowed = FIELDS_BY_ROLE.get(role)
    if allowed is None:
        fail("PARTNER_BRIEF_ROLE")
    market = pattern(document["market"], r"[A-Z]{2}")
    product = evidence_text(document["product"])
    items = document["items"]
    if type(items) is not list or not 1 <= len(items) <= 24:
        fail("PARTNER_BRIEF_ITEM_COUNT")
    cleaned = {}
    for item in items:
        fields = ("kind", "ref", "text", "observed_at", "state")
        object_only(item, fields, fields)
        if item["kind"] not in allowed or item["state"] not in ("CONFIRMED", "HYPOTHESIS", "WITHDRAWN"):
            fail("PARTNER_BRIEF_ITEM")
        ref = pattern(item["ref"], r"EV_[A-Z0-9_]{1,80}")
        if ref in cleaned:
            fail("PARTNER_BRIEF_DUPLICATE_REF")
        aware_time(item["observed_at"])
        cleaned[ref] = dict(item, text=evidence_text(item["text"]) if item["state"] != "WITHDRAWN" else None,
                            next_entry=ROUTES.get(item["kind"], "workspace-creative-research"))
    return dict(status="PARTNER_BRIEF_REVIEWED", role=role, product=product, market=market,
                results=list(cleaned.values()), source_status="USER_SUPPLIED_EVIDENCE",
                host_execution=False, network_used=False, actions_authorized=False)
