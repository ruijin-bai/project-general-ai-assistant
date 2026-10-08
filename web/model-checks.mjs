const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function createModelChecks({getState,request,render,toast,adoptRead,getDraft,appendItems}) {
  const records=new Map(),current=()=>getState().current;
  const recordFor=id=>{if (!records.has(id)) records.set(id,{data:null,selected:new Set(),reading:false,error:null});return records.get(id);};
  const owned=(id,epoch,selection)=>current()?.id===id && getState().identityEpoch===epoch && getState().selection===selection;
  async function read() {
    const matter=current();if (!['hr','leave'].includes(matter?.domain)) return;
    const record=recordFor(matter.id);if (record.reading) return;
    const epoch=getState().identityEpoch,selection=getState().selection;record.reading=true;record.error=null;render();
    try {
      const [data,checks,fresh]=await Promise.all([request(`/api/matters/${encodeURIComponent(matter.id)}/checks/model-candidates`),request(`/api/matters/${encodeURIComponent(matter.id)}/checks`),request(`/api/matters/${encodeURIComponent(matter.id)}`)]);
      if (!owned(matter.id,epoch,selection)) return;
      if (data.version!==fresh.version || checks.version!==fresh.version || data.revision_no!==checks.revision_no) throw {message:'读回期间事项已变化；选择、编辑和基版保留，请重新核对。'};
      adoptRead(data,checks,fresh);record.data=data;
    } catch(error) {if (owned(matter.id,epoch,selection)) record.error=error.message||'材料核对候选暂未读回，人工清单和原稿保留。';}
    finally {if (owned(matter.id,epoch,selection)) {record.reading=false;render();}}
  }
  function input(element) {if (element.dataset.modelCheck===undefined || !current()) return false;const record=recordFor(current().id);if (element.checked) record.selected.add(element.dataset.modelCheck);else record.selected.delete(element.dataset.modelCheck);return true;}
  function append() {
    const matter=current(),record=records.get(matter?.id),data=record?.data,draft=getDraft();
    if (!data || !draft || !record.selected.size) {toast('请读回并明确选入要核对的要求；默认不选择。');return;}
    if (data.stale || data.version!==matter.version || data.revision_no!==matter.revision_no || draft.baseVersion!==matter.version) {toast('输入或清单基版已变化，原编辑保留，请读回当前差异并核对。');return;}
    if ([...record.selected].some(id=>!data.items.some(item=>item.id===id))) {toast('有旧批次选择，请清除后重新核对当前候选。');return;}
    const ids=new Set(draft.records.map(item=>item.id)),added=data.items.filter(item=>record.selected.has(item.id)&&!ids.has(item.id));
    if (draft.records.length+added.length>100) {toast('整次追加超过100项，未追加；已有清单保留。');return;}
    if (!added.length) {toast('所选同批次ID已在清单，未重复追加或覆盖人工编辑。');return;}
    appendItems(added);record.selected.clear();toast(`已追加 ${added.length} 项到未保存清单；全部仍待核，请逐项编辑核对后保存。`);render();
  }
  function action(name) {if (name==='model-checks-read') {read();return true;}if (name==='model-checks-append') {append();return true;}if (name==='model-checks-clear') {records.get(current()?.id)?.selected.clear();render();return true;}return false;}
  const refsMarkup=(refs,label)=>refs.length?refs.map(ref=>`<details><summary>${label} · 来源 ${esc(ref.source_id)} 第${esc(ref.start_line)}–${esc(ref.end_line)}行</summary><pre>${esc(ref.excerpt)}</pre></details>`).join(''):'<p class="small dirty-note">未匹配已有材料来源；保持待补，不造材料。</p>';
  function markup(matter) {
    if (!['hr','leave'].includes(matter.domain)) return '';
    const record=recordFor(matter.id),data=record.data,disabled=getState().busy||record.reading?'disabled':'',leave=matter.domain==='leave',label=leave?'签证休假返岗材料匹配':'人事核对';
    return `<section class="panel model-checks-panel"><div class="panel-head"><h2>模型材料核对候选</h2><button class="button quiet" data-action="model-checks-read" ${disabled}>读取已保存${label}候选（不再调用模型）</button></div><p class="scope-note">先明确选择模型整理，从已读要求和材料提出每轮最多8项候选。原文出处由程序核对，要求适用和材料匹配仍须人工核对；${leave?'不编国内证明或NIS适用／窗口，不判断签发、休假获准或可通行。':'不决定录用、工资、处罚或资格。'}</p>${record.reading?'<p role="status">正在读回候选与当前清单，原编辑保留。</p>':''}${record.error?`<p class="dirty-note">${esc(record.error)}</p>`:''}${data?`<p class="scope-note">${esc(data.scope)} · 输入修订 ${esc(data.basis_revision??'尚无')}／当前 ${esc(matter.revision_no)}。</p>${data.stale||data.version!==matter.version?'<p class="dirty-note">批次输入或基版已变化，保留历史供核对，暂不能追加。</p>':''}${data.items.map(item=>`<article class="evidence-record"><label><input type="checkbox" data-model-check="${esc(item.id)}" ${record.selected.has(item.id)?'checked':''} ${disabled}>选入：${esc(item.requirement_text)}</label><p class="small">本地核对：待核 · 正式适用、合规及业务决定：未知</p><p>${esc(item.note)}</p>${leave&&item.model_note?`<details><summary>原模型自由备注（不作计算或效力依据）</summary><pre>${esc(item.model_note)}</pre><p class="small dirty-note">自由备注保留供核对，不选入核对清单；日期先后、适用或效力不得据此采用。</p></details>`:''}${refsMarkup(item.requirement_refs,'要求原文')}${refsMarkup(item.provided_refs,'材料匹配候选原文')}</article>`).join('')||'<p>没有有出处的要求候选；可沿下方入口人工维护，不猜标准。</p>'}<div class="artifact-toolbar"><button class="button" data-action="model-checks-append" ${disabled||data.stale||data.version!==matter.version?'disabled':''}>把选中候选追加到未保存核对清单</button><button class="button quiet" data-action="model-checks-clear" ${disabled}>清除${label}候选选择</button></div><p class="scope-note">仅追加新ID，不覆盖现有人工项。材料引用只是匹配候选，全部仍待核；核对保存与${leave?'正式适用、签发或出入境决定':'正式人事决定'}分开。</p>`:''}</section>`;
  }
  return {read,input,action,markup,clear:()=>records.clear()};
}
