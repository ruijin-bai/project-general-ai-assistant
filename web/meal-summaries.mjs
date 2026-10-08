const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={keep:'希望保餐',request_reduce:'调整申请',pending:'待核',conflict:'冲突待核'};
export function createMealSummaries({getState,request,post,render,toast,preserveArtifact,refreshMatter}) {
  const records=new Map(),current=()=>getState().current;
  const initial=()=>({selected:new Set(),date:'',reportId:'',cookId:'',ack:false,dirty:false});
  const rec=id=>{if(!records.has(id))records.set(id,{data:null,preview:null,draft:initial(),open:false,reading:false,error:null});return records.get(id);};
  const eligible=m=>['travel','leave'].includes(m?.domain)&&(getState().session?.mode==='local_preview'||getState().session?.principal?.capabilities?.includes('clerk'));
  const owned=(id,epoch,selection)=>current()?.id===id&&getState().identityEpoch===epoch&&getState().selection===selection;
  const path=id=>`/api/matters/${encodeURIComponent(id)}/meal-summaries`;
  async function read() {
    const m=current();if(!eligible(m))return;const r=rec(m.id);if(r.reading)return;
    const epoch=getState().identityEpoch,selection=getState().selection;r.reading=true;r.open=true;r.error=null;render();
    try {const data=await request(path(m.id));if(!owned(m.id,epoch,selection))return;r.data=data;r.preview=null;r.draft.ack=false;preserveArtifact();await refreshMatter(m.id,selection);}
    catch(error){if(owned(m.id,epoch,selection))r.error=error.message||'餐次范围暂未读回，选择保留。';}
    finally{if(owned(m.id,epoch,selection)){r.reading=false;render();}}
  }
  async function preview() {
    const m=current(),r=rec(m.id),d=r.draft;if(!d.selected.size||!d.date){toast('请明确选择本人行程和本次用餐日期。');return;}
    const epoch=getState().identityEpoch,selection=getState().selection;r.reading=true;r.error=null;r.preview=null;render();
    try {const view=await request(path(m.id)+'/preview',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':getState().session?.csrf_token||'','Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({selected_ids:[...d.selected],meal_date:d.date})});if(owned(m.id,epoch,selection)){r.preview=view;preserveArtifact();await refreshMatter(m.id,selection);}}
    catch(error){if(owned(m.id,epoch,selection))r.error=error.message||'核对未完成，原记录保留。';}
    finally{if(owned(m.id,epoch,selection)){r.reading=false;render();}}
  }
  async function save() {
    const m=current(),r=rec(m.id),p=r.preview;if(!p?.has_records){toast('先核对本次范围；缺记录不生成假人数0。');return;}
    const epoch=getState().identityEpoch,selection=getState().selection;preserveArtifact();
    await post(path(m.id)+'/render',{expected_version:m.version,selected_ids:p.selected_ids,meal_date:p.meal_date,basis_token:p.basis_token},async result=>{
      if(!owned(m.id,epoch,selection))return;r.draft.dirty=false;r.draft.ack=false;await refreshMatter(m.id,selection);await read();
      r.draft.reportId=result?.artifact_id||r.data?.reports[0]?.artifact_id||'';render();toast('已另存可编辑餐次汇总；原人工稿保留，请从稿件列表选择。尚未分享或调整餐次。');
    });
  }
  async function share() {
    const m=current(),r=rec(m.id),d=r.draft,report=r.data?.reports.find(report=>report.artifact_id===d.reportId);
    if(!report||report.stale||!d.cookId||!d.ack){toast('请核对当前固定汇总，明确选择厨师并确认分享范围。');return;}
    const epoch=getState().identityEpoch,selection=getState().selection;preserveArtifact();
    await post(`/api/matters/${encodeURIComponent(m.id)}/shares`,{expected_version:m.version,artifact_id:report.artifact_id,artifact_version:1,recipient_id:d.cookId,title:report.title,content:report.cook_content,dependency_refs:[]},async()=>{
      if(!owned(m.id,epoch,selection))return;d.ack=false;await refreshMatter(m.id,selection);await read();toast('已保存给所选厨师的固定只读副本；撤回可在本事项分享记录办理。没有供餐或扣餐。');
    });
  }
  function markup(m) {
    if(!eligible(m))return '';const r=rec(m.id),data=r.data,d=r.draft,p=r.preview,disabled=getState().busy||r.reading?'disabled':'';
    const report=data?.reports.find(report=>report.artifact_id===d.reportId);
    return `<details class="meal-summaries-details" ${r.open?'open':''}><summary class="details-toggle">所选行程餐次需求汇总与厨师只读副本</summary><p class="scope-note">按已保存原述引用计数，无完整人员基线；不从出返时间推扣餐，也不登记真实备餐、供餐。未保存餐次草稿不参加。</p><button class="button" data-action="meal-summaries-read" ${disabled}>读取本人行程与已保存汇总</button>${r.error?`<p class="dirty-note">${esc(r.error)}</p>`:''}${data?`
      <label class="field-label" for="meal-summary-date">本次用餐日期（明确年月日）</label><input id="meal-summary-date" type="date" data-meal-summary-field="date" value="${esc(d.date)}" ${disabled}>
      <p class="small muted">明确选择1至12份本人行程／休假，默认不选；当前可列最新40份。</p><div>${data.items.map(item=>`<label class="evidence-record"><input type="checkbox" data-meal-summary-select="${esc(item.id)}" ${d.selected.has(item.id)?'checked':''} ${disabled}> ${esc(item.goal_text)} · r${item.revision_no}</label>`).join('')}</div>
      <div class="artifact-toolbar"><button class="button" data-action="meal-summaries-preview" ${disabled}>核对本次去重与冲突</button><button class="button quiet" data-action="meal-summaries-clear" ${disabled}>清空本次范围</button><span class="small muted">明确选择 ${d.selected.size} 份；未另存选择仅在本页保留。</span></div>
      ${p?`<h4>所选日期的原述需求</h4><div class="table-scroll"><table><thead><tr><th>原述餐别</th><th>保餐引用</th><th>调整申请</th><th>待核</th><th>冲突</th><th>不同引用</th><th>重复原记录</th></tr></thead><tbody>${p.groups.map(g=>`<tr><td>${esc(g.meal_slot)}</td>${['keep','request_reduce','pending','conflict','unique_refs','duplicates'].map(k=>`<td>${g[k]}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${!p.has_records?'<p class="dirty-note">所选日期无已保存餐次；人数未知，不当0。</p>':''}<details><summary>经办私人核对原记录（不进入厨师副本）</summary>${p.details.map(item=>`<p>${esc(item.person_ref)} · ${esc(item.meal_slot)} · ${esc(labels[item.state])} · ${item.records.length}条原记录<br>${item.records.map(row=>`${esc(labels[row.request])}：${esc(row.note||'无备注')}`).join('<br>')}</p>`).join('')}</details><label class="field-label" for="meal-summary-preview">程序生成的固定厨师副本预览</label><textarea id="meal-summary-preview" readonly rows="9">${esc(p.cook_content)}</textarea><button class="button" data-action="meal-summaries-save" ${disabled||!p.has_records||['paused','handoff','processing'].includes(m.assistant_status)?'disabled':''}>另存本次餐次汇总稿</button>`:''}
      ${data.reports.length?`<h4>保存后的固定程序副本</h4><label class="field-label" for="meal-summary-report">选择已保存汇总（个人编辑稿另从稿件列表打开）</label><select id="meal-summary-report" data-meal-summary-field="reportId" ${disabled}><option value="">请选择汇总</option>${data.reports.map(row=>`<option value="${esc(row.artifact_id)}" ${row.artifact_id===d.reportId?'selected':''}>${esc(row.title)}${row.stale?' · 依据变化／历史':''}</option>`).join('')}</select>${report?`<textarea readonly aria-label="已保存固定厨师汇总" rows="9">${esc(report.cook_content)}</textarea>${report.stale?'<p class="dirty-note">所选行程依据已变化，旧汇总保留；重核另存后才能新分享。</p>':''}`:''}${data.cooks.length?`<label class="field-label" for="meal-summary-cook">明确接收厨师账号</label><select id="meal-summary-cook" data-meal-summary-field="cookId" ${disabled}><option value="">请选择厨师</option>${data.cooks.map(cook=>`<option value="${esc(cook.id)}" ${cook.id===d.cookId?'selected':''}>${esc(cook.display_name)}</option>`).join('')}</select><label><input type="checkbox" data-meal-summary-ack ${d.ack?'checked':''} ${disabled}> 本人已核对上述固定程序副本，仅分享计数、餐别与日期；私人行程、人员引用、备注和个人编辑稿不发送</label><div><button class="button" data-action="meal-summaries-share" ${disabled||!report||report.stale||!d.cookId||!d.ack?'disabled':''}>分享固定汇总给所选厨师</button></div>`:'<p class="muted small">没有可选择的活跃厨师账号；私人汇总可继续编辑保存。</p>'}`:''}`:'<p class="small muted">先明确读取；不自动纳入其他事项或分享。</p>'}</details>`;
  }
  return {markup,input:element=>{if(!eligible(current()))return false;const r=rec(current().id),d=r.draft;
    if(element.dataset.mealSummarySelect!==undefined){element.checked?d.selected.add(element.dataset.mealSummarySelect):d.selected.delete(element.dataset.mealSummarySelect);r.preview=null;d.dirty=true;render();return true;}
    if(element.dataset.mealSummaryField!==undefined){const field=element.dataset.mealSummaryField;d[field]=element.value;d.ack=false;if(field==='date'){r.preview=null;d.dirty=true;}render();return true;}
    if(element.dataset.mealSummaryAck!==undefined){d.ack=element.checked;render();return true;}return false;},
    action:name=>{if(!name.startsWith('meal-summaries-')||!eligible(current()))return false;if(name==='meal-summaries-read')read();else if(name==='meal-summaries-preview')preview();else if(name==='meal-summaries-save')save();else if(name==='meal-summaries-share')share();else if(name==='meal-summaries-clear'){const r=rec(current().id);r.draft.selected.clear();r.draft.dirty=false;r.preview=null;render();}return true;},
    toggle:element=>{if(!element.matches('.meal-summaries-details')||!current())return false;rec(current().id).open=element.open;return true;},clear:()=>records.clear(),isDirty:()=>[...records.values()].some(r=>r.draft.dirty)};
}
