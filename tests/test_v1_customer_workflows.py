import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "codex-ads-live" / "run.py"


def run_customer(*args):
    process = subprocess.run(
        [sys.executable, "-I", "-B", str(RUN), *map(str, args)],
        capture_output=True, text=True, timeout=25,
    )
    return process.returncode, json.loads(process.stdout or "{}")


def fixture(directory, name, value):
    target = directory.resolve() / name
    target.write_text(json.dumps(value), encoding="utf-8")
    return target


class KnowledgeWorkflowTest(unittest.TestCase):
    def test_contrasting_strategies_have_context_and_sources(self):
        code, result = run_customer("knowledge-search", "--topic", "strategy")
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "LOCAL_KNOWLEDGE_MATCHED")
        self.assertEqual(result["coverage"], {"reported": 50, "catalog": 20, "all_located": 58})
        entries = result["claims"]
        practitioners = [row for row in entries if row["kind"] == "PRACTITIONER"]
        self.assertGreaterEqual(len({row["author"] for row in practitioners}), 2)
        self.assertTrue(all(row["applies_when"] and row["limitations"] for row in practitioners))
        self.assertFalse(result["actions_authorized"])

    def test_catalog_and_visual_note_status_are_retained_for_same_video(self):
        code, result = run_customer("knowledge-search", "--topic", "creative", "--video-id", "0VrNNCDLWPc")
        self.assertEqual(code, 0)
        video = result["video"]
        self.assertEqual(video["catalog_membership"], "CONFIRMED")
        self.assertIn("No YouTube captions", video["original_status"])
        self.assertTrue(any("visual_analysis" in locator for locator in video["note_locators"]))
        self.assertFalse(video["verbatim_transcript_verified"])
        self.assertTrue(all(row["status"] != "VERIFIED_TRANSCRIPT" for row in result["note_index"]))

    def test_note_only_video_is_not_promoted_to_catalog_member(self):
        code, result = run_customer("knowledge-search", "--topic", "all", "--video-id", "Hh4zkUdRVzg")
        self.assertEqual(code, 0)
        self.assertEqual(result["video"]["catalog_membership"], "UNCONFIRMED")
        self.assertTrue(result["video"]["note_locators"])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "local ffmpeg required")
class MediaWorkflowTest(unittest.TestCase):
    def test_media_frames_audio_and_supplied_subtitles_stay_bound_to_hash(self):
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp).resolve()
            media = local / "synthetic.mp4"
            subtitles = local / "synthetic.srt"
            subprocess.run(
                [shutil.which("ffmpeg"), "-nostdin", "-loglevel", "error",
                 "-f", "lavfi", "-i", "color=c=blue:s=80x80:r=8:d=1",
                 "-f", "lavfi", "-i", "sine=frequency=700:duration=1",
                 "-c:v", "mpeg4", "-c:a", "aac", str(media)],
                capture_output=True, check=True, timeout=10,
            )
            subtitles.write_text("1\n00:00:00,120 --> 00:00:00,820\nSynthetic caption\n")
            output = local / "evidence"
            code, result = run_customer(
                "media-analyze", "--file", media, "--subtitles", subtitles, "--output", output,
            )
            self.assertEqual(code, 0)
            self.assertEqual(result["media_sha256"], hashlib.sha256(media.read_bytes()).hexdigest())
            self.assertTrue((output / result["frames"][0]["path"]).is_file())
            self.assertTrue((output / result["audio"]["path"]).is_file())
            self.assertEqual(result["subtitles"][0]["start_ms"], 120)
            self.assertEqual(result["subtitles"][0]["source"], "USER_SUPPLIED_SRT")
            self.assertFalse(result["actions_authorized"])
            html = (output / "review.html").read_text()
            self.assertIn(result["frames"][0]["path"], html)
            self.assertIn("120–820 ms", html)
            observations = fixture(local, "observations.json", {
                "media_sha256": result["media_sha256"],
                "observations": {
                    axis: {"kind": "FRAME" if axis == "human_feel" else "SUBTITLE",
                           "index": 0, "observation": "Synthetic evidence"}
                    for axis in ("selling_point", "hook", "conversion_point", "human_feel")
                },
            })
            code, second = run_customer("media-analyze", "--file", media, "--subtitles", subtitles,
                                        "--output", local / "second", "--observations", observations,
                                        "--concept-id", "fixture-creative")
            self.assertEqual(code, 0, second)
            self.assertEqual({v["state"] for v in second["review"].values()}, {"USER_OBSERVED"})
            review = json.loads((local / "second" / second["workspace_review_file"]).read_text())["reviews"][0]
            self.assertEqual(review["asset_sha256"], result["media_sha256"])
            self.assertEqual(review["concept_id"], "fixture-creative")
            self.assertEqual({item["kind"] for item in review["observations"]}, {"VISUAL", "SUBTITLE"})
            stale = json.loads(observations.read_text())
            stale["media_sha256"] = "0" * 64
            fixture(local, "stale.json", stale)
            code, rejected = run_customer("media-analyze", "--file", media, "--subtitles", subtitles,
                                          "--output", local / "stale", "--observations", local / "stale.json")
            self.assertNotEqual(code, 0)
            self.assertEqual(rejected["error_code"], "MEDIA_OBSERVATION_HASH_MISMATCH")
            self.assertFalse((local / "stale").exists())

    def test_missing_source_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp).resolve()
            out = local / "evidence"
            code, detail = run_customer("media-analyze", "--file", local / "missing.mp4", "--output", out)
            self.assertNotEqual(code, 0)
            self.assertEqual(detail["error_code"], "MEDIA_INPUT_NOT_REGULAR_FILE")
            self.assertFalse(out.exists())


