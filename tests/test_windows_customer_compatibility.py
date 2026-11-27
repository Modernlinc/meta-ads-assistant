"""Platform behavior checked without accessing any real credential or account."""
import ctypes
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_v1_customer_workflows import ROOT, run_customer

sys.path.insert(0, str(ROOT / "codex-ads-live" / "src"))
from codex_ads_live import native_credentials, onboarding
from codex_ads_live.validation import InputError


class WindowsCustomerCompatibilityTest(unittest.TestCase):
    def test_windows_preflight_labels_client_build_without_claiming_servicing(self):
        windows = SimpleNamespace(major=10, minor=0, build=26200, product_type=1)
        with patch.object(onboarding.platform, "system", return_value="Windows"), patch.object(
                onboarding.sys, "getwindowsversion", return_value=windows, create=True):
            result = onboarding.preflight()
        self.assertEqual(result["checks"]["operating_system"], "IN_SCOPE")
        self.assertEqual(result["windows"]["family"], "WINDOWS_11")
        self.assertEqual(result["windows"]["build"], 26200)
        self.assertFalse(result["windows"]["servicing_verified"])
        self.assertFalse(result["checks_are_native_acceptance"])

    def test_windows_server_and_unrecognized_build_are_not_labeled_supported_clients(self):
        for data in (dict(major=10, minor=0, build=26100, product_type=3),
                     dict(major=10, minor=0, build=10240, product_type=1)):
            with self.subTest(data=data), patch.object(onboarding.platform, "system", return_value="Windows"), patch.object(
                    onboarding.sys, "getwindowsversion", return_value=SimpleNamespace(**data), create=True):
                result = onboarding.preflight()
                self.assertEqual(result["checks"]["operating_system"], "WINDOWS_CLIENT_BUILD_REVIEW_REQUIRED")

    def test_power_shell_utf8_bom_fixture_reopens_at_normal_entry(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "客户 素材"
            root.mkdir()
            doc = root / "接入 权限.json"
            doc.write_text(
                '{"schema":"meta-access-readiness-v1","task":"ADS_REPORT","app_user_role":"ON_APP",'
                '"asset_role":"UNKNOWN","grants":{"ads_read":"UNKNOWN"}}', encoding="utf-8-sig")
            code, reply = run_customer("meta-access-review", "--input", doc)
            self.assertEqual(code, 0, reply)
            self.assertEqual(reply["status"], "META_PREREQUISITES_REVIEW")
            self.assertFalse(reply["actions_authorized"])


class WindowsCredentialBoundaryTest(unittest.TestCase):
    # On every host the only native library is an in-memory fake.
    def load(self, data, *, user="200.300", present=True):
        def read(target, kind, flags, output):
            self.assertEqual((target, kind, flags), ("mock-item", 1, 0))
            if not present:
                return False
            pointer = output._obj
            self.item = pointer._type_()
            self.blob = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
            self.item.Type = 1
            self.item.TargetName = target
            self.item.UserName = user
            self.item.CredentialBlob = self.blob
            self.item.CredentialBlobSize = len(data)
            pointer.contents = self.item
            return True
        return SimpleNamespace(CredReadW=Mock(side_effect=read), CredFree=Mock())

    def test_generic_credential_can_have_one_windows_utf16_terminator(self):
        lib = self.load("synthetic-placeholder\0".encode("utf-16-le"))
        with patch.object(ctypes, "WinDLL", return_value=lib, create=True):
            value = native_credentials._windows_read("mock-item", "200.300")
        self.assertEqual(value, "synthetic-placeholder")
        lib.CredFree.assert_called_once()

    def test_deleted_mismatched_or_malformed_credential_fails_closed(self):
        cases = (
            (self.load(b"", present=False), "NATIVE_CREDENTIAL_UNAVAILABLE", 0),
            (self.load("invalid".encode("utf-16-le"), user="900.999"), "NATIVE_CREDENTIAL_IDENTITY_MISMATCH", 1),
            (self.load(b"\x00\xd8"), "NATIVE_CREDENTIAL_ENCODING", 1),
        )
        for lib, error, freed in cases:
            with self.subTest(error=error), patch.object(ctypes, "WinDLL", return_value=lib, create=True):
                with self.assertRaisesRegex(InputError, error):
                    native_credentials._windows_read("mock-item", "200.300")
            self.assertEqual(lib.CredFree.call_count, freed)


if __name__ == "__main__":
    unittest.main()
