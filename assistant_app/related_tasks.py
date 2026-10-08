"""Model-discovered private work suggestions; explicit human creation, no business action."""
import json
from .meeting_candidates import normalize_refs
from .preparation import DOMAINS, initial_facts, prepare
from .store import AppError, dump, now, string, uid
from .structured import StructuredService, _strict

INSTRUCTIONS = '''
主动理解原话还涉及哪些独立事项，不根据当前类别套固定关联。出行和用餐分别办理；只有原话或明确需求能支持时才建议核对用餐，不能认定必须减餐。也可发现会议、公文、费用、物资等关系，不凑建议。
除前述键外另返回related_tasks数组，最多4条，无依据为空；最终JSON键集合以本条附加要求为准，原业务专用数组继续保留。
每条只含domain,goal_text,reason,refs。domain从travel,dining,menu,feedback,repair,cleaning,utilities,document,meeting,expense,inventory,leave,hr选择，须不同于当前domain。dining为独立用餐需求，menu为厨师菜单管理。
goal_text为最多1200字的可修正准备目标，reason为最多600字的关联理由和待核项。不补人员、日期、数量或批准，不把建议写成实际执行；不能要求必建事项。
refs为1至3处已发送sources原话引用，每处仅source_index,start_line,end_line,quote；实际换行从1计，quote逐字等于起止行原文。只能用本事项所发原话，不能引用其他事项、生成稿或猜测记录。
content简短；所有关联只是候选，用户可忽略或修正，经本人明确选择后才保存独立事项。'''

def normalize_related(items, detail):
    if not isinstance(items, list) or len(items)>4: raise ValueError('invalid related tasks')
    result=[];seen=set()
    for item in items:
        if not isinstance(item,dict) or set(item)!={'domain','goal_text','reason','refs'}:raise ValueError('invalid related task shape')
        domain=item['domain']
        if not isinstance(domain,str) or domain not in DOMAINS or domain in {'general',detail['domain']}:raise ValueError('invalid related domain')
        for field,limit in (('goal_text',1200),('reason',600)):
            value=item[field]
            if not isinstance(value,str) or not value.strip() or len(value)>limit:raise ValueError('invalid related text')
        if not isinstance(item['refs'],list) or not 1<=len(item['refs'])<=3:raise ValueError('invalid references')
        refs=normalize_refs(item['refs'],detail);key=(domain,item['goal_text'])
        if key in seen:raise ValueError('duplicate related task')
        seen.add(key);result.append({**item,'refs':refs,'confirmation':'candidate'})
    return result

def origin_current(c, matter, facts, depth=0):
    origin=facts.get('__related_origin')
    if not origin:return True
    if depth>=8:return False
    parent=c.execute('SELECT * FROM matters WHERE id=? AND owner=?',(origin['matter_id'],matter['owner'])).fetchone()
    if not parent or parent['revision_no']!=origin['input_revision']:return False
    row=c.execute('SELECT fields FROM revisions WHERE matter_id=? AND revision_no=?',(parent['id'],parent['revision_no'])).fetchone()
    if not row:return False
    stored=json.loads(row[0]);states=stored.get('__source_states',{}).get('items',{})
    return not any(v.get('state')=='withheld' for v in states.values()) and all(states.get(ref['source_id'],{}).get('state','usable')=='usable' for ref in origin['refs']) and origin_current(c,parent,stored,depth+1)

