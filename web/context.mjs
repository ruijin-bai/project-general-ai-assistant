const esc = (value) => String(value ?? '').replace(/[&<>"']/g,(char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const clean = (note) => ({id:note.id,text:note.text || ''});
const clone = (value) => JSON.parse(JSON.stringify(value));
const draftOf = (data) => {const records = (data.notes || []).map(clean);return {records,original:clone(records),removed_ids:[],baseVersion:data.version,dirty:false};};
const scopeText = '仅本人明确输入的非敏感惯用写法或展示偏好；不代替身份、证照或批准，不自动用于模型或业务。删除不删除已有业务历史或曾显式复制的来源。';
export function createContextWorkspace({getState,request,post,render,toast,canAccess}) {
  let data = null,draft = null,error = null,reading = false,serial = 0;
  const selected = new Set();
  const own = (epoch,selection) => getState().identityEpoch === epoch && getState().selection === selection && getState().view === 'context' && canAccess();
  async function read({adoptVersion=false} = {}) {
    if (!canAccess()) return;const epoch = getState().identityEpoch,selection = getState().selection,requestSerial = ++serial;reading = true;
    if (getState().view === 'context') render();
    try {
      const fresh = await request('/api/context');if (!own(epoch,selection) || requestSerial !== serial) return;
      data = fresh;error = null;if (!draft?.dirty) draft = draftOf(fresh);else if (adoptVersion) draft.baseVersion = fresh.version;
      for (const id of selected) if (!draft.records.some((note) => note.id === id)) selected.delete(id);
    } catch (failure) {if (own(epoch,selection) && requestSerial === serial) {error = failure.message || '本人偏好暂未读回，未保存输入保留。';if ([401,403].includes(failure.status)) {data = null;draft = null;selected.clear();}}}
    finally {if (epoch === getState().identityEpoch && requestSerial === serial) {reading = false;if (getState().view === 'context') render();}}
  }
  function input(element) {
    const id = element.closest('[data-context-id]')?.dataset.contextId;if (!id || !draft) return false;
    if (element.dataset.contextSelect !== undefined) {if (element.checked) selected.add(id);else selected.delete(id);return true;}
    if (element.dataset.contextField !== 'text') return false;const row = draft.records.find((note) => note.id === id);if (row) row.text = element.value;draft.dirty = true;
    const label = globalThis.document?.querySelector('#context-saved-label');if (label) {label.textContent = '有未保存编辑，保存基版保持原值';label.classList.add('dirty-note');}return true;
  }
  async function save() {
    if (!canAccess() || !draft) return;
    const originals = new Map(draft.original.map((note) => [note.id,note.text])),upserts = draft.records.map(clean).filter((note) => originals.get(note.id) !== note.text);
    if (!upserts.length && !draft.removed_ids.length) {toast('本人偏好没有新的修改。');return;}
    if (upserts.some((note) => !note.text.trim() || note.text.length > 1000)) {toast('每条请写本人明确的非敏感偏好，不能为空且最多1000字；移除请点明确移除。');return;}
    const epoch = getState().identityEpoch,selection = getState().selection;
    await post('/api/context',{expected_version:draft.baseVersion,upserts,removed_ids:[...draft.removed_ids]},(fresh) => {if (!own(epoch,selection)) return;data = fresh;draft = draftOf(fresh);error = null;for (const id of selected) if (!draft.records.some((note) => note.id === id)) selected.delete(id);toast('本人偏好已保存，未加入事项事实、来源或模型输入。');});
  }
  async function copySelected() {
    const notes = (draft?.records || []).filter((note) => selected.has(note.id));if (!notes.length) {toast('请明确勾选准备复制的偏好。');return;}
    const text = notes.map((note) => note.text).join('\n'),epoch = getState().identityEpoch,selection = getState().selection;
    try {await request('/api/context');if (!own(epoch,selection)) return;await navigator.clipboard.writeText(text);toast('已复制明确勾选的本页文字；若要用于事项，请自行粘贴并核对来源。');}
    catch (failure) {if (own(epoch,selection)) {if ([401,403].includes(failure.status)) {data = null;draft = null;selected.clear();error = failure.message;render();}else toast('未完成复制，可手工选中本页文字。');}}
  }
  function action(name,button) {
    if (!name?.startsWith('context-')) return false;if (!canAccess()) return true;
    if (name === 'context-reload') {read({adoptVersion:true});return true;}
    if (name === 'context-copy') {copySelected();return true;}
    if (!draft) return true;
    if (name === 'context-add') {if (draft.records.length >= 12) return true;draft.records.push({id:globalThis.crypto.randomUUID(),text:''});}
    else if (name === 'context-remove') {const id = button.closest('[data-context-id]')?.dataset.contextId;if (draft.original.some((note) => note.id === id) && !draft.removed_ids.includes(id)) draft.removed_ids.push(id);draft.records = draft.records.filter((note) => note.id !== id);selected.delete(id);}else return false;
    draft.dirty = true;render();return true;
  }
  function markup() {
    if (!canAccess()) return '<section class="panel"><h1>本人偏好</h1><p>当前账号未授予本人准备偏好入口。</p></section>';
    const disabled = getState().busy || reading ? 'disabled' : '',saved = new Map((data?.notes || []).map((note) => [note.id,note]));
    return `<section class="panel personal-context-panel"><div class="panel-head"><div><div class="eyebrow">可选 · 本人明确输入</div><h1>本人偏好</h1></div><button class="button quiet" data-action="context-reload" ${disabled}>读取最新偏好与差异</button></div><p class="scope-note">${esc(data?.scope || scopeText)}</p><p class="small muted">可记录称呼惯用写法、日期展示方式、文稿排版习惯。请勿存密码、证件号码、健康情况等敏感资料；偏好不自动填入任何事项，也不自动送给模型。</p>${error ? `<p class="inline-error" role="alert">${esc(error)}</p>` : ''}${reading ? '<p class="small muted" role="status">正在读取本人明确偏好，当前草稿保留。</p>' : ''}${draft ? `<p class="small muted">当前保存基版 ${esc(draft.baseVersion)} · 最新已读取版本 ${esc(data.version)}。${data.version !== draft.baseVersion ? '本地输入保持原值；请明确读取差异并核对后再保存。' : ''}</p><form id="context-form">${draft.records.map((note,index) => {const server = saved.get(note.id);return `<article class="context-note" data-context-id="${esc(note.id)}"><div class="panel-head"><label class="auto-prepare-label"><input type="checkbox" data-context-select ${selected.has(note.id) ? 'checked' : ''} ${disabled}>明确选中此偏好用于复制</label><button type="button" class="button quiet" data-action="context-remove" ${disabled}>明确移除</button></div><label class="field-label" for="context-${esc(note.id)}">本人明确偏好 ${index + 1}（最多1000字）</label><textarea id="context-${esc(note.id)}" data-context-field="text" required maxlength="1000" ${disabled}>${esc(note.text)}</textarea>${server ? `<small class="muted">${esc(server.origin || '本人明确输入')} · 更新 ${esc(server.updated_at)}；首次记录 ${esc(server.recorded_at)}</small>${server.text !== note.text ? `<details><summary class="details-toggle">查看已保存原文，核对差异</summary><p class="original">${esc(server.text)}</p></details>` : ''}` : `<small class="dirty-note">${draft.original.some((old) => old.id === note.id) ? '服务器已移除此偏好；本地编辑保留，重新保存须明确核对。' : '本地新偏好，尚未保存。'}</small>`}</article>`;}).join('') || '<p class="small muted">尚无当前偏好。不要求填写，也不会从历史业务或模型中猜测。</p>'}${draft.removed_ids.length ? `<p class="small dirty-note">待明确移除 ${draft.removed_ids.length} 条当前偏好；未保存前服务端原记录仍在。不会删除正式业务历史或曾复制的来源。</p>` : ''}<div class="artifact-toolbar"><button type="button" class="button" data-action="context-add" ${disabled || draft.records.length >= 12 ? 'disabled' : ''}>添加本人偏好</button><button type="submit" class="button primary" ${disabled}>保存本人偏好</button><button type="button" class="button quiet" data-action="context-copy" ${disabled}>复制明确选中的本页文字</button><span id="context-saved-label" class="small ${draft.dirty ? 'dirty-note' : 'muted'}">${draft.dirty ? '有未保存编辑，保存基版保持原值' : '已保存或尚未填写'}</span></div></form>` : '<p class="small muted">请读取本人偏好后按需要维护。</p>'}</section>`;
  }
  return {read,input,save,action,markup,isDirty:() => Boolean(draft?.dirty),clear:() => {data = null;draft = null;error = null;reading = false;++serial;selected.clear();}};
}
