"""Review independently checked archives in one named customer workspace."""
from collections import defaultdict
from decimal import Decimal, localcontext

from .ads_reconciliation import read_run_observations
from .client_workspace import closed_account_window, health_check, read, workspace_identity
from .meta_write_mapping import utcnow
from .monitor import account_cycle_pagination_unverified
from .operator_review import pattern
from .review import ads_analysis
from .validation import fail


def selection(profile, requested):
    if not 1 <= len(requested) <= 12 or len(set(requested)) != len(requested):
        fail("MULTI_ACCOUNT_SELECTION_INVALID")
    for account in requested:
        pattern(account, r"[1-9][0-9]{0,39}")
        if f"ACCOUNT:{account}" not in profile["assets"]:
            fail("MULTI_ACCOUNT_NOT_MAPPED")
    return set(requested)


def prepare(workspace, requested, days):
    profile = read(workspace, existing_only=True)
    selected = selection(profile, requested)
    if not 1 <= days <= 30:
        fail("MULTI_ACCOUNT_LOOKBACK_INVALID")
    stamp = utcnow()
    plans = []
    for account in sorted(selected):
        route = health_check(workspace, "ads-analysis", account, clock=lambda: stamp, existing_only=True)
        zone = profile["checks"].get(f"ACCOUNT:{account}", {}).get("timezone")
        active = route["access_observation"] == "RECENT_NAMED_ACCOUNT_READ" and zone is not None
        window = closed_account_window(days, zone, stamp) if active else None
        plans.append(dict(account_id=account, currency=profile["checks"].get(f"ACCOUNT:{account}", {}).get("currency"),
                          timezone=zone, period=window, route=route["reason"],
                          ready_for_separate_read=active, next_entry="workspace-plan" if active else "workspace-route"))
    return dict(status="MULTI_ACCOUNT_LOCAL_PLAN", workspace_identity_sha256=workspace_identity(profile),
                accounts=plans, network_used=False, actions_authorized=False)


def cohorts(reports):
    partitions = defaultdict(list)
    keys = ("currency", "timezone", "attribution", "action_report_time", "date_start", "date_stop", "api_version")
    for account, entry in reports.items():
        report = entry["report"]
        if report["state"] == "COMPUTED" and report["analysis"]["coverage"] == "COMPLETE":
            context = report["context"]
            partitions[tuple(context[key] for key in keys)].append((account, report["analysis"]["metrics"]))
    result = []
    for key, members in sorted(partitions.items()):
        outputs = {}
        with localcontext() as arithmetic:
            arithmetic.prec = 64
            for measure in ("spend", "impressions", "clicks"):
                values = [metrics.get(measure, {}).get("value") for _, metrics in members]
                outputs[measure] = (str(sum(Decimal(v) for v in values))
                                    if all(v is not None for v in values) else None)
        result.append(dict(context=dict(zip(keys, key)), accounts=[a for a, _ in members],
                           totals=outputs, state="COMPLETE_SAME_REPORT_CONTEXT"))
    return result


def summarize(workspace, receipts, requested=None):
    if not 1 <= len(receipts) <= 12:
        fail("MULTI_ACCOUNT_RECEIPT_COUNT")
    profile = read(workspace, existing_only=True)
    identity = workspace_identity(profile)
    selected = selection(profile, requested) if requested is not None else None
    reports = {}
    for path in receipts:
        observation, binding = read_run_observations(path, workspace_insights=True)
        if observation["workspace_identity_sha256"] != identity:
            fail("MULTI_ACCOUNT_WORKSPACE_MISMATCH")
        sources = observation["sources"]
        query = sources["ads"]["plan"]
        account_id = query["account_id"]
        if f"ACCOUNT:{account_id}" not in profile["assets"]:
            fail("MULTI_ACCOUNT_NOT_MAPPED")
        if selected is not None and account_id not in selected:
            fail("MULTI_ACCOUNT_RECEIPT_NOT_SELECTED")
        if account_id in reports:
            fail("MULTI_ACCOUNT_DUPLICATE_RECEIPT")
        report = ads_analysis(sources, pagination_unverified=account_cycle_pagination_unverified(observation))
        reports[account_id] = {
            "account_id": account_id,
            "period": {"since": query["since"], "until": query["until"]},
            "scope": {key: query[key] for key in ("operation", "version")},
            "report": report,
            "evidence": binding,
        }
    return {
        "status": "PER_ACCOUNT_REPORTS",
        "accounts": [reports[key] for key in sorted(reports)],
        "shared_totals": None,
        "cohort_totals": cohorts(reports),
        "selected_accounts_without_receipts": sorted((selected or set()) - reports.keys()),
        "shared_totals_state": "PER_ACCOUNT_CURRENCY_TIMEZONE_AND_ATTRIBUTION_PRESERVED",
        "network_used": False,
    }


