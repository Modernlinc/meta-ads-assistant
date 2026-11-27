from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "codex-ads-live" / "src"))

from codex_ads_live import ads_library, commerce_watch, multi_account_review
from codex_ads_live.validation import InputError


class PublicArchiveTest(unittest.TestCase):
    def setUp(self):
        self.plan = ads_library.query({
            "query": "sample product", "countries": ["US"], "status": "ACTIVE",
            "date_min": None, "date_max": None,
        }, 5, 4, page_id="400", archive_id="500")

    def test_exact_pair_and_cursor_loss(self):
        calls = []

        def response(route):
            calls.append(route)
            self.assertEqual(parse_qs(urlsplit(route).query)["search_page_ids"], ['["400"]'])
            return {"data": [{"id": "500", "page_id": "401"}, {"id": "500", "page_id": "400"}],
                    "paging": {"next": "https://example.invalid/not-followed"}}

        result = ads_library.query_result(self.plan, response)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["stop"], "CURSOR_UNAVAILABLE")
        self.assertEqual(result["data_state"], "PARTIAL")
        self.assertEqual(len(result["observation"]["records"]), 1)
        self.assertEqual(result["detail_state"], "ID_PAIR_OBSERVED")

    def test_empty_and_unavailable_are_independent(self):
        empty = ads_library.query_result(self.plan, lambda _: {"data": []})
        self.assertEqual(empty["data_state"], "NO_ROWS_IN_OBSERVED_SCOPE")

        def denied(_):
            raise InputError("PERMISSION")

        failure = ads_library.query_result(self.plan, denied)
        self.assertEqual(failure["status"], "PUBLIC_AD_QUERY_SCOPE_UNAVAILABLE")
        self.assertEqual(failure["fallback"], "IMPORT_AUTHORIZED_PUBLIC_OBSERVATIONS")


class AccountReviewTest(unittest.TestCase):
    def test_selected_ad_slice_uses_only_rows_for_that_target(self):
        rows = [
            {"account_id": "100", "ad_id": "41", "campaign_id": "11", "adset_id": "21",
             "date_start": "2026-11-18", "date_stop": "2026-11-18", "spend": "3.50",
             "impressions": "50", "clicks": "4"},
            {"account_id": "100", "ad_id": "42", "campaign_id": "11", "adset_id": "22",
             "date_start": "2026-11-18", "date_stop": "2026-11-18", "spend": "5.00",
             "impressions": "60", "clicks": "5"},
        ]
        common = dict(result="COMPLETE", observed_at="2026-11-20T12:00:00Z", pages=1, generation=1,
                      last_successful_sync="2026-11-20T12:00:00Z", last_error=None)
        account = dict(common, plan={"account_id": "100"},
                       rows=[dict(account_id="100", currency="USD", timezone_name="UTC")])
        ads = dict(common, plan=dict(account_id="100", operation="insights", since="2026-11-18",
                                    until="2026-11-18", version="v27.0"), rows=rows)
        capture = dict(workspace_identity_sha256="fixture", sources=dict(account=account, ads=ads))
        with patch.object(multi_account_review, "read", return_value={"assets": {"ACCOUNT:100": {}}}), \
                patch.object(multi_account_review, "workspace_identity", return_value="fixture"), \
                patch.object(multi_account_review, "read_run_observations", return_value=(capture, {})):
            result = multi_account_review.insights_slice(Path("fixture"), Path("receipt"), "100", "adset", "21")
            missing = multi_account_review.insights_slice(Path("fixture"), Path("receipt"), "100", "ad", "999")
        self.assertEqual(result["analysis"]["metrics"]["spend"]["value"], "3.50")
        self.assertEqual(result["analysis"]["row_count"], 1)
        self.assertEqual(missing["status"], "ACCOUNT_INSIGHTS_ID_NOT_OBSERVED")

    def test_account_sections_keep_currency_and_timezone_but_reject_other_workspace(self):
        common = dict(result="COMPLETE", observed_at="2026-11-20T12:00:00Z", pages=1, generation=1,
                      last_successful_sync="2026-11-20T12:00:00Z", last_error=None)

        def source(account_id, currency, zone, workspace="fixture-workspace"):
            account = dict(common, plan={"account_id": account_id},
                           rows=[dict(account_id=account_id, currency=currency, timezone_name=zone)])
            ads = dict(common, plan=dict(account_id=account_id, operation="insights", since="2026-11-18",
                                        until="2026-11-18", version="v27.0"), rows=[])
            return dict(workspace_identity_sha256=workspace, sources={"account": account, "ads": ads}), {}

        profile = {"assets": {"ACCOUNT:100": {}, "ACCOUNT:200": {}}}
        with patch.object(multi_account_review, "read", return_value=profile):
            with patch.object(multi_account_review, "workspace_identity", return_value="fixture-workspace"):
                with patch.object(multi_account_review, "read_run_observations",
                                  side_effect=[source("100", "USD", "UTC"), source("200", "EUR", "Asia/Tokyo")]):
                    result = multi_account_review.summarize(Path("fixture"), [Path("a"), Path("b")])
                self.assertIsNone(result["shared_totals"])
                contexts = [section["report"]["context"] for section in result["accounts"]]
                self.assertEqual([(r["currency"], r["timezone"]) for r in contexts],
                                 [("USD", "UTC"), ("EUR", "Asia/Tokyo")])
                with patch.object(multi_account_review, "read_run_observations",
                                  return_value=source("100", "USD", "UTC", "other")):
                    with self.assertRaisesRegex(InputError, "MULTI_ACCOUNT_WORKSPACE_MISMATCH"):
                        multi_account_review.summarize(Path("fixture"), [Path("a")])


class LocalScheduleTest(unittest.TestCase):
    def test_cold_report_uses_saved_timestamp_and_marks_stale(self):
        with patch.object(commerce_watch, "utcnow", return_value=datetime(2026, 11, 26, tzinfo=timezone.utc)):
            result = commerce_watch._result({
                "successful_at": "2026-11-23T00:00:00+00:00", "report": {"source": "prior"},
                "last_error": None, "cycles": 1,
            }, 0, "REPORT_AVAILABLE")
        self.assertEqual(result["freshness"], "STALE")
        self.assertEqual(result["report_age_seconds"], 259200)
        self.assertEqual(result["completed_cycles"], 0)
