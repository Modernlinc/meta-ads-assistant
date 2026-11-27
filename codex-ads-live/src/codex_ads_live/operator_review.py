"""Evidence-only decisions for the operator, without account writes."""
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit
import re

from .business_profile import SECRET_PATTERN
from .local_paths import checked_path
from .operator_v1 import knowledge_search
from .validation import fail, object_only, strict_json, validate_https_destination


def load_input(path):
    source = checked_path(path, link_error="OPERATOR_INPUT_LINK", volume_error="OPERATOR_VOLUME_NOT_MOUNTED")
    if not source.is_file() or source.stat().st_size > 400_000:
        fail("OPERATOR_INPUT_NOT_BOUNDED_FILE")
    text = source.read_text(encoding="utf-8-sig")
    if SECRET_PATTERN.search(text):
        fail("OPERATOR_INPUT_SECRET_PATTERN")
    return strict_json(text, max_chars=400_000)


def structured(doc, schema, fields, optional=()):
    object_only(doc, ("schema", *fields, *optional), ("schema", *fields))
    if doc["schema"] != schema:
        fail("OPERATOR_INPUT_SCHEMA")


def minor(value):
    if value is not None and (type(value) is not int or not 0 <= value <= 10**12):
        fail("OPERATOR_NUMBER_INVALID")
    return value


def pattern(value, regex):
    if type(value) is not str or not re.fullmatch(regex, value):
        fail("OPERATOR_IDENTIFIER_INVALID")
    return value


def evidence_text(value):
    if type(value) is not str or not 1 <= len(value.strip()) <= 300 or not value.isprintable():
        fail("OPERATOR_EVIDENCE_TEXT")
    return value.strip()


def ratio(numerator, denominator):
    if numerator is None or not denominator:
        return None
    return str((Decimal(numerator) / denominator).quantize(Decimal("0.000001")))


def tracking_path(destination):
    validate_https_destination(destination, "OPERATOR_DESTINATION_INVALID")
    keys = [
        key.lower() for key, _ in parse_qsl(urlsplit(destination).query)
        if re.fullmatch(r"utm_[a-z_]{1,24}", key.lower())
    ]
    distinct = sorted(set(keys))
    return distinct, sorted(k for k, n in Counter(keys).items() if n > 1)


