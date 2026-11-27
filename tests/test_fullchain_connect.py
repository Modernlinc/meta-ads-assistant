"""Exercise the installed CLI handler and replace only terminal/native HTTPS boundaries."""
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codex-ads-live" / "src"))

from codex_ads_live.__main__ import main
from codex_ads_live.credential_record import prepare_record
from codex_ads_live.validation import InputError
from codex_ads_live.workspace_connect import discover_pages


class OwnerTerminal(StringIO):
    def isatty(self):
        return True


class WorkspaceConnectTest(unittest.TestCase):
    def command(self, *args, terminal=False):
        stdout = OwnerTerminal() if terminal else StringIO()
        with redirect_stdout(stdout):
            code = main(list(map(str, args)))
        output = stdout.getvalue()
        start = output.index("{")
        return code, json.loads(output[start:])

    def test_fresh_and_resumed_setup_have_distinct_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder).resolve() / "project"
            args = ("workspace-connect", "--workspace", target)
            self.assertEqual(self.command(*args)[1]["step"], "INITIALIZE_WORKSPACE")
            self.assertEqual(self.command(*args, "--start")[1]["step"], "CHOOSE_ROUTE")
            self.assertEqual(self.command(*args, "--route", "host-connector")[1]["step"], "CONNECT_IN_OFFICIAL_HOST")
            self.assertEqual(self.command(*args)[1]["step"], "CONNECT_IN_OFFICIAL_HOST")
            step = self.command(*args, "--route", "user-graph", "--app-id", "101", "--business-id", "102")[1]
            self.assertEqual(step["step"], "ENROLL_USER_REFERENCE")
            self.assertFalse(step["read_authorized"])

    @unittest.skipUnless(sys.platform in ("darwin", "win32"), "record platform must be supported")
    def test_cli_pagination_bad_second_page_can_select_only_observed(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder).resolve() / "project"
            args = ("workspace-connect", "--workspace", target)
            self.command(*args, "--start", "--route", "user-graph", "--app-id", "101", "--business-id", "102")
            platform = "macos" if sys.platform == "darwin" else "windows"
            prepare_record(target / "credential-record", "101", "103", platform)
            requests = []

            @contextmanager
            def fake_lease(self, binding):
                yield "synthetic_test_placeholder"

            def fake_get(path, credential, check, **kwargs):
                check()
                request = urlsplit(path)
                requests.append((request.path, parse_qs(request.query)))
                if len(requests) == 2:
                    raise InputError("PERMISSION")
                return {"data": [{"id": "act_104", "account_id": "104", "currency": "USD", "account_status": 1}],
                        "paging": {"next": "https://ignored.invalid/", "cursors": {"after": "Next1"}}}

            expiry = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
            metadata = {"data": {"is_valid": True, "app_id": "101", "user_id": "103", "type": "USER",
                                 "scopes": ["ads_read"], "expires_at": expiry, "data_access_expires_at": expiry}}
            boundary = "codex_ads_live.workspace_connect."
            with (patch("sys.stdin", OwnerTerminal()), patch("builtins.input", return_value="LIST AD ACCOUNTS WITH APP 101 FOR 103"),
                  patch(boundary + "NativeCredentialBroker.lease", fake_lease),
                  patch(boundary + "_debug", return_value=metadata), patch(boundary + "_get_connection_json", side_effect=fake_get)):
                code, response = self.command(*args, "--discover", "--proof-not-required", terminal=True)
            self.assertEqual(code, 0)
            self.assertEqual([item[0] for item in requests], ["/v27.0/me/adaccounts"] * 2)
            self.assertEqual(requests[1][1]["after"], ["Next1"])
            self.assertEqual(response["discovery"]["state"], "PARTIAL")
            self.assertEqual(response["discovery"]["pages_read"], 1)
            self.assertEqual(response["step"], "SELECT_ACCOUNT")
            code, rejected = self.command(*args, "--select-account", "105", "--ownership-evidence", "EV_OWNED")
            self.assertEqual((code, rejected["error_code"]), (2, "META_CONNECT_ACCOUNT_NOT_DISCOVERED"))
            code, selected = self.command(*args, "--select-account", "104", "--ownership-evidence", "EV_OWNED")
            self.assertEqual((code, selected["account_id"], selected["step"]), (0, "104", "CHECK_NAMED_ACCOUNT"))
            self.assertEqual(self.command(*args)[1]["account_id"], "104")
            self.assertFalse(selected["write_authorized"])

    def test_empty_complete_and_two_pages(self):
        self.assertEqual(discover_pages(lambda _: {"data": []})["state"], "COMPLETE")
        pages = iter([
            {"data": [{"id": "act_11", "account_id": "11"}],
             "paging": {"next": "ignore", "cursors": {"after": "token"}}},
            {"data": [{"id": "act_22", "account_id": "22"}]},
        ])
        result = discover_pages(lambda _: next(pages))
        self.assertEqual(result["state"], "COMPLETE")
        self.assertEqual([row["account_id"] for row in result["accounts"]], ["11", "22"])

    @unittest.skipUnless(sys.platform in ('darwin','win32'), 'record platform must be supported')
    def test_synthetic_system_user_discovers_then_reads_without_user_token_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp).resolve()/'system-workspace'
            base = ('--workspace',workspace)
            initial = self.command('workspace-connect',*base,'--start','--route','system-user',
                                   '--app-id','501','--business-id','502')
            self.assertEqual(initial[1]['step'],'SYSTEM_USER_CONFIGURATION')
            platform = 'macos' if sys.platform=='darwin' else 'windows'
            prepare_record(workspace/'system-credential-record','501','503',platform)
            prepare_record(workspace/'app-inspector-record','501','504',platform)
            requests = []
            deadline = int((datetime.now(timezone.utc)+timedelta(hours=3)).timestamp())
            scope = {'ads':'synthetic_system_scope','app':'synthetic_inspector_scope'}

            @contextmanager
            def lease(self,binding):
                yield scope['ads'] if binding.principal_id=='503' else scope['app']

            def debug(auth, verify, *, candidate, **_):
                self.assertEqual((auth,candidate),(scope['app'],scope['ads']))
                verify()
                return dict(data=dict(is_valid=True,app_id='501',user_id='503',expires_at=deadline,scopes=['ads_read']))

            def get(path, auth, verify, **_):
                verify()
                endpoint=urlsplit(path).path
                requests.append(endpoint)
                if endpoint.endswith('/system_users'):
                    self.assertEqual(auth,scope['app'])
                    return dict(data=[dict(id='503',role='EMPLOYEE')])
                self.assertEqual(auth,scope['ads'])
                account = dict(id='act_101',account_id='101',account_status=1,currency='USD',timezone_name='UTC')
                return {'/v27.0/me':{'id':'503'},'/v27.0/me/adaccounts':{'data':[account]},
                        '/v27.0/act_101':account}[endpoint]

            boundary='codex_ads_live.system_user_read.'
            with (patch('sys.stdin',OwnerTerminal()),
                  patch('builtins.input',side_effect=('LIST AD ACCOUNTS SYSTEM USER 503 APP 501',
                                                     'READ INVENTORY ACCOUNT act_101 ACCOUNT 101 SYSTEM USER 503 APP 501')),
                  patch(boundary+'NativeCredentialBroker.lease',lease),patch(boundary+'_debug',side_effect=debug),
                  patch(boundary+'_get_connection_json',side_effect=get)):
                code, selected=self.command('workspace-connect',*base,'--discover','--proof-not-required',terminal=True)
                self.assertEqual((code,selected['step']),(0,'SELECT_ACCOUNT'))
                code, selected=self.command('workspace-connect',*base,'--select-account','101',
                                            '--ownership-evidence','EV_KNOWN_OWNER')
                self.assertEqual((code,selected['step']),(0,'SYSTEM_USER_READ_READY'))
                output=workspace.parent/'inventory'
                code, observed=self.command('native-inventory','get','account',*base,'--output',output,
                                            '--execute-read',terminal=True)
            self.assertEqual(code,0,observed)
            self.assertEqual(observed['rows'][0]['id'],'act_101')
            self.assertEqual(requests,['/v27.0/502/system_users','/v27.0/me','/v27.0/me/adaccounts',
                                       '/v27.0/502/system_users','/v27.0/me','/v27.0/act_101','/v27.0/act_101'])
            self.assertNotIn(scope['ads'],(output/'NATIVE-INVENTORY.json').read_text())

    def test_official_host_receipt_selects_original_account_without_native_credentials(self):
        from codex_ads_live.client_workspace import draft_review_scope, map_asset, read

        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp).resolve()
            workspace=folder/'host-workspace'
            base=('--workspace',workspace)
            self.command('workspace-connect',*base,'--start','--route','host-connector')
            receipt=folder/'source.json'
            receipt.write_text(json.dumps(dict(schema='meta-host-account-list-v1',
                observed_at=datetime.now(timezone.utc).isoformat(),coverage='COMPLETE',pages_read=1,
                source=dict(provider='META_OFFICIAL_ADS_MCP',host='Fixture',ref='EV_HOST_FIXTURE'),
                accounts=[dict(account_id='105',currency='USD',account_status=1,name='Scoped host account')])))
            code, guidance=self.command('workspace-connect',*base,'--host-receipt',receipt)
            self.assertEqual((code,guidance['step']),(0,'SELECT_ACCOUNT'))
            self.assertEqual(guidance['discovery']['accounts'][0]['name'],'Scoped host account')
            code, guidance=self.command('workspace-connect',*base,'--select-account','105','--ownership-evidence','EV_OWNER')
            self.assertEqual((code,guidance['step']),(0,'HOST_ACCOUNT_READY'))
            self.assertFalse(guidance['host_permission_verified_locally'])
            self.assertFalse(guidance['write_authorized'])
            profile=read(workspace)
            self.assertIsNone(profile['binding'])
            self.assertEqual(draft_review_scope(profile,'105',host_draft=True)['account_id'],'105')
            with self.assertRaisesRegex(InputError,'WORKSPACE_DRAFT_REVIEW_PROFILE_REQUIRED'):
                draft_review_scope(profile,'105')
            map_asset(workspace,'PAGE','500','EV_PAGE')
            local=folder/'local-only'
            code, draft=self.command('workspace-first-launch-plan',*base,'--page-id','500','--output',local)
            self.assertEqual(code,0,draft)
            self.assertFalse(draft['dispatchable'])
            code, state=self.command('workspace-first-launch-progress',*base,'--plan-dir',local)
            self.assertEqual((code,state['status']),(0,'FIRST_LAUNCH_HOST_CONNECTOR_DRAFT_ONLY'))
            code, denied=self.command('workspace-first-launch-progress',*base,'--plan-dir',local,'--reserve')
            self.assertEqual((code,denied['error_code']),(2,'FIRST_LAUNCH_HOST_CONNECTOR_DRAFT_ONLY'))
            code, rejected=self.command('native-inventory','get','account',*base,'--output',folder/'report','--execute-read')
            self.assertEqual((code,rejected['error_code']),(2,'META_HOST_REPORTING_REQUIRES_ORIGINAL_CONNECTOR'))
