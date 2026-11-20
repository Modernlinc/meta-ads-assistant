"""Explicit reporting context and per-metric freshness; no causal attribution."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from zoneinfo import ZoneInfoNotFoundError
import re
from .read_adapter import (CLICK_WINDOW, INSIGHTS_FIELDS, PARENT_ID_FIELDS, WEBSITE_FIELDS, WEBSITE_LEAD, WEBSITE_PURCHASE,
                           identifier, normalize_action_stats, numeric_string, post_identifier)
from .timezones import load_zone
from .validation import aware_time, fail, object_only
from .vendor import t0_core as t0

METRICS=("post_video_live_current_viewers","post_video_views_live","post_video_social_actions_count_unique","post_video_views_by_live_status")
CONTEXT=("account_id","currency","timezone","date_start","date_stop","level","attribution","action_report_time","breakdowns","api_version","time_increment")
WEBSITE_ATTRIBUTION = "EXPLICIT_7D_CLICK"
AD_TOTAL_METRICS = ("spend", "impressions", "clicks")


def validate_context(context):
    object_only(context,CONTEXT,CONTEXT)
    identifier(context["account_id"])
    if type(context["currency"]) is not str or not re.fullmatch(r"[A-Z]{3}",context["currency"]):fail("CURRENCY_REQUIRED")
    try:load_zone(context["timezone"])
    except (TypeError,ValueError,ZoneInfoNotFoundError):fail("IANA_TIMEZONE_REQUIRED")
    if (context["level"]!="ad" or context["attribution"] not in ("UNIFIED_ADSET", WEBSITE_ATTRIBUTION)
            or context["action_report_time"]!="impression"):
        fail("REPORT_CONTEXT_UNSUPPORTED")
    if context["api_version"]!="v27.0" or type(context["time_increment"]) is not int or context["time_increment"]!=1 or context["breakdowns"]!=[]:fail("REPORT_GRAIN_UNSUPPORTED")
    try:
        if any(type(context[k]) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}",context[k]) for k in ("date_start","date_stop")):fail("INVALID_DATE_RANGE")
        if date.fromisoformat(context["date_start"])>date.fromisoformat(context["date_stop"]):fail("DATE_ORDER")
    except (TypeError,ValueError):fail("INVALID_DATE_RANGE")


def aggregate_ads(rows,context,expected_context,coverage):
    validate_context(context);validate_context(expected_context)
    if context!=expected_context:fail("REPORT_CONTEXT_MISMATCH")
    if type(rows) is not list or len(rows)>10000:fail("ROW_BUDGET")
    if coverage not in ("COMPLETE","PARTIAL"):fail("COVERAGE_REQUIRED")
    website = context["attribution"] == WEBSITE_ATTRIBUTION
    fields = (*INSIGHTS_FIELDS, *PARENT_ID_FIELDS)
    if website:
        fields += WEBSITE_FIELDS
    seen=set()
    for row in rows:
        object_only(row,fields,("account_id","ad_id","date_start","date_stop"))
        identifier(row["ad_id"])
        for field in PARENT_ID_FIELDS:
            if field in row:
                identifier(row[field])
        try:
            for key in ("date_start","date_stop"):
                if type(row[key]) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}",row[key]):fail("INVALID_ROW_DATE")
                date.fromisoformat(row[key])
        except ValueError:fail("INVALID_ROW_DATE")
        if row["account_id"]!=context["account_id"]:fail("ACCOUNT_SCOPE_MISMATCH")
        if row["date_start"]!=row["date_stop"]:fail("DAILY_GRAIN_REQUIRED")
        if not context["date_start"]<=row["date_start"]<=context["date_stop"]:fail("ROW_OUTSIDE_DATE_SCOPE")
        key=(row["ad_id"],row["date_start"])
        if key in seen:fail("DUPLICATE_REPORT_ROW")
        seen.add(key)
        if website:
            if row.get("account_currency") not in (None, context["currency"]):
                fail("REPORT_INSIGHTS_CURRENCY_MISMATCH")
            for field in ("actions","action_values"):
                if field in row and normalize_action_stats(row[field],field) != row[field]:
                    fail("REPORT_UNSANITIZED_WEBSITE_ACTION")
    values = _report_metrics(rows, context, coverage)
    limits = ["仅当前查询口径；不推定所有广告或所有经营结果"]
    if website:
        limits.append("Meta 归因网站事件；合格线索、订单净额、利润和增量需独立核验")
    else:
        limits.append("当前查询未请求网站转化数据")
    return {"metrics":values,"coverage":coverage,"row_count":len(rows),"causal_claim_allowed":False,
            "numeric_ui_reconciliation":"NOT_PERFORMED","currency":context["currency"],"timezone":context["timezone"],
            "limitations":limits}


def _report_metrics(rows, context, coverage):
    with localcontext() as arithmetic:
        # Up to 10,000 rows, with 24 integral and 12 fractional digits per field.
        arithmetic.prec = 64
        arithmetic.rounding = ROUND_HALF_EVEN
        values = _ads_values(rows, coverage)
        if context["attribution"] == WEBSITE_ATTRIBUTION:
            values.update(_website_values(rows, coverage, values["spend"]["value"]))
        return values


def aggregate_hierarchy(rows, context, coverage):
    """Group already validated ad-day rows by IDs returned in the same query."""
    ad_campaigns = defaultdict(set)
    ad_sets = defaultdict(set)
    set_campaigns = defaultdict(set)
    for row in rows:
        ad, campaign, adset = row["ad_id"], row.get("campaign_id"), row.get("adset_id")
        if campaign:
            ad_campaigns[ad].add(campaign)
        if adset:
            ad_sets[ad].add(adset)
            if campaign:
                set_campaigns[adset].add(campaign)

    campaigns, adsets = defaultdict(list), defaultdict(list)
    campaign_missing = campaign_conflicting = adset_missing = adset_conflicting = 0
    for row in rows:
        ad, campaign, adset = row["ad_id"], row.get("campaign_id"), row.get("adset_id")
        conflicting_campaign = len(ad_campaigns[ad]) > 1
        if conflicting_campaign:
            campaign_conflicting += 1
        elif not campaign:
            campaign_missing += 1
        else:
            campaigns[campaign].append(row)
        if conflicting_campaign or len(ad_sets[ad]) > 1 or (adset and len(set_campaigns[adset]) > 1):
            adset_conflicting += 1
        elif not campaign or not adset:
            adset_missing += 1
        else:
            adsets[adset].append(row)

    def rollup(groups, missing, conflicting, is_adset):
        group_coverage = "PARTIAL" if missing or conflicting else coverage
        summaries = []
        for group, members in sorted(groups.items()):
            metrics = _report_metrics(members, context, group_coverage)
            summary = {
                "id": group,
                "row_count": len(members),
                "metrics": metrics,
            }
            if is_adset:
                summary["campaign_id"] = members[0]["campaign_id"]
            summaries.append(summary)
        return {
            "coverage": group_coverage,
            "assigned_rows": len(rows) - missing - conflicting,
            "missing_parent_rows": missing,
            "conflicting_parent_rows": conflicting,
            "groups": summaries,
        }

    return {
        "basis": "PARENT_IDS_IN_CURRENT_INSIGHTS_ROWS",
        "campaigns": rollup(campaigns, campaign_missing, campaign_conflicting, False),
        "adsets": rollup(adsets, adset_missing, adset_conflicting, True),
    }


def delivery_totals(rows, coverage):
    values={}
    for metric in AD_TOTAL_METRICS:
        known=[]
        for row in rows:
            if row.get(metric) is not None:
                numeric_string(row[metric],integer=metric!="spend")
                known.append(t0.number(row[metric],integer=metric!="spend"))
        subtotal=format(sum(known,Decimal(0)),"f") if known else None
        complete=bool(rows) and len(known)==len(rows) and coverage=="COMPLETE"
        values[metric]={"status":"VALUE" if complete else ("EMPTY" if not rows and coverage=="COMPLETE" else "PARTIAL"),
                        "value":subtotal if complete else None,
                        "known_subtotal":subtotal}
    return values


def _ads_values(rows,coverage):
    values = delivery_totals(rows, coverage)
    impressions=values["impressions"]["value"];clicks=values["clicks"]["value"];spend=values["spend"]["value"]
    denominator=Decimal(impressions) if impressions is not None else None
    values["ctr_percent"]={"status":"VALUE" if denominator and clicks is not None else "UNKNOWN",
                           "value":format(Decimal(clicks)/denominator*100,".4f") if denominator and clicks is not None else None}
    values["cpm"]={"status":"VALUE" if denominator and spend is not None else "UNKNOWN",
                   "value":format(Decimal(spend)/denominator*1000,".4f") if denominator and spend is not None else None}
    return values


def _website_values(rows, coverage, spend):
    def value(row, field, action_type):
        return next((Decimal(entry[CLICK_WINDOW]) for entry in row.get(field,())
                     if entry["action_type"] == action_type), None)

    purchases = [value(row,"actions",WEBSITE_PURCHASE) for row in rows]
    leads = [value(row,"actions",WEBSITE_LEAD) for row in rows]
    purchase_value = [value(row,"action_values",WEBSITE_PURCHASE) if row.get("account_currency") else None
                      for row in rows]
    totals = {}
    for name, series in (("website_purchases",purchases),("website_leads",leads),("website_purchase_value",purchase_value)):
        known = [item for item in series if item is not None]
        complete = bool(rows) and len(known)==len(rows) and coverage=="COMPLETE"
        subtotal = format(sum(known,Decimal(0)),"f") if known else None
        if complete:
            status = "VALUE"
        elif coverage=="PARTIAL" or known:
            status = "PARTIAL"
        else:
            status = "MISSING" if rows else "EMPTY"
        totals[name] = {"status":status,"value":subtotal if complete else None,"known_subtotal":subtotal}

    def ratio(numerator, denominator, reason=None):
        if reason is None:
            if numerator is None or denominator is None:
                reason = "COMPLETE_NUMERATOR_AND_DENOMINATOR_REQUIRED"
            elif Decimal(denominator)==0:
                reason = "ZERO_DENOMINATOR"
        result = format(Decimal(numerator)/Decimal(denominator),".4f") if reason is None else None
        return {"status":"VALUE" if result is not None else "UNKNOWN","value":result,"reason":reason}

    currency_unchecked = any(not row.get("account_currency") for row in rows)
    money_reason = "ROW_CURRENCY_REQUIRED" if currency_unchecked else None
    count = totals["website_purchases"]["value"]
    amount = totals["website_purchase_value"]["value"]
    contradictory = any(count == 0 and amount is not None and amount>0 for count,amount in zip(purchases,purchase_value))
    roas_reason = money_reason
    if roas_reason is None and contradictory:
        roas_reason = "PURCHASE_COUNT_VALUE_CONFLICT"
    elif roas_reason is None and count is None:
        roas_reason = "COMPLETE_PURCHASE_COUNT_REQUIRED"
    totals["website_purchase_cpa"] = ratio(spend,count,money_reason)
    totals["website_lead_cpl"] = ratio(spend,totals["website_leads"]["value"],money_reason)
    totals["website_purchase_roas"] = ratio(amount,spend,roas_reason)
    return totals


def live_metric(record,as_of,max_age_seconds):
    keys=("metric","object_id","state","value","period","observed_at","data_as_of","bucket_end_time")
    object_only(record,keys,("metric","object_id","state","period","observed_at","data_as_of"))
    if record["metric"] not in METRICS:fail("LIVE_METRIC_UNSUPPORTED")
    if isinstance(record["object_id"],str) and not record["object_id"].startswith("SIM_") and "_" in record["object_id"]:
        post_identifier(record["object_id"])
    else:
        identifier(record["object_id"])
    if record["state"] not in ("VALUE","EMPTY","MISSING","ERROR","NOT_RUN","UNSUPPORTED"):fail("METRIC_STATE")
    observed=aware_time(record["observed_at"]);current=aware_time(as_of)
    if observed>current:fail("OBSERVATION_IN_FUTURE")
    period=record["period"]
    if period is not None and (type(period) is not str or not re.fullmatch(r"[a-z_]{1,32}",period)):fail("INVALID_PERIOD")
    if record.get("bucket_end_time") is not None:aware_time(record["bucket_end_time"])
    value=None
    if record["state"]=="VALUE":
        if period is None:fail("VALUE_PERIOD_UNVERIFIED")
        raw=record.get("value")
        if type(raw) is dict:
            if not raw or len(raw)>20:fail("INVALID_STRUCTURED_METRIC")
            value={}
            for key,item in raw.items():
                if type(key) is not str or not re.fullmatch(r"[a-z_]{1,32}",key):fail("INVALID_METRIC_DIMENSION")
                value[key]=numeric_string(item,integer=True)
        else:value=numeric_string(raw,integer=True)
    elif "value" in record:fail("NON_VALUE_MUST_OMIT_VALUE")
    freshness=t0.freshness(record["data_as_of"],as_of,max_age_seconds)
    if record["data_as_of"] is not None and aware_time(record["data_as_of"])>observed:freshness="CLOCK_SKEW"
    return {**{key:record.get(key) for key in keys if key!="value"},"value":value,"freshness":freshness,
            "as_of":as_of,"max_age_seconds":max_age_seconds,"max_age_basis":"LOCAL_USER_THRESHOLD_NOT_PLATFORM_SLA",
            "causal_claim_allowed":False,"status_is_live_end_signal":False}


def compare_live_metrics(previous,current,threshold_fraction="0.20"):
    for key in ("metric","object_id","period"):
        if previous[key]!=current[key]:fail("METRIC_CONTEXT_MISMATCH")
    if any(x["state"]!="VALUE" or x["freshness"]!="FRESH" or type(x["value"]) is not str for x in (previous,current)):
        return {"signal":"INSUFFICIENT_DATA","causal_claim_allowed":False}
    if aware_time(current["data_as_of"])<=aware_time(previous["data_as_of"]):
        return {"signal":"SOURCE_NOT_ADVANCED","causal_claim_allowed":False}
    return {"signal":t0.anomaly(current["value"],previous["value"],threshold_fraction,"FRESH"),
            "causal_claim_allowed":False,"next_step":"复核同口径来源与内容变化；不据此自动调预算"}
