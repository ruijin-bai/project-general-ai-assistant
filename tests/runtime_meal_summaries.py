"""Local HTTP engineering fixture/evidence; no model, real business or credentials output."""
import http.cookiejar
import json
from pathlib import Path
import urllib.request
import urllib.error
import uuid

BASE='http://127.0.0.1:8767'
PROOF=Path('docs/evidence/meal-summaries-v03.json')
class Client:
    def __init__(self,account):
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf=self.get('/api/session')['csrf_token']
        self.csrf=self.post('/api/auth/login',{'username':account,'password':'EngineeringOnly-2026!'})['csrf_token']
    def get(self,path):
        with self.opener.open(BASE+path,timeout=20) as response:return json.load(response)
    def post(self,path,data):
        req=urllib.request.Request(BASE+path,json.dumps(data,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':self.csrf,'Idempotency-Key':str(uuid.uuid4())},method='POST')
        with self.opener.open(req,timeout=20) as response:return json.load(response)
    def raw(self,path):
        with self.opener.open(BASE+path,timeout=20) as response:return response.read().decode()
    def denied(self,path):
        try:self.get(path)
        except urllib.error.HTTPError as error:return error.code
        raise AssertionError('unexpected read')

def setup():
    clerk=Client('qa-clerk');cook=Client('qa-cook')
    proof={'kind':'local engineering fixture only; no actual travel, deduction, cooking or model call', 'prior_menu_states':[{key:item.get(key) for key in ('id','version','status')} for item in cook.get('/api/menu-publications')['items']], 'setup':[]}
    for n,domain in ((1,'travel'),(2,'leave')):
        m=clerk.post('/api/matters',{'domain':domain,'goal_text':f'工程T052餐次私有行程{n}（非实际出行；私有备注不进入厨师）','auto_prepare':False})
        m=clerk.post('/api/matters/'+m['id']+'/artifacts',{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':f'工程T052原人工稿{n}必须保留，非实际业务。'})
        old=m['saved_artifact_id'];plan=[('工程甲引用','keep','2026-10-09'),('工程乙引用','keep' if n==1 else 'request_reduce','2026-10-09'),('工程丙引用' if n==1 else '工程丁引用','pending' if n==1 else 'request_reduce','2026-10-09')]
        if n==1:plan.append(('工程另日引用','keep','2026-10-10'))
        rows=[{'id':str(uuid.uuid4()),'person_ref':person,'meal_date':date,'meal_slot':'工程午餐','request':request,'note':'工程私人保餐备注，不得自动进入厨师副本。'} for person,request,date in plan]
        j=clerk.post('/api/matters/'+m['id']+'/journey/meal-requests',{'expected_version':m['version'],'input_revision':m['revision_no'],'upserts':rows,'removed_ids':[]})
        proof['setup'].append({'id':m['id'],'domain':domain,'original_artifact_id':old,'before':clerk.get('/api/matters/'+m['id']),'journey':j})
    proof['root_id']=proof['setup'][0]['id'];proof['selected_ids']=[item['id'] for item in proof['setup']]
    proof['inputs']=clerk.get('/api/matters/'+proof['root_id']+'/meal-summaries')
    PROOF.write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'root_id':proof['root_id'],'selected_ids':proof['selected_ids'],'cooks':proof['inputs']['cooks']},ensure_ascii=False))

def collect():
    proof=json.loads(PROOF.read_text(encoding='utf-8'));clerk=Client('qa-clerk');cook=Client('qa-cook');employee=Client('qa-employee');mid=proof['root_id']
    reports=clerk.get('/api/matters/'+mid+'/meal-summaries')['reports'];assert len(reports)==1
    report=reports[0];aid=report['artifact_id'];received=next(item for item in cook.get('/api/shares')['items'] if item['title']==report['title']);grant=received['id']
    received=cook.get('/api/shares/'+grant);md=cook.raw('/api/shares/'+grant+'/download?version=1&format=md')
    assert received['content']==md==report['cook_content']
    private=clerk.raw('/api/artifacts/'+aid+'/download?version=2&format=md');assert private.endswith('工程T052本人暂停时的私有核对备注，不进入厨师固定副本。')
    assert all(word not in md for word in ('私有核对备注','工程甲引用','工程乙引用','工程丙引用','工程丁引用',mid,proof['selected_ids'][1],'工程私人保餐备注'))
    checks={'cook_private_matter':cook.denied('/api/matters/'+mid),'cook_private_summary':cook.denied('/api/matters/'+mid+'/meal-summaries'),'employee_other_grant':employee.denied('/api/shares/'+grant)}
    assert all(status==404 for status in checks.values())
    after=[]
    for original in proof['setup']:
        fresh=clerk.get('/api/matters/'+original['id']);assert all(fresh[key]==original['before'][key] for key in ('facts','sources','tracks','revision_no'))
        old=clerk.raw('/api/artifacts/'+original['original_artifact_id']+'/download?version=1&format=md');assert old.startswith('工程T052原人工稿')
        assert fresh['assistant_status']=='waiting';after.append(fresh)
    menu_states=[{key:item.get(key) for key in ('id','version','status')} for item in cook.get('/api/menu-publications')['items']];assert menu_states==proof['prior_menu_states']
    proof.update(report=report,received_share=received,downloaded_fixed_md=md,private_artifact_v2=private,after=after,permissions=checks,menus_unchanged=True,model_calls_this_increment=0,ui_path='经办选2份／同日核对1保餐1调整1待核1冲突4引用2额外记录→另存原稿保留→暂停人工v2保存→刷新重开→恢复→明确固定副本给厨师→厨师只读；UI现场未调用模型。',verification='1 combined Store test (both local and identity), final 0.559s; affected Python/JS syntax; actual HTTP and browser',runtime_session=31563)
    PROOF.write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'root_id':mid,'artifact_id':aid,'share_id':grant,'counts':report['groups'],'private_preserved':True,'permissions':checks,'menus_unchanged':True},ensure_ascii=False))

if __name__=='__main__':
    import sys
    collect() if '--collect' in sys.argv else setup()
