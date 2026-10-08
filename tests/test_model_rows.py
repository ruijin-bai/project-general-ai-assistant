"""Targeted model rows -> human confirmation -> exact arithmetic and preservation."""
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from assistant_app.model import ModelClient
from assistant_app.model_rows import normalize_rows
from assistant_app.server import Runner
from assistant_app.store import AppError, Store, uid
from assistant_app.structured import StructuredService

class ModelRowsSmoke(unittest.TestCase):
    def test_literal_rows_scoped_read_preserved_edit_and_exact_confirmation(self):
        for protected in (False,True):
            for domain in ('expense','inventory'):
                with self.subTest(protected=protected,domain=domain),tempfile.TemporaryDirectory(prefix='qa-model-rows-',dir=Path(__file__).resolve().parent) as folder:
                    store=Store(folder);actors=[None,None]
                    if protected:
                        store.enable_identity()
                        with store.transaction() as c:
                            for i in range(2):
                                aid=uid();c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')",(aid,'fixture'+str(i),'fixture'));actors[i]={'id':aid,'auth_epoch':1}
                    dimension='currency' if domain=='expense' else 'unit';unit='USD' if domain=='expense' else '条';label='打印' if domain=='expense' else '抹布'
                    with store.as_actor(actors[0]):
                        m=store.mutate('create',None,{'domain':domain,'goal_text':'工程演练：核对原话，未批准支付或实际收发','auto_prepare':False},uid())
                        lines=[f'期间2026-10，{label}原值0.1，{unit}。',f'期间2026-10，{label}原值0.2，{unit}。','原值10.1；其他记录缺数，不能补零。',f'旧人工记录，1.0，{"EUR" if domain=="expense" else "个"}，2026-09。']
                        m=store.mutate('source',m['id'],{'expected_version':m['version'],'kind':'user_text','text':'\n'.join(lines)},uid());source=m['sources'][-1]
                        proposals=[{'label':label,'value':value,dimension:unit,'period':'2026-10','refs':[{'source_index':2,'start_line':i,'end_line':i,'quote':lines[i-1]}]} for i,value in ((1,'0.1'),(2,'0.2'))]
                        normalized=normalize_rows(proposals,m);self.assertEqual(normalized[0]['confirmation'],'candidate')
                        object_ref=json.loads(json.dumps(proposals[:1]));object_ref[0]['refs']=object_ref[0]['refs'][0]
                        self.assertEqual(normalize_rows(object_ref,m),normalized[:1])
                        multiple=json.loads(json.dumps(proposals[:1]));multiple[0]['refs'].insert(0,{'source_index':1,'start_line':1,'end_line':1,'quote':m['sources'][0]['text']})
                        self.assertEqual(normalize_rows(multiple,m)[0]['source_id'],source['id'])
                        bad=json.loads(json.dumps(proposals));bad[0]['value']='0.3'
                        with self.assertRaises(ValueError):normalize_rows(bad,m)
                        for raw_number in ('1e2','0x10','1/2','2026-10'):
                            malformed=json.loads(json.dumps(proposals[:1]));malformed[0]['value']='1' if raw_number!='2026-10' else '2026'
                            quote=f"{label}，{raw_number}，{unit}，2026-10。";changed={**m,'sources':[m['sources'][0],{**source,'text':quote}]}
                            malformed[0]['refs'][0].update(start_line=1,end_line=1,quote=quote)
                            with self.assertRaises(ValueError):normalize_rows(malformed,changed)
                        bad=json.loads(json.dumps(proposals));bad[0]['refs'][0].update(start_line=3,end_line=3,quote=lines[2]);bad[0].update(label='其他记录',period='10.1',**{dimension:'原值'})
                        with self.assertRaisesRegex(ValueError,'row number'):normalize_rows(bad,m)
                        with self.assertRaises(ValueError):normalize_rows([proposals[0],proposals[0]],m)
                        bad=json.loads(json.dumps(proposals));bad[0]['value']='0'
                        with self.assertRaises(ValueError):normalize_rows(bad,m)
                        prior={'id':'manual-old','label':'旧人工记录','value':'1.0',dimension:'EUR' if domain=='expense' else '个','period':'2026-09','source_id':source['id'],'source_position':'第4行'}
                        m=store.mutate('rows',m['id'],{'expected_version':m['version'],'rows':[prior]},uid())
                        m=store.mutate('manual_artifact',m['id'],{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':'旧人工稿，不能覆盖'},uid())
                        original=store.detail(m['id']);calls=[];client=ModelClient(enabled=True,api_key='EngineeringStubOnly')
                        response={'content':'工程候选，数值未计算','candidates':{},'questions':['另一记录缺数，保持待核'],'row_candidates':proposals}
                        raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(response,ensure_ascii=False)}}],'usage':{'total_tokens':123}}).encode()
                        def opened(request,timeout):
                            payload=json.loads(request.data);calls.append(payload);self.assertIn('row_candidates',payload['messages'][0]['content']);self.assertEqual(json.loads(payload['messages'][1]['content'])['sources'][1]['source_index'],2);return BytesIO(raw)
                        client._opener=SimpleNamespace(open=opened)
                        store.mutate('prepare',m['id'],{'expected_version':m['version'],'kind':'prepare','mode':'model'},uid())
                    runner=Runner.__new__(Runner);runner.store=store;runner.model=client;runner.stopped=threading.Event();self.assertTrue(runner.step())
                    with store.as_actor(actors[0]):
                        service=StructuredService(store);before=store.detail(m['id']);candidates=service.get_row_candidates(m['id'])
                        self.assertEqual(store.detail(m['id']),before);self.assertEqual(before['rows'],original['rows']);self.assertEqual(before['calculation']['groups'],original['calculation']['groups']);self.assertEqual(len(calls),1)
                        self.assertFalse(candidates['stale']);self.assertEqual(len(candidates['items']),2)
                        paused=store.mutate('control',m['id'],{'expected_version':before['version'],'command':'pause'},uid())
                        rows=[prior]+[{key:item[key] for key in ('id','label','value',dimension,'period','source_id','source_position')} for item in candidates['items']]
                        rows[1]['label']=label+'（本人核对）';data={'expected_version':paused['version'],'rows':rows};key=uid();saved=store.mutate('rows',m['id'],data,key)
                        self.assertEqual(store.mutate('rows',m['id'],data,key),saved)
                        self.assertEqual(saved['assistant_status'],'paused');self.assertEqual(saved['tracks'],original['tracks']);self.assertTrue(service.get_row_candidates(m['id'])['stale'])
                        self.assertTrue(any(a['content']=='旧人工稿，不能覆盖' for a in saved['artifacts']))
                        if domain=='expense':self.assertEqual(next(g for g in saved['calculation']['groups'] if g['currency']=='USD')['total'],'0.3')
                        else:self.assertEqual(sorted(g['total'] for g in saved['calculation']['groups']),['0.1','0.2','1.0'])
                        with self.assertRaises(AppError) as conflict:store.mutate('rows',m['id'],data,uid())
                        self.assertEqual(conflict.exception.status,409)
                        reopened=Store(folder)
                        with reopened.as_actor(actors[0]):self.assertEqual(reopened.detail(m['id'])['rows'],saved['rows'])
                        self.assertEqual(len(calls),1)
                    if protected:
                        with store.as_actor(actors[1]),self.assertRaises(AppError) as denied:StructuredService(store).get_row_candidates(m['id'])
                        self.assertEqual(denied.exception.status,404)