def business_readiness(doc):
    structured(doc, "business-readiness-v1", ("account_id", "product_id", "market", "currency", "as_of", "facts"),
               ("catalog_reference",))
    account = pattern(doc["account_id"], r"[1-9][0-9]{0,39}")
    product = pattern(doc["product_id"], r"[A-Za-z0-9_-]{1,80}")
    market = pattern(doc["market"], r"[A-Z]{2}")
    currency = pattern(doc["currency"], r"[A-Z]{3}")
    from .commerce_csv import day
    checked = day(doc["as_of"])
    kinds = ("PRODUCT", "OFFER", "DESTINATION", "LANDING_PAGE_STATUS", "NET_ORDER_MINOR", "VARIABLE_COST_MINOR")
    facts = doc["facts"]
    if type(facts) is not list or len(facts) > 100:
        fail("BUSINESS_FACT_LIMIT")
    observed = defaultdict(dict)
    identifiers = set()
    for fact in facts:
        fields = ("id", "kind", "value", "observed_on")
        object_only(fact, fields, fields)
        kind, ref = fact["kind"], pattern(fact["id"], r"EV_[A-Z0-9_]{1,80}")
        if kind not in kinds or ref in identifiers or day(fact["observed_on"]) > checked:
            fail("BUSINESS_FACT_INVALID")
        identifiers.add(ref)
        value = fact["value"]
        if value is None:
            continue
        if kind.endswith("_MINOR"):
            value = minor(value)
        else:
            value = evidence_text(value)
            if kind == "DESTINATION":
                tracking_path(value)
            if kind == "LANDING_PAGE_STATUS" and value not in ("READY", "ISSUES"):
                fail("BUSINESS_LANDING_PAGE_STATUS")
        observed[kind][ref] = value
    values = {}
    for kind in kinds:
        entries = observed[kind]
        candidates = set(entries.values())
        state = "MISSING" if not candidates else "CONFIRMED" if len(candidates) == 1 else "CONFLICT"
        values[kind] = dict(state=state, value=next(iter(candidates)) if state == "CONFIRMED" else None,
                            evidence_ids=sorted(entries))
    income, cost = (values[k]["value"] for k in ("NET_ORDER_MINOR", "VARIABLE_COST_MINOR"))
    margin = income - cost if income is not None and cost is not None else None
    missing = [kind for kind, row in values.items() if row["state"] == "MISSING"]
    conflicts = [kind for kind, row in values.items() if row["state"] == "CONFLICT"]
    catalog_check = "NOT_PROVIDED"
    if "catalog_reference" in doc:
        catalog = doc["catalog_reference"]
        fields = ("catalog_id", "product_id", "destination", "currency", "availability", "reference")
        object_only(catalog, fields, fields)
        pattern(catalog["catalog_id"], r"[1-9][0-9]{0,39}")
        pattern(catalog["reference"], r"EV_[A-Z0-9_]{1,80}")
        tracking_path(catalog["destination"])
        catalog_check = ("MATCHED_LOCAL_REFERENCE" if catalog["product_id"] == product
                         and catalog["currency"] == currency and catalog["availability"] == "IN_STOCK"
                         and catalog["destination"] == values["DESTINATION"]["value"] else "CONFLICT_OR_UNAVAILABLE")
    ready = (not missing and not conflicts and values["LANDING_PAGE_STATUS"]["value"] == "READY" and margin > 0
             and catalog_check != "CONFLICT_OR_UNAVAILABLE")
    return dict(status="BUSINESS_EVIDENCE_READY" if ready else "BUSINESS_EVIDENCE_NEEDED",
                account_id=account, product_id=product, currency=currency, market=market, fields=values,
                missing=missing, conflicts=conflicts, contribution_before_ad_minor=margin, catalog_state=catalog_check,
                break_even_purchase_cpa_minor=max(0, margin) if margin is not None else None,
                network_used=False, actions_authorized=False)


