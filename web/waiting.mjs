const esc = (value) => String(value ?? '').replace(/[&<>"']/g,(char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const clone = (value) => JSON.parse(JSON.stringify(value));
const clean = (row) => ({id:row.id,who:row.who || '',reason:row.reason || '',next_step:row.next_step || '',due_at:row.due_at || null,state:row.state || 'waiting',note:row.note || ''});
const draftOf = (data) => {const records = (data.items || []).filter((row) => row.active !== false).map(clean);return {records,original:clone(records),removed_ids:[],baseVersion:data.version,dirty:false,open:false,historyOpen:false};};
function summary(data,busy=false,blocked=false) {
  const active = (data?.items || []).filter((row) => row.active !== false),pending = active.filter((row) => row.state === 'waiting');
  return `<p class="small muted">${data ? `${pending.length} 个本人已记录等待点；解除等待仅为本地记录。` : '尚未读取本地等待点，不推缺报或无人负责。'}</p><ul class="waiting-custom-list">${pending.map((row) => `<li><strong>${esc(row.who)}</strong> · ${esc(row.reason)}${row.due_now === true ? '<span class="dirty-note"> · 明确本地提醒时间已到</span>' : ''}${row.material_basis ? `<small class="${row.basis_stale?'dirty-note':'muted'}">材料节点原日期：${esc(row.material_basis.date)}${row.basis_stale?' · 依据变化，请核对旧待办并明确采用当前依据':''}</small>` : ''}${row.next_step ? `<small>下一步：${esc(row.next_step)}</small>` : ''}${row.due_at ? `<small>明示时间：${esc(row.due_at)}</small>` : ''}<button class="button quiet" type="button" data-action="waiting-followup" data-point-id="${esc(row.id)}" ${busy || blocked || row.basis_stale ? 'disabled' : ''} title="只准备当前已保存点的文字，不使用未保存等待编辑">准备跟进文字（已保存点）</button></li>`).join('') || '<li class="small muted">当前没有本人已记录的待等点；不表示业务已办结。</li>'}</ul>`;
}
function markup(data,draft,busy,error,blocked=false) {
  const disabled = busy ? 'disabled' : '';
  if (!data || !draft) return `<div class="waiting-workbench"><h3>本地等待点</h3><p class="small muted">${esc(error || '正在读取本人记录；不会自动催问或发送通知。')}</p><button class="button quiet" data-action="waiting-reload" ${disabled}>读取本地等待点</button></div>`;
  const saved = new Map((data.items || []).filter((row) => row.active !== false).map((row) => [row.id,row]));
  return `<div class="waiting-workbench"><h3>本人可维护的等待点</h3><div id="waiting-custom-summary">${summary(data,busy,blocked)}</div><p id="waiting-background-update" class="small dirty-note" role="status" hidden></p>${error ? `<p class="small dirty-note">${esc(error)}</p>` : ''}<details id="waiting-editor-details" ${draft.open ? 'open' : ''}><summary class="details-toggle">编辑／添加等待点</summary><p class="scope-note">${esc(data.scope || '仅本人本地等待记录，不分派、不授予权限、不发送通知。')} · 保存基版 ${esc(draft.baseVersion)}。暂停AI仍可维护；“已解除等待”不等于实际批准、执行或业务办结。</p><button class="button quiet" type="button" data-action="waiting-reload" ${disabled}>明确读取最新点与差异</button><form id="waiting-form">${draft.records.map((row,index) => {const meta = saved.get(row.id);return `<article class="waiting-edit-record" data-waiting-id="${esc(row.id)}"><div class="panel-head"><h3>等待点 ${index + 1}</h3><button class="button quiet" type="button" data-action="waiting-remove" ${disabled}>明确移除</button></div><label class="field-label" for="waiting-${esc(row.id)}-who">等待谁（本人也需写明，仅原述，不绑定账号）</label><input id="waiting-${esc(row.id)}-who" data-waiting-field="who" required maxlength="200" value="${esc(row.who)}" ${disabled}><label class="field-label" for="waiting-${esc(row.id)}-reason">实际等待原因</label><textarea id="waiting-${esc(row.id)}-reason" data-waiting-field="reason" required maxlength="2000" ${disabled}>${esc(row.reason)}</textarea><label class="field-label" for="waiting-${esc(row.id)}-next">下一步实际准备动作</label><input id="waiting-${esc(row.id)}-next" data-waiting-field="next_step" required maxlength="2000" value="${esc(row.next_step)}" ${disabled}><label class="field-label" for="waiting-${esc(row.id)}-due">本地提醒时间（可空，ISO时间须明确UTC偏移）</label><input id="waiting-${esc(row.id)}-due" data-waiting-field="due_at" maxlength="100" value="${esc(row.due_at)}" placeholder="2026-10-09T09:00:00+01:00；空为不设" ${disabled}><label class="field-label" for="waiting-${esc(row.id)}-state">本人等待记录</label><select id="waiting-${esc(row.id)}-state" data-waiting-field="state" ${disabled}><option value="waiting" ${row.state === 'waiting' ? 'selected' : ''}>仍在等待</option><option value="resolved" ${row.state === 'resolved' ? 'selected' : ''}>本人已解除此等待（非业务办结）</option></select><label class="field-label" for="waiting-${esc(row.id)}-note">实际备注（可空）</label><textarea id="waiting-${esc(row.id)}-note" data-waiting-field="note" maxlength="2000" ${disabled}>${esc(row.note)}</textarea>${meta ? `<small class="muted">实际记录者：${esc(meta.recorded_by_name || meta.recorded_by || '未记录')} · ${esc(meta.recorded_at)}</small>` : '<small class="dirty-note">本地新增，尚未保存</small>'}${draft.dirty && meta && JSON.stringify(clean(meta)) !== JSON.stringify(row) ? `<details><summary class="details-toggle">服务器已保存原述，供差异核对</summary><p class="small">${esc(meta.who)} · ${esc(meta.reason)}<br>下一步：${esc(meta.next_step)}<br>明示提醒：${esc(meta.due_at || '未设')} · ${meta.state === 'resolved' ? '本地已解除等待' : '仍在等待'}<br>${esc(meta.note)}</p></details>` : ''}${!meta && draft.original.some((item) => item.id === row.id) ? '<p class="small dirty-note">服务器已移除此点，本地编辑保留；重新保存前须核对实际范围。</p>' : ''}</article>`;}).join('') || '<p class="small muted">尚无本人记录点，按实际缺口添加。</p>'}<div class="artifact-toolbar"><button class="button" type="button" data-action="waiting-add" ${disabled || draft.records.length >= 20 ? 'disabled' : ''}>添加等待点</button><button class="button primary" type="submit" ${disabled}>保存增量等待记录</button><span id="waiting-saved-label" class="small ${draft.dirty ? 'dirty-note' : 'muted'}">${draft.dirty ? '有未保存编辑，基版保持原值' : '已保存或尚未填写'}</span></div></form></details><details id="waiting-history-details" ${draft.historyOpen ? 'open' : ''}><summary class="details-toggle">已移除记录 · 历史只读保留</summary>${(data.items || []).filter((row) => row.active === false).map((row) => `<p class="small muted"><strong>${esc(row.who)}</strong> · ${esc(row.reason)}<br>原下一步：${esc(row.next_step)} · ${esc(row.due_at || '未设提醒')}<br>${esc(row.note)}<br>实际记录者：${esc(row.recorded_by_name || row.recorded_by || '未记录')} · ${esc(row.recorded_at)}</p>`).join('') || '<p class="small muted">尚无已移除点，移除不会删除既往原述。</p>'}</details></div>`;
}
export function createWaitingWorkspace({getState,getElement,request,post,render,toast,refreshMatter,preserveArtifact=() => {},preserveBackground=() => false}) {
  const dataByMatter = new Map(),drafts = new Map(),errors = new Map(),followups = new Map();
  const current = () => getState().current;
  const own = (id,epoch,selection) => getState().identityEpoch === epoch && getState().selection === selection && current()?.id === id;
  const keepDom = () => Boolean(current() && getElement('.waiting-workbench') && (drafts.get(current().id)?.dirty || getElement('#waiting-editor-details')?.open || getElement('.waiting-followup') || globalThis.document?.activeElement?.closest('.waiting-workbench')));
  function noteVersion() {
    const matter = current(),draft = drafts.get(matter?.id),label = getElement('#waiting-background-update');if (!matter || !draft || !label) return;
    const data = dataByMatter.get(matter.id),version = Math.max(matter.version || 0,data?.version || 0),error = errors.get(matter.id);
    label.hidden = !error && version === draft.baseVersion;label.textContent = error || `后台已读到事项版本 ${version}；当前编辑与原基版 ${draft.baseVersion} 保留。请明确读取最新点与差异后核对。`;
    const summaryBox = getElement('#waiting-custom-summary');if (summaryBox) summaryBox.innerHTML = summary(data,getState().busy,current()?.model_disclosure_blocked || ['paused','handoff'].includes(current()?.assistant_status));
    for (const [key,record] of followups) if (key.startsWith(`${matter.id}:`) && record.data) {const label = getElement(`#waiting-followup-stale-${record.data.point_id}`);if (label) {label.hidden = matter.version === record.data.version;label.textContent = `当前事项版本 ${matter.version}，下方文字来自已保存点的版本 ${record.data.version}；快照已过时，请明确对照，不自动改文字或等待状态。`;}}
  }
  function accept(data,{background=false,adoptVersion=false} = {}) {
    if ((dataByMatter.get(data.id)?.version || 0) > data.version) return;
    dataByMatter.set(data.id,data);errors.delete(data.id);const draft = drafts.get(data.id);
    if (background && (keepDom() || preserveBackground())) {noteVersion();return;}
    if (!draft?.dirty) {const fresh = draftOf(data);fresh.open = draft?.open || false;fresh.historyOpen = draft?.historyOpen || false;drafts.set(data.id,fresh);}else if (adoptVersion) draft.baseVersion = data.version;
  }
  async function read(id,{background=false,adoptVersion=false} = {}) {
    if (current()?.id !== id) return;const state = getState(),epoch = state.identityEpoch,selection = state.selection;
    try {
      const data = await request(`/api/matters/${encodeURIComponent(id)}/waiting`);if (!own(id,epoch,selection)) return;
      accept(data,{background,adoptVersion});
      if (background && (keepDom() || preserveBackground())) {noteVersion();return;}
      render();
    } catch (error) {if (own(id,epoch,selection)) {errors.set(id,error.message || '本地等待点暂不可读取；其他编辑继续保留。');if (background && (keepDom() || preserveBackground())) noteVersion();else render();}}
  }
  function dirty(draft) {draft.dirty = true;const label = getElement('#waiting-saved-label');if (label) {label.textContent = '有未保存编辑，保存基版未自动推进';label.classList.add('dirty-note');}}
  const followupKey = (matterId,pointId) => `${matterId}:${pointId}`;
  const stopped = (matter) => matter.model_disclosure_blocked || ['paused','handoff'].includes(matter.assistant_status);
  const pointText = (point) => !point ? '当前读回中未找到此点，请明确核对。' : `${point.active === false ? '已移除（历史保留）' : point.state === 'resolved' ? '本人已解除等待，非业务办结' : '仍在等待'}\n对象：${point.who}\n原因：${point.reason}\n下一步：${point.next_step}\n本人明示时间：${point.due_at || '未设'}\n备注：${point.note || '无'}\n记录时间：${point.recorded_at || '未记录'}`;
  function followupMarkup(matter) {
    return [...followups.entries()].filter(([key]) => key.startsWith(`${matter.id}:`)).map(([,record]) => {
      const data = record.data,pointId = record.pointId,point = (dataByMatter.get(matter.id)?.items || []).find((row) => row.id === pointId),disabled = getState().busy || record.reading ? 'disabled' : '',stale = data && (matter.version !== data.version || matter.revision_no !== data.input_revision);
      return `<article class="waiting-followup"><h3>私人跟进文字 · 待本人使用</h3><p class="scope-note">仅从已保存等待点准备，不使用未保存等待编辑；不发送、不证明催办、受理或完成。${esc(data?.scope || '')}</p>${record.error ? `<p class="small dirty-note">${esc(record.error)}</p>` : ''}${data ? `<p class="small muted">文字基版 ${esc(data.version)}／输入修订 ${esc(data.input_revision)}；当前已读事项 ${esc(matter.version)}／修订 ${esc(matter.revision_no)}。</p><p id="waiting-followup-stale-${esc(pointId)}" class="small dirty-note" ${stale ? '' : 'hidden'}>文字快照已过时，正文保留；核对当前已保存等待后再采用当前基版。</p><details><summary class="details-toggle">当前已保存等待原述／状态</summary><pre class="waiting-point-snapshot">${esc(pointText(point))}</pre></details><label class="field-label" for="waiting-followup-text-${esc(pointId)}">本人编辑的私人文字</label><textarea id="waiting-followup-text-${esc(pointId)}" data-waiting-followup="${esc(pointId)}" maxlength="16000" ${disabled}>${esc(record.text)}</textarea><div class="artifact-toolbar"><button class="button" type="button" data-action="waiting-followup-save" data-point-id="${esc(pointId)}" ${disabled}>保存私人文字稿</button><button class="button quiet" type="button" data-action="waiting-followup-copy" data-point-id="${esc(pointId)}" ${disabled}>复制本人文字</button><button class="button quiet" type="button" data-action="waiting-followup-current" data-point-id="${esc(pointId)}" ${disabled}>读取当前等待与差异</button>${record.checkedCurrent ? `<button class="button quiet" type="button" data-action="waiting-followup-rebase" data-point-id="${esc(pointId)}" ${disabled}>核对当前等待后采用当前保存基版</button>` : ''}</div>${record.checkedCurrent ? `<pre class="waiting-point-snapshot">已明确读取：事项版本 ${esc(record.checkedCurrent.version)}／修订 ${esc(record.checkedCurrent.revision_no)}\n${esc(pointText(record.checkedCurrent.point))}</pre><p class="small muted">采用基版仅调整本文字稿保存版本，不换正文、不确认状态、不重新生成；若后台再变化仍须重核。</p>` : ''}<p id="waiting-followup-dirty-${esc(pointId)}" class="small ${record.dirty ? 'dirty-note' : 'muted'}">${record.dirty ? '有未保存私人文字，原基版保留' : '当前文字已读或已另存'}</p>${record.savedArtifact ? `<p class="small muted">已另存私有稿 ${esc(record.savedArtifact.artifact_id)} · v${esc(record.savedArtifact.version)}，可在稿件列表明确选择；原主编辑稿保持选中。</p>` : ''}` : '<p class="small muted">正在读取实际已保存等待点；失败时其他编辑仍保留。</p>'}</article>`;
    }).join('');
  }
  async function readFollowup(pointId) {
    const matter = current(),state = getState();if (!matter || state.busy || stopped(matter)) return;
    const key = followupKey(matter.id,pointId),existing = followups.get(key);
    if (existing?.data) {toast('此点已有私人文字快照；正文保留，可读取当前等待与差异后采用基版。');return;}
    if (existing?.reading) return;
    const record = existing || {pointId,data:null,text:'',dirty:false,reading:false,error:null,savedArtifact:null,checkedCurrent:null};followups.set(key,record);record.reading = true;record.error = null;
    const epoch = state.identityEpoch,selection = state.selection;render();
    try {const data = await request(`/api/matters/${encodeURIComponent(matter.id)}/waiting/${encodeURIComponent(pointId)}/followup`);if (!own(matter.id,epoch,selection)) return;record.data = data;record.text = data.content;record.pointId = data.point_id;record.dirty = true;}
    catch (error) {if (own(matter.id,epoch,selection)) record.error = error.message || '跟进文字暂不可准备，其他人工稿保留。';}
    finally {if (own(matter.id,epoch,selection)) {record.reading = false;render();}}
  }
  async function readFollowupCurrent(pointId) {
    const matter = current(),record = followups.get(followupKey(matter?.id,pointId));if (!matter || !record?.data || getState().busy || record.reading) return;
    const epoch = getState().identityEpoch,selection = getState().selection;record.reading = true;render();
    try {const fresh = await request(`/api/matters/${encodeURIComponent(matter.id)}`);if (!own(matter.id,epoch,selection)) return;const point = (fresh.waiting_points || []).find((row) => row.id === pointId);record.checkedCurrent = {version:fresh.version,revision_no:fresh.revision_no,point};record.error = point ? null : '当前本人记录中未找到原等待点，不能采用此保存基版。';await refreshMatter(matter.id,selection,fresh);}
    catch (error) {if (own(matter.id,epoch,selection)) record.error = error.message || '当前等待未读回，正文与基版保留。';}
    finally {if (own(matter.id,epoch,selection)) {record.reading = false;render();}}
  }
  async function saveFollowup(pointId) {
    const matter = current(),record = followups.get(followupKey(matter?.id,pointId));if (!matter || !record?.data || getState().busy) return;
    if (!record.text.trim() || record.text.length > 16000) {toast('私人文字须非空，最多16000字，超限不截断。');return;}
    const epoch = getState().identityEpoch,selection = getState().selection;preserveArtifact();let saved = false;
    await post(`/api/matters/${encodeURIComponent(matter.id)}/waiting/${encodeURIComponent(pointId)}/followup`,{expected_version:record.data.version,input_revision:record.data.input_revision,content:record.text},async (data) => {if (!own(matter.id,epoch,selection)) return;saved = true;record.savedArtifact = data;record.dirty = record.text !== data.content;record.error = null;record.checkedCurrent = null;toast('已另存私人文字稿，未发送、未催办，等待状态未改变。');await refreshMatter(matter.id,selection);});
    if (!saved && own(matter.id,epoch,selection)) {record.error = '保存未确认，正文保留；请读取当前等待与差异，明确采用当前保存基版后重试。';render();}
  }
  async function copyFollowup(pointId) {
    const matter = current(),record = followups.get(followupKey(matter?.id,pointId));if (!matter || !record?.data || getState().busy) return;const epoch = getState().identityEpoch,selection = getState().selection;
    try {await request(`/api/matters/${encodeURIComponent(matter.id)}/waiting`);if (!own(matter.id,epoch,selection)) return;try {await navigator.clipboard.writeText(record.text);if (own(matter.id,epoch,selection)) toast('已请求复制本人文字；未发送或记为催办。');}catch {const field = getElement(`#waiting-followup-text-${pointId}`);field?.focus();field?.select();toast('剪贴板不可用，已选中文字，可手工复制。');}}
    catch (error) {if (own(matter.id,epoch,selection)) {record.error = error.message || '当前权限未确认，正文保留，未请求剪贴板。';render();}}
  }
  function input(element) {
    if (element.dataset.waitingFollowup) {const record = followups.get(followupKey(current()?.id,element.dataset.waitingFollowup));if (record) {record.text = element.value;record.dirty = true;const label = getElement(`#waiting-followup-dirty-${record.pointId}`);if (label) {label.textContent = '有未保存私人文字，原基版保留';label.classList.add('dirty-note');}}return true;}
    const field = element.dataset.waitingField,id = element.closest('[data-waiting-id]')?.dataset.waitingId,draft = drafts.get(current()?.id);if (!field || !id || !draft) return false;
    const row = draft.records.find((item) => item.id === id);if (row) {row[field] = field === 'due_at' ? element.value || null : element.value;dirty(draft);}return true;
  }
  function toggle(element) {const draft = drafts.get(current()?.id);if (!draft || !element.isConnected) return;if (element.id === 'waiting-editor-details') draft.open = element.open;else if (element.id === 'waiting-history-details') draft.historyOpen = element.open;}
  async function save() {
    const matter = current(),draft = drafts.get(matter?.id);if (!draft) return;
    const original = new Map(draft.original.map((row) => [row.id,JSON.stringify(clean(row))])),upserts = draft.records.map(clean).filter((row) => original.get(row.id) !== JSON.stringify(row));
    if (!upserts.length && !draft.removed_ids.length) {toast('等待点没有新的修改。');return;}
    if (upserts.some((row) => row.due_at && !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/.test(row.due_at))) {toast('提醒时间须明确年月日、时间和UTC偏移；不确定时留空，不猜时区。');return;}
    const state = getState(),selection = state.selection,epoch = state.identityEpoch;
    await post(`/api/matters/${encodeURIComponent(matter.id)}/waiting`,{expected_version:draft.baseVersion,upserts,removed_ids:[...draft.removed_ids]},async (data) => {if (!own(matter.id,epoch,selection)) return;const fresh = draftOf(data);fresh.open = draft.open;fresh.historyOpen = draft.historyOpen;dataByMatter.set(matter.id,data);drafts.set(matter.id,fresh);errors.delete(matter.id);toast('等待点已保存，不发送通知、不唤醒AI，也不判业务办结。');await refreshMatter(matter.id,selection);});
  }
  function action(name,button) {
    if (!name?.startsWith('waiting-')) return false;const matter = current();if (!matter) return true;
    if (name === 'waiting-reload') {read(matter.id,{adoptVersion:true});return true;}
    const pointId = button.dataset.pointId;
    if (name === 'waiting-followup') {readFollowup(pointId);return true;}
    if (name === 'waiting-followup-current') {readFollowupCurrent(pointId);return true;}
    if (name === 'waiting-followup-save') {saveFollowup(pointId);return true;}
    if (name === 'waiting-followup-copy') {copyFollowup(pointId);return true;}
    if (name === 'waiting-followup-rebase') {const record = followups.get(followupKey(matter.id,pointId));if (record?.checkedCurrent?.point && !getState().busy) {record.data = {...record.data,version:record.checkedCurrent.version,input_revision:record.checkedCurrent.revision_no};record.checkedCurrent = null;record.error = null;record.dirty = true;toast('仅采用本人已核对保存基版，正文保持原值，等待状态未改变。');render();}return true;}
    const draft = drafts.get(matter.id);if (!draft) return true;
    if (name === 'waiting-add') {if (draft.records.length >= 20) return true;draft.records.push(clean({id:globalThis.crypto.randomUUID()}));draft.open = true;}
    else if (name === 'waiting-remove') {const id = button.closest('[data-waiting-id]')?.dataset.waitingId;if (draft.original.some((row) => row.id === id) && !draft.removed_ids.includes(id)) draft.removed_ids.push(id);draft.records = draft.records.filter((row) => row.id !== id);}else return false;
    dirty(draft);render();return true;
  }
  return {read,input,toggle,save,action,keepDom,noteVersion,
    ingest:(matter,options) => {if (Array.isArray(matter.waiting_points)) accept({id:matter.id,version:matter.version,items:matter.waiting_points,scope:'本人注明的等待对象、下一步与本地提醒；不授他人权限，不代表真实受理、业务办结、自动催办或消息送达'},options);},
    noteError:(message) => {if (current()) {errors.set(current().id,message);noteVersion();}},
    markup:(matter) => markup(dataByMatter.get(matter.id),drafts.get(matter.id),getState().busy,errors.get(matter.id),stopped(matter)) + followupMarkup(matter),isDirty:() => [...drafts.values()].some((draft) => draft.dirty) || [...followups.values()].some((record) => record.dirty),clear:() => {dataByMatter.clear();drafts.clear();errors.clear();followups.clear();}};
}
