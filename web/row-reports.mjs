const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function createRowReports({getState,request,post,render,toast,preserveArtifact,refreshMatter}) {
  const records=new Map(),current=()=>getState().current;
  const initial=()=>({selected:new Set(),period:'',note:'',ack:false,dirty:false});
  const recordFor=id=>{if(!records.has(id))records.set(id,{data:null,draft:initial(),reading:false,error:null,open:false});return records.get(id);};
  const owned=(id,epoch,selection)=>current()?.id===id&&getState().identityEpoch===epoch&&getState().selection===selection;
  const path=id=>`/api/matters/${encodeURIComponent(id)}/rows/reports`;
  const repeated=record=>record.data?.duplicate_groups.some(ids=>ids.filter(id=>record.draft.selected.has(id)).length>1);
  async function read() {
    const matter=current();if(!['expense','inventory'].includes(matter?.domain))return;
    const record=recordFor(matter.id);if(record.reading)return;
    const epoch=getState().identityEpoch,selection=getState().selection;record.reading=true;record.open=true;record.error=null;render();
    try {
      const [data,fresh]=await Promise.all([request(path(matter.id)),request(`/api/matters/${encodeURIComponent(matter.id)}`)]);
      if(!owned(matter.id,epoch,selection))return;
      if(data.version!==fresh.version||data.revision_no!==fresh.revision_no)throw {message:'读取期间明细已变化，请重新读取；范围输入保留。'};
      if(record.data?.rows_revision!==data.rows_revision)record.draft.ack=false;
      preserveArtifact();record.data=data;await refreshMatter(matter.id,selection);
    }catch(error){if(owned(matter.id,epoch,selection))record.error=error.message||'汇总明细暂未读回，输入保留。';}
    finally{if(owned(matter.id,epoch,selection)){record.reading=false;render();}}
  }
  async function save() {
    const matter=current(),record=recordFor(matter.id),data=record.data,draft=record.draft;
    if(!data||data.version!==matter.version||data.revision_no!==matter.revision_no||data.stale){toast('先读取并重核保存当前明细，再另存汇总；选择和备注保留。');return;}
    if(!draft.selected.size){toast('请明确选择已保存行，不默认选择全部。');return;}
    const epoch=getState().identityEpoch,selection=getState().selection;preserveArtifact();
    await post(path(matter.id)+'/render',{expected_version:data.version,input_revision:data.revision_no,rows_revision:data.rows_revision,row_ids:[...draft.selected],note:draft.note,duplicate_acknowledged:draft.ack},async()=>{
      if(!owned(matter.id,epoch,selection))return;
      record.draft=initial();await refreshMatter(matter.id,selection);await read();toast('已另存选行汇总；在稿件列表查看和编辑。原明细和原稿保留，CSV固定本次选择。');
    });
  }
  async function download(artifactId) {
    const matter=current(),epoch=getState().identityEpoch,selection=getState().selection,controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try {
      const response=await fetch(path(matter.id)+`/${encodeURIComponent(artifactId)}/csv`,{signal:controller.signal});
      if(!response.ok)throw {message:'固定选行CSV暂未读回，请核对当前账号及保存记录。'};
      const blob=await response.blob();if(!owned(matter.id,epoch,selection))return;
      const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='已保存所选明细.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(error){if(owned(matter.id,epoch,selection))toast(error.name==='AbortError'?'下载超时；历史汇总保留，可重试。':error.message);}
    finally{clearTimeout(timer);}
  }
  function markup(matter) {
    if(!['expense','inventory'].includes(matter.domain))return '';
    const record=recordFor(matter.id),data=record.data,draft=record.draft,disabled=getState().busy||record.reading?'disabled':'',dimension=matter.domain==='expense'?'currency':'unit';
    const outdated=data&&(data.stale||data.version!==matter.version||data.revision_no!==matter.revision_no),missing=data?[...draft.selected].filter(id=>!data.rows.some(row=>row.id===id)):[];
    return `<details class="row-reports-details" ${record.open?'open':''}><summary class="details-toggle">选期／选行汇总与固定导出</summary><div class="panel-head"><h3>本人明确汇总范围</h3><button class="button" data-action="row-reports-read" ${disabled}>读取已保存明细与汇总（输入保留）</button></div><p class="scope-note">${esc(data?.scope||'只汇总明确选中的已保存行；不同币种、物品、单位和期间分别计算，不推断缺报或真实批准、付款、收发。')}</p>${record.error?`<p class="dirty-note">${esc(record.error)}</p>`:''}${data?`<p class="small ${outdated?'dirty-note':'muted'}">明细修订 ${esc(data.rows_revision??'未保存')}／当前 ${esc(matter.revision_no)} · 所读基版 ${esc(data.version)}／当前 ${esc(matter.version)}${outdated?'；重新读取；来源或事实换版须重核保存明细。':''}</p><label class="field-label" for="row-report-period">原述期间（不转换月季）</label><select id="row-report-period" data-row-report-field="period" ${disabled}><option value="">请选择期间</option>${data.periods.map(period=>`<option value="${esc(period)}" ${period===draft.period?'selected':''}>${esc(period)}</option>`).join('')}</select><button class="button quiet" data-action="row-reports-period" ${disabled}>加入所选期间全部已保存行</button><button class="button quiet" data-action="row-reports-clear" ${disabled}>清空本次选择</button><p class="small" id="row-report-count">本次明确选择 ${draft.selected.size} 行；未保存表格编辑不参加汇总。</p>${missing.length?`<p class="dirty-note">${missing.length}个原选行已不在当前明细；请清空后重新选择，旧稿仍保留。</p>`:''}<div class="row-report-selection">${data.rows.map(row=>`<label class="evidence-record"><input type="checkbox" data-row-report-select="${esc(row.id)}" ${draft.selected.has(row.id)?'checked':''} ${disabled}> ${esc(row.label)} · ${esc(row.value)} ${esc(row[dimension])} · ${esc(row.period)}<span class="small muted"> · ${esc(row.source_position)} · 来源 ${esc(row.source_id)}</span></label>`).join('')||'<p class="small muted">尚无已保存明细，请先核对并保存。</p>'}</div><label class="field-label" for="row-report-note">本人范围／项目组织备注（已知再写，可空）</label><textarea id="row-report-note" data-row-report-field="note" maxlength="2000" ${disabled}>${esc(draft.note)}</textarea>${repeated(record)?`<p class="dirty-note">选中行存在同原处相同明细；请排除重复候选，或写明为何分别累计。</p><label><input type="checkbox" data-row-report-ack ${draft.ack?'checked':''} ${disabled}> 本人已核对重复原处，按当前选行累计并在备注说明口径</label>`:''}<div class="artifact-toolbar"><button class="button" data-action="row-reports-save" ${disabled||outdated||missing.length||!draft.selected.size||['paused','handoff'].includes(matter.assistant_status)?'disabled':''}>另存所选期间汇总稿</button>${draft.dirty?'<span class="small dirty-note">范围输入尚未另存</span>':''}</div>${data.reports.length?'<h4>已保存汇总的固定明细</h4>':''}${data.reports.map(report=>`<p class="small">r${esc(report.rows_revision)} · ${esc(report.periods.join('、'))} · ${report.selected_count}行 <button class="button quiet" data-action="row-reports-csv" data-report-id="${esc(report.artifact_id)}" ${disabled}>下载此汇总固定CSV</button></p>`).join('')}`:'<p class="small muted">请先读取；期间和行不自动选入。</p>'}</details>`;
  }
  return {markup,
    input:element=>{if(!current())return false;const record=recordFor(current().id),draft=record.draft;
      if(element.dataset.rowReportSelect!==undefined){element.checked?draft.selected.add(element.dataset.rowReportSelect):draft.selected.delete(element.dataset.rowReportSelect);draft.ack=false;draft.dirty=true;render();return true;}
      if(element.dataset.rowReportField!==undefined){draft[element.dataset.rowReportField]=element.value;draft.dirty=true;return true;}
      if(element.dataset.rowReportAck!==undefined){draft.ack=element.checked;draft.dirty=true;return true;}return false;},
    action:(name,element)=>{if(!name.startsWith('row-reports-'))return false;
      if(name==='row-reports-read')read();else if(name==='row-reports-save')save();else if(name==='row-reports-csv')download(element.dataset.reportId);
      else{const record=recordFor(current().id);if(name==='row-reports-clear')record.draft.selected.clear();else if(name==='row-reports-period')record.data?.rows.filter(row=>row.period===record.draft.period).forEach(row=>record.draft.selected.add(row.id));record.draft.ack=false;record.draft.dirty=true;render();}return true;},
    toggle:element=>{if(!element.matches('.row-reports-details')||!current())return false;recordFor(current().id).open=element.open;return true;},
    clear:()=>records.clear(),isDirty:()=>[...records.values()].some(record=>record.draft.dirty)};
}
