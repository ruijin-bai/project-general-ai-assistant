"""Isolated engineering fixture/readback; no actual trip, meal or business action."""
import json, sqlite3
from pathlib import Path
from tests.runtime_meal_summaries import Client

PROOF=Path('docs/evidence/private-context-v03.json')
TARGET='b78e94c2-acd3-435f-a8fd-a9aa0344a732'
PREVIOUS='ad22ef5f-517d-4fda-b8d5-3e6ac9594401'

def database():
    return sqlite3.connect(Path('tests/ui-check-v03/assistant.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)

def setup():
    if PROOF.exists():
        print(json.dumps({'existing_fixture':json.loads(PROOF.read_text(encoding='utf-8'))['matter_id']},ensure_ascii=False));return
    clerk=Client('qa-clerk')
    goal='工程T056，非真实业务：11月12日去工程工区核对设备，下午13点离营，18点返营。我之前已有一个专门核对外出当天午餐是否留饭的用餐事项，这次请先看看能否沿那个事项继续核对；如果只是相似标题就告诉我待核，别重复新建，也别替我安排供餐。人员、车辆和午餐需求均未确认。'
    m=clerk.post('/api/matters',{'domain':'general','goal_text':goal,'auto_prepare':False})
    m=clerk.post('/api/matters/'+m['id']+'/artifacts',{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':'工程T056原人工稿，保持原文：先核关联，不安排车辆或供餐。'})
    prior=json.loads(Path('docs/evidence/expense-journey-v03.json').read_text(encoding='utf-8'))
    with database() as c:count=c.execute('SELECT COUNT(*) FROM matters').fetchone()[0]
    p={'task':'T056','scope':'isolated engineering titles only; no actual business or execution',
       'matter_id':m['id'],'original_artifact_id':m['saved_artifact_id'],
       'before':clerk.get('/api/matters/'+m['id']),
       'context_before':clerk.get('/api/matters/'+m['id']+'/related-tasks/context'),
       'target_before':clerk.get('/api/matters/'+TARGET),
       'prior_t058':clerk.get('/api/matters/'+PREVIOUS),
       'prior_t053_private_v2':clerk.raw('/api/artifacts/'+prior['report']['artifact_id']+'/download?version=2&format=md'),
       'matter_count_after_setup':count,'runtime_session':22231}
    assert not p['context_before']['enabled'] and any(i['matter_id']==TARGET for i in p['context_before']['items'])
    PROOF.write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'matter_id':m['id'],'allowed':False,'available_titles':len(p['context_before']['items'])},ensure_ascii=False))

def collect():
    clerk=Client('qa-clerk');other=Client('qa-employee');p=json.loads(PROOF.read_text(encoding='utf-8'));mid=p['matter_id'];m=clerk.get('/api/matters/'+mid)
    data=clerk.get('/api/matters/'+mid+'/related-tasks');linked=data['matches']['linked']
    assert len(linked)==1 and linked[0]['target']['matter_id']==TARGET and linked[0]['withdrawn']
    assert linked[0]['note']=='工程T056本人核对：这只是已有午餐需求的办理关联，日期、是否留饭和实际供餐仍另行核对。'
    assert not m['private_title_context_enabled'] and m['assistant_status']=='waiting'
    assert m['facts']==p['before']['facts'] and m['tracks']==p['before']['tracks'] and m['sources']==p['before']['sources']
    assert clerk.get('/api/matters/'+TARGET)==p['target_before'] and clerk.get('/api/matters/'+PREVIOUS)==p['prior_t058']
    assert clerk.raw('/api/artifacts/'+p['original_artifact_id']+'/download?version=1&format=md')=='工程T056原人工稿，保持原文：先核关联，不安排车辆或供餐。'
    assert clerk.raw('/api/artifacts/'+p['model_artifact_id']+'/download?version=2&format=md')==p['model_v2_md']
    withdrawn_preview=clerk.get('/api/matters/'+mid+'/model-input')
    assert 'other_tasks' not in withdrawn_preview['input']
    prior=json.loads(Path('docs/evidence/expense-journey-v03.json').read_text(encoding='utf-8'))
    assert clerk.raw('/api/artifacts/'+prior['report']['artifact_id']+'/download?version=2&format=md')==p['prior_t053_private_v2']
    with database() as c:
        count=c.execute('SELECT COUNT(*) FROM matters').fetchone()[0]
        usage=[json.loads(r[0]) for r in c.execute("SELECT message FROM events WHERE matter_id=? AND event_type='model_usage'",(mid,))]
        response=json.loads(c.execute("SELECT response FROM actions WHERE matter_id=? AND kind='prepare_model' AND status='completed'",(mid,)).fetchone()[0])
    assert count==p['matter_count_after_setup'] and len(usage)==1
    assert len(response['related_context']['items'])<=10
    denied={'context':other.denied('/api/matters/'+mid+'/related-tasks/context'),'matches':other.denied('/api/matters/'+mid+'/related-tasks')};assert set(denied.values())=={404}
    p.update(after=m,related=data,actual_model_response=response,actual_model_usage=usage,
             preview_after_withdrawal=withdrawn_preview,
             matter_count_after=count,permissions=denied,prior_manual_and_targets_unchanged=True,
             model_calls_this_increment=1,
             verification='reused valid two-mode private-context critical smoke 0.865s; affected JS syntax; actual Jiaorong/UI consent, match, open, pause, manual save, reopen, withdraw scope/link and resume')
    PROOF.write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'matter_id':mid,'matched_target':TARGET,'new_matters_during_matching':0,'model_calls':1,'permissions':denied,'prior_targets_unchanged':True},ensure_ascii=False))

if __name__=='__main__':
    import sys
    collect() if '--collect' in sys.argv else setup()