class OperatorReviewTest(unittest.TestCase):
    def test_strategy_reports_margin_and_sample_budget_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fixture(Path(tmp), "strategy.json", {
                "schema": "operator-strategy-v1", "account_id": "100", "currency": "USD",
                "product": "Synthetic fixture", "offer": "Bundle", "goal": "PURCHASE",
                "audience": "Source audience", "destination": "https://example.com/p?utm_source=meta",
                "proof_ref": "EV_SYNTHETIC_TEST", "commercial": {
                    "net_order_minor": 4000, "variable_cost_minor": 2500, "target_cpa_minor": 1000,
                    "test_budget_minor": 4000, "minimum_results": 6, "days": 5,
                }, "test_variable": "CREATIVE_ANGLE", "advantage_plus": "UNKNOWN",
            })
            code, result = run_customer("strategy-review", "--input", path)
            self.assertEqual(code, 0)
            self.assertEqual(result["break_even_cpa_minor"], 1500)
            self.assertEqual(result["test"]["minimum_result_budget_feasible"], False)
            self.assertEqual(result["test"]["candidates"], 2)
            self.assertFalse(result["actions_authorized"])
            evidence = json.loads(path.read_text())
            evidence["commercial"]["variable_cost_minor"] = None
            fixture(Path(tmp), "strategy.json", evidence)
            code, missing = run_customer("strategy-review", "--input", path)
            self.assertEqual(code, 0)
            self.assertIsNone(missing["break_even_cpa_minor"])

    def test_diagnosis_blocks_cross_attribution_and_keeps_missing(self):
        def period(window, clicks):
            return {"account_id": "100", "currency": "USD", "timezone": "UTC", "attribution": window,
                    "scope_id": "100", "coverage": "COMPLETE", "delivery": "ACTIVE", "impressions": 1000,
                    "spend_minor": 1000, "clicks": clicks, "landing_views": None,
                    "conversions": 2, "conversion_value_minor": 4200}
        with tempfile.TemporaryDirectory() as tmp:
            path = fixture(Path(tmp), "diagnosis.json", {
                "schema": "operator-diagnosis-v1", "before": period("7d_click", 50),
                "after": period("1d_click", 25), "minimum_results": 5,
            })
            code, result = run_customer("performance-diagnose", "--input", path)
        self.assertEqual(code, 0)
        self.assertFalse(result["comparable"])
        self.assertIn("attribution", result["comparability_mismatches"])
        self.assertIsNone(result["metrics"]["after"]["landing_view_rate"])
        self.assertEqual(result["hypotheses"], [])

    def test_equal_closed_dates_compare_cpm_and_landing_view_hypotheses(self):
        common = dict(account_id="100", currency="USD", timezone="UTC", attribution="7d_click",
                      scope_id="PLACEMENT:MOBILE", coverage="COMPLETE", delivery="ACTIVE", impressions=1000,
                      clicks=100, conversions=10, conversion_value_minor=5500,
                      report_time="IMPRESSION", result_event="PURCHASE")
        with tempfile.TemporaryDirectory() as tmp:
            path = fixture(Path(tmp), "diagnosis.json", {
                "schema": "operator-diagnosis-v1",
                "before": dict(common, since="2026-11-10", until="2026-11-10", landing_views=90, spend_minor=1200),
                "after": dict(common, since="2026-11-11", until="2026-11-11", landing_views=60, spend_minor=4000),
                "minimum_results": 5,
            })
            code, result = run_customer("performance-diagnose", "--input", path)
        self.assertEqual(code, 0, result)
        self.assertTrue(result["comparable"])
        self.assertTrue({"cpm_minor", "landing_view_rate"}.issubset({v["metric"] for v in result["hypotheses"]}))
        self.assertFalse(result["actions_authorized"])

    def test_tracking_separates_valid_pair_from_missing_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fixture(Path(tmp), "tracking.json", {
                "schema": "operator-tracking-v1", "dataset_id": "123",
                "destination": "https://example.com/?utm_source=meta&utm_source=facebook",
                "events": [
                    {"channel": "PIXEL", "name": "Purchase", "event_id": "EV_ONE"},
                    {"channel": "CAPI", "name": "Purchase", "event_id": "EV_ONE"},
                    {"channel": "CAPI", "name": "Purchase", "event_id": None},
                ],
            })
            code, result = run_customer("tracking-audit", "--input", path)
        self.assertEqual(code, 0)
        self.assertEqual(result["paired_event_keys"], 1)
        self.assertEqual(result["missing_event_ids"], 1)
        self.assertEqual(result["duplicate_utm_keys"], ["utm_source"])
        self.assertFalse(result["actions_authorized"])


