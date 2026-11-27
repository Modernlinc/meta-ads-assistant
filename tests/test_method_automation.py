"""Verify business-intent routing, graceful fallback and the method-to-consumer handoff."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_fullchain_connect as connect_tests
from codex_ads_live import client_workspace, method_library, method_workflow
from codex_ads_live.journal import digest
from codex_ads_live.method_bundle import CORE_METHOD_ROLES, RESEARCH_LOCK
from codex_ads_live.validation import InputError


class MethodAutomationTest(unittest.TestCase):
    command = connect_tests.WorkspaceConnectTest.command

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.workspace = self.root/'client'
        client_workspace.initialize(self.workspace)
        self.source = self.root/'tracking.txt'
        self.source.write_text('Synthetic: the same Purchase test ID arrived on Pixel and CAPI.', encoding='utf-8')

    def plan(self, objective, *, offline=True):
        output = self.root/'plan'
        args = ['method-plan','--workspace',self.workspace,'--objective',objective,'--output',output]
        if offline:
            args.append('--offline')
        code, plan = self.command(*args)
        self.assertEqual(code,0,plan)
        return plan

    def application(self, plan):
        core = plan['payload']['methods']['meta-facebook-ads']
        brief = dict(schema='partner-evidence-brief-v1', role='META_AD_PLANNING', product='Fixture cup',
                     market='US', items=[dict(kind='measurement_requirement', ref='EV_TEST',
                         text='Compare test IDs across channels.', observed_at='2026-11-27T01:00:00Z',
                         state='HYPOTHESIS')])
        return dict(schema='meta-method-application-v1', methods={'meta-facebook-ads':dict(
            deliverable='Use the same event identity for the paired test. Production remains unknown.', brief=brief,
            read_files=[core['entry'],'method.md','tracking-parameters-and-url-chec-a8da3084.md'],
            input_requirements=['dataset and event IDs'], assumptions=[], unknowns=['production readback'])},
            consumer=dict(command='tracking-audit', document=dict(schema='operator-tracking-v1',
                dataset_id='202', destination='https://merchant.example/products/cup?utm_source=meta',
                events=[dict(channel=channel,name='Purchase',event_id='EV_TEST',mode='TEST')
                        for channel in ('PIXEL','CAPI')])))

    def apply(self, plan, value, output='application'):
        packet = self.root/'application.json'
        packet.write_text(json.dumps(value), encoding='utf-8')
        return self.command('method-apply','--workspace',self.workspace,'--plan',plan['plan_file'],
                            '--intent','tracking','--source-material',self.source,'--input',packet,
                            '--output',self.root/output)

    def test_all_catalog_sources_and_business_route_references_resolve(self):
        current = method_workflow.catalog()
        self.assertEqual({row['method_id'] for row in current['methods']}, set(CORE_METHOD_ROLES) | set(RESEARCH_LOCK['methods']))
        self.assertEqual(sum(row['bundled_or_optional']=='OPTIONAL' for row in current['methods']),81)
        for row in current['routes'].values():
            self.assertTrue(set(row['core']) <= CORE_METHOD_ROLES.keys())
            self.assertTrue(set(row['optional']) <= RESEARCH_LOCK['methods'].keys())
            self.assertTrue(row['required_inputs'] and row['outputs'] and row['downstream_consumers'])
        self.assertEqual(method_workflow.infer_intents('帮我做一轮新素材测试', current['routes']), ['experiment'])
        self.assertEqual(method_workflow.infer_intents('研究竞品广告并形成自己的创意方向', current['routes']), ['competitor'])
        self.assertEqual(method_workflow.infer_intents('帮我核对Shopify退款和广告消耗', current['routes']), ['commerce'])
        self.assertEqual(method_workflow.infer_intents('千川素材测试', current['routes']), [])

    def test_plain_objective_uses_core_and_real_operator_consumer_without_network(self):
        with patch.object(method_library,'_fetch',side_effect=AssertionError('network')):
            plan = self.plan('检查 Pixel 和 CAPI 为什么重复记单')
            self.assertEqual(plan['payload']['tasks'][0]['intent'],'tracking')
            self.assertEqual(plan['payload']['methods']['r21-conversion-tracking']['status'],'METHOD_OPTIONAL_OFFLINE')
            code, result = self.apply(plan,self.application(plan))
        self.assertEqual(code,0,result)
        self.assertEqual(result['status'],'METHOD_OUTPUT_CONSUMED_LOCALLY')
        report = json.loads((self.root/'application/BUSINESS-RESULT.json').read_bytes())
        self.assertEqual(report['paired_test_event_keys'],1)
        self.assertFalse(report['platform_deduplication_verified'])
        self.assertEqual(result['consumer_input_sha256'],digest(self.application(plan)['consumer']))
        receipt=json.loads((self.root/'application/meta-facebook-ads/METHOD-RECEIPT.json').read_bytes())
        self.assertEqual(receipt['payload']['application']['unknowns'],['production readback'])
        self.assertFalse(result['action_authorized'])

    def test_reading_evidence_and_same_workspace_are_required(self):
        plan=self.plan('检查 Pixel 和 CAPI')
        value=self.application(plan)
        insufficient=deepcopy(value)
        insufficient['methods']['meta-facebook-ads']['read_files']=['entry.md']
        self.assertEqual(self.apply(plan,insufficient)[1]['error_code'],'METHOD_APPLICATION_READING_REQUIRED')
        with_wrong_type=deepcopy(value)
        with_wrong_type['consumer']['command']='commerce-reconcile'
        self.assertEqual(self.apply(plan,with_wrong_type)[1]['error_code'],'METHOD_CONSUMER_OUTSIDE_INTENT')
        client_workspace.map_asset(self.workspace,'ACCOUNT','123','EV_FIXTURE')
        client_workspace.default_asset(self.workspace,'ACCOUNT','123')
        self.assertEqual(self.apply(plan,value)[1]['error_code'],'METHOD_PLAN_WORKSPACE_CHANGED')
        self.assertFalse((self.root/'application').exists())

    def test_available_optional_is_automatically_loaded_and_missing_source_is_local_fallback(self):
        verified=dict(handler='r21-ab-testing', status='SOURCE_METHOD_VERIFIED', source_directory=str(self.root),
                      source_commit='a'*40, method_sha256='b'*64, read_files=['SKILL.md','references/metrics.md'])
        with (patch.object(method_library,'lookup',side_effect=(None,verified)),
              patch.object(method_library,'install',return_value=dict(network_used=True)) as installer):
            chosen = self.plan('帮我设计一轮新素材测试',offline=False)
        installer.assert_called_once_with(self.workspace,'r21-ab-testing')
        loaded = chosen['payload']['methods']['r21-ab-testing']
        self.assertEqual(loaded['read_files'],verified['read_files'])
        self.assertTrue(loaded['network_used'])
        with patch.object(method_library,'install',side_effect=InputError('METHOD_PINNED_SOURCE_NOT_FOUND')):
            fallback = method_workflow.plan(self.workspace,None,['experiment'],None,None)
        self.assertEqual(fallback['payload']['tasks'][0]['status'],'METHODS_READY')
        self.assertEqual(fallback['payload']['methods']['r21-ab-testing']['status'],'METHOD_PINNED_SOURCE_NOT_FOUND')
        with (patch.object(method_library,'lookup',return_value=dict(status='METHOD_DISABLED')),
              patch.object(method_library,'install',side_effect=AssertionError('must respect user choice'))):
            retained = method_workflow.plan(self.workspace,None,['experiment'],None,None)
        self.assertEqual(retained['payload']['methods']['r21-ab-testing']['status'],'METHOD_DISABLED')

    def test_public_not_found_is_distinct_from_offline_and_malformed_locks_fail(self):
        args=('org/repo','b'*40,'skills/example/SKILL.md',10)
        with patch.object(method_library,'_read_public_file',return_value=(404,None)):
            with self.assertRaisesRegex(InputError,'METHOD_PINNED_SOURCE_NOT_FOUND'):
                method_library._fetch(*args)
        with patch.object(method_library,'_read_public_file',side_effect=[(404,None),(None,None),(504,None),(404,None)]):
            with self.assertRaisesRegex(InputError,'METHOD_DOWNLOAD_RETRY_OR_OFFLINE'):
                method_library._fetch(*args)
        row = list(RESEARCH_LOCK['methods']['r21-ab-testing'])
        row[3]={'SKILL.md':'b'*64,'../elsewhere.md':'a'*64}
        with patch.dict(RESEARCH_LOCK['methods'],{'r21-ab-testing':row}):
            with self.assertRaisesRegex(InputError,'METHOD_INSTALL_INVALID_LOCK'):
                method_library._spec('r21-ab-testing')

    def test_old_version_notice_license_and_missing_reference_are_not_reused(self):
        fixture = self.root/'fixture'
        refs = fixture/'references'
        refs.mkdir(parents=True)
        with patch.object(method_library,'_BASE',fixture):
            with self.assertRaises(OSError):
                method_library._spec('r21-ab-testing')
            from hashlib import sha256
            repo, commit = RESEARCH_LOCK['repositories']['R21']
            method = 'r21-ab-testing'
            entry = 'skills/ab-testing/SKILL.md'
            files = {'LICENSE':b'MIT License','skills/ab-testing/SKILL.md':b'Method',
                     'skills/ab-testing/references/example.md':b'Fixed reference'}
            legal = dict(schema='external-method-provenance-v1',sources={'R21':dict(repository=repo,
                commit=commit,license='unknown',license_file='LICENSE',license_sha256=sha256(files['LICENSE']).hexdigest())})
            provenance=refs/'external-method-provenance.json'
            provenance.write_text(json.dumps(legal))
            with self.assertRaisesRegex(InputError,'METHOD_INSTALL_LICENSE_UNVERIFIED'):
                method_library._spec(method)
            legal['sources']['R21']['license']='MIT'
            provenance.write_text(json.dumps(legal))
            dependencies = {name.removeprefix('skills/ab-testing/'):sha256(data).hexdigest()
                            for name,data in files.items() if name!='LICENSE'}
            with (patch.dict(RESEARCH_LOCK['methods'],{method:['R21',entry,'META_AD_PLANNING',dependencies]}),
                  patch.object(method_library,'_fetch',side_effect=lambda _r,_v,name,_d:files[name])):
                ready=method_library.install(self.workspace,method)
                notice=Path(ready['notice'])
                notice.write_text('notice changed')
                self.assertEqual(method_library.lookup(self.workspace,method)['dependency'],'NOTICE.md')
                ready=method_library.install(self.workspace,method)
                ref=Path(ready['source_directory'])/'references/example.md'
                ref.rename(ref.with_suffix('.missing'))
                self.assertEqual(method_library.lookup(self.workspace,method)['status'],'SOURCE_DEPENDENCY_MISSING')
                changed_dependencies={**dependencies, 'SKILL.md':'a'*64}
                with patch.dict(RESEARCH_LOCK['methods'],{method:['R21',entry,'META_AD_PLANNING',changed_dependencies]}):
                    self.assertEqual(method_library.lookup(self.workspace,method)['status'],'METHOD_INSTALL_UPDATE_REQUIRED')
