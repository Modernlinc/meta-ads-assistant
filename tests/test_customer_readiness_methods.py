"""Exercise customer installation, pinned verification, normal discovery and recovery."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_fullchain_connect as connect_tests
from codex_ads_live import client_workspace, method_library
from codex_ads_live.credential_record import prepare_record, read_record
from codex_ads_live.journal import digest
from codex_ads_live.method_bundle import RESEARCH_LOCK
from codex_ads_live.validation import InputError


class CustomerMethodReadinessTest(unittest.TestCase):
    command = connect_tests.WorkspaceConnectTest.command
    optional = 'r21-ab-testing'

    def setUp(self):
        holder = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(holder).resolve()
        self.workspace = self.root/'client'
        self.command('workspace-connect','--workspace',self.workspace,'--start')
        group,entry,role,_ = RESEARCH_LOCK['methods'][self.optional]
        self.repo,self.commit=RESEARCH_LOCK['repositories'][group]
        self.payloads={'LICENSE':b'MIT License\nfixture',
                       entry:b'# Test method\nRead the material.',
                       'skills/ab-testing/references/example.md':b'# Example\nDefine a variable.'}
        self.expected = {name.removeprefix('skills/ab-testing/'):hashlib.sha256(value).hexdigest()
                         for name,value in self.payloads.items() if name!='LICENSE'}
        self.enterContext(patch.dict(RESEARCH_LOCK['methods'],{self.optional:[group,entry,role,self.expected]}))
        self.enterContext(patch.object(method_library,'_BASE',self.root))
        reference=self.root/'references'
        reference.mkdir()
        (reference/'external-method-provenance.json').write_text(json.dumps(
            dict(schema='external-method-provenance-v1',sources={group:dict(repository=self.repo,commit=self.commit,
                license='MIT',license_file='LICENSE',license_sha256=hashlib.sha256(self.payloads['LICENSE']).hexdigest())})),
            encoding='utf-8')

    def local(self, verb):
        return self.command(verb,'--workspace',self.workspace,'--skill',self.optional)

    def fake_fetch(self,repository,commit,path,deadline):
        self.assertEqual((repository,commit),(self.repo,self.commit))
        return self.payloads[path]

    def test_bundled_four_load_with_tamper_free_files_and_no_network(self):
        with patch.object(method_library,'_fetch',side_effect=AssertionError('network')) as fetch:
            for name in ('meta-facebook-ads','ad-creative','ecommerce-analytics','brand-brief'):
                code, found=self.command('method','--skill',name)
                self.assertEqual(code,0,found)
                root=Path(found['source_directory'])
                for item in ('entry.md','method.md','LICENSE.txt','NOTICE.md'):
                    self.assertIn(item,found['read_files'])
                    self.assertTrue((root/item).is_file())
                self.assertEqual(hashlib.sha256((root/'method.md').read_bytes()).hexdigest(),found['method_sha256'])
            fetch.assert_not_called()

    def test_optional_installs_only_pinned_text_then_reopens_and_reactivates_offline(self):
        self.assertEqual(self.local('method')[1]['status'],'SOURCE_DIRECTORY_REQUIRED')
        with patch.object(method_library,'_fetch',side_effect=self.fake_fetch) as fetch:
            code, installed=self.local('method-install')
            self.assertEqual((code,installed['status']),(0,'METHOD_INSTALL_VERIFIED'))
            self.assertTrue(installed['network_used'])
            self.assertEqual([item.args[2] for item in fetch.call_args_list],list(self.payloads))
        with patch.object(method_library,'_fetch',side_effect=AssertionError('network')):
            code, reopened=self.local('method')
            self.assertEqual((code,reopened['status']),(0,'SOURCE_METHOD_VERIFIED'))
            self.assertEqual(Path(reopened['license_file']).read_bytes(),self.payloads['LICENSE'])
            self.assertIn(self.commit,Path(reopened['notice']).read_text())
            disabled=self.local('method-disable')[1]
            self.assertTrue(disabled['files_retained'])
            self.assertEqual(self.local('method')[1]['status'],'METHOD_DISABLED')
            reactivated=self.local('method-install')[1]
            self.assertTrue(reactivated['reused'])
            self.assertFalse(reactivated['network_used'])
            self.assertEqual(self.local('method')[1]['source_directory'],reopened['source_directory'])

    def test_failed_or_tampered_optional_download_does_not_publish_invalid_files(self):
        with patch.object(method_library,'_fetch',side_effect=InputError('METHOD_DOWNLOAD_RETRY_OR_OFFLINE')):
            self.assertEqual(self.local('method-install')[1]['error_code'],'METHOD_DOWNLOAD_RETRY_OR_OFFLINE')
        with patch.object(method_library,'_fetch',return_value=b'tampered'):
            self.assertEqual(self.local('method-install')[1]['error_code'],'METHOD_DOWNLOAD_HASH_MISMATCH')
        self.assertEqual(self.local('method')[1]['status'],'SOURCE_DIRECTORY_REQUIRED')
        self.assertFalse((self.workspace/'method-library/bundles').exists())
        with patch.object(method_library,'_fetch',side_effect=self.fake_fetch):
            receipt=self.local('method-install')[1]
        (Path(receipt['source_directory'])/'SKILL.md').write_text('unexpected contents')
        self.assertEqual(self.local('method')[1]['status'],'SOURCE_VERSION_MISMATCH')
        with patch.object(method_library,'_fetch',side_effect=self.fake_fetch):
            recovered=self.local('method-install')[1]
        self.assertNotEqual(recovered['source_directory'],receipt['source_directory'])
        self.assertEqual(self.local('method')[1]['status'],'SOURCE_METHOD_VERIFIED')

    def test_first_setup_stops_without_app_or_proof_and_resumes_at_original_state(self):
        route=('workspace-connect','--workspace',self.workspace)
        self.assertEqual(self.command(*route)[1]['step'],'CHOOSE_ROUTE')
        self.assertEqual(self.command(*route,'--route','user-graph')[1]['step'],'PREPARE_APP')
        self.assertEqual(self.command(*route,'--app-id','101','--business-id','102')[1]['step'],'ENROLL_USER_REFERENCE')
        self.assertEqual(self.command(*route,'--discover')[1]['step'],'REVIEW_APP_PROOF')
        self.assertEqual(self.command(*route)[1]['step'],'ENROLL_USER_REFERENCE')

    def test_installed_optional_and_bundled_results_save_without_source_root(self):
        self.command('workspace-connect','--workspace',self.workspace,'--route','user-graph',
                     '--app-id','101','--business-id','102')
        client_workspace.map_asset(self.workspace,'ACCOUNT','104','EV_LOCAL_FIXTURE')
        client_workspace.default_asset(self.workspace,'ACCOUNT','104')
        material=self.root/'product.txt'
        material.write_text('Synthetic 320 ml cup')
        with patch.object(method_library,'_fetch',side_effect=self.fake_fetch):
            self.assertEqual(self.local('method-install')[0],0)
        with patch.object(method_library,'_fetch',side_effect=AssertionError('network')):
            for name in (self.optional,'meta-facebook-ads'):
                summary={'schema':'partner-evidence-brief-v1','role':'META_AD_PLANNING',
                         'product':'Synthetic cup','market':'US','items':[
                             {'kind':'test_hypothesis','ref':'EV_LOCAL_FIXTURE','observed_at':'2026-11-27T00:00:00Z',
                              'state':'HYPOTHESIS','text':'Hold the offer constant when comparing copy.'}]}
                request=self.root/(name+'.json')
                request.write_text(json.dumps(dict(deliverable='Synthetic A/B plan',brief=summary)))
                code,saved=self.command('workspace-method-record','--workspace',self.workspace,'--skill',name,
                                        '--input',request,'--output',self.root/name,'--source-material',material)
                self.assertEqual((code,saved.get('status')),(0,'HOST_METHOD_RESULT_ACCEPTED'),saved)
                receipt=json.loads((self.root/name/'METHOD-RECEIPT.json').read_bytes())
                self.assertEqual(receipt['payload']['method'],name)

    def test_expired_account_routes_to_reconnect_without_switching_asset(self):
        self.command('workspace-connect','--workspace',self.workspace,'--route','user-graph',
                     '--app-id','101','--business-id','102')
        client_workspace.map_asset(self.workspace,'ACCOUNT','104','EV_LOCAL_FIXTURE')
        client_workspace.default_asset(self.workspace,'ACCOUNT','104')
        record=self.workspace/'credential-record'
        prepare_record(record,'101','103','macos')
        enrolled=read_record(record)

        def bind_expired(profile):
            profile['binding']=dict(principal_id='103',secret_ref=enrolled['reference'],record_hash=digest(enrolled),
                proof='NOT_REQUIRED_CONFIRMED',owner_confirmed=True,developer_confirmed=True,accounts=['104'],
                auth_status='CURRENT',expires_at='2020-01-01T12:00:00+00:00')

        client_workspace.update(self.workspace,bind_expired)
        code, result=self.command('workspace-connect','--workspace',self.workspace)
        self.assertEqual((code,result['step'],result['account_id']),(0,'RECONNECT','104'))
        self.assertFalse(result['read_authorized'])
