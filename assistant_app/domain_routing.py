"""Initial preparation classification, never business approval or execution."""
from .preparation import DOMAINS,initial_facts
from .meeting_candidates import normalize_refs
from .store import AppError,dump,now

INSTRUCTIONS='''
本目标选择由助理判断办理类别。另返回preparation_domain对象，仅含domain,reason,refs：domain为preparation_categories中的一个类别；reason最多600字说明主要目标，refs为1至3处当前sources的逐字原话引用（source_index,start_line,end_line,quote）。无明确主要目标或多个目标无法判断时domain为general，refs可为空，继续提供有用准备并只问关键缺项。
类别只是办理范围，不表示已申请、获批或执行。出行、用餐分别理解；related_tasks只提其他独立需求，不重复主要类别，不为了分类要求用户多填表。
正文和补问使用日常名称、具体事项标题，不展示JSON字段名或other_tasks/title_index等技术名。'''

def enabled(detail):
    return detail.get('domain')=='general' and detail.get('domain_routing',{}).get('mode')=='auto' and detail.get('domain_routing',{}).get('state')=='pending'

def normalize(value,detail):
    if not isinstance(value,dict) or set(value)!={'domain','reason','refs'} or not isinstance(value['domain'],str) or value['domain'] not in DOMAINS:
        raise ValueError('invalid preparation domain')
    if not isinstance(value['reason'],str) or not value['reason'].strip() or len(value['reason'])>600:
        raise ValueError('invalid preparation domain')
    refs=value['refs']
    if not isinstance(refs,list) or not (0 if value['domain']=='general' else 1)<=len(refs)<=3:
        raise ValueError('invalid references')
    return {'domain':value['domain'],'reason':value['reason'],'refs':normalize_refs(refs,detail) if refs else []}

def switchable(c,matter,facts):
    allowed={'__workflow','__domain_routing','__source_states','__source_files','__attachment_text','__goal_revision','__private_title_context'}
    if any(key.startswith('__') and key not in allowed for key in facts):return False
    for key in DOMAINS[matter['domain']]['fields']:
        item=facts.get(key,{})
        if item.get('value') is not None and (matter['domain']!='general' or item.get('status')=='confirmed'):return False
    if c.execute("SELECT 1 FROM events WHERE matter_id=? AND event_type='business_evidence' LIMIT 1",(matter['id'],)).fetchone():return False
    safe=('request:create','request:artifact','request:manual_artifact','request:goal','request:facts','request:source','request:message','request:control','request:prepare','request:workflow','request:category','request:structured_private_context_scope','request:structured_existing_match_save','request:structured_existing_match_withdraw')
    return not c.execute("SELECT 1 FROM actions WHERE matter_id=? AND status='completed' AND kind LIKE 'request:%' AND kind NOT IN ("+','.join('?' for _ in safe)+") LIMIT 1",(matter['id'],*safe)).fetchone()

def apply_initial(c,store,matter,facts,proposal,action_id):
    metadata=facts.get('__domain_routing',{})
    if matter['domain']!='general' or metadata.get('mode')!='auto' or metadata.get('state')!='pending' or not proposal:return False
    metadata={**metadata,**proposal,'action_id':action_id,'state':'pending'}
    target=proposal['domain']
    if target!='general' and switchable(c,matter,facts):
        for key,value in initial_facts(target).items():facts.setdefault(key,value)
        c.execute('UPDATE matters SET domain=? WHERE id=?',(target,matter['id']))
        metadata['state']='classified'
        store._event(c,matter['id'],'domain_routed','助理按目标原话进入'+DOMAINS[target]['label']+'准备范围；可调整，原话与人工稿保留，未批准或执行。')
    elif target!='general':metadata['state']='needs_review'
    facts['__domain_routing']=metadata
    return True

def correct(c,store,matter,data):
    if set(data)!={'expected_version','input_revision','domain'} or not isinstance(data['domain'],str) or data['domain'] not in DOMAINS:
        raise AppError('invalid_input','请选择本事项的办理类别。')
    if type(data['input_revision']) is not int or data['input_revision']!=matter['revision_no']:
        raise AppError('basis_changed','原话已变化，请保留选择并读回核对。',409)
    facts=store._stored_facts(c,matter)
    if '__domain_routing' not in facts:raise AppError('category_scope','既有事项保持原类别；可修正原目标或另存独立事项。',409)
    if data['domain']==matter['domain']:return
    if not switchable(c,matter,facts):raise AppError('category_in_use','已有业务字段或办理记录，当前类别与原记录保留；请修正目标或另存独立事项。',409)
    target=data['domain'];revision=matter['revision_no']+1
    for key,value in initial_facts(target).items():facts.setdefault(key,value)
    facts['__domain_routing']={**facts['__domain_routing'],'mode':'manual','state':'selected','domain':target,'previous_domain':matter['domain'],'selected_at':now()}
    c.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(matter['id'],revision,dump(facts),now(),store.actor_id()))
    c.execute('UPDATE matters SET domain=? WHERE id=?',(target,matter['id']))
    store._cancel(c,matter['id'],'本人调整办理类别，旧准备停止')
    store._bump(c,matter['id'],revision_no=revision,control_epoch=matter['control_epoch']+1,assistant_status=matter['assistant_status'] if matter['assistant_status'] in {'paused','handoff'} else 'waiting',preparation_status='needs_review')
    store._event(c,matter['id'],'domain_corrected','本人调整为'+DOMAINS[target]['label']+'准备范围；旧字段、原话和人工稿留存，不调用模型、不改变实际业务状态。')
