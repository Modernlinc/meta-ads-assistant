"""Client paths crossing Meta, Shopify and commerce reporting boundaries."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlsplit

import test_fullchain_connect as connect_tests

OwnerTerminal = connect_tests.OwnerTerminal
from codex_ads_live import ads_library, client_workspace
from codex_ads_live.native_insights import NativeInsightsPlan, collect_rows
from codex_ads_live.strategy_plan import compile_selection


class FullchainReportTest(unittest.TestCase):
    command = connect_tests.WorkspaceConnectTest.command

    def workspace(self, temp):
        target = Path(temp).resolve() / "current"
        client_workspace.initialize(target)
        client_workspace.map_asset(target, "ACCOUNT", "101", "EV_OWNER")
        client_workspace.default_asset(target, "ACCOUNT", "101")
        return target

    def test_tracking_cli_produces_reusable_audit_input_and_preserves_custom_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.workspace(tmp)
            output = workspace.parent / "tracking"
            code, data = self.command("tracking-prepare", "--workspace", workspace, "--output", output,
                                      "--destination", "https://shop.example.com/offer", "--dataset", "103",
                                      "--event", "OrderAfterFraudCheck", "--trigger", "订单确认后",
                                      "--campaign", "spring", "--stack", "shopify-native")
            self.assertEqual(code, 0)
            self.assertEqual(data["configuration"]["primary_event"]["kind"], "CUSTOM")
            self.assertFalse(data["production_events_observed"])
            code, audit = self.command("tracking-audit", "--input", output / "tracking-input.json")
            self.assertEqual(code, 0)
            self.assertEqual(set(audit["utm_keys"]), {"utm_campaign", "utm_medium", "utm_source"})
            self.assertEqual(audit["observed_events"], 0)
            from codex_ads_live.tracking_setup import consume_configuration

            scope = dict(workspace_identity=data['workspace_sha256'], account_id='101')
            configured = json.loads((output/'tracking-input.json').read_text())
            configured['events'] = [dict(channel='PIXEL', name='OrderAfterFraudCheck',
                                         event_id='EV_FIXTURE_TEST', mode='TEST')]
            signal = consume_configuration(output/'TRACKING-CONFIGURATION.json', scope, configured, 'PURCHASE')
            self.assertEqual(signal['goal_match'], 'REVIEW_CUSTOM_EVENT_SEMANTICS')
            self.assertEqual(signal['evidence_counts'], {'TEST':1, 'PRODUCTION':0})
            self.assertFalse(signal['native_receipt_verified'])

    def test_real_shopify_cli_uses_fixed_https_and_reuses_workspace_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.workspace(tmp)
            store, shop, zone = "demo-shop.myshopify.com", "gid://shopify/Shop/5", "America/Los_Angeles"
            args = ("--workspace", workspace)
            code, requested = self.command("shopify-bind", *args, "--store", store, "--shop-id", shop,
                                           "--currency", "USD", "--timezone", zone)
            self.assertEqual(code, 0)
            self.assertTrue(requested["credential_service"].startswith("ModernLinc.CodexAdsLive.Shopify."))
            requests = []

            @contextmanager
            def fake_lease(binding):
                yield "synthetic_shopify_placeholder"

            class Response(BytesIO):
                status = 200

            class Connection:
                def __init__(self, host, **_):
                    self.host = host

                def request(self, method, path, body, headers):
                    requests.append((self.host, method, path, body, headers.copy()))

                def getresponse(self):
                    if len(requests) == 1:
                        body = dict(
                            shop=dict(id=shop, myshopifyDomain=store, currencyCode="USD", ianaTimezone=zone),
                            currentAppInstallation=dict(accessScopes=[dict(handle="read_orders")]))
                    else:
                        body = dict(shop=dict(id=shop, myshopifyDomain=store),
                                    orders=dict(nodes=[], pageInfo=dict(hasNextPage=False)))
                    return Response(json.dumps({"data": body}).encode())

                def close(self):
                    pass

            from codex_ads_live.shopify_transport import read_snapshot
            today = datetime.now(ZoneInfo(zone)).date().isoformat()
            output = workspace.parent / "shopify"
            fqn = "codex_ads_live.shopify_transport."
            with (patch("sys.stdin", OwnerTerminal()),
                  patch("builtins.input", return_value=f"READ SHOPIFY {store} {today} {today}"),
                  patch(fqn + "credential_lease", fake_lease),
                  patch(fqn + "http.client.HTTPSConnection", Connection)):
                code, result = self.command("shopify-read", *args, "--output", output,
                                            "--since", today, "--until", today, terminal=True)
            self.assertEqual(code, 0, result)
            self.assertEqual(result["status"], "SHOPIFY_READ_COMPLETE")
            self.assertEqual(len(requests), 2)
            for host, method, path, packet, headers in requests:
                self.assertEqual((host, method, path), (store, "POST", "/admin/api/2026-10/graphql.json"))
                self.assertTrue(json.loads(packet)["query"].startswith("query "))
                self.assertIn("X-Shopify-Access-Token", headers)
            saved = output / "SHOPIFY-SNAPSHOT.json"
            self.assertNotIn("synthetic_shopify_placeholder", saved.read_text())
            observed = read_snapshot(workspace, saved)
            self.assertEqual(observed["source"], "SHOPIFY_ADMIN_HTTPS")
            self.assertEqual(observed["refund_transaction_evidence"]["successful_amount"], "0")

    def test_shopify_https_resumes_after_permission_loss_and_separates_older_cash_refunds(self):
        from codex_ads_live.shopify_transport import read_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.workspace(tmp)
            store, shop, zone = 'fixture-shop.myshopify.com', 'gid://shopify/Shop/5', 'UTC'
            args = ('--workspace', workspace)
            self.assertEqual(self.command('shopify-bind', *args, '--store', store, '--shop-id', shop,
                                         '--currency', 'USD', '--timezone', zone)[0], 0)
            window = datetime.now(timezone.utc).date()
            day = window.isoformat()
            earlier = (window - timedelta(days=4)).isoformat()
            shop_details = dict(id=shop, myshopifyDomain=store)
            access = dict(shop=dict(shop_details, currencyCode='USD', ianaTimezone=zone),
                          currentAppInstallation=dict(accessScopes=[{'handle':'read_orders'},{'handle':'read_all_orders'}]))
            amount = {'shopMoney': {'amount': '8.25', 'currencyCode': 'USD'}}
            writes = []
            first_page = dict(shop=shop_details, orders=dict(nodes=[], pageInfo=dict(hasNextPage=True,endCursor='page-2')))
            second_page = dict(shop=shop_details, orders=dict(nodes=[], pageInfo=dict(hasNextPage=False)))
            older = dict(id='gid://shopify/Order/40', createdAt=earlier+'T10:00:00+00:00',
                         updatedAt=day+'T10:00:00+00:00', refunds=[
                dict(id='gid://shopify/Refund/35', transactions=dict(pageInfo={'hasNextPage':False}, nodes=[
                    dict(id='gid://shopify/OrderTransaction/48', status='SUCCESS', kind='REFUND',
                         processedAt=day+'T10:00:00+00:00', gateway='fixture', amountSet=amount),
                    dict(id='gid://shopify/OrderTransaction/49', status='PENDING', kind='REFUND',
                         processedAt=None, gateway='fixture', amountSet=amount),
                ])),
            ])
            cash = dict(shop=shop_details, orders=dict(nodes=[older], pageInfo={'hasNextPage':False}))
            exchanges = iter([(200,access), (200,first_page), (403,None), (200,access), (200,second_page), (200,cash)])

            @contextmanager
            def fake_lease(binding):
                yield 'synthetic_shopify_placeholder'

            class Response(BytesIO):
                def __init__(self, status, result):
                    super().__init__(json.dumps(dict(data=result)).encode())
                    self.status = status

                def getheader(self, _):
                    return None

            class Connection:
                def __init__(self, _host, **_):
                    pass

                def request(self, method, path, body, headers):
                    payload = json.loads(body)
                    writes.append((payload['query'].split()[1].split('(')[0], payload['variables'].get('after')))

                def getresponse(self):
                    return Response(*next(exchanges))

                def close(self):
                    pass

            output = workspace.parent/'shopify'
            query = ('shopify-read', *args, '--output', output, '--since', day, '--until', day,
                     '--include-cross-window-refunds')
            prefix = 'codex_ads_live.shopify_transport.'
            with (patch('sys.stdin', OwnerTerminal()),
                  patch('builtins.input', return_value=f'READ SHOPIFY {store} {day} {day} EARLIER REFUNDS'),
                  patch(prefix+'credential_lease', fake_lease),
                  patch(prefix+'http.client.HTTPSConnection', Connection)):
                initial = self.command(*query, terminal=True)
                resumed = self.command(*query,'--resume',terminal=True)
            self.assertIn('UNAUTHORIZED', initial[1]['coverage_gaps'])
            self.assertEqual(resumed[1]['status'], 'SHOPIFY_READ_COMPLETE', resumed)
            self.assertEqual(writes,[('StoreAccess',None), ('Orders',None), ('Orders','page-2'),
                                    ('StoreAccess',None), ('Orders','page-2'), ('CrossWindowRefunds',None)])
            saved = read_snapshot(workspace, output/'SHOPIFY-SNAPSHOT.json')
            receipt = saved['refund_transaction_evidence']
            self.assertEqual(receipt['successful_amount'], '8.25')
            self.assertEqual(receipt['successful_ids'], ['gid://shopify/OrderTransaction/48'])
            self.assertEqual(saved['totals']['line_refunds'], '0')

    def test_native_campaign_uses_returned_reach_and_not_daily_ad_aggregation(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.workspace(tmp)
            output = directory.parent / "insights"
            mode = ("native-insights", "--workspace", directory, "--output", output)
            code, preview = self.command(*mode, "--level", "campaign", "--id", "303",
                                         "--since", "2026-11-01", "--until", "2026-11-07")
            self.assertEqual(code, 0)
            plan = NativeInsightsPlan("101", "campaign", "303", "2026-11-01", "2026-11-07")
            self.assertEqual(preview["request"], plan.request())

            class Transport:
                def get(self, request):
                    if request["path"] != "/v27.0/303/insights":
                        raise AssertionError(request)
                    return 200, dict(data=[dict(account_id="101", campaign_id="303",
                        date_start="2026-11-01", date_stop="2026-11-07", reach="120", frequency="1.45",
                        impressions="174", account_currency="USD")])

            report = collect_rows(plan, Transport(), "USD", 3)
            self.assertEqual(report["basis"], "NATIVE_INSIGHTS")
            self.assertEqual(report["rows"][0]["metrics"]["reach"]["value"], "120")
            self.assertEqual(report["rows"][0]["metrics"]["spend"]["state"], "FIELD_ABSENT")

    @unittest.skipUnless(sys.platform in ('darwin', 'win32'), 'record platform must be supported')
    def test_native_report_async_resumes_original_job_and_feeds_daily_spend(self):
        from codex_ads_live.credential_record import prepare_record, read_record
        from codex_ads_live.journal import digest
        from codex_ads_live.native_insights import commerce_spend

        with tempfile.TemporaryDirectory() as tmp:
            directory = self.workspace(tmp)
            record_dir = directory / 'credential-record'
            prepare_record(record_dir, '501', '503', 'macos' if sys.platform == 'darwin' else 'windows')
            record = read_record(record_dir)
            now = datetime.now(timezone.utc)
            expiry = now + timedelta(hours=1)

            def connect(profile):
                profile.update(app_id='501', business_id='502')
                profile['binding'] = dict(accounts=['101'], app_id='501', auth_status='CURRENT',
                    expires_at=expiry.isoformat(), principal_id='503', secret_ref=record['reference'],
                    record_hash=digest(record), proof='NOT_REQUIRED_CONFIRMED',
                    owner_confirmed=True, developer_confirmed=True)
                profile['binding'].pop('app_id')
                profile['checks']['ACCOUNT:101'] = dict(at=now.isoformat(), status='HEALTHY',
                    permissions=['ads_read'], expires=expiry.isoformat(), currency='USD', timezone='America/Los_Angeles')

            client_workspace.update(directory, connect)
            requests = []
            levels = (('account','101','act_101','150'), ('campaign','701','701','109'),
                      ('adset','702','702','84'), ('ad','703','703','41'))
            responses = iter([
                dict(report_run_id='701'),
                dict(id='701', account_id='101', async_status='Job Running', async_percent_completion=30),
                dict(id='701', account_id='101', async_status='Job Completed', async_percent_completion=100),
                dict(data=[dict(account_id='101', date_start='2026-11-01', date_stop='2026-11-01',
                                account_currency='USD', spend='12.34', reach='120')],
                     paging={'next':'https://ignored.invalid', 'cursors':{'after':'SameJob2'}}),
                dict(id='701', account_id='101', async_status='Job Completed', async_percent_completion=100),
                dict(data=[dict(account_id='101', date_start='2026-11-02', date_stop='2026-11-02',
                                account_currency='USD', reach='15')]),
                *[dict(data=[dict(dict(account_id='101',date_start='2026-11-01',date_stop='2026-11-02',
                                      account_currency='USD',reach=reach),
                                  **({level+'_id':target} if level!='account' else {}))])
                  for level,target,_,reach in levels],
            ])

            @contextmanager
            def synthetic_lease(self, binding):
                yield 'synthetic_native_fixture'

            class Response(BytesIO):
                status = 200

            class Connection:
                def request(self, method, path, body=None, headers=None):
                    requests.append((method, path, body))
                    assert headers['Authorization'] == 'Bearer synthetic_native_fixture'

                def getresponse(self):
                    return Response(json.dumps(next(responses)).encode())

                def close(self):
                    pass

            meta = dict(data=dict(is_valid=True, app_id='501', user_id='503', type='USER', scopes=['ads_read'],
                                  expires_at=int(expiry.timestamp()), data_access_expires_at=int(expiry.timestamp())))
            account = dict(id='act_101', account_id='101', currency='USD', timezone_name='America/Los_Angeles')
            output = directory.parent / 'native-output'
            job = directory.parent / 'native-job'
            args = ('native-insights', '--workspace', directory, '--output', output,
                    '--level', 'account', '--since', '2026-11-01', '--until', '2026-11-02',
                    '--report-mode', 'async', '--job-state', job, '--daily', '--execute-read', '--page-budget', '1')
            approval = 'READ ACCOUNT 101 ACCOUNT 101 2026-11-01 2026-11-02 REPORT JOB '
            with (patch('sys.stdin', OwnerTerminal()),
                  patch('builtins.input', side_effect=(approval + 'CREATE', approval + 'RESUME', approval + 'RESUME',
                        *(f'READ {level.upper()} {target} ACCOUNT 101 2026-11-01 2026-11-02'
                          for level,target,_,_ in levels))),
                  patch('codex_ads_live.native_insights.NativeCredentialBroker.lease', synthetic_lease),
                  patch('codex_ads_live.native_insights._debug', return_value=meta),
                  patch('codex_ads_live.native_insights._get_connection_json', side_effect=lambda path,*args,**kwargs:
                        account if urlsplit(path).path.endswith('/act_101') else
                        dict(id=urlsplit(path).path.rsplit('/',1)[1],account_id='101')),
                  patch('codex_ads_live.http_read.graph_https_connection', return_value=Connection())):
                initial = self.command(*args, terminal=True)
                partial = self.command(*args, '--resume', terminal=True)
                final = self.command(*args, '--resume', terminal=True)
                async_requests = list(requests)
                for level,target,node,reach in levels:
                    flags = ('--id',target) if level!='account' else ()
                    with self.subTest(level=level):
                        code, result = self.command('native-insights','--workspace',directory,
                            '--level',level,'--since','2026-11-01','--until','2026-11-02','--execute-read',
                            '--output',directory.parent/f'native-{level}',*flags,terminal=True)
                        self.assertEqual(code,0,result)
                        self.assertEqual(result['report']['rows'][0]['metrics']['reach']['value'],reach)
                        method,path,_ = requests[-1]
                        self.assertEqual((method,urlsplit(path).path),('GET',f'/v27.0/{node}/insights'))
                        self.assertEqual(parse_qs(urlsplit(path).query)['level'],[level])
            self.assertEqual(initial[1]['status'], 'NATIVE_INSIGHTS_JOB_PENDING', initial)
            self.assertEqual(partial[1]['status'], 'NATIVE_INSIGHTS_JOB_RESULTS_INCOMPLETE', partial)
            self.assertEqual(partial[1]['rows_preserved'], 1)
            self.assertEqual(final[1]['status'], 'NATIVE_INSIGHTS_OBSERVED', final)
            self.assertEqual([(m, urlsplit(p).path) for m, p, _ in async_requests], [
                ('POST', '/v27.0/act_101/insights'), ('GET', '/v27.0/701'),
                ('GET', '/v27.0/701'), ('GET', '/v27.0/701/insights'),
                ('GET', '/v27.0/701'), ('GET', '/v27.0/701/insights')])
            self.assertEqual(parse_qs(urlsplit(async_requests[-1][1]).query)['after'], ['SameJob2'])
            self.assertEqual(parse_qs(async_requests[0][2])['time_increment'], ['1'])
            spend, _ = commerce_spend(directory, output / 'NATIVE-INSIGHTS.json', '101', 'USD',
                                      'America/Los_Angeles', '2026-11-01', '2026-11-02', 2)
            self.assertEqual(spend, {('2026-11-01',): dict(date='2026-11-01', spend_minor='1234')})
            self.assertNotIn('synthetic_native_fixture', (output / 'NATIVE-INSIGHTS.json').read_text())

    @unittest.skipUnless(sys.platform in ('darwin', 'win32'), 'record platform must be supported')
    def test_native_inventory_reads_existing_parents_and_resumes_original_edge(self):
        from codex_ads_live.credential_record import prepare_record, read_record
        from codex_ads_live.journal import digest
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            directory = self.workspace(tmp)
            client_workspace.map_asset(directory,'PAGE','500','EV_PAGE')
            record_dir = directory/'credential-record'
            prepare_record(record_dir, '501', '503', 'macos' if sys.platform == 'darwin' else 'windows')
            record = read_record(record_dir)
            now = datetime.now(timezone.utc)
            expiry = now+timedelta(hours=1)

            def connect(profile):
                profile.update(app_id='501', business_id='502')
                profile['binding'] = dict(accounts=['101'], auth_status='CURRENT', expires_at=expiry.isoformat(),
                    principal_id='503', secret_ref=record['reference'], record_hash=digest(record),
                    proof='NOT_REQUIRED_CONFIRMED', owner_confirmed=True, developer_confirmed=True)
                profile['checks']['ACCOUNT:101'] = dict(at=now.isoformat(), status='HEALTHY',
                    permissions=['ads_read'], expires=expiry.isoformat(), currency='USD', timezone='UTC')

            client_workspace.update(directory, connect)
            calls = []

            @contextmanager
            def lease(self, _binding):
                yield 'synthetic_inventory_only'

            def get(path, _credential, verify, **_):
                verify()
                url = urlsplit(path)
                params = parse_qs(url.query)
                calls.append((url.path,params.get('after')))
                if url.path == '/v27.0/act_101':
                    return dict(id='act_101', account_id='101', currency='USD', timezone_name='UTC')
                if url.path == '/v27.0/754':
                    return dict(id='754', account_id='101', status='PAUSED', objective='OUTCOME_SALES')
                if url.path == '/v27.0/771':
                    return dict(id='771', account_id='101', campaign_id='754', status='PAUSED')
                if url.path == '/v27.0/791':
                    return dict(id='791', account_id='101', campaign_id='754', adset_id='771',
                                creative={'id':'799'})
                if url.path == '/v27.0/799':
                    return dict(id='799', account_id='101', title='Only the scoped creative')
                if url.path in ('/v27.0/act_101/activities', '/v27.0/771/activities'):
                    self.assertEqual(params['business_id'], ['502'])
                    return dict(data=[
                        dict(object_id='771', event_type='update_ad_set', event_time='2026-11-06T04:00:00Z'),
                        dict(object_id='771', event_type='update_ad_set', event_time='2026-11-08T04:00:00Z'),
                    ])
                edges = {
                    '/v27.0/act_101/campaigns':'754', '/v27.0/act_101/adsets':'771',
                    '/v27.0/act_101/ads':'791', '/v27.0/754/ads':'791', '/v27.0/771/ads':'791',
                    '/v27.0/791/adcreatives':'799',
                }
                if url.path in edges:
                    return dict(data=[get('/v27.0/'+edges[url.path], _credential, verify)])
                if url.path == '/v27.0/754/adsets':
                    row = dict(id='772' if 'after' in params else '771', account_id='101', campaign_id='754', status='PAUSED')
                    page = {'data':[row]}
                    if 'after' not in params:
                        page['paging'] = {'next':'https://ignored.invalid/do-not-use', 'cursors':{'after':'cursor2'}}
                    return page
                if url.path == '/v27.0/':
                    self.assertEqual(params['ids'], ['771,772,773'])
                    return {'771':dict(id='771',account_id='101',campaign_id='754',status='PAUSED'),
                            '772':{'error':{'code':200}}}
                raise AssertionError(path)

            metadata = dict(data=dict(is_valid=True, app_id='501', user_id='503', type='USER', scopes=['ads_read'],
                expires_at=int(expiry.timestamp()), data_access_expires_at=int(expiry.timestamp())))
            args = ('native-inventory','list','adset','--workspace',directory,'--parent-kind','campaign',
                    '--parent-id','754','--page-budget','1')
            first = directory.parent/'inventory-page-one'
            second = directory.parent/'inventory-page-two'
            with (patch('sys.stdin', OwnerTerminal()),
                  patch('builtins.input', return_value='READ INVENTORY ADSET 754 ACCOUNT 101'),
                  patch('codex_ads_live.native_insights.NativeCredentialBroker.lease', lease),
                  patch('codex_ads_live.native_insights._debug', return_value=metadata),
                  patch('codex_ads_live.native_insights._get_connection_json', side_effect=get)):
                a = self.command(*args,'--output',first,'--execute-read',terminal=True)
                b = self.command(*args,'--output',second,'--execute-read','--resume-from',
                                 first/'NATIVE-INVENTORY.json',terminal=True)
                with patch('builtins.input', return_value='READ INVENTORY ADSET 771,772,773 ACCOUNT 101'):
                    batched = self.command('native-inventory','get','adset','--workspace',directory,'--id','771',
                                           '--id','772','--id','773','--output',directory.parent/'batch',
                                           '--execute-read',terminal=True)
                scenarios = (
                    ('get','account','act_101','act_101','act_101',()),
                    ('get','campaign','754','754','754',('--id','754')),
                    ('get','adset','771','771','771',('--id','771')),
                    ('get','ad','791','791','791',('--id','791')),
                    ('get','creative','799','799','799',('--id','799')),
                    ('list','campaign','act_101','act_101/campaigns','754',()),
                    ('list','adset','act_101','act_101/adsets','771',()),
                    ('list','ad','act_101','act_101/ads','791',()),
                    ('list','ad','754','754/ads','791',('--parent-kind','campaign','--parent-id','754')),
                    ('list','ad','771','771/ads','791',('--parent-kind','adset','--parent-id','771')),
                    ('list','creative','791','791/adcreatives','799',('--parent-kind','ad','--parent-id','791')),
                    ('list','activity','act_101','act_101/activities','771',()),
                    ('list','activity','771','771/activities','771',('--parent-kind','adset','--parent-id','771')),
                )
                for number,(action,kind,target,edge,ident,flags) in enumerate(scenarios):
                    with self.subTest(edge=edge):
                        phrase = f'READ INVENTORY {kind.upper()} {target} ACCOUNT 101'
                        if kind == 'activity':
                            flags += ('--since','2026-11-05','--until','2026-11-07')
                            phrase += ' 2026-11-05 TO 2026-11-07'
                        start = len(calls)
                        with patch('builtins.input', return_value=phrase):
                            code, result = self.command('native-inventory',action,kind,'--workspace',directory,
                                '--output',directory.parent/f'edge-{number}','--execute-read',*flags,terminal=True)
                        self.assertEqual(code,0,result)
                        self.assertEqual(result['coverage'],'COMPLETE')
                        self.assertEqual([x.get('id',x.get('object_id')) for x in result['rows']],[ident])
                        self.assertIn(('/v27.0/'+edge,None),calls[start:])
            self.assertEqual((a[0],a[1]['coverage']), (0,'PARTIAL'), a)
            self.assertEqual((b[0],b[1]['coverage']), (0,'COMPLETE'), b)
            self.assertEqual([row['id'] for row in b[1]['rows']], ['771','772'])
            self.assertEqual([row for row in calls if row[0]=='/v27.0/754/adsets'],
                             [('/v27.0/754/adsets',None),('/v27.0/754/adsets',['cursor2'])])
            self.assertEqual(batched[1]['coverage'],'PARTIAL')
            self.assertEqual([item['id'] for item in batched[1]['rows']],['771'])
            self.assertEqual(batched[1]['unavailable'],[dict(id='772',cause='PERMISSION'),
                                                        dict(id='773',cause='OBJECT_NOT_RETURNED')])
            self.assertNotIn('synthetic_inventory_only',(second/'NATIVE-INVENTORY.json').read_text())
            route = ('workspace-first-launch-update','--workspace',directory,'--ad-id','791',
                     '--existing-ad-snapshot',directory.parent/'edge-3/NATIVE-INVENTORY.json','--existing-page','500',
                     '--action','PAUSE_AD')
            code, proposal = self.command(*route)
            self.assertEqual(code,0,proposal)
            self.assertEqual(proposal['proposal']['objects']['ad_id'],'791')
            self.assertEqual(proposal['proposal']['existing_ad']['creative_id'],'799')

            def managed_get(path,*args,**options):
                url = urlsplit(path)
                if url.path.endswith('/act_101'):
                    return dict(id='act_101',account_id='101',account_status=1,currency='USD',timezone_name='UTC')
                return dict(id='791',account_id='101',campaign_id='754',adset_id='771',status='ACTIVE',
                    creative=dict(id='799',account_id='101',object_story_spec={'page_id':'500'}))

            provider=SimpleNamespace(_validated_lease=lambda *args: object(), _get=managed_get)
            with patch('codex_ads_live.first_launch_update.LocalFirstLaunchHost',return_value=provider):
                code, intent=self.command(*route,'--mode','observe')
            self.assertEqual(code,0,intent)
            self.assertEqual(intent['update_scope']['before'],'ACTIVE')
            self.assertEqual(intent['request']['path'],'/v27.0/791')
            self.assertEqual(intent['request']['form'],'status=PAUSED')
            self.assertFalse(intent['write_authorized'])

    def test_focused_readback_holds_unresolved_custom_event_without_changing_report_attribution(self):
        from dataclasses import asdict
        from codex_ads_live.journal import digest
        from codex_ads_live.media_buyer import _consume_focus
        from codex_ads_live.validation import InputError

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve()/'focus.json'
            scope = dict(account_id='101', since='2026-11-01', until='2026-11-02', currency='USD', timezone='UTC')
            plan = NativeInsightsPlan('101','ad','701',scope['since'], scope['until'])
            source = dict(query=asdict(plan), currency='USD', timezone='UTC')
            snapshot = dict(schema='native-insights-snapshot-v1', workspace_sha256='a'*64, account_id='101',
                            source_context=source, report={'coverage':'COMPLETE','rows':[]},
                            object_context=dict(object_id='701', account_id='101', adset=dict(event=dict(
                                custom_conversion_id='888', comparison_requires_event_semantics=True))))
            path.write_text(json.dumps(dict(payload=snapshot, sha256=digest(snapshot))))
            queue = dict(targets=[{'ad_id':'701'}])
            _consume_focus(queue,[str(path)],'a'*64,scope)
            result = queue['targets'][0]['readback']
            self.assertEqual(result['next_review'], 'VERIFY_CONVERSION_DEFINITION')
            self.assertEqual(result['attribution_comparison'], 'NOT_INFERRED')
            with self.assertRaisesRegex(InputError,'BUYER_FOCUSED_READBACK_SCOPE'):
                _consume_focus(dict(targets=[{'ad_id':'702'}]),[str(path)],'a'*64,scope)

    def test_report_from_cli_reconciliation_keeps_out_of_window_rows_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            area = Path(tmp).resolve()
            paths = {}
            for name, text in {
                "orders": "store_id,order_id,line_id,date,currency,gross_minor,discount_minor,product_cost_minor,fulfillment_minor,payment_minor\n"
                          "store,one,line,2026-11-01,USD,1000,0,100,10,5\n"
                          "store,two,line,2026-11-02,USD,2000,0,200,20,10\n",
                "refunds": "store_id,refund_id,order_id,line_id,date,currency,refund_minor\n",
                "spend": "account_id,date,currency,spend_minor\n101,2026-11-01,USD,100\n",
            }.items():
                paths[name] = area / (name + ".csv")
                paths[name].write_text(text)
            output = area / "report"
            code, result = self.command("commerce-reconcile", "--orders", paths["orders"], "--refunds", paths["refunds"],
                                        "--spend", paths["spend"], "--store-id", "store", "--account-id", "101",
                                        "--currency", "USD", "--since", "2026-11-01", "--until", "2026-11-01",
                                        "--output", output)
            self.assertEqual(code, 0, result)
            self.assertEqual(result["period_totals"]["net_sales_minor"], 1000)
            with ZipFile(output / "commerce-report.xlsx") as z:
                self.assertIsNone(z.testzip())
                for name in z.namelist():
                    ET.fromstring(z.read(name))
                daily = z.read("xl/worksheets/sheet2.xml").decode()
                self.assertIn("2026-11-01", daily)
                self.assertIn("<v>1000</v>", daily)
                self.assertNotIn("2026-11-02", daily)

    def test_store_session_export_scope_and_partial_coverage(self):
        from hashlib import sha256
        from codex_ads_live.commerce_report import attach_funnel_export
        from codex_ads_live.validation import InputError

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            export = root/'sessions.csv'
            export.write_text('date,sessions,checkout_sessions,purchase_sessions\n2026-11-01,100,20,4\n')
            scope = dict(store_id='fixture',since='2026-11-01',until='2026-11-02')
            document = dict(scope, schema='commerce-funnel-export-v1',timezone='UTC',source='SHOPIFY_ANALYTICS_EXPORT',
                            file=str(export),sha256=sha256(export.read_bytes()).hexdigest())
            partial = attach_funnel_export(scope,document,'UTC')['store_funnel']
            self.assertEqual(partial['daily']['2026-11-01']['sessions'],100)
            self.assertEqual(partial['missing_days'],['2026-11-02'])
            self.assertIsNone(partial['counts']['sessions'])
            self.assertIsNone(partial['checkout_per_session'])
            with self.assertRaisesRegex(InputError,'COMMERCE_FUNNEL_EXPORT_SCOPE'):
                attach_funnel_export(scope,dict(document,timezone='America/Los_Angeles'),'UTC')

    def test_catalog_preflight_holds_exact_template_separate_from_manual_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp).resolve()
            feed = directory/'feed.csv'
            feed.write_text('id,title,link,image_link,availability,condition,price\n'
                'cup,Cup,https://shop.example.com/cup,https://shop.example.com/cup.png,in stock,new,42.00 USD\n')
            evidence = directory/'catalog.json'
            template = dict(catalog_id='501',product_set_id='502',page_id='503',
                            destination='https://shop.example.com/cup',main_text='See verified size')
            evidence.write_text(json.dumps(dict(currency='USD',product_set_ids=['cup'],event_content_ids=['cup'],
                                                meta_template=template)))
            code, output = self.command('catalog-review','--input',evidence,'--feed',feed,'--output',directory/'review')
            self.assertEqual(code,0,output)
            self.assertTrue(output['template_review']['ready_for_operator_review'])
            packet=output['template_review']['request_fields']
            self.assertEqual(packet['product_set_id'],'502')
            self.assertEqual(set(packet['object_story_spec']),{'page_id','template_data'})
            self.assertFalse(output['template_review']['dispatchable'])
            self.assertEqual(output['platform_state'],'NOT_RUN')

    def test_selected_budget_is_compiled_only_once_and_keeps_distinct_parents(self):
        assets = ["b"*64, "a"*64]
        groups = [dict(name=f"Group {i}", adset_id=None, budget=None, assets=[asset],
                       targeting=dict(geo_locations={"countries":["US"]}, age_min=24, age_max=50))
                  for i, asset in enumerate(assets, 1)]
        selection = dict(mode="NEW", campaign_name="Experiment", budget_owner="CBO", budget_schedule="DAILY",
                         start_time=None, end_time=None, campaign_budget="20.00", campaign_id=None, groups=groups)
        plan = compile_selection(selection, account="101", currency="USD", goal="PURCHASE", dataset="104",
                                 asset_ids=assets, maximum_minor=20000)
        self.assertEqual(plan["nodes"][0]["params"]["daily_budget"], 2000)
        self.assertTrue(all("daily_budget" not in row["params"] for row in plan["nodes"] if row["kind"] == "ADSET"))
        self.assertEqual([row["depends_on"][0] for row in plan["nodes"] if row["kind"] == "AD"], ["adset-1", "adset-2"])
        self.assertFalse(plan["dispatchable"])

    def test_cbo_two_groups_continue_through_normal_first_launch_and_select_each_ad(self):
        from codex_ads_live import business_profile, first_launch_plan
        from codex_ads_live.journal import digest
        from codex_ads_live.strategy_plan import managed_nodes

        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.workspace(tmp)
            client_workspace.map_asset(workspace, 'PAGE', '102', 'EV_PAGE')
            client_workspace.default_asset(workspace, 'PAGE', '102')
            def declare(profile):
                profile.update(app_id='105', business_id='106')
                business = business_profile.blank(profile['client_id'], '101')
                business.update(offer_summary='Fixture', goal='Test structure', result_type='PURCHASE',
                                result_definition='Purchase', currency='USD', maximum_test_spend='150.00',
                                protected_daily_spend='30.00')
                profile['business_profiles'] = {'101': business}
            client_workspace.update(workspace, declare)
            assets = ['a' * 64, 'b' * 64]
            groups = [dict(name=f'Group {i}', adset_id=None, budget=None, assets=[asset],
                           targeting=dict(geo_locations={'countries':['US']}, age_min=24, age_max=50))
                      for i, asset in enumerate(assets)]
            groups[1]['targeting'].update(placements={'facebook':['feed']}, excluded_custom_audiences=['702'])
            selection = dict(mode='NEW', campaign_name='Fixture', budget_owner='CBO', budget_schedule='DAILY',
                             start_time=None, end_time=None, campaign_budget='20.00', campaign_id=None, groups=groups)
            plan = compile_selection(selection, account='101', currency='USD', goal='PURCHASE', dataset='104',
                                     asset_ids=assets, maximum_minor=15000)
            nodes = managed_nodes(plan, test_limit=12000, daily_limit=3000)
            frozen = [dict(asset_sha256=sha, concept_id=f'fixture-{i}') for i, sha in enumerate(assets)]
            scope = client_workspace.draft_review_scope(client_workspace.read(workspace), '101')
            binding = dict(profile_sha256=digest(scope), goal='PURCHASE', creatives_sha256=digest(frozen),
                           account_id='101', page_id='102', currency='USD', destination_url='https://example.com',
                           dataset_id='104', budget_minor=12000, protected_daily_minor=3000,
                           managed_strategy=dict(nodes=nodes, plan_sha256=plan['plan_sha256'],
                                                 budget_owner='CBO', budget_schedule='DAILY'))
            target = workspace.parent / 'launch'
            with patch('codex_ads_live.creative_research.freeze_selection', side_effect=frozen):
                first_launch_plan.prepare(workspace, target, creative_selection=[('sample','v',sha) for sha in assets],
                                          operator_intake=binding)
            for draft_path in target.glob('*-draft*.json'):
                draft = json.loads(draft_path.read_text())
                draft['context']['timezone'] = 'America/Los_Angeles'
                params = draft['parameters']
                if draft['operation'] == 'CREATE_CREATIVE':
                    params.update(name='Creative', image_hash='fixture_image', primary_text='Example', headline='Headline',
                                  description='Description', call_to_action='SHOP_NOW')
                if draft['operation'] == 'CREATE_AD':
                    params['name'] = 'Ad fixture'
                draft_path.write_text(json.dumps(draft))
            args = ('workspace-first-launch-progress', '--workspace', workspace, '--plan-dir', target)
            readback_ids = []
            for number, operation in enumerate(('CAMPAIGN','ADSET','CREATIVE','AD','ADSET','CREATIVE','AD'), start=710):
                code, pending = self.command(*args)
                self.assertEqual(pending['status'], 'FIRST_LAUNCH_STAGE_FOR_REVIEW', (code, pending))
                params = pending['preview']['draft']['parameters']
                self.assertEqual(pending['preview']['draft']['operation'], 'CREATE_' + operation)
                packet = parse_qs(pending['creation_request']['body'])
                if operation == 'CAMPAIGN':
                    self.assertEqual(packet['daily_budget'], ['2000'])
                    self.assertEqual(packet['spend_cap'], ['12000'])
                if operation == 'ADSET':
                    self.assertEqual(packet['campaign_id'], ['710'])
                    self.assertNotIn('daily_budget', packet)
                    self.assertNotIn('lifetime_budget', packet)
                    if number == 714:
                        self.assertEqual(json.loads(packet['targeting'][0])['excluded_custom_audiences'], [{'id':'702'}])
                        self.assertEqual(pending['expected_object']['selected_targeting']['excluded_custom_audiences'], ['702'])
                if operation == 'AD':
                    self.assertEqual(params['adset_id'], str(number - 2))
                    readback_ids.append(str(number))
                code, reserved = self.command(*args, '--reserve')
                self.assertEqual(reserved['status'], 'FIRST_LAUNCH_OUTCOME_UNKNOWN', (code, reserved))
                record = reserved['readback_template']
                record.update(state='CREATIVE_OBSERVED' if operation == 'CREATIVE' else 'PAUSED',
                              evidence_ref='EV_SYNTHETIC_MAPPED', observed_at=datetime.now(timezone.utc).isoformat(),
                              object=dict(reserved['expected_object'], id=str(number)))
                receipt = workspace.parent / f'demo-readback-{number}.json'
                receipt.write_text(json.dumps(record))
                code, recorded = self.command(*args, '--readback', receipt)
                self.assertEqual(code, 0, recorded)
            for remote in readback_ids:
                code, result = self.command(*args, '--ad-id', remote)
                self.assertEqual(code, 0, result)
                self.assertEqual(result['preview_target']['ad_id'], remote)
                self.assertEqual(result['separate_activation_scope']['adset_id'], str(int(remote) - 2))
                self.assertEqual(result['activation_context']['daily_budget_minor'], 2000)
                self.assertEqual(result['activation_context']['selected_budget'],
                                 dict(owner='CAMPAIGN', schedule='DAILY', budget_minor=2000))

    def test_dynamic_manual_assets_feed_survives_production_wire_and_readback(self):
        from codex_ads_live.dynamic_creative import build
        from codex_ads_live.first_launch_graph import _check_form
        from codex_ads_live.first_launch_readback import _project
        from codex_ads_live.first_launch_managed_write import _ManagedAttempt
        from codex_ads_live.first_launch_host import graph_post_request
        from codex_ads_live.journal import digest
        from codex_ads_live.meta_write_mapping import validate_creative_parameters, creative_request_parameters
        from codex_ads_live.validation import InputError
        from types import SimpleNamespace
        from urllib.parse import urlencode

        asset = 'a'*64
        dynamic = dict(images=['fixture_123','fixture_234'], alternate_primary_texts=['Compare the specs'],
                       alternate_headlines=['See exact capacity'])
        selected = dict(mode='NEW', sequence='FULL', campaign_name='Dynamic',
            budget_owner='ABO',budget_schedule='DAILY',start_time=None,end_time=None,campaign_budget=None,
            campaign_id=None,groups=[dict(name='Group 1',adset_id=None,budget='12.00',
                targeting=dict(geo_locations={'countries':['US']},age_min=24,age_max=50),assets=[asset],
                dynamic_media=dynamic)])
        plan = compile_selection(selected,account='101',currency='USD',goal='PURCHASE',dataset='104',
                                 asset_ids=[asset],maximum_minor=15000)
        _, group, creative, _ = plan['nodes']
        self.assertTrue(group['params']['is_dynamic_creative'])
        params = dict(creative['params'], name='Dynamic assets',image_hash=None,destination_url='https://shop.example.com/cup',
                      primary_text='Know your cup',headline='View the grey cup',description='See dimensions',
                      call_to_action='LEARN_MORE')
        validate_creative_parameters(params)
        packet = creative_request_parameters(params,'502')
        form = urlencode({key:json.dumps(value,separators=(',',':')) if type(value) is dict else value
                          for key,value in packet.items()})
        path = '/v27.0/act_101/adcreatives'
        binding = SimpleNamespace(path=path,request_sha256=digest(graph_post_request(path,form)),
                                  operation='CREATE_CREATIVE',page_id='502')
        _check_form(binding,form)
        self.assertEqual(json.loads(parse_qs(form)['object_story_spec'][0]),{'page_id':'502'})
        self.assertEqual(json.loads(parse_qs(form)['asset_feed_spec'][0])['ad_formats'],['SINGLE_IMAGE'])
        response = dict(id='710',account_id='101',**packet)
        observed = _project(SimpleNamespace(level='CREATIVE',object_id='710',account_id='101'),'502',200,response)
        expected = digest(build(params))
        self.assertEqual(observed[1]['asset_feed_sha256'],expected)
        scope = dict(expected_campaign_id='701',creative_asset_feed_sha256=expected)
        parents = {'703':dict(id='703',account_id='101',campaign_id='701',status='PAUSED',is_dynamic_creative=True),
                   '710':response}
        host = SimpleNamespace(row=scope,_get=lambda value:parents[urlsplit(value).path.split('/')[-1]])
        ad_body = urlencode(dict(adset_id='703',creative=json.dumps(dict(creative_id='710'))))
        _ManagedAttempt.verify_creation_assets(host,SimpleNamespace(operation='CREATE_AD',account_id='101'),ad_body)
        parents['703']['is_dynamic_creative'] = False
        with self.assertRaisesRegex(InputError,'META_DYNAMIC_ADSET_SCOPE'):
            _ManagedAttempt.verify_creation_assets(host,SimpleNamespace(operation='CREATE_AD',account_id='101'),ad_body)
        with self.assertRaisesRegex(InputError,'META_WRITE_CREATIVE_MEDIA_MODES_CONFLICT'):
            validate_creative_parameters(dict(params,image_hash='conflicting_static_image'))

    def test_customer_task_can_record_paused_structure_before_choosing_any_creative(self):
        from codex_ads_live import business_profile

        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.workspace(tmp)
            client_workspace.map_asset(workspace, 'PAGE', '500', 'EV_PAGE')
            client_workspace.default_asset(workspace, 'PAGE', '500')

            def declare(profile):
                profile.update(app_id='105', business_id='106')
                business = business_profile.blank(profile['client_id'], '101')
                business.update(offer_summary='Fixture', goal='Test structure', result_type='PURCHASE',
                                result_definition='Purchase', currency='USD', maximum_test_spend='150.00',
                                protected_daily_spend='30.00')
                profile['business_profiles'] = {'101': business}

            client_workspace.update(workspace, declare)
            example = connect_tests.ROOT/'codex-ads-live/fixtures/media-buyer-intake.example.json'
            source = json.loads(example.read_text())
            source.pop('creatives')
            source['business']['account_id'] = source['strategy']['account_id'] = '101'
            groups = [dict(name='Initial group', adset_id=None, budget='12.00', assets=[],
                           targeting=dict(geo_locations={'countries':['US']}, age_min=24, age_max=50))]
            source['selected_strategy'] = dict(mode='NEW', sequence='STRUCTURE_FIRST', campaign_name='First campaign',
                budget_owner='ABO', budget_schedule='DAILY', start_time=None, end_time=None,
                campaign_budget=None, campaign_id=None, groups=groups)
            path = workspace.parent/'structure-input.json'
            path.write_text(json.dumps(source))
            args = ('--workspace', workspace, '--case', 'paused-structure')
            self.assertEqual(self.command('media-buyer','prepare',*args,'--input',path)[0], 0)
            code, prepared = self.command('media-buyer','plan',*args)
            self.assertEqual(code, 0, prepared)
            self.assertEqual(prepared['intake']['blockers'], [], prepared['intake'])
            plan_dir = Path(prepared['plan_dir'])
            self.assertEqual(sorted(item.name for item in plan_dir.glob('*-draft.json')), ['adset-draft.json','campaign-draft.json'])
            command = prepared['customer_commands']['continue']
            for name, remote in (('campaign','771'), ('adset','772')):
                draft_path = plan_dir/f'{name}-draft.json'
                draft = json.loads(draft_path.read_text())
                draft['context']['timezone'] = 'America/Los_Angeles'
                draft_path.write_text(json.dumps(draft))
                code, stage = self.command(*command,'--reserve')
                self.assertEqual(code, 0, stage)
                record = stage['readback_template']
                record.update(state='PAUSED', evidence_ref='EV_SYNTHETIC_PARENT',
                              observed_at=datetime.now(timezone.utc).isoformat(),
                              object=dict(stage['expected_object'], id=remote))
                receipt = workspace.parent/f'{name}-readback.json'
                receipt.write_text(json.dumps(record))
                code, observed = self.command(*command,'--readback',receipt)
                self.assertEqual(code, 0, observed)
            _, reopened = self.command('media-buyer','status',*args)
            self.assertEqual(reopened['launch']['status'], 'FIRST_LAUNCH_STRUCTURE_RECORDED')
            self.assertEqual([(row['id'],row.get('campaign_id')) for row in reopened['launch']['structure']],
                             [('771',None),('772','771')])
            self.assertNotIn('ad_id', reopened['launch'])
            asset = 'a'*64
            research = dict(brand=None, learning=[], sources=[dict(id='source', kind='PRODUCT_FACT', url=None,
                observed_at='2026-11-27T00:00:00Z', product='Fixture', fact='Two sizes')],
                concepts=[dict(id='idea', source_ids=['source'], hypothesis='Size guide',
                    variants=[dict(id='v1', brief='Show both sizes')], state='AVAILABLE')],
                reviews=[dict(asset_sha256=asset, concept_id='idea', observations=[],
                    comments=dict(selling_point=None, hook=None, conversion_point=None, human_feel=None),
                    weakest=None, suggestion=None)])
            research_path = workspace.parent/'structure-creative.json'
            research_path.write_text(json.dumps(research))
            self.assertEqual(self.command('workspace-creative-research','--workspace',workspace,'--input',research_path)[0], 0)
            source['creatives'] = [dict(concept_id='idea', variant_id='v1', asset_sha256=asset)]
            source['existing_structure'] = str(plan_dir)
            source['selected_strategy'].update(mode='APPEND', sequence='FULL', campaign_id='771', groups=[
                dict(name='Original group', adset_id='772', budget=None, targeting=None, assets=[asset]),
            ])
            continuation = workspace.parent/'continuation.json'
            continuation.write_text(json.dumps(source))
            args = ('--workspace', workspace, '--case', 'add-creative')
            code, received = self.command('media-buyer','prepare',*args,'--input',continuation)
            self.assertEqual(code, 0, received)
            self.assertEqual(received['intake']['previous_structure_plan_sha256'], reopened['launch']['plan_sha256'])
            code, next_plan = self.command('media-buyer','plan',*args)
            self.assertEqual(code, 0, next_plan)
            next_dir = Path(next_plan['plan_dir'])
            self.assertEqual(sorted(item.name for item in next_dir.glob('*-draft.json')), ['ad-draft.json','creative-draft.json'])

    def test_existing_adset_appends_creative_and_ad_without_creating_parents(self):
        from codex_ads_live import business_profile, first_launch_plan
        from codex_ads_live.first_launch_managed_write import _ManagedAttempt
        from codex_ads_live.journal import digest
        from codex_ads_live.strategy_plan import managed_nodes
        from codex_ads_live.validation import InputError
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.workspace(tmp)
            client_workspace.map_asset(workspace, 'PAGE', '102', 'EV_PAGE')
            client_workspace.default_asset(workspace, 'PAGE', '102')
            def declare(profile):
                profile.update(app_id='105', business_id='106')
                business = business_profile.blank(profile['client_id'], '101')
                business.update(offer_summary='Fixture', goal='Existing parent', result_type='PURCHASE',
                                result_definition='Purchase', currency='USD', maximum_test_spend='150.00',
                                protected_daily_spend='30.00')
                profile['business_profiles'] = {'101': business}
            client_workspace.update(workspace, declare)
            asset = 'b' * 64
            selection = dict(mode='APPEND', campaign_name='Fixture', budget_owner='ABO', budget_schedule='DAILY',
                start_time=None, end_time=None, campaign_budget=None, campaign_id='701', groups=[
                    dict(name='Group', adset_id='703', budget=None, targeting=None, assets=[asset])])
            plan = compile_selection(selection, account='101', currency='USD', goal='PURCHASE', dataset='104',
                                     asset_ids=[asset], maximum_minor=15000)
            nodes = managed_nodes(plan, test_limit=12000, daily_limit=3000)
            frozen = dict(asset_sha256=asset, concept_id='fixture')
            scope = client_workspace.draft_review_scope(client_workspace.read(workspace), '101')
            binding = dict(profile_sha256=digest(scope), goal='PURCHASE', creatives_sha256=digest([frozen]),
                           account_id='101', page_id='102', currency='USD', destination_url='https://example.com',
                           dataset_id='104', budget_minor=12000, protected_daily_minor=3000,
                           managed_strategy=dict(nodes=nodes, plan_sha256=plan['plan_sha256'],
                                                 budget_owner='ABO', budget_schedule='DAILY', mode='APPEND'))
            target = workspace.parent / 'launch'
            with patch('codex_ads_live.creative_research.freeze_selection', return_value=frozen):
                prepared = first_launch_plan.prepare(workspace, target, creative_selection=[('sample','v',asset)],
                                                     operator_intake=binding)
            self.assertEqual(prepared['draft_files'], ['creative-draft.json','ad-draft.json'])
            paths = [target / name for name in prepared['draft_files']]
            for draft_path in paths:
                draft = json.loads(draft_path.read_text())
                draft['context']['timezone'] = 'America/Los_Angeles'
                draft['parameters']['name'] = 'Synthetic creative or ad'
                if draft['operation'] == 'CREATE_CREATIVE':
                    draft['parameters'].update(image_hash='fixture_image', primary_text='Example', headline='Headline',
                                               description='Description', call_to_action='SHOP_NOW', url_tags='utm_campaign=test')
                draft_path.write_text(json.dumps(draft))
            args = ('workspace-first-launch-progress', '--workspace', workspace, '--plan-dir', target)
            for remote_id, operation in (('710','CREATE_CREATIVE'), ('711','CREATE_AD')):
                code, pending = self.command(*args)
                self.assertEqual(pending['status'], 'FIRST_LAUNCH_STAGE_FOR_REVIEW', (code,pending))
                packet = pending['creation_request']['body']
                if operation == 'CREATE_CREATIVE':
                    self.assertEqual(parse_qs(packet)['url_tags'], ['utm_campaign=test'])
                else:
                    self.assertEqual(parse_qs(packet)['adset_id'], ['703'])
                    parent = dict(id='703', account_id='101', campaign_id='701', status='PAUSED')
                    guard = SimpleNamespace(row={'expected_campaign_id':'701'}, _get=lambda _: parent)
                    bound = SimpleNamespace(operation=operation, account_id='101')
                    _ManagedAttempt.verify_creation_assets(guard, bound, packet)
                    parent['campaign_id'] = '799'
                    with self.assertRaisesRegex(InputError, 'FIRST_LAUNCH_MANAGED_ADSET_PARENT_SCOPE'):
                        _ManagedAttempt.verify_creation_assets(guard, bound, packet)
                code, reserved = self.command(*args, '--reserve')
                self.assertEqual(reserved['status'], 'FIRST_LAUNCH_OUTCOME_UNKNOWN', (code,reserved))
                record = reserved['readback_template']
                record.update(state='CREATIVE_OBSERVED' if operation == 'CREATE_CREATIVE' else 'PAUSED',
                              evidence_ref='EV_SYNTHETIC_MAPPED', observed_at=datetime.now(timezone.utc).isoformat(),
                              object=dict(reserved['expected_object'], id=remote_id))
                receipt = workspace.parent / f'demo-readback-{remote_id}.json'
                receipt.write_text(json.dumps(record))
                code, recorded = self.command(*args, '--readback', receipt)
                self.assertEqual(code, 0, recorded)
            self.assertEqual(recorded['status'], 'FIRST_LAUNCH_OBJECT_CHAIN_RECORDED')
            self.assertEqual(recorded['separate_activation_scope']['campaign_id'], '701')
            self.assertEqual(recorded['separate_activation_scope']['adset_id'], '703')
            self.assertIsNone(recorded['activation_context']['daily_budget_minor'])
            from codex_ads_live.first_launch_activation_host import _guard_valid
            from codex_ads_live.first_launch_controls import observe_guard

            observation = recorded['preview_observation_template']
            now = datetime.now(timezone.utc)
            observation.update(observed_at=now.isoformat(), evidence_ref='EV_SYNTHETIC_AD_PREVIEW')
            path = workspace.parent/'ad-preview.json'
            path.write_text(json.dumps(observation))
            start, stop = now+timedelta(seconds=5), now+timedelta(minutes=20)
            args = ('workspace-first-launch-activation-review','--workspace',workspace,'--plan-dir',target,
                    '--preview-observation',path,'--start-at',start.isoformat(),'--end-at',stop.isoformat(),
                    '--maximum-spend-minor','6000')
            code, refused = self.command(*args)
            self.assertEqual((code,refused['error_code']), (2,'FIRST_LAUNCH_EXISTING_PARENT_ACTIVATION_REQUIRES_SEPARATE_GUARD'))
            code, reviewed = self.command(*args,'--parent-budget','ADSET','DAILY','1200')
            self.assertEqual(code, 0, reviewed)
            self.assertEqual(reviewed['review_blockers'], [])
            self.assertFalse(reviewed['dispatchable'])
            graph = {
                'act_101': dict(id='act_101',account_id='101',account_status=1,currency='USD',timezone_name='America/Los_Angeles'),
                '701': dict(id='701',account_id='101',status='ACTIVE',spend_cap='6000'),
                '703': dict(id='703',account_id='101',status='ACTIVE',campaign_id='701',daily_budget='1200',
                            start_time=start.isoformat(),end_time=stop.isoformat()),
                '711': dict(id='711',account_id='101',status='PAUSED',campaign_id='701',adset_id='703',creative={'id':'710'}),
            }
            get = lambda value: graph[urlsplit(value).path.split('/')[-1]]
            observed_at = start+timedelta(seconds=5)
            with patch('codex_ads_live.first_launch_controls.utcnow', return_value=observed_at):
                _guard_valid(reviewed, observe_guard(reviewed,get,stop), observed_at)
                graph['703']['daily_budget'] = '1300'
                with self.assertRaisesRegex(InputError,'FIRST_LAUNCH_ACTIVATION_LIVE_SCOPE_MISMATCH'):
                    _guard_valid(reviewed, observe_guard(reviewed,get,stop), observed_at)

    def test_activation_rechecks_native_budget_layer_with_same_amount_and_cap(self):
        from codex_ads_live.first_launch_controls import observe_guard
        from codex_ads_live.first_launch_activation_host import _guard_valid
        from codex_ads_live.validation import InputError

        now = datetime.now(timezone.utc)
        start, stop = now - timedelta(hours=1), now + timedelta(hours=1)
        selected = dict(owner='CAMPAIGN', schedule='LIFETIME', budget_minor=2000)
        review = dict(review_sha256='a'*64, binding=dict(
            account_id='101', ad_id='105', adset_id='104', campaign_id='103', creative_id='106',
            currency='USD', timezone='America/Los_Angeles', preview_observation_sha256='b'*64,
            daily_budget_minor=None, selected_budget=selected, starts_at=start.isoformat(),
            ends_at=stop.isoformat(), maximum_spend_minor=5000))
        payloads = {
            'act_101':dict(id='act_101', account_id='101', account_status=1, currency='USD', timezone_name='America/Los_Angeles'),
            '103':dict(id='103', account_id='101', status='ACTIVE', spend_cap='5000', lifetime_budget='2000'),
            '104':dict(id='104', account_id='101', status='ACTIVE', campaign_id='103',
                       start_time=start.isoformat(), end_time=stop.isoformat(), daily_budget='0'),
            '105':dict(id='105', account_id='101', status='PAUSED', campaign_id='103', adset_id='104', creative={'id':'106'}),
        }
        def get(url):
            return payloads[urlsplit(url).path.rsplit('/', 1)[-1]]
        guard = observe_guard(review, get, stop)
        self.assertEqual(guard.effect['selected_budget'], selected)
        _guard_valid(review, guard, datetime.now(timezone.utc))
        payloads['104']['daily_budget'] = '1'
        with self.assertRaisesRegex(InputError, 'FIRST_LAUNCH_ACTIVATION_LIVE_BUDGET_LAYER_MISMATCH'):
            observe_guard(review, get, stop)

    def test_native_rules_cli_protects_exact_operations_and_recovers_receipt(self):
        from codex_ads_live.journal import digest
        from codex_ads_live.native_rules import rule_observation
        from codex_ads_live.first_launch_managed_write import _ManagedAttempt
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            state = root / 'state'
            state.mkdir()
            launch = dict(workspace_identity_sha256='a'*64, plan_sha256='b'*64, app_id='102',
                          business_id='103', account_id='104', page_id='105')
            effects = []
            source = root / 'rule.json'
            actions = ('create','rename','reconfigure','disable','delete')
            native_row = dict(id='305', account_id='104', status='DISABLED', name='Original')
            options = dict(name='Review ads', entity_type='ADSET', metric='cost_per',
                           value_minor=3000, window='LAST_3_DAYS', execution='PAUSE')
            sequence = []

            class Host:
                def __init__(self, scope):
                    self.scope = scope

                def rpc(self, verb, kind, key, attempt=None):
                    self.last = verb
                    sequence.append(verb)
                    if verb == 'get':
                        return dict(attempt_id='e'*64)
                    if verb == 'execute':
                        return dict(status='ACKNOWLEDGED_READBACK_REQUIRED', rule_id='305')
                    raise AssertionError(verb)

                def _validated_lease(self, _kind, _key):
                    return 'fixture-only'

                def assert_lease(self, lease, _kind, _key):
                    if lease != 'fixture-only':
                        raise AssertionError('lease')

                def _get(self, path, lease, assert_current):
                    assert_current()
                    route = urlsplit(path).path
                    if route == '/v27.0/305':
                        return native_row
                    if route == '/v27.0/305/history':
                        return dict(data=[dict(timestamp=1795730000,results=[dict(object_id='401')])])
                    if route == '/v27.0/act_104/adrules_library':
                        return dict(data=[native_row])
                    raise AssertionError(route)

            with (patch('codex_ads_live.first_launch_progress.progress', return_value=launch),
                  patch('codex_ads_live.first_launch_local_host.LocalFirstLaunchHost', Host)):
                for action in actions:
                    doc = dict(currency='USD', rule_id='305' if action != 'create' else None,
                               config=(options if action in ('create','reconfigure') else
                                       dict(name='Revised') if action == 'rename' else None),
                               before_sha256=(digest(rule_observation(native_row,'104')) if action != 'create' else None))
                    source.write_text(json.dumps(doc))
                    args = ('native-rules', action, '--workspace', root, '--input', source)
                    code, preview = self.command(*args)
                    self.assertEqual(code, 0, preview)
                    wire = preview['request']
                    self.assertEqual(wire['method'], 'DELETE' if action == 'delete' else 'POST')
                    if action in ('create','reconfigure'):
                        self.assertEqual(parse_qs(wire['body'])['status'], ['DISABLED'])
                    attempt = SimpleNamespace(scope=preview['native_rule_scope'],
                        row=dict(key=preview['scope_sha256'], approved_at=datetime.now(timezone.utc).isoformat()),
                        attempt_id='c'*64, deadline=datetime.now(timezone.utc)+timedelta(minutes=2),
                        _assert_current=lambda: None, _get=lambda path: native_row)

                    def transport(path, body, **contract):
                        contract['recheck']()
                        effects.append((contract['method'], path, body))
                        return {'id':'305'} if action == 'create' else {'success':True}

                    prefix = 'codex_ads_live.first_launch_managed_write.'
                    with patch(prefix + 'STATE', state), patch(prefix + '_send_scoped_post', side_effect=transport):
                        outcome = _ManagedAttempt.mutate_rule(attempt)
                    self.assertEqual(outcome['rule_id'], '305')
                    self.assertEqual(outcome['status'], 'DELETE_ACKNOWLEDGED' if action == 'delete'
                                     else 'ACKNOWLEDGED_READBACK_REQUIRED')
                    box = root / ('operation-' + action)
                    code, output = self.command(*args, '--state-dir', box, '--confirmed-mode', 'write')
                    self.assertEqual(code, 0, output)
                    code, output = self.command(*args, '--state-dir', box, '--confirmed-mode', 'recover')
                    self.assertTrue(output['local_receipt_reused'])
                code, listing = self.command('native-rules','list','--workspace',root,'--confirmed-mode','read')
                self.assertEqual((code,listing['rows'][0]['id']), (0,'305'))
                code, history = self.command('native-rules','history','--workspace',root,'--rule-id','305',
                                             '--confirmed-mode','read')
                self.assertEqual((code,history['rows'][0]['timestamp']), (0,1795730000))
            self.assertEqual(sequence, ['get','execute']*5)
            self.assertEqual([verb for verb, _, _ in effects], ['POST','POST','POST','POST','DELETE'])

    def test_creative_compare_reuses_actual_frames_and_frozen_copy_in_client_draft(self):
        from base64 import b64decode
        from hashlib import sha256
        from codex_ads_live import business_profile

        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.workspace(tmp)
            client_workspace.update(workspace, lambda profile: profile.update(app_id='105', business_id='106'))
            client_workspace.map_asset(workspace, 'PAGE', '102', 'EV_PAGE')
            client_workspace.default_asset(workspace, 'PAGE', '102')
            copy = dict(primary_text='Measure before you order', headline='Choose a size', description='Size guide',
                        call_to_action='LEARN_MORE')
            assets = ['a' * 64, 'b' * 64]
            source = dict(id='material', kind='PRODUCT_FACT', url=None, observed_at='2026-11-27T00:00:00Z',
                          product='Fixture', fact='Two sizes')
            ideas, reviews, report_args = [], [], []
            frame = b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX9sAAAAASUVORK5CYII=')
            for i, asset in enumerate(assets, 1):
                concept = f'idea-{i}'
                variant = dict(id='v1', brief=f'size concept {i}', copy=copy,
                               storyboard=[dict(start_ms=0, visual='Show ruler', script='Measure the item')])
                ideas.append(dict(id=concept, source_ids=['material'], hypothesis='Clarity', variants=[variant], state='AVAILABLE'))
                observation = dict(id='frame', asset_sha256=asset, kind='VISUAL', evidence_ref='frame-01', time_ms=0)
                comments = dict(selling_point=dict(observation_id='frame', comment='Ruler visible'),
                                hook=None, conversion_point=None, human_feel=None)
                reviews.append(dict(asset_sha256=asset, concept_id=concept, observations=[observation],
                                    comments=comments, weakest=None, suggestion=None))
                directory = workspace.parent / concept
                directory.mkdir()
                (directory/'frame-01.png').write_bytes(frame)
                report = directory/'MEDIA.json'
                report.write_text(json.dumps(dict(status='LOCAL_MEDIA_EVIDENCE', media_sha256=asset,
                    frames=[dict(path='frame-01.png', timestamp_ms=0, sha256=sha256(frame).hexdigest())])))
                report_args.extend(('--concept-id', concept, '--media-report', report))
            data = dict(brand=None, sources=[source], concepts=ideas, learning=[], reviews=reviews)
            packet = workspace.parent/'research.json'
            packet.write_text(json.dumps(data))
            a = ('--workspace', workspace)
            code, prepared = self.command('workspace-creative-research', *a, '--input', packet)
            self.assertEqual(code, 0, prepared)
            area = workspace.parent/'comparison'
            code, compared = self.command('creative-compare', *a, '--account-id', '101', *report_args, '--output', area)
            self.assertEqual(code, 0, compared)
            page = (area/'comparison.html').read_text()
            self.assertEqual(page.count('<img '), 2)
            self.assertIn('Show ruler', page)
            self.assertIn(copy['headline'], page)
            self.assertEqual((area/f'frame-{assets[0]}-0.png').read_bytes(), frame)
            path = area/compared['concepts'][0]['media'][0]['choices'][0]['file']
            plan = workspace.parent/'launch'
            args = ('workspace-first-launch-plan', *a, '--selection-file', path, '--output')
            code, selected = self.command(*args, plan)
            self.assertEqual(code, 0, selected)
            draft = json.loads((plan/'creative-draft.json').read_text())
            self.assertEqual(draft['context']['material_sha256'], assets[0])
            self.assertEqual({key: draft['parameters'][key] for key in copy}, copy)
            ideas[0]['variants'][0]['brief'] = 'Review after changing the brief'
            packet.write_text(json.dumps(data))
            self.assertEqual(self.command('workspace-creative-research', *a, '--input', packet)[0], 0)
            code, rejected = self.command(*args, workspace.parent/'stale-launch')
            self.assertEqual((code, rejected['error_code']), (2, 'CREATIVE_COMPARE_CHOICE_STALE'))

            def declare(profile):
                business = business_profile.blank(profile['client_id'],'101')
                business.update(offer_summary='Cup',goal='Measure',result_type='PURCHASE',result_definition='Order',
                                currency='USD',maximum_test_spend='120.00',protected_daily_spend='30.00')
                profile['business_profiles']={'101':business}

            client_workspace.update(workspace,declare)
            raw=json.loads((connect_tests.ROOT/'codex-ads-live/fixtures/media-buyer-intake.example.json').read_bytes())
            raw['page_id']='102'
            raw['business']['account_id']=raw['strategy']['account_id']='101'
            raw['creatives']=[dict(concept_id='idea-1',variant_id='v1',asset_sha256=assets[0])]
            intake=workspace.parent/'buyer.json'
            intake.write_text(json.dumps(raw))
            case=(*a,'--case','creative-learning')
            code, initial=self.command('media-buyer','prepare',*case,'--input',intake)
            self.assertEqual(code,0,initial)
            code, frozen=self.command('media-buyer','plan',*case)
            self.assertEqual(code,0,frozen)
            self.assertFalse(frozen['intake']['blockers'],frozen)
            readback=dict(frozen['launch'],stages=[dict(readback=dict(object={'creative_id':'750'}))])
            report=dict(delivery_state='POSITIVE_IMPRESSIONS_OBSERVED',recommendation='REVIEW_SAME_AD_AND_WINDOW',
                       reviewed_window=dict(since='2026-11-24',until='2026-11-25'),
                       launch_evidence=dict(state='SUPPLIED_MATCHING_SCOPE',creative=dict(id='750',state='RETURNED'),
                                           captured_at='2026-11-27T01:00:00Z'))
            review=workspace.parent/'review.json'
            review.write_text('{}')
            with (patch('codex_ads_live.media_buyer._period_review',return_value=dict(ad_id='754',reports=[report])),
                  patch('codex_ads_live.media_buyer.progress',return_value=readback)):
                self.assertEqual(self.command('media-buyer','review',*case,'--input',review)[0],0)
                code, learned=self.command('media-buyer','learn',*case)
                self.assertEqual(code,0,learned)
                self.assertEqual(learned['retained_candidates'],['idea-2'])
                self.assertEqual(self.command('media-buyer','learn',*case)[1],learned)
            code, research=self.command('workspace-creative-research',*a)
            self.assertEqual(research['research']['concepts']['idea-1']['state'],'USED')
            self.assertEqual(research['research']['learning'][learned['evidence_ref']]['state'],'WATCH')

    def test_audience_preview_seed_check_and_managed_adapter_use_same_source(self):
        from codex_ads_live.first_launch_managed_write import _ManagedAttempt
        from codex_ads_live.first_launch_progress import _request_preview
        from codex_ads_live.native_audience import seed_review, observe
        from codex_ads_live.validation import InputError
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.workspace(tmp)
            launch = dict(workspace_identity_sha256='a'*64, plan_sha256='b'*64, app_id='102',
                          business_id='103', account_id='101', page_id='105')
            options = dict(currency='USD', seed_id='702', name='US purchasers', country='US', percent=2,
                           minimum_observed_seed=100)
            path = workspace.parent/'audience.json'
            path.write_text(json.dumps(options))
            with patch('codex_ads_live.first_launch_progress.progress', return_value=launch):
                code, preview = self.command('native-audience', 'create-lookalike', '--workspace', workspace,
                                             '--input', path)
            self.assertEqual(code, 0, preview)
            scope = preview['native_audience_scope']
            native = dict(id='702', account_id='101', subtype='CUSTOM', operation_status={'code':200},
                          delivery_status={'code':200}, approximate_count_lower_bound=99,
                          permission_for_actions={'subtype_supports_lookalike':True})
            self.assertTrue(observe(lambda _: native, '101','702')['usable'])
            self.assertEqual(seed_review(scope,lambda _: native)['creation_preview'], 'SEED_OR_ELIGIBILITY_INSUFFICIENT')
            attempt = SimpleNamespace(scope=scope, row=dict(key=preview['scope_sha256'],
                approved_at=datetime.now(timezone.utc).isoformat()), attempt_id='c'*64,
                deadline=datetime.now(timezone.utc)+timedelta(minutes=2), _assert_current=lambda: None,
                _get=lambda _: native)
            exchanges = []

            def transport(relative_path, body, **contract):
                contract['recheck']()
                exchanges.append((relative_path, body))
                return {'id':'704'}

            prefix = 'codex_ads_live.first_launch_managed_write.'
            with patch(prefix+'STATE', workspace.parent), patch(prefix+'_send_scoped_post', side_effect=transport):
                with self.assertRaisesRegex(InputError, 'SEED_OR_ELIGIBILITY_INSUFFICIENT'):
                    _ManagedAttempt.create_audience(attempt)
                native['approximate_count_lower_bound'] = 120
                result = _ManagedAttempt.create_audience(attempt)
            self.assertEqual(result['audience_id'], '704')
            self.assertIn('READ_BACK_BEFORE_USE', result['status'])
            self.assertEqual(len(exchanges), 1)
            self.assertEqual(exchanges[0][0], '/v27.0/act_101/customaudiences')
            params = parse_qs(exchanges[0][1])
            self.assertEqual(params['origin_audience_id'], ['702'])
            self.assertEqual(json.loads(params['lookalike_spec'][0])['ratio'], 0.02)
            self.assertRaisesRegex(InputError, 'NATIVE_AUDIENCE_SCOPE', observe, lambda _: native, '102', '702')

            returned = dict(native, id=result['audience_id'], subtype='LOOKALIKE')
            group = dict(name='Verifiable audience group',adset_id=None,budget='5.00',assets=['a'*64],
                targeting=dict(geo_locations={'countries':['US']},age_min=21,age_max=48,interests=['700'],
                    custom_audiences=[returned['id']],excluded_custom_audiences=['702'],
                    placements={'facebook':['feed']}))
            selected = dict(mode='APPEND',campaign_name='Original campaign',campaign_id='754',budget_owner='ABO',
                            budget_schedule='DAILY',campaign_budget=None,start_time=None,end_time=None,groups=[group])
            compiled = compile_selection(selected,account='101',currency='USD',goal='PURCHASE',dataset='108',
                                         asset_ids=['a'*64],maximum_minor=2000)
            adset = next(node for node in compiled['nodes'] if node['kind']=='ADSET')
            payload = dict(adset['params'], campaign_id='754')
            request = _request_preview(dict(operation='CREATE_ADSET',account_id='101',parameters=payload))
            self.assertEqual(request['path'],'/v27.0/act_101/adsets')
            form = request['body']
            wire = json.loads(parse_qs(form)['targeting'][0])
            self.assertEqual(wire['custom_audiences'],[{'id':'704'}])
            self.assertEqual(wire['excluded_custom_audiences'],[{'id':'702'}])
            self.assertEqual(wire['flexible_spec'],[{'interests':[{'id':'700'}]}])
            self.assertEqual(wire['facebook_positions'],['feed'])
            nodes = {'754':dict(id='754',account_id='101',status='PAUSED',objective='OUTCOME_SALES'),
                     '702':native,'704':returned}
            looked_up = []

            def get(path):
                identifier = urlsplit(path).path.rsplit('/',1)[-1]
                looked_up.append(identifier)
                return nodes[identifier]

            guard = SimpleNamespace(profile={'account_id':'101'},_get=get)
            binding = SimpleNamespace(operation='CREATE_ADSET',account_id='101')
            _ManagedAttempt.verify_creation_assets(guard,binding,form)
            self.assertEqual(looked_up,['754','704','702'])
            returned['delivery_status'] = {'code':100}
            with self.assertRaisesRegex(InputError,'META_WRITE_EXISTING_AUDIENCE_NOT_READY'):
                _ManagedAttempt.verify_creation_assets(guard,binding,form)

    def test_public_ad_query_keeps_valid_first_page_when_second_is_restricted(self):
        from codex_ads_live.validation import InputError

        plan = ads_library.query(dict(query='cup',countries=['US'],status='ALL',date_min=None,date_max=None),
                                 4,2,page_id='400',archive_id='501')
        visited = []

        def receive(endpoint):
            visited.append(endpoint)
            if len(visited)==2:
                raise InputError('PERMISSION')
            return dict(data=[
                dict(id='501',page_id='401',ad_creative_bodies=['Wrong pair']),
                dict(id='501',page_id='400',ad_creative_link_titles=['Capacity'],ad_creative_bodies=None)
            ], paging={'next':'https://ignored.invalid', 'cursors':{'after':'onlyCursor2'}})

        response = ads_library.query_result(plan,receive)
        self.assertEqual((response['data_state'],response['error_code']),('PARTIAL','PERMISSION'))
        self.assertEqual([urlsplit(path).path for path in visited],['/v27.0/ads_archive']*2)
        self.assertEqual(parse_qs(urlsplit(visited[1]).query)['after'],['onlyCursor2'])
        rows = response['observation']['records']
        self.assertEqual([(row['page_id'],row['ad_archive_id'],row['body']) for row in rows],[('400','501',None)])
        self.assertEqual(rows[0]['detail']['additional_observations']['titles'],['Capacity'])

    def test_paused_group_schedule_only_shortens_frozen_end_time_and_reads_it_back(self):
        from codex_ads_live.first_launch_update import observe, request, current_value
        from codex_ads_live.validation import InputError

        now = datetime.now(timezone.utc).replace(microsecond=0)
        before, after = ((now+timedelta(days=n)).isoformat() for n in (3,2))
        scope = dict(schema='first-launch-update-v1',workspace_sha256='a'*64,plan_sha256='b'*64,
            business_id='105',app_id='106',account_id='101',page_id='102',currency='USD',timezone='UTC',
            action='SET_ADSET_END_TIME',objects=dict(campaign_id='700',adset_id='710',ad_id='711',
                affected_ads=['711']), before=before,after=after,observed_at=now.isoformat())
        data = {
            'act_101':dict(id='act_101',account_id='101',currency='USD',timezone_name='UTC',account_status=1),
            '700':dict(id='700',account_id='101',status='PAUSED'),
            '710':dict(id='710',account_id='101',campaign_id='700',status='PAUSED',
                start_time=(now+timedelta(days=1)).isoformat(),end_time=before),
            'ads':dict(data=[dict(id='711',account_id='101',campaign_id='700',adset_id='710',status='PAUSED')]),
        }

        def get(path):
            return data[urlsplit(path).path.rsplit('/',1)[-1]]

        self.assertEqual(current_value(scope,observe(scope,get,preflight=True)),before)
        edge, form = request(scope)
        self.assertEqual((edge,parse_qs(form)),('/v27.0/710',{'end_time':[after]}))
        with self.assertRaisesRegex(InputError,'FUTURE_SHORTENING_REQUIRED'):
            request(dict(scope,after=(now+timedelta(days=4)).isoformat()))
        data['ads']['data'][0]['status'] = 'ACTIVE'
        with self.assertRaisesRegex(InputError,'PAUSED_CHAIN_REQUIRED'):
            observe(scope,get,preflight=True)
        data['ads']['data'][0]['status'] = 'PAUSED'
        data['710']['end_time'] = after
        self.assertEqual(current_value(scope,observe(scope,get,preflight=False)),after)

    def test_public_record_keeps_title_without_storing_snapshot_url(self):
        plan = ads_library.query(dict(query="demo", countries=["US"], status="ALL", date_min=None, date_max=None),
                                 10, 2, page_id="11", archive_id="12")
        raw = dict(data=[dict(id="12", page_id="11", ad_creative_link_titles=["Public product name"],
            ad_snapshot_url="https://www.facebook.com/ads/archive/render_ad/?access_token=not-a-secret")])
        observation = ads_library.query_result(plan, lambda _: raw)["observation"]["records"][0]
        self.assertIsNone(observation["body"])
        self.assertEqual(observation["detail"]["additional_observations"]["titles"], ["Public product name"])
        self.assertNotIn("access_token", json.dumps(observation))
