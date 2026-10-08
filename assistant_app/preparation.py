"""Business-specific preparation; no guessed authority or execution."""
from datetime import datetime

def spec(label, fields, required, tracks, checklist):
    return dict(label=label, fields=fields, required=required, tracks=tracks, checklist=checklist)

DOMAINS = {
    'general': spec('综合事项', {'subject':'事项主题','facts_text':'已知事实','requirements_ref':'要求来源'}, [], {}, ['保留目标原话，按实际要求准备下一步。']),
    'travel': spec('出行安排', dict(zip(('destination','departure_at','return_at','participants','purpose','meal_request'),('目的地','计划离营时间','计划返营时间','参与人员','用途','餐次需求'))), ['destination','departure_at','return_at','participants','purpose'], {'safety':'真实安全批准待核','vehicle':'真实车辆安排待核','movement':'实际出返未知','meal':'餐次口径与实际供餐待核'}, ['沿原渠道核安全审批及其适用人员、地点、时段和修订。','车辆安排、实际出返和餐次各自保留凭据；准备不保证有车、不扣餐。']),
    'repair': spec('报修', {'location':'地点','problem':'问题原述','reporter':'实际报修人','access_window':'可进入时段','contact':'必要联系方式','existing_record':'已有工单引用'}, ['location','problem'], {'assignment':'分派待核','acceptance':'承接未知','execution':'实际处理声明待核','requester_result':'报修人结果未知','inspection':'专业验收适用与结果待核','closure':'关闭口径待核'}, ['整理地点与问题原话，向原经办提供简短需求。','实际分派、承接、处理声明、报修人反馈和必要专业验收分别记录。','不凭“修好了”的转述自动关闭，仍有问题可继续补充。']),
    'cleaning': spec('清洁服务', {'location':'地点','scope':'清洁范围','requester':'需求人','access_window':'可进入时段','access_permission':'进入许可依据','request_kind':'临时需求或例行引用','existing_record':'现有安排引用'}, ['location','scope'], {'assignment':'安排待核','acceptance':'承接未知','execution':'处理声明待核','requester_result':'发起人反馈未知','closure':'服务结束口径待核'}, ['需求本身不构成进入私人房间的许可；进入条件由实际责任人核对。','临时需求独立于例行安排，不覆盖已有任务。','实际清洁与发起人反馈分开，未回复不视为满意。']),
    'dining': spec('用餐安排', {'meal_date':'用餐日期','meal_slot':'餐别原述','request_text':'用餐需求','person_ref':'必要人员引用','requirements_ref':'已知安排口径'}, ['request_text'], {'arrangement':'实际用餐安排待核','serving':'实际供餐未知'}, ['独立保存用餐需求；出行可能有关，但不能根据计划自动扣餐。','日期、餐别、人员及饭点口径不明列待核；自愿需求不当批准、备餐或实供。']),
    'menu': spec('菜单管理', {'menu_date':'用餐日期','meal_slot':'餐别','dishes':'菜品原述','cook':'实际厨师','dietary_constraints':'授权过敏或忌口','serving_scope':'发布范围','menu_ref':'原菜单或版本'}, ['menu_date','meal_slot','dishes'], {'publication':'厨师真实发布待核','preparation':'实际备餐未知','serving':'实际供餐未知'}, ['按明确日期、餐别和菜品整理菜单候选。','厨师决定并实际发布；草稿不等于发布、备餐或实供。','未知过敏不写“无”；人数、配料和库存不推算。']),
    'feedback': spec('用餐意见', {'feedback_text':'意见原话','menu_ref':'菜单或版本引用','menu_date':'用餐日期','meal_slot':'餐别','dish':'相关菜品','reported_by':'实际反馈人','dietary_constraint':'明确过敏或忌口'}, ['feedback_text'], {'recording':'原渠道意见提交待核','response':'厨师回应未知','adoption':'实际采用未知'}, ['保存自愿意见原话，不要求每天填写或全员反馈。','关联不清列未归类，保留少数意见、分歧和明确过敏。','无可靠身份与原意见ID，不统计独立人数、多数或全员比例。']),
    'leave': spec('休假与证照', {'person':'授权人员引用','leave_start':'休假起始日期','leave_end':'休假结束日期','departure_at':'计划离营','return_at':'计划返营','flight_info':'已知航班','passport_info':'护照必要字段或引用','visa_info':'签证必要字段或引用','domestic_proof':'国内真实证明引用','nis_entry_card':'真实入境卡引用','nis_exit_card':'真实离境卡引用','requirements_ref':'适用要求来源','travel_ref':'已有旅程引用'}, ['person','leave_start','leave_end'], {'leave_decision':'真实休假决定待核','passport':'护照材料与回执待核','visa':'签证材料与回执待核','domestic_proof':'国内真实证明待核','nis_entry':'入境卡适用及回执待核','nis_exit':'离境卡适用及回执待核','movement':'真实离返未知','meal':'餐次核对待核'}, ['逐项核真实证明、护照签证及适用入出境卡，缺失明确保留。','证明名称、时效、卡适用与办理窗口沿实际要求核对，不生成证明或通行结论。','接送与餐次引用已有旅程，不重复建行程事实。']),
    'document': spec('公文办理', {'document_kind':'文种或核对目标','audience':'接收对象','subject':'主题','facts_text':'事实底稿或原稿','template_ref':'模板引用','disclosure_scope':'允许披露范围','source_ref':'底稿引用'}, ['subject','facts_text'], {'issuance':'正式签发未知','submission':'原渠道提交未知','delivery':'送达接受未知','archive':'权威存档待核'}, ['无模板时仅形成通用草稿，保留事实底稿。','按原稿定位待核问题，不补造文号、引语、成绩、数字、署名或立场。','正式签发和外发由实际责任人处理。']),
    'meeting': spec('会议事务', {'meeting_subject':'会议主题','meeting_date':'会议日期','notes_text':'文字笔记或转写','participants':'记录中参与人','location':'地点','decisions_text':'记录中明确决定','actions_text':'记录中行动原述','source_ref':'原笔记引用'}, ['notes_text'], {'booking':'会议室确认未知','record_confirmation':'纪要核对待核','action_acceptance':'真实行动承接未知','action_result':'完成声明与结果待核'}, ['讨论、提议、决定和分歧分开；原记录无决定不补造。','行动责任人和期限来自原记录，缺失列待核，不凭纪要称已承接。','文字路径无需语音识别，会议室候选不保证可用。']),
    'expense': spec('费用报销', {'claimant':'申请人引用','purpose':'事由','period':'明确期间','expense_lines':'明细原文','currency':'币种','evidence_refs':'真实票据引用','requirements_ref':'财务要求来源','external_record':'原财务记录'}, ['purpose','expense_lines'], {'submission':'财务提交未知','review':'原渠道审核未知','payment':'实际支付未知'}, ['核明细、期间、币种、票据与真实财务要求，缺少材料列待补。','明细原文未结构核对，不自动合计；不同币种不直接合并。','材料齐备不等于合规、批准或支付。']),
    'inventory': spec('物资管理', {'inventory_goal':'采购或收发盘点目标','item_lines':'实际物品明细','period':'范围期间','location':'库位或使用范围','unit_basis':'单位口径','source_ref':'原台账或单据','external_record':'原记录标识'}, ['inventory_goal','item_lines'], {'request_decision':'采购发放决定未知','receipt':'真实收货未知','issue':'真实发放未知','reconciliation':'权威台账核对待核'}, ['保留原位置、期间、数量、单位与来源引用。','无已核库存基线不算余额、不判短缺，缺行不删除、缺数不当零。','保存单据或准备采购清单不记真实入出库。']),
    'hr': spec('人事事务', {'hr_goal':'本次招聘或人事核对目标','person_scope':'授权人员范围','materials_text':'实际授权材料','period':'期间','requirements_ref':'岗位合同考勤要求','source_ref':'现有权威记录','disclosure_scope':'披露范围'}, ['hr_goal','materials_text'], {'material_review':'实际材料核对待核','decision':'真实人事决定未知','signature':'真实签署未知','external_acceptance':'接收未知'}, ['仅整理本次授权材料和要求，未知要求列缺项。','不据疑点判资格、旷工、薪资、处罚、合同生效或录用。','个人证件只引用本次必要部分；国籍不推语言或能力。']),
    'utilities': spec('水电管理', {'location':'读数位置','period':'本次期间','meter_ref':'明确仪表引用','readings_text':'实际读数原文','unit_basis':'单位口径','source_ref':'真实原记录','requirements_ref':'适用核对要求'}, ['readings_text','meter_ref'], {'reading_verification':'真实读数及权威实绩待核','billing':'正式计费及支付未知'}, ['按真实原文位置逐项核仪表、单位、读数与明确时点；本地核对不代表传感器核真。','同仪表同单位且两端已核对才精确算相邻输入差；冲突或倒退只列疑点，不报负耗量。','表倍率、回零、损耗、费用及权威实绩口径未知，不推断真实耗量、计费或支付。']),
}
FIELDS = tuple(DOMAINS['travel']['fields'])
LABELS = DOMAINS['travel']['fields']
DATE_FIELDS = {'meal_date','departure_at','return_at','menu_date','meeting_date','leave_start','leave_end'}

