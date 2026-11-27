"""Offline operator commands exposed through the regular installed runner."""
from pathlib import Path
import re

from .validation import fail, strict_json


CATALOG = Path(__file__).resolve().parents[2] / "references" / "operator-knowledge.json"
TOPICS = ("strategy", "creative", "tracking", "diagnosis", "commerce", "competitors")
PLATFORMS = ("meta", "shopify", "qianchuan", "google-ads", "tiktok-ads", "linkedin-ads")


def add_commands(subparsers):
    knowledge = subparsers.add_parser("knowledge-search", help="检索有来源的策略与视频目录")
    knowledge.add_argument("--topic", choices=(*TOPICS, "all"), required=True)
    knowledge.add_argument("--video-id")
    knowledge.add_argument("--batch01", action="store_true", help="读取批次01知识或定位方法正文")
    knowledge.add_argument("--card-id", help="与 --batch01 一起读取已核验的完整方法正文")
    knowledge.add_argument("--notes", action="store_true", help="分页检查来源笔记索引")
    knowledge.add_argument("--query", help="按来源编号、视频名称或笔记关键词检索索引")
    knowledge.add_argument("--offset", type=int, default=0)
    knowledge.add_argument("--limit", type=int, default=50)
    meta = subparsers.add_parser("meta-guidance-review", help="按地区和账户条件核对旧界面与官方流程")
    meta.add_argument("--input", type=Path, required=True)
    access = subparsers.add_parser("meta-access-review", help="核对具名用途需要的权限、资质和审批来源")
    access.add_argument("--input", type=Path, required=True)
    media = subparsers.add_parser("media-analyze", help="核对本机广告素材、抽帧和字幕证据")
    media.add_argument("--file", type=Path, required=True)
    media.add_argument("--output", type=Path, required=True)
    media.add_argument("--subtitles", type=Path)
    media.add_argument("--model", type=Path)
    media.add_argument("--model-sha256")
    media.add_argument("--language", default="auto")
    media.add_argument("--fallback-subtitles", type=Path)
    media.add_argument("--observations", type=Path)
    media.add_argument("--concept-id", help="为现有创意概念导出工作区审阅附件")
    for name, help_text in (
        ("strategy-review", "比较广告策略，核对单位经济和试验预算"),
        ("performance-diagnose", "比较可比报表并保留数据缺口"),
        ("tracking-audit", "核对本地 Pixel/CAPI 事件证据和 UTM"),
        ("business-readiness", "合并并核验商品、优惠、落地页和成本佐证"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--input", type=Path, required=True)
    commerce = subparsers.add_parser("commerce-reconcile", help="核对具名店铺的订单、退款及广告费用 CSV")
    for column in ("orders", "refunds", "spend"):
        commerce.add_argument("--" + column, type=Path, required=True)
    for scope in ("store-id", "account-id", "currency", "since", "until"):
        commerce.add_argument("--" + scope, required=True)
    commerce.add_argument("--refund-basis", choices=("event", "order"), default="event")
    commerce.add_argument("--output", type=Path)
    accounts = subparsers.add_parser("multi-account-review", help="对照同一工作区内各账户的只读报表")
    accounts.add_argument("--workspace", type=Path, required=True)
    accounts.add_argument("--receipt", action="append", type=Path, required=True)
    accounts.add_argument("--account-id", action="append")
    account_plan = subparsers.add_parser("multi-account-plan", help="将多个已登记账户拆成独立查询范围")
    account_plan.add_argument("--workspace", type=Path, required=True)
    account_plan.add_argument("--account-id", action="append", required=True)
    account_plan.add_argument("--last-complete-days", type=int, required=True)
    inventory = subparsers.add_parser("account-inventory-review", help="在具名账户的已保存快照内筛选对象")
    inventory.add_argument("--workspace", type=Path, required=True)
    inventory.add_argument("--monitor", type=Path, required=True)
    inventory.add_argument("--account-id", required=True)
    inventory.add_argument("--kind", choices=("campaigns", "adsets", "ads"), required=True)
    inventory.add_argument("--id", action="append")
    inventory.add_argument("--parent-id")
    inventory.add_argument("--parent-kind", choices=("campaign", "adset"))
    insights = subparsers.add_parser("account-insights-slice", help="从具名账户的原报表核同一广告或父级指标")
    insights.add_argument("--workspace", type=Path, required=True)
    insights.add_argument("--receipt", type=Path, required=True)
    insights.add_argument("--account-id", required=True)
    insights.add_argument("--kind", choices=("account", "campaign", "adset", "ad"), required=True)
    insights.add_argument("--id")
    compare = subparsers.add_parser("creative-compare", help="离线对照同一工作区的多个创意概念")
    compare.add_argument("--workspace", type=Path, required=True)
    compare.add_argument("--account-id", required=True)
    compare.add_argument("--concept-id", action="append", required=True)
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--media-report", type=Path, action="append", default=[])
    page_export = subparsers.add_parser("page-live-export-review", help="按登记 Page 核对直播汇总导出")
    page_export.add_argument("--workspace", type=Path, required=True)
    page_export.add_argument("--input", type=Path, required=True)
    evidence = subparsers.add_parser("workspace-evidence", help="按账户查看或修订本机复盘证据")
    evidence.add_argument("--workspace", type=Path, required=True)
    evidence.add_argument("--account-id", required=True)
    evidence.add_argument("--input", type=Path)
    evidence.add_argument("--plan-dir", type=Path)
    shopify = subparsers.add_parser("shopify-read-preview", help="核对店铺、订单日期与只读权限声明")
    shopify.add_argument("--input", type=Path, required=True)
    adapter = subparsers.add_parser("shopify-read-plan", help="预览授权接入所需的固定只读 GraphQL 查询与范围")
    adapter.add_argument("--input", type=Path, required=True)
    shopify_review = subparsers.add_parser("shopify-read-review", help="解码具名 Shopify Admin GraphQL 去标识回包")
    shopify_review.add_argument("--input", type=Path, required=True)
    shopify_review.add_argument("--spend", type=Path)
    shopify_review.add_argument("--account-id")
    shopify_review.add_argument("--spend-timezone")
    shopify_review.add_argument("--minor-digits", type=int)
    partner = subparsers.add_parser("partner-brief-review", help="整理其他经营模块提供的具名佐证")
    partner.add_argument("--input", type=Path, required=True)
    detail = subparsers.add_parser("public-ad-inspect", help="按 Page 和广告双编号核对已有竞品观察")
    detail.add_argument("--input", type=Path, required=True)
    detail.add_argument("--page-id", required=True)
    detail.add_argument("--archive-id", required=True)
    detail.add_argument("--media-report", type=Path)
    detail.add_argument("--angle-hypothesis")
    routing = subparsers.add_parser("operator-route", help="查看指定平台可用的本地入口及授权边界")
    routing.add_argument("--platform", choices=PLATFORMS, required=True)
    watch = subparsers.add_parser("commerce-watch", help="本地有界对账任务，可选择日或周")
    watch.add_argument("--input", type=Path, required=True)
    watch.add_argument("--state-dir", type=Path, required=True)
    watch.add_argument("--cycles", type=int, default=1)
    watch.add_argument("--interval-seconds", type=int, default=60)
    watch.add_argument("--runtime-seconds", type=int, default=300)
    watch.add_argument("--retry-count", type=int, default=1)
    watch.add_argument("--resume", action="store_true")
    for name, help_text in (("commerce-watch-stop", "请求停止本机对账任务"),
                            ("commerce-watch-report", "读取本机对账任务的最近报告与新鲜度")):
        action = subparsers.add_parser(name, help=help_text)
        action.add_argument("--state-dir", type=Path, required=True)


def run_review(command, source):
    from .operator_review import business_readiness, load_input, performance_diagnose, strategy_review, tracking_audit

    contracts = {
        "strategy-review": strategy_review,
        "performance-diagnose": performance_diagnose,
        "tracking-audit": tracking_audit,
        "business-readiness": business_readiness,
    }
    return contracts[command](load_input(source))


def knowledge_search(topic, video_id=None, *, notes=False, query=None, offset=0, limit=50):
    if video_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        fail("KNOWLEDGE_VIDEO_ID_INVALID")
    doc = strict_json(CATALOG.read_text(encoding="utf-8"))
    if doc["schema"] != "operator-knowledge-v1":
        fail("KNOWLEDGE_CATALOG_SCHEMA")
    video = doc["videos"].get(video_id)
    if video_id is not None and video is None:
        fail("KNOWLEDGE_VIDEO_NOT_FOUND")
    # The original catalog membership is independent of whether a later note exists.
    claims = [
        row for row in doc["claims"]
        if (topic == "all" or topic in row["topics"])
        and (video_id is None or row["video_id"] in (None, video_id))
    ]
    result = {
        "status": "LOCAL_KNOWLEDGE_MATCHED",
        "coverage": doc["coverage"],
        "claims": claims,
        "video": video,
        "note_index": doc.get("note_index", {}).get(video_id, []) if video_id else [],
        "actions_authorized": False,
    }
    if notes or query is not None:
        if not 0 <= offset <= 10000 or not 1 <= limit <= 100:
            fail("KNOWLEDGE_NOTE_PAGINATION_INVALID")
        if query is not None and (not query.strip() or len(query) > 80 or not query.isprintable()):
            fail("KNOWLEDGE_NOTE_QUERY_INVALID")
        phrase = query.casefold().strip() if query else ""
        hit = []
        for ref, records in doc.get("note_index", {}).items():
            if video_id is not None and ref != video_id:
                continue
            video_meta = doc["videos"][ref]
            common = f"{ref} {video_meta.get('creator')} {video_meta.get('title')}"
            for row in records:
                if not phrase or phrase in " ".join((common, *map(str, row.values()))).casefold():
                    hit.append(dict(row, video_id=ref, transcript_verified=False))
        result.update(note_page=hit[offset:offset + limit], note_matches=len(hit),
                      next_offset=offset + limit if offset + limit < len(hit) else None,
                      note_basis="SOURCE_POINTERS_FILTERED_INDEPENDENTLY_OF_CLAIM_TOPIC")
    return result


def operator_route(platform):
    if platform == "meta":
        scope, entry = "NAMED_META_WORKSPACE", "workspace-route"
    elif platform == "shopify":
        scope, entry = "LOCAL_CSV_AND_READ_SCOPE_PREVIEW", "shopify-read-preview"
    else:
        scope, entry = "SEPARATE_PLATFORM_REQUIRED", None
    return dict(status="OPERATOR_ROUTE", platform=platform, scope=scope, customer_entry=entry,
                credentials_accessed=False, requests_sent=0)
