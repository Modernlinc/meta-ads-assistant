"""Resolve historical Meta instructions against a reviewed first-party source."""
from datetime import date, timedelta

from .commerce_csv import day
from .operator_review import evidence_text, pattern
from .validation import fail, object_only


SOURCES = {
    ("AD_CREATION", "ADS_MANAGER_WEB", "US"): "https://secure.facebook.com/business/ads",
    ("AD_CREATION", "ADS_MANAGER_WEB", "GB"): "https://en-gb.facebook.com/business/ads",
}
CHECKED = date(2026, 11, 26)
STEPS = (
    "进入具名广告账户的 Ads Manager，打开该账户提供的广告创建流程。",
    "按业务目标检查目标、受众、版位、预算和素材格式。",
    "将预算、日期和素材整理成待审批草案。",
)


def review(document):
    keys = ("schema", "task", "surface", "market", "qualification", "as_of", "historical")
    object_only(document, keys, keys)
    if document["schema"] != "meta-guidance-review-v1":
        fail("META_GUIDANCE_SCHEMA")
    task = pattern(document["task"], r"[A-Z_]{1,40}")
    surface = pattern(document["surface"], r"[A-Z_]{1,40}")
    market = pattern(document["market"], r"[A-Z]{2}")
    qualification = document["qualification"]
    if qualification not in ("CONFIRMED", "INELIGIBLE", "UNKNOWN"):
        fail("META_GUIDANCE_QUALIFICATION_INVALID")
    historical = document["historical"]
    keys = ("title", "published_on", "ui", "instruction")
    object_only(historical, keys, keys)
    record = {key: evidence_text(historical[key]) for key in ("title", "ui", "instruction")}
    record["published_on"] = day(historical["published_on"])
    as_of = date.fromisoformat(day(document["as_of"]))
    if date.fromisoformat(record["published_on"]) > as_of:
        fail("META_GUIDANCE_HISTORICAL_DATE_INVALID")
    url = SOURCES.get((task, surface, market))
    valid = url and qualification == "CONFIRMED" and CHECKED <= as_of <= CHECKED + timedelta(days=90)
    source = None if not url else {
        "url": url, "checked_on": CHECKED.isoformat(), "market": market,
        "ui_version": "ADS_MANAGER_WEB_PUBLIC_GUIDANCE", "api_version": None,
        "required_qualification": "具名广告账户及可使用的广告身份",
        "qualification_basis": "CALLER_DECLARED", "account_ui_verified": False,
    }
    return dict(status="OFFICIAL_GUIDANCE_AVAILABLE" if valid else "CONFLICT_UNRESOLVED",
                historical=dict(record, state="HISTORICAL_ONLY"), official_source=source,
                steps=list(STEPS) if valid else [], requests_sent=0, actions_authorized=False)