def review_inventory(workspace, monitor, account, kind, identifiers, parent, parent_kind=None):
    from .monitor import AD_INVENTORY_CHANNELS, ReadMonitor
    from .review import inventory_analysis

    profile = read(workspace, existing_only=True)
    selection(profile, [account])
    state = ReadMonitor(monitor, mode="READ_ONLY", existing_only=True).status(existing_only=True)
    scope = state.get("sources", {})
    if (set(scope) != AD_INVENTORY_CHANNELS
            or state.get("unpublished_snapshot_count")
            or state.get("workspace_identity_sha256") != workspace_identity(profile)
            or scope["account"]["plan"]["account_id"] != account):
        fail("ACCOUNT_INVENTORY_SNAPSHOT_SCOPE")
    report = inventory_analysis(scope)
    if not report["levels"]:
        fail("ACCOUNT_INVENTORY_ACCOUNT_PROOF_REQUIRED")
    rows = report["levels"]["ad_inventory" if kind == "ads" else kind]["rows"]
    target = set()
    if identifiers is not None:
        if not 1 <= len(identifiers) <= 50:
            fail("ACCOUNT_INVENTORY_IDS_LIMIT")
        target = {pattern(identifier, r"[1-9][0-9]{0,39}") for identifier in identifiers}
        rows = [row for row in rows if row["id"] in target]
    if parent is None and parent_kind is not None:
        fail("ACCOUNT_INVENTORY_PARENT_NOT_APPLICABLE")
    if parent is not None:
        pattern(parent, r"[1-9][0-9]{0,39}")
        if kind == "campaigns" or (kind == "adsets" and parent_kind == "adset"):
            fail("ACCOUNT_INVENTORY_PARENT_NOT_APPLICABLE")
        field = "campaign_id" if kind == "adsets" or parent_kind == "campaign" else "adset_id"
        rows = [row for row in rows if row.get(field) == parent]
    return dict(status="ACCOUNT_INVENTORY_REVIEWED", account_id=account,
                coverage=report["coverage"], objects=rows, missing_ids=sorted(target - {r["id"] for r in rows}),
                missing_ids_basis="NOT_OBSERVED_IN_THIS_CAPTURE", network_used=False, actions_authorized=False)


def insights_slice(workspace, receipt, account, kind, identifier):
    from .analysis import aggregate_ads
    profile = read(workspace, existing_only=True)
    selection(profile, [account])
    if (kind == "account") != (identifier is None):
        fail("ACCOUNT_INSIGHTS_OBJECT_SCOPE")
    if identifier is not None:
        pattern(identifier, r"[1-9][0-9]{0,39}")
    observation, binding = read_run_observations(receipt, workspace_insights=True)
    sources = observation["sources"]
    report = sources["ads"]
    if (observation["workspace_identity_sha256"] != workspace_identity(profile)
            or report["plan"]["account_id"] != account):
        fail("ACCOUNT_INSIGHTS_REPORT_SCOPE")
    analysis = ads_analysis(sources, pagination_unverified=account_cycle_pagination_unverified(observation))
    base = dict(account_id=account, object_kind=kind, object_id=identifier, evidence=binding,
                basis="OBSERVED_AD_DAY_ROWS", network_used=False, actions_authorized=False)
    if analysis["state"] != "COMPUTED":
        return dict(base, status="ACCOUNT_INSIGHTS_NOT_AVAILABLE", reason=analysis["reason"], analysis=None)
    context = analysis["context"]
    field = kind + "_id"
    selected = report["rows"] if kind == "account" else [row for row in report["rows"] if row.get(field) == identifier]
    if kind != "account" and not selected:
        return dict(base, status="ACCOUNT_INSIGHTS_ID_NOT_OBSERVED", context=context, analysis=None)
    coverage = analysis["analysis"]["coverage"]
    if any(field not in row for row in report["rows"]):
        coverage = "PARTIAL"
    return dict(base, status="ACCOUNT_INSIGHTS_SLICE_REVIEWED", context=context,
                analysis=aggregate_ads(selected, context, context, coverage))