def initial_facts(domain='travel'):
    return {field:{'value':None,'status':'unknown'} for field in DOMAINS[domain]['fields']}

def readiness(domain, facts):
    selected = DOMAINS[domain]
    missing = [selected['fields'][key] for key in selected['required'] if facts.get(key,{}).get('status') != 'confirmed']
    if domain == 'travel':
        departure = facts.get('departure_at',{}).get('value') if facts.get('departure_at',{}).get('status') == 'confirmed' else None
        returning = facts.get('return_at',{}).get('value') if facts.get('return_at',{}).get('status') == 'confirmed' else None
        if any(value and len(value) == 10 for value in (departure,returning)):
            missing.append('具体离返营时间（目前仅提供日期）')
        if departure and returning:
            start,end = datetime.fromisoformat(departure),datetime.fromisoformat(returning)
            if (start.tzinfo is None) != (end.tzinfo is None):
                missing.append('离返营时区口径（不能混用带时区与未注明时区的时间）')
    waiting = [{'reason':'补充或核对：'+'、'.join(missing),'who':'事项发起人'}] if missing else []
    if selected['tracks']:
        waiting.append({'reason':'、'.join(selected['tracks'].values()),'who':'实际责任人或原渠道待核'})
    steps = (['核对缺少的必要事实'] if missing else []) + ['查看并修改'+selected['label']+'准备稿','按实际要求沿原渠道办理，保留真实凭据']
    return missing,waiting,steps

