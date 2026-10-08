const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const kinds={passport:'护照材料',visa:'签证材料',domestic_proof:'国内真实证明',nis_entry:'入境卡材料',nis_exit:'离境卡材料'};
const types={submission_statement:'本人登记提交声明（不核外部提交）',receipt_reference:'引用实际取得回执原文',receipt_source_checked:'本人核对已引用回执原文',needs_review:'材料或手续待重核说明'};
const receiptLabels={unknown:'回执未提供／未知',source_provided:'回执原处已提供，待核',local_source_checked:'本人已核所给回执原文，效力未知',needs_review:'原回执保留，当前依据待重核'};
const submissionLabels={unknown:'提交未登记／未知',statement_recorded:'提交声明已登记，未核外部提交',needs_review:'原提交声明保留，当前依据待重核'};
export function createMaterialRecords({getState,request,post,render,toast,preserveArtifact,refreshMatter}) {
  const records=new Map(),current=()=>getState().current;
  const initial=()=>({material_kind:'visa',record_type:'submission_statement',source_id:'',start_line:'',end_line:'',receipt_id:'',occurred_at:'',note:'',dirty:false});
  const recordFor=id=>{if (!records.has(id)) records.set(id,{data:null,draft:initial(),reading:false,error:null,open:false,baseVersion:null,baseRevision:null});return records.get(id);};
  const owned=(id,epoch,selection)=>current()?.id===id&&getState().identityEpoch===epoch&&getState().selection===selection;
  async function read() {
    const matter=current();if (matter?.domain!=='leave') return;
    const record=recordFor(matter.id);if (record.reading) return;
    const epoch=getState().identityEpoch,selection=getState().selection;record.reading=true;record.error=null;record.open=true;render();
    try {
      const [data,fresh]=await Promise.all([request(`/api/matters/${encodeURIComponent(matter.id)}/leave/material-records`),request(`/api/matters/${encodeURIComponent(matter.id)}`)]);
      if (!owned(matter.id,epoch,selection)) return;
      if (data.version!==fresh.version||data.revision_no!==fresh.revision_no) throw {message:'读回时事项已变化，请再读取；未保存说明保留。'};
      preserveArtifact();record.data=data;record.baseVersion=data.version;record.baseRevision=data.revision_no;
      await refreshMatter(matter.id,selection);
    } catch(error) {if (owned(matter.id,epoch,selection)) record.error=error.message||'材料记录暂未读回，说明保留。';}
    finally {if (owned(matter.id,epoch,selection)) {record.reading=false;render();}}
  }
  function input(element) {
    if (element.dataset.materialRecordField===undefined||current()?.domain!=='leave') return false;
    const record=recordFor(current().id);record.draft[element.dataset.materialRecordField]=element.value;record.draft.dirty=true;return true;
  }
  function toggle(element) {if (!element.matches('.material-records-details')||!current()) return false;recordFor(current().id).open=element.open;return true;}
  async function save() {
    const matter=current(),record=recordFor(matter.id),draft=record.draft;
    if (!record.data) {toast('先读取当前各材料记录再登记；说明保留。');return;}
    if (record.baseVersion!==matter.version||record.baseRevision!==matter.revision_no) {toast('事项基版已变化，先读取最新材料记录并核对；说明保留。');return;}
    if (!draft.note.trim()) {toast('请填写实际取得的声明／核对说明，不能用计划代填执行。');return;}
    let ref={source_id:draft.source_id,start_line:Number(draft.start_line),end_line:Number(draft.end_line)},receiptId;
    if (draft.record_type==='receipt_source_checked') {
      const receipt=record.data.records.find(item=>item.id===draft.receipt_id&&item.record_type==='receipt_reference'&&item.material_kind===draft.material_kind);
      if (!receipt) {toast('请选择已引用的同类回执并阅读原处。');return;}
      ref=receipt.ref;receiptId=receipt.id;
    } else if (!ref.source_id||!Number.isInteger(ref.start_line)||!Number.isInteger(ref.end_line)||ref.start_line<1||ref.end_line<ref.start_line) {toast('请明确本事项已保存来源和真实行号。');return;}
    const body={expected_version:record.baseVersion,input_revision:record.baseRevision,material_kind:draft.material_kind,record_type:draft.record_type,note:draft.note.trim(),occurred_at:draft.occurred_at.trim()||null,ref};
    if (receiptId) body.receipt_id=receiptId;
    const epoch=getState().identityEpoch,selection=getState().selection;
    preserveArtifact();await post(`/api/matters/${encodeURIComponent(matter.id)}/leave/material-records`,body,async result=>{
      if (!owned(matter.id,epoch,selection)) return;
      record.data=result;record.baseVersion=result.version;record.baseRevision=result.revision_no;record.draft=initial();record.open=true;
      await refreshMatter(matter.id,selection);toast('独立材料来源记录已保存；未提交申请、签发或改写外部内容。');
    });
  }
  async function renderDraft() {
    const matter=current(),record=recordFor(matter.id);if (!record.data||record.baseVersion!==matter.version) {toast('请先读取最新材料记录再另存。');return;}
    const epoch=getState().identityEpoch,selection=getState().selection;preserveArtifact();
    await post(`/api/matters/${encodeURIComponent(matter.id)}/leave/material-records/render`,{expected_version:record.baseVersion,input_revision:record.baseRevision},async()=>{
      if (!owned(matter.id,epoch,selection)) return;
      await refreshMatter(matter.id,selection);toast('已另存材料办理核对稿；原稿保留，可在稿件列表主动选择新稿。');
    });
  }
  function action(name) {if (name==='material-records-read') {read();return true;}if (name==='material-records-render') {renderDraft();return true;}return false;}
  function submit(form) {if (form.id!=='material-records-form') return false;save();return true;}
  const options=(choices,value)=>Object.entries(choices).map(([key,label])=>`<option value="${esc(key)}" ${key===value?'selected':''}>${esc(label)}</option>`).join('');
  function markup(matter) {
    if (matter.domain!=='leave') return '';
    const record=recordFor(matter.id),draft=record.draft,data=record.data,disabled=getState().busy||record.reading?'disabled':'';
    const field=(name,label,extra='')=>`<label class="field-label" for="material-record-${name}">${label}</label><input id="material-record-${name}" data-material-record-field="${name}" value="${esc(draft[name])}" ${extra} ${disabled}>`;
    return `<section class="panel"><details class="material-records-details" ${record.open?'open':''}><summary class="details-toggle">各材料的办理声明与回执</summary><div class="panel-head"><h2>独立材料记录</h2><button class="button" data-action="material-records-read" ${disabled}>读取最新材料办理记录（说明保留）</button></div><p class="scope-note">只保存本人取得的实际声明、回执原处与本地核对；原文、记录人和日期分别保留。未提供材料不等于未曾提交，提交声明不等于真实提交获核；不生成证明或卡、不判断获准或可通行。</p>${record.error?`<p class="dirty-note">${esc(record.error)}</p>`:''}${data?`<p class="small ${data.version!==matter.version?'dirty-note':'muted'}">已读基版 ${esc(data.version)}／当前 ${esc(matter.version)} · 事实修订 ${esc(data.revision_no)}。${data.version!==matter.version?'记录依据已变化，请重新读取核对；说明保留。':''}</p><div class="table-scroll"><table class="rows-table"><thead><tr><th>材料</th><th>提交声明</th><th>回执核对</th></tr></thead><tbody>${data.items.map(item=>`<tr><td>${esc(item.label)}</td><td>${esc(submissionLabels[item.submission])}</td><td>${esc(receiptLabels[item.receipt])}</td></tr>`).join('')}</tbody></table></div><p class="scope-note">五类材料的正式适用与签发效力均未知，休假决定、真实离返、接送与餐次各自核对。</p>`:'<p class="small muted">尚未读取，不以空列表代表未办理。</p>'}<form id="material-records-form"><div class="facts-grid"><label class="field-label" for="material-record-material_kind">材料类型</label><select id="material-record-material_kind" data-material-record-field="material_kind" ${disabled}>${options(kinds,draft.material_kind)}</select><label class="field-label" for="material-record-record_type">记录性质</label><select id="material-record-record_type" data-material-record-field="record_type" ${disabled}>${options(types,draft.record_type)}</select></div><p class="small muted">核对回执时选择已有回执，引用原处由程序沿用；其他记录请选择已有来源并注明行号。</p><label class="field-label" for="material-record-source_id">本事项已保存来源</label><select id="material-record-source_id" data-material-record-field="source_id" ${disabled}><option value="">请选择</option>${(matter.sources||[]).map(source=>`<option value="${esc(source.id)}" ${source.id===draft.source_id?'selected':''}>${esc(source.text.slice(0,75))}</option>`).join('')}</select><div class="facts-grid">${field('start_line','原处起始行','type="number" min="1" step="1"')}${field('end_line','原处结束行','type="number" min="1" step="1"')}</div><label class="field-label" for="material-record-receipt_id">本人核对的已引用回执</label><select id="material-record-receipt_id" data-material-record-field="receipt_id" ${disabled}><option value="">请选择（只在核对回执时使用）</option>${(data?.records||[]).filter(item=>item.record_type==='receipt_reference').map(item=>`<option value="${esc(item.id)}" ${item.id===draft.receipt_id?'selected':''}>${esc(kinds[item.material_kind])} · ${esc(item.note.slice(0,70))}${item.stale?' · 旧依据待重核':''}</option>`).join('')}</select>${field('occurred_at','实际发生日期／带偏移时间（未知留空）','placeholder="2026-10-08 或 2026-10-08T14:00:00+01:00" maxlength="100"')}<label class="field-label" for="material-record-note">本人实际声明／核对说明</label><textarea id="material-record-note" data-material-record-field="note" maxlength="2000" ${disabled}>${esc(draft.note)}</textarea><button class="button" type="submit" ${disabled||!data?'disabled':''}>保存本人材料来源记录</button><p class="small muted">暂停AI期间仍可如实登记；事实或要求换版不声称外部内容自动更新。</p></form>${data?`<details><summary class="details-toggle">已保存原处与历史记录 ${data.records.length} 条</summary>${data.records.map(item=>`<article class="evidence-record"><strong>${esc(kinds[item.material_kind])} · ${esc(types[item.record_type])}</strong><p>${esc(item.note)}</p><p class="small">实际日期：${esc(item.occurred_at||'未知')} · 记录人：${esc(item.actor_name)}${item.stale?' · 当前依据待重核':''}</p><pre>${esc(item.excerpt)}</pre><p class="small muted">来源 ${esc(item.ref.source_id)} 第${esc(item.ref.start_line)}–${esc(item.ref.end_line)}行</p></article>`).join('')||'<p>尚无来源记录；真实办理未知。</p>'}</details><button class="button quiet" data-action="material-records-render" ${disabled||!data.records.length||['paused','handoff'].includes(matter.assistant_status)?'disabled':''}>另存材料办理声明与回执核对稿</button>`:''}</details></section>`;
  }
  return {markup,input,toggle,action,submit,clear:()=>records.clear(),isDirty:()=>[...records.values()].some(record=>record.draft.dirty&&(record.draft.note||record.draft.source_id||record.draft.occurred_at))};
}
