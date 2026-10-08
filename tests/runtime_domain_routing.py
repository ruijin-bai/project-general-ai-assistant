"""Readback of isolated engineering natural-goal UI, no actual repair or dispatch."""
import json,sqlite3
from pathlib import Path
from tests.runtime_meal_summaries import Client

PROOF=Path('docs/evidence/domain-routing-v03.json')
GOAL='工程T059，非真实业务：工程B房的空调在滴水。帮我整理一份给综合经办核对的报修需求，必要的联系人和可进入时段还没确定；别替我分派、进房或说已经修好。'

def setup():
    if PROOF.exists():print('已存在工程恢复记录，未重建');return
    c=Client('qa-clerk');ids=['dacf99ed-5ee8-4ddb-a3a9-5bdbec29be96','ad22ef5f-517d-4fda-b8d5-3e6ac9594401','b78e94c2-acd3-435f-a8fd-a9aa0344a732']
    p={'task':'T059','goal_text':GOAL,'scope':'isolated engineering only; no repair, entry or dispatch','prior_matters':[c.get('/api/matters/'+mid) for mid in ids],'runtime_session':37386}
    PROOF.write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'goal_text':GOAL,'prior_preserved':len(ids)},ensure_ascii=False))

def collect(mid):
    c=Client('qa-clerk');p=json.loads(PROOF.read_text(encoding='utf-8'));m=c.get('/api/matters/'+mid)
    assert m['goal_text']==GOAL and m['domain']=='repair' and m['assistant_status']=='waiting'
    assert m['domain_routing']['mode']=='manual' and m['domain_routing']['state']=='selected'
    assert len(m['sources'])==1 and m['sources'][0]['text']==GOAL and all(t['status']=='unknown' for t in m['tracks'].values())
    model=next(a for a in m['artifacts'] if a['title']=='交融模型准备稿');assert model['version']==2 and model['status']=='human_saved'
    assert model['content'].endswith('工程T059本人核对备注：联系人与进入时段还需核对，准备稿不代表分派或处理。')
    original=c.raw('/api/artifacts/'+model['id']+'/download?version=1&format=md');assert original==p['model_v1_md']
    for before in p['prior_matters']:assert c.get('/api/matters/'+before['id'])==before
    with sqlite3.connect(Path('tests/ui-check-v03/assistant.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
        usage=[json.loads(r[0]) for r in db.execute("SELECT message FROM events WHERE matter_id=? AND event_type='model_usage'",(mid,))]
        changes=[r[0] for r in db.execute("SELECT event_type FROM events WHERE matter_id=? AND event_type IN ('domain_routed','domain_corrected')",(mid,))]
    assert len(usage)==1 and changes.count('domain_routed')==1 and changes.count('domain_corrected')==2
    assert len([a for a in m['actions'] if a['kind']=='prepare_model'])==1
    denied=Client('qa-employee').denied('/api/matters/'+mid);assert denied==404
    p.update(matter_id=mid,after=m,model_artifact_id=model['id'],model_v2_md=model['content'],actual_model_usage=usage,category_events=changes,other_owner_read=denied,prior_matters_unchanged=True,
             verification='combined local/identity routing critical smoke 0.430s; affected Python/JS syntax; actual Jiaorong natural goal, classification, manual v2, pause, category correction, reopen, restore and resume')
    PROOF.write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'matter_id':mid,'domain':'repair','model_calls':1,'manual_version':2,'prior_matters_unchanged':True,'other_owner_read':denied},ensure_ascii=False))

if __name__=='__main__':
    import sys
    collect(sys.argv[2]) if '--collect' in sys.argv else setup()
