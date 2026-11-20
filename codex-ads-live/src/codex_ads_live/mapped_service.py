"""Independent, durable synthetic service. No HTTP, credentials or Meta writes.

Atomic comparisons and correlated receipts below are simulator properties;
they are NOT assertions about Meta CAS or idempotency support.
"""
from copy import deepcopy
from dataclasses import asdict
import re

from .activation import CURRENT, LEGACY, inventory, is_activation, is_budget_change
from .journal import Journal, canonical, digest
from .mapped_actions import (AD_MATERIAL_VERSIONS, DUPLICATE_CREATE_REQUEST, MappedWriteTransport, packet_review,
                             same_create_request, validate_receipt)
from .validation import InputError, fail, object_only
from .meta_write_mapping import validate_targeting
from .write_snapshots import SnapshotPlan, SyntheticSnapshotTransport, normalize_snapshot
from .materials import validate_material, CREATIVE_KEYS


def _node(plan, data, configuration=None):
    return dict(plan=asdict(plan),data=normalize_snapshot(plan,200,data),configuration=deepcopy(configuration or {}))


def _creative_content(object_id, account, request):
    story=request['parameters']['object_story_spec']
    return dict(id=object_id,account_id=account,actor_id=story['page_id'],object_story_spec=deepcopy(story),
                asset_feed_spec={},degrees_of_freedom_spec={},platform_customizations={},url_tags='')


def _validate(data):
    if type(data) is not dict: fail('MAPPED_SERVICE_SCHEMA')
    keys=('schema','nodes','receipts','materials') if data.get('schema')=='mapped-synthetic-service-v2' else ('schema','nodes','receipts')
    object_only(data,keys,keys)
    if data['schema'] not in ('mapped-synthetic-service-v1','mapped-synthetic-service-v2'): fail('MAPPED_SERVICE_SCHEMA')
    if (type(data['nodes']) is not dict or len(data['nodes'])>100 or type(data['receipts']) is not dict
            or len(data['receipts'])>50 or len(canonical(data).encode())>1500000): fail('MAPPED_SERVICE_LIMIT')
    for key,node in data['nodes'].items():
        object_only(node,('plan','data','configuration'),('plan','data','configuration'))
        object_only(node['plan'],('level','object_id','account_id','page_id','version'),('level','object_id','account_id','page_id','version'))
        plan=SnapshotPlan(**node['plan'])
        if key!=plan.level+':'+plan.object_id or normalize_snapshot(plan,200,node['data'])!=node['data']:
            fail('MAPPED_SERVICE_NODE')
        if type(node['configuration']) is not dict: fail('MAPPED_SERVICE_CONFIGURATION')
        # Configuration is either an empty seed or parameters from one validated
        # action. Only those fields may be retained; no arbitrary payload store.
        allowed={'name','objective','buying_type','special_ad_categories','is_adset_budget_sharing_enabled',
            'status','campaign_id','daily_budget','billing_event','optimization_goal','bid_strategy',
            'destination_type','targeting','adset_id','creative'}
        if plan.level=='ADSET':
            allowed.add('promoted_object')
        if plan.level=='CREATIVE':
            allowed={'name','image_hash','destination_url','primary_text','headline','description','call_to_action'}
        if set(node['configuration'])-allowed: fail('MAPPED_SERVICE_CONFIGURATION')
    for execution,item in data['receipts'].items():
        object_only(item,('packet','receipt','node_key'),('packet','receipt','node_key'))
        if not re.fullmatch('SIM_EXEC_[A-F0-9]{32}',execution): fail('MAPPED_EXECUTION_ID')
        validate_receipt(item['receipt'],dict(packet=item['packet'],execution_id=execution))
        level=item['packet']['draft']['operation'].removeprefix('CREATE_')
        if not item['packet']['draft']['operation'].startswith('CREATE_'): level=item['packet']['draft']['target_level']
        if item['node_key']!=level+':'+item['receipt']['object_id'] or item['node_key'] not in data['nodes']:
            fail('MAPPED_SERVICE_RECEIPT_NODE')
    materials=data.get('materials',{})
    if type(materials) is not dict or len(materials)>30: fail('MAPPED_SERVICE_MATERIAL_LIMIT')
    for creative_id,content in materials.items():
        object_only(content,CREATIVE_KEYS,CREATIVE_KEYS)
        node=data['nodes'].get('CREATIVE:'+creative_id)
        if node is None or (content['id'],content['account_id'],content['actor_id'])!=(creative_id,node['data']['account_id'],node['data']['actor_id']): fail('MAPPED_SERVICE_MATERIAL_SCOPE')


