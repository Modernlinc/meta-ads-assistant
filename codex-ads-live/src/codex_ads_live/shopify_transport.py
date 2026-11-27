"""Shopify-specific read binding. Credentials are leased only for a confirmed window."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import http.client
import re
import ssl
import sys
from time import sleep
from pathlib import Path
from uuid import uuid4

from . import client_workspace as workspace
from .http_read import arm_http_deadline, read_json_response
from .journal import Journal, canonical, digest
from .local_paths import checked_path
from .meta_write_mapping import _write_new
from .monitor_tools import new_directory
from .native_credentials import _mac_read, _windows_read
from .operator_review import pattern
from .shopify_graphql import Collector, VERSION, gid, query
from .shopify_readiness import preview
from .timezones import load_zone
from .validation import fail, object_only, strict_json

ACCESS_QUERY = (
    "query StoreAccess { shop { id myshopifyDomain currencyCode ianaTimezone } "
    "currentAppInstallation { accessScopes { handle } } }"
)
SHOP = r"[a-z0-9][a-z0-9-]{0,61}\.myshopify\.com"
PROFILE_FIELDS = ("store", "shop_id", "timezone", "currency", "reference")
BODY_FIELDS = ("schema", "workspace_sha256", "binding_sha256", "store_readout")


def validate_binding(value):
    object_only(value, PROFILE_FIELDS, PROFILE_FIELDS)
    pattern(value["store"], SHOP)
    gid(value["shop_id"], "Shop")
    pattern(value["timezone"], r"[A-Za-z0-9_+/.-]{1,100}")
    try:
        load_zone(value["timezone"])
    except (ValueError, KeyError):
        fail("SHOPIFY_TIMEZONE_INVALID")
    pattern(value["currency"], r"[A-Z]{3}")
    pattern(value["reference"], r"shop_[0-9a-f]{32}")


def bind(directory, store, shop_id, zone, currency):
    def change(profile):
        prior = profile.get("shopify_read_binding")
        candidate = dict(store=store, shop_id=shop_id, timezone=zone, currency=currency,
                         reference=prior["reference"] if prior else "shop_" + uuid4().hex)
        validate_binding(candidate)
        if prior is not None and prior != candidate:
            fail("SHOPIFY_BINDING_SCOPE_CHANGED")
        profile["shopify_read_binding"] = candidate

    workspace.update(directory, change)
    profile = workspace.read(directory, existing_only=True)
    binding = profile["shopify_read_binding"]
    service, label = credential_labels(binding)
    return dict(status="SHOPIFY_READ_BINDING_PREPARED", credential_service=service,
                credential_account=label, store=store, shop_id=shop_id, live_validation="NOT_RUN")


def credential_labels(binding):
    validate_binding(binding)
    return "ModernLinc.CodexAdsLive.Shopify." + binding["reference"], binding["store"]


@contextmanager
def credential_lease(binding):
    service, label = credential_labels(binding)
    if sys.platform == "darwin":
        credential = _mac_read(service, label)
    elif sys.platform == "win32":
        credential = _windows_read(service, label)
    else:
        fail("NATIVE_CREDENTIAL_PLATFORM_UNSUPPORTED")
    if type(credential) is not str or not re.fullmatch(r"[A-Za-z0-9._~-]{1,8192}", credential):
        fail("SHOPIFY_CREDENTIAL_FORMAT")
    try:
        yield credential
    finally:
        credential = None


class ShopifyHTTPS:
    def __init__(self, store, credential, before_request, *, inventory):
        self.store = pattern(store, SHOP)
        self.credential = credential
        self.before_request = before_request
        self.inventory = inventory
        self.cross_window_refunds = False
        self.wait_seconds = 0

    def _post(self, text, variables):
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "X-Shopify-Access-Token": self.credential}
        wire = canonical(dict(query=text, variables=variables)).encode("utf-8")
        self.before_request()
        connection = http.client.HTTPSConnection(self.store, port=443, timeout=12, context=ssl.create_default_context())
        response = None
        self.wait_seconds = 0
        try:
            arm_http_deadline(connection, 12)
            connection.request("POST", f"/admin/api/{VERSION}/graphql.json", body=wire, headers=headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                fail("SHOPIFY_HTTP_REDIRECT_REFUSED")
            if response.status != 200:
                retry = response.getheader("Retry-After") or ""
                self.wait_seconds = min(4, int(retry)) if re.fullmatch(r"[0-9]{1,6}", retry) else 1
                return response.status, None
            body = read_json_response(response)
            self.before_request()
            return response.status, body
        except http.client.HTTPException as error:
            raise ConnectionError("SHOPIFY_NETWORK_INTERRUPTED") from error
        finally:
            headers.clear()
            try:
                if response is not None:
                    response.close()
            finally:
                connection.close()

    def access(self):
        return self._post(ACCESS_QUERY, {})

    def exchange(self, packet):
        fields = ("operation", "shop", "method", "path", "query", "variables")
        object_only(packet, fields, fields)
        operation = packet["operation"]
        operations = ("Orders", "OrderLines", "RefundLines", "RefundTransactions")
        if self.cross_window_refunds:
            operations += ("CrossWindowRefunds",)
        if (operation not in operations
                or (packet["shop"], packet["method"], packet["path"]) !=
                   (self.store, "POST", f"/admin/api/{VERSION}/graphql.json")
                or packet["query"] != query(operation, self.inventory, refund_transactions=True)):
            fail("SHOPIFY_HTTP_PACKET_MISMATCH")
        return self._post(packet["query"], packet["variables"])

    def wait(self):
        self.before_request()
        sleep(max(self.wait_seconds, 1))
        self.before_request()


def _scopes(status, result, binding):
    if status in (401, 403):
        fail("SHOPIFY_REAUTHORIZE_STORE")
    if status == 429:
        fail("SHOPIFY_ACCESS_THROTTLED")
    if status != 200 or type(result) is not dict or result.get("errors"):
        fail("SHOPIFY_ACCESS_CHECK_FAILED")
    data = result.get("data") or {}
    shop = data.get("shop") or {}
    if any(shop.get(field) != binding[key] for field, key in (
            ("id", "shop_id"), ("myshopifyDomain", "store"), ("currencyCode", "currency"), ("ianaTimezone", "timezone"))):
        fail("SHOPIFY_STORE_MISMATCH")
    scopes = (data.get("currentAppInstallation") or {}).get("accessScopes")
    if type(scopes) is not list or len(scopes) > 100 or any(type(row) is not dict for row in scopes):
        fail("SHOPIFY_SCOPE_LIST_INVALID")
    return [pattern(row.get("handle"), r"[a-z][a-z0-9_]{1,60}") for row in scopes]


def collect(directory, since, until, output, *, inventory=False, resume=False, cross_window_refunds=False):
    initial = workspace.read(directory, existing_only=True)
    if "shopify_read_binding" not in initial:
        fail("SHOPIFY_READ_BINDING_REQUIRED")
    binding = initial["shopify_read_binding"]
    scope = workspace.workspace_identity(initial)
    document = dict(schema="shopify-order-readiness-v1", store=binding["store"], since=since,
                    until=until, checked_on=datetime.now(load_zone(binding["timezone"])).date().isoformat(), reported_scopes=[])
    preview(document)
    if resume:
        output = checked_path(output, link_error="SHOPIFY_READ_PATH_LINK", volume_error="WORKSPACE_VOLUME_NOT_MOUNTED")
        if not output.is_dir():
            fail("SHOPIFY_RESUME_OUTPUT_REQUIRED")
        if (output / "SHOPIFY-SNAPSHOT.json").is_file():
            final = read_snapshot(directory, output / "SHOPIFY-SNAPSHOT.json")
            prior_cross = final.get("refund_transaction_evidence", {}).get("scan", {}).get("requested", False)
            if (final["since"], final["until"], final.get("inventory_requested", False), prior_cross) != (
                    since, until, inventory, cross_window_refunds):
                fail("SHOPIFY_RESUME_SCOPE_MISMATCH")
            return dict(status=final["status"], report_reused=True, network_used=False)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        fail("SHOPIFY_INTERACTIVE_TERMINAL_REQUIRED")
    phrase = f"READ SHOPIFY {binding['store']} {since} {until}"
    if inventory:
        phrase += " INVENTORY"
        print("本次另读取获授权的库存与现行库存成本字段。")
    if cross_window_refunds:
        phrase += " EARLIER REFUNDS"
        print("本次另检查在窗口开始后更新的更早订单，按交易处理日期识别本期退款。")
    print("范围为所列店铺的只读订单与退款核查。")
    if input(f"确认请键入 {phrase}：") != phrase:
        fail("SHOPIFY_READ_NOT_CONFIRMED")
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(minutes=5)
    expected = digest(binding)

    def checkpoint():
        current = workspace.read(directory, existing_only=True)
        if (workspace.workspace_identity(current) != scope or
                digest(current.get("shopify_read_binding")) != expected):
            fail("SHOPIFY_BINDING_SCOPE_CHANGED")
        if datetime.now(timezone.utc) >= deadline:
            fail("SHOPIFY_READ_DEADLINE")

    output = Path(output)
    root = output if resume else new_directory(output)
    progress = Journal(root / "progress", stage_snapshots=True, existing_only=resume)

    with credential_lease(binding) as credential:
        adapter = ShopifyHTTPS(binding["store"], credential, checkpoint, inventory=False)
        try:
            status, response = adapter.access()
            document["reported_scopes"] = _scopes(status, response, binding)
            checked = preview(document)
            if checked["missing_reported_scopes"]:
                fail("SHOPIFY_REAUTHORIZE_REQUIRED_SCOPES")
            if cross_window_refunds and "read_all_orders" not in document["reported_scopes"]:
                fail("SHOPIFY_CROSS_WINDOW_REQUIRES_READ_ALL_ORDERS")
            document.update(shop_id=binding["shop_id"], timezone=binding["timezone"],
                            currency=binding["currency"], inventory=inventory)
            collector = Collector(document, adapter.exchange, retry_wait=adapter.wait, refund_transactions=True,
                                  cross_window_refunds=cross_window_refunds)
            adapter.inventory = collector.inventory
            adapter.cross_window_refunds = cross_window_refunds
            read_scope = digest(dict(workspace=scope, binding=expected, since=since, until=until,
                                     api_version=VERSION, observed_scopes=sorted(set(document["reported_scopes"])),
                                     inventory=inventory, cross_window_refunds=cross_window_refunds))

            def validate_saved(point):
                object_only(point, ("query_sha256", "checkpoint"), ("query_sha256", "checkpoint"))
                if point["query_sha256"] != read_scope or type(point["checkpoint"]) is not dict:
                    fail("SHOPIFY_RESUME_SCOPE_MISMATCH")
                resume_row = point["checkpoint"]
                keys = ("after", "seen", "orders", "lines", "refunds", "refund_lines",
                        "transaction_rows", "transaction_coverage")
                object_only(resume_row, keys, keys)
                if type(resume_row["seen"]) is not list or not resume_row["seen"] or len(resume_row["seen"]) > 1000:
                    fail("SHOPIFY_RESUME_CHECKPOINT_FORMAT")
                for cursor in resume_row["seen"]:
                    pattern(cursor, r"[A-Za-z0-9_+.=/:-]{1,512}")
                if resume_row["after"] not in resume_row["seen"] or type(resume_row["transaction_coverage"]) is not bool:
                    fail("SHOPIFY_RESUME_CHECKPOINT_FORMAT")
                if any(type(resume_row[key]) is not dict for key in keys[2:-1]):
                    fail("SHOPIFY_RESUME_CHECKPOINT_FORMAT")

            with progress.locked():
                prior, damaged = progress.load(validate_saved)
                if damaged or progress.unpublished_snapshot_count():
                    fail("SHOPIFY_RESUME_CHECKPOINT_INVALID")

                def save_page(page):
                    checkpoint()
                    progress.save(dict(query_sha256=read_scope, checkpoint=page), validate_saved)

                outcome = collector.run(resume=prior["checkpoint"] if prior else None, page_committed=save_page)
                checkpoint()
                next_partial = len(tuple(root.glob("PARTIAL-*.json"))) + 1
                outcome.update(source="SHOPIFY_ADMIN_HTTPS", network_used=True, scope_verified_by_platform=True,
                               observed_scopes=sorted(set(document["reported_scopes"])), timezone=binding["timezone"],
                               inventory_requested=inventory)
                payload = dict(schema="shopify-read-snapshot-v1", workspace_sha256=scope,
                               binding_sha256=expected, store_readout=outcome)
                filename = "SHOPIFY-SNAPSHOT.json" if outcome["status"] == "SHOPIFY_READ_COMPLETE" else (
                    f"PARTIAL-{next_partial:05d}.json")
                file = _write_new(root, filename, dict(payload=payload, sha256=digest(payload)))
        finally:
            adapter.credential = None
            credential = None
    return dict(status=outcome["status"], returned_orders=len(outcome["orders"]),
                coverage_gaps=outcome["gaps"], snapshot=file, network_used=True)


def read_snapshot(directory, path):
    path = checked_path(path, link_error="SHOPIFY_READ_PATH_LINK", volume_error="WORKSPACE_VOLUME_NOT_MOUNTED")
    if not path.is_file() or path.stat().st_size > 5_000_000:
        fail("SHOPIFY_SNAPSHOT_FILE")
    with path.open("rb") as stream:
        value = strict_json(stream.read(5_000_001).decode("utf-8"), max_chars=5_000_000)
    object_only(value, ("payload", "sha256"), ("payload", "sha256"))
    data = value["payload"]
    object_only(data, BODY_FIELDS, BODY_FIELDS)
    profile = workspace.read(directory, existing_only=True)
    binding = profile.get("shopify_read_binding")
    if (binding is None or data["schema"] != "shopify-read-snapshot-v1" or digest(data) != value["sha256"]
            or data["workspace_sha256"] != workspace.workspace_identity(profile)
            or data["binding_sha256"] != digest(binding)):
        fail("SHOPIFY_SNAPSHOT_SCOPE_MISMATCH")
    result = data["store_readout"]
    if any(result.get(key) != binding[key] for key in ("store", "shop_id", "currency", "timezone")):
        fail("SHOPIFY_STORE_MISMATCH")
    return result


def add_commands(sub):
    register = sub.add_parser("shopify-bind", help="登记原工作区的具名店铺和本机凭据引用")
    register.add_argument("--workspace", type=Path, required=True)
    for field in ("store", "shop-id", "timezone", "currency"):
        register.add_argument("--" + field, required=True)
    command = sub.add_parser("shopify-read", help="经本人确认后收集具名店铺的只读订单与退款")
    for field in ("workspace", "output"):
        command.add_argument("--" + field, type=Path, required=True)
    command.add_argument("--since", required=True)
    command.add_argument("--until", required=True)
    command.add_argument("--resume", action="store_true")
    command.add_argument("--inventory", action="store_true")
    command.add_argument("--include-cross-window-refunds", action="store_true")