def prepare(detail):
    selected = DOMAINS[detail['domain']]
    facts = detail['facts']
    missing,_,_ = readiness(detail['domain'],facts)
    lines = ['# '+selected['label']+'准备稿','','生成方式：规则模板（本次未调用模型）。','此稿仅用于人工准备；未批准、派遣、发布、扣餐、签发或执行。','','## 目标',detail['goal_text'],'','## 已记录事实']
    for key,label in selected['fields'].items():
        item = facts.get(key,{'value':None,'status':'unknown'})
        state = {'unknown':'待核','candidate':'候选待核','confirmed':'已确认'}[item['status']]
        lines.append(f"- {label}：{item['value'] or '未知'}（{state}）")
    lines += ['','## 场景准备清单'] + ['- '+step for step in selected['checklist']]
    if detail['domain'] == 'meeting':
        lines += ['','## 讨论与分歧',facts.get('notes_text',{}).get('value') or '未提供文字笔记，不推断会议内容。','','## 明确决定',facts.get('decisions_text',{}).get('value') or '原记录未明确提供，待核；不将提议写为决定。','','## 行动候选',facts.get('actions_text',{}).get('value') or '责任、行动与期限待核；尚未真实承接。']
    elif detail['domain'] == 'document':
        lines += ['','## 通用草稿底稿',facts.get('facts_text',{}).get('value') or '事实底稿未提供，待补；不补造内容。','','## 校核待办','核对原文事实、对象和披露范围；正式文号、署名及未提供数字留待真实责任人处理。']
    elif detail['domain'] == 'menu':
        lines += ['','## 菜单候选',(facts.get('menu_date',{}).get('value') or '日期待核')+' / '+(facts.get('meal_slot',{}).get('value') or '餐别待核'),facts.get('dishes',{}).get('value') or '菜品待补','厨师真实发布、备餐与供餐均另核。']
    elif detail['domain'] == 'leave':
        lines += ['','## 分材料清单']
        for key in ('passport_info','visa_info','domestic_proof','nis_entry_card','nis_exit_card'):
            lines.append('- '+selected['fields'][key]+'：'+(facts.get(key,{}).get('value') or '未提供；适用性与办理要求待核'))
    if missing:
        lines += ['','## 待补','- '+'、'.join(missing)]
    if detail.get('calculation'):
        calculation = detail['calculation']
        lines += ['', '## 程序计算（本地人工核对明细）', calculation['scope']]
        if calculation.get('stale'):
            lines.append('事实或来源已修订：以下为原核对范围的计算，须重核后用于下一步。')
        for group in calculation['groups']:
            lines.append('- ' + ' / '.join(str(group[key]) for key in ('label','currency','unit','period') if key in group) + '：' + group['total'])
        if not calculation['groups']:
            lines.append('未提供可合计行；不将未知金额或数量当作0。')
    lines += ['','## 来源范围']
    for source in detail['sources']:
        if not source.get('preparation_usable', True):
            continue
        lines.append(f"- 来源 {source['id']} / {source['kind']} / {source['recorded_at']} / 陈述人：{source['reported_by'] or '未注明'}")
    lines += ['','## 下一步','编辑并保存可用成果；复制或下载只代表取出准备材料，不代表发送或业务办结。']
    return '\n'.join(lines),'needs_review' if missing else 'ready'
