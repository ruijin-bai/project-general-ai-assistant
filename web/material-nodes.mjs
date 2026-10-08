const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function createMaterialNodes({getState,request,post,render,toast,preserveArtifact,refreshMatter,refreshWaiting}) {
  const records=new Map(),current=()=>getState().current;
  const initial=()=>({material_id:'',who:'本人',due_at:'',note:'',dirty:false});
  const recordFor=id=>{if(!records.has(id)) records.set(id,{data:null,draft:initial(),reading:false,error:null,open:false});return records.get(id);};
  const owned=(id,epoch,selection)=>current()?.id===id&&getState().identityEpoch===epoch&&getState().selection===selection;
  async function read() {
    const matter=current();if(matter?.domain!=='leave') return;
    const record=recordFor(matter.id);if(record.reading) return;
    const epoch=getState().identityEpoch,selection=getState().selection;record.reading=true;record.open=true;record.error=null;render();
    try {
      const [data,fresh]=await Promise.all([request(`/api/matters/${encodeURIComponent(matter.id)}/leave/material-nodes`),request(`/api/matters/${encodeURIComponent(matter.id)}`)]);
      if(!owned(matter.id,epoch,selection)) return;
      if(data.version!==fresh.version||data.revision_no!==fresh.revision_no) throw {message:'读取期间事项已变化，请重新读取；待办输入保留。'};
      preserveArtifact();record.data=data;await refreshMatter(matter.id,selection);await refreshWaiting(matter.id);
    } catch(error) {if(owned(matter.id,epoch,selection)) record.error=error.message||'材料日期节点暂未读回，输入保留。';}
    finally {if(owned(matter.id,epoch,selection)){record.reading=false;render();}}
  }
  async function save(mode,materialId) {
    const matter=current(),record=recordFor(matter.id),data=record.data,draft=record.draft;
    if(!data||data.version!==matter.version||data.revision_no!==matter.revision_no){toast('先读取最新材料日期和原待办再核对；输入保留。');return;}
    const item=data.items.find(item=>item.material_id===materialId);
    if(!item?.eligible){toast('先在材料清单保存明确日期、原处及本人核对；缺项保持待核。');return;}
    if(mode==='create'&&item.existing_point){toast('同一材料已有待办，原文字与已移除状态保留；请沿本地等待点编辑。');return;}
    const body={expected_version:data.version,input_revision:data.revision_no,material_id:materialId,mode,basis_token:item.basis_token};
    if(mode==='create') Object.assign(body,{who:draft.who.trim(),due_at:draft.due_at.trim()||null,note:draft.note.trim()});
    const epoch=getState().identityEpoch,selection=getState().selection;preserveArtifact();
    await post(`/api/matters/${encodeURIComponent(matter.id)}/leave/material-nodes`,body,async()=>{
      if(!owned(matter.id,epoch,selection)) return;
      if(mode==='create') record.draft=initial();
      await refreshMatter(matter.id,selection);await read();
      toast(mode==='create'?'个人材料核对待办已保存；提醒时点由本人设置，未发送通知。':'已记录本人采用当前材料依据；旧待办文字、提醒和处理状态保留。');
    });
  }
  async function renderDates() {
    const matter=current(),record=recordFor(matter.id),data=record.data;
    if(!data||data.version!==matter.version||data.revision_no!==matter.revision_no){toast('先读取最新材料、日期比较与关联行程；输入保留。');return;}
    const epoch=getState().identityEpoch,selection=getState().selection;preserveArtifact();
    await post(`/api/matters/${encodeURIComponent(matter.id)}/leave/material-dates/render`,{expected_version:data.version,input_revision:data.revision_no,report_token:data.date_report.report_token},async()=>{
      if(!owned(matter.id,epoch,selection))return;
      await refreshMatter(matter.id,selection);await read();toast('已另存程序日期核对稿，原稿保留；可在稿件列表主动选择、编辑和固定导出。');
    });
  }
  function datesMarkup(report,matter,disabled) {
    if(!report)return '';
    return `<div class="material-dates-report"><h3>程序日期核对</h3><p class="scope-note">${esc(report.scope)}</p><p class="small">已比较 ${report.computed_count} 项 · 待核 ${report.pending_count} 项 · 共同日期比较UTC偏移：${esc(report.comparison_offset||'未指定；仅纯年月日可直接比较')}</p>${report.items.filter(row=>row.status==='computed').map(row=>`<p class="small"><strong>${esc(row.material_label)}／${esc(row.endpoint_label)}</strong>：材料日期 ${esc(row.material_date)} 在比较日期 ${esc(row.comparison_date)} ${row.relation==='same_day'?'同日':`${row.relation==='after'?'之后':'之前'}${Math.abs(row.calendar_days)}个日历日`}<br>原值：${esc(row.endpoint_value)}；${esc(row.conversion)}。仅日期比较，具体时点和效力待核。</p>`).join('')||'<p class="small dirty-note">尚无本人确认的可比较日期，不使用模型候选。</p>'}<details><summary class="details-toggle">日期与口径缺项 ${report.pending_count} 项</summary>${report.items.filter(row=>row.status!=='computed').map(row=>`<p class="small">${esc(row.material_label)}／${esc(row.endpoint_label)}：${esc(row.reason)}</p>`).join('')}</details><button class="button quiet" data-action="material-dates-render" ${disabled||!report.computed_count||['paused','handoff'].includes(matter.assistant_status)?'disabled':''}>另存程序日期核对稿</button></div>`;
  }
  function markup(matter) {
    if(matter.domain!=='leave') return '';
    const record=recordFor(matter.id),data=record.data,draft=record.draft,disabled=getState().busy||record.reading?'disabled':'';
    const field=(name,label,extra='')=>`<label class="field-label" for="material-node-${name}">${label}</label><input id="material-node-${name}" data-material-node-field="${name}" value="${esc(draft[name])}" ${extra} ${disabled}>`;
    return `<section class="panel"><details class="material-nodes-details" ${record.open?'open':''}><summary class="details-toggle">材料日期与个人核对待办</summary><div class="panel-head"><h2>材料日期跟进</h2><button class="button" data-action="material-nodes-read" ${disabled}>读取材料日期与原待办（输入保留）</button></div><p class="scope-note">${esc(data?.scope||'只从本人保存并核对的材料日期建立私人核对待办；不猜办理窗口、效力、过期或可通行。提醒时间须本人明确填写，不默认材料日期午夜。')}</p>${record.error?`<p class="dirty-note">${esc(record.error)}</p>`:''}${datesMarkup(data?.date_report,matter,disabled)}${data?`<p class="small ${data.version!==matter.version?'dirty-note':'muted'}">材料基版 ${esc(data.version)}／当前 ${esc(matter.version)} · 事实修订 ${esc(data.revision_no)}${data.version!==matter.version?'；请重新读取并核对，输入保留。':''}</p>${data.items.map(item=>`<article class="evidence-record"><strong>${esc(item.label)} · ${esc(item.date||'日期未知')}</strong><p class="small">${item.eligible?'本人已保存日期及原处核对，可建立待办':'日期、来源或本人核对待补；不猜节点'}</p>${item.excerpt?`<pre>${esc(item.excerpt)}</pre><p class="small muted">已存原处：${esc(item.source_position)}</p>`:''}${item.existing_point?`<p class="small">原待办：${esc(item.existing_point.who)} · ${esc(item.existing_point.reason)}<br>提醒：${esc(item.existing_point.due_at||'未设')} · ${item.existing_point.active===false?'本人已移除，历史保留':item.existing_point.state==='resolved'?'本人已解除等待':'仍在等待'}<br>${esc(item.existing_point.note)}</p><p class="small ${item.basis_stale?'dirty-note':'muted'}">原节点材料日期：${esc(item.bound_date||'未知')}；${item.basis_stale?'材料或相关事实变化，先在等待点核对修改旧文字／提醒，再明确采用当前依据。':'当前所选材料依据未变化。'}</p>${item.basis_stale&&item.eligible&&item.existing_point.active!==false?`<button class="button quiet" data-action="material-node-rebind" data-material-id="${esc(item.material_id)}" ${disabled}>本人核对后采用当前材料依据（保留旧待办）</button>`:''}`:''}</article>`).join('')}<form id="material-nodes-form"><label class="field-label" for="material-node-material_id">建立待办的已核材料</label><select id="material-node-material_id" data-material-node-field="material_id" ${disabled}><option value="">请选择</option>${data.items.filter(item=>item.eligible&&!item.existing_point).map(item=>`<option value="${esc(item.material_id)}" ${item.material_id===draft.material_id?'selected':''}>${esc(item.label)} · ${esc(item.date)}</option>`).join('')}</select>${field('who','本人选择等待对象（不绑定他人账号）','maxlength="200" required')}${field('due_at','本人明确的本地提醒时间（未知留空）','maxlength="100" placeholder="2026-10-09T09:00:00+01:00"')}<label class="field-label" for="material-node-note">本人核对待办备注</label><textarea id="material-node-note" data-material-node-field="note" maxlength="2000" ${disabled}>${esc(draft.note)}</textarea><button class="button" type="submit" ${disabled}>建立个人材料核对待办</button><p class="small muted">保存后沿“本人可维护的等待点”修改原因、提醒、备注及解除等待；暂停期间可手工维护，恢复后才准备新跟进文字。</p></form>`:'<p class="small muted">尚未读取，未知材料不自动形成截止日或通知。</p>'}</details></section>`;
  }
  return {markup,
    input:element=>{if(element.dataset.materialNodeField===undefined||current()?.domain!=='leave')return false;const draft=recordFor(current().id).draft;draft[element.dataset.materialNodeField]=element.value;draft.dirty=true;return true;},
    toggle:element=>{if(!element.matches('.material-nodes-details')||!current())return false;recordFor(current().id).open=element.open;return true;},
    action:(name,element)=>{if(name==='material-nodes-read'){read();return true;}if(name==='material-dates-render'){renderDates();return true;}if(name==='material-node-rebind'){save('rebind',element.dataset.materialId);return true;}return false;},
    submit:form=>{if(form.id!=='material-nodes-form')return false;save('create',recordFor(current().id).draft.material_id);return true;},
    clear:()=>records.clear(),isDirty:()=>[...records.values()].some(record=>record.draft.dirty)};
}