def strategy_review(doc):
    structured(doc, "operator-strategy-v1", (
        "account_id", "currency", "product", "offer", "goal", "audience", "destination",
        "proof_ref", "commercial", "test_variable", "advantage_plus",
    ), ("prior_results", "alternative_audience", "placements", "attribution_basis", "lead_value_minor"))
    account = pattern(doc["account_id"], r"[1-9][0-9]{0,39}")
    currency = pattern(doc["currency"], r"[A-Z]{3}")
    for key in ("product", "offer", "audience"):
        evidence_text(doc[key])
    pattern(doc["proof_ref"], r"EV_[A-Z0-9_]{1,80}")
    valid = {
        "goal": ("PURCHASE", "LEAD", "QUALIFIED_LEAD"),
        "test_variable": ("CREATIVE_ANGLE", "AUDIENCE", "OFFER", "PLACEMENT", "LANDING_PAGE"),
        "advantage_plus": ("UNKNOWN", "ELIGIBLE", "NOT_ELIGIBLE"),
    }
    if any(doc[k] not in choices for k, choices in valid.items()):
        fail("OPERATOR_STRATEGY_CHOICE_INVALID")
    keys = ("net_order_minor", "variable_cost_minor", "target_cpa_minor", "test_budget_minor",
            "minimum_results", "days")
    c = doc["commercial"]
    object_only(c, keys, keys)
    revenue, variable, cpa, budget, results, days = (minor(c[key]) for key in keys)
    if days not in range(1, 91) or results not in range(1, 100001) or cpa == 0:
        fail("OPERATOR_TEST_SCOPE_INVALID")
    tracking, duplicate = tracking_path(doc["destination"])
    margin = revenue - variable if revenue is not None and variable is not None else None
    room = budget >= cpa * results if budget is not None and cpa is not None else None
    prior = minor(doc.get("prior_results"))
    audience_alternative = doc.get("alternative_audience")
    if audience_alternative is not None:
        evidence_text(audience_alternative)
    placements = doc.get("placements", [])
    if type(placements) is not list or len(placements) > 12:
        fail("OPERATOR_PLACEMENT_LIST")
    for placement in placements:
        pattern(placement, r"[A-Z_]{1,40}")
    attribution = doc.get("attribution_basis")
    if attribution is not None:
        evidence_text(attribution)
    ceiling = margin if doc["goal"] == "PURCHASE" else minor(doc.get("lead_value_minor"))
    comparison = {
        "budget": [
            {"option": "ABO", "use_when": "固定各实验组预算，需保证每组取得观察", "claim_ids": ["K-V0258"]},
            {"option": "CBO", "use_when": "同目标下集中信号，仍需控制观察期和分配偏差", "claim_ids": ["K-V0268"]},
        ],
        "audience": [
            {"option": "BROAD", "use_when": "以同素材和转化口径测试宽泛覆盖"},
            {"option": "INTEREST", "use_when": "存在来自商品或客户研究的兴趣假设", "candidate": audience_alternative},
        ],
        "placement": {"manual_candidates": placements,
                      "advantage_plus_state": doc["advantage_plus"], "eligibility_basis": "CALLER_DECLARED"},
        "scale": {"eligible_for_consideration": prior is not None and prior >= results and room is True
                  and attribution is not None and ceiling is not None and cpa <= ceiling,
                  "requires": ["SAME_ATTRIBUTION_WINDOW", "OBSERVED_PROFIT_OR_LEAD_VALUE", "APPROVAL"]},
        "attribution": {"basis": attribution, "required_for_comparison": True},
    }
    return dict(
        status="STRATEGY_REVIEW_READY", account_id=account, currency=currency,
        break_even_cpa_minor=max(0, margin) if margin is not None and doc["goal"] == "PURCHASE" else None,
        contribution_before_ad_minor=margin,
        missing_inputs=[k for k in keys if c[k] is None],
        evidence_ref=doc["proof_ref"], stated_advantage_plus_eligibility=doc["advantage_plus"],
        destination_utm_keys=tracking, duplicate_utm_keys=duplicate,
        options=knowledge_search("strategy")["claims"],
        comparison=comparison, prior_results=prior, strategy_selected=None,
        test=dict(candidates=2, variable=doc["test_variable"], primary_result=doc["goal"],
                  budget_minor=budget, target_cpa_minor=cpa, minimum_results=results, observation_days=days,
                  minimum_result_budget_feasible=room,
                  control="KEEP_EXISTING_OFFER_AND_PRIOR_ASSET",
                  guardrails=["INDEPENDENT_APPROVAL", "KEEP_OTHER_VARIABLES_FIXED",
                              "STOP_AT_DECLARED_SPEND_CAP", "VERIFY_MEASUREMENT_BEFORE_INTERPRETATION",
                              "NO_WINNER_IF_MINIMUM_RESULTS_UNMET"]),
        actions_authorized=False,
    )


PERIOD_CONTEXT = ("account_id", "currency", "timezone", "attribution", "scope_id")
PERIOD_METRICS = ("impressions", "spend_minor", "clicks", "landing_views", "conversions", "conversion_value_minor")


def period(value):
    fields = (*PERIOD_CONTEXT, *PERIOD_METRICS, "coverage", "delivery")
    object_only(value, (*fields, "since", "until", "report_time", "result_event"), fields)
    for key in PERIOD_CONTEXT:
        evidence_text(value[key])
    if value["coverage"] not in ("COMPLETE", "PARTIAL", "UNAVAILABLE", "EMPTY"):
        fail("OPERATOR_PERIOD_COVERAGE_INVALID")
    if value["delivery"] not in ("ACTIVE", "PAUSED", "WITH_ISSUES", "UNKNOWN"):
        fail("OPERATOR_PERIOD_DELIVERY_INVALID")
    for key in PERIOD_METRICS:
        minor(value[key])
    if ("since" in value) != ("until" in value):
        fail("OPERATOR_PERIOD_DATE_PAIR_REQUIRED")
    span = None
    if "since" in value:
        try:
            start, end = (date.fromisoformat(pattern(value[key], r"[0-9]{4}-[0-9]{2}-[0-9]{2}"))
                          for key in ("since", "until"))
        except ValueError:
            fail("OPERATOR_PERIOD_DATE_INVALID")
        span = (end - start).days + 1
        if not 1 <= span <= 367:
            fail("OPERATOR_PERIOD_DATE_SPAN_INVALID")
    for key in ("report_time", "result_event"):
        if key in value:
            evidence_text(value[key])
    return value, span