class RelatedTasksService(StructuredService):
    def _owned(self,c,mid):
        if self.store.requires_auth and not set(self.store.principal()['capabilities']) & {'employee','clerk','cook'}:raise AppError('forbidden','当前职责无私人事项权限。',403)
        matter=self.store._matter(c,mid)
        return self._owner(c,mid,matter['domain'])

    def _batch(self,c,matter):
        for row in c.execute("SELECT * FROM actions WHERE matter_id=? AND kind='prepare_model' AND status='completed' ORDER BY rowid DESC LIMIT 20",(matter['id'],)):
            response=json.loads(row['response']) if row['response'] else {}
            if 'related_tasks' in response:return dict(row),response
        return None,None

    def read(self,mid):
        with self.store.connect() as c:
            _,matter,facts=self._owned(c,mid);action,batch=self._batch(c,matter)
            origin=facts.get('__related_origin');linked=[]
            for row in c.execute("SELECT response FROM actions WHERE matter_id=? AND kind='request:structured_related_task_create' AND status='completed' ORDER BY rowid",(mid,)):
                item=json.loads(row[0]);linked.append({key:item[key] for key in ('candidate_id','action_id','matter_id','goal_text','domain')})
            stale=False
            if batch:
                stale=batch['input_revision']!=matter['revision_no'] or not origin_current(c,matter,facts)
                try:self._ensure_preparation_sources(c,mid)
                except AppError as error:
                    if error.status!=409:raise
                    stale=True
            items=[{**item,'refs':self._refs(c,mid,item['refs'])} for item in batch['related_tasks']] if batch else []
            from .private_context import PrivateContextService
            return {'expected_version':matter['version'],'input_revision':matter['revision_no'],'action_id':action['id'] if action else None,'items':items,'stale':stale,'linked':linked,'origin':{**origin,'stale':not origin_current(c,matter,facts)} if origin else None,'matches':PrivateContextService(self.store).view(c,matter)}

    def create(self,mid,data,key):
        _strict(data,{'expected_version','input_revision','action_id','candidate_id','goal_text'})
        goal=string(data['goal_text'],'本人核对后的独立目标',2000)
        with self.store.transaction() as c:
            actor,matter,facts=self._owned(c,mid);key,fingerprint,previous=self._request(c,actor,'related_task_create',mid,data,key)
            if previous is not None:
                self.store._matter(c,previous['matter_id']);return previous
            self.store._check_version(matter,data)
            action,batch=self._batch(c,matter)
            if type(data['input_revision']) is not int or data['input_revision']!=matter['revision_no'] or not action or data['action_id']!=action['id'] or batch['input_revision']!=matter['revision_no']:
                raise AppError('basis_changed','原话或模型依据已变化，保留编辑并重新核对建议。',409)
            self._ensure_preparation_sources(c,mid)
            item=next((i for i in batch['related_tasks'] if i['id']==data['candidate_id']),None)
            if not item:raise AppError('not_found','当前关联候选不存在。',404)
            for row in c.execute("SELECT response FROM actions WHERE matter_id=? AND kind='request:structured_related_task_create' AND status='completed'",(mid,)):
                prior=json.loads(row[0])
                if prior['candidate_id']==item['id'] and prior['action_id']==action['id']:
                    self.store._matter(c,prior['matter_id']);return prior
            refs=self._refs(c,mid,item['refs'],require_usable=True);child=uid()
            c.execute('INSERT INTO matters VALUES(?,?,?,?,?,?,?,?,?,?)',(child,actor['id'],goal,item['domain'],1,1,0,'waiting','needs_review',now()))
            initial=initial_facts(item['domain']);initial['__workflow']={'enabled':False,'mode':'template'}
            initial['__related_origin']={'matter_id':mid,'input_revision':matter['revision_no'],'action_id':action['id'],'candidate_id':item['id'],'reason':item['reason'],'refs':item['refs'],'saved_by':actor['id']}
            c.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(child,1,dump(initial),now(),actor['id']))
            self.store._source(c,child,'user_text',goal,None)
            self.store._source(c,child,'user_text','\n'.join(ref['excerpt'] for ref in refs),None)
            content,_=prepare(self.store._detail(c,child));aid=uid()
            c.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)',(aid,1,child,'preparation',DOMAINS[item['domain']]['label']+'准备稿',content,'template_draft',1,now()))
            self.store._event(c,child,'related_created','本人核对并另存独立事项；事实仍待核，未调用模型或改真实安排。')
            self.store._event(c,mid,'related_created','本人明确另存关联候选为独立事项，原稿和业务状态保留。')
            self.store._bump(c,mid)
            response={'matter_id':child,'goal_text':goal,'domain':item['domain'],'candidate_id':item['id'],'action_id':action['id']}
            return self._remember(c,key,fingerprint,'related_task_create',mid,response)

    def unlink(self,mid,data,key):
        _strict(data,{'expected_version','input_revision','acknowledged_copy_review'})
        if data['acknowledged_copy_review'] is not True:raise AppError('review_required','须本人明确核对当前副本再解除关联。')
        with self.store.transaction() as c:
            actor,matter,facts=self._owned(c,mid);key,fingerprint,previous=self._request(c,actor,'related_task_unlink',mid,data,key)
            if previous is not None:return previous
            self.store._check_version(matter,data)
            if type(data['input_revision']) is not int or data['input_revision']!=matter['revision_no']:raise AppError('basis_changed','事项已变化，原编辑保留，先读回核对。',409)
            if not facts.get('__related_origin'):raise AppError('not_linked','当前没有此类关联。')
            facts.pop('__related_origin');revision=matter['revision_no']+1
            c.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(mid,revision,dump(facts),now(),actor['id']))
            self.store._cancel(c,mid,'本人核对副本并解除当前关联；旧稿和来源保留')
            self.store._bump(c,mid,revision_no=revision,control_epoch=matter['control_epoch']+1,assistant_status=matter['assistant_status'] if matter['assistant_status'] in {'paused','handoff'} else 'waiting',preparation_status='needs_review')
            self.store._event(c,mid,'related_unlinked','本人明确核对当前副本后独立继续；原关联修订、原话及人工稿历史保留，不改真实用餐或其他业务结果。')
            return self._remember(c,key,fingerprint,'related_task_unlink',mid,{'matter_id':mid,'input_revision':revision})