class CompetitorWorkflowTest(unittest.TestCase):
    def test_public_detail_preview_freezes_both_ids_and_limits_query_to_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = fixture(Path(tmp), "public-scope.json", {
                "query": "synthetic product", "countries": ["DE"], "status": "ACTIVE",
                "date_min": None, "date_max": None,
            })
            status, payload = run_customer("public-ad-query", "--input", source, "--page-id", "400",
                                           "--archive-id", "500", "--sample-limit", "2")
        self.assertEqual(status, 0)
        self.assertEqual(payload["plan"]["detail_target"], {"page_id": "400", "ad_archive_id": "500"})
        self.assertEqual(json.loads(payload["plan"]["parameters"]["search_page_ids"]), ["400"])
        self.assertFalse(payload["network_used"])

    def test_competitor_inspection_keeps_angle_out_of_source_observation(self):
        observation = {
            "scope": {"query": "sample", "countries": ["US"], "status": "ACTIVE",
                      "date_min": None, "date_max": None},
            "records": [{"ad_archive_id": "500", "page_id": "400",
                         "source_url": "https://www.facebook.com/ads/library/?id=500",
                         "observed_at": "2026-11-25T00:00:00Z", "body": "Sample bundle",
                         "detail": None, "angle_hypothesis": None}],
            "coverage": "SAMPLE",
        }
        with tempfile.TemporaryDirectory() as tmp:
            source = fixture(Path(tmp), "public.json", observation)
            code, result = run_customer("public-ad-inspect", "--input", source, "--page-id", "400",
                                        "--archive-id", "500", "--angle-hypothesis", "Price contrast")
        self.assertEqual(code, 0)
        self.assertEqual(result["observed"]["body"], "Sample bundle")
        self.assertIsNone(result["observed"]["angle_hypothesis"])
        self.assertEqual(result["reviewed_angle_hypothesis"], "Price contrast")