class SyntheticMappedService(MappedWriteTransport):
    def __init__(self,directory,*,existing_only=False):
        self.journal=Journal(directory,existing_only=existing_only)
        self.existing_only=existing_only
        self.transports=[];self.send_attempts=0

    def _load(self):
        data,damaged=self.journal.load(_validate)
        if damaged: fail('MAPPED_SERVICE_RECOVERY_REQUIRED')
        if data is None: fail('MAPPED_SERVICE_NOT_SEEDED')
        if data['schema']=='mapped-synthetic-service-v1': data.update(schema='mapped-synthetic-service-v2',materials={})
        return data

    def seed(self,fixtures, *, budget_targeting=None):
        """Explicit fixtures keyed by level, not a live response ingestion API."""
        if type(fixtures) is not dict: fail('MAPPED_SERVICE_FIXTURES')
        nodes={}
        for level, rows in fixtures.items():
            for row in rows if isinstance(rows, list) else [rows]:
                object_id = row['id'].removeprefix('act_')
                key = level + ':' + object_id
                if key in nodes:
                    fail('MAPPED_SERVICE_DUPLICATE_FIXTURE')
                plan = (SnapshotPlan(level, object_id, page_id=object_id) if level == 'PAGE'
                        else SnapshotPlan(level, object_id, account_id=row['account_id']))
                nodes[key] = _node(plan, row)
        if budget_targeting is not None:
            if type(budget_targeting) is not dict:
                fail('MAPPED_SERVICE_FIXTURES')
            for adset_id, targeting in budget_targeting.items():
                validate_targeting(targeting)
                adset = nodes.get('ADSET:' + adset_id)
                campaign = nodes.get('CAMPAIGN:' + adset['data']['campaign_id']) if adset else None
                if not campaign:
                    fail('MAPPED_SERVICE_OBJECT_MISSING')
                adset['configuration']['targeting'] = deepcopy(targeting)
                campaign['configuration']['is_adset_budget_sharing_enabled'] = False
        with self.journal.locked():
            old,damaged=self.journal.load(_validate)
            if old is not None or damaged: fail('MAPPED_SERVICE_ALREADY_SEEDED')
            self.journal.save(dict(schema='mapped-synthetic-service-v2',nodes=nodes,receipts={},materials={}),_validate)

    def budget_inventory(self, draft, allowed):
        with self.journal.locked():
            return inventory(self._load(), draft, allowed)

    def activation_inventory(self, draft, allowed):
        with self.journal.locked():
            return inventory(self._load(), draft, allowed)

    @staticmethod
    def _match_activation(data, packet, *, after=False):
        current = inventory(data, packet['draft'], packet['bundle']['allowlist'])
        coverage = packet['coverage']
        if coverage['schema'] == LEGACY and after:
            current.pop('campaign')
        elif coverage['schema'] != CURRENT:
            fail('ACTIVATION_LEGACY_REPROPOSAL_REQUIRED')
        expected = coverage['inventory']
        if after:
            expected = deepcopy(expected)
            expected['target_before']['status'] = 'ACTIVE'
            if expected['target_level'] == 'CAMPAIGN' and coverage['schema'] == CURRENT:
                expected['campaign']['status'] = 'ACTIVE'
            key = {'ADSET': 'adsets', 'AD': 'ads'}.get(expected['target_level'])
            for row in expected[key] if key else ():
                if row['id'] == expected['target_id']:
                    row['status'] = 'ACTIVE'
        if current != expected:
            fail('ACTIVATION_INVENTORY_CHANGED')

    @staticmethod
    def _match_budget(data, packet, *, after=False):
        if packet['schema'] != 'mapped-synthetic-action-v4':
            fail('BUDGET_LEGACY_REPROPOSAL_REQUIRED')
        expected = packet['coverage']['inventory']
        if after:
            expected = deepcopy(expected)
            updated_budget = str(packet['draft']['parameters']['daily_budget'])
            expected['target_before']['daily_budget'] = updated_budget
            expected['adsets'][0]['daily_budget'] = updated_budget
        if inventory(data, packet['draft'], packet['bundle']['allowlist']) != expected:
            fail('BUDGET_INVENTORY_CHANGED')

    def seed_material(self,material):
        validate_material(material)
        if material['source_mode']!='SYNTHETIC' or material['creative'] is None: fail('MAPPED_SYNTHETIC_MATERIAL_ONLY')
        with self.journal.locked():
            data=self._load();creative_id=material['creative_id']
            if creative_id in data['materials']: fail('MAPPED_SERVICE_MATERIAL_ALREADY_EXISTS')
            data['materials'][creative_id]=deepcopy(material['creative']);self.journal.save(data,_validate)

    def material_content(self,creative_id):
        with self.journal.locked(): return deepcopy(self._load()['materials'].get(creative_id))

    def simulate_material_change(self,material):
        validate_material(material)
        if material['source_mode']!='SYNTHETIC' or material['creative'] is None: fail('MAPPED_SYNTHETIC_MATERIAL_ONLY')
        with self.journal.locked():
            data=self._load();creative_id=material['creative_id']
            if creative_id not in data['materials']: fail('MAPPED_SERVICE_MATERIAL_MISSING')
            data['materials'][creative_id]=deepcopy(material['creative']);self.journal.save(data,_validate)

    def state(self):
        with self.journal.locked(): return deepcopy(self._load())

    def simulate_change(self,level,object_id,updates):
        """Test-only mutation of fixed read fields, never a network request."""
        if type(updates) is not dict: fail('MAPPED_SERVICE_CHANGE')
        with self.journal.locked():
            data=self._load();key=level+':'+object_id
            if key not in data['nodes']: fail('MAPPED_SERVICE_OBJECT_MISSING')
            node=data['nodes'][key];plan=SnapshotPlan(**node['plan'])
            changed={**node['data'],**deepcopy(updates)};normalized=normalize_snapshot(plan,200,changed)
            if normalized!=changed: fail('MAPPED_SERVICE_CHANGE')
            node['data']=normalized;self.journal.save(data,_validate)

    def simulate_targeting_change(self, adset, targeting):
        validate_targeting(targeting)
        with self.journal.locked():
            data = self._load()
            node = data['nodes'].get('ADSET:' + adset)
            if node is None:
                fail('MAPPED_SERVICE_OBJECT_MISSING')
            node['configuration']['targeting'] = deepcopy(targeting)
            self.journal.save(data, _validate)

    @staticmethod
    def _check_duplicate_create(data,draft):
        if any(same_create_request(draft,item['packet']['draft']) for item in data['receipts'].values()):
            fail(DUPLICATE_CREATE_REQUEST)

    def preflight(self,packet):
        packet_review(packet)
        draft=packet['draft']
        if draft['operation'].startswith('CREATE_'):
            with self.journal.locked():
                self._check_duplicate_create(self._load(),draft)

    def snapshot_factory(self,plan):
        transport=SyntheticSnapshotTransport(plan,[])
        def get(request):
            if request!=plan.request(): fail('SNAPSHOT_REQUEST_CHANGED')
            transport.calls.append(deepcopy(request))
            with self.journal.locked():
                node=self._load()['nodes'].get(plan.level+':'+plan.object_id)
                if node is None or node['plan']!=asdict(plan): fail('MAPPED_SERVICE_OBJECT_MISSING')
                return 200,deepcopy(node['data'])
        # Keep the collector's exact synthetic type contract, while reading the
        # independent service afresh for every simulated GET in both sweeps.
        transport.get=get;self.transports.append(transport);return transport

    def send(self,packet,execution_id,*,before_commit=None):
        review=packet_review(packet)
        if type(execution_id) is not str or not re.fullmatch('SIM_EXEC_[A-F0-9]{32}',execution_id): fail('MAPPED_EXECUTION_ID')
        self.send_attempts+=1
        with self.journal.locked():
            data=self._load()
            if execution_id in data['receipts']: fail('MAPPED_SERVICE_REPLAY_REFUSED')
            if not callable(before_commit): fail('MAPPED_SERVICE_COMMIT_GUARD_REQUIRED')
            draft=packet['draft'];op=draft['operation']
            if op.startswith('CREATE_'):
                self._check_duplicate_create(data,draft)
            if is_activation(packet['draft']):
                if packet['schema'] != 'mapped-synthetic-action-v3':
                    fail('ACTIVATION_LEGACY_REPROPOSAL_REQUIRED')
                self._match_activation(data, packet)
            elif is_budget_change(packet['draft']):
                self._match_budget(data, packet)
            if packet['draft']['operation']=='CREATE_AD':
                if packet['schema'] not in AD_MATERIAL_VERSIONS: fail('MAPPED_LEGACY_AD_REPROPOSAL_REQUIRED')
                material=packet['material']
                if data['materials'].get(material['creative_id'])!=material['creative']: fail('MAPPED_SERVICE_MATERIAL_CHANGED')
            for entry in packet['bundle']['entries']:
                plan=SnapshotPlan(**entry['plan']);node=data['nodes'].get(plan.level+':'+plan.object_id)
                if node is None or node['plan']!=entry['plan'] or node['data']!=entry['data']:
                    fail('MAPPED_SERVICE_STATE_CHANGED')
            params=deepcopy(draft['parameters']);account=draft['account_id']
            if op.startswith('CREATE_'):
                level=op.removeprefix('CREATE_');used={x['plan']['object_id'] for x in data['nodes'].values()}
                number=90000000
                while str(number) in used: number+=1
                object_id=str(number);key=level+':'+object_id
                body=dict(id=object_id,account_id=account,status='PAUSED',effective_status='PAUSED')
                if level=='CAMPAIGN':
                    body.update(objective=params['objective'],buying_type=params['buying_type'],special_ad_categories=params['special_ad_categories'],daily_budget='0',lifetime_budget='0')
                elif level=='ADSET':
                    body.update(campaign_id=params['campaign_id'],daily_budget=str(params['daily_budget']),lifetime_budget='0',
                        **{k:params[k] for k in ('billing_event','optimization_goal','bid_strategy','destination_type')})
                elif level=='AD':
                    parent=data['nodes']['ADSET:'+params['adset_id']]['data']
                    body.update(campaign_id=parent['campaign_id'],adset_id=params['adset_id'],creative={'id':params['creative']['creative_id']})
                elif level=='CREATIVE':
                    content=_creative_content(object_id,account,review['approval_binding']['request'])
                    body={key:content[key] for key in ('id','account_id','actor_id')}
                    data['materials'][object_id]=content
                else:
                    fail('MAPPED_SERVICE_CREATE_LEVEL')
                data['nodes'][key]=_node(SnapshotPlan(level,object_id,account_id=account),body,params)
            else:
                object_id=draft['target_id'];key=draft['target_level']+':'+object_id
                node=data['nodes'][key]
                if op=='SET_ADSET_DAILY_BUDGET': node['data']['daily_budget']=str(params['daily_budget'])
                else: node['data']['status']=params['status']
                # effective_status is observed state, not a promised consequence.
                node['configuration'].update(params)
            node=data['nodes'][key]
            receipt=dict(schema='mapped-synthetic-receipt-v1',execution_id=execution_id,review_sha256=review['review_sha256'],
                request_sha256=digest(review['approval_binding']['request']),account_id=account,object_id=object_id,operation=op,
                parameters=params,configured_status=node['data'].get('status'),after_data_sha256=digest(node['data']),
                after_configuration_sha256=digest(node['configuration']))
            data['receipts'][execution_id]=dict(packet=deepcopy(packet),receipt=receipt,node_key=key)
            self.journal.save(data,_validate,before_write=before_commit)
            return deepcopy(receipt)

    def readback(self,packet,execution_id):
        review=packet_review(packet)
        with self.journal.locked(existing_only=self.existing_only):
            data=self._load();item=data['receipts'].get(execution_id)
            if item is None: return None
            if item['packet']!=packet: fail('MAPPED_SERVICE_RECEIPT_CONFLICT')
            try:
                if packet['schema'] == 'mapped-synthetic-action-v3':
                    self._match_activation(data, packet, after=True)
                if packet['schema'] == 'mapped-synthetic-action-v4':
                    self._match_budget(data, packet, after=True)
            except InputError:
                fail('MAPPED_SERVICE_REVIEW_SCOPE_CHANGED')
            if packet['schema'] in AD_MATERIAL_VERSIONS and packet['material'] is not None:
                material=packet['material']
                if data['materials'].get(material['creative_id'])!=material['creative']: fail('MAPPED_SERVICE_MATERIAL_CHANGED')
            node=data['nodes'][item['node_key']];receipt=item['receipt']
            if digest(node['data'])!=receipt['after_data_sha256'] or digest(node['configuration'])!=receipt['after_configuration_sha256']:
                fail('MAPPED_SERVICE_READBACK_CHANGED')
            if packet['draft']['operation']=='CREATE_CREATIVE':
                expected=_creative_content(receipt['object_id'],receipt['account_id'],review['approval_binding']['request'])
                if data['materials'].get(receipt['object_id'])!=expected:
                    fail('MAPPED_SERVICE_MATERIAL_CHANGED')
            return deepcopy(receipt)
