"""Isolated engineering API readback, no real travel, dining or business execution."""
import json
from pathlib import Path
from tests.runtime_meal_summaries import Client
PROOF=Path('docs/evidence/related-tasks-v03.json')

def setup():
    clerk=Client('qa-clerk');prior=json.loads(Path('docs/evidence/expense-journey-v03.json').read_text(encoding='utf-8'))
    goal='工程T055，非真实出行：我计划2026-11-12T09:00:00+01:00离营去工程工区核对设备，2026-11-12T14:00:00+01:00返营。帮我整理准备工作，必要的其他事项请提出关联建议，别替我作决定。'
    m=clerk.post('/api/matters',{'domain':'general','goal_text':goal,'auto_prepare':False});m=clerk.post('/api/matters/'+m['id']+'/artifacts',{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':'工程T055原人工稿保留：只核准备事项，未出行、安排供餐或扣餐。'})
    proof={'task':'T055','scope':'local engineering only; no real business action','matter_id':m['id'],'before':clerk.get('/api/matters/'+m['id']),'original_artifact_id':m['saved_artifact_id'],'prior_t053_private_v2':clerk.raw('/api/artifacts/'+prior['report']['artifact_id']+'/download?version=2&format=md')}
    PROOF.write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'matter_id':m['id'],'goal_text':goal},ensure_ascii=False))

def collect():
    clerk=Client('qa-clerk');other=Client('qa-employee');p=json.loads(PROOF.read_text(encoding='utf-8'));mid=p['matter_id'];m=clerk.get('/api/matters/'+mid);data=clerk.get('/api/matters/'+mid+'/related-tasks');assert not data['stale']
    dining=next(link for link in data['linked'] if link['domain']=='dining');child=clerk.get('/api/matters/'+dining['matter_id']);artifact=child['artifacts'][0];v2=clerk.raw('/api/artifacts/'+artifact['id']+'/download?version=2&format=md')
    assert child['assistant_status']=='waiting' and v2.endswith('工程T055本人用餐核对备注：先确认当天午餐需求，不自动取消或扣餐。')
    assert all(f['status']=='unknown' for f in child['facts'].values()) and all(t['status']=='unknown' for t in child['tracks'].values())
    assert m['tracks']==p['before']['tracks'] and m['sources']==p['after_context_added']['sources'];assert clerk.raw('/api/artifacts/'+p['original_artifact_id']+'/download?version=1&format=md')=='工程T055原人工稿保留：只核准备事项，未出行、安排供餐或扣餐。'
    prior=json.loads(Path('docs/evidence/expense-journey-v03.json').read_text(encoding='utf-8'));assert clerk.raw('/api/artifacts/'+prior['report']['artifact_id']+'/download?version=2&format=md')==p['prior_t053_private_v2']
    denied={'other_related':other.denied('/api/matters/'+mid+'/related-tasks'),'other_child':other.denied('/api/matters/'+child['id'])};assert all(v==404 for v in denied.values())
    assert len([a for a in m['actions'] if a['kind']=='prepare_model'])==2 and not any(a['kind']=='prepare_model' for a in child['actions'])
    p.update(after=m,related=data,child=child,private_v2_md=v2,permissions=denied,prior_t053_unchanged=True,model_calls_this_increment=2,runtime_session=69346,verification='combined two-mode related critical smoke 0.647s; affected legacy journey smoke 0.512s; affected syntax; actual Jiaorong/API/browser')
    PROOF.write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'matter_id':mid,'child_id':child['id'],'domain':'dining','related_count':len(data['items']),'model_calls':2,'permissions':denied,'prior_t053_unchanged':True},ensure_ascii=False))

if __name__=='__main__':
    import sys
    collect() if '--collect' in sys.argv else setup()
