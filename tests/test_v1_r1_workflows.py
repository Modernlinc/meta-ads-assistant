from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from test_v1_customer_workflows import fixture, run_customer


class MetaGuidanceTest(unittest.TestCase):
    def test_historical_create_path_cannot_become_a_current_sop(self):
        case = {
            "schema": "meta-guidance-review-v1", "task": "AD_CREATION",
            "surface": "ADS_MANAGER_WEB", "market": "US", "qualification": "CONFIRMED",
            "as_of": "2026-11-26",
            "historical": {"title": "2021 版建广告演示（合成）", "published_on": "2021-03-01",
                           "ui": "LEGACY_AD_CREATION", "instruction": "使用历史界面的快速创建。"},
        }
        with TemporaryDirectory() as tmp:
            path = fixture(Path(tmp), "guidance.json", case)
            code, result = run_customer("meta-guidance-review", "--input", path)
            self.assertEqual(code, 0, result)
            self.assertEqual(result["status"], "OFFICIAL_GUIDANCE_AVAILABLE")
            self.assertEqual(result["historical"]["state"], "HISTORICAL_ONLY")
            self.assertEqual(result["official_source"]["checked_on"], "2026-11-26")
            self.assertEqual(result["official_source"]["market"], "US")
            self.assertNotIn("使用历史界面", str(result["steps"]))
            case["qualification"] = "UNKNOWN"
            fixture(Path(tmp), "guidance.json", case)
            code, unresolved = run_customer("meta-guidance-review", "--input", path)
        self.assertEqual(code, 0, unresolved)
        self.assertEqual(unresolved["status"], "CONFLICT_UNRESOLVED")
        self.assertEqual(unresolved["steps"], [])