class IntegrationContractTest(unittest.TestCase):
    def test_shopify_previews_older_window_without_returning_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = fixture(Path(tmp), "shop.json", {
                "schema": "shopify-order-readiness-v1", "store": "example-123.myshopify.com",
                "since": "2026-06-01", "until": "2026-11-25", "checked_on": "2026-11-26",
                "reported_scopes": ["read_orders"],
            })
            code, result = run_customer("shopify-read-preview", "--input", source)
        self.assertEqual(code, 0)
        self.assertEqual(result["missing_reported_scopes"], ["read_all_orders"])
        self.assertEqual(result["window_with_reported_scopes"]["since"], "2026-09-27")
        self.assertFalse(result["scope_verified_by_platform"])
        self.assertIn("inventory", result["excluded_data"])
        self.assertFalse(result["network_used"])

    def test_partner_role_routes_approved_fields_and_keeps_withdrawal(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = fixture(Path(tmp), "partner.json", {
                "schema": "partner-evidence-brief-v1", "role": "BACKOFFICE", "product": "Cup", "market": "US",
                "items": [{"kind": "refund_definition", "ref": "EV_REFUND", "text": "Superseded",
                           "observed_at": "2026-11-25T00:00:00Z", "state": "WITHDRAWN"}],
            })
            code, result = run_customer("partner-brief-review", "--input", source)
        self.assertEqual(code, 0)
        self.assertEqual(result["results"][0]["next_entry"], "commerce-reconcile")
        self.assertIsNone(result["results"][0]["text"])
        self.assertFalse(result["host_execution"])


class CommerceWorkflowTest(unittest.TestCase):
    def test_replayed_lines_and_refunds_are_counted_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            orders = root / "orders.csv"
            orders.write_text(
                "store_id,order_id,line_id,date,currency,gross_minor,discount_minor,product_cost_minor,fulfillment_minor,payment_minor\n"
                "shop-test,o1,l1,2026-11-24,USD,6000,1000,2000,500,200\n"
                "shop-test,o1,l1,2026-11-24,USD,6000,1000,2000,500,200\n"
            )
            refunds = root / "refunds.csv"
            refunds.write_text(
                "store_id,refund_id,order_id,line_id,date,currency,refund_minor\n"
                "shop-test,r1,o1,l1,2026-11-25,USD,1000\n"
                "shop-test,r1,o1,l1,2026-11-25,USD,1000\n"
            )
            spend = root / "spend.csv"
            spend.write_text("account_id,date,currency,spend_minor\n100,2026-11-24,USD,800\n100,2026-11-25,USD,200\n")
            code, reply = run_customer(
                "commerce-reconcile", "--orders", orders, "--refunds", refunds, "--spend", spend,
                "--store-id", "shop-test", "--account-id", "100", "--currency", "USD",
                "--since", "2026-11-24", "--until", "2026-11-25",
            )
            self.assertEqual(code, 0)
            self.assertEqual(reply["period_totals"]["net_sales_minor"], 4000)
            self.assertEqual(reply["period_totals"]["contribution_after_ads_minor"], 300)
            self.assertEqual(reply["period_totals"]["mer"], "4.000000")
            self.assertEqual(reply["duplicate_rows"], {"orders": 1, "refunds": 1, "spend": 0})
            self.assertFalse(reply["actions_authorized"])
            self.assertEqual(reply["live_shopify_status"], "DEFERRED_NOT_RUN")
            args = ["--orders", orders, "--refunds", refunds, "--spend", spend,
                    "--store-id", "different-shop", "--account-id", "100", "--currency", "USD",
                    "--since", "2026-11-24", "--until", "2026-11-25"]
            code, rejected = run_customer("commerce-reconcile", *args)
            self.assertNotEqual(code, 0)
            self.assertEqual(rejected["error_code"], "COMMERCE_SCOPE_OR_CURRENCY_MISMATCH")

    def test_watchdog_runs_local_reconciliation_and_stops_without_resending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "orders.csv").write_text(
                "store_id,order_id,line_id,date,currency,gross_minor,discount_minor,product_cost_minor,fulfillment_minor,payment_minor\n"
                "shop-test,o1,l1,2026-11-24,USD,6000,1000,2000,500,200\n"
            )
            (root / "refunds.csv").write_text("store_id,refund_id,order_id,line_id,date,currency,refund_minor\n")
            (root / "spend.csv").write_text("account_id,date,currency,spend_minor\n100,2026-11-24,USD,800\n")
            cfg = fixture(root, "schedule.json", {
                "schema": "shop-commerce-watch-v1", "store_id": "shop-test", "account_id": "100",
                "currency": "USD", "since": "2026-11-24", "until": "2026-11-24", "refund_basis": "event",
                "orders": "orders.csv", "refunds": "refunds.csv", "spend": "spend.csv",
            })
            state = root / "state"
            code, first = run_customer("commerce-watch", "--input", cfg, "--state-dir", state,
                                       "--cycles", "1", "--interval-seconds", "1")
            self.assertEqual(code, 0)
            self.assertEqual(first["status"], "CYCLE_BUDGET_REACHED")
            self.assertEqual(first["report"]["period_totals"]["observed_spend_minor"], 800)
            code, _ = run_customer("commerce-watch-stop", "--state-dir", state)
            self.assertEqual(code, 0)
            code, blocked = run_customer("commerce-watch", "--input", cfg, "--state-dir", state,
                                         "--cycles", "1", "--interval-seconds", "1")
            self.assertEqual(code, 0)
            self.assertEqual(blocked["status"], "STOP_REQUESTED")
            self.assertEqual(blocked["completed_cycles"], 0)


if __name__ == "__main__":
    unittest.main()
