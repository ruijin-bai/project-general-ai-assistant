const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function createExpenseJourney({getState,request,post,render,toast,preserveArtifact,refreshMatter,getPurposeDraft}) {
  const records=new Map(),current=()=>getState().current;
  const eligible=m=>m?.domain==='expense'&&(getState().session?.mode==='local_preview'||getState().session?.principal?.capabilities?.some(cap=>['employee','clerk'].includes(cap)));
  const rec=id=>{if(!records.has(id))records.set(id,{data:null,preview:null,source:'',selected:new Set(),fill:false,dirty:false,reading:false,open:false,error:null});return records.get(id);};
  const owned=(id,epoch,selection)=>current()?.id===id&&getState().identityEpoch===epoch&&getState().selection===selection;
  const path=id=>`/api/matters/${encodeURIComponent(id)}/expense-journey`;
  async function read() {
    const m=current();if(!eligible(m))return;const r=rec(m.id);if(r.reading)return;
    const epoch=getState().identityEpoch,selection=getState().selection;r.reading=true;r.open=true;r.error=null;render();
    try {const data=await request(path(m.id));if(!owned(m.id,epoch,selection))return;r.data=data;preserveArtifact();await refreshMatter(m.id,selection);}
    catch(error){if(owned(m.id,epoch,selection))r.error=error.message||'行程引用暂未读回，人工值保留。';}
    finally{if(owned(m.id,epoch,selection)){r.reading=false;render();}}
  }
  async function preview() {
    const m=current(),r=rec(m.id);if(!r.source){toast('请明确选择本人行程，不默认关联。');return;}
    const epoch=getState().identityEpoch,selection=getState().selection;r.reading=true;r.error=null;render();
    try {const data=await request(path(m.id)+'/preview',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':getState().session?.csrf_token||'','Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({travel_id:r.source})});if(!owned(m.id,epoch,selection))return;r.preview=data;r.selected=new Set([...r.selected].filter(field=>data.items.some(item=>item.field===field&&item.status==='confirmed'&&item.value)));preserveArtifact();await refreshMatter(m.id,selection);}
    catch(error){if(owned(m.id,epoch,selection)){r.preview=null;r.error=error.message||'计划字段暂未读回，私人编辑保留。';}}
    finally{if(owned(m.id,epoch,selection)){r.reading=false;render();}}
  }
  async function save() {
    const m=current(),r=rec(m.id),p=r.preview;if(!p||!r.selected.size){toast('先读当前计划，再明确选必要的已确认字段。');return;}
    if(r.fill&&String(getPurposeDraft()??'').trim()){toast('费用事由已有未保存人工文字；先取消填空白选项，人工文字保留。');return;}
    const epoch=getState().identityEpoch,selection=getState().selection;preserveArtifact();
    await post(path(m.id)+'/save',{expected_version:m.version,input_revision:m.revision_no,travel_id:p.travel_id,travel_revision:p.travel_revision,fields:[...r.selected],fill_empty_purpose:r.fill},async result=>{
      if(!owned(m.id,epoch,selection))return;r.dirty=false;r.fill=false;r.preview=null;await refreshMatter(m.id,selection);await read();toast(result.purpose_filled?'行程引用及空白费用事由已保存；金额、期间与原稿保留，请核对费用明细再另存汇总。':'所选计划引用已保存，已有费用事由保留；核对费用明细后，所选行汇总会带上引用。');
    });
  }
  async function unlink() {
    const m=current(),epoch=getState().identityEpoch,selection=getState().selection;preserveArtifact();
    await post(path(m.id)+'/unlink',{expected_version:m.version,input_revision:m.revision_no},async()=>{if(!owned(m.id,epoch,selection))return;const r=rec(m.id);r.dirty=false;r.preview=null;r.selected.clear();r.fill=false;await refreshMatter(m.id,selection);await read();toast('当前行程引用已解除，历史和已复用事由保留；费用明细重核后可独立继续。');});
  }
  function markup(m) {
    if(!eligible(m))return '';const r=rec(m.id),p=r.preview,context=r.data?.context,disabled=getState().busy||r.reading?'disabled':'';
    const humanPurpose=Boolean(String(getPurposeDraft()??'').trim()),canFill=p?.purpose_can_fill&&!m.facts?.purpose?.value&&!humanPurpose&&r.selected.has('purpose');
    if(!canFill)r.fill=false;
    return `<details class="expense-journey-details" ${r.open?'open':''}><summary class="details-toggle">复用本人行程准备费用（不重复抄计划）</summary><p class="scope-note">明确选择已确认计划值，私人原话、同行、联系方式和实际出返不自动带入。不把行程日期当财务期间，不计算费用或决定报销资格。引用修订后原金额／票据保留，明细重核保存后再另存所选费用汇总。</p><button class="button" data-action="expense-journey-read" ${disabled}>读取本人行程与当前费用引用</button>${r.error?`<p class="dirty-note">${esc(r.error)}</p>`:''}${r.data?`
      ${context?`<h4>已保存的必要计划引用${context.stale?' · 历史／待重核':''}</h4>${context.stale?'<p class="dirty-note">关联行程或来源已换版／不可读，旧引用保留。重读核对保存当前选择，或明确解除引用后独立继续，不能沿旧依据新生成费用汇总。</p>':''}<ul>${Object.values(context.fields).map(row=>`<li>${esc(row.label)}：${esc(row.value)} · 已核计划引用／非实际结果</li>`).join('')}</ul><p class="small muted">行程修订 ${context.travel_revision} · 保存 ${esc(context.saved_at)}；原财务提交／审核／付款状态独立保留。</p><button class="button quiet" data-action="expense-journey-unlink" ${disabled}>解除当前行程引用（历史保留）</button>`:'<p class="small muted">当前未关联行程，可沿费用明细独立办理；行程不是必填前置。</p>'}
      <label class="field-label" for="expense-journey-source">本人已保存行程（明确选择，最新40份）</label><select id="expense-journey-source" data-expense-journey-source ${disabled}><option value="">请选择行程</option>${r.data.items.map(item=>`<option value="${esc(item.id)}" ${r.source===item.id?'selected':''}>${esc(item.goal_text)} · r${item.revision_no}</option>`).join('')}</select><button class="button" data-action="expense-journey-preview" ${disabled}>读取所选计划字段（不调用模型）</button>
      ${p?`<p class="small muted">所读行程修订 ${p.travel_revision}；模型候选和缺值须先在原行程人工核对，不直接当已核事实。</p>${p.items.map(item=>`<label class="evidence-record"><input type="checkbox" data-expense-journey-field="${item.field}" ${r.selected.has(item.field)?'checked':''} ${disabled||item.status!=='confirmed'||!item.value?'disabled':''}> ${esc(item.label)}：${esc(item.value??'尚未提供')} · ${item.status==='confirmed'?'本人已核计划':item.status==='candidate'?'候选待核':'未知'}</label>`).join('')}<label><input type="checkbox" data-expense-journey-fill ${r.fill&&canFill?'checked':''} ${disabled||!canFill?'disabled':''}> 明确用所选原用途填入空白费用事由（已有人工值保留）</label>${humanPurpose||m.facts?.purpose?.value?'<p class="small muted">费用事由已有人工值／编辑，保持原值；可只保存必要计划引用。</p>':''}<div><button class="button" data-action="expense-journey-save" ${disabled||!r.selected.size?'disabled':''}>保存所选行程引用与空白事由</button></div>`:''}`:''}</details>`;
  }
  return {markup,input:element=>{if(!eligible(current()))return false;const r=rec(current().id);
    if(element.dataset.expenseJourneySource!==undefined){r.source=element.value;r.preview=null;r.selected.clear();r.fill=false;r.dirty=true;render();return true;}
    if(element.dataset.expenseJourneyField!==undefined){element.checked?r.selected.add(element.dataset.expenseJourneyField):r.selected.delete(element.dataset.expenseJourneyField);if(!r.selected.has('purpose'))r.fill=false;r.dirty=true;render();return true;}
    if(element.dataset.expenseJourneyFill!==undefined){r.fill=element.checked;r.dirty=true;return true;}return false;},
    action:name=>{if(!name.startsWith('expense-journey-')||!eligible(current()))return false;if(name==='expense-journey-read')read();else if(name==='expense-journey-preview')preview();else if(name==='expense-journey-save')save();else if(name==='expense-journey-unlink')unlink();return true;},
    toggle:element=>{if(element.matches('.expense-journey-details')&&current())rec(current().id).open=element.open;},clear:()=>records.clear(),isDirty:()=>[...records.values()].some(r=>r.dirty)};
}
