"""Review aggregate metrics from one Page-owner export."""
from .analysis import METRICS
from .client_workspace import read, workspace_identity
from .operator_review import pattern, minor
from .read_adapter import post_identifier
from .validation import aware_time, fail, object_only


def review(workspace, document):
    keys = ("schema", "page_id", "post_id", "source", "since", "until", "observations")
    object_only(document, keys, keys)
    if document["schema"] != "page-live-export-v1" or document["source"] != "PAGE_OWNER_EXPORT":
        fail("PAGE_EXPORT_SCOPE")
    page = pattern(document["page_id"], r"[1-9][0-9]{0,39}")
    post_identifier(document["post_id"], page)
    profile = read(workspace, existing_only=True)
    if f"PAGE:{page}" not in profile["assets"]:
        fail("PAGE_EXPORT_PAGE_NOT_MAPPED")
    start, end = (aware_time(document[key]) for key in ("since", "until"))
    if start >= end or (end - start).days > 90:
        fail("PAGE_EXPORT_WINDOW_INVALID")
    samples = document["observations"]
    if type(samples) is not list or len(samples) > 300:
        fail("PAGE_EXPORT_SAMPLE_LIMIT")
    results = {}
    for sample in samples:
        fields = ("metric", "observed_at", "state", "value")
        object_only(sample, fields, fields)
        metric, state, value = (sample[key] for key in ("metric", "state", "value"))
        instant = aware_time(sample["observed_at"])
        if not start <= instant <= end or metric not in METRICS:
            fail("PAGE_EXPORT_SAMPLE_SCOPE")
        if state not in ("OBSERVED", "MISSING", "UNAVAILABLE") or (state == "OBSERVED") != (value is not None):
            fail("PAGE_EXPORT_SAMPLE_STATE")
        if value is not None and metric == "post_video_views_by_live_status":
            object_only(value, ("live", "vod"))
            if not value or any(minor(n) is None for n in value.values()):
                fail("PAGE_EXPORT_STRUCTURED_METRIC")
        else:
            minor(value)
        key = (instant, metric)
        normalized = dict(metric=metric, observed_at=instant.isoformat(), state=state, value=value)
        if results.setdefault(key, normalized) != normalized:
            fail("PAGE_EXPORT_SAMPLE_CONFLICT")
    return dict(status="PAGE_OWNER_EXPORT_REVIEWED", page_id=page, post_id=document["post_id"],
                workspace_identity_sha256=workspace_identity(profile), source="OPERATOR_SUPPLIED_AGGREGATE",
                observations=[results[key] for key in sorted(results)],
                absent_metrics=sorted(set(METRICS) - {key[1] for key in results}),
                verified_continuous_live=False, network_used=False, media_collected=False)
