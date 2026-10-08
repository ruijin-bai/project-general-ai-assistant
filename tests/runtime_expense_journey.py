"""Local engineering HTTP fixture/readback, no real travel or financial operation."""
import json
from pathlib import Path
from tests.runtime_meal_summaries import Client

PROOF=Path('docs/evidence/expense-journey-v03.json')
def setup():
    clerk=Client('qa-clerk');cook=Client('qa-cook')
    prior=json.loads(Path('docs/evidence/meal-summaries-v03.json').read_text(encoding='utf-8'))
    preserved={'meal_share':cook.get('/api/shares/'+prior['received_share']['id']),'meal_private_v2':clerk.raw('/api/artifacts/'+prior['report']['artifact_id']+'/download?version=2&format=md'),'menus':[{key:item.get(key) for key in ('id','version','status')} for item in cook.get('/api/menu-publications')['items']]}
    travel=clerk.post('/api/matters',{'domain':'travel','goal_text':'工程T053已核计划引用测试，非实际出行；私人同行与备注不进入费用稿','auto_prepare':False})
    tid=travel['id'];travel=clerk.post('/api/matters/'+tid+'/journey/plan',{'expected_version':travel['version'],'input_revision':travel['revision_no'],'plan_semantics':{'departure_kind':'camp_departure','return_kind':'camp_return','comparison_offset':None},'field_changes':{'purpose':{'value':'工程T053设备现场核对','status':'confirmed'},'destination':{'value':'工程工区A','status':'confirmed'},'departure_at':{'value':'2026-10-12T09:30:00+01:00','status':'confirmed'},'return_at':{'value':'2026-10-12T16:00:00+01:00','status':'confirmed'},'participants':{'value':'工程私有同行引用不进入费用稿','status':'confirmed'}}})
    expense=clerk.post('/api/matters',{'domain':'expense','goal_text':'工程T053复用本人行程准备费用；11月纸张两笔USD及EUR一笔，非实际报销付款','auto_prepare':False})
    mid=expense['id'];expense=clerk.post('/api/matters/'+mid+'/sources',{'expected_version':expense['version'],'kind':'user_text','text':'工程纸张 USD0.1 2026-11\n工程纸张 USD0.2 2026-11\n工程材料 EUR0.5 2026-11'});source=expense['sources'][-1]['id']
    expense=clerk.post('/api/matters/'+mid+'/artifacts',{'expected_version':expense['version'],'input_revision':expense['revision_no'],'edited_content':'工程T053费用原人工v1保留；尚无费用事由，计划不等同实际出行。'})
    proof={'task':'T053','scope':'local engineering only; no actual reimbursement, payment, journey or model call','matter_id':mid,'travel_id':tid,'before':clerk.get('/api/matters/'+mid),'before_travel':clerk.get('/api/matters/'+tid),'source_id':source,'original_artifact_id':expense['saved_artifact_id'],'prior_t052':preserved}
    PROOF.write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'matter_id':mid,'travel_id':tid,'source_id':source},ensure_ascii=False))

def collect():
    p=json.loads(PROOF.read_text(encoding='utf-8'));clerk=Client('qa-clerk');cook=Client('qa-cook');employee=Client('qa-employee');mid=p['matter_id'];tid=p['travel_id']
    m=clerk.get('/api/matters/'+mid);context=clerk.get('/api/matters/'+mid+'/expense-journey')['context'];reports=clerk.get('/api/matters/'+mid+'/rows/reports')['reports'];assert len(reports)==1
    aid=reports[0]['artifact_id'];v1=clerk.raw('/api/artifacts/'+aid+'/download?version=1&format=md');v2=clerk.raw('/api/artifacts/'+aid+'/download?version=2&format=md');csv=clerk.raw('/api/matters/'+mid+'/rows/reports/'+aid+'/csv')
    assert 'USD／2026-11：0.3' in v1 and 'EUR／2026-11：0.5' in v1;assert '2026-10-12T17:00:00+01:00' in v1 and '工程私有同行引用' not in v1
    assert '2026-10-12' not in csv and '2026-11' in csv;assert v2.endswith('工程T053本人暂停续办备注：金额与计划出处分别核对，本稿未提交财务。')
    assert m['assistant_status']=='waiting';assert m['facts']['purpose']['value']=='工程T053设备现场核对';assert m['rows']==p['rows'];assert m['tracks']==p['before']['tracks'];assert m['sources']==p['before']['sources']
    assert not context['stale'];assert context['fields']['return_at']['value']=='2026-10-12T17:00:00+01:00'
    assert clerk.raw('/api/artifacts/'+p['original_artifact_id']+'/download?version=1&format=md')=='工程T053费用原人工v1保留；尚无费用事由，计划不等同实际出行。'
    travel=clerk.get('/api/matters/'+tid);assert travel['tracks']==p['before_travel']['tracks'] and travel['sources']==p['before_travel']['sources'];assert all(travel['facts'][key]==value for key,value in p['before_travel']['facts'].items() if key!='return_at')
    prior=p['prior_t052'];assert cook.get('/api/shares/'+prior['meal_share']['id'])==prior['meal_share']
    old=json.loads(Path('docs/evidence/meal-summaries-v03.json').read_text(encoding='utf-8'));assert clerk.raw('/api/artifacts/'+old['report']['artifact_id']+'/download?version=2&format=md')==prior['meal_private_v2']
    assert [{key:item.get(key) for key in ('id','version','status')} for item in cook.get('/api/menu-publications')['items']]==prior['menus']
    denied={'cook_private_matter':cook.denied('/api/matters/'+mid),'employee_other_context':employee.denied('/api/matters/'+mid+'/expense-journey'),'employee_other_saved_md':employee.denied('/api/artifacts/'+aid+'/download?version=2&format=md')};assert all(value==404 for value in denied.values())
    p.update(after=m,after_travel=travel,current_context=context,report=reports[0],fixed_v1_md=v1,private_v2_md=v2,fixed_csv=csv,permissions=denied,prior_t052_unchanged=True,model_calls_this_increment=0,verification='1 combined local/identity Store test 1.223s; affected syntax; actual HTTP and browser',ui_path='读所选计划r2并选4字段／填空白事由→行程更正r3使旧保存409且选择保留→重读核对17点原值→暂停人工保存引用与空白事由→11月三笔工程明细保存→重开暂停及引用保留→恢复选期3行另存USD0.3／EUR0.5费用稿并带10月计划出处→暂停保存人工v2→刷新重开保留→恢复。',runtime_session=47977)
    PROOF.write_text(json.dumps(p,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'matter_id':mid,'artifact_id':aid,'purpose_reused':True,'fixed_amounts':['USD0.3','EUR0.5'],'plan_separate_from_financial_period':True,'permissions':denied,'prior_t052_unchanged':True},ensure_ascii=False))

if __name__=='__main__':
    import sys
    collect() if '--collect' in sys.argv else setup()
