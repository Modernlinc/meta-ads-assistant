"""GET request/response contract with a deterministic, network-free transport.

An optional dormant HTTP interface requires a separate secure broker. Scope flags describe reviewed metadata;
they are not OAuth grants or evidence of ownership. All fixtures are synthetic.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
import re
from .validation import InputError, aware_time, fail


INSIGHTS_FIELDS = ("account_id", "ad_id", "date_start", "date_stop", "spend", "impressions", "clicks")
INSIGHTS_MAX_DAYS = 31
PARENT_ID_FIELDS = ("campaign_id", "adset_id")
WEBSITE_FIELDS = ("account_currency", "actions", "action_values")
CONVERSION_INSIGHTS = "insights_website_conversions"
HIERARCHY_INSIGHTS = "insights_hierarchy"
CONVERSION_HIERARCHY_INSIGHTS = "insights_website_hierarchy"
INSIGHTS_OPERATIONS = ("insights", CONVERSION_INSIGHTS, HIERARCHY_INSIGHTS, CONVERSION_HIERARCHY_INSIGHTS)
PLACEMENT_INSIGHTS = "placement_insights"
PLACEMENT_DIMENSIONS = ("publisher_platform", "platform_position")
DATED_INSIGHTS_OPERATIONS = (*INSIGHTS_OPERATIONS, PLACEMENT_INSIGHTS)
WEBSITE_INSIGHTS_OPERATIONS = (CONVERSION_INSIGHTS, CONVERSION_HIERARCHY_INSIGHTS)
HIERARCHY_INSIGHTS_OPERATIONS = (HIERARCHY_INSIGHTS, CONVERSION_HIERARCHY_INSIGHTS)
RECENT_LIVE_COMMENTS = "recent_live_comments"
COMMENT_OPERATIONS = ("comments", RECENT_LIVE_COMMENTS)
CLICK_WINDOW = "7d_click"
WEBSITE_PURCHASE = "offsite_conversion.fb_pixel_purchase"
WEBSITE_LEAD = "offsite_conversion.fb_pixel_lead"
ACTION_DIMENSIONS = {
    "action_device", "conversion_destination", "matched_persona_id", "matched_persona_name", "signal_source_bucket",
    "standard_event_content_type", "action_canvas_component_name", "action_carousel_card_id",
    "action_carousel_card_name", "action_destination", "action_reaction", "action_target_id",
    "action_video_sound", "action_video_type", "is_business_ai_assisted",
}

FIELDS = {
    "account": ("id", "account_id", "currency", "timezone_name", "account_status"),
    "campaigns": ("id", "account_id", "status", "effective_status"),
    "adsets": ("id", "account_id", "campaign_id", "status", "effective_status"),
    "ads": ("id", "account_id", "campaign_id", "adset_id", "status", "effective_status"),
    "ad_videos": ("id", "account_id", "creative"),
    "insights": INSIGHTS_FIELDS,
    CONVERSION_INSIGHTS: (*INSIGHTS_FIELDS, *WEBSITE_FIELDS),
    HIERARCHY_INSIGHTS: (*INSIGHTS_FIELDS, *PARENT_ID_FIELDS),
    CONVERSION_HIERARCHY_INSIGHTS: (*INSIGHTS_FIELDS, *WEBSITE_FIELDS, *PARENT_ID_FIELDS),
    PLACEMENT_INSIGHTS: ("account_id", "account_currency", "date_start", "date_stop", "spend", "impressions", "clicks"),
    "page": ("id",),
    "live": ("id", "from", "status", "video", "broadcast_start_time"),
    "live_post": ("id", "from", "attachments"),
    "comments": ("id", "created_time"),
    RECENT_LIVE_COMMENTS: ("id", "created_time"),
}
EDGES = {"campaigns", "adsets", "ads", "ad_videos", *DATED_INSIGHTS_OPERATIONS, *COMMENT_OPERATIONS}
LIVE_PENDING = {"live", "comments", RECENT_LIVE_COMMENTS, "live_post", "post_insights", "video_insights"}
LIVE_END_STATES = ("LIVE_STOPPED", "VOD")
LIVE_STATUSES = ("LIVE", *LIVE_END_STATES, "SCHEDULED_UNPUBLISHED", "SCHEDULED_LIVE", "SCHEDULED_CANCELED")
STATES = {"ACTIVE", "PAUSED", "DELETED", "ARCHIVED", "IN_PROCESS", "WITH_ISSUES", "PENDING_REVIEW",
          "DISAPPROVED", "PREAPPROVED", "PENDING_BILLING_INFO", "CAMPAIGN_PAUSED", "ADSET_PAUSED"}


def identifier(value):
    if type(value) is not str or not re.fullmatch(r"(?:[0-9]{1,40}|SIM_[A-Z0-9_]{1,60})", value):
        fail("INVALID_OBJECT_ID")
    return value


def positive_integer(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        fail("INVALID_BUDGET")
    return value


def post_identifier(value, page_id=None):
    """Names a Page post, including the Page prefix of numeric Graph IDs."""
    if type(value) is not str:
        fail("INVALID_PAGE_POST_ID")
    if re.fullmatch(r"[1-9][0-9]{0,39}_[1-9][0-9]{0,39}", value):
        if page_id is not None and value.split("_",1)[0] != page_id:
            fail("PAGE_POST_SCOPE_MISMATCH")
    elif not (re.fullmatch(r"SIM_[A-Z0-9_]{1,80}", value)
              and (page_id is None or page_id.startswith("SIM_"))):
        fail("INVALID_PAGE_POST_ID")
    return value


def numeric_string(value, integer=False):
    if type(value) is not str or not re.fullmatch(r"[0-9]{1,24}(?:\.[0-9]{1,12})?",value):
        fail("INVALID_NUMERIC_FIELD")
    number = Decimal(value)
    if integer and number != number.to_integral_value():
        fail("INVALID_COUNT")
    return value


@dataclass(frozen=True)
class ReadPlan:
    operation: str
    object_id: str
    account_id: str | None = None
    page_id: str | None = None
    since: str | None = None
    until: str | None = None
    limit: int = 10
    version: str = "v27.0"
    reviewed_live_contract: bool = False

    def __post_init__(self):
        if type(self.operation) is not str or self.operation not in FIELDS or self.version != "v27.0":
            fail("UNSUPPORTED_OPERATION_OR_VERSION")
        if type(self.reviewed_live_contract) is not bool:
            fail("INVALID_REVIEW_FLAG")
        if self.operation=="live_post":
            post_identifier(self.object_id,identifier(self.page_id))
        else:
            identifier(self.object_id)
        positive_integer(self.limit,100)
        if self.operation in ("account", "campaigns", "adsets", "ads", "ad_videos", *DATED_INSIGHTS_OPERATIONS):
            if self.page_id is not None: fail("UNUSED_SCOPE_FIELD")
            identifier(self.account_id)
            if self.object_id != self.account_id:
                fail("ACCOUNT_SCOPE_MISMATCH")
        elif self.operation == "page":
            if self.account_id is not None: fail("UNUSED_SCOPE_FIELD")
            identifier(self.page_id)
            if self.object_id != self.page_id:
                fail("PAGE_SCOPE_MISMATCH")
        else:
            if self.account_id is not None: fail("UNUSED_SCOPE_FIELD")
            identifier(self.page_id)
            if self.reviewed_live_contract is not True:
                fail("LIVE_CONTRACT_NOT_VERIFIED")
        if self.operation in DATED_INSIGHTS_OPERATIONS:
            if any(type(x) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}",x) for x in (self.since,self.until)):
                fail("INVALID_DATE_RANGE")
            try:
                start, stop = date.fromisoformat(self.since), date.fromisoformat(self.until)
                if start > stop or (stop-start).days >= INSIGHTS_MAX_DAYS:
                    fail("DATE_RANGE_OUTSIDE_LOCAL_BUDGET")
            except (ValueError,TypeError):
                fail("INVALID_DATE_RANGE")
        elif self.operation == RECENT_LIVE_COMMENTS and self.since is not None and self.until is None:
            if type(self.since) is not str or not re.fullmatch(
                    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",self.since):
                fail("INVALID_COMMENT_SINCE")
            aware_time(self.since)
        elif self.since is not None or self.until is not None:
            fail("DATE_RANGE_NOT_SUPPORTED")

    def request(self, after=None):
        node = ("act_" + self.account_id) if self.account_id is not None else self.object_id
        edge = "/"+self.operation if self.operation in EDGES else ""
        if self.operation == 'ad_videos': edge = '/ads'
        if self.operation in DATED_INSIGHTS_OPERATIONS: edge = '/insights'
        if self.operation == RECENT_LIVE_COMMENTS: edge = '/comments'
        params={"fields":",".join(FIELDS[self.operation])}
        if self.operation=="live_post":
            params["fields"] = "id,from{id},attachments.limit(2){media_type,target{id}}"
        if self.operation == 'ad_videos': params['fields'] = 'id,account_id,creative{id,video_id}'
        if self.operation in EDGES: params["limit"]=self.limit
        if self.operation == RECENT_LIVE_COMMENTS:
            params.update(order="reverse_chronological", filter="toplevel")
            if self.since is not None: params["since"] = self.since
        if self.operation in INSIGHTS_OPERATIONS:
            params.update(level="ad", time_range={"since":self.since,"until":self.until},
                          time_increment=1, action_report_time="impression")
            if self.operation in WEBSITE_INSIGHTS_OPERATIONS:
                params.update(action_attribution_windows=[CLICK_WINDOW], action_breakdowns=["action_type"],
                              use_unified_attribution_setting=False, use_account_attribution_setting=False)
            else:
                params["use_unified_attribution_setting"] = True
        if self.operation == PLACEMENT_INSIGHTS:
            params.update(level="account", time_range={"since":self.since,"until":self.until},
                          time_increment=1, breakdowns=list(PLACEMENT_DIMENSIONS))
        if after is not None:
            if type(after) is not str or not re.fullmatch(r"[A-Za-z0-9_=-]{1,2048}",after):
                fail("INVALID_CURSOR")
            params["after"]=after
        return {"method":"GET", "path":"/"+self.version+"/"+node+edge, "params":params}


@dataclass(frozen=True, kw_only=True)
class PageLiveReadPlan(ReadPlan):
    """A LiveVideo read pinned to a previously declared backing Video."""

    expected_live_video_id: str

    def __post_init__(self):
        super().__post_init__()
        if self.operation != 'live': fail('LIVE_VIDEO_SCOPE_REQUIRES_LIVE_READ')
        identifier(self.expected_live_video_id)


@dataclass
class NormalizedPage:
    rows: list
    state: str
    error_code: str | None = None
    cursor: str | None = field(default=None,repr=False)
    has_next: bool = False


AUTH_SESSION_EXPIRED_OR_INVALID = 'AUTH_SESSION_EXPIRED_OR_INVALID'
GRAPH_AUTH_ERRORS = ('AUTHENTICATION', AUTH_SESSION_EXPIRED_OR_INVALID)


def error_category(status, body):
    # Never echo raw error messages/URLs/headers. A code100 object error remains
    # ambiguous (ID, visibility, version, or parameter), not 'permission denied'.
    if status == 429: return "RATE_LIMIT"
    if 500 <= status < 600: return "SERVICE_UNAVAILABLE"
    error = body.get("error")
    code = error.get("code") if type(error) is dict else None
    code = code if type(code) is int else -1
    if code in (102,190):
        subcode = error.get("error_subcode")
        if type(subcode) is int and subcode == 463:
            return AUTH_SESSION_EXPIRED_OR_INVALID
        return "AUTHENTICATION"
    if status == 401: return "AUTHENTICATION"
    if code in (4,17,32,613,80000,80001,80002,80003,80004,80005,80006,80008): return "RATE_LIMIT"
    if code == 10 or 200 <= code <= 299 or status == 403: return "PERMISSION"
    if code == 100 or status == 404: return "OBJECT_OR_PARAMETER"
    if code in (1,2): return "SERVICE_UNAVAILABLE"
    return "UNCLASSIFIED_ERROR"


def normalize_post_attachment(value):
    if type(value) is not dict or type(value.get("data")) is not list or len(value["data"])!=1:
        fail("LIVE_POST_VIDEO_UNVERIFIED")
    paging = value.get("paging", {})
    if type(paging) is not dict or paging.get("next") is not None:
        fail("LIVE_POST_VIDEO_UNVERIFIED")
    attachment = value["data"][0]
    if type(attachment) is not dict or attachment.get("media_type")!="video" or type(attachment.get("target")) is not dict:
        fail("LIVE_POST_VIDEO_UNVERIFIED")
    return {"data":[{"media_type":"video","target":{"id":identifier(attachment["target"].get("id"))}}]}


def normalize_action_stats(value, field):
    if type(value) is not list or len(value)>200:
        fail("INSIGHTS_ACTION_STATS_INVALID")
    allowed = (WEBSITE_PURCHASE, WEBSITE_LEAD) if field=="actions" else (WEBSITE_PURCHASE,)
    seen = set()
    entries = []
    for item in value:
        if type(item) is not dict or type(item.get("action_type")) is not str:
            fail("INSIGHTS_ACTION_STATS_INVALID")
        action_type = item["action_type"]
        if action_type not in allowed:
            continue
        if action_type in seen or ACTION_DIMENSIONS.intersection(item):
            fail("INSIGHTS_ACTION_STATS_AMBIGUOUS")
        seen.add(action_type)
        if item.get(CLICK_WINDOW) is not None:
            entries.append({"action_type":action_type, CLICK_WINDOW:numeric_string(item[CLICK_WINDOW])})
    return sorted(entries, key=lambda item: item["action_type"])


def row_identity(plan, row):
    if plan.operation == PLACEMENT_INSIGHTS:
        return row["date_start"], *(row[key] for key in PLACEMENT_DIMENSIONS)
    if plan.operation in INSIGHTS_OPERATIONS:
        return row["ad_id"], row["date_start"], row["date_stop"]
    return row["id"]


def placement_dimension(value):
    if type(value) is not str or not re.fullmatch(r"[a-z0-9_]{1,40}", value):
        fail("PLACEMENT_GRAIN_REQUIRED")
    return value


def normalize_row(plan,row):
    if type(row) is not dict: fail("INVALID_ROW")
    output={}
    # Unknown response fields are deliberately dropped, including names,
    # message/from(author), credentials and arbitrary nested expansions.
    for key in FIELDS[plan.operation]:
        if key not in row or row[key] is None: continue
        value=row[key]
        if key in ("id","account_id","ad_id","campaign_id","adset_id"):
            if key=="id" and plan.operation=="account":
                if value != "act_"+plan.account_id: fail("RETURNED_ACCOUNT_MISMATCH")
            elif key=="id" and plan.operation=="live_post": post_identifier(value,plan.page_id)
            elif (key=='id' and plan.operation in COMMENT_OPERATIONS and type(value) is str
                  and re.fullmatch(r'[0-9]{1,40}_[0-9]{1,40}',value)):
                # Keep compound response identities only as deduplication keys.
                # Neither segment provides ownership or another request target.
                pass
            else: identifier(value)
        elif key in ("spend","impressions","clicks"):
            numeric_string(value,integer=key!="spend")
        elif key in ("created_time","broadcast_start_time"):
            aware_time(value)
        elif key in ("from","video"):
            if type(value) is not dict or "id" not in value: fail("INVALID_RELATION")
            value={"id":identifier(value["id"])}
        elif key == 'attachments':
            value = normalize_post_attachment(value)
        elif key == 'creative':
            if type(value) is not dict or 'id' not in value: fail('INVALID_CREATIVE_RELATION')
            clean = {'id': identifier(value['id'])}
            if value.get('video_id') is not None: clean['video_id'] = identifier(value['video_id'])
            value = clean
        elif key=="account_status":
            if type(value) is not int or not 0<=value<=0xFFFFFFFF: fail("INVALID_ACCOUNT_STATUS")
        elif key in ("currency", "account_currency"):
            if type(value) is not str or not re.fullmatch(r"[A-Z]{3}",value): fail("INVALID_CURRENCY")
        elif key in ("actions", "action_values"):
            value = normalize_action_stats(value,key)
        elif key=="timezone_name":
            if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_+/-]{1,80}",value): fail("INVALID_TIMEZONE")
        elif key in ("date_start","date_stop"):
            try: date.fromisoformat(value)
            except (TypeError,ValueError): fail("INVALID_DATE_FIELD")
        elif key in ("status","effective_status"):
            allowed=LIVE_STATUSES if plan.operation=="live" else STATES
            if type(value) is not str or value not in allowed: fail("UNRECOGNIZED_STATUS")
        output[key]=value
    if plan.operation in ('account','page') and ('id' not in output or plan.operation=='account' and 'account_id' not in output):
        fail('ROW_ID_MISSING')
    if plan.account_id:
        if output.get("account_id") != plan.account_id: fail("RETURNED_ACCOUNT_MISMATCH")
    if plan.operation in ("page","live","live_post") and output.get("id")!=plan.object_id:
        fail("RETURNED_OBJECT_MISMATCH")
    if plan.operation=="live" and output.get("from",{}).get("id")!=plan.page_id:
        fail("LIVE_OWNER_UNVERIFIED")
    if type(plan) is PageLiveReadPlan:
        video = output.get('video', {}).get('id')
        if video is None: fail('LIVE_VIDEO_RELATION_UNVERIFIED')
        if video != plan.expected_live_video_id: fail('LIVE_VIDEO_RELATION_MISMATCH')
    if plan.operation=="live_post":
        if output.get("from",{}).get("id")!=plan.page_id:
            fail("LIVE_POST_OWNER_UNVERIFIED")
        if "attachments" not in output:
            fail("LIVE_POST_VIDEO_UNVERIFIED")
    if plan.operation == PLACEMENT_INSIGHTS:
        for key in PLACEMENT_DIMENSIONS:
            output[key] = placement_dimension(row.get(key))
        start, stop = output.get("date_start"), output.get("date_stop")
        if start is None or start != stop or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", start):
            fail("PLACEMENT_GRAIN_REQUIRED")
        if not plan.since <= start <= plan.until:
            fail("RETURNED_DATE_OUTSIDE_SCOPE")
    elif plan.operation in INSIGHTS_OPERATIONS:
        if not output.get("ad_id") or output.get("date_start") is None or output.get("date_stop") is None:
            fail("INSIGHTS_GRAIN_MISSING")
        if not plan.since <= output["date_start"] <= output["date_stop"] <= plan.until:
            fail("RETURNED_DATE_OUTSIDE_SCOPE")
    elif not output.get("id"):
        fail("ROW_ID_MISSING")
    if plan.operation == RECENT_LIVE_COMMENTS and plan.since is not None:
        if 'created_time' not in output: fail("COMMENT_CREATION_TIME_MISSING")
        if aware_time(output['created_time']) < aware_time(plan.since): fail("RETURNED_DATE_OUTSIDE_SCOPE")
    return output


def normalize_page(plan, http_status, body):
    if type(http_status) is not int or not 100<=http_status<600 or type(body) is not dict:
        fail("INVALID_RESPONSE_ENVELOPE")
    if "error" in body or not 200<=http_status<300:
        return NormalizedPage([],"ERROR",error_category(http_status,body))
    if plan.operation in EDGES:
        if type(body.get("data")) is not list: fail("DATA_ARRAY_REQUIRED")
        if len(body["data"])>plan.limit: fail("RESPONSE_EXCEEDS_PAGE_BUDGET")
        raw_rows=body["data"]
    else:
        raw_rows=[body]
    rows=[normalize_row(plan,row) for row in raw_rows]
    paging=body.get("paging",{})
    if type(paging) is not dict: fail("INVALID_PAGING")
    has_next="next" in paging and paging["next"] is not None
    cursor=None
    if has_next:
        if plan.operation not in EDGES: fail("UNEXPECTED_PAGING")
        cursors=paging.get("cursors",{})
        if type(cursors) is not dict or not cursors.get("after"): fail("NEXT_WITHOUT_CURSOR")
        cursor=cursors["after"]
        plan.request(cursor)  # validate opaque cursor; never follow paging.next
    return NormalizedPage(rows,"VALUE" if rows else "EMPTY",cursor=cursor,has_next=has_next)


class MockTransport:
    """Sequence-backed fixtures; refuses non-synthetic request paths."""
    def __init__(self,responses):
        self.responses=deepcopy(responses); self.calls=[]

    def get(self,request):
        if request["method"]!="GET" or not re.fullmatch(r"/v27\.0/(?:act_)?SIM_[A-Z0-9_]+(?:/(?:campaigns|adsets|ads|insights|comments|video_insights))?",request["path"]):
            fail("MOCK_SYNTHETIC_GET_ONLY")
        self.calls.append(deepcopy(request))
        if not self.responses: fail("MOCK_RESPONSES_EXHAUSTED")
        return self.responses.pop(0)


class ReadSession:
    """Bounded all-or-nothing page commits, no automatic retries on errors."""
    def __init__(self,plan,transport):
        from .http_read import GraphReadTransport
        if type(transport) not in (MockTransport, GraphReadTransport): fail("UNREVIEWED_TRANSPORT")
        if type(transport) is GraphReadTransport and plan != transport.plan: fail("READ_PLAN_BINDING_CHANGED")
        self.mode = "MOCK" if type(transport) is MockTransport else "READ_ONLY"
        self.plan=plan; self.transport=transport; self.rows={}; self.cursor=None
        self.cursors=set(); self.pages=0; self.state="NOT_RUN"; self.last_error=None

    def collect(self,page_budget=2):
        positive_integer(page_budget,20)
        if self.state in ("COMPLETE","EMPTY"): return self.report()
        if self.state=="ERROR": fail("EXPLICIT_REVIEW_REQUIRED")
        for _ in range(page_budget):
            try:
                status,body=self.transport.get(self.plan.request(self.cursor))
                page=normalize_page(self.plan,status,body)
                if page.state=="ERROR":
                    self.state="ERROR"; self.last_error=page.error_code; break
                if page.has_next and page.cursor in self.cursors: fail("CURSOR_LOOP")
                candidate=deepcopy(self.rows)
                for row in page.rows:
                    key=row_identity(self.plan,row)
                    if key in candidate and candidate[key]!=row: fail("DUPLICATE_ROW_CONFLICT")
                    candidate[key]=row
            except InputError as exc:
                self.state="ERROR";self.last_error=str(exc);break
            self.rows=candidate; self.pages+=1; self.cursor=page.cursor
            if page.has_next:
                self.cursors.add(page.cursor);self.state="GAP"
            else:
                self.state="COMPLETE" if self.rows else "EMPTY";break
        return self.report()

    def report(self):
        # Only sanitized rows; no opaque cursors, URLs, headers or raw messages.
        return {"mode":self.mode, "operation":self.plan.operation,"state":self.state,"pages":self.pages,
                "rows":deepcopy(list(self.rows.values())),"last_error":self.last_error,
                "coverage":"BOUNDED_OBSERVATION_NOT_GLOBAL_TOTAL", "real_connection_verified":False}
