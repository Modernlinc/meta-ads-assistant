"""Check verified Batch 01 text, automatic route and scope boundaries."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import test_fullchain_connect as connect_tests
from codex_ads_live import batch_knowledge, client_workspace
from codex_ads_live.validation import InputError


class BatchKnowledgeTest(TestCase):
    command = connect_tests.WorkspaceConnectTest.command

    def test_all_25_bodies_and_governed_source_metadata_are_available(self):
        code, scan = self.command('knowledge-search','--batch01','--topic','all')
        self.assertEqual((code,scan['count']),(0,25))
        for row in scan['rows']:
            found = batch_knowledge.search('all', method_id=row['method_id'])
            self.assertIn('不适用',found['body'])
            self.assertIn('分歧',found['body'])
            self.assertTrue(found['sources'])
            self.assertFalse(found['actions_authorized'])
            self.assertTrue(all(source['review_status'] != 'DISCOVERED' for source in found['sources'].values()))

    def test_natural_plan_retains_both_budget_schools_without_approval(self):
        with TemporaryDirectory() as temp:
            workspace=Path(temp).resolve()/'client'
            client_workspace.initialize(workspace)
            code, plan=self.command('method-plan','--workspace',workspace,'--objective','这个账户用 CBO 还是 ABO','--offline')
        row=plan['payload']['tasks'][0]
        self.assertEqual((code,row['intent']),(0,'strategy'))
        self.assertTrue({'MC-STR-02','MC-STR-03','MC-STR-05'}<={card['method_id'] for card in row['knowledge_candidates']})
        self.assertFalse(plan['payload']['actions_authorized'])

    def test_reference_and_cross_intent_cannot_be_promoted_to_compilation(self):
        found=batch_knowledge.search('all',method_id='MC-MEA-03')['method']
        packet=dict(method_id=found['method_id'],method_sha256=found['sha256'],selection='USED_AS_BASIS',
                    applicability_check='Missing account qualification',contribution='Use case context',
                    counterevidence='Contrary observations exist',assumptions=[],unknowns=['native field'])
        with self.assertRaisesRegex(InputError,'BATCH_KNOWLEDGE_REFERENCE_CANNOT_COMPILE'):
            batch_knowledge.applied([packet],'strategy','media-buyer prepare',[])
        with self.assertRaisesRegex(InputError,'BATCH_KNOWLEDGE_APPLICATION_SCOPE'):
            batch_knowledge.applied([packet],'tracking','tracking-audit',[])

    def test_tampered_method_text_is_refused(self):
        source=batch_knowledge._ROOT
        with TemporaryDirectory() as temp:
            fixture=Path(temp).resolve()
            for root_file in ('catalog.json','sources.json'):
                (fixture/root_file).write_bytes((source/root_file).read_bytes())
            (fixture/'cards').mkdir()
            (fixture/'cards/MC-STR-01.md').write_text('tampered')
            with patch.object(batch_knowledge,'_ROOT',fixture):
                with self.assertRaisesRegex(InputError,'BATCH_KNOWLEDGE_CONTENT_CHANGED'):
                    batch_knowledge.search('all',method_id='MC-STR-01')
