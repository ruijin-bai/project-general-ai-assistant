"""Explicit per-matter Jiaorong title context, never other owners or source bodies."""
import json
from .meeting_candidates import normalize_refs
from .store import AppError,dump,now,uid
from .structured import StructuredService,_strict,_nullable

INSTRUCTIONS='''
本次可能附有other_tasks：本人已明确允许发送的最多10份其他私有事项目标标题（无资料正文、事实、账号或其他人事项）。title_index为本次有限序号，不是授权执行。
另返回related_matches数组，最多4项，无可确认候选为空。每项仅含target_index,target_quote,reason,refs。target_index须对应other_tasks.title_index，target_quote逐字等于该目标标题全文，reason最多600字，说明关联依据及不确定处；refs为1至3处当前sources的原话引用（source_index,start_line,end_line,quote）。不引用其他事项未提供的材料。
优先指出可继续办理的已有事项，避免为同一需求另建；标题相似不等于同一事件，不猜人员、日期对应或批准执行。不得按类别固定配对。不确定就说明待核；没有other_tasks则related_matches必须为空。保持原业务数组与related_tasks，最终JSON另加本related_matches键。'''

def usable(c,store,matter):
    from .related_tasks import origin_current
    facts=store._stored_facts(c,matter)
    return origin_current(c,matter,facts) and not any(v.get('state')=='withheld' for v in facts.get('__source_states',{}).get('items',{}).values())

def title_snapshot(c,store,matter,force=False):
    facts=store._stored_facts(c,matter)
    if not force and not facts.get('__private_title_context',{}).get('enabled',False):return None
    items=[]
    for row in c.execute('SELECT * FROM matters WHERE owner=? AND id!=? ORDER BY updated_at DESC,rowid DESC LIMIT 50',(matter['owner'],matter['id'])):
        if len(row['goal_text'])<=1000 and usable(c,store,row):
            items.append({'title_index':len(items)+1,'matter_id':row['id'],'domain':row['domain'],'goal_text':row['goal_text'],'input_revision':row['revision_no']})
            if len(items)==10:break
    return {'items':items,'limit':10,'scope':'本人其他事项的目标标题；无事实、来源正文或其他人内容'}

def snapshot_current(c,store,matter,snapshot):
    if snapshot is None:return True
    if not store._stored_facts(c,matter).get('__private_title_context',{}).get('enabled',False):return False
    for item in snapshot['items']:
        row=c.execute('SELECT * FROM matters WHERE id=? AND owner=?',(item['matter_id'],matter['owner'])).fetchone()
        if not row or row['revision_no']!=item['input_revision'] or row['goal_text']!=item['goal_text'] or not usable(c,store,row):return False
    return True

def normalize_matches(items,detail):
    if not isinstance(items,list) or len(items)>4:raise ValueError('invalid related matches')
    context=detail.get('_related_context') or {'items':[]};result=[];seen=set()
    for item in items:
        if not isinstance(item,dict) or set(item)!={'target_index','target_quote','reason','refs'}:raise ValueError('invalid related match shape')
        index=item['target_index'];target=next((t for t in context['items'] if type(index) is int and t['title_index']==index),None)
        if not target or index in seen or item['target_quote']!=target['goal_text']:raise ValueError('invalid related target')
        if not isinstance(item['reason'],str) or not item['reason'].strip() or len(item['reason'])>600:raise ValueError('invalid related reason')
        if not isinstance(item['refs'],list) or not 1<=len(item['refs'])<=3:raise ValueError('invalid references')
        seen.add(index);result.append({'target':target,'reason':item['reason'],'refs':normalize_refs(item['refs'],detail),'confirmation':'candidate'})
    return result

