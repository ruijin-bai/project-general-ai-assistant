"""Explicit own journey reuse for expense preparation, with frozen field provenance."""
import json
from .journey import JourneyService
from .store import AppError, dump, now, string
from .structured import StructuredService, _strict

FIELDS={'purpose':'原行程用途','destination':'计划目的地','departure_at':'计划离营原值','return_at':'计划返营原值'}
SCOPE='仅复用本人明确选择、已人工确认的计划字段；不把计划当实际出返，不推申请人、财务期间、费用、票据、批准或付款。'


class ExpenseJourneyService(StructuredService):
    def _owned(self, connection, matter_id):
        if self.store.requires_auth and not set(self.store.principal()['capabilities']) & {'employee','clerk'}:
            raise AppError('forbidden','需已获员工或综合经办职责才能复用本人私有行程费用准备。',403)
        return self._owner(connection,matter_id,'expense')

    def _travel(self, connection, travel_id):
        if self.store.requires_auth and not set(self.store.principal()['capabilities']) & {'employee','clerk'}:
            raise AppError('forbidden','当前职责不能重新引用私人行程。',403)
        travel_id=string(travel_id,'本人行程ID',80)
        _,travel,facts=JourneyService(self.store)._owned(connection,travel_id)
        if travel['domain']!='travel':
            raise AppError('invalid_domain','明确选本人出行事项，不把休假材料或其他人的分享当行程授权。')
        self._ensure_preparation_sources(connection,travel_id)
        return travel,facts

    def _context(self, connection, matter, facts, require_current=False):
        saved=facts.get('__expense_journey')
        if not saved:
            return None
        result={**saved,'stale':False,'scope':SCOPE}
        try:
            travel,current=self._travel(connection,saved['travel_id'])
            result['stale']=travel['revision_no']!=saved['travel_revision'] or any(current.get(field,{}).get('status')!='confirmed' or current.get(field,{}).get('value')!=record['value'] for field,record in saved['fields'].items())
        except AppError as error:
            if error.status==401:raise
            result['stale']=True
        if require_current and result['stale']:
            raise AppError('journey_basis_changed','关联行程或来源已变化，请明确重读核对／解除引用后再准备；旧费用明细、稿件和历史引用保留。',409)
        return result

    def inputs(self, matter_id):
        with self.store.connect() as c:
            _,matter,facts=self._owned(c,matter_id)
            items=[dict(row) for row in c.execute("SELECT id,goal_text,revision_no FROM matters WHERE owner=? AND domain='travel' ORDER BY updated_at DESC LIMIT 40",(self.store.actor_id(),))]
            return {'expected_version':matter['version'],'input_revision':matter['revision_no'],'items':items,'context':self._context(c,matter,facts),'scope':SCOPE}

    def preview(self, matter_id, data):
        _strict(data,{'travel_id'})
        with self.store.connect() as c:
            _,matter,facts=self._owned(c,matter_id);self._ensure_preparation_sources(c,matter_id)
            travel,source=self._travel(c,data['travel_id'])
            return {'expected_version':matter['version'],'input_revision':matter['revision_no'],'travel_id':travel['id'],'travel_revision':travel['revision_no'],
                    'items':[{'field':field,'label':label,'value':source.get(field,{}).get('value'),'status':source.get(field,{}).get('status','unknown')} for field,label in FIELDS.items()],
                    'purpose_can_fill':not facts.get('purpose',{}).get('value'),'scope':SCOPE}

    def save(self, matter_id, data, key):
        _strict(data,{'expected_version','input_revision','travel_id','travel_revision','fields','fill_empty_purpose'})
        selected=data['fields']
        if not isinstance(selected,list) or not 1<=len(selected)<=4 or any(not isinstance(field,str) or field not in FIELDS for field in selected) or len(set(selected))!=len(selected) or type(data['fill_empty_purpose']) is not bool:
            raise AppError('invalid_input','明确选1至4个不同计划字段，填空白事由须明确布尔值。')
        if data['fill_empty_purpose'] and 'purpose' not in selected:
            raise AppError('invalid_input','须明确选原用途才可填空白费用事由。')
        with self.store.transaction() as c:
            actor,matter,facts=self._owned(c,matter_id)
            key,fingerprint,previous=self._request(c,actor,'expense_journey_save',matter_id,data,key)
            if previous is not None:return previous
            self.store._check_version(matter,data)
            if type(data['input_revision']) is not int or data['input_revision']!=matter['revision_no']:
                raise AppError('basis_changed','费用事实修订已变化，请重读，人工编辑保留。',409)
            self._ensure_preparation_sources(c,matter_id)
            travel,source=self._travel(c,data['travel_id'])
            if type(data['travel_revision']) is not int or travel['revision_no']!=data['travel_revision']:
                raise AppError('journey_basis_changed','所读行程已换版，请重读并核对，原选择和人工值保留。',409)
            chosen={field:{'label':FIELDS[field],'value':source.get(field,{}).get('value'),'source_status':'confirmed'} for field in selected}
            if any(source.get(field,{}).get('status')!='confirmed' or not isinstance(row['value'],str) or not row['value'].strip() for field,row in chosen.items()):
                raise AppError('journey_unconfirmed','所选计划须先在原行程人工确认；模型候选、缺值不能直接复用作已核值。',409)
            filled=False
            if data['fill_empty_purpose'] and not facts.get('purpose',{}).get('value'):
                facts['purpose']={'value':chosen['purpose']['value'],'status':'confirmed'};filled=True
            facts['__expense_journey']={'travel_id':travel['id'],'travel_revision':travel['revision_no'],'fields':chosen,'purpose_filled':filled,'saved_by':actor['id'],'saved_at':now()}
            result=self._commit(c,actor,matter,facts,'expense_journey_saved','本人明确复用所选计划引用；已有费用事由和金额保留，财务期间／实绩／批准付款未推定。')
            return self._remember(c,key,fingerprint,'expense_journey_save',matter_id,{**result,'context':self._context(c,self.store._matter(c,matter_id),facts),'purpose_filled':filled})

    def unlink(self, matter_id, data, key):
        _strict(data,{'expected_version','input_revision'})
        with self.store.transaction() as c:
            actor,matter,facts=self._owned(c,matter_id);key,fingerprint,previous=self._request(c,actor,'expense_journey_unlink',matter_id,data,key)
            if previous is not None:return previous
            self.store._check_version(matter,data)
            if type(data['input_revision']) is not int or data['input_revision']!=matter['revision_no']:
                raise AppError('basis_changed','费用事实已换版，先重读核对，原稿保留。',409)
            if not facts.get('__expense_journey'):raise AppError('not_linked','当前没有所选行程引用。')
            facts.pop('__expense_journey')
            result=self._commit(c,actor,matter,facts,'expense_journey_unlinked','本人解除当前计划引用；原修订与已复用的费用事由保留，不删除行程或改变费用／实际财务状态。')
            return self._remember(c,key,fingerprint,'expense_journey_unlink',matter_id,result)

    def _commit(self, c, actor, matter, facts, event, message):
        revision=matter['revision_no']+1
        c.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(matter['id'],revision,dump(facts),now(),actor['id']))
        self.store._cancel(c,matter['id'],'本人修订行程费用准备引用，旧准备停止；人工稿保留')
        status=matter['assistant_status'] if matter['assistant_status'] in {'paused','handoff'} else 'waiting'
        self.store._bump(c,matter['id'],revision_no=revision,control_epoch=matter['control_epoch']+1,assistant_status=status,preparation_status='needs_review')
        self.store._event(c,matter['id'],event,message)
        self.store._continue(c,matter['id'])
        return {'expected_version':self.store._matter(c,matter['id'])['version'],'input_revision':revision}


def expense_context(connection, store, matter, facts, require_current=False):
    return ExpenseJourneyService(store)._context(connection,matter,facts,require_current) if matter['domain']=='expense' else None


def context_lines(context):
    if not context:return []
    return ['', '## 本人所选行程计划引用', SCOPE, '行程来源：'+context['travel_id']+'；所核修订 '+str(context['travel_revision']),
            *['- '+row['label']+'：'+row['value']+'（本人所选已核计划引用，非实际业务结果）' for row in context['fields'].values()]]


def report_references(connection, matter_id):
    return {record['artifact_id']:record['snapshot'].get('journey_context') for row in connection.execute("SELECT response FROM actions WHERE matter_id=? AND kind='request:structured_rows_report_render' AND status='completed'",(matter_id,)) for record in [json.loads(row['response'])] if record['snapshot'].get('journey_context')}


def reference_current(connection, owner_id, context):
    row=connection.execute('SELECT owner,revision_no FROM matters WHERE id=?',(context['travel_id'],)).fetchone()
    return bool(row and row['owner']==owner_id and row['revision_no']==context['travel_revision'])
