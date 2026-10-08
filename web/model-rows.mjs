const esc = value => String(value ?? '').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export function createModelRows({getState,request,render,toast,peekRows,appendRows,preserveArtifact}) {
  const records = new Map(),current = () => getState().current;
  const recordFor = id => {if (!records.has(id)) records.set(id,{open:false,data:null,selected:new Set(),reading:false,error:null});return records.get(id);};
  const owned = (id,epoch,selection) => current()?.id===id && getState().identityEpoch===epoch && getState().selection===selection;
  async function read() {
    const matter=current();if (!['expense','inventory'].includes(matter?.domain)) return;
    const record=recordFor(matter.id);if (record.reading) return;
    const epoch=getState().identityEpoch,selection=getState().selection;record.reading=true;record.error=null;render();
    try {
      const [data,fresh]=await Promise.all([request(`/api/matters/${encodeURIComponent(matter.id)}/rows/model-candidates`),request(`/api/matters/${encodeURIComponent(matter.id)}`)]);
      if (!owned(matter.id,epoch,selection)) return;
      if (data.version!==fresh.version || data.revision_no!==fresh.revision_no) throw {message:'读回期间事项已变化，请保留选择并重新核对。'};
      preserveArtifact();getState().current=fresh;record.data=data;record.selected=new Set([...record.selected].filter(id=>data.items.some(item=>item.id===id)));
    } catch(error) {if (owned(matter.id,epoch,selection)) record.error=error.message || '候选暂未读回；人工明细和原稿保留。';}
    finally {if (owned(matter.id,epoch,selection)) {record.reading=false;render();}}
  }
  function input(element) {
    if (element.dataset.modelRow===undefined || !current()) return false;
    const record=recordFor(current().id);if (element.checked) record.selected.add(element.dataset.modelRow);else record.selected.delete(element.dataset.modelRow);return true;
  }
  function append() {
    const matter=current(),record=records.get(matter?.id),data=record?.data;
    if (!data || !record.selected.size) {toast('请明确选择要核对的模型明细，默认不选入。');return;}
    if (data.stale || data.version!==matter.version || data.revision_no!==matter.revision_no) {toast('输入或基版已变化，请读取已保存候选与当前差异后核对；原编辑保留。');return;}
    const ids=new Set(peekRows().map(row=>row.id)),dimension=matter.domain==='expense'?'currency':'unit';
    const selected=data.items.filter(item=>record.selected.has(item.id)),added=selected.filter(item=>!ids.has(item.id)).map(item=>Object.fromEntries(['id','label','value',dimension,'period','source_id','source_position'].map(key=>[key,item[key]])));
    if (peekRows().length+added.length>200) {toast('整次追加会超过200行，未追加；请减少选择。');return;}
    if (!added.length) {toast('所选稳定ID已在当前明细，未重复添加或覆盖人工修改。');return;}
    if (!appendRows(added,data)) return;
    record.selected.clear();
    toast(`已把 ${added.length} 行模型候选追加到未保存表格；逐行核对后保存才计算。`);render();
  }
  function action(name) {if (name==='model-rows-read') {read();return true;}if (name==='model-rows-append') {append();return true;}return false;}
  function toggle(element) {if (element.isConnected && element.id==='model-rows-details' && current()) recordFor(current().id).open=element.open;}
  function markup(matter) {
    if (!['expense','inventory'].includes(matter.domain)) return '';
    const record=recordFor(matter.id),data=record.data,expense=matter.domain==='expense',dimension=expense?'currency':'unit',disabled=getState().busy||record.reading?'disabled':'';
    return `<details id="model-rows-details" ${record.open?'open':''}><summary class="details-toggle">从自然语言读取已保存模型明细</summary><p class="scope-note">先明确选择模型整理，模型可从已保存散句记录提出每轮最多8条明细；本入口只读回，不再调用。数值、币种／单位及期间必须在引用原文中明示，含义仍需人工核对。</p><button type="button" class="button quiet" data-action="model-rows-read" ${disabled}>读取已保存模型明细（不再调用模型）</button>${record.reading?'<p role="status">正在读取候选与当前基版，原稿和人工明细保留。</p>':''}${record.error?`<p class="small dirty-note">${esc(record.error)}</p>`:''}${data?`<p class="scope-note">${esc(data.scope)} · 批次输入修订 ${esc(data.basis_revision??'尚无')}／当前 ${esc(matter.revision_no)}。</p>${data.stale||data.version!==matter.version?'<p class="dirty-note">该批次或基版已变化，旧候选保留供核对，暂不能追加。</p>':''}<div class="table-scroll"><table class="rows-table"><thead><tr><th>选择</th><th>${expense?'费用名称':'物品'}</th><th>${expense?'金额':'数量'}</th><th>${expense?'币种':'单位'}</th><th>期间</th><th>原文出处</th></tr></thead><tbody>${data.items.map(item=>`<tr><td><label><input type="checkbox" data-model-row="${esc(item.id)}" ${record.selected.has(item.id)?'checked':''} ${disabled}>选入：${esc(item.label)}</label></td><td>${esc(item.label)}</td><td>${esc(item.value)}</td><td>${esc(item[dimension])}</td><td>${esc(item.period)}</td><td>${item.refs.map(ref=>`<small>来源 ${esc(ref.source_id)} 第${esc(ref.start_line)}–${esc(ref.end_line)}行</small><pre>${esc(ref.excerpt)}</pre>`).join('')}</td></tr>`).join('')||'<tr><td colspan="6">没有完整明细候选；缺项保留待核，可沿现有入口人工填写。</td></tr>'}</tbody></table></div><button type="button" class="button" data-action="model-rows-append" ${disabled||data.stale||data.version!==matter.version?'disabled':''}>把选中模型明细追加到未保存表格</button><p class="scope-note">不覆盖已有行，不自动跨来源去重；同一记录的新模型批次也须核对重复。追加不是确认，计算只用下方已人工保存明细。</p>`:''}</details>`;
  }
  return {read,input,toggle,action,markup,clear:()=>records.clear(),isDirty:()=>[...records.values()].some(record=>record.selected.size)};
}