class PrivateContextService(StructuredService):
    def _owned(self,c,mid):
        if self.store.requires_auth and not set(self.store.principal()['capabilities']) & {'employee','clerk','cook'}:raise AppError('forbidden','当前职责无私人事项权限。',403)
        matter=self.store._matter(c,mid);return self._owner(c,mid,matter['domain'])

    def preview(self,mid):
        with self.store.connect() as c:
            _,matter,facts=self._owned(c,mid)
            return {'expected_version':matter['version'],'input_revision':matter['revision_no'],'enabled':facts.get('__private_title_context',{}).get('enabled',False),**title_snapshot(c,self.store,matter,force=True)}

    def save_scope(self,mid,data,key):
        _strict(data,{'expected_version','input_revision','enabled'})
        if type(data['enabled']) is not bool:raise AppError('invalid_input','是否允许须为明确布尔值。')
        with self.store.transaction() as c:
            actor,matter,facts=self._owned(c,mid);key,fingerprint,previous=self._request(c,actor,'private_context_scope',mid,data,key)
            if previous is not None:return previous
            self.store._check_version(matter,data)
            if type(data['input_revision']) is not int or data['input_revision']!=matter['revision_no']:raise AppError('basis_changed','当前事项已换版，保留编辑并读回核对。',409)
            facts['__private_title_context']={'enabled':data['enabled'],'saved_by':actor['id'],'saved_at':now()};revision=matter['revision_no']+1
            c.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(mid,revision,dump(facts),now(),actor['id']))
            self.store._cancel(c,mid,'本人修改标题模型引用许可，旧准备停止')
            self.store._bump(c,mid,revision_no=revision,control_epoch=matter['control_epoch']+1,assistant_status=matter['assistant_status'] if matter['assistant_status'] in {'paused','handoff'} else 'waiting',preparation_status='needs_review')
            self.store._event(c,mid,'private_context_scope','本人'+('允许' if data['enabled'] else '停止')+'后续明确交融整理参考最多10份本人其他事项目标标题；不发送其他事项事实、资料正文或其他人内容。已发送内容无法召回。')
            return self._remember(c,key,fingerprint,'private_context_scope',mid,{'matter_id':mid,'input_revision':revision,'enabled':data['enabled']})

    def _batch(self,c,matter):
        for row in c.execute("SELECT * FROM actions WHERE matter_id=? AND kind='prepare_model' AND status='completed' ORDER BY rowid DESC LIMIT 20",(matter['id'],)):
            batch=json.loads(row['response']) if row['response'] else {}
            if 'related_matches' in batch:return dict(row),batch
        return None,None

    def view(self,c,matter):
        action,batch=self._batch(c,matter);linked=[];withdrawn=set()
        for row in c.execute("SELECT response FROM actions WHERE matter_id=? AND kind='request:structured_existing_match_withdraw' AND status='completed'",(matter['id'],)):withdrawn.add(json.loads(row[0])['link_id'])
        for row in c.execute("SELECT response FROM actions WHERE matter_id=? AND kind='request:structured_existing_match_save' AND status='completed' ORDER BY rowid",(matter['id'],)):
            item=json.loads(row[0]);target=c.execute('SELECT * FROM matters WHERE id=? AND owner=?',(item['target']['matter_id'],matter['owner'])).fetchone()
            linked.append({**item,'withdrawn':item['link_id'] in withdrawn,'stale':item['input_revision']!=matter['revision_no'] or not target or target['revision_no']!=item['target']['input_revision'] or not usable(c,self.store,target)})
        stale=bool(batch and (batch['input_revision']!=matter['revision_no'] or not snapshot_current(c,self.store,matter,batch.get('related_context'))))
        if batch:
            try:self._ensure_preparation_sources(c,matter['id'])
            except AppError as error:
                if error.status!=409:raise
                stale=True
        items=[{**i,'refs':self._refs(c,matter['id'],i['refs'])} for i in batch['related_matches']] if batch else []
        return {'action_id':action['id'] if action else None,'items':items,'stale':stale,'linked':linked}

    def save_match(self,mid,data,key):
        _strict(data,{'expected_version','input_revision','action_id','match_id','note'})
        note=_nullable(data['note'],'本人关联核对备注',2000)
        with self.store.transaction() as c:
            actor,matter,_=self._owned(c,mid);key,fingerprint,previous=self._request(c,actor,'existing_match_save',mid,data,key)
            if previous is not None:return previous
            self.store._check_version(matter,data);action,batch=self._batch(c,matter)
            if type(data['input_revision']) is not int or data['input_revision']!=matter['revision_no'] or not action or data['action_id']!=action['id'] or batch['input_revision']!=matter['revision_no'] or not snapshot_current(c,self.store,matter,batch.get('related_context')):raise AppError('basis_changed','当前或被参考事项已变化，保留核对备注，请重新核对。',409)
            self._ensure_preparation_sources(c,mid);item=next((i for i in batch['related_matches'] if i['id']==data['match_id']),None)
            if not item:raise AppError('not_found','本批已有事项候选不存在。',404)
            view=self.view(c,matter)
            prior=next((i for i in view['linked'] if i['match_id']==item['id'] and i['action_id']==action['id'] and not i['withdrawn']),None)
            if prior:return {k:v for k,v in prior.items() if k not in {'stale','withdrawn'}}
            response={'link_id':uid(),'action_id':action['id'],'match_id':item['id'],'input_revision':matter['revision_no'],'target':item['target'],'reason':item['reason'],'refs':item['refs'],'note':note,'saved_by':actor['id']}
            self.store._event(c,mid,'existing_match_saved','本人核对并保存已有事项关联；没有新建事项、复制事实或改变对方状态，关联理由与备注留作历史。')
            self.store._bump(c,mid)
            return self._remember(c,key,fingerprint,'existing_match_save',mid,response)

    def withdraw(self,mid,data,key):
        _strict(data,{'expected_version','link_id'})
        with self.store.transaction() as c:
            actor,matter,_=self._owned(c,mid);key,fingerprint,previous=self._request(c,actor,'existing_match_withdraw',mid,data,key)
            if previous is not None:return previous
            self.store._check_version(matter,data)
            if not any(i['link_id']==data['link_id'] for i in self.view(c,matter)['linked']):raise AppError('not_found','本事项已存关联不存在。',404)
            self.store._event(c,mid,'existing_match_withdrawn','本人解除当前已有事项关联；原核对备注、来源、两份事项与人工稿历史保留。')
            self.store._bump(c,mid)
            return self._remember(c,key,fingerprint,'existing_match_withdraw',mid,{'link_id':data['link_id']})