def metrics(data):
    click, view, conversions = (data[key] for key in ("clicks", "landing_views", "conversions"))
    spend, value, impressions = (data[key] for key in ("spend_minor", "conversion_value_minor", "impressions"))
    return dict(click_rate=ratio(click, impressions), landing_view_rate=ratio(view, click),
                conversion_rate=ratio(conversions, view), cpa_minor=ratio(spend, conversions),
                cpm_minor=ratio(spend * 1000 if spend is not None else None, impressions),
                cpc_minor=ratio(spend, click),
                aov_minor=ratio(value, conversions), roas=ratio(value, spend))


def performance_diagnose(doc):
    structured(doc, "operator-diagnosis-v1", ("before", "after", "minimum_results"), ("stage_evidence",))
    (before, before_days), (after, after_days) = (period(doc[key]) for key in ("before", "after"))
    threshold = minor(doc["minimum_results"])
    if not threshold or threshold > 100000:
        fail("OPERATOR_DIAGNOSIS_THRESHOLD_INVALID")
    mismatch = [key for key in PERIOD_CONTEXT if before[key] != after[key]]
    mismatch.extend(key for key in ("report_time", "result_event")
                    if key not in before or key not in after or before[key] != after[key])
    if before_days is None or after_days is None or before_days != after_days:
        mismatch.append("period_length")
    elif before["until"] >= after["since"]:
        mismatch.append("period_order_or_overlap")
    if before["coverage"] != "COMPLETE" or after["coverage"] != "COMPLETE":
        mismatch.append("coverage")
    points = dict(before=metrics(before), after=metrics(after))
    hypotheses, followup = [], []
    if after["delivery"] in ("PAUSED", "WITH_ISSUES"):
        followup.append("REVIEW_DELIVERY_STATUS")
    if after["conversions"] is None or after["conversions"] < threshold:
        followup.append("INSUFFICIENT_RESULT_SAMPLE")
    if mismatch:
        followup.append("REBUILD_COMPARABLE_REPORTS")
    else:
        for key, check in (
            ("click_rate", "REVIEW_CREATIVE_ANGLE_OFFER_AND_PLACEMENT"),
            ("landing_view_rate", "CHECK_TRACKING_LANDING_PAGE_SPEED_AND_CLICK_QUALITY"),
            ("conversion_rate", "REVIEW_PRODUCT_OFFER_FUNNEL_AND_EVENT_INTEGRITY"),
            ("aov_minor", "CHECK_PRODUCT_MIX_DISCOUNTS_AND_ORDER_VALUE"),
            ("roas", "RECONCILE_PLATFORM_ATTRIBUTION_WITH_STORE_FACTS"),
        ):
            old, new = points["before"][key], points["after"][key]
            if old is not None and new is not None and Decimal(new) < Decimal(old):
                hypotheses.append(dict(metric=key, before=old, after=new,
                                       investigation=check, state="NEEDS_CONFIRMATION"))
        old_cpm, new_cpm = (points[key]["cpm_minor"] for key in ("before", "after"))
        if old_cpm is not None and new_cpm is not None and Decimal(new_cpm) > Decimal(old_cpm):
            hypotheses.append(dict(metric="cpm_minor", before=old_cpm, after=new_cpm,
                                   investigation="COMPARE_PLACEMENT_MIX_AND_AUDIENCE_AUCTION",
                                   state="NEEDS_CONFIRMATION"))
    unavailable = {label: [k for k in PERIOD_METRICS if row[k] is None]
                   for label, row in (("before", before), ("after", after))}
    stage_names = ("delivery", "placement", "tracking", "sample", "creative", "offer", "landing_page", "funnel")
    supporting = doc.get("stage_evidence", {})
    object_only(supporting, stage_names)
    stages = []
    for name in stage_names:
        if name not in supporting:
            stages.append({"stage": name, "state": "EVIDENCE_MISSING"})
            continue
        row = supporting[name]
        fields = ("reference", "state")
        object_only(row, fields, fields)
        pattern(row["reference"], r"EV_[A-Z0-9_]{1,80}")
        if row["state"] not in ("OBSERVED_CLEAR", "OBSERVED_ISSUE", "HYPOTHESIS"):
            fail("OPERATOR_DIAGNOSIS_STAGE_STATE")
        stages.append(dict(stage=name, reference=row['reference'],
                           state='HYPOTHESIS' if row['state']=='HYPOTHESIS' else 'REFERENCE_UNVERIFIED',
                           declared_state=row['state'], evidence_basis='CALLER_DECLARED_REFERENCE'))
    return dict(status="PERFORMANCE_EVIDENCE_REVIEW", comparable=not mismatch,
                comparability_mismatches=mismatch, metrics=points, hypotheses=hypotheses,
                missing_metrics=unavailable, followup=followup, stages=stages,
                causal_claim_authorized=False, actions_authorized=False)