def shop_connection(nodes, after=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": after is not None, "endCursor": after}}


def shop_page(orders, after=None, *, shop="1", errors=None):
    return {"operation": "Orders", "after": None, "status": 200, "body": {
        "data": {"shop": {"id": "gid://shopify/Shop/" + shop, "myshopifyDomain": "fixture.myshopify.com"},
                 "orders": shop_connection(orders, after)},
        "errors": errors or [],
    }}


def money(amount):
    return {"shopMoney": {"amount": amount, "currencyCode": "USD"}}


def shop_order():
    return {
        "id": "gid://shopify/Order/5", "createdAt": "2026-11-24T06:00:00Z", "displayFulfillmentStatus": "FULFILLED",
        "customer": {"email": "ignored@example.invalid"},
        "lineItems": shop_connection([
            {"id": "gid://shopify/LineItem/10", "quantity": 1, "originalTotalSet": money("20.00"),
             "discountAllocations": [{"allocatedAmountSet": money("2.00")}],
             "variant": {"inventoryItem": {"id": "gid://shopify/InventoryItem/40",
                          "tracked": True, "unitCost": {"amount": "0.00", "currencyCode": "USD"}}}},
            {"id": "gid://shopify/LineItem/11", "quantity": 2, "originalTotalSet": money("8.00"),
             "discountAllocations": [], "variant": {"inventoryItem": {"id": "gid://shopify/InventoryItem/41",
                          "tracked": False, "unitCost": None}}},
        ]),
        "refunds": [{"id": "gid://shopify/Refund/21", "processedAt": "2026-11-25T10:00:00Z",
                     "totalRefundedSet": money("6.00"), "refundLineItems": shop_connection([
                         {"id": "gid://shopify/RefundLineItem/31", "lineItem": {"id": "gid://shopify/LineItem/10"},
                          "quantity": 1, "subtotalSet": money("5.00")},
                     ])}],
    }


def shop_case(exchanges, scopes=None):
    return {
        "schema": "shopify-read-review-v1", "store": "fixture.myshopify.com",
        "shop_id": "gid://shopify/Shop/1", "timezone": "America/New_York", "currency": "USD",
        "since": "2026-11-20", "until": "2026-11-25", "checked_on": "2026-11-26",
        "reported_scopes": scopes if scopes is not None else ["read_orders", "read_products", "read_inventory"],
        "inventory": True, "exchanges": exchanges,
    }


class ShopifyReadWorkflowTest(unittest.TestCase):
    def review(self, case):
        with TemporaryDirectory() as tmp:
            path = fixture(Path(tmp), "shopify.json", case)
            return run_customer("shopify-read-review", "--input", path)

    def test_reads_scoped_pages_deduplicates_lines_and_excludes_private_fields(self):
        order = shop_order()
        second = dict(shop_page([order]), after="cursor-a")
        code, result = self.review(shop_case([shop_page([order], "cursor-a"), second]))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "SHOPIFY_READ_COMPLETE")
        self.assertEqual(result["api_version"], "2026-10")
        self.assertEqual(len(result["orders"]), 1)
        self.assertEqual(len(result["lines"]), 2)
        self.assertEqual(len(result["refund_lines"]), 1)
        self.assertEqual(result["totals"]["merchandise_after_line_refunds"], "21.00")
        self.assertIsNone(result["totals"]["historical_product_cost"])
        self.assertEqual(result["lines"][0]["inventory"]["unit_cost"]["state"], "PRESENT")
        self.assertEqual(result["lines"][1]["inventory"]["unit_cost"]["state"], "NULL")
        self.assertNotIn("ignored@example.invalid", str(result))
        self.assertFalse(result["network_used"])

    def test_throttle_interruption_and_5xx_replay_the_same_bound_page(self):
        first = shop_page([shop_order()], "page2")
        second = dict(shop_page([]), after="page2")
        failures = [{"operation": "Orders", "after": after, "status": status}
                    for after, status in ((None, 429), (None, 503), ("page2", "INTERRUPTED"))]
        code, result = self.review(shop_case([*failures[:2], first, failures[2], second]))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["attempts"],
                         ["HTTP_THROTTLED", "HTTP_TEMPORARY", "RESPONSE", "NETWORK_INTERRUPTED", "RESPONSE"])
        self.assertEqual(result["status"], "SHOPIFY_READ_COMPLETE")
        code, partial = self.review(shop_case([first, failures[2], failures[2], failures[2]]))
        self.assertEqual(code, 0, partial)
        self.assertEqual(partial["status"], "SHOPIFY_READ_PARTIAL")
        self.assertIsNone(partial["totals"])
        self.assertEqual(len(partial["orders"]), 1)

    def test_repeated_cursor_and_cross_shop_are_distinct_failures(self):
        stub = shop_page([shop_order()], "repeated")
        code, partial = self.review(shop_case([stub, dict(stub, after="repeated")]))
        self.assertEqual(code, 0, partial)
        self.assertEqual(partial["gaps"], ["CURSOR_REPEATED"])
        self.assertIsNone(partial["totals"])
        code, payload = self.review(shop_case([stub, dict(shop_page([], shop="2"), after="repeated")]))
        self.assertEqual(code, 2, payload)
        self.assertEqual(payload["error_code"], "SHOPIFY_STORE_MISMATCH")
        self.assertNotIn("lines", payload)
        invalid = shop_page([])
        invalid["body"]["data"]["orders"]["pageInfo"]["hasNextPage"] = True
        code, missing = self.review(shop_case([invalid]))
        self.assertEqual(code, 0, missing)
        self.assertEqual(missing["gaps"], ["CURSOR_MISSING_OR_INVALID"])

    def test_redacted_absent_and_unauthorized_inventory_are_not_zero(self):
        order = shop_order()
        del order["lineItems"]["nodes"][0]["variant"]["inventoryItem"]["unitCost"]
        code, result = self.review(shop_case([shop_page([order], errors=[{
            "path": ["orders", "nodes", 0, "lineItems", "nodes", 1, "variant", "inventoryItem", "unitCost"],
            "extensions": {"code": "REDACTED"}, "message": "Private upstream error",
        }])]))
        self.assertEqual(code, 0, result)
        self.assertEqual([r["inventory"]["unit_cost"]["state"] for r in result["lines"]],
                         ["FIELD_ABSENT", "REDACTED"])
        self.assertNotIn("Private upstream error", str(result))
        code, result = self.review(shop_case([shop_page([order])], ["read_orders"]))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["lines"][0]["inventory"]["state"], "UNAUTHORIZED")

    def test_nested_order_lines_require_the_correct_parent_and_date_window(self):
        order = shop_order()
        child = order["lineItems"]["nodes"].pop()
        order["lineItems"]["pageInfo"] = {"hasNextPage": True, "endCursor": "next-line"}
        extension = {"operation": "OrderLines", "after": "next-line", "object_id": order["id"], "status": 200,
                     "body": {"data": {"shop": shop_page([])["body"]["data"]["shop"],
                                       "node": {"id": order["id"], "lineItems": shop_connection([child])}}}}
        code, result = self.review(shop_case([shop_page([order]), extension]))
        self.assertEqual(code, 0, result)
        self.assertEqual(len(result["lines"]), 2)
        order["createdAt"] = "2026-11-20T00:00:00Z"
        code, rejected = self.review(shop_case([shop_page([order]), extension]))
        self.assertEqual(code, 2, rejected)
        self.assertEqual(rejected["error_code"], "SHOPIFY_ORDER_OUTSIDE_WINDOW")

    def test_shared_reconciliation_spend_keeps_historical_cost_unknown(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            doc = fixture(root, "shopify.json", shop_case([shop_page([shop_order()])]))
            spend = root / "ads.csv"
            spend.write_text("account_id,date,currency,spend_minor\n100,2026-11-24,USD,300\n")
            code, result = run_customer("shopify-read-review", "--input", doc, "--spend", spend,
                                        "--account-id", "100", "--spend-timezone", "America/New_York", "--minor-digits", "2")
        self.assertEqual(code, 0, result)
        statement = result["reconciliation"]["period_totals"]
        self.assertEqual(statement["net_sales_minor"], 2100)
        self.assertEqual(statement["observed_spend_minor"], 300)
        self.assertIsNone(statement["contribution_after_ads_minor"])


class LocalOperatorWorkflowTest(unittest.TestCase):
    def test_business_facts_disagree_without_calculating_a_margin(self):
        configuration = {
            "schema": "business-readiness-v1", "account_id": "100", "product_id": "sku-cup",
            "market": "US", "currency": "USD", "as_of": "2026-11-26",
            "facts": [
                {"id": "EV_PRICE_A", "kind": "NET_ORDER_MINOR", "value": 4000, "observed_on": "2026-11-25"},
                {"id": "EV_PRICE_B", "kind": "NET_ORDER_MINOR", "value": 5000, "observed_on": "2026-11-26"},
                {"id": "EV_COGS", "kind": "VARIABLE_COST_MINOR", "value": 1500, "observed_on": "2026-11-25"},
            ],
        }
        with TemporaryDirectory() as tmp:
            path = fixture(Path(tmp), "business.json", configuration)
            code, result = run_customer("business-readiness", "--input", path)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["fields"]["NET_ORDER_MINOR"]["state"], "CONFLICT")
        self.assertIsNone(result["contribution_before_ad_minor"])
        self.assertIn("DESTINATION", result["missing"])
        self.assertEqual(result["status"], "BUSINESS_EVIDENCE_NEEDED")

    def test_note_index_is_pageable_without_promoting_unchecked_notes(self):
        code, result = run_customer("knowledge-search", "--topic", "all", "--notes", "--limit", "20")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["note_matches"], 492)
        self.assertEqual(len(result["note_page"]), 20)
        self.assertEqual(result["next_offset"], 20)
        self.assertFalse(result["note_page"][0]["transcript_verified"])

    def test_tracking_test_events_cannot_complete_a_production_pair(self):
        case = {
            "schema": "operator-tracking-v1", "dataset_id": "100", "destination": "https://example.com/?utm_source=meta",
            "events": [
                {"event_id": "EV_ONE", "channel": "PIXEL", "name": "Purchase"},
                {"event_id": "EV_ONE", "channel": "CAPI", "name": "Purchase", "mode": "TEST"},
                {"event_id": "EV_ONE", "channel": "CAPI", "name": "Lead", "mode": "TEST"},
            ],
            "attribution": {
                "meta": {"event": "Purchase", "window": "7d_click", "model": "META", "report_time": "click",
                         "result_count": 10, "scope": "US_WIN01"},
                "ga4": {"event": "purchase", "window": "30d", "model": "GA4", "report_time": "event",
                        "result_count": 9, "scope": "US_WIN01"},
            },
        }
        with TemporaryDirectory() as tmp:
            doc = fixture(Path(tmp), "tracking.json", case)
            code, result = run_customer("tracking-audit", "--input", doc)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["paired_event_keys"], 0)
        self.assertEqual(result["event_id_used_with_multiple_names"], 1)
        self.assertIsNone(result["attribution"]["meta_less_ga4"])
        self.assertIn("window", result["attribution"]["mismatched_fields"])

    def test_customer_reports_can_select_two_local_accounts_and_one_page(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ws = root / "workspace"
            self.assertEqual(run_customer("workspace-init", "--workspace", ws)[0], 0)
            for kind, identifier in (("ACCOUNT", "100"), ("ACCOUNT", "101"), ("PAGE", "9001")):
                code, asset = run_customer("workspace-add", "--workspace", ws, "--kind", kind,
                                           "--id", identifier, "--evidence-ref", "EV_SYNTHETIC")
                self.assertEqual(code, 0, asset)
            code, plan = run_customer("multi-account-plan", "--workspace", ws, "--account-id", "100",
                                      "--account-id", "101", "--last-complete-days", "7")
            self.assertEqual(code, 0, plan)
            self.assertEqual([r["account_id"] for r in plan["accounts"]], ["100", "101"])
            self.assertTrue(all(not row["ready_for_separate_read"] for row in plan["accounts"]))
            evidence = fixture(root, "page.json", {
                "schema": "page-live-export-v1", "page_id": "9001", "post_id": "9001_1234",
                "source": "PAGE_OWNER_EXPORT", "since": "2026-11-25T00:00:00Z", "until": "2026-11-25T01:00:00Z",
                "observations": [{"metric": "post_video_live_current_viewers", "observed_at": "2026-11-25T00:20:00Z",
                                  "state": "OBSERVED", "value": 0}],
            })
            code, live = run_customer("page-live-export-review", "--workspace", ws, "--input", evidence)
            self.assertEqual(code, 0, live)
            self.assertEqual(live["observations"][0]["value"], 0)
            self.assertFalse(live["verified_continuous_live"])
            code, history = run_customer("workspace-evidence", "--workspace", ws, "--account-id", "100")
            self.assertEqual(code, 0, history)
            self.assertEqual(history["rows"], [])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "local ffmpeg required")
    def test_missing_model_has_an_explicit_srt_fallback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            video = root / "clip.mp4"
            subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=blue:s=48x48:r=8:d=1", "-c:v", "mpeg4", str(video)], check=True, timeout=10)
            srt = root / "fallback.srt"
            srt.write_text("1\n00:00:00,100 --> 00:00:00,850\nSynthetic subtitle\n")
            code, result = run_customer("media-analyze", "--file", video, "--output", root / "out",
                                        "--model", root / "missing.bin", "--fallback-subtitles", srt,
                                        "--model-sha256", "0" * 64, "--language", "en")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["transcript_state"], "USER_SUPPLIED_SRT_FALLBACK")
        self.assertEqual(result["transcription_model"]["state"], "MODEL_NOT_AVAILABLE")