def compare_attribution(value):
    object_only(value, ("meta", "ga4"), ("meta", "ga4"))
    keys = ("event", "window", "model", "report_time", "result_count", "scope")
    for record in value.values():
        object_only(record, keys, keys)
        for key in keys[:-2] + ("scope",):
            evidence_text(record[key])
        minor(record["result_count"])
    meta, ga4 = value["meta"], value["ga4"]
    fields = [key for key in keys if key != "result_count" and meta[key] != ga4[key]]
    a, b = meta["result_count"], ga4["result_count"]
    return dict(state="COMPARABLE" if not fields and a is not None and b is not None else "BASIS_OR_COUNTS_DIFFER",
                mismatched_fields=fields, meta_less_ga4=a-b if not fields and a is not None and b is not None else None,
                basis="CALLER_SUPPLIED_AGGREGATES")


def tracking_audit(doc):
    structured(doc, "operator-tracking-v1", ("dataset_id", "destination", "events"), ("attribution",))
    pattern(doc["dataset_id"], r"[1-9][0-9]{0,39}")
    keys, duplicates = tracking_path(doc["destination"])
    events = doc["events"]
    if type(events) is not list or len(events) > 2000:
        fail("OPERATOR_EVENT_LIST")
    signals = defaultdict(Counter)
    tests = defaultdict(Counter)
    names = defaultdict(set)
    missing = 0
    missing_test = 0
    for row in events:
        fields = ("channel", "name", "event_id")
        object_only(row, (*fields, "mode"), fields)
        channel = row["channel"]
        if channel not in ("PIXEL", "CAPI"):
            fail("OPERATOR_EVENT_CHANNEL")
        name = pattern(row["name"], r"[A-Za-z][A-Za-z0-9_]{0,79}")
        mode = row.get("mode", "PRODUCTION")
        if mode not in ("PRODUCTION", "TEST"):
            fail("OPERATOR_EVENT_MODE")
        identity = row["event_id"]
        if identity is None:
            missing += mode == "PRODUCTION"
            missing_test += mode == "TEST"
            continue
        pattern(identity, r"EV_[A-Z0-9_]{1,80}")
        target = signals if mode == "PRODUCTION" else tests
        target[name, identity][channel] += 1
        names[mode, identity].add(name)
    return dict(
        status="TRACKING_EVIDENCE_REVIEW", observed_events=len(events), utm_keys=keys,
        duplicate_utm_keys=duplicates, missing_event_ids=missing,
        paired_event_keys=sum(set(counts) == {"PIXEL", "CAPI"} for counts in signals.values()),
        same_channel_repeats=sum(n - 1 for counts in signals.values() for n in counts.values()),
        unpaired_event_keys=sum(len(counts) == 1 for counts in signals.values()),
        missing_test_event_ids=missing_test,
        paired_test_event_keys=sum(set(counts) == {"PIXEL", "CAPI"} for counts in tests.values()),
        event_id_used_with_multiple_names=sum(len(value) > 1 for value in names.values()),
        attribution=compare_attribution(doc["attribution"]) if "attribution" in doc else None,
        read_only_recipes={
            "meta": "在现有测试事件面板核对同一事件名称和事件 ID",
            "gtm": "查看现有 Tag Assistant 调试记录的触发器和变量",
            "ga4": "使用既有 DebugView 记录核实时点与事件参数",
        },
        platform_deduplication_verified=False, actions_authorized=False,
    )
