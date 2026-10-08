import {continuationSummary} from './continuation.mjs';
import {conversationMarkup} from './conversation.mjs';
import {createPrivateContext} from './private-context.mjs';
import {createRelatedTasks} from './related-tasks.mjs';
import {adoptionSelectionMarkup,adoptionInput,adoptionPayload,adoptionHistoryMarkup} from './menu-adoptions.mjs';
import {createMenuModelReview} from './menu-model-review.mjs';
import {feedbackReviewMarkup} from './menu-feedback-review.mjs';
import {createModelJourney} from './model-journey.mjs';
import {createRowReports} from './row-reports.mjs';
import {createExpenseJourney} from './expense-journey.mjs';
import {createMealSummaries} from './meal-summaries.mjs';
import {createMaterialNodes} from './material-nodes.mjs';
import {createMaterialRecords} from './material-records.mjs';
import {createAttachmentsWorkspace} from './attachments.mjs';
import {createReminderPreferences} from './reminders.mjs';
import {createSourceImpact} from './source-impact.mjs';
import {createModelPreview} from './model-preview.mjs';
import {createTabularWorkspace} from './tabular.mjs';
import {createModelRows} from './model-rows.mjs';
import {createModelChecks} from './model-checks.mjs';
import {createExtractionWorkspace} from './extraction.mjs';
import {createSourceLifecycle,sourceStateLabels} from './source-lifecycle.mjs';
import {createReadingsWorkspace} from './readings.mjs';
import {createContextWorkspace} from './context.mjs';
import {createWaitingWorkspace} from './waiting.mjs';
import {ownerSharingMarkup, receivedSharingMarkup, createShareDraft, shareProjection} from './sharing.mjs';
import {journeyMarkup, createJourneyDraft, journeyDirty, incrementalChanges, mealRecord, materialRecord, materialLabels} from './journey.mjs';
import {structureMarkup, createStructureDraft, structureChanges, makeStructureRecord, sourceLines, checksMarkup, cleanCheckRecord, createChecksDraft, checksChanges} from './structured.mjs';
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const trackStatusLabels = {unknown:'待核 / 未登记',pending:'待处理',candidate:'候选，待核',confirmed:'已登记，须核依据'};
const statusLabels = {idle:'待整理',processing:'正在整理',waiting:'等待依据',paused:'已暂停',handoff:'人工接手'};
const sourceLabels = {user_text:'本人文字',wechat_text:'授权微信文字',oral_note:'口头摘录',excel_excerpt:'授权 Excel 摘录'};
const state = {session:null,identityEpoch:0,view:'workspace',authTab:'login',catalog:{},items:[],menus:[],menuFeedback:new Map(),feedbackDrafts:new Map(),feedbackOpen:null,accounts:[],accountDraft:{username:'',display_name:'',capabilities:[]},activationToken:null,current:null,loading:true,busy:false,selection:0,error:null,difference:null,activityOpen:false,search:'',domainFilter:'',drafts:new Map(),composer:{goal_text:'',domain:'auto',source_kind:'user_text',reported_by:'',auto_prepare:true,initial_mode:null,optionsOpen:false}};
let detailController, pollTimer, toastTimer;
const waitingUI = createWaitingWorkspace({getState:() => state,getElement:$,request:(...args) => request(...args),post:(...args) => auxiliaryWrite(...args),render:() => render(),toast:(message) => toast(message),refreshMatter:syncWaitingMatter,preserveArtifact:() => {const artifact = editingArtifact(),draft = draftFor();if (artifact && draft.artifactText === null) draft.selectedArtifactId = artifact.id;},preserveBackground:() => keepStructureDom() || keepJourneyDom() || keepChecksDom() || keepShareDom() || readingsUI.keepDom() || sourceUI.keepDom() || extractionUI.keepDom() || tabularUI.keepDom() || modelPreviewUI.keepDom() || (sourceImpactUI.keepDom() || attachmentsUI.keepDom())});
const remindersUI = createReminderPreferences({getState:() => state,getElement:$,request:(...args) => request(...args),post:(...args) => auxiliaryWrite(...args),toast:(message) => toast(message),canAccess:() => canCreateMatter(),refreshList:() => renderList()});
const contextUI = createContextWorkspace({getState:() => state,request:(...args) => request(...args),post:(...args) => auxiliaryWrite(...args),render:() => render(),toast:(message) => toast(message),canAccess:() => canCreateMatter()});
const readingsUI = createReadingsWorkspace({getState:() => state,getElement:$,request:(...args) => request(...args),post:(...args) => auxiliaryWrite(...args),render:() => render(),toast:(message) => toast(message),refreshMatter:syncReadingsMatter,preserveBackground:() => waitingUI.keepDom() || keepShareDom() || sourceUI.keepDom() || extractionUI.keepDom() || tabularUI.keepDom() || modelPreviewUI.keepDom() || (sourceImpactUI.keepDom() || attachmentsUI.keepDom()),preserveArtifact:() => {const artifact = editingArtifact(),draft = draftFor();if (artifact && draft.artifactText === null) draft.selectedArtifactId = artifact.id;}});
const sourceUI = createSourceLifecycle({getState:() => state,getElement:$,request:(...args) => request(...args),post:(...args) => auxiliaryWrite(...args),render:() => render(),toast:(message) => toast(message),refreshMatter:syncSourceStateMatter,preserveBackground:() => waitingUI.keepDom() || readingsUI.keepDom() || keepStructureDom() || keepJourneyDom() || keepChecksDom() || keepShareDom() || extractionUI.keepDom() || tabularUI.keepDom() || modelPreviewUI.keepDom() || (sourceImpactUI.keepDom() || attachmentsUI.keepDom())});
const extractionUI = createExtractionWorkspace({getState:() => state,getElement:$,request:(...args) => request(...args),post:(...args) => auxiliaryWrite(...args),render:() => render(),toast:(message) => toast(message),applyMatter:(matter) => {state.current = matter;waitingUI.ingest(matter);render();schedulePoll();refreshList();},preserveBackground:() => sourceUI.keepDom() || waitingUI.keepDom() || readingsUI.keepDom() || keepStructureDom() || keepJourneyDom() || keepChecksDom() || keepShareDom()});
const tabularUI = createTabularWorkspace({getState:() => state,getElement:$,request:(...args) => request(...args),render:() => render(),toast:(message) => toast(message),peekRows:() => draftFor()?.rows ?? state.current?.rows ?? [],appendRows:(rows,data) => {
  const matter = state.current,draft = draftFor();if (!matter || matter.version !== data.version || matter.revision_no !== data.revision_no || sourceUI.modelBlocked(matter)) {toast('当前来源或版本已变化，新候选暂不能追加；原人工编辑保留。');return false;}
  if (draft.rows !== null && (draft.rowsBaseVersion ?? matter.version) !== data.version) {toast('现有人工明细草稿来自另一事项版本；请明确读取服务器差异并核对，不能混入新版本候选。');return false;}
  ensureRows().push(...rows);draft.rowsOpen = true;return true;
}});
const modelRowsUI = createModelRows({getState:() => state,request:(...args) => request(...args),render:() => render(),toast:(message) => toast(message),peekRows:() => draftFor()?.rows ?? state.current?.rows ?? [],preserveArtifact:() => {const artifact = editingArtifact(),draft = draftFor();if (artifact && draft.artifactText === null) draft.selectedArtifactId = artifact.id;},appendRows:(rows,data) => {
  const matter = state.current,draft = draftFor();if (!matter || matter.version !== data.version || matter.revision_no !== data.revision_no || sourceUI.modelBlocked(matter)) {toast('当前来源或版本已变化，新候选暂不能追加；原人工编辑保留。');return false;}
  if (draft.rows !== null && (draft.rowsBaseVersion ?? matter.version) !== data.version) {toast('现有人工明细草稿来自另一版本，请先读取差异并核对；原编辑保留。');return false;}
  ensureRows().push(...rows);draft.rowsOpen = true;return true;
}});
const modelChecksUI = createModelChecks({getState:() => state,request:(...args) => request(...args),render:() => render(),toast:(message) => toast(message),getDraft:() => state.checksDrafts?.get(state.current?.id),adoptRead:(data,checks,fresh) => {
  const artifact = editingArtifact(),human = draftFor();if (artifact && human.artifactText === null) human.selectedArtifactId = artifact.id;
  state.current = fresh;checksState();state.checks.set(fresh.id,checks);state.checksErrors.delete(fresh.id);
  if (!state.checksDrafts.get(fresh.id)?.dirty) state.checksDrafts.set(fresh.id,createChecksDraft(checks));
},appendItems:(items) => {const draft = state.checksDrafts.get(state.current.id);draft.records.push(...items.map(cleanCheckRecord));markChecksDirty(draft);}});
const modelPreviewUI = createModelPreview({getState:() => state,getElement:$,request:(...args) => request(...args),render:() => render()});
const sourceImpactUI = createSourceImpact({getState:() => state,getElement:$,request:(...args) => request(...args),render:() => render()});
const modelJourneyUI = createModelJourney({getState:()=>state,request:(...args)=>request(...args),render:()=>render(),toast:message=>toast(message),preserveArtifact:()=>{const artifact=editingArtifact(),draft=draftFor();if(artifact&&draft.artifactText===null)draft.selectedArtifactId=artifact.id;},getPlan:()=>state.journeyDrafts?.get(state.current?.id)?.plan,adoptRead:(matter,data)=>{state.current=matter;journeyState();const old=state.journeyDrafts.get(matter.id),fresh=createJourneyDraft(data,matter);fresh.ui=old?.ui||{};for(const key of ['plan','meals','materials','movement','link'])if(old?.[key]?.dirty)fresh[key]=old[key];state.journeys.set(matter.id,data);state.journeyDrafts.set(matter.id,fresh);}});
const menuModelUI = createMenuModelReview({getState:()=>state,request:(...args)=>request(...args),post:(...args)=>auxiliaryWrite(...args),render:()=>render(),toast:message=>toast(message),getReplyDraft:cookResponseDraft,adoptFeedback:(id,items)=>{state.menuFeedback.set(id,items);for(const item of items){const menu=state.menus.find(menu=>menu.id===id),draft=state.menuResponseDrafts?.get(`${id}:${item.id}`);if(draft&&!draft.response_text){draft.expected_publication_version=menu.version;draft.feedback_correction_count=item.correction_count??0;}}}});
const expenseJourneyUI = createExpenseJourney({getState:()=>state,request:(...args)=>request(...args),post:(...args)=>auxiliaryWrite(...args),render:()=>render(),toast:message=>toast(message),preserveArtifact:()=>{const artifact=editingArtifact(),draft=draftFor();if(artifact&&draft.artifactText===null)draft.selectedArtifactId=artifact.id;},refreshMatter:syncWaitingMatter,getPurposeDraft:()=>draftFor()?.factChanges?.purpose});
const privateContextUI = createPrivateContext({getState:()=>state,request:(...args)=>request(...args),post:(...args)=>auxiliaryWrite(...args),render:()=>render(),toast:message=>toast(message),refreshMatter:syncWaitingMatter});
const relatedTasksUI = createRelatedTasks({getState:()=>state,request:(...args)=>request(...args),post:(...args)=>auxiliaryWrite(...args),render:()=>render(),toast:message=>toast(message),refreshMatter:syncWaitingMatter});
const rowReportsUI = createRowReports({getState:()=>state,request:(...args)=>request(...args),post:(...args)=>auxiliaryWrite(...args),render:()=>render(),toast:message=>toast(message),preserveArtifact:()=>{const artifact=editingArtifact(),draft=draftFor();if(artifact&&draft.artifactText===null) draft.selectedArtifactId=artifact.id;},refreshMatter:syncWaitingMatter});
const mealSummariesUI = createMealSummaries({getState:()=>state,request:(...args)=>request(...args),post:(...args)=>auxiliaryWrite(...args),render:()=>render(),toast:message=>toast(message),preserveArtifact:()=>{const artifact=editingArtifact(),draft=draftFor();if(artifact&&draft.artifactText===null)draft.selectedArtifactId=artifact.id;},refreshMatter:syncWaitingMatter});
const materialNodesUI = createMaterialNodes({getState:()=>state,request:(...args)=>request(...args),post:(...args)=>auxiliaryWrite(...args),render:()=>render(),toast:message=>toast(message),preserveArtifact:()=>{const artifact=editingArtifact(),draft=draftFor();if(artifact&&draft.artifactText===null) draft.selectedArtifactId=artifact.id;},refreshMatter:syncWaitingMatter,refreshWaiting:id=>waitingUI.read(id)});
const materialRecordsUI = createMaterialRecords({getState:()=>state,request:(...args)=>request(...args),post:(...args)=>auxiliaryWrite(...args),render:()=>render(),toast:message=>toast(message),preserveArtifact:()=>{const artifact=editingArtifact(),draft=draftFor();if (artifact&&draft.artifactText===null) draft.selectedArtifactId=artifact.id;},refreshMatter:syncWaitingMatter});
const attachmentsUI = createAttachmentsWorkspace({getState:() => state,getElement:$,request:(...args) => request(...args),post:(...args) => auxiliaryWrite(...args),render:() => render(),toast:(message) => toast(message),refreshMatter:syncWaitingMatter,ensureIdentity:() => ensureIdentity(),preserveArtifact:() => {const artifact = editingArtifact(),draft = draftFor();if (artifact && draft.artifactText === null) draft.selectedArtifactId = artifact.id;}});
async function syncSourceStateMatter(id,selection) {
  const epoch = state.identityEpoch;
  try {const matter = await request(`/api/matters/${encodeURIComponent(id)}`);if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === id) {state.current = matter;waitingUI.ingest(matter);if (sharingAllowed()) await readOwnShares(id);render();refreshList();}}
  catch (error) {if (epoch === state.identityEpoch && selection === state.selection) state.error = {message:'来源引用控制已保存，最新事项暂未读回；原稿和其他编辑保留，请明确读取后继续。',status:error.status};}
}
async function syncReadingsMatter(id,selection) {
  const epoch = state.identityEpoch;
  try {const matter = await request(`/api/matters/${encodeURIComponent(id)}`);if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === id) {state.current = matter;waitingUI.ingest(matter);await readingsUI.read(id);render();refreshList();}}
  catch (error) {if (epoch === state.identityEpoch && selection === state.selection) state.error = {message:'读数操作已保存，事项暂未读回；请明确读取后继续，其他人工编辑保留。',status:error.status};}
}
async function syncWaitingMatter(id,selection,providedMatter) {
  const epoch = state.identityEpoch;
  try {const matter = providedMatter || await request(`/api/matters/${encodeURIComponent(id)}`);if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === id) {state.current = matter;waitingUI.ingest(matter);render();refreshList();}}
  catch (error) {if (epoch === state.identityEpoch && selection === state.selection) state.error = {message:'等待记录已保存，最新事项暂未读回；其他编辑保留，请明确读取后继续。',status:error.status};}
}

function formatTime(value) {
  if (!value) return '时间未记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN',{timeZone:'Africa/Lagos',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
}
function draftFor(matter = state.current) {
  if (!matter) return null;
  if (!state.drafts.has(matter.id)) state.drafts.set(matter.id,{factsOpen:false,factChanges:{},artifactText:null,artifactId:null,artifactVersion:null,selectedArtifactId:null,prepareMode:'template',publicNotes:'',sourceText:'',sourceKind:'user_text',reportedBy:'',sourcesOpen:false,evidenceOpen:false,evidence:{track:'',record_type:'statement',reported_by:'',occurred_at:'',source_id:'',source_position:'',text:''},rowsOpen:false,rows:null,messageText:'',messageMode:null,toolsOpen:false});
  return state.drafts.get(matter.id);
}
function sharingState() {state.shareDrafts ||= new Map();state.ownShares ||= new Map();state.shareRecipients ||= [];state.ownShareErrors ||= new Map();state.receivedShares ||= [];}
function sharingAllowed() {return authenticated() && multiUser() && canCreateMatter();}
function shareDraftFor(matter = state.current,artifact = editingArtifact(matter)) {sharingState();if (!matter || !artifact) return null;const key = `${matter.id}:${artifact.id}`;if (!state.shareDrafts.has(key)) state.shareDrafts.set(key,createShareDraft(matter,artifact));return state.shareDrafts.get(key);}
function ownSharingMarkupFor(matter,artifact) {if (!sharingAllowed() || !artifact) return '';sharingState();return ownerSharingMarkup({matter,artifact,draft:shareDraftFor(matter,artifact),recipients:state.shareRecipients,items:state.ownShares.get(matter.id) || [],open:draftFor(matter).shareOpen,busy:state.busy,error:state.ownShareErrors.get(matter.id)});}
function keepShareDom() {return sharingAllowed() && Boolean($('#share-form')) && (draftFor()?.shareOpen || shareDraftFor()?.dirty || document.activeElement?.closest('#share-form'));}
function notifyShareBackground() {const draft = shareDraftFor(),label = $('#share-background-update');if (!draft || !label) return;label.hidden = state.current.version === draft.baseMatterVersion;label.textContent = `当前事项已读到版本 ${state.current.version}；分享文字和稿件版本 ${draft.artifact_version} 保留原值。提交前请读取当前事项与分享记录核对。`;}
async function readShareRecipients() {
  if (!sharingAllowed()) return;
  sharingState();if (state.shareRecipientsLoading) return;state.shareRecipientsLoading = true;const epoch = state.identityEpoch;
  try {const result = await request('/api/share-recipients');if (epoch !== state.identityEpoch) return;state.shareRecipients = result.items || [];state.shareRecipientsLoaded = true;render();}
  catch (error) {if (epoch === state.identityEpoch && state.current) {state.shareRecipientsLoaded = true;state.ownShareErrors.set(state.current.id,error.message || '活跃经办暂未读回，输入保留。');render();}}
  finally {if (epoch === state.identityEpoch) state.shareRecipientsLoading = false;}
}
async function readOwnShares(id,{background=false} = {}) {
  if (!sharingAllowed() || state.current?.id !== id) return;
  sharingState();const epoch = state.identityEpoch,selection = state.selection;
  try {
    const result = await request(`/api/matters/${encodeURIComponent(id)}/shares`);
    if (epoch !== state.identityEpoch || selection !== state.selection || state.current?.id !== id) return;
    state.ownShares.set(id,result.items || []);state.ownShareErrors.delete(id);
    if (background && (keepStructureDom() || keepJourneyDom() || keepChecksDom() || keepShareDom() || waitingUI.keepDom() || readingsUI.keepDom() || sourceUI.keepDom() || extractionUI.keepDom() || tabularUI.keepDom() || modelPreviewUI.keepDom() || (sourceImpactUI.keepDom() || attachmentsUI.keepDom()))) {notifyShareBackground();waitingUI.noteVersion();readingsUI.noteVersion();sourceUI.noteVersion();extractionUI.noteVersion();tabularUI.noteVersion();modelPreviewUI.noteVersion();sourceImpactUI.noteVersion();attachmentsUI.noteVersion();return;}
    render();
  } catch (error) {if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === id) {if ([403,404].includes(error.status)) state.ownShares.set(id,[]);state.ownShareErrors.set(id,error.message || '本人分享记录暂不可读取。');if (background && (keepShareDom() || waitingUI.keepDom() || readingsUI.keepDom() || sourceUI.keepDom() || extractionUI.keepDom() || tabularUI.keepDom() || modelPreviewUI.keepDom() || (sourceImpactUI.keepDom() || attachmentsUI.keepDom()))) {notifyShareBackground();waitingUI.noteVersion();readingsUI.noteVersion();sourceUI.noteVersion();extractionUI.noteVersion();tabularUI.noteVersion();modelPreviewUI.noteVersion();sourceImpactUI.noteVersion();attachmentsUI.noteVersion();}else render();}}
}
async function createSharing() {
  const matter = state.current,draft = shareDraftFor();if (!sharingAllowed() || !draft) return;
  if (!draft.recipient_id || !state.shareRecipients.some((recipient) => recipient.id === draft.recipient_id)) {toast('请明确选择当前活跃的综合经办。');return;}
  if (!draft.acknowledged || !draft.title.trim() || !draft.content.trim() || draft.content.length > 20000) {toast('请核对实际分享文字、标题和范围确认；最多20000字，不自动截断。');return;}
  const selection = state.selection;
  auxiliaryWrite(`/api/matters/${encodeURIComponent(matter.id)}/shares`,{expected_version:draft.baseMatterVersion,artifact_id:draft.artifact_id,artifact_version:draft.artifact_version,recipient_id:draft.recipient_id,title:draft.title.trim(),content:draft.content,dependency_refs:[...draft.dependency_refs]},async () => {
    draft.dirty = false;draft.acknowledged = false;
    toast('已在本工作区明确分享固定文字给选定经办；未分享私人来源或外部送达。');
    const fresh = await request(`/api/matters/${encodeURIComponent(matter.id)}`);
    if (selection === state.selection && state.current?.id === matter.id) {state.current = fresh;draft.baseMatterVersion = fresh.version;await readOwnShares(matter.id);refreshList();}
  });
}
function clearReceivedShare(id) {sharingState();state.receivedShares = state.receivedShares.filter((share) => share.id !== id);if (state.receivedShare?.id === id) state.receivedShare = null;}
async function refreshReceivedShares() {
  if (!multiUser() || !['clerk','cook'].some(hasCapability)) return;
  sharingState();const epoch = state.identityEpoch;
  try {const result = await request('/api/shares');if (epoch !== state.identityEpoch) return;state.receivedShares = (result.items || []).map(shareProjection);state.receivedShareError = null;if (state.receivedShare && !state.receivedShares.some((item) => item.id === state.receivedShare.id)) state.receivedShare = null;if (state.view === 'shares') render();}
  catch (error) {if (epoch === state.identityEpoch) {if ([403,404].includes(error.status)) {state.receivedShares = [];state.receivedShare = null;}state.receivedShareError = error.message || '当前共享材料不可读取。';if (state.view === 'shares') render();}}
}
async function loadReceivedShare(id,{copy=false} = {}) {
  if (!multiUser() || !['clerk','cook'].some(hasCapability)) return;
  const epoch = state.identityEpoch,selection = ++state.selection;
  state.receivedShare = null;state.receivedShareError = null;render();
  try {
    const result = shareProjection(await request(`/api/shares/${encodeURIComponent(id)}`));if (epoch !== state.identityEpoch || selection !== state.selection || state.view !== 'shares') return;
    state.receivedShare = result;render();
    if (copy) {try {await navigator.clipboard.writeText(result.content);toast('已复核当前权限并复制固定文字，未外发或归入私人来源。');}catch {toast('无法访问剪贴板，请在只读文字区手工选中复制。');}}
  } catch (error) {if (epoch === state.identityEpoch && selection === state.selection) {if ([403,404].includes(error.status)) clearReceivedShare(id);state.receivedShareError = error.message || '此投影已不可读取，其他私人编辑保留。';if (state.view === 'shares') render();}}
}
async function downloadReceivedShare(format) {
  const share = state.receivedShare;if (!share || !['txt','md'].includes(format)) return;
  const epoch = state.identityEpoch,selection = state.selection;
  try {
    await ensureIdentity();if (epoch !== state.identityEpoch || selection !== state.selection) return;
    const response = await fetch(`/api/shares/${encodeURIComponent(share.id)}/download?version=${encodeURIComponent(share.version)}&format=${format}`,{credentials:'same-origin'});
    if (!response.ok) {let body = {};try {body = await response.json();}catch {}throw {status:response.status,message:body.error?.message || body.message || '当前共享授权下不可下载此固定版本。'};}
    const blob = await response.blob();if (epoch !== state.identityEpoch || selection !== state.selection || state.view !== 'shares') return;
    const url = URL.createObjectURL(blob),link = document.createElement('a');link.href = url;link.download = `共享准备文字-v${share.version}.${format}`;link.click();setTimeout(() => URL.revokeObjectURL(url),1000);toast(`已请求浏览器下载共享固定版本 ${share.version}，不代表外部送达或业务接受。`);
  } catch (error) {if (epoch === state.identityEpoch) {if ([403,404].includes(error.status)) clearReceivedShare(share.id);if (error.status === 401) {clearPrivate({authenticated:false,mode:state.session?.mode});state.session = await request('/api/session');}state.receivedShareError = error.message || '下载未完成，当前投影权限须重核。';if (state.view === 'shares' || !authenticated()) render();}}
}

function renderSourceSearch() {
  const container = $('#source-search-results'),button = $('#search-sources');if (!container || !button) return;
  button.hidden = !canCreateMatter();button.disabled = Boolean(state.sourceSearch?.loading);
  const result = authenticated() && canCreateMatter() ? state.sourceSearch : null;container.hidden = !result;
  if (!result) {container.innerHTML = '';return;}
  container.innerHTML = `<div class="panel-head"><h2>本人已保存原文 · “${escapeHtml(result.query)}”</h2><button class="button quiet" data-source-search-close>收起结果</button></div><p class="scope-note">${escapeHtml(result.scope || '仅检索本人本工作区已保存来源，不访问外部知识、群聊或在线表，不发送模型。')}</p>${result.loading ? '<p class="small muted">正在检索已有来源…</p>' : result.error ? `<p class="small dirty-note">${escapeHtml(result.error)}</p>` : `<p class="small muted">本次返回 ${result.items?.length || 0} 个真实行片段。${result.limited ? '已达返回范围，更多结果请细化关键词。' : '未匹配不代表材料不存在或未提供。'}</p><div class="source-search-list">${(result.items || []).map((hit,index) => `<button class="source-search-hit" data-source-search-hit="${index}"><strong>${escapeHtml(hit.goal_text)}</strong><small>${escapeHtml(domainLabel(hit.domain))} · ${escapeHtml(sourceLabels[hit.kind] || hit.kind)} · ${escapeHtml(hit.reported_by || '陈述人待核')} · 第 ${escapeHtml(hit.start_line)}–${escapeHtml(hit.end_line)} 行</small><span>${escapeHtml(hit.excerpt)}</span>${hit.excerpt_truncated ? '<small>此片段已截短，请打开原事项读完整原文。</small>' : ''}</button>`).join('')}</div>`}`;
}
async function searchOwnSources() {
  if (!canCreateMatter()) return;
  const query = $('#search').value.trim();if (!query || query.length > 120) {toast('请输入1–120字的本人原文关键词。');return;}
  const epoch = state.identityEpoch,serial = state.sourceSearchSerial = (state.sourceSearchSerial || 0) + 1;
  state.sourceSearch = {query,loading:true,items:[]};renderSourceSearch();
  try {const result = await request(`/api/sources/search?q=${encodeURIComponent(query)}`);if (epoch === state.identityEpoch && serial === state.sourceSearchSerial) {state.sourceSearch = {...result,query,loading:false};renderSourceSearch();}}
  catch (error) {if (epoch === state.identityEpoch && serial === state.sourceSearchSerial) {state.sourceSearch = {query,loading:false,items:[],error:error.message || '本人来源暂不可检索，当前编辑保留。'};renderSourceSearch();}}
}
function sourceHitMarkup(matter) {
  const hit = state.sourceHit;if (!hit || hit.matter_id !== matter.id) return '';
  return `<div class="draft-preserved source-hit-context"><div><strong>已定位本人原文：第 ${escapeHtml(hit.start_line)}–${escapeHtml(hit.end_line)} 行</strong><p class="small">${escapeHtml(hit.excerpt)}</p><small>来源 ${escapeHtml(hit.source_id)} · ${escapeHtml(hit.reported_by || '陈述人待核')}${hit.excerpt_truncated ? ' · 片段截短，须读下方完整来源' : ''}。原文定位不代表内容已核真或符合要求。</small></div></div>`;
}

function checksState() {state.checks ||= new Map();state.checksDrafts ||= new Map();state.checksErrors ||= new Map();}
function checksDomain(matter) {return ['hr','leave','expense','inventory','general'].includes(matter?.domain);}
function checksMarkupFor(matter) {if (!checksDomain(matter)) return '';checksState();return modelChecksUI.markup(matter) + checksMarkup({matter,data:state.checks.get(matter.id),draft:state.checksDrafts.get(matter.id),sources:matter.sources || [],busy:state.busy,error:state.checksErrors.get(matter.id)});}
function keepChecksDom() {return checksDomain(state.current) && Boolean($('.checks-panel'));}
function notifyChecksBackground() {
  const label = $('#checks-background-update'),draft = state.checksDrafts?.get(state.current?.id);if (!label || !draft) return;
  const version = Math.max(state.current.version || 0,draft.latestReadVersion || 0),error = state.checksErrors?.get(state.current.id);label.hidden = !error && version === draft.baseVersion;
  label.textContent = error || `后台已读取事项版本 ${version}；当前要求／材料编辑与原基版 ${draft.baseVersion} 保留。请明确读取最新核对与差异后核实。`;
  const status = $('.matter-header .status-pill');if (status) {status.textContent = statusLabels[state.current.assistant_status] || '待核';status.className = `status-pill ${state.current.assistant_status}`;}
}
async function readChecks(id,{adoptVersion=false,background=false} = {}) {
  if (state.current?.id !== id || !checksDomain(state.current)) return;
  checksState();const epoch = state.identityEpoch,selection = state.selection;
  try {
    const data = await request(`/api/matters/${encodeURIComponent(id)}/checks`);
    if (epoch !== state.identityEpoch || selection !== state.selection || state.current?.id !== id) return;
    const draft = state.checksDrafts.get(id);
    if (background && (keepChecksDom() || waitingUI.keepDom() || sourceUI.keepDom() || extractionUI.keepDom() || tabularUI.keepDom() || modelPreviewUI.keepDom() || (sourceImpactUI.keepDom() || attachmentsUI.keepDom()))) {if (draft) draft.latestReadVersion = data.version;state.checksErrors.delete(id);notifyChecksBackground();return;}
    state.checks.set(id,data);state.checksErrors.delete(id);
    if (!draft?.dirty) {const fresh = createChecksDraft(data);fresh.ui = draft?.ui || {};state.checksDrafts.set(id,fresh);}
    else if (adoptVersion) {draft.baseVersion = data.version;draft.baseRevision = data.revision_no;draft.baseStructureVersion = data.structure_version;}
    render();
  } catch (error) {if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === id) {state.checksErrors.set(id,error.message || '核对记录暂不可读取，编辑保留。');if (background && (keepChecksDom() || waitingUI.keepDom() || sourceUI.keepDom() || extractionUI.keepDom() || tabularUI.keepDom() || modelPreviewUI.keepDom() || (sourceImpactUI.keepDom() || attachmentsUI.keepDom()))) notifyChecksBackground();else render();}}
}
async function syncChecksMatter(id,selection) {
  const epoch = state.identityEpoch;
  try {const matter = await request(`/api/matters/${encodeURIComponent(id)}`);if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === id) {state.current = matter;await readChecks(id);render();refreshList();}}
  catch (error) {if (epoch === state.identityEpoch && selection === state.selection) state.error = {message:'操作已保存，事项暂未读回最新版本；待明确读取再继续，编辑仍保留。',status:error.status};}
}
function markChecksDirty(draft) {draft.dirty = true;const label = $('#checks-saved-label');if (label) {label.textContent = '有未保存编辑；后台不会推进保存基版';label.classList.add('dirty-note');}const button = $('[data-action="checks-render"]');if (button) button.disabled = true;}
function checksInput(input) {
  const id = input.closest('[data-check-id]')?.dataset.checkId,matter = state.current;if (!id || !checksDomain(matter)) return false;
  const draft = state.checksDrafts?.get(matter.id),row = draft?.records.find((item) => item.id === id);if (!row) return false;
  const group = input.closest('[data-check-ref-group]')?.dataset.checkRefGroup;
  if (input.dataset.refField && group) {const ref = row[group]?.[Number(input.closest('[data-ref-index]')?.dataset.refIndex)];if (ref) ref[input.dataset.refField] = input.dataset.refField === 'source_id' ? input.value : input.value === '' ? '' : Number(input.value);markChecksDirty(draft);return true;}
  if (input.dataset.checkField) {row[input.dataset.checkField] = input.value;markChecksDirty(draft);return true;}return false;
}
function saveChecks() {
  const matter = state.current,draft = state.checksDrafts?.get(matter?.id);if (!draft || !checksDomain(matter)) return;
  const changes = checksChanges(draft);if (!changes.upserts.length && !changes.removed_ids.length) {toast('核对清单没有新的修改。');return;}
  if (changes.upserts.some((row) => !row.provided_refs.length && row.check !== 'pending')) {toast('未提供真实材料引用，不能记已给来源或已核。');return;}
  const selection = state.selection;
  auxiliaryWrite(`/api/matters/${encodeURIComponent(matter.id)}/checks`,{expected_version:draft.baseVersion,input_revision:draft.baseRevision,base_structure_version:draft.baseStructureVersion,...changes},async (data) => {const fresh = createChecksDraft(data);fresh.ui = draft.ui || {};state.checksDrafts.set(matter.id,fresh);state.checks.set(matter.id,data);state.checksErrors.delete(matter.id);toast('要求与材料核对已增量保存；正式合规和业务决定保持未知。');await syncChecksMatter(matter.id,selection);});
}
function checksAction(action,button) {
  const matter = state.current;if (!checksDomain(matter)) return false;
  if (action === 'checks-reload') {readChecks(matter.id,{adoptVersion:true});return true;}
  const draft = state.checksDrafts?.get(matter.id);if (!draft) return true;
  if (action === 'checks-render') {
    const data = state.checks.get(matter.id);if (draft.dirty || data.stale || !draft.records.length || ['paused','handoff','processing'].includes(matter.assistant_status)) return true;
    const artifact = editingArtifact(matter),human = draftFor(matter);if (artifact && human.artifactText === null) human.selectedArtifactId = artifact.id;
    const selection = state.selection;
    auxiliaryWrite(`/api/matters/${encodeURIComponent(matter.id)}/checks/render`,{expected_version:data.version,input_revision:data.revision_no,structure_version:data.structure_version},async () => {toast('已另存核对准备稿，原人工正文与编辑保留，未作正式决定。');await syncChecksMatter(matter.id,selection);});return true;
  }
  const id = button.closest('[data-check-id]')?.dataset.checkId,row = draft.records.find((item) => item.id === id);
  if (action === 'checks-add' && draft.records.length < 100) draft.records.push(cleanCheckRecord({id:crypto.randomUUID()}));
  else if (action === 'checks-remove' && row) {if (draft.original.some((item) => item.id === id) && !draft.removed_ids.includes(id)) draft.removed_ids.push(id);draft.records = draft.records.filter((item) => item.id !== id);}
  else if (['checks-add-ref','checks-remove-ref'].includes(action) && row) {
    const group = button.closest('[data-check-ref-group]')?.dataset.checkRefGroup;if (!['requirement_refs','provided_refs'].includes(group)) return true;
    if (action === 'checks-add-ref' && row[group].length < 10) row[group].push({source_id:'',start_line:'',end_line:''});
    else if (action === 'checks-remove-ref') {row[group].splice(Number(button.dataset.refIndex),1);if (group === 'provided_refs' && !row[group].length) row.check = 'pending';}
    else return true;
  } else return false;
  markChecksDirty(draft);render();return true;
}

function journeyState() {state.journeys ||= new Map();state.journeyDrafts ||= new Map();state.journeyErrors ||= new Map();}
function journeyDomain(matter) {return ['travel','leave'].includes(matter?.domain);}
function journeyMarkupFor(matter) {if (!journeyDomain(matter)) return '';journeyState();return modelJourneyUI.markup(matter) + materialNodesUI.markup(matter) + materialRecordsUI.markup(matter) + mealSummariesUI.markup(matter) + journeyMarkup({matter,data:state.journeys.get(matter.id),draft:state.journeyDrafts.get(matter.id),labels:labelsFor(matter),sources:matter.sources || [],travelItems:state.items.filter((item) => item.domain === 'travel'),principalName:state.session?.principal?.display_name,busy:state.busy,error:state.journeyErrors.get(matter.id)});}
function keepJourneyDom() {return journeyDomain(state.current) && Boolean($('.journey-panel'));}
function notifyJourneyBackground() {
  const label = $('#journey-background-update'),draft = state.journeyDrafts?.get(state.current?.id);if (!label || !draft) return;
  const versions = ['plan','meals','materials','movement','link'].map((key) => draft[key]?.baseVersion || 0),error = state.journeyErrors?.get(state.current.id),latest = Math.max(state.current.version || 0,draft.latestReadVersion || 0);
  label.hidden = !error && versions.every((version) => version === latest);label.textContent = error || `后台已读取事项版本 ${latest}，当前控件、计划与声明编辑保留原基版。请明确读取联动差异后核对。`;
  const status = $('.matter-header .status-pill');if (status) {status.textContent = statusLabels[state.current.assistant_status] || '待核';status.className = `status-pill ${state.current.assistant_status}`;}
}
async function readJourney(id,{adoptVersion=false,background=false} = {}) {
  const matter = state.current;if (matter?.id !== id || !journeyDomain(matter)) return;
  journeyState();const epoch = state.identityEpoch,selection = state.selection;
  try {
    const data = await request(`/api/matters/${encodeURIComponent(id)}/journey`);
    if (epoch !== state.identityEpoch || selection !== state.selection || state.current?.id !== id) return;
    const current = state.journeyDrafts.get(id);
    if (background && (keepJourneyDom() || waitingUI.keepDom() || sourceUI.keepDom() || extractionUI.keepDom() || tabularUI.keepDom() || modelPreviewUI.keepDom() || (sourceImpactUI.keepDom() || attachmentsUI.keepDom()))) {if (current) current.latestReadVersion = data.version;state.journeyErrors.delete(id);notifyJourneyBackground();return;}
    state.journeys.set(id,data);state.journeyErrors.delete(id);
    const fresh = createJourneyDraft(data,state.current);fresh.ui = current?.ui || {};
    for (const key of ['plan','meals','materials','movement','link']) if (current?.[key]?.dirty) {fresh[key] = current[key];if (adoptVersion) {fresh[key].baseVersion = data.version;fresh[key].baseRevision = data.revision_no;}}
    state.journeyDrafts.set(id,fresh);render();
  } catch (error) {if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === id) {state.journeyErrors.set(id,error.message || '联动记录暂不可读取，输入保留。');if (background && (keepJourneyDom() || waitingUI.keepDom() || sourceUI.keepDom() || extractionUI.keepDom() || tabularUI.keepDom() || modelPreviewUI.keepDom() || (sourceImpactUI.keepDom() || attachmentsUI.keepDom()))) notifyJourneyBackground();else render();}}
}
async function syncJourneyMatter(id,selection) {
  const epoch = state.identityEpoch;
  try {const matter = await request(`/api/matters/${encodeURIComponent(id)}`);if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === id) {state.current = matter;await readJourney(id);render();refreshList();}}
  catch (error) {if (epoch === state.identityEpoch && selection === state.selection) state.error = {message:'操作已保存，事项暂未读回最新版本；待明确读取后继续，其他编辑保留。',status:error.status};}
}
function markJourneyDirty(editor,key) {
  editor.dirty = true;const label = $(`[data-journey-saved="${key}"]`);if (label) {label.textContent = '有未保存编辑，基版保持原值';label.classList.add('dirty-note');}const button = $('[data-action="journey-render"]');if (button) button.disabled = true;
}
function journeyInput(input) {
  const form = input.closest('form[id^="journey-"]'),matter = state.current;if (!form || !journeyDomain(matter)) return false;
  const key = {'journey-plan-form':'plan','journey-meals-form':'meals','journey-materials-form':'materials','journey-movement-form':'movement','journey-link-form':'link'}[form.id];if (!key) return false;
  const draft = state.journeyDrafts?.get(matter.id),editor = draft?.[key];if (!editor) return false;
  if (key === 'plan' && input.dataset.journeyPlan) {editor.fields[input.dataset.journeyPlan][input.dataset.planProperty] = input.value;markJourneyDirty(editor,key);return true;}
  const field = input.dataset.journeyField;if (!field) return false;
  let row = editor;if (key === 'meals') row = editor.records.find((item) => item.id === input.closest('[data-meal-id]')?.dataset.mealId);
  if (key === 'materials') row = editor.records.find((item) => item.id === input.closest('[data-material-id]')?.dataset.materialId);
  if (!row) return false;row[field] = input.value;markJourneyDirty(editor,key);
  if (key === 'materials' && field === 'availability' && input.value === 'missing') {row.source_id = '';row.source_position = '';row.check = 'pending';row.valid_until = null;}
  if (key === 'materials' && field === 'provider_class' && input.value === 'current_actor_statement') row.provider_name = '';
  if (key === 'movement' && field === 'kind' && input.value !== 'returned') row.related_departure_event_id = '';
  if (key === 'link' && field === 'travel_matter_id') {const target = state.items.find((item) => item.id === input.value);editor.travel_revision = target?.revision_no || '';}
  if (['availability','provider_class','mode','kind','supersedes_event_id','travel_matter_id'].includes(field)) render();return true;
}
function journeyWrite(key,suffix,body) {
  const matter = state.current,draft = state.journeyDrafts?.get(matter?.id);if (!draft) return;
  const selection = state.selection;
  auxiliaryWrite(`/api/matters/${encodeURIComponent(matter.id)}/${suffix}`,body,async () => {draft[key].dirty = false;toast('已保存本地计划／需求／实际声明，正式批准、安排、供餐与手续仍待核。');await syncJourneyMatter(matter.id,selection);});
}
function journeySubmit(form) {
  const matter = state.current,draft = state.journeyDrafts?.get(matter?.id);if (!draft || !journeyDomain(matter)) return false;
  const key = {'journey-plan-form':'plan','journey-meals-form':'meals','journey-materials-form':'materials','journey-movement-form':'movement','journey-link-form':'link'}[form.id];if (!key) return false;
  const editor = draft[key],base = {expected_version:editor.baseVersion,input_revision:editor.baseRevision};
  if (key === 'plan') {
    const changes = Object.fromEntries(Object.entries(editor.fields).filter(([field,value]) => JSON.stringify(value) !== JSON.stringify(editor.original[field])).map(([field,value]) => [field,{value:value.value.trim() || null,status:value.value.trim() ? value.status : 'unknown'}]));
    journeyWrite(key,'journey/plan',{...base,field_changes:changes,plan_semantics:{departure_kind:'camp_departure',return_kind:'camp_return',comparison_offset:editor.comparison_offset.trim() || null}});
  } else if (key === 'meals' || key === 'materials') {
    const changes = incrementalChanges(editor,key === 'meals' ? mealRecord : materialRecord);if (!changes.upserts.length && !changes.removed_ids.length) {toast('明细没有新的修改。');return true;}
    journeyWrite(key,key === 'meals' ? 'journey/meal-requests' : 'leave/materials',{...base,...changes});
  } else if (key === 'movement') {
    if (!editor.note.trim()) {toast('请填写真实说明；不使用计划代填实际。');return true;}
    if (editor.mode === 'transcribed' && (!editor.person_ref.trim() || !editor.reported_by.trim())) {toast('代录须注明实际人员引用与原陈述人。');return true;}
    if (Boolean(editor.source_id) !== Boolean(editor.source_position.trim())) {toast('来源ID和真实行号须成对提供，或都留空。');return true;}
    if (editor.supersedes_event_id && !editor.correction_reason.trim()) {toast('更正历史声明须填写实际原因。');return true;}
    const body = {expected_version:editor.baseVersion,kind:editor.kind,note:editor.note.trim()};
    if (editor.mode === 'transcribed') {body.person_ref = editor.person_ref.trim();body.reported_by = editor.reported_by.trim();}
    for (const field of ['occurred_at','source_id','source_position']) if (editor[field]?.trim()) body[field] = editor[field].trim();
    if (editor.kind === 'returned' && editor.related_departure_event_id) body.related_departure_event_id = editor.related_departure_event_id;
    if (editor.supersedes_event_id) {body.supersedes_event_id = editor.supersedes_event_id;body.correction_reason = editor.correction_reason.trim();}
    journeyWrite(key,'journey/movement',body);
  } else {
    if (!editor.travel_matter_id || !Number.isInteger(Number(editor.travel_revision)) || Number(editor.travel_revision) < 1) {toast('请选择本人真实旅程，并注明已保存修订。');return true;}
    journeyWrite(key,'leave/travel-link',{expected_version:editor.baseVersion,travel_matter_id:editor.travel_matter_id,travel_revision:Number(editor.travel_revision)});
  }
  return true;
}
function journeyAction(action,button) {
  const matter = state.current;if (!journeyDomain(matter)) return false;
  if (action === 'journey-reload') {readJourney(matter.id,{adoptVersion:true});return true;}
  const draft = state.journeyDrafts?.get(matter.id);if (!draft) return true;
  if (action === 'journey-render') {
    if (journeyDirty(draft) || ['paused','handoff','processing'].includes(matter.assistant_status)) return true;
    const data = state.journeys.get(matter.id),artifact = editingArtifact(matter),human = draftFor(matter);if (artifact && human.artifactText === null) human.selectedArtifactId = artifact.id;
    const selection = state.selection;
    auxiliaryWrite(`/api/matters/${encodeURIComponent(matter.id)}/journey/render`,{expected_version:data.version,input_revision:data.revision_no},async () => {toast('已另存同修订四轨准备稿；原人工正文与未保存编辑保留。');await syncJourneyMatter(matter.id,selection);});return true;
  }
  if (action === 'journey-unlink') {journeyWrite('link','leave/travel-link',{expected_version:draft.link.baseVersion,travel_matter_id:null,travel_revision:null});return true;}
  const materials = action.includes('material'),key = materials ? 'materials' : 'meals',editor = draft[key];
  if (action === 'journey-add-meal' && editor.records.length < 100) editor.records.push({id:crypto.randomUUID(),person_ref:'',meal_date:'',meal_slot:'',request:'pending',note:''});
  else if (action === 'journey-add-material' && editor.records.length < 5) {const kind = Object.keys(materialLabels).find((value) => !editor.records.some((row) => row.kind === value));if (!kind) return true;editor.records.push({id:crypto.randomUUID(),kind,availability:'missing',provider_class:'current_actor_statement',valid_until:null,check:'pending'});}
  else if (['journey-remove-meal','journey-remove-material'].includes(action)) {const id = button.closest(materials ? '[data-material-id]' : '[data-meal-id]')?.dataset[materials ? 'materialId' : 'mealId'];if (editor.original.some((row) => row.id === id) && !editor.removed_ids.includes(id)) editor.removed_ids.push(id);editor.records = editor.records.filter((row) => row.id !== id);}
  else return false;
  draft.ui ||= {};draft.ui[key] = true;markJourneyDirty(editor,key);render();return true;
}

function structureState() {state.structures ||= new Map();state.structureDrafts ||= new Map();state.structureProgressDrafts ||= new Map();state.structureErrors ||= new Map();}
function structuredDomain(matter) {return ['meeting','document'].includes(matter?.domain);}
function progressDraftsFor(id) {structureState();if (!state.structureProgressDrafts.has(id)) state.structureProgressDrafts.set(id,new Map());return state.structureProgressDrafts.get(id);}
function meetingCandidateRecord(id) {state.meetingCandidates ||= new Map();if (!state.meetingCandidates.has(id)) state.meetingCandidates.set(id,{data:null,selected:new Set(),reading:false,error:null});return state.meetingCandidates.get(id);}
function meetingCandidateMarkup(matter) {
  if (!structuredDomain(matter)) return '';
  const documentDomain = matter.domain === 'document';
  const record = meetingCandidateRecord(matter.id),data = record.data,disabled = state.busy || record.reading ? 'disabled' : '',labels = {discussion:'讨论',proposal:'提议',decision:'决定候选',dissent:'分歧',action:'行动候选'};
  return `<section class="panel meeting-candidate-panel"><div class="panel-head"><h2>${documentDomain ? '模型分节 · 核对后续办' : '模型条目 · 核对后续办'}</h2><button class="button quiet" data-action="meeting-candidates-read" ${disabled}>${documentDomain ? '读取已保存模型分节（不再调用模型）' : '读取已保存模型条目（不再调用模型）'}</button></div>${record.reading ? '<p role="status">正在读回本事项候选与当前结构，人工编辑保留。</p>' : ''}${record.error ? `<p class="dirty-note small">${escapeHtml(record.error)}</p>` : ''}${data ? `<p class="scope-note">${escapeHtml(data.scope)} · 模型输入修订 ${escapeHtml(data.basis_revision ?? '尚无')}／当前 ${escapeHtml(data.revision_no)}。</p>${data.stale ? '<p class="dirty-note small">该模型批次的输入已修订，保留历史；请核对当前原文并按需另行整理，不能直接追加旧候选。</p>' : ''}${data.items.map(item=>`<article class="evidence-record"><label><input type="checkbox" data-meeting-candidate="${escapeHtml(item.id)}" ${record.selected.has(item.id) ? 'checked' : ''} ${disabled}>选入：${escapeHtml(documentDomain ? item.heading : labels[item.kind])}</label><p>${escapeHtml(documentDomain ? item.body : item.text)}</p>${documentDomain ? '<p class="small">通用草稿候选，未签发或送达</p>' : `<p class="small">责任原述：${escapeHtml(item.responsible_text || '未知')} · 期限原述：${escapeHtml(item.deadline_text || '未知')} · 无账号绑定／日期确认</p>`}${item.refs.map(ref=>`<details><summary>来源 ${escapeHtml(ref.source_id)} 第${escapeHtml(ref.start_line)}–${escapeHtml(ref.end_line)}行</summary><pre>${escapeHtml(ref.excerpt)}</pre></details>`).join('')}</article>`).join('') || '<p class="small muted">本批次没有条目候选；可保留正文或自行添加真实结构项，不补造行动。</p>'}<div class="artifact-toolbar"><button class="button" data-action="meeting-candidates-append" ${disabled || data.stale ? 'disabled' : ''}>${documentDomain ? '把选中分节追加到未保存结构' : '把选中条目追加到未保存结构'}</button><button class="button quiet" data-action="meeting-candidates-clear" ${disabled}>清除候选选择</button><span class="small">已选择 ${record.selected.size} 条</span></div><p class="scope-note">追加只填候选与原文引用，不改旧结构、人工稿或实际进度。请在下方编辑核对并保存增量结构；日期及本人负责须分别明确。</p>` : '<p class="small muted">先主动选择模型整理，再读取候选；也可直接编辑下方结构。此入口不隐式调用模型。</p>'}</section>`;
}
async function readMeetingCandidates() {
  const matter = state.current;if (!structuredDomain(matter)) return;
  const record = meetingCandidateRecord(matter.id);if (record.reading) return;
  const epoch = state.identityEpoch,selection = state.selection,artifact = editingArtifact(),human = draftFor();if (artifact && human.artifactText === null) human.selectedArtifactId = artifact.id;
  record.reading = true;record.error = null;render();
  try {
    const [data,structure,fresh] = await Promise.all([request(`/api/matters/${encodeURIComponent(matter.id)}/${matter.domain}/candidates`),request(`/api/matters/${encodeURIComponent(matter.id)}/${matter.domain}/structure`),request(`/api/matters/${encodeURIComponent(matter.id)}`)]);
    if (epoch !== state.identityEpoch || selection !== state.selection || state.current?.id !== matter.id) return;
    if (data.version !== structure.version || data.version !== fresh.version) throw new Error('读回期间事项变化，未推进基版；请明确再次读取，编辑保留。');
    record.data = data;state.current = fresh;state.structures.set(matter.id,structure);
    if (!state.structureDrafts.get(matter.id)?.dirty) state.structureDrafts.set(matter.id,createStructureDraft(structure,matter.domain));
  } catch(error) {if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === matter.id) record.error = error.message || '候选未读回，已保存稿和结构保留。';}
  finally {record.reading = false;if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === matter.id) render();}
}
function appendMeetingCandidates() {
  const matter = state.current,record = meetingCandidateRecord(matter.id),data = record.data,draft = state.structureDrafts.get(matter.id);
  if (!data || !record.selected.size || !draft) {toast('请读取并明确选择条目。');return;}
  if (data.stale || data.version !== matter.version || data.revision_no !== matter.revision_no || draft.baseVersion !== data.version) {toast('输入或结构基版已变化；候选、选择和编辑保留，请读取当前候选与结构后核对。');return;}
  const available = new Map(data.items.map(item=>[item.id,item]));if ([...record.selected].some(id=>!available.has(id))) {toast('有旧批次选择，不能混入当前候选；请核对后清除选择。');return;}
  const existing = new Set(draft.records.map(item=>item.id)),added = [...record.selected].filter(id=>!existing.has(id)).map(id=>({...available.get(id),refs:available.get(id).refs.map(ref=>({source_id:ref.source_id,start_line:ref.start_line,end_line:ref.end_line}))}));
  if (draft.records.length + added.length > 100) {toast('追加将超过100项，整次未追加，原结构保留。');return;}
  if (!added.length) {toast('同批次条目已在结构中，未重复追加或覆盖人工修改。');return;}
  draft.records.push(...added);record.selected.clear();markStructureDirty(draft);render();toast(`已追加 ${added.length} 条到未保存结构；仍为候选，请编辑核对后保存。`);
}
function structuredMarkupFor(matter) {if (!structuredDomain(matter)) return '';structureState();return meetingCandidateMarkup(matter) + structureMarkup({matter,data:state.structures.get(matter.id),draft:state.structureDrafts.get(matter.id),sources:matter.sources || [],principalId:state.session?.principal?.id,busy:state.busy,progressDrafts:progressDraftsFor(matter.id),ui:draftFor(matter).structureUi || {},error:state.structureErrors.get(matter.id)});}
function keepStructureDom() {return structuredDomain(state.current) && Boolean($('.structured-panel'));}
function notifyStructureBackground() {
  const label = $('#structure-background-update'),draft = state.structureDrafts?.get(state.current?.id);if (!label || !draft) return;
  const data = state.structures?.get(state.current.id),error = state.structureErrors?.get(state.current.id),version = Math.max(state.current.version || 0,data?.version || 0,draft.latestReadVersion || 0);
  label.hidden = !error && version === draft.baseVersion;
  label.textContent = error || `后台已读到事项版本 ${version}；当前结构控件和编辑保持原样，保存基版 ${draft.baseVersion}。请明确读取最新结构与差异后核对。`;
  const status = $('.matter-header .status-pill');if (status) {status.textContent = statusLabels[state.current.assistant_status] || '待核';status.className = `status-pill ${state.current.assistant_status}`;}
}
async function readStructure(id,{adoptVersion=false,background=false} = {}) {
  const m = state.current;if (m?.id !== id || !structuredDomain(m)) return;
  structureState();const epoch = state.identityEpoch, selection = state.selection;
  try {
    const data = await request(`/api/matters/${encodeURIComponent(id)}/${m.domain}/structure`);
    if (epoch !== state.identityEpoch || selection !== state.selection || state.current?.id !== id) return;
    if (background && (keepStructureDom() || waitingUI.keepDom() || sourceUI.keepDom() || extractionUI.keepDom() || tabularUI.keepDom() || modelPreviewUI.keepDom() || (sourceImpactUI.keepDom() || attachmentsUI.keepDom()))) {const draft = state.structureDrafts.get(id);if (draft) draft.latestReadVersion = data.version;state.structureErrors.delete(id);notifyStructureBackground();return;}
    state.structures.set(id,data);state.structureErrors.delete(id);
    const draft = state.structureDrafts.get(id);
    if (!draft?.dirty) state.structureDrafts.set(id,createStructureDraft(data,m.domain));
    else if (adoptVersion) {draft.baseVersion = data.version;draft.baseRevision = data.revision_no;draft.baseStructureVersion = data.structure_version;}
    if (adoptVersion) for (const [itemId,progress] of progressDraftsFor(id)) {const item = data.items?.find((row) => row.id === itemId);if (item) {progress.baseVersion = data.version;progress.baseItemRevision = item.item_revision;}}
    render();
  } catch (error) {if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === id) {state.structureErrors.set(id,error.message || '结构暂不可读取，已有输入保留。');if (background && (keepStructureDom() || waitingUI.keepDom() || sourceUI.keepDom() || extractionUI.keepDom() || tabularUI.keepDom() || modelPreviewUI.keepDom() || (sourceImpactUI.keepDom() || attachmentsUI.keepDom()))) notifyStructureBackground();else render();}}
}
async function syncStructuredMatter(id,selection) {
  const epoch = state.identityEpoch;
  try {const matter = await request(`/api/matters/${encodeURIComponent(id)}`);if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === id) {state.current = matter;await readStructure(id);render();refreshList();}}
  catch (error) {if (epoch === state.identityEpoch && selection === state.selection) state.error = {message:'操作已保存，事项暂未读回最新版本；读取后再继续，编辑仍保留。',status:error.status};}
}
function markStructureDirty(draft) {draft.dirty = true;const label = $('#structure-saved-label');if (label) {label.textContent = '有未保存编辑；保存基线未自动推进';label.classList.add('dirty-note');}const button = $('[data-action="structure-render"]');if (button) button.disabled = true;}
function structureInput(input) {
  const itemId = input.closest('[data-structure-id]')?.dataset.structureId, m = state.current;
  if (!itemId || !structuredDomain(m)) return false;
  structureState();const draft = state.structureDrafts.get(m.id), record = draft?.records.find((item) => item.id === itemId);if (!record) return false;
  if (input.dataset.progressField) {
    const data = state.structures.get(m.id), meta = data?.items?.find((item) => item.id === itemId);if (!meta) return true;
    const drafts = progressDraftsFor(m.id);let progress = drafts.get(itemId);
    if (!progress) {progress = {command:meta.responsible_account_id === state.session?.principal?.id && meta.responsible_account_id ? 'self_accept' : 'reported_progress',reported_by:'',note:'',occurred_at:'',baseVersion:data.version,baseItemRevision:meta.item_revision};drafts.set(itemId,progress);}
    progress[input.dataset.progressField] = input.value;return true;
  }
  if (input.dataset.refField) {const ref = record.refs[Number(input.closest('[data-ref-index]')?.dataset.refIndex)];if (ref) ref[input.dataset.refField] = input.dataset.refField === 'source_id' ? input.value : input.value === '' ? '' : Number(input.value);markStructureDirty(draft);return true;}
  const field = input.dataset.structureField;if (!field) return false;
  if (field === 'responsible_account_id') record[field] = input.checked ? state.session?.principal?.id || null : null;
  else if (field === 'deadline_kind') record.deadline = input.value === 'unknown' ? {kind:'unknown',value:null} : {kind:input.value,value:'',confirmed:true};
  else if (field === 'deadline_value') record.deadline = {...record.deadline,value:input.value,confirmed:true};
  else record[field] = input.value;
  markStructureDirty(draft);if (['kind','confirmation','deadline_kind'].includes(field)) render();return true;
}
function saveStructure() {
  const m = state.current,draft = state.structureDrafts?.get(m?.id);if (!draft || !structuredDomain(m)) return;
  const changes = structureChanges(draft);if (!changes.upserts.length && !changes.removed_ids.length) {toast('结构没有新的修改。');return;}
  const selection = state.selection;
  auxiliaryWrite(`/api/matters/${encodeURIComponent(m.id)}/${m.domain}/structure`,{expected_version:draft.baseVersion,input_revision:draft.baseRevision,base_structure_version:draft.baseStructureVersion,...changes},async (data) => {state.structures.set(m.id,data);state.structureDrafts.set(m.id,createStructureDraft(data,m.domain));state.structureErrors.delete(m.id);toast('结构增量已保存，历史及人工正文保留；未批准或签发。');await syncStructuredMatter(m.id,selection);});
}
function saveProgress(itemId) {
  const m = state.current,data = state.structures?.get(m?.id),meta = data?.items?.find((item) => item.id === itemId),draft = progressDraftsFor(m.id).get(itemId);
  if (!meta || !draft?.note?.trim()) {toast('请填写实际说明；不补造行动进度。');return;}
  if (draft.command === 'reported_progress' && !draft.reported_by.trim()) {toast('代录进度须注明实际原陈述人。');return;}
  if (draft.command.startsWith('self_') && meta.responsible_account_id !== state.session?.principal?.id) {toast('仅已保存责任账号为本人的行动可登记本人声明。');return;}
  const body = {expected_version:draft.baseVersion,item_revision:draft.baseItemRevision,command:draft.command,note:draft.note};
  if (draft.command === 'reported_progress') body.reported_by = draft.reported_by;
  if (draft.occurred_at.trim()) body.occurred_at = draft.occurred_at.trim();
  const selection = state.selection;
  auxiliaryWrite(`/api/matters/${encodeURIComponent(m.id)}/meeting/actions/${encodeURIComponent(itemId)}/progress`,body,async () => {progressDraftsFor(m.id).delete(itemId);toast('已保存独立进度声明，不等同实际验收。');await syncStructuredMatter(m.id,selection);});
}
function renderStructure() {
  const m = state.current,data = state.structures?.get(m?.id),draft = state.structureDrafts?.get(m?.id);if (!data || draft?.dirty || ['paused','handoff'].includes(m.assistant_status)) return;
  const artifact = editingArtifact(m),human = draftFor(m);if (artifact && human.artifactText === null) human.selectedArtifactId = artifact.id;
  const selection = state.selection;
  auxiliaryWrite(`/api/matters/${encodeURIComponent(m.id)}/structure/render`,{expected_version:data.version,input_revision:data.revision_no,structure_version:data.structure_version},async () => {toast('已生成独立结构准备稿；原人工正文与编辑保留，可在稿件列表选择。');await syncStructuredMatter(m.id,selection);});
}

function serviceState() {state.services ||= [];state.serviceDrafts ||= new Map();state.serviceAccounts ||= {clerk:[],executor:[]};}
function serviceDraft(service) {serviceState();if (!state.serviceDrafts.has(service.id)) state.serviceDrafts.set(service.id,{executor_id:'',assign_note:'',respond_note:'',occurred_at:'',result:'unverified',result_note:'',withdraw_note:'',input_revision:'',revise_note:'',reported_by:'',statement_note:'',evidence_refs:''});return state.serviceDrafts.get(service.id);}
function serviceSubmitDraft(matter) {const draft = draftFor(matter);return draft.serviceSubmit ||= {clerk_id:'',contact:'',notes:'',permission_status:'unknown',permission_note:''};}
function latestArtifact(matter) {return matter.artifacts?.[0];}
function editingArtifact(matter = state.current) {const draft = draftFor(matter);return matter.artifacts?.find((artifact) => artifact.id === (draft.artifactId || draft.selectedArtifactId)) || latestArtifact(matter);}
function selectArtifact(id) {
  const draft = draftFor(), artifact = editingArtifact();
  if (!state.current.artifacts?.some((item) => item.id === id)) return;
  if (draft.artifactText !== null && draft.artifactText !== artifact?.content && !confirm('切换稿件会替换正文中未保存的编辑。请先保存或复制；确认后切换吗？')) {render();return;}
  draft.artifactText = null;draft.artifactId = null;draft.artifactVersion = null;draft.selectedArtifactId = id;render();
}
function languageDraftMarkup(matter,artifact,dirty) {
  if (!artifact) return '';
  const draft = draftFor(matter),basis = artifact.language_basis,disabled = state.busy || !state.session?.model_configured || sourceUI.modelBlocked(matter) || ['paused','handoff','processing'].includes(matter.assistant_status) || artifact.stale || dirty;
  return `<section class="panel"><details class="language-draft-panel"><summary class="details-toggle">双语／同一原稿另存语言稿</summary><p class="scope-note">仅向许可交融发送当前所选的已保存正文 v${escapeHtml(artifact.version)}，不发送未保存编辑或其他稿件；需先保存并核对当前原稿。明确选择语言，不按国籍推断。</p><label class="field-label" for="translation-language">目标语言（本人明确选择）</label><select id="translation-language"><option value="">请选择</option><option value="en" ${draft.translationLanguage==='en'?'selected':''}>English</option><option value="zh" ${draft.translationLanguage==='zh'?'selected':''}>中文</option></select><button class="button" data-action="translate-artifact" ${disabled?'disabled':''}>调用交融并另存语言稿</button><p class="small muted">本次最多4000字，完整翻译不自动截断；数字／日期／常用币种原值核对，不保证语义已核。原稿保留，可暂停／恢复；未配置时沿人工稿路径继续。</p></details>${basis?`<aside class="scope-note"><strong>内部核对依据（与正文分开）</strong><p>${escapeHtml(basis.target_language==='en'?'English':'中文')} · 原稿 ${escapeHtml(basis.artifact_id)} v${escapeHtml(basis.artifact_version)} · 事实修订 ${escapeHtml(basis.input_revision)}。${basis.original_stale?'原稿或事实已变化，须重新核对；历史译文保留。':'基于此固定已保存版本。'}</p><p>语言稿尚未外发；数字／日期／币种校验只针对初次模型译文，人工编辑及语义、职责、立场须本人核对。</p></aside>`:''}</section>`;
}
function translateArtifact() {
  const matter = state.current, artifact = editingArtifact(),draft = draftFor();
  if (!matter || !artifact || state.busy) return;
  if (draft.artifactText!==null && draft.artifactText!==artifact.content) {toast('请先保存并核对原稿编辑；未保存文字不会发送。');return;}
  if (!['en','zh'].includes(draft.translationLanguage)) {toast('请本人明确选择目标语言。');return;}
  if (artifact.content.length>4000) {toast('原稿超过4000字，请另存所需完整段落后继续，不自动截断。');return;}
  const existing = matter.artifacts?.find(item => item.language_basis?.artifact_id===artifact.id && item.language_basis?.artifact_version===artifact.version && item.language_basis?.input_revision===matter.revision_no && item.language_basis?.target_language===draft.translationLanguage);
  if (existing) {selectArtifact(existing.id);toast('同原稿版本的语言稿已保存，已打开；没有再次调用或覆盖人工编辑。');return;}
  mutate(`/api/matters/${encodeURIComponent(matter.id)}/language-drafts`,{expected_version:matter.version,input_revision:matter.revision_no,artifact_id:artifact.id,artifact_version:artifact.version,target_language:draft.translationLanguage},{message:'已登记所选固定版本语言稿；原稿与编辑保留，完成后请主动查看新稿。'});
}
function labelsFor(matter = state.current) {return matter?.field_labels || state.catalog[matter?.domain]?.fields || {};}
function domainLabel(domain) {return state.catalog[domain]?.label || '综合事项';}
const matterGroups = [
  {id:'logistics',label:'后勤服务',domains:['travel','dining','menu','feedback','repair','cleaning','utilities']},
  {id:'office',label:'行政办公',domains:['document','meeting']},
  {id:'finance',label:'费用与物资',domains:['expense','inventory']},
  {id:'people',label:'人员事务',domains:['leave','hr']},
  {id:'general',label:'综合事项',domains:['general']},
];
function groupedDomains() {
  const known = new Set(matterGroups.flatMap(group => group.domains));
  return [...matterGroups.map(group => ({...group,domains:group.domains.filter(key => state.catalog[key])})),{id:'other',label:'其他事项',domains:Object.keys(state.catalog).filter(key => !known.has(key))}].filter(group => group.domains.length);
}
function domainMatches(domain, filter) {return !filter || domain === filter || groupedDomains().some(group => filter === `group:${group.id}` && group.domains.includes(domain));}
function domainOptions(selected, all = false) {
  const option = (key,label) => `<option value="${escapeHtml(key)}" ${key === selected ? 'selected' : ''}>${escapeHtml(label)}</option>`;
  return `${all ? option('','全部事项') : ''}${groupedDomains().map(group => `<optgroup label="${escapeHtml(group.label)}">${all ? option(`group:${group.id}`,`全部${group.label}`) : ''}${group.domains.map(key => option(key,domainLabel(key))).join('')}</optgroup>`).join('')}`;
}
function categorySuggestions() {return groupedDomains().map(group => `<section class="matter-category"><h2>${escapeHtml(group.label)}</h2><div class="suggestions">${group.domains.map(key => `<button class="suggestion" data-suggestion="${escapeHtml(key)}">${escapeHtml(domainLabel(key))}</button>`).join('')}</div></section>`).join('');}
function modelStatus() {const status = state.session?.model_status;const reason = typeof status === 'string' ? status : status?.reason;return ({disabled:'交融模型未启用',ready:`交融模型已配置 · 本进程已发起 ${Number(status?.calls_used || 0)} 次调用，结果见事项记录`,invalid_config:'交融模型配置待核',missing_or_invalid_key:'交融模型凭据未配置'})[reason] || (state.session?.model_configured ? '模型已配置，按需调用' : '模型未配置');}
function categoryMarkup(m) {if(!Object.keys(m.domain_routing||{}).length)return '';const draft=draftFor(m),meta=m.domain_routing;return `<details class="category-panel" ${draft.categoryOpen?'open':''}><summary class="details-toggle">办理类别：${escapeHtml(domainLabel(m.domain))} · ${meta.mode==='manual'?'本人选择':meta.state==='classified'?'助理判断':'待理解／核对'}</summary><p class="small muted">${escapeHtml(meta.mode==='manual'?'由你选择办理范围；原话和人工稿保留。':meta.reason||'已保存目标；明确选择模型整理时理解类别，也可自行选择。')}</p><form id="category-form"><select name="domain" aria-label="调整办理类别" ${state.busy||!m.category_change_allowed?'disabled':''}>${domainOptions(draft.categoryDomain??m.domain)}</select><button class="button quiet" type="submit" ${state.busy||!m.category_change_allowed?'disabled':''}>按这个类别继续</button></form>${!m.category_change_allowed?'<p class="small muted">已有业务字段或办理记录，类别与原记录保留；可修正目标或另存独立事项。</p>':''}</details>`;}
function adoptCategoryDraft(matter) {const previous=state.current;if(previous?.id===matter.id&&previous.domain!==matter.domain){const draft=draftFor(previous);draft.categoryFactDrafts||={};draft.categoryFactDrafts[previous.domain]=draft.factChanges;draft.factChanges=draft.categoryFactDrafts[matter.domain]||{};return true;}return false;}
function modelScope() {return state.session?.model_configured ? '模型整理需主动选择并点击；将把本事项的目标、已保存事实与来源文字发给已配置模型。人工编辑稿不自动发送，结果仍须核对。' + (state.current?.private_title_context_enabled ? '本事项已允许额外参考最多10份本人其他事项目标标题；不含那些事项的事实或资料正文。' : '') : '模型未配置，可使用本地模板整理。资料不会发送给外部模型。';}
function sourceOptions(selected) {return Object.entries(sourceLabels).map(([value,label]) => `<option value="${value}" ${value === selected ? 'selected' : ''}>${label}</option>`).join('');}
const evidenceLabels = {statement:'人工转录声明',receipt_reference:'凭据候选引用',review_note:'核对备注'};
function matterSourceOptions(matter,selected) {return `<option value="">请选择已保存来源</option>${(matter.sources || []).map((source,index) => `<option value="${escapeHtml(source.id)}" ${source.id === selected ? 'selected' : ''}>${index + 1}. ${escapeHtml(sourceLabels[source.kind] || source.kind)} · ${escapeHtml(source.reported_by || '陈述人待核')} · ${escapeHtml(source.text?.slice(0,35) || '')}</option>`).join('')}`;}
function sourceFileDeclarationMarkup(source) {
  const info = source.file_info;if (!info) return '';
  return `<p class="small muted source-file-declaration">本人选文件声明：${escapeHtml(info.name)} · ${escapeHtml(String(info.format || '').toUpperCase())} · 声明原文件 ${escapeHtml(info.reported_bytes)} 字节 · ${info.text_edited ? '提交文字有改动（包括修剪），不是原始附件' : '声明提交文字与加载原文一致'}。这里只保存所提供文字与本人声明，未保存原始附件、未作OCR或Excel同步。</p>`;
}
function clearSourceFile(draft) {draft.sourceFileName = '';draft.sourceFileFormat = null;draft.sourceFileBytes = null;draft.sourceFileOriginal = null;$('#source-file-pending')?.remove();}
function sourceName(matter,id) {const source = matter.sources?.find((item) => item.id === id);return source ? `${sourceLabels[source.kind] || source.kind} · ${source.reported_by || '陈述人待核'}` : '来源待核';}
function ensureRows() {const draft = draftFor();if (draft.rows === null) {draft.rows = (state.current.rows || []).map((row) => ({...row}));draft.rowsBaseVersion = state.current.version;draft.rowsBaseRevision = state.current.revision_no;}return draft.rows;}
function legacyMarkup(matter) {
  const entries = Object.entries(matter.legacy_facts || {});
  return entries.length ? `<details class="legacy-facts"><summary class="details-toggle">旧版本字段 · 只读保留 ${entries.length} 项</summary><p class="small muted">这些字段不属于当前业务，不参与当前准备和计算；原记录保留。</p>${entries.map(([key,fact]) => `<div class="legacy-row"><strong>${escapeHtml(key)}</strong><span>${escapeHtml(fact?.value ?? '')} · ${escapeHtml(fact?.status || '待核')}</span></div>`).join('')}</details>` : '';
}
function evidenceMarkup(matter,draft,disabled) {
  const tracks = state.catalog[matter.domain]?.tracks || {};
  if (!Object.keys(tracks).length) return '';
  const e = draft.evidence;
  const records = [...(matter.evidence || [])].reverse().map((record) => `<article class="evidence-record"><div><strong>${escapeHtml(tracks[record.track] || record.track)} · ${escapeHtml(evidenceLabels[record.record_type] || record.record_type)}</strong><span class="artifact-meta">事实修订 ${escapeHtml(record.input_revision)}${record.stale ? ' · 修订已变化，需重核' : ''}</span></div><p>${escapeHtml(record.text)}</p><small>陈述人：${escapeHtml(record.reported_by)} · 声明发生时间：${escapeHtml(record.occurred_at)}<br>来源：${escapeHtml(sourceName(matter,record.source_id))} · 位置：${escapeHtml(record.source_position)}<br>本地记录者：${escapeHtml(record.recorded_by)} · ${escapeHtml(formatTime(record.recorded_at))}</small></article>`).join('');
  return `<section class="panel"><details id="evidence-details" ${draft.evidenceOpen ? 'open' : ''}><summary class="details-toggle">人工转录与凭据候选 <span class="muted">${matter.evidence?.length || 0} 条</span></summary><p class="scope-note">保存你实际获得的声明、凭据引用或核对备注；系统不核真、不认证陈述人身份，不据此确认批准、派遣、发布、付款或执行。</p>${records || '<p class="small muted">尚无转录记录。</p>'}<form id="evidence-form" class="source-form"><div class="facts-grid"><div class="field"><label for="evidence-track">关联独立业务环节</label><select id="evidence-track" name="track" required ${disabled}><option value="">请选择环节</option>${Object.entries(tracks).map(([key,label]) => `<option value="${escapeHtml(key)}" ${key === e.track ? 'selected' : ''}>${escapeHtml(label)}</option>`).join('')}</select></div><div class="field"><label for="evidence-type">记录性质</label><select id="evidence-type" name="record_type" ${disabled}>${Object.entries(evidenceLabels).map(([key,label]) => `<option value="${key}" ${key === e.record_type ? 'selected' : ''}>${label}</option>`).join('')}</select></div><div class="field"><label for="evidence-reporter">原陈述人（不等同认证身份）</label><input id="evidence-reporter" name="reported_by" required maxlength="200" value="${escapeHtml(e.reported_by)}" ${disabled}></div><div class="field"><label for="evidence-time">声明发生日期／时间</label><input id="evidence-time" name="occurred_at" required placeholder="2026-10-08 或 2026-10-08T14:00:00+01:00" value="${escapeHtml(e.occurred_at)}" ${disabled}></div><div class="field"><label for="evidence-source">已保存来源</label><select id="evidence-source" name="source_id" required ${disabled}>${matterSourceOptions(matter,e.source_id)}</select></div><div class="field"><label for="evidence-position">来源位置</label><input id="evidence-position" name="source_position" required maxlength="200" placeholder="例如：原话第 2 句、表格第 3 行" value="${escapeHtml(e.source_position)}" ${disabled}></div></div><label class="field-label" for="evidence-text">实际获得的文字与适用范围</label><textarea id="evidence-text" name="text" required maxlength="6000" ${disabled}>${escapeHtml(e.text)}</textarea><button class="button" type="submit" ${disabled}>保存转录记录</button>${!matter.sources?.length ? '<p class="small dirty-note">请先在来源区保存原话，再引用记录。</p>' : ''}</form></details></section>`;
}
function rowsMarkup(matter,draft,disabled) {
  if (!['expense','inventory'].includes(matter.domain)) return '';
  const expense = matter.domain === 'expense', unitKey = expense ? 'currency' : 'unit', unitLabel = expense ? '币种' : '单位';
  const rows = draft.rows ?? matter.rows ?? [], calculation = matter.calculation;
  const tableRows = rows.map((row,index) => `<tr data-row-id="${escapeHtml(row.id)}"><td>${index + 1}</td>${['label','value',unitKey,'period'].map((key) => `<td><input id="row-${escapeHtml(row.id)}-${key}" data-row-field="${key}" aria-label="第 ${index + 1} 行 ${escapeHtml({label:'费用名称／物品',value:expense ? '金额' : '数量',[unitKey]:unitLabel,period:'期间'}[key])}" value="${escapeHtml(row[key] ?? '')}" ${key === 'value' ? 'inputmode="decimal"' : ''} required ${disabled}></td>`).join('')}<td><select id="row-${escapeHtml(row.id)}-source_id" data-row-field="source_id" aria-label="第 ${index + 1} 行 来源" required ${disabled}>${matterSourceOptions(matter,row.source_id)}</select></td><td><input id="row-${escapeHtml(row.id)}-source_position" data-row-field="source_position" aria-label="第 ${index + 1} 行 来源位置" value="${escapeHtml(row.source_position ?? '')}" required ${disabled}></td><td><button class="button quiet" type="button" data-action="remove-row" data-row-id="${escapeHtml(row.id)}" aria-label="删除第 ${index + 1} 行" ${disabled}>删除</button></td></tr>`).join('');
  const groups = (calculation?.groups || []).map((group) => `<li><strong>${escapeHtml(expense ? group.currency : `${group.label} / ${group.unit}`)} · ${escapeHtml(group.period)}</strong><span>${escapeHtml(group.total)}${expense ? '' : ` ${escapeHtml(group.unit)}`} <small>· ${group.row_ids?.length ?? 0} 行</small></span></li>`).join('');
  return `<section class="panel"><details id="rows-details" ${draft.rowsOpen ? 'open' : ''}><summary class="details-toggle">${expense ? '费用' : '物资'}明细与可靠计算 <span class="muted">已保存 ${matter.rows?.length || 0} 行</span></summary><p class="scope-note">逐行核对名称、数值、${unitLabel}、期间和来源。空数值不能当零；${expense ? '币种和期间' : '物品、单位和期间'}不同则分别计算。保存仅表示本地人工核对，不等同报销批准、付款、入库或领用。</p>${expenseJourneyUI.markup(matter)}${tabularUI.markup(matter)}${modelRowsUI.markup(matter)}<form id="rows-form"><div class="table-scroll"><table class="rows-table"><thead><tr><th>行</th><th>${expense ? '费用名称' : '物品'}</th><th>${expense ? '金额' : '数量'}</th><th>${unitLabel}</th><th>期间</th><th>来源</th><th>来源位置</th><th>操作</th></tr></thead><tbody>${tableRows || '<tr><td colspan="8" class="muted">尚无明细。添加你已获得并有来源的信息。</td></tr>'}</tbody></table></div><div class="artifact-toolbar"><button class="button" type="button" data-action="add-row" ${disabled}>添加一行</button><button class="button primary" type="submit" ${disabled}>保存人工核对明细</button>${draft.rows !== null ? `<span class="small dirty-note" id="rows-saved-label">表格有未保存编辑</span><button class="button quiet" type="button" data-action="use-server-rows" ${disabled}>采用服务器明细</button>` : '<span class="small muted" id="rows-saved-label">显示已保存明细</span>'}</div></form><div class="calculation-result"><h3>已保存明细的计算结果</h3>${calculation?.row_count > 0 && Number.isInteger(calculation.input_revision) && calculation.input_revision > 0 ? `<button class="button quiet" type="button" data-action="download-rows-csv" ${disabled}>下载已保存明细 r${calculation.input_revision} CSV</button><p class="scope-note">取明确已保存明细修订，不含当前未保存表格；仅本地CSV文件，不更新Excel，也不代表批准、付款或收发。公式样式文字会加前置单引号作为文本，数值原字符串仍在CSV中；电子表格软件的显示格式需自行核对。导出含来源与修订等附加列，不保证可直接用于四列表格候选重导。</p>` : ''}<p id="calculation-draft-warning" class="small dirty-note" ${draft.rows !== null ? '' : 'hidden'}>以下结果不包含当前未保存编辑，保存核对后由程序重算。</p>${calculation?.stale ? '<p class="small dirty-note">事实／来源或明细修订已变化；下方是原已核行的历史计算，须逐项重核后再保存。</p>' : ''}<p class="small muted">${calculation ? `${escapeHtml(calculation.scope)} · ${escapeHtml(calculation.row_count)} 行 · 事实修订 ${escapeHtml(calculation.input_revision)}` : '明细尚未人工确认，未计算；未知数值不当零。'}</p><ul>${groups || '<li class="muted">尚无可展示的计算分组。</li>'}</ul></div>${rowReportsUI.markup(matter)}</details></section>`;
}
function workflowMarkup(matter,disabled) {return `<div class="workflow-control"><div><strong>持续本地整理：${matter.workflow?.enabled ? '已开启' : '未开启'}</strong><p class="small muted">开启后，补充事实、来源或核对明细后继续准备本地模板；不会自动调用模型。暂停或人工接手期间停止推进，每事项最多 20 轮后等待人工。</p></div><button class="button quiet" data-action="toggle-workflow" ${disabled}>${matter.workflow?.enabled ? '关闭持续整理' : '开启持续整理'}</button></div>`;}
function authenticated() {return Boolean(state.session) && state.session.authenticated !== false;}
function hasCapability(capability) {return state.session?.principal?.capabilities?.includes(capability) || false;}
function multiUser() {return state.session?.mode !== 'local_preview' && state.session?.authenticated !== undefined;}
function canCreateMatter() {return authenticated() && (!multiUser() || ['employee','clerk','cook'].some(hasCapability));}
function clearPrivate(session = null) {
  detailController?.abort();clearTimeout(pollTimer);clearTimeout(toastTimer);++state.identityEpoch;++state.selection;waitingUI.clear();remindersUI.clear();contextUI.clear();readingsUI.clear();sourceUI.clear();extractionUI.clear();tabularUI.clear();modelRowsUI.clear();modelChecksUI.clear();materialRecordsUI.clear();mealSummariesUI.clear();materialNodesUI.clear();rowReportsUI.clear();expenseJourneyUI.clear();relatedTasksUI.clear();privateContextUI.clear();modelJourneyUI.clear();menuModelUI.clear();modelPreviewUI.clear();sourceImpactUI.clear();attachmentsUI.clear();
  state.session = session;state.items = [];state.current = null;state.drafts.clear();state.menus = [];state.menuFeedback.clear();state.feedbackDrafts.clear();state.feedbackOpen = null;state.menuResponseDrafts = new Map();state.menuRevisionDrafts = new Map();state.accounts = [];state.accountDraft = {username:'',display_name:'',capabilities:[]};state.activationToken = null;state.catalog = {};state.error = null;state.difference = null;state.search = '';state.domainFilter = '';state.view = 'workspace';state.loading = false;state.busy = false;state.composer = {goal_text:'',domain:'auto',source_kind:'user_text',reported_by:'',auto_prepare:true,initial_mode:null,optionsOpen:false};
  $('#search').value = '';$('#toast').hidden = true;$('#toast').textContent = '';history.replaceState(null,'',location.pathname + location.search);
  state.legacySummary = null;state.authTab = 'login';
  state.services = [];state.serviceCurrent = null;state.serviceDrafts = new Map();state.serviceFollowupDrafts = new Map();state.serviceAccounts = {clerk:[],executor:[]};state.lostServiceId = null;
  state.ownFeedback = new Map();state.ownFeedbackDrafts = new Map();state.ownFeedbackErrors = new Map();
  state.structures = new Map();state.structureDrafts = new Map();state.structureProgressDrafts = new Map();state.structureErrors = new Map();
  state.meetingCandidates = new Map();
  state.journeys = new Map();state.journeyDrafts = new Map();state.journeyErrors = new Map();
  state.checks = new Map();state.checksDrafts = new Map();state.checksErrors = new Map();
  state.shareDrafts = new Map();state.ownShares = new Map();state.shareRecipients = [];state.shareRecipientsLoaded = false;state.shareRecipientsLoading = false;state.ownShareErrors = new Map();state.receivedShares = [];state.receivedShare = null;state.receivedShareError = null;
  state.sourceSearch = null;state.sourceHit = null;state.sourceSearchSerial = (state.sourceSearchSerial || 0) + 1;renderSourceSearch();
}
function updateChrome() {
  remindersUI.mount();
  if ($('#context-nav')) $('#context-nav').hidden = !canCreateMatter();
  if ($('#shares-nav')) $('#shares-nav').hidden = !authenticated() || !multiUser() || !['clerk','cook'].some(hasCapability);
  $('#principal-name').textContent = authenticated() ? state.session?.principal?.display_name || '' : '';
  $('#logout').hidden = !authenticated() || !multiUser();$('#menus-nav').hidden = !authenticated() || !['employee','cook'].some(hasCapability);$('#accounts-nav').hidden = !authenticated() || !state.session?.principal?.is_admin;$('#new-matter').hidden = !canCreateMatter();$('#domain-filter').hidden = !canCreateMatter();
  $('#model-status').textContent = authenticated() ? modelStatus() : '请登录';$('#model-status').title = authenticated() ? modelScope() : '';$('#search').disabled = !authenticated();
  $('#preview-mode').textContent = multiUser() ? '本地独立账号' : '本地预览';$('#workspace-mode').textContent = multiUser() ? '本地独立账号工作区' : '本地经办工作区';$('#workspace-principal').textContent = multiUser() ? authenticated() ? state.session?.principal?.display_name || '当前主体待核' : '请登录自己的账号' : '只整理准备，不执行正式业务';
  $('#services-nav').hidden = !authenticated() || !['employee','clerk','executor'].some(hasCapability);
}
function authMarkup() {
  const activate = state.authTab === 'activate', disabled = state.busy ? 'disabled' : '';
  return `<section class="auth-panel panel"><div class="eyebrow">本地独立身份工作区</div><h1>${activate ? '激活自己的账号' : '登录工作区'}</h1><p class="small muted">按实际授予的职责使用，私人目标和来源不会自动公开。账号登录不等于正式实名认证。</p>${errorMarkup()}${state.session?.bootstrap_required ? '<div class="inline-error">本工作区尚未创建管理员。请由有权者停止服务后，在本机使用 run.py --setup-admin 用户名交互建立；浏览器不创建特权账号。</div>' : ''}<form id="auth-form" class="auth-form">${activate ? '<label class="field-label" for="activation-token">管理员单独交付的一次性激活码</label><input id="activation-token" name="token" type="password" required autocomplete="off" maxlength="256">' : '<label class="field-label" for="username">账号</label><input id="username" name="username" required autocomplete="username" maxlength="200">'}<label class="field-label" for="password">${activate ? '自己设置密码（至少 12 字符）' : '密码'}</label><input id="password" name="password" type="password" required ${activate ? 'minlength="12"' : ''} maxlength="256" autocomplete="${activate ? 'new-password' : 'current-password'}"><button class="button primary" type="submit" ${disabled || !state.session ? 'disabled' : ''}>${state.busy ? '正在处理…' : activate ? '激活账号' : '登录'}</button></form><button class="button quiet" data-action="auth-tab">${activate ? '已有账号，返回登录' : '收到激活码，设置自己的密码'}</button><p class="scope-note">密码与激活码不写入地址、日志或浏览器本地存储；本应用不替人传送激活码。</p></section>`;
}
function accountMarkup() {
  const disabled = state.busy ? 'disabled' : '';
  return `<header class="matter-header"><div><div class="eyebrow">仅管理员可用</div><h1>本工作区账号</h1><p class="small muted">管理员管理本地授权，不因此获得员工私有内容或厨师发布权限。</p></div></header>${errorMarkup()}${state.activationToken ? `<section class="panel token-panel"><h2>一次性激活码 · 仅本次呈现</h2><p class="small">由实际管理员按原渠道单独交给账号本人，本人自行设置密码。本应用未发送任何消息。</p><code>${escapeHtml(state.activationToken)}</code><button class="button" data-action="dismiss-token">已记下，关闭激活码</button><p class="scope-note">关闭后不能从账号列表再次读取。</p></section>` : ''}<section class="panel"><h2>创建待激活账号</h2><form id="account-form"><div class="facts-grid"><div class="field"><label for="account-username">唯一用户名</label><input id="account-username" name="username" required maxlength="200" value="${escapeHtml(state.accountDraft.username)}" ${disabled}></div><div class="field"><label for="account-name">实际显示名</label><input id="account-name" name="display_name" required maxlength="200" value="${escapeHtml(state.accountDraft.display_name)}" ${disabled}></div></div><fieldset class="capability-options"><legend>仅选择已核定职责</legend>${Object.entries({employee:'员工',clerk:'综合经办',cook:'厨师',executor:'执行者'}).map(([key,label]) => `<label><input type="checkbox" name="capability" value="${key}" ${state.accountDraft.capabilities.includes(key) ? 'checked' : ''} ${disabled}>${label}</label>`).join('')}</fieldset><p class="scope-note">不由此表设置他人密码。执行者必要服务入口尚未开放，不授予其他人的私人事项。</p><button class="button primary" type="submit" ${disabled}>建立账号并生成一次性激活码</button></form></section><section class="panel"><h2>已登记账号</h2>${state.accounts.map((account) => `<div class="account-row"><strong>${escapeHtml(account.display_name)} · ${escapeHtml(account.username || account.username_key || '')}</strong><span>${escapeHtml(account.state || '待核')} · ${escapeHtml((account.capabilities || []).join('、') || '无业务职责')}${account.is_admin ? ' · 管理员' : ''}</span>${accountControls(account,disabled)}</div>`).join('') || '<p class="small muted">尚未读取账号列表。</p>'}<p class="scope-note">停用或重置会撤销原登录会话。重置仅生成新激活码，本人自行重新设密码；不替他人设置密码。</p></section>${legacyClaimMarkup()}`;
}
function accountControls(account,disabled) {
  const own = account.id === state.session?.principal?.id;
  return `<div class="admin-controls"><button class="button quiet" data-action="disable-account" data-account-id="${escapeHtml(account.id)}" ${disabled || own || account.state === 'disabled' ? 'disabled' : ''}>停用账号</button><button class="button quiet" data-action="reset-account" data-account-id="${escapeHtml(account.id)}" ${disabled || own ? 'disabled' : ''}>重置为待激活</button></div>`;
}
function legacyClaimMarkup() {
  const summary = state.legacySummary;
  if (!summary || !(summary.unassigned_count > 0)) return '';
  const clerks = state.accounts.filter((account) => account.state === 'active' && account.capabilities?.includes('clerk'));
  return `<section class="panel"><h2>保留的旧版成果 · 明确归属</h2><p class="small muted">${escapeHtml(summary.scope || '')}。仅显示未归属数量 ${escapeHtml(summary.unassigned_count)}，不展示旧私人正文。</p><form id="legacy-claim-form"><label class="field-label" for="legacy-target">由管理员核定归属给已激活的综合经办</label><select id="legacy-target" name="target_account_id" required ${state.busy ? 'disabled' : ''}><option value="">请选择实际经办账号</option>${clerks.map((account) => `<option value="${escapeHtml(account.id)}">${escapeHtml(account.display_name)} · ${escapeHtml(account.username || account.username_key || '')}</option>`).join('')}</select><label class="auto-prepare-label"><input type="checkbox" required>我已核定这些旧成果应由选定经办承接；原事实、人工稿和原操作者记录继续保留。</label><button class="button" type="submit" ${state.busy || !clerks.length ? 'disabled' : ''}>明确认领 ${escapeHtml(summary.unassigned_count)} 件旧成果</button></form>${!clerks.length ? '<p class="small muted">需先由实际经办激活已授予 clerk 职责的账号。</p>' : ''}</section>`;
}
function responseHistoryMarkup(responses = []) {
  return responses.length ? `<div class="cook-response-history"><h3>厨师本人文字回应</h3>${responses.map((response) => `<article class="evidence-record"><strong>${escapeHtml(response.cook_display_name || '实际厨师待核')} · ${response.old_feedback_scope ? '旧意见范围的历史回应' : '针对当前意见范围的回应'}</strong><p>${escapeHtml(response.response_text)}</p><small>所看菜单版本 ${escapeHtml(response.publication_version)} · 意见更正编号 ${escapeHtml(response.feedback_correction_count)} · ${escapeHtml(formatTime(response.recorded_at))}<br>${escapeHtml(response.scope || '厨师本人文字声明，不代表采纳、备餐、供餐或效果。')}</small></article>`).join('')}</div>` : '<p class="small muted">尚无厨师文字回应；未回应不表示已采纳或拒绝。</p>';
}
function cookResponseDraft(menu,feedback) {
  state.menuResponseDrafts ||= new Map();const key = `${menu.id}:${feedback.id}`;
  if (!state.menuResponseDrafts.has(key)) state.menuResponseDrafts.set(key,{publication_id:menu.id,feedback_id:feedback.id,expected_publication_version:menu.version,feedback_correction_count:feedback.correction_count ?? 0,response_text:''});
  return state.menuResponseDrafts.get(key);
}
function cookResponseMarkup(menu,feedback,available,disabled) {
  const history = responseHistoryMarkup(feedback.responses || []) + adoptionHistoryMarkup(feedback.adoptions || []);if (!available) return history;
  const draft = cookResponseDraft(menu,feedback);
  return `${history}<form class="cook-response-form" data-cook-response-menu="${escapeHtml(menu.id)}" data-cook-response-feedback="${escapeHtml(feedback.id)}"><label class="field-label" for="cook-response-${escapeHtml(feedback.id)}">本人明确回应这条意见（最多3000字）</label><textarea id="cook-response-${escapeHtml(feedback.id)}" name="response_text" required maxlength="3000" ${disabled}>${escapeHtml(draft.response_text)}</textarea><p class="scope-note">针对菜单版本 ${escapeHtml(draft.expected_publication_version)}、意见更正编号 ${escapeHtml(draft.feedback_correction_count)}；文字回应不代表已采纳、备餐或实际供餐。员工更正后旧范围回应只作历史。</p><div class="artifact-toolbar"><button class="button" type="submit" ${disabled}>明确保存厨师本人回应</button><button class="button quiet" type="button" data-action="reload-menu-feedback" data-menu-id="${escapeHtml(menu.id)}" ${disabled}>读取当前意见与差异</button></div></form>`;
}
function unsubmittedResponsesMarkup() {
  if (!hasCapability('cook')) return '';
  const orphaned = [...(state.menuResponseDrafts?.values() || [])].filter((draft) => draft.response_text && !(state.menus.some((menu) => menu.id === draft.publication_id && menu.status === 'published') && state.menuFeedback.get(draft.publication_id)?.some((feedback) => feedback.id === draft.feedback_id)));
  return orphaned.length ? `<section class="panel"><h2>本人尚未提交的回应文字</h2><p class="scope-note">原共享投影目前不可读／不可回应，已清空其意见内容。这里只保留你自己尚未提交的文字，不按旧意见范围继续提交。</p>${orphaned.map((draft) => `<textarea readonly aria-label="本人未提交回应">${escapeHtml(draft.response_text)}</textarea>`).join('')}</section>` : '';
}
async function loadMenuFeedback(id,{adoptVersion=false} = {}) {
  const menu = state.menus.find((item) => item.id === id);if (!menu || !hasCapability('cook') || menu.publisher_id !== state.session?.principal?.id) return;
  const epoch = state.identityEpoch,selection = state.selection;
  try {
    const result = await request(`/api/menu-publications/${encodeURIComponent(id)}/feedback`);if (epoch !== state.identityEpoch || selection !== state.selection) return;
    state.menuFeedback.set(id,result.items || []);menuModelUI.invalidate(id,result.items || []);state.feedbackOpen = id;
    for (const feedback of result.items || []) {const draft = state.menuResponseDrafts?.get(`${id}:${feedback.id}`);if (draft && (adoptVersion || !draft.response_text)) {draft.expected_publication_version = menu.version;draft.feedback_correction_count = feedback.correction_count ?? 0;}}
    if (state.view === 'menus') render();
  } catch (error) {if (epoch === state.identityEpoch && selection === state.selection) {if ([403,404].includes(error.status)) {state.menuFeedback.delete(id);if (state.feedbackOpen === id) state.feedbackOpen = null;}state.error = {message:error.message || '反馈暂不可读取，本人回应编辑保留。',status:error.status};if (state.view === 'menus') render();}}
}
function submitCookResponse(form) {
  const menu = state.menus.find((item) => item.id === form.dataset.cookResponseMenu),feedback = state.menuFeedback.get(menu?.id)?.find((item) => item.id === form.dataset.cookResponseFeedback);
  if (!menu || !feedback || menu.status !== 'published' || !hasCapability('cook') || menu.publisher_id !== state.session?.principal?.id) return;
  const draft = cookResponseDraft(menu,feedback);if (!draft.response_text.trim()) {toast('请填写厨师本人实际回应，不能把未回应记为采纳。');return;}
  auxiliaryWrite(`/api/menu-publications/${encodeURIComponent(menu.id)}/feedback/${encodeURIComponent(feedback.id)}/respond`,{expected_publication_version:draft.expected_publication_version,feedback_correction_count:draft.feedback_correction_count,response_text:draft.response_text.trim()},async (response) => {draft.response_text = '';const rows = state.menuFeedback.get(menu.id) || [];state.menuFeedback.set(menu.id,rows.map((item) => item.id === feedback.id ? {...item,responses:[...(item.responses || []),response]} : item));toast('已登记厨师本人文字回应；不表示采纳、实际供餐或效果。');await loadMenuFeedback(menu.id);});
}

function feedbackDraft(publication) {const key = `${publication.id}:${publication.version}`;if (!state.feedbackDrafts.has(key)) state.feedbackDrafts.set(key,{feedback_text:'',dish:'',dietary_constraint:''});return state.feedbackDrafts.get(key);}
function goalRevisionMarkup(matter,disabled) {
  const draft = draftFor(matter),edit = draft.goalEdit;
  const history = (matter.activities || []).filter(item=>item.event_type === 'goal_revised').slice(-20).reverse().map(item=>{try {return {...JSON.parse(item.message),recorded_at:item.recorded_at};} catch {return null;}}).filter(Boolean);
  if (!draft.goalEditorOpen && !history.length) return '';
  return `<section class="panel">${draft.goalEditorOpen && edit ? `<form id="goal-revision-form"><h2>修正当前委托</h2><label class="field-label" for="revised-goal-text">调整后的目标原话</label><textarea id="revised-goal-text" name="goal_text" maxlength="12000" required ${disabled}>${escapeHtml(edit.goal_text)}</textarea><fieldset><legend>需要重新核对的已有事实（只选受影响项）</legend>${Object.entries(labelsFor(matter)).filter(([field])=>matter.facts?.[field]?.value).map(([field,label])=>`<label class="small"><input type="checkbox" name="recheck_field" value="${escapeHtml(field)}" ${edit.recheck_fields.includes(field) ? 'checked' : ''} ${disabled}> ${escapeHtml(label)}：${escapeHtml(matter.facts[field].value)}</label>`).join('<br>') || '<p class="small muted">当前没有已填写的事实值。</p>'}</fieldset><p class="scope-note">保留未选择的已核事实；所选值保留并标待重核。旧目标原文和人工稿留作历史，原有暂不用来源仍保持控制。暂停／接手保持；既有批准、安排或处理记录不随目标改写。业务类型仍为${escapeHtml(domainLabel(matter.domain))}。</p><p class="small ${edit.expected_version !== matter.version ? 'dirty-note' : 'muted'}">编辑基版 ${escapeHtml(edit.expected_version)} · 当前已读事项版本 ${escapeHtml(matter.version)}。${edit.expected_version !== matter.version ? '基版已变化，原文字保留；请读回差异后核对采用当前基版。' : ''}</p><div class="artifact-toolbar"><button class="button primary" type="submit" ${disabled}>保存修正目标</button><button class="button quiet" type="button" data-action="adopt-goal-base" ${disabled}>核对后采用当前基版（文字保留）</button></div></form>` : ''}${history.length ? `<details><summary class="details-toggle">委托修正历史（最近20次，原文保留）</summary>${history.map(item=>`<article class="evidence-record"><strong>${escapeHtml(formatTime(item.recorded_at))} · 输入修订 ${escapeHtml(item.input_revision)}</strong><p>旧目标：${escapeHtml(item.before_goal)}</p><p>修正目标：${escapeHtml(item.after_goal)}</p><small>待重核：${escapeHtml((item.recheck_fields || []).map(field=>labelsFor(matter)[field] || field).join('、') || '未指定事实变化，保留已核事实')}</small></article>`).join('')}</details>` : ''}</section>`;
}
function menuRevisionMarkup(menu,disabled) {
  const draft = state.menuRevisionDrafts?.get(menu.id);
  if (!draft) return '';
  return `<form data-menu-revision="${escapeHtml(menu.id)}"><h3>修订本人公开菜单</h3><label class="field-label" for="menu-revision-menu_date-${escapeHtml(menu.id)}">日期</label><input type="date" id="menu-revision-menu_date-${escapeHtml(menu.id)}" name="menu_date" value="${escapeHtml(draft.menu_date)}" required ${disabled}><label class="field-label" for="menu-revision-meal_slot-${escapeHtml(menu.id)}">餐别</label><input id="menu-revision-meal_slot-${escapeHtml(menu.id)}" name="meal_slot" value="${escapeHtml(draft.meal_slot)}" maxlength="100" required ${disabled}><label class="field-label" for="menu-revision-dishes-${escapeHtml(menu.id)}">公开菜品</label><textarea id="menu-revision-dishes-${escapeHtml(menu.id)}" name="dishes" maxlength="6000" required ${disabled}>${escapeHtml(draft.dishes)}</textarea><label class="field-label" for="menu-revision-notes-${escapeHtml(menu.id)}">公开备注</label><textarea id="menu-revision-notes-${escapeHtml(menu.id)}" name="notes" maxlength="2000" ${disabled}>${escapeHtml(draft.notes)}</textarea>${adoptionSelectionMarkup(menu,state.menuFeedback.get(menu.id)||[],draft,disabled)}<p class="scope-note">基于版本 ${escapeHtml(draft.expected_version)}。保存会替代当前本地发布；旧菜单及意见各自保留，不迁移到新菜品。私人事实、人工稿和实际供餐状态保持。</p><button class="button primary" type="submit" ${disabled || (menu.status !== 'published' ? 'disabled' : '')}>保存并发布修订</button>${menu.status !== 'published' ? '<p class="dirty-note">原菜单已撤回或被替代，编辑仍保留；请在当前发布菜单上核对修订。</p>' : ''}</form>`;
}
function submitMenuRevision(form) {
  const menu = state.menus.find((item) => item.id === form.dataset.menuRevision), draft = state.menuRevisionDrafts?.get(menu?.id);
  if (!menu || !draft || !hasCapability('cook') || menu.publisher_id !== state.session?.principal?.id || menu.status !== 'published') return;
  const body = {expected_version:draft.expected_version,menu_date:draft.menu_date,meal_slot:draft.meal_slot,dishes:draft.dishes,notes:draft.notes,adoptions:adoptionPayload(draft)};
  auxiliaryWrite(`/api/menu-publications/${encodeURIComponent(menu.id)}/revise`,body,async () => {state.menuRevisionDrafts.delete(menu.id);toast('菜单修订已保存；旧菜单与旧意见保留，新版反馈分别收集。');await refreshMenus();});
}
function menusMarkup() {
  const disabled = state.busy ? 'disabled' : '';
  return `<header class="matter-header"><div><div class="eyebrow">明确公开字段 · 本工作区</div><h1>已发布菜单</h1><p class="small muted">厨师主动发布的本地快照，不表示实际备餐或供餐。意见完全自愿，只提交给对应厨师。</p></div><button class="button" data-action="refresh-menus" ${disabled}>刷新菜单</button></header>${errorMarkup()}${state.menus.map((menu) => {
    const own = menu.publisher_id === state.session?.principal?.id && hasCapability('cook'), available = !menu.status || menu.status === 'published', draft = feedbackDraft(menu);
    return `<section class="panel menu-publication"><div class="panel-head"><h2>${escapeHtml(menu.menu_date)} · ${escapeHtml(menu.meal_slot)}</h2><span class="artifact-meta">${escapeHtml(menu.publisher_name)} · 版本 ${escapeHtml(menu.version)}</span></div><span class="status-pill ${available ? '' : 'paused'}">${available ? (menu.previous_publication_id ? '本人修订后发布' : '本地已发布') : (menu.replacement_publication_id ? '已被新版替代的历史快照' : '本人已撤回的历史快照')}</span><p class="menu-dishes">${escapeHtml(menu.dishes)}</p>${menu.notes ? `<p class="small">公开备注：${escapeHtml(menu.notes)}</p>` : ''}<p class="scope-note">发布于 ${escapeHtml(formatTime(menu.published_at))}；只显示本次安全发布快照，不读取原事项和私人来源。</p>${own ? `<div class="artifact-toolbar"><button class="button" data-action="view-menu-feedback" data-menu-id="${escapeHtml(menu.id)}" ${disabled}>读取本菜单自愿反馈</button>${available ? `<button class="button" data-action="revise-menu" data-menu-id="${escapeHtml(menu.id)}" ${disabled}>修订公开菜单</button><button class="button quiet" data-action="withdraw-menu" data-menu-id="${escapeHtml(menu.id)}" ${disabled}>撤回本次发布</button>` : ''}</div>${menuRevisionMarkup(menu,disabled)}${state.feedbackOpen === menu.id ? `<div class="menu-feedback-records">${feedbackReviewMarkup(menu,state.menuFeedback.get(menu.id) || [])}${menuModelUI.markup(menu,state.menuFeedback.get(menu.id) || [])}<h3>仅对应厨师可读的实际反馈</h3>${(state.menuFeedback.get(menu.id) || []).map((feedback) => `<article class="evidence-record"><strong>${escapeHtml(feedback.submitted_by || '提交者待核')} · 所看版本 ${escapeHtml(feedback.version)}</strong><p>${escapeHtml(feedback.feedback_text)}</p>${feedback.dish ? `<small>菜品：${escapeHtml(feedback.dish)}</small><br>` : ''}${feedback.dietary_constraint ? `<small>本人明确约束：${escapeHtml(feedback.dietary_constraint)}</small><br>` : ''}<small>${escapeHtml(formatTime(feedback.submitted_at))}${feedback.corrected_by_self ? ` · 本人已明确更正 · ${escapeHtml(formatTime(feedback.corrected_at))}` : ''}</small>${cookResponseMarkup(menu,feedback,available,disabled)}</article>`).join('') || '<p class="small muted">暂无已提交反馈；未反馈不表示无意见。</p>'}</div>` : ''}` : ''}${available && hasCapability('employee') ? `<details class="menu-feedback-form"><summary class="details-toggle">自愿向本菜单厨师反馈</summary><form data-feedback-menu="${escapeHtml(menu.id)}"><label class="field-label" for="feedback-${escapeHtml(menu.id)}">意见原话（必填）</label><textarea id="feedback-${escapeHtml(menu.id)}" name="feedback_text" required maxlength="6000" ${disabled}>${escapeHtml(draft.feedback_text)}</textarea><label class="field-label">相关菜品（可选）<input name="dish" value="${escapeHtml(draft.dish)}" maxlength="500" ${disabled}></label><label class="field-label">本人明确过敏／忌口（可选，仅给本菜单厨师）<input name="dietary_constraint" value="${escapeHtml(draft.dietary_constraint)}" maxlength="2000" ${disabled}></label><p class="scope-note">你看到的是版本 ${escapeHtml(menu.version)}。提交不改变菜单、采购或供餐状态，不要求每天评价。</p><button class="button" type="submit" ${disabled}>提交给本菜单厨师</button></form></details>` : ''}</section>`;
  }).join('') || '<section class="panel"><p class="small muted">当前没有可读取的已发布菜单。</p></section>'}${unsubmittedResponsesMarkup()}`;
}
function menuPublishMarkup(matter,draft,disabled) {
  if (matter.domain !== 'menu' || !hasCapability('cook')) return '';
  const ready = ['menu_date','meal_slot','dishes'].every((key) => matter.facts?.[key]?.value && matter.facts[key].status === 'confirmed');
  return `<section class="panel"><h2>明确发布菜单快照</h2><p class="scope-note">只公开下方日期、餐别、菜品和你单独填写的备注给本工作区员工；不发布准备稿全文、来源原话、私人过敏、电话或材料。此操作不是微信群发布、备餐或实供。</p><form id="menu-publish-form"><div class="public-menu-preview">${['menu_date','meal_slot','dishes'].map((key) => `<p><strong>${escapeHtml(labelsFor(matter)[key] || key)}</strong><span>${escapeHtml(matter.facts?.[key]?.value || '待核；请先核对事实')}</span></p>`).join('')}</div><label class="field-label" for="menu-public-notes">另行公开的备注（默认空，请勿填他人私人信息）</label><textarea id="menu-public-notes" name="notes" maxlength="2000" ${disabled}>${escapeHtml(draft.publicNotes)}</textarea>${ready ? '' : '<p class="small dirty-note">请先在“核对事实”中保存确认日期、餐别和菜品，再明确发布。</p>'}<button class="button primary" type="submit" ${disabled || !ready ? 'disabled' : ''}>发布给本工作区员工</button></form></section>`;
}
function ownFeedbackState() {state.ownFeedback ||= new Map();state.ownFeedbackDrafts ||= new Map();state.ownFeedbackErrors ||= new Map();}
function ownFeedbackDraft(record) {ownFeedbackState();if (!state.ownFeedbackDrafts.has(record.id)) state.ownFeedbackDrafts.set(record.id,{feedback_text:record.feedback_text,dish:record.dish || '',dietary_constraint:record.dietary_constraint || '',baseVersion:record.expected_version,dirty:false});return state.ownFeedbackDrafts.get(record.id);}
function ownFeedbackMarkup(matter,disabled) {
  if (matter.domain !== 'feedback' || !hasCapability('employee')) return '';
  ownFeedbackState();const record = state.ownFeedback.get(matter.id), error = state.ownFeedbackErrors.get(matter.id);
  if (!record) return error ? `<section class="panel"><h2>已提交意见的独立投影</h2><p class="small dirty-note">${escapeHtml(error)}；私人事实和人工稿仍保留。</p><button class="button" data-action="reload-own-feedback" ${disabled}>重新读取提交状态</button></section>` : '';
  const draft = ownFeedbackDraft(record), shared = record.shared_with_publisher ?? record.shared;
  return `<section class="panel"><div class="panel-head"><h2>已提交给厨师的意见</h2><button class="button quiet" data-action="reload-own-feedback" ${disabled}>读取独立提交状态</button></div><span class="status-pill ${shared ? '' : 'paused'}">${shared ? '仅向原菜单厨师共享' : '已撤回厨师共享，仅本人私存'}</span><p class="scope-note">所看菜单版本 ${escapeHtml(record.publication_version)} · 提交于 ${escapeHtml(formatTime(record.submitted_at))}。普通私人事实编辑不会更改此提交投影。${record.corrected_by_self ? `本人已明确更正 ${escapeHtml(record.correction_count)} 次 · ${escapeHtml(formatTime(record.corrected_at))}` : ''}</p>${record.menu_snapshot ? `<details><summary class="details-toggle">所反馈的原菜单快照</summary><p>${escapeHtml(record.menu_snapshot.menu_date)} · ${escapeHtml(record.menu_snapshot.meal_slot)} · 所看版本 ${escapeHtml(record.publication_version)}</p><p>${escapeHtml(record.menu_snapshot.dishes)}</p>${record.menu_snapshot.notes ? `<p>${escapeHtml(record.menu_snapshot.notes)}</p>` : ''}${record.menu_snapshot.replacement_publication_id ? '<p class="scope-note">此菜单已有修订版；本意见仍属于原菜单，不计入新版。</p>' : ''}</details>` : ''}<div class="submitted-feedback-snapshot"><strong>当前${shared ? '共享' : '本人私存'}记录</strong><p>${escapeHtml(record.feedback_text)}</p>${record.dish ? `<small>相关菜品：${escapeHtml(record.dish)}</small><br>` : ''}${record.dietary_constraint ? `<small>本人明确约束：${escapeHtml(record.dietary_constraint)}</small>` : ''}</div>${responseHistoryMarkup(record.responses || [])}${adoptionHistoryMarkup(record.adoptions || [])}<form id="own-feedback-form" class="source-form"><label class="field-label" for="own-feedback-text">本人明确更正的意见原话</label><textarea id="own-feedback-text" name="feedback_text" required maxlength="6000" ${disabled}>${escapeHtml(draft.feedback_text)}</textarea><label class="field-label" for="own-feedback-dish">相关菜品（可选）</label><input id="own-feedback-dish" name="dish" maxlength="500" value="${escapeHtml(draft.dish)}" ${disabled}><label class="field-label" for="own-feedback-constraint">本人明确过敏／忌口（可选）</label><input id="own-feedback-constraint" name="dietary_constraint" maxlength="2000" value="${escapeHtml(draft.dietary_constraint)}" ${disabled}><p class="scope-note">${shared ? '点击后才更新给原厨师的意见；不更改菜单、采购或供餐状态。' : '共享已撤回，此后更正只保存本人记录，不会重新公开给厨师。'}</p><div class="artifact-toolbar"><button class="button" type="submit" ${disabled}>${shared ? '明确更正已提交给厨师的意见' : '保存本人私有更正'}</button>${shared ? `<button class="button quiet" type="button" data-action="withdraw-own-feedback" ${disabled}>撤回给厨师的意见共享</button>` : ''}<span id="own-feedback-saved" class="small ${draft.dirty ? 'dirty-note' : 'muted'}">${draft.dirty ? '有未提交的更正，当前投影仍保留原记录' : '显示已保存记录'}</span></div></form></section>`;
}
const serviceTrackNames = {assignment:'软件分派',acceptance:'执行者本人承接',execution:'执行者处理声明',requester_result:'发起人本人结果',inspection:'专业验收',closure:'正式关闭'};
const serviceValueNames = {unassigned:'未分派',assigned:'已由经办软件分派',withdrawn:'已撤回软件安排',pending:'等待本人回应',accepted:'本人已接受',declined:'本人已拒接',not_reported:'未登记处理声明',in_progress:'本人声明处理中',blocked:'本人报告障碍',claimed_done:'执行者声明已处理',unknown:'待核',unverified:'发起人尚未验证',resolved:'发起人称已解决',still_problem:'发起人称仍有问题',open:'未正式关闭',waiting_rule:'等待关闭口径'};
function serviceTrack(service,key) {const track = service.tracks?.[key];return typeof track === 'string' ? track : track?.status || 'unknown';}
function serviceAccountOptions(capability,selected) {serviceState();return `<option value="">请选择已授予职责的实际账号</option>${state.serviceAccounts[capability].map((account) => `<option value="${escapeHtml(account.id)}" ${account.id === selected ? 'selected' : ''}>${escapeHtml(account.display_name)}</option>`).join('')}`;}
function projectionMarkup(projection) {
  const permission = projection.entry_permission || {status:'unknown'};
  return `<div class="service-projection"><div><strong>服务类型</strong><span>${escapeHtml(domainLabel(projection.domain))}</span></div><div><strong>需求性质</strong><span>${escapeHtml({temporary:'临时需求',routine_ref:'例行安排引用',unknown:'性质待核'}[projection.request_kind] || '性质待核')}</span></div><div><strong>地点</strong><span>${escapeHtml(projection.location || '待核')}</span></div><div><strong>本次需求</strong><span>${escapeHtml(projection.request_text || '待核')}</span></div><div><strong>可进入时段</strong><span>${escapeHtml(projection.access_window || '时段待核')}</span></div><div><strong>进入许可</strong><span class="${permission.status === 'unknown' ? 'dirty-note' : ''}">${permission.status === 'reported_permission' ? '只有原述许可，仍须按实际范围核对' : '进入许可待核，不能据此入室'}${permission.status === 'reported_permission' && permission.note ? ` · ${escapeHtml(permission.note)}` : ''}</span></div>${projection.contact ? `<div><strong>本次必要联系</strong><span>${escapeHtml(projection.contact)}</span></div>` : ''}${projection.notes ? `<div><strong>明确共享备注</strong><span>${escapeHtml(projection.notes)}</span></div>` : ''}</div>`;
}
function serviceSubmitMarkup(matter,disabled) {
  if (!multiUser() || !['repair','cleaning'].includes(matter.domain) || !['employee','clerk'].some(hasCapability)) return '';
  const draft = serviceSubmitDraft(matter), field = matter.domain === 'repair' ? 'problem' : 'scope';
  const ready = ['location',field].every((key) => matter.facts?.[key]?.status === 'confirmed' && matter.facts[key].value);
  const projection = {location:matter.facts?.location?.value,request_text:matter.facts?.[field]?.value,domain:matter.domain,access_window:matter.facts?.access_window?.status === 'confirmed' ? matter.facts.access_window.value : '',request_kind:matter.facts?.request_kind?.status === 'confirmed' ? matter.facts.request_kind.value : 'unknown',contact:draft.contact,notes:draft.notes,entry_permission:{status:draft.permission_status,note:draft.permission_note}};
  return `<section class="panel"><details id="service-submit-details" ${draftFor(matter).serviceSubmitOpen ? 'open' : ''}><summary class="details-toggle">明确交给经办处理 · 固定必要投影</summary><p class="scope-note">只共享下方必要服务信息给选定经办及其明确分派的执行者。私人目标、来源、完整成果和其他材料不共享；不等于现场批准或派单系统同步。</p><div id="service-share-preview">${projectionMarkup(projection)}</div><form id="service-submit-form" class="source-form"><label class="field-label" for="service-clerk">一个实际综合经办账号</label><select id="service-clerk" name="clerk_id" required ${disabled}>${serviceAccountOptions('clerk',draft.clerk_id)}</select><button class="button quiet" type="button" data-action="load-service-clerks" ${disabled}>读取可选经办</button><label class="field-label" for="service-contact">本次明确允许服务人员使用的必要联系（默认空）</label><input id="service-contact" name="contact" maxlength="500" value="${escapeHtml(draft.contact)}" ${disabled}><label class="field-label" for="service-permission">原述进入许可</label><select id="service-permission" name="permission_status" ${disabled}><option value="unknown" ${draft.permission_status === 'unknown' ? 'selected' : ''}>未知，不能据此入室</option><option value="reported_permission" ${draft.permission_status === 'reported_permission' ? 'selected' : ''}>有实际原述许可，仍待核范围</option></select><input name="permission_note" ${draft.permission_status === 'reported_permission' ? 'required' : ''} aria-label="进入许可原述与适用范围" placeholder="有原述许可时写明实际依据与范围" value="${escapeHtml(draft.permission_note)}" maxlength="1000" ${disabled}><label class="field-label" for="service-submit-notes">另行明确共享的备注（默认空）</label><textarea id="service-submit-notes" name="notes" maxlength="2000" ${disabled}>${escapeHtml(draft.notes)}</textarea>${!ready ? '<p class="small dirty-note">先在核对事实中保存确认地点及问题／清洁范围；无照片和主管审批也可准备。</p>' : ''}<button class="button primary" type="submit" ${disabled || !ready ? 'disabled' : ''}>交给选定经办处理</button></form></details></section>`;
}
function servicesMarkup() {
  serviceState();const service = state.serviceCurrent, disabled = state.busy ? 'disabled' : '';
  const list = `<div class="service-list">${state.services.map((item) => `<button class="matter-item ${item.id === service?.id ? 'selected' : ''}" data-action="open-service" data-service-id="${escapeHtml(item.id)}"><span class="matter-title">${escapeHtml(item.projection?.location || '服务地点待核')} · ${escapeHtml(item.projection?.request_text || '服务需求')}</span><span class="matter-meta">${escapeHtml(domainLabel(item.projection?.domain))} · ${escapeHtml(serviceValueNames[serviceTrack(item,'execution')] || '待核')}</span></button>`).join('') || '<p class="small muted">当前没有可读取的服务，未分派不等于已处理。</p>'}</div>`;
  let content = '';
  if (service) {
    const draft = serviceDraft(service), actor = state.session?.principal?.id, requester = actor === service.requester_id, clerk = actor === service.clerk_id && hasCapability('clerk'), executor = actor === service.executor_id && hasCapability('executor'), shared = service.sharing_status !== 'withdrawn', accepted = serviceTrack(service,'acceptance') === 'accepted', pending = serviceTrack(service,'acceptance') === 'pending';
    const trackMarkup = Object.entries(serviceTrackNames).map(([key,label]) => `<div class="track"><div class="track-name">${label}</div><div class="track-status">${escapeHtml(serviceValueNames[serviceTrack(service,key)] || serviceTrack(service,key))}</div></div>`).join('');
    const input = (name,label,value,extra='') => `<label class="field-label" for="service-${name}">${label}</label>${name.endsWith('_note') || name === 'evidence_refs' ? `<textarea id="service-${name}" name="${name}" maxlength="${name === 'evidence_refs' ? 2000 : 6000}" ${extra} ${disabled}>${escapeHtml(value)}</textarea>` : `<input id="service-${name}" name="${name}" value="${escapeHtml(value)}" ${extra} ${disabled}>`}`;
    content = `<section class="panel"><div class="panel-head"><h2>${escapeHtml(domainLabel(service.projection?.domain))} · 必要服务工作区</h2><button class="button" data-action="reload-service" ${disabled}>读取最新状态</button></div><p class="scope-note">${escapeHtml(service.scope || '独立账号的软件动作与本人声明，不证明现场执行。')} · 服务版本 ${escapeHtml(service.version)} · 分派轮次 ${escapeHtml(service.assignment_epoch)} · 原事实修订 ${escapeHtml(service.input_revision)}</p>${service.stale ? '<p class="dirty-note small">原事实修订已变化，当前仍是旧服务快照；由发起人明确更新需求，经办重新确认安排。</p>' : ''}${!shared ? '<p class="dirty-note small">服务共享已撤回，保留本人已存历史；撤回不证明现场工作已停止。</p>' : ''}${projectionMarkup(service.projection || {})}<section class="tracks">${trackMarkup}</section><div class="service-actions">${clerk && shared ? `<form id="service-assign-form"><h3>经办明确分派／重新安排</h3><select name="executor_id" required ${disabled}>${serviceAccountOptions('executor',draft.executor_id)}</select><button class="button quiet" type="button" data-action="load-service-executors" ${disabled}>读取可选执行者</button>${input('assign_note','实际安排说明（已有安排时需说明改派原因）',draft.assign_note,service.executor_id ? 'required' : '')}<button class="button" type="submit" ${disabled}>明确分派给选定执行者</button></form>` : ''}${executor && shared ? `<form id="service-respond-form"><h3>仅登记本人承接与实际声明</h3><label class="field-label" for="service-respond_note">本人真实说明（障碍／已处理声明必填）</label><textarea id="service-respond_note" name="respond_note" maxlength="6000" ${disabled}>${escapeHtml(draft.respond_note)}</textarea>${input('occurred_at','实际发生日期／时间（可选，不猜相对时间）',draft.occurred_at,'placeholder="2026-10-08T14:00:00+01:00"')}<div class="artifact-toolbar"><button class="button" type="submit" data-command="accept" ${disabled || !pending ? 'disabled' : ''}>本人接受</button><button class="button quiet" type="submit" data-command="decline" ${disabled || !pending ? 'disabled' : ''}>本人拒接</button><button class="button" type="submit" data-command="start" ${disabled || !accepted || serviceTrack(service,'execution') === 'claimed_done' ? 'disabled' : ''}>本人声明开始处理</button><button class="button" type="submit" data-command="blocked" ${disabled || !accepted ? 'disabled' : ''}>本人报告障碍</button><button class="button" type="submit" data-command="claimed_done" ${disabled || !accepted ? 'disabled' : ''}>本人声明已处理</button></div><p class="scope-note">暂停 AI 不禁止有权者如实登记；已处理声明不会替发起人确认解决或专业验收。</p></form>` : ''}${requester && shared ? `<form id="service-result-form"><h3>发起人本人核对结果</h3><select name="result" ${disabled}>${Object.entries({unverified:'本人尚未验证',resolved:'本人核对后认为已解决',still_problem:'本人仍发现问题'}).map(([key,label]) => `<option value="${key}" ${key === draft.result ? 'selected' : ''}>${label}</option>`).join('')}</select>${input('result_note','本人实际说明（可选）',draft.result_note)}<button class="button" type="submit" ${disabled}>保存本人结果</button></form><details><summary class="details-toggle">明确更新服务需求</summary><form id="service-revise-form"><p class="scope-note">先在原私人事项核对并保存新事实，再采用其修订编号。更新会使旧接受失效，等待经办重新安排。</p>${input('input_revision','已保存的新事实修订编号',draft.input_revision,'type="number" min="1" step="1" required')}${input('revise_note','实际变化说明',draft.revise_note,'required')}<button class="button" type="submit" ${disabled}>明确更新需求并重核安排</button></form></details>` : ''}${(requester || clerk) && shared ? `<details><summary class="details-toggle">代录原话／撤回共享或安排</summary><form id="service-statement-form"><h3>实际代录声明（不改变本人状态）</h3>${input('reported_by','实际原陈述人',draft.reported_by,'required')}${input('statement_note','实际获得的声明原话',draft.statement_note,'required')}${input('evidence_refs','凭据候选引用（可选，每行一项）',draft.evidence_refs)}<button class="button" type="submit" ${disabled}>保存代录候选</button></form><form id="service-withdraw-form"><h3>${requester ? '发起人撤回服务共享' : '经办撤回执行者安排'}</h3>${input('withdraw_note','实际撤回原因；撤回不证明现场停止',draft.withdraw_note,'required')}<button class="button" type="submit" ${disabled}>${requester ? '明确撤回服务共享' : '明确撤派'}</button></form></details>` : ''}</div><p class="small muted">专业验收与关闭口径尚未核定；已处理声明、本人认为解决和正式关闭分别记录。</p><button class="button" disabled title="专业验收／清洁关闭规则尚未核定">正式闭单 · 等待业务口径</button></section><section class="panel"><h2>等待与下一步</h2><ul class="waiting-list">${(service.waiting || []).map((item) => `<li>${typeof item === 'string' ? escapeHtml(item) : `<strong>${escapeHtml(item.who_name || item.who || '责任对象待核')}</strong>${escapeHtml(item.reason || '')}`}</li>`).join('') || '<li>按当前独立状态核对实际责任人。</li>'}</ul><ol class="next-list">${(service.next_steps || []).map((step) => `<li>${escapeHtml(step)}</li>`).join('')}</ol></section>${serviceFollowupMarkup(service,disabled)}<section class="panel"><h2>本人获授权可见的服务事件</h2>${(service.events || []).map((event) => `<article class="evidence-record"><strong>${escapeHtml(event.command || event.action || '实际记录')} · 分派轮次 ${escapeHtml(event.assignment_epoch ?? '待核')}</strong><p>${escapeHtml(event.note || '')}</p><small>实际操作者：${escapeHtml(event.actor_display_name || event.actor_id || event.recorded_by || '待核')}${event.reported_by ? ` · 原陈述人：${escapeHtml(event.reported_by)}` : ''} · ${escapeHtml(formatTime(event.recorded_at || event.occurred_at))}</small></article>`).join('') || '<p class="small muted">暂无当前授权范围内事件。</p>'}</section>`;
  } else if (state.lostServiceId) {const draft = state.serviceDrafts.get(state.lostServiceId);content = `<section class="panel"><h2>原服务当前无读取权限</h2><p class="small muted">原投影与历史已从本页清空；只保留你本人未提交的说明，可按原渠道交当前经办。不要以旧状态继续回应。</p><textarea readonly aria-label="本人未提交说明">${escapeHtml(draft?.respond_note || draft?.statement_note || draft?.withdraw_note || '')}</textarea></section>`;}
  return `<header class="matter-header"><div><div class="eyebrow">按实际账号承接 · 不新增批准层</div><h1>待办服务</h1></div><button class="button" data-action="refresh-services" ${disabled}>刷新本人服务</button></header>${errorMarkup()}${list}${content || '<section class="panel"><p class="small muted">选择一个获授权服务，查看必要需求和本人下一步。</p></section>'}`;
}
function toast(message) {clearTimeout(toastTimer);$('#toast').textContent = message;$('#toast').hidden = false;toastTimer = setTimeout(() => {$('#toast').hidden = true;},3200);}
async function ensureIdentity() {
  if (!multiUser() || !authenticated()) return true;
  const epoch = state.identityEpoch, previousId = state.session?.principal?.id;
  const authorization = (session) => JSON.stringify({admin:session?.principal?.is_admin,caps:[...(session?.principal?.capabilities || [])].sort()});
  const fresh = await request('/api/session');
  if (epoch !== state.identityEpoch) return false;
  if (fresh.authenticated === false || fresh.principal?.id !== previousId || authorization(fresh) !== authorization(state.session)) {
    clearPrivate(fresh);state.error = {message:'登录主体已变化或失效，前一账号未保存私人编辑已清空；已保存记录仍在原账号。'};render();
    if (fresh.authenticated !== false) await startWorkspace();
    return false;
  }
  state.session = fresh;return true;
}
async function request(path,options = {}) {
  const identityEpoch = state.identityEpoch;
  if (!path.startsWith('/api/auth/') && path !== '/api/session' && !await ensureIdentity()) throw {name:'AbortError',code:'identity_changed',message:'主体已变化，旧请求已停止。'};
  if (options.headers?.['X-CSRF-Token'] && !path.startsWith('/api/auth/')) options.headers['X-CSRF-Token'] = state.session?.csrf_token || '';
  let response;
  try {response = await fetch(path,{credentials:'same-origin',...options});}
  catch (error) {if (error.name === 'AbortError') throw error;throw {code:'network_error',message:'连接失败。输入已保留，请检查服务后重试。',retryable:true};}
  let data;
  try {data = await response.json();}
  catch {throw {code:'invalid_response',message:'服务未返回可读取的结果。输入已保留。',retryable:true};}
  if (identityEpoch !== state.identityEpoch) throw {name:'AbortError',code:'identity_changed',message:'登录主体已改变，旧响应已丢弃。'};
  if (!response.ok) {
    if (response.status === 401 && !['/api/auth/login','/api/auth/activate','/api/session'].includes(path)) {
      const mode = state.session?.mode;clearPrivate({authenticated:false,mode});state.error = {message:'登录已失效。已保存内容保留在服务端，请重新登录；本页未保存私人编辑已清空。'};render();
      const epoch = state.identityEpoch;request('/api/session').then((session) => {if (epoch === state.identityEpoch) {state.session = session;if (authenticated()) startWorkspace();else render();}}).catch(() => {});
    }
    throw {...data,status:response.status};
  }
  return data;
}
function writeOptions(body) {return {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.session?.csrf_token || '', 'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify(body)};}
async function auxiliaryWrite(path,body,onSuccess) {
  if (state.busy || !authenticated()) return;
  const epoch = state.identityEpoch;state.busy = true;state.error = null;render();
  try {const result = await request(path,writeOptions(body));if (epoch === state.identityEpoch) await onSuccess?.(result);}
  catch (error) {if (epoch === state.identityEpoch) {
    state.error = {message:error.message || '操作未完成，输入仍保留。',status:error.status};
    if (path.startsWith('/api/menu-publications/') && [403,404].includes(error.status)) {const id = path.split('/')[3];state.menus = state.menus.filter((menu) => menu.id !== id);state.menuFeedback.delete(id);refreshMenus();}
    if (path.startsWith('/api/shares/') && [403,404].includes(error.status)) {const id = path.split('/')[3];clearReceivedShare(id);for (const [matterId,shares] of state.ownShares || []) state.ownShares.set(matterId,shares.filter((share) => share.id !== id));}
    if (path.startsWith('/api/services/') && [403,404].includes(error.status)) {serviceState();const id = path.split('/')[3];state.serviceCurrent = null;state.lostServiceId = id;state.services = state.services.filter((service) => service.id !== id);}
  }}
  finally {if (epoch === state.identityEpoch) {state.busy = false;render();schedulePoll();}}
}
async function authOperation(form) {
  if (state.busy || !state.session) return;
  const fields = new FormData(form), activate = state.authTab === 'activate';
  const body = activate ? {token:fields.get('token'),password:fields.get('password')} : {username:fields.get('username'),password:fields.get('password')};
  const options = writeOptions(body), epoch = state.identityEpoch;form.reset();state.busy = true;state.error = null;render();
  try {
    const result = await request(activate ? '/api/auth/activate' : '/api/auth/login',options);
    if (epoch !== state.identityEpoch) return;
    if (activate) {state.authTab = 'login';toast('账号已激活，请使用自己的账号和密码登录。');state.session = await request('/api/session');}
    else {clearPrivate(result);await startWorkspace();}
  } catch (error) {if (epoch === state.identityEpoch) state.error = {message:error.message || '登录或激活未完成。',status:error.status};}
  finally {body.password = '';if (activate) body.token = '';options.body = '';if (epoch === state.identityEpoch) {state.busy = false;render();}}
}
async function logout(csrfToken = state.session?.csrf_token) {
  const mode = state.session?.mode;clearPrivate({authenticated:false,mode,csrf_token:csrfToken});state.busy = true;render();
  const epoch = state.identityEpoch;
  try {await request('/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken || '', 'Idempotency-Key':crypto.randomUUID()},body:'{}'});if (epoch === state.identityEpoch) {state.session = await request('/api/session');state.busy = false;render();}}
  catch (error) {if (epoch === state.identityEpoch) {state.busy = false;state.error = {message:'本页私人内容已清空，但服务端退出尚未确认。请重试退出。',retryable:true,retry:() => logout(csrfToken)};render();}}
}
async function refreshMenus() {
  if (!authenticated() || !['employee','cook'].some(hasCapability)) return;
  const epoch = state.identityEpoch;
  try {const result = await request('/api/menu-publications');if (epoch === state.identityEpoch) {state.menus = (result.items || []).filter((menu) => !menu.status || menu.status === 'published' || menu.status === 'withdrawn' && hasCapability('cook') && menu.publisher_id === state.session?.principal?.id);render();}}
  catch (error) {if (epoch === state.identityEpoch) {state.error = {message:error.message || '菜单暂不可读取。'};render();}}
}
async function refreshAccounts() {
  if (!state.session?.principal?.is_admin) return;
  const epoch = state.identityEpoch;
  try {const [result,legacy] = await Promise.all([request('/api/admin/accounts'),request('/api/admin/legacy')]);if (epoch === state.identityEpoch) {state.accounts = result.items || [];state.legacySummary = legacy;render();}}
  catch (error) {if (epoch === state.identityEpoch) {state.error = {message:error.message || '账号列表暂不可读取。'};render();}}
}
async function readOwnFeedback(matterId,{adoptVersion=false,background=false} = {}) {
  if (!multiUser() || !hasCapability('employee')) return;
  ownFeedbackState();const epoch = state.identityEpoch, selection = state.selection;
  try {
    const record = await request(`/api/menu-feedback/${encodeURIComponent(matterId)}`);
    if (epoch !== state.identityEpoch || selection !== state.selection || state.current?.id !== matterId) return;
    state.ownFeedback.set(matterId,record);state.ownFeedbackErrors.delete(matterId);
    const draft = state.ownFeedbackDrafts.get(matterId);
    if (!draft?.dirty) state.ownFeedbackDrafts.set(matterId,{feedback_text:record.feedback_text,dish:record.dish || '',dietary_constraint:record.dietary_constraint || '',baseVersion:record.expected_version,dirty:false});
    else if (adoptVersion) draft.baseVersion = record.expected_version;
    if (background && (waitingUI.keepDom() || sourceUI.keepDom() || extractionUI.keepDom() || tabularUI.keepDom() || modelPreviewUI.keepDom() || (sourceImpactUI.keepDom() || attachmentsUI.keepDom()))) {waitingUI.noteVersion();sourceUI.noteVersion();extractionUI.noteVersion();tabularUI.noteVersion();modelPreviewUI.noteVersion();sourceImpactUI.noteVersion();attachmentsUI.noteVersion();}else render();
  } catch (error) {
    if (epoch !== state.identityEpoch || selection !== state.selection || state.current?.id !== matterId) return;
    if (error.status === 404) {state.ownFeedback.set(matterId,null);state.ownFeedbackErrors.delete(matterId);}
    else state.ownFeedbackErrors.set(matterId,error.message || '已提交意见暂不可读取');
    if (background && (waitingUI.keepDom() || sourceUI.keepDom() || extractionUI.keepDom() || tabularUI.keepDom() || modelPreviewUI.keepDom() || (sourceImpactUI.keepDom() || attachmentsUI.keepDom()))) {waitingUI.noteVersion();sourceUI.noteVersion();extractionUI.noteVersion();tabularUI.noteVersion();modelPreviewUI.noteVersion();sourceImpactUI.noteVersion();attachmentsUI.noteVersion();}else render();
  }
}
async function syncFeedbackMatter(id,selection) {
  const epoch = state.identityEpoch;
  try {const matter = await request(`/api/matters/${encodeURIComponent(id)}`);if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === id) {state.current = matter;render();}}
  catch (error) {if (epoch === state.identityEpoch && selection === state.selection) {state.error = {message:'意见操作已保存，私人事项暂未读回最新版本；请读取最新版本后继续编辑。'};render();}}
}
async function loadServiceAccounts(capability) {
  serviceState();const epoch = state.identityEpoch;
  try {const result = await request(`/api/service-accounts?capability=${capability}`);if (epoch === state.identityEpoch) {state.serviceAccounts[capability] = result.items || [];render();}}
  catch (error) {if (epoch === state.identityEpoch) {state.error = {message:error.message || '可选服务账号暂不可读取。'};render();}}
}
async function refreshServices() {
  serviceState();const epoch = state.identityEpoch;
  try {const result = await request('/api/services');if (epoch === state.identityEpoch) {state.services = result.items || [];if (state.serviceCurrent && !state.services.some((item) => item.id === state.serviceCurrent.id)) {state.lostServiceId = state.serviceCurrent.id;state.serviceCurrent = null;}render();}}
  catch (error) {if (epoch === state.identityEpoch) {state.error = {message:error.message || '本人服务暂不可读取。'};render();}}
}
async function loadService(id,{reload=false} = {}) {
  serviceState();const epoch = state.identityEpoch, selection = ++state.selection, previous = state.serviceCurrent;
  if (!reload) {state.serviceCurrent = null;state.error = null;state.difference = null;}state.lostServiceId = null;render();
  try {const result = await request(`/api/services/${encodeURIComponent(id)}`);if (epoch !== state.identityEpoch || selection !== state.selection) return;state.serviceCurrent = result.service || result;
    if (reload && state.error) state.difference = `已读取服务最新版本 ${state.serviceCurrent.version}，分派轮次 ${state.serviceCurrent.assignment_epoch}。你的未保存输入仍保留；请核对当前范围与状态再提交。${JSON.stringify(previous?.projection) !== JSON.stringify(state.serviceCurrent.projection) ? '\n必要服务范围已变化，请按上方最新投影逐项核对。' : ''}`;
    render();
  } catch (error) {if (epoch === state.identityEpoch && selection === state.selection) {if ([403,404].includes(error.status)) {state.serviceCurrent = null;state.lostServiceId = id;state.services = state.services.filter((item) => item.id !== id);}state.error = {message:error.message || '服务暂不可读取。',status:error.status};render();}}
}
function writeService(suffix,body,clearFields = []) {
  const service = state.serviceCurrent;if (!service) return;
  const draft = serviceDraft(service), selection = state.selection;
  auxiliaryWrite(`/api/services/${encodeURIComponent(service.id)}/${suffix}`,{expected_version:service.version,...body},async (result) => {
    for (const key of clearFields) draft[key] = '';
    if (selection === state.selection) state.serviceCurrent = result.service || result;
    toast('已保存当前账号软件动作或声明，其他独立业务状态按真实记录保留。');await refreshServices();
  });
}
function serviceFollowupMarkup(service,disabled) {
  if (!hasCapability('clerk') || service.clerk_id !== state.session?.principal?.id || service.sharing_status !== 'shared') return '';
  const draft = state.serviceFollowupDrafts?.get(service.id);
  return `<section class="panel"><div class="panel-head"><h2>经办私有跟进稿</h2><button class="button" data-action="read-service-draft" ${disabled}>读取跟进稿与当前差异</button></div><p class="scope-note">只为当前经办准备可用文字，未发送。发起人及执行者看不到此稿；保存不改变处理、结果、验收或关闭。</p>${draft ? `<form id="service-followup-form"><label class="field-label" for="service-followup-content">可编辑跟进正文</label><textarea id="service-followup-content" name="content" class="artifact-editor" maxlength="12000" required ${disabled}>${escapeHtml(draft.content)}</textarea><p class="small ${draft.dirty ? 'dirty-note' : 'muted'}">${draft.version ? `跟进稿已保存 v${escapeHtml(draft.version)}` : '当前为本地记录整理预览，尚未保存'} · 基于服务版本 ${escapeHtml(draft.basis_service_version)}${draft.dirty ? ' · 有未保存编辑' : ''}</p>${draft.remote ? `<p class="small ${draft.remote.stale || draft.remote.version !== draft.version || draft.remote.current_service_version !== draft.basis_service_version ? 'dirty-note' : 'muted'}">当前已读回：跟进稿 v${escapeHtml(draft.remote.version)}，服务版本 ${escapeHtml(draft.remote.current_service_version)}。${draft.remote.stale ? '原稿或服务依据已变化，需核对；原正文保留。' : ''}</p>` : ''}<div class="artifact-toolbar"><button class="button" type="submit" ${disabled}>保存本人跟进稿</button><button class="button quiet" type="button" data-action="copy-service-draft" ${disabled}>复制当前正文</button><button class="button quiet" type="button" data-action="adopt-service-draft-base" ${disabled || !draft.remote ? 'disabled' : ''}>核对后采用当前基版（正文保留）</button></div><p class="scope-note">服务状态变化后先读取实际记录，再核对采用当前基版。不会自动覆盖人工正文或把声明当验收。</p></form>${draft.remote?.history?.length ? `<details ${draft.historyPreview ? 'open' : ''}><summary class="details-toggle">已保存记录（最近20版；历史保留）</summary><ul>${draft.remote.history.map(item=>`<li>跟进稿 v${escapeHtml(item.version)} · 服务版本 ${escapeHtml(item.basis_service_version)} · ${escapeHtml(formatTime(item.saved_at))} <button class="button quiet" type="button" data-action="read-service-draft-history" data-version="${escapeHtml(item.version)}" ${disabled}>查看此版</button></li>`).join('')}</ul>${draft.historyPreview ? `<label class="field-label" for="service-followup-history">已保存 v${escapeHtml(draft.historyPreview.version)}（只读，当前编辑保留）</label><textarea id="service-followup-history" readonly>${escapeHtml(draft.historyPreview.content)}</textarea>` : ''}</details>` : ''}` : '<p class="small muted">点击读取，按当前服务的必要信息、独立状态和已获授权声明准备文字；不运行模型。</p>'}</section>`;
}
async function readServiceFollowup() {
  const service = state.serviceCurrent;if (!service || !hasCapability('clerk') || service.clerk_id !== state.session?.principal?.id) return;
  const epoch = state.identityEpoch, selection = state.selection;
  try {
    const result = await request(`/api/services/${encodeURIComponent(service.id)}/draft`);
    if (epoch !== state.identityEpoch || selection !== state.selection || state.serviceCurrent?.id !== service.id) return;
    state.serviceFollowupDrafts ||= new Map();let draft = state.serviceFollowupDrafts.get(service.id);
    if (!draft) {draft = {...result,dirty:false};state.serviceFollowupDrafts.set(service.id,draft);}
    // A read never replaces existing unsaved text or silently adopts a new basis.
    draft.remote = result;render();
  } catch (error) {if (epoch === state.identityEpoch && selection === state.selection) {state.error = {message:error.message || '跟进稿暂不可读；本人编辑保留。',status:error.status};render();}}
}
function saveServiceFollowup() {
  const service = state.serviceCurrent,draft = state.serviceFollowupDrafts?.get(service?.id);if (!service || !draft) return;
  const selection = state.selection;
  auxiliaryWrite(`/api/services/${encodeURIComponent(service.id)}/draft`,{expected_version:draft.basis_service_version,base_version:draft.version,content:draft.content},(result)=>{
    if (selection !== state.selection || state.serviceCurrent?.id !== service.id) return;
    state.serviceFollowupDrafts.set(service.id,{...result,remote:result,dirty:false});toast('本人跟进稿已保存，服务状态保持，未发送。');
  });
}
async function refreshList() {
  if (!authenticated()) return;
  const epoch = state.identityEpoch;
  try {const data = await request('/api/worklist');if (epoch === state.identityEpoch) {state.items = data.items || [];renderList();}}
  catch (error) {if (epoch === state.identityEpoch) $('#worklist').innerHTML = `<p class="empty-note">${escapeHtml(error.message || '事项列表暂不可读取')}</p>`;}
}
function renderList() {
  const items = state.items.filter((item) => domainMatches(item.domain,state.domainFilter) && item.goal_text.toLocaleLowerCase().includes(state.search.toLocaleLowerCase()));
  $('#list-count').textContent = state.items.length || '';
  $('#worklist').innerHTML = items.length ? items.map((item) => `<button class="matter-item ${state.current?.id === item.id ? 'selected' : ''}" data-open="${escapeHtml(item.id)}" ${state.current?.id === item.id ? 'aria-current="page"' : ''}><span class="matter-title">${escapeHtml(item.goal_text)}</span><span class="matter-meta">${escapeHtml(domainLabel(item.domain))} · ${escapeHtml(statusLabels[item.assistant_status] || '待核')}<br>${escapeHtml(formatTime(item.updated_at))}${item.reminders?.length && !remindersUI.quietActive() ? `<br><span class="dirty-note">本人本地提醒已到 · ${item.reminders.length} 点</span>` : ''}</span></button>`).join('') : `<p class="empty-note">${state.search || state.domainFilter ? '没有匹配的事项' : '从一个目标开始，事项会保留在这里。'}</p>`;
}
function activityMarkup(activity) {
  const type = activity.event_type;
  if (!['material_record','attachment_text_saved','goal_revised','menu_revised_local','feedback_corrected_local','meeting_action_progress','journey_movement','journey_plan_saved','journey_meals_saved','journey_materials_saved','journey_link_saved','preparation_shared','preparation_share_withdrawn','feedback_cook_response','feedback_cook_adoption','waiting_changed','source_state_changed','source_candidates_applied'].includes(type)) return escapeHtml(activity.message);
  let record;try {record = JSON.parse(activity.message);} catch {return escapeHtml(activity.message);}
  if (!record || typeof record !== 'object' || Array.isArray(record)) return escapeHtml(activity.message);
  const actor = record.changed_by || record.actor_id || activity.recorded_by, name = record.changed_by_name || record.actor_name || record.actor_display_name || activity.actor_display_name || (actor === state.session?.principal?.id ? state.session.principal.display_name : '实际记录账号');
  let message;
  if (type === 'material_record') message = `${materialLabels[record.material_kind] || '材料'} · ${{submission_statement:'本人登记提交声明',receipt_reference:'回执原处已提供',receipt_source_checked:'本人已核所给回执原文',needs_review:'材料或手续待重核'}[record.record_type] || '实际记录'}：${record.note || '原述待核'}；实际日期 ${record.occurred_at || '未知'}，未提交申请、签发或更新外部内容。`;
  else if (type === 'source_candidates_applied') message = `本人明确选入 ${(record.fields || []).length} 个本地明示标签原文候选，实际出处保留；未确认真实、未解释叙述或相对日期，原本人确认值不覆盖。`;
  else if (type === 'source_state_changed') message = `本人明确修改来源准备引用范围：${sourceStateLabels[record.state] || '当前状态待核'}。实际说明：${record.note || '待核'}${record.state === 'withheld' ? `；已撤回本事项有效文字分享 ${record.revoked_share_count ?? '待核'} 份，本事项全部新准备及模型输入暂缓` : ''}。原文／人工稿历史保留，不代表制度效力或法律撤权，已取出副本无法召回。`;
  else if (type === 'waiting_changed') message = `本人明确维护本地等待点：新增／修改 ${record.upserts?.length || 0} 点，明确移除 ${record.removed_ids?.length || 0} 点。原述历史保留；不代表真实受理、业务办结或消息送达。`;
  else if (type === 'attachment_text_saved') message = `本人核对并转存原附件正文片段：${record.filename || '附件'}；${record.human_edited ? '含人工修改，原件位置需重新核对' : '按正文片段保序'}，${record.truncated ? '已截断，未读取全文' : '未读取部分仍需核对'}；未确认事实，原件与人工稿保留。`;
  else if (type === 'goal_revised') message = `本人修正委托目标；输入修订 ${record.input_revision ?? '待核'}，仅所选事实待重核，旧目标原文、人工稿与真实操作历史保留。`;
  else if (type === 'feedback_cook_adoption') message = `厨师本人采用声明：${record.note}；对应本地菜单版本 ${record.target_publication_version}、原意见更正编号 ${record.feedback_correction_count}；不表示实际备餐、供餐或厨房安全，声明历史另查。`;
  else if (type === 'menu_revised_local') message = `厨师本人修订本地菜单：版本 ${record.previous_version ?? '待核'} → ${record.version ?? '待核'}；旧菜单和旧意见原关联保留，采用声明另记，实际供餐待核。`;
  else if (type === 'feedback_corrected_local') message = `本人已明确更正菜单意见；${record.shared === true ? '更正给原菜单厨师的共享投影' : '只保存本人私有记录，未重新公开'}。所看菜单版本 ${record.publication_version ?? '待核'}；原文与来源历史保留。`;
  else if (type === 'feedback_cook_response') message = `厨师本人回应：${record.response_text || '回应文字未记录'}；针对菜单版本 ${record.publication_version ?? '待核'}、意见更正编号 ${record.feedback_correction_count ?? '待核'}。这是文字声明，不代表采纳、实际供餐或效果，旧意见范围需查看回应历史。`;
  else if (type === 'preparation_shared') message = '本人在本工作区明确分享一份独立固定准备文字给选定接收账号；不授权原私人事项、事实或来源历史，未外部送达。';
  else if (type === 'preparation_share_withdrawn') message = '原分享者明确撤回本工作区文字共享；旧读取／下载失效，历史保留，已取出副本无法召回。';
  else if (type.startsWith('journey_') && type.endsWith('_saved')) message = `${({journey_plan_saved:'本人保存计划与离返营口径',journey_meals_saved:'本人保存人工餐次需求',journey_materials_saved:'本人保存必要材料来源清单',journey_link_saved:'本人明确变更固定旅程引用'})[type]}；事实修订 ${record.input_revision ?? '待核'}${record.meal_recheck_required ? '，计划范围已变，原餐次需求须重核' : ''}。正式批准、执行与外部回执仍待核。`;
  else if (type === 'journey_movement') message = `${({departed:'实际已离营声明',returned:'实际已返营声明',not_travelled:'实际未出行声明'})[record.kind] || '实际出返声明'}：${record.note || '说明未记录'}；${record.evidence_class === 'self_statement' ? '当前本人声明' : '实际代录，待核'}${record.reported_by ? `，原陈述人：${record.reported_by}` : ''}；发生时间：${record.occurred_at || '未知，未用计划代填'}${record.supersedes_event_id ? '；已明确更正历史声明，原述保留' : ''}。安全批准与现场核验独立待核。`;
  else {const command = ({self_accept:'本人接受',self_start:'本人声明开始',self_blocked:'本人报告障碍',self_claimed_done:'本人声明已完成',reported_progress:'代录实际进度',result_note:'本人核对说明'})[record.command];if (!command) return escapeHtml(activity.message);message = `${command}：${record.note || '实际说明未记录'}。行动条目修订 ${record.item_revision ?? '待核'}${record.reported_by ? `；原陈述人：${record.reported_by}` : ''}${record.occurred_at ? `；声明发生时间：${record.occurred_at}` : ''}。声明与实际验收独立。`;}
  return `${escapeHtml(message)}<div class="small muted activity-actor" title="${escapeHtml(actor || '未记录')}">实际记录者：${escapeHtml(name)}${actor ? `<details><summary>查看实际账号记录</summary><code>${escapeHtml(actor)}</code></details>` : ''}</div>${type === 'source_state_changed' ? `<details class="small muted"><summary>查看完整实际来源控制记录</summary><pre class="activity-record-json">${escapeHtml(JSON.stringify(record,null,2))}</pre></details>` : ''}`;
}
function errorMarkup() {
  if (!state.error) return '';
  const error = state.error;
  const conflict = error.status === 409 || /conflict|stale/i.test(error.code || '');
  return `<div class="inline-error" role="alert"><p>${escapeHtml(error.message || '操作未完成，输入已保留。')}</p>${conflict ? `<p>你的编辑仍保留。先读取最新版本并核对差异，再自行保存。</p><button class="button" data-action="${state.view === 'context' ? 'context-reload' : state.view === 'services' ? 'reload-service' : state.view === 'menus' ? state.feedbackOpen ? 'reload-menu-feedback' : 'refresh-menus' : 'reload-conflict'}">读取最新版本与差异</button>` : error.retryable && error.retry ? '<button class="button" data-action="retry">重试本次操作</button>' : ''} <button class="button quiet" data-action="dismiss-error">收起提示</button>${state.difference ? `<div class="difference">${escapeHtml(state.difference)}</div>` : ''}</div>`;
}
function render() {
  const oldLog = $('.conversation-log'), oldScroll = oldLog?.scrollTop ?? 0, stickToEnd = !oldLog || oldLog.scrollHeight-oldLog.scrollTop-oldLog.clientHeight < 40;
  updateChrome();renderSourceSearch();
  const focused = document.activeElement;
  const focusSelector = focused?.id ? `#${CSS.escape(focused.id)}` : focused?.name && focused.form?.id ? `#${CSS.escape(focused.form.id)} [name="${CSS.escape(focused.name)}"]` : null;
  const selectionStart = focused?.selectionStart, selectionEnd = focused?.selectionEnd;
  function restoreEditingFocus() {
    if (!focusSelector) return;
    const input = document.querySelector(focusSelector);
    if (!input || input.disabled) return;
    input.focus({preventScroll:true});
    if (typeof selectionStart === 'number' && input.setSelectionRange) {try {input.setSelectionRange(selectionStart,selectionEnd);} catch { /* Non-text inputs do not expose a selection. */ }}
  }
  renderList();
  const workspace = $('#workspace');
  if (state.session?.authenticated === false) {workspace.innerHTML = authMarkup();return;}
  if (state.loading) {workspace.innerHTML = '<div class="loading-state" role="status"><span class="loading-dot"></span>正在读取工作区…</div>';return;}
  if (state.view === 'context') {workspace.innerHTML = `${errorMarkup()}${contextUI.markup()}`;restoreEditingFocus();return;}
  if (state.view === 'accounts') {workspace.innerHTML = accountMarkup();restoreEditingFocus();return;}
  if (state.view === 'menus') {workspace.innerHTML = menusMarkup();restoreEditingFocus();return;}
  if (state.view === 'services') {workspace.innerHTML = servicesMarkup();restoreEditingFocus();return;}
  if (state.view === 'shares') {sharingState();workspace.innerHTML = receivedSharingMarkup({items:state.receivedShares,current:state.receivedShare,busy:state.busy,error:state.receivedShareError});restoreEditingFocus();return;}
  if (authenticated() && !canCreateMatter() && !state.current) {workspace.innerHTML = '<section class="panel"><h1>本地授权工作区</h1><p class="small muted">当前账号未授予私人准备事项操作；请使用已授权入口。执行者从“待办服务”查看本人当前获分派的必要信息。</p></section>';return;}
  if (!state.current) {
    const c = state.composer,initialMode=c.initial_mode??(state.session?.model_configured?'model':'template');
    workspace.innerHTML = `<section class="intro"><div class="eyebrow">一个目标，持续办下去</div><h1>有什么需要我帮你办？</h1><p>直接说目标，我会整理准备成果和需要核对的问题。</p>${errorMarkup()}<form id="create-form" class="goal-composer"><label class="field-label" for="goal">想办的事情</label><textarea id="goal" name="goal_text" required maxlength="12000" placeholder="例如：整理这次出行的用车需求，列出还需要核对的信息。" ${state.busy ? 'disabled' : ''}>${escapeHtml(c.goal_text)}</textarea><div class="composer-toolbar"><select name="initial_mode" aria-label="首次整理方式" ${state.busy ? 'disabled' : ''}><option value="model" ${initialMode==='model'?'selected':''} ${state.session?.model_configured?'':'disabled'}>交融理解并整理${state.session?.model_configured?'':'（待配置）'}</option><option value="template" ${initialMode==='template'?'selected':''}>本地模板／人工准备</option></select><button class="button primary" type="submit" ${state.busy||!state.session?'disabled':''}>${state.busy?'正在保存…':'交给助理'}</button></div><p class="scope-note">${initialMode==='model'?'本次将目标及原话发送交融，理解办理类别并准备可编辑成果；之后每次模型整理仍由你明确发起。':'先保存在本地，类别未知时沿综合事项准备；也可自己选择类别。'}</p><details id="create-options" ${c.optionsOpen?'open':''}><summary class="details-toggle">类别与原话来源（可选）</summary><label class="field-label">办理类别<select name="domain" aria-label="办理类别（默认助理判断）" ${state.busy?'disabled':''}><option value="auto" ${c.domain==='auto'?'selected':''}>助理根据目标判断</option>${domainOptions(c.domain)}</select></label><label class="field-label">原话来源<select name="source_kind" aria-label="原话来源" ${state.busy?'disabled':''}>${sourceOptions(c.source_kind)}</select></label><input name="reported_by" aria-label="原陈述人" placeholder="陈述人（口头摘录请注明）" value="${escapeHtml(c.reported_by)}" ${c.source_kind==='oral_note'?'required':''} ${state.busy?'disabled':''}>${initialMode==='template'?`<label class="auto-prepare-label"><input id="auto-prepare" name="auto_prepare" type="checkbox" ${c.auto_prepare?'checked':''} ${state.busy?'disabled':''}>补充后继续本地整理，不自动调用模型</label>`:''}</details></form><details><summary class="details-toggle">按业务类别开始（可选）</summary><div class="matter-categories" aria-label="按业务分类的事项入口">${categorySuggestions()}</div></details><p class="scope-note">本地经办工作区 · ${escapeHtml(modelStatus())}。<br>${escapeHtml(modelScope())}<br>不读取微信群、不同步在线表；准备稿不代表真实批准或执行。</p></section>`;
    restoreEditingFocus();
    return;
  }
  const m = state.current, draft = draftFor(), artifact = editingArtifact(m), latest = latestArtifact(m), fieldLabels = labelsFor(m);
  const disabled = state.busy ? 'disabled' : '';
  const isProcessing = m.assistant_status === 'processing';
  const isHeld = ['paused','handoff'].includes(m.assistant_status);
  const editorValue = draft.artifactText ?? artifact?.content ?? '';
  const dirty = draft.artifactText !== null && draft.artifactText !== (artifact?.content ?? '');
  const tracks = Object.keys(m.tracks || {}).length ? `<section class="tracks" aria-label="独立业务状态">${Object.entries(m.tracks).map(([key,track]) => `<div class="track"><div class="track-name">${escapeHtml(state.catalog[m.domain]?.tracks?.[key] || key)}</div><div class="track-status">${escapeHtml(trackStatusLabels[track.status] || track.status || '待核 / 未登记')}</div></div>`).join('')}</section>` : '';
  const facts = Object.entries(fieldLabels).map(([key,label]) => {const fact = m.facts?.[key];return `<div class="field"><label for="fact-${key}">${label} <span class="muted">${fact?.status === 'confirmed' ? '· 已确认' : fact?.status === 'candidate' ? fact?.model ? '· 模型候选，待核' : '· 候选，待核' : '· 待补 / 待核'}</span></label><input id="fact-${key}" name="${key}" value="${escapeHtml(Object.hasOwn(draft.factChanges,key) ? draft.factChanges[key] ?? '' : fact?.value ?? '')}" placeholder="${['departure_at','return_at'].includes(key) ? 'YYYY-MM-DD HH:mm（本地预览口径）' : '按已知信息填写，未知可留空'}" ${disabled}></div>`;}).join('');
  const sourceList = [...(m.sources || [])].reverse().map((source) => `<div class="source-block ${state.sourceHit?.source_id === source.id ? 'source-hit-selected' : ''}" id="source-${escapeHtml(source.id)}"><small>${escapeHtml(sourceLabels[source.kind] || source.kind)} · ${escapeHtml(source.reported_by || '陈述人待核')} · ${escapeHtml(formatTime(source.recorded_at))}</small><p>${escapeHtml(source.text)}</p>${sourceFileDeclarationMarkup(source)}${source.attachment_text ? `<p class="scope-note">原附件正文关联：${escapeHtml(source.attachment_text.filename)} · 附件ID ${escapeHtml(source.attachment_text.attachment_id)} · SHA-256 ${escapeHtml(source.attachment_text.sha256)}<br>${escapeHtml(source.attachment_text.scope)} ${source.attachment_text.truncated ? '原件提取已截断，未读取全文。' : ''} ${(source.attachment_text.unread || []).map(escapeHtml).join('；')}</p>` : ''}${sourceUI.markup(m,source)}</div>`).join('');
  workspace.innerHTML = `${errorMarkup()}${sourceHitMarkup(m)}${sourceUI.modelBlocked(m) ? '<div class="draft-preserved" role="status">本事项有暂不用来源：全部新准备及模型输入暂缓。原文、历史人工稿可读可改，来源修正与真实声明仍可继续；已取出副本无法召回。</div>' : ''}<header class="matter-header"><div><div class="eyebrow">${escapeHtml(domainLabel(m.domain))} / 持续工作区</div><h1>${escapeHtml(m.goal_text)}</h1><span class="status-pill ${escapeHtml(m.assistant_status)}">${escapeHtml(statusLabels[m.assistant_status] || '待核')}</span> <span class="artifact-meta">事实修订 ${escapeHtml(m.revision_no)} · 业务结果待核</span></div><div class="header-actions"><button class="button quiet" data-action="edit-goal" ${disabled}>${draft.goalEditorOpen ? '收起目标修正' : '修正目标'}</button>${isHeld ? `<button class="button" data-action="resume" ${disabled}>继续准备</button>` : `<button class="button" data-action="pause" ${disabled}>暂停助理</button>`}<button class="button quiet" data-action="handoff" ${disabled || (m.assistant_status === 'handoff' ? 'disabled' : '')}>人工接手</button><button class="button quiet" data-action="toggle-activity" aria-expanded="${state.activityOpen}">${state.activityOpen ? '收起' : '展开'}活动</button></div></header>${goalRevisionMarkup(m,disabled)}${conversationMarkup(m,{draft,busy:state.busy,configured:state.session?.model_configured,time:formatTime,scope:modelScope(),category:categoryMarkup(m)})}${continuationMarkup(m)}${relatedTasksUI.markup(m)}<details id="matter-tools" ${draft.toolsOpen ? 'open' : ''}><summary class="details-toggle matter-tools-summary">事实、成果编辑与办理操作${dirty ? ' · 有未保存正文' : ''}</summary>${tracks}<div class="workspace-grid ${state.activityOpen ? '' : 'activity-closed'}"><div class="content-column"><section class="panel"><div class="panel-head"><h2>目标与已有事实</h2><button class="button quiet" data-action="toggle-facts" aria-expanded="${draft.factsOpen}">${draft.factsOpen ? '收起事实' : '核对事实'}</button></div><p class="original">${escapeHtml(m.goal_text)}</p>${draft.factsOpen ? `<form id="facts-form"><div class="facts-grid">${facts}</div><p class="small muted">只确认你实际知道的内容。此操作只核对事实，不构成真实批准、发布、支付或执行。</p><button class="button" type="submit" ${disabled}>保存已核事实</button></form>` : `<div class="facts-summary">${Object.entries(fieldLabels).map(([key,label]) => `<span>${label}：${escapeHtml(m.facts?.[key]?.value || '待补 / 待核')}${m.facts?.[key]?.status === 'candidate' ? '（候选，待核）' : ''}</span>`).join('')}</div>`}${legacyMarkup(m)}${extractionUI.markup(m)}</section>${structuredMarkupFor(m)}${journeyMarkupFor(m)}${checksMarkupFor(m)}${readingsUI.markup(m)}<section class="panel"><div class="panel-head"><div><div class="section-kicker">可编辑 · 可复制 · 可续办</div><h2>${escapeHtml(artifact?.title || '准备成果')}</h2></div><div class="prepare-controls"><label class="sr-only" for="prepare-mode">整理方式</label><select id="prepare-mode" ${disabled || (isProcessing || isHeld ? 'disabled' : '')}><option value="template" ${draft.prepareMode === 'template' ? 'selected' : ''}>本地模板</option><option value="model" ${draft.prepareMode === 'model' ? 'selected' : ''} ${state.session?.model_configured && !sourceUI.modelBlocked(m) ? '' : 'disabled'}>模型整理${sourceUI.modelBlocked(m) ? '（有暂不用来源）' : state.session?.model_configured ? '' : '（未配置）'}</option></select><button class="button primary" data-action="prepare" ${disabled || (isProcessing || isHeld ? 'disabled' : '')}>${isProcessing ? '正在整理…' : artifact ? '重新整理准备稿' : '整理准备稿'}</button><button class="button quiet" data-action="read-preparation-result" ${disabled}>读取已保存结果（编辑保留）</button></div></div><p class="small muted">${isHeld ? '助理已暂停未来动作，你仍可编辑成果、复制或人工继续。' : modelScope()}</p>${workflowMarkup(m,disabled)}${privateContextUI.markup(m)}${modelPreviewUI.markup(m)}${latest && artifact?.id !== latest.id ? `<div class="draft-preserved" role="status">已生成新准备稿，当前仍保留原稿${draft.artifactText !== null ? '和未保存编辑' : ''}。<button class="button" data-action="use-latest-artifact" ${disabled}>查看最新稿</button></div>` : ''}${(m.actions || []).slice(-1).filter((action) => action.status === 'failed' && action.error).map((action) => `<p class="inline-error">${escapeHtml(typeof action.error === 'string' ? action.error : action.error.message || '本次整理未完成，可重试模板或人工继续。')}</p>`).join('')}${artifact ? `${artifact.stale || artifact.input_revision !== m.revision_no ? '<p class="small dirty-note">事实 / 来源已变化，需重核；人工修改保留。</p>' : ''}<div class="artifact-picker"><label class="field-label" for="artifact-select">稿件（保存和生成记录分别保留）</label><select id="artifact-select" ${disabled}>${(m.artifacts || []).map((item,index) => `<option value="${escapeHtml(item.id)}" ${item.id === artifact.id ? 'selected' : ''}>${escapeHtml(item.title || '准备稿')} · ${item.status === 'human_saved' ? '人工保存' : item.status === 'model_draft' ? '模型候选' : '准备稿'} · 版本 ${escapeHtml(item.version)}${index === 0 ? ' · 最近保存' : ''}</option>`).join('')}</select></div><label class="field-label" for="artifact-editor">准备稿正文</label><textarea id="artifact-editor" class="artifact-editor" ${disabled}>${escapeHtml(editorValue)}</textarea><div class="artifact-toolbar"><button class="button" data-action="save-artifact" ${disabled}>保存编辑</button><button class="button quiet" data-action="copy-artifact" ${disabled}>复制</button><button class="button quiet" data-action="download-artifact" ${disabled}>下载已保存 v${escapeHtml(artifact.version)} TXT</button><button class="button quiet" data-action="download-artifact-md" ${disabled}>下载已保存 v${escapeHtml(artifact.version)} MD</button><button class="button quiet" data-action="download-artifact-docx" ${disabled} title="通用可编辑文字文档，非正式模板、印章或签发">下载已保存 v${escapeHtml(artifact.version)} DOCX</button><span class="saved-label ${dirty ? 'dirty-note' : ''}" id="saved-label">${dirty ? '有未保存编辑' : `已保存 · 版本 ${escapeHtml(artifact.version)}`}</span></div>` : `<div class="artifact-empty"><p>可以整理本地模板，也可以直接写人工稿。助理暂停／人工接手时，仍可保存自己的文字。</p><button class="button" data-action="start-manual-artifact" ${disabled}>直接写人工稿</button><p class="small muted">先建立含目标的空白稿，再编辑保存；不调用模型，不生成事实或业务结果。</p></div>`}</section>${languageDraftMarkup(m,artifact,dirty)}${ownSharingMarkupFor(m,artifact)}${ownFeedbackMarkup(m,disabled)}${serviceSubmitMarkup(m,disabled)}${menuPublishMarkup(m,draft,disabled)}${evidenceMarkup(m,draft,disabled)}${rowsMarkup(m,draft,disabled)}${attachmentsUI.markup(m)}<section class="panel"><details id="sources-details" ${draft.sourcesOpen ? 'open' : ''}><summary class="details-toggle">来源与补充信息 <span class="muted">${m.sources?.length || 0} 条</span></summary>${sourceImpactUI.markup(m)}${sourceList}<form id="source-form" class="source-form"><label class="field-label" for="source-file">从本地UTF-8 TXT／MD／CSV／TSV加载原文（最多12000字／48003字节，仅加载待保存）</label><input id="source-file" type="file" accept=".txt,.md,.csv,.tsv,text/plain,text/markdown,text/csv,text/tab-separated-values" ${disabled}>${draft.sourceFileName ? `<p id="source-file-pending" class="small muted">本人待保存文件声明：${escapeHtml(draft.sourceFileName)}${draft.sourceFileFormat ? ` · ${escapeHtml(draft.sourceFileFormat.toUpperCase())}` : ''}${Number.isInteger(draft.sourceFileBytes) ? ` · 原文件 ${draft.sourceFileBytes} 字节` : ''}。必要来源说明请手工补充；文件名不充当陈述人，不保存原始附件。${['csv','tsv'].includes(draft.sourceFileFormat) ? '表格文字可按实际情况自行选择“授权 Excel 摘录”来源，不代表 Excel 同步；表头与原值须核对。' : ''}</p>` : ''}<label class="field-label" for="source-kind">补充来源</label><select id="source-kind" name="kind" ${disabled}>${sourceOptions(draft.sourceKind)}</select><input name="reported_by" aria-label="补充来源陈述人" placeholder="陈述人（口头摘录请注明）" value="${escapeHtml(draft.reportedBy)}" ${draft.sourceKind === 'oral_note' ? 'required' : ''} ${disabled}><textarea name="text" aria-label="补充原话" required maxlength="12000" placeholder="粘贴获授权原话，或记录实际听到的内容…" ${disabled}>${escapeHtml(draft.sourceText)}</textarea><button class="button" type="submit" ${disabled}>保存补充来源</button><p class="scope-note">只保存你提供的文字，未连接微信或在线 Excel。</p></form></details></section></div>${state.activityOpen ? `<aside class="side-column" aria-label="等待与活动"><section class="panel waiting-panel"><h2>正在等待</h2><ul class="waiting-list">${(m.waiting || []).filter((item) => !(m.waiting_points || []).some((point) => point.id === item.id)).map((item) => `<li><strong>${escapeHtml(item.who_name || item.who || '责任对象待核')}</strong>${escapeHtml(item.reason)}</li>`).join('') || '<li><strong>待整理</strong>等待依据以已保存信息为准。</li>'}</ul></section><section class="panel"><h2>下一步</h2><ol class="next-list">${(m.next_steps || []).map((step) => `<li>${escapeHtml(step)}</li>`).join('') || '<li>核对已有信息，整理准备稿。</li>'}</ol></section><section class="panel activity-panel"><div class="panel-head"><h2>活动</h2><span class="artifact-meta">实际记录</span></div><ol class="activity-list">${[...(m.activities || [])].reverse().map((activity) => `<li>${activityMarkup(activity)}<time>${escapeHtml(formatTime(activity.recorded_at))}</time></li>`).join('') || '<li>暂无活动记录</li>'}</ol></section></aside>` : ''}</div></details>`;
  if (sourceUI.modelBlocked(m)) for (const button of workspace.querySelectorAll('[data-action="prepare"],[data-action="structure-render"],[data-action="journey-render"],[data-action="checks-render"],[data-action="readings-render"]')) {button.disabled = true;button.title = '本事项新准备及模型输入暂缓；人工稿、来源修正和真实声明仍可继续。';}
  modelPreviewUI.noteVersion();tabularUI.noteVersion();sourceImpactUI.noteVersion();attachmentsUI.noteVersion();
  const log=$('.conversation-log');if(log)log.scrollTop=stickToEnd?log.scrollHeight:oldScroll;
  restoreEditingFocus();
}
function continuationMarkup(m) {
  const draft=draftFor(m);
  return `<section class="panel continuation-panel" aria-label="持续办理与等待">${continuationSummary(m,{busy:state.busy})}<details id="continuation-details" ${draft.continuationOpen?'open':''}><summary class="details-toggle">本人等待记录与可编辑跟进</summary>${waitingUI.markup(m)}</details></section>`;
}
async function saveQuestionWaiting(index) {
  const m=state.current,q=m?.conversation_questions?.[index];if(!m||state.busy||typeof q!=='string')return;
  if((m.waiting_points||[]).some(p=>p.active!==false&&p.reason===q)){draftFor().continuationOpen=true;render();toast('这个问题已有等待记录，可核对原记录继续。');return;}
  if((m.waiting_points||[]).filter(p=>p.active!==false).length>=20){toast('已有20个等待点，请先核对已有记录。');return;}
  const selection=state.selection,epoch=state.identityEpoch;
  await auxiliaryWrite(`/api/matters/${encodeURIComponent(m.id)}/waiting`,{expected_version:m.version,upserts:[{id:crypto.randomUUID(),who:'本人核对',reason:q,next_step:'向实际来源核对，取得答复后补充到本事项；不猜未知内容。',due_at:null,state:'waiting',note:'本人从当前准备问题明确留作待办，问题本身仍待核。'}],removed_ids:[]},async()=>{
    if(epoch!==state.identityEpoch||selection!==state.selection||state.current?.id!==m.id)return;
    draftFor().continuationOpen=true;toast('已留作本人待办，未分派或发送；可修改等待对象与下一步。');await syncWaitingMatter(m.id,selection);
  });
}
function syncConversation() {
  const panel=$('.conversation-panel'),m=state.current;if(!panel||!m)return;
  const related=$('.related-tasks-panel'),focused=document.activeElement,inside=panel.contains(focused)||related?.contains(focused),id=inside?focused.id:null,start=focused?.selectionStart,end=focused?.selectionEnd;
  const oldLog=panel.querySelector('.conversation-log'),scroll=oldLog?.scrollTop??0,atEnd=!oldLog||oldLog.scrollHeight-scroll-oldLog.clientHeight<40;
  panel.outerHTML=conversationMarkup(m,{draft:draftFor(),busy:state.busy,configured:state.session?.model_configured,time:formatTime,scope:modelScope(),category:categoryMarkup(m)});
  const continuation=$('.continuation-summary');if(continuation)continuation.outerHTML=continuationSummary(m,{busy:state.busy});
  const relatedMarkup=relatedTasksUI.markup(m);if(related)related.outerHTML=relatedMarkup;else if(relatedMarkup)$('.conversation-panel').insertAdjacentHTML('afterend',relatedMarkup);
  const log=$('.conversation-log');if(log)log.scrollTop=atEnd?log.scrollHeight:scroll;
  if(id){const input=document.getElementById(id);if(input&&!input.disabled){input.focus({preventScroll:true});if(typeof start==='number'&&input.setSelectionRange)input.setSelectionRange(start,end);}}
}
function applyMatter(matter,{background=false} = {}) {
  const categoryChanged=adoptCategoryDraft(matter);state.current = matter;waitingUI.ingest(matter,{background});if (state.sourceHit?.matter_id === matter.id) draftFor(matter).sourcesOpen = true;
  if(categoryChanged)render();else if (background && (sourceImpactUI.keepDom() || attachmentsUI.keepDom())) {sourceImpactUI.noteVersion();attachmentsUI.noteVersion();}else if (background && modelPreviewUI.keepDom()) modelPreviewUI.noteVersion();else if (background && tabularUI.keepDom()) tabularUI.noteVersion();else if (background && extractionUI.keepDom()) extractionUI.noteVersion();else if (background && sourceUI.keepDom()) sourceUI.noteVersion();else if (background && waitingUI.keepDom()) waitingUI.noteVersion();else if (background && readingsUI.keepDom()) readingsUI.noteVersion();else if (background && keepStructureDom()) notifyStructureBackground();else if (background && keepJourneyDom()) notifyJourneyBackground();else if (background && keepChecksDom()) notifyChecksBackground();else if (background && keepShareDom()) notifyShareBackground();else render();
  if(background)syncConversation();
  schedulePoll();if (matter.domain === 'feedback') readOwnFeedback(matter.id,{background});if (structuredDomain(matter)) readStructure(matter.id,{background});if (journeyDomain(matter)) readJourney(matter.id,{background});if (checksDomain(matter)) readChecks(matter.id,{background});if (sharingAllowed()) readOwnShares(matter.id,{background});if (matter.domain === 'utilities') readingsUI.read(matter.id,{background});if (matter.sources?.length) sourceUI.read(matter.id,{background});
}
function schedulePoll() {
  clearTimeout(pollTimer);
  if (state.current?.assistant_status === 'processing' && !state.busy) {
    const id = state.current.id, selection = state.selection;
    pollTimer = setTimeout(async () => {
      try {const matter = await request(`/api/matters/${encodeURIComponent(id)}`);if (selection === state.selection && state.current?.id === id) {applyMatter(matter,{background:true});if (matter.assistant_status !== 'processing') refreshList();}}
      catch (error) {if (selection === state.selection) {if (attachmentsUI.keepDom()) attachmentsUI.noteError(error.message || '后台暂未读回，所选原件、已读列表和保存基版保留，请明确读回核对。');else if (sourceImpactUI.keepDom()) sourceImpactUI.noteError(error.message || '后台暂未读回，来源影响已读范围保留，请明确刷新。');else if (modelPreviewUI.keepDom()) modelPreviewUI.noteError(error.message || '后台暂未读回，展开的旧模型输入预览保留，未发送。');else if (tabularUI.keepDom()) tabularUI.noteError(error.message || '后台暂未读回，原表选择和人工明细保留。');else if (extractionUI.keepDom()) extractionUI.noteError(error.message || '后台暂未读回，候选选择和原基版保留。');else if (sourceUI.keepDom()) sourceUI.noteError(error.message || '后台暂未读回，来源引用控制和原编辑保留。');else if (waitingUI.keepDom()) waitingUI.noteError(error.message || '后台暂未读回，等待点控件与基版保持原样。');else if (readingsUI.keepDom()) readingsUI.noteError(error.message || '后台暂未读回，读数编辑保持原样。');else if (keepStructureDom()) {structureState();state.structureErrors.set(id,error.message || '后台暂未读回，编辑控件保持原样。');notifyStructureBackground();}else if (keepJourneyDom()) {journeyState();state.journeyErrors.set(id,error.message || '后台暂未读回，编辑控件保持原样。');notifyJourneyBackground();}else if (keepChecksDom()) {checksState();state.checksErrors.set(id,error.message || '后台暂未读回，编辑控件保持原样。');notifyChecksBackground();}else {state.error = {...error,retry:() => loadMatter(id)};render();}}}
    },900);
  }
}
async function loadMatter(id) {
  if (!authenticated()) {render();return;}
  state.view = 'workspace';
  detailController?.abort();clearTimeout(pollTimer);
  const selection = ++state.selection;state.current = null;state.loading = true;state.error = null;state.difference = null;render();
  detailController = new AbortController();
  try {const matter = await request(`/api/matters/${encodeURIComponent(id)}`,{signal:detailController.signal});if (selection !== state.selection) return;state.loading = false;applyMatter(matter);}
  catch (error) {if (error.name === 'AbortError' || selection !== state.selection) return;state.loading = false;state.error = {...error,retry:() => loadMatter(id)};render();}
}
async function mutate(path,body,{method='POST',key=crypto.randomUUID(),onSuccess,message} = {}) {
  if (state.busy || !state.session) return;
  const selection = state.selection, identityEpoch = state.identityEpoch, origin = state.current?.id;
  // Keep the visible saved draft selected while new preparation runs.
  const visibleArtifact = state.current && editingArtifact();
  if (visibleArtifact && !draftFor().selectedArtifactId) draftFor().selectedArtifactId = visibleArtifact.id;
  state.busy = true;state.error = null;state.difference = null;clearTimeout(pollTimer);render();
  try {
    const matter = await request(path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':state.session.csrf_token,'Idempotency-Key':key},body:JSON.stringify(body)});
    if (selection === state.selection) {
      adoptCategoryDraft(matter);onSuccess?.(matter);state.current = matter;waitingUI.ingest(matter);
      if (matter.domain === 'feedback') readOwnFeedback(matter.id);if (structuredDomain(matter)) readStructure(matter.id);if (journeyDomain(matter)) readJourney(matter.id);if (checksDomain(matter)) readChecks(matter.id);if (sharingAllowed()) readOwnShares(matter.id);
      if (!origin) {++state.selection;history.replaceState(null,'',`#${encodeURIComponent(matter.id)}`);}
      if (matter.domain === 'utilities') readingsUI.read(matter.id);if (matter.sources?.length) sourceUI.read(matter.id);
      if (message) toast(message);
    }
    refreshList();
  } catch (error) {
    if (selection === state.selection) state.error = {...error,retry:() => mutate(path,body,{method,key,onSuccess,message})};
  } finally {if (identityEpoch === state.identityEpoch) {state.busy = false;render();schedulePoll();}}
}
async function reloadConflict() {
  if (!state.current || state.busy) return;
  const id = state.current.id, selection = state.selection, oldMatter = state.current;
  state.busy = true;render();
  try {
    const matter = await request(`/api/matters/${encodeURIComponent(id)}`);
    if (selection !== state.selection) return;
    const changes = Object.entries(labelsFor(matter)).filter(([key]) => JSON.stringify(matter.facts?.[key]) !== JSON.stringify(oldMatter.facts?.[key])).map(([key,label]) => `${label}：${oldMatter.facts?.[key]?.value ?? '待核'} → ${matter.facts?.[key]?.value ?? '待核'}`);
    if (matter.goal_text !== oldMatter.goal_text) changes.unshift(`服务器目标：${oldMatter.goal_text} → ${matter.goal_text}；你的目标编辑仍保留。`);
    const oldArtifact = editingArtifact(oldMatter), newArtifact = matter.artifacts?.find((artifact) => artifact.id === oldArtifact?.id) || latestArtifact(matter);
    if (oldArtifact?.content !== newArtifact?.content) changes.push(`服务器准备稿（版本 ${newArtifact?.version ?? '待核'}）：\n${newArtifact?.content || '暂无'}\n\n你的编辑仍在正文框中，尚未覆盖服务器。`);
    const draft = draftFor();
    if (JSON.stringify(oldMatter.rows || []) !== JSON.stringify(matter.rows || [])) changes.push(`服务器明细：\n${JSON.stringify(matter.rows || [],null,2)}\n表格中的本地编辑仍保留，保存前请逐项核对。`);
    const editing = matter.artifacts?.find((artifact) => artifact.id === draft.artifactId);
    if (draft.artifactText !== null && editing) draft.artifactVersion = editing.version;
    state.current = matter;if (draft.rows !== null) {draft.rowsBaseVersion = matter.version;draft.rowsBaseRevision = matter.revision_no;}state.difference = `已读最新事项版本 ${matter.version}。\n${changes.join('\n') || '事实内容未变化，其他事项状态已更新。'}\n核对后请重新点击对应保存按钮。`;
    if (matter.domain === 'feedback') await readOwnFeedback(id,{adoptVersion:true});
    if (structuredDomain(matter)) await readStructure(id,{adoptVersion:true});
    if (journeyDomain(matter)) await readJourney(id,{adoptVersion:true});
    if (checksDomain(matter)) await readChecks(id,{adoptVersion:true});
    await waitingUI.read(id,{adoptVersion:true});if (extractionUI.loaded(id)) await extractionUI.read(id,{adoptVersion:true});if (matter.sources?.length) await sourceUI.read(id,{adoptVersion:true});if (matter.domain === 'utilities') await readingsUI.read(id,{adoptVersion:true});
    if (sharingAllowed()) {for (const [key,sharing] of state.shareDrafts) if (key.startsWith(`${id}:`)) sharing.baseMatterVersion = matter.version;await readOwnShares(id);}
  } catch (error) {if (selection === state.selection) state.error = {...error,retry:reloadConflict};}
  finally {state.busy = false;render();schedulePoll();}
}
function prepare() {
  const m = state.current, draft = draftFor();
  if (!m || state.busy || ['processing','paused','handoff'].includes(m.assistant_status)) return;
  if (sourceUI.modelBlocked(m)) {toast('本事项有暂不用来源，全部新准备及模型输入暂缓；人工稿保存、来源修正和真实声明仍可继续。');return;}
  if (draft.prepareMode === 'model' && !state.session?.model_configured) {toast('模型未配置，请使用本地模板。');return;}
  mutate(`/api/matters/${encodeURIComponent(m.id)}/actions`,{expected_version:m.version,kind:'prepare',mode:draft.prepareMode},{message:draft.artifactText !== null ? '已提交整理，原稿编辑继续保留；新稿完成后可选择查看。' : '已提交整理任务，真实业务状态保持待核。'});
}
async function copyArtifact() {
  const m = state.current, draft = draftFor(), artifact = editingArtifact(m);
  const content = draft.artifactText ?? artifact?.content;
  if (!content) return;
  try {await navigator.clipboard.writeText(content);}
  catch {$('#artifact-editor')?.focus();$('#artifact-editor')?.select();toast('无法访问剪贴板。正文已选中，请手动复制。');return;}
  toast('已复制准备稿，未发送到外部。');
  if (state.current?.id === m.id) mutate(`/api/matters/${encodeURIComponent(m.id)}/events`,{expected_version:m.version,event_type:'copied',message:draft.artifactText !== null && draft.artifactText !== artifact.content ? '复制了尚未保存的准备稿编辑，未外发。' : '复制了准备稿，未外发。'});
}
async function importSourceFile(input) {
  const file = input.files?.[0],matter = state.current;if (!file || !matter) return;
  const draft = draftFor(matter),epoch = state.identityEpoch,selection = state.selection;input.value = '';
  if (draft.sourceText !== '') {toast('已有待保存来源文字已保留；请先保存或复制并清空，才可加载文件。');return;}
  const name = file.name.split(/[\\/]/).pop(),format = /\.(txt|md|csv|tsv)$/i.exec(name)?.[1]?.toLowerCase();
  if (!format) {toast('只支持UTF-8 TXT／MD／CSV／TSV文字；PDF／DOCX未读取，不假解析。');return;}
  if (!name || name.length > 200) {toast('文件声明只接受最多200字符的文件名；请保留原文另选合理文件名或手工粘贴。');return;}
  if (!Number.isInteger(file.size) || file.size < 0 || file.size > 48003) {toast('文件超过48003字节范围或大小无效，请选取实际需要的原文段落（最多12000字），不会截断。');return;}
  try {
    const content = new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());
    if (epoch !== state.identityEpoch || selection !== state.selection || state.current?.id !== matter.id) return;
    if (content.length > 12000) {toast('原文超过12000字，请选段后加载，不会截断或替换已有内容。');return;}
    if (content.includes('\u0000')) {toast('文件含无法作为文字来源保存的空字符，请选择UTF-8纯文本原文。');return;}
    if (draft.sourceText !== '') {toast('读取期间新增的来源文字已保留，文件未覆盖。');return;}
    if (!content.trim()) {clearSourceFile(draft);toast('文件没有可保存的非空原文，未附加文件声明；可手工粘贴实际内容。');return;}
    draft.sourceText = content;draft.sourceFileName = name;draft.sourceFileFormat = format;draft.sourceFileBytes = file.size;draft.sourceFileOriginal = content;draft.sourcesOpen = true;render();toast('文件文字仅加载到待保存区；请核对来源说明后保存，未保存原始附件、未自动发送模型。');
  } catch {if (epoch === state.identityEpoch && selection === state.selection) toast('UTF-8解码未成功，请转换为UTF-8 TXT／MD／CSV／TSV或手工粘贴实际选段。已有文字保留。');}
}
async function downloadRowsCsv() {
  const matter = state.current,calculation = matter?.calculation,revision = calculation?.input_revision;
  if (!['expense','inventory'].includes(matter?.domain) || !calculation?.row_count || !Number.isInteger(revision) || revision < 1) return;
  const epoch = state.identityEpoch,selection = state.selection,controller = new AbortController(),timeout = setTimeout(() => controller.abort(),30000);
  try {
    await ensureIdentity();if (epoch !== state.identityEpoch || selection !== state.selection || state.current?.id !== matter.id) return;
    const response = await fetch(`/api/matters/${encodeURIComponent(matter.id)}/rows/download?input_revision=${encodeURIComponent(revision)}`,{credentials:'same-origin',signal:controller.signal});
    if (!response.ok) {let body = {};try {body = await response.json();}catch {}throw {status:response.status,message:body.error?.message || body.message || '此明确已保存明细修订不可下载。'};}
    const savedBlob = await response.blob();if (epoch !== state.identityEpoch || selection !== state.selection || state.current?.id !== matter.id) return;
    const blob = new Blob([savedBlob],{type:'text/csv;charset=utf-8'}),url = URL.createObjectURL(blob),link = document.createElement('a');link.href = url;link.download = `${matter.domain === 'expense' ? '费用' : '物资'}已保存明细-r${revision}.csv`;link.click();setTimeout(() => URL.revokeObjectURL(url),1000);
    toast(`已请求浏览器下载已保存明细修订 ${revision} CSV，不含未保存编辑，未更新Excel。`);
  } catch (error) {if (epoch === state.identityEpoch && selection === state.selection && state.current?.id === matter.id) {if (error.status === 401) {clearPrivate({authenticated:false,mode:state.session?.mode});try {state.session = await request('/api/session');}catch {}}state.error = {message:error.name === 'AbortError' ? '下载请求已超时；人工明细和其他草稿保留，可稍后重试。' : error.message || '明细下载未完成；未保存编辑仍保留。',status:error.status};render();}}
  finally {clearTimeout(timeout);}
}
async function downloadArtifact(format='txt') {
  const artifact = editingArtifact(),m = state.current;if (!artifact || !Number.isInteger(artifact.version) || artifact.version < 1 || !['txt','md','docx'].includes(format)) return;
  const epoch = state.identityEpoch,selection = state.selection,version = artifact.version,id = artifact.id;
  try {
    await ensureIdentity();if (epoch !== state.identityEpoch || selection !== state.selection) return;
    const response = await fetch(`/api/artifacts/${encodeURIComponent(id)}/download?version=${encodeURIComponent(version)}&format=${format}`,{credentials:'same-origin'});
    if (!response.ok) {let body = {};try {body = await response.json();} catch {}throw {status:response.status,message:body.error?.message || body.message || '固定版本下载不可用。'};}
    const savedBlob = await response.blob(),blob = format === 'docx' ? new Blob([savedBlob],{type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}) : savedBlob;if (epoch !== state.identityEpoch || selection !== state.selection || state.current?.id !== m.id) return;
    const url = URL.createObjectURL(blob),link = document.createElement('a');link.href = url;link.download = `事项准备稿-v${version}.${format}`;link.click();setTimeout(() => URL.revokeObjectURL(url),1000);
    toast(`已请求浏览器下载已保存版本 ${version}；未保存编辑未包含，未外发。`);
  } catch (error) {if (epoch === state.identityEpoch) {if (error.status === 401) {clearPrivate({authenticated:false,mode:state.session?.mode});state.session = await request('/api/session');}state.error = {message:error.message || '下载未完成，正文编辑仍保留。',status:error.status};render();}}
}
$('#workspace').addEventListener('toggle',(event) => {
  const detail = event.target;if(detail.id==='continuation-details'&&detail.isConnected&&state.current)draftFor().continuationOpen=detail.open;if(detail.id==='create-options'&&detail.isConnected)state.composer.optionsOpen=detail.open;if(detail.matches('.category-panel')&&detail.isConnected&&state.current)draftFor().categoryOpen=detail.open;waitingUI.toggle(detail);readingsUI.toggle(detail);sourceUI.toggle(detail);extractionUI.toggle(detail);tabularUI.toggle(detail);modelRowsUI.toggle(detail);modelPreviewUI.toggle(detail);privateContextUI.toggle(detail);relatedTasksUI.toggle(detail);sourceImpactUI.toggle(detail);attachmentsUI.toggle(detail);if (detail.id === 'share-details' && detail.isConnected && detail.open && sharingAllowed() && !state.shareRecipientsLoaded && !state.shareRecipientsLoading) readShareRecipients();if (detail.isConnected && detail.dataset?.checksDetails && state.current) {const draft = state.checksDrafts?.get(state.current.id);if (draft) {draft.ui ||= {};if (detail.dataset.checksDetails === 'sources') draft.ui.sourcesOpen = detail.open;else {draft.ui.differenceOpen ||= {};draft.ui.differenceOpen[detail.dataset.checkDifferenceId] = detail.open;}}}
  if (detail.isConnected && detail.dataset?.journeyDetails && state.current) {const draft = state.journeyDrafts?.get(state.current.id);if (draft) {draft.ui ||= {};draft.ui[detail.dataset.journeyDetails] = detail.open;}}
  if (detail.isConnected && detail.dataset?.structureDetails && state.current) {const ui = draftFor().structureUi ||= {sourcesOpen:false,progressOpen:{},differenceOpen:{}};if (detail.dataset.structureDetails === 'sources') ui.sourcesOpen = detail.open;else {const id = detail.closest('[data-structure-id]')?.dataset.structureId;if (id) (ui[detail.dataset.structureDetails === 'progress' ? 'progressOpen' : 'differenceOpen'] ||= {})[id] = detail.open;}}
  if (detail.id === 'matter-tools' && detail.isConnected && state.current) draftFor().toolsOpen = detail.open;
  const key = {'sources-details':'sourcesOpen','evidence-details':'evidenceOpen','rows-details':'rowsOpen','service-submit-details':'serviceSubmitOpen','share-details':'shareOpen'}[event.target.id];if (key && event.target.isConnected && state.current) draftFor()[key] = event.target.open;},true);
$('#workspace').addEventListener('input',(event) => {
  if (event.target.dataset?.meetingCandidate) {const record = meetingCandidateRecord(state.current.id),id = event.target.dataset.meetingCandidate;if (event.target.checked) record.selected.add(id);else record.selected.delete(id);render();return;}
  const input = event.target;if(input.closest('#conversation-form')) {const draft=draftFor();if(input.name==='text')draft.messageText=input.value;else if(input.name==='mode'){draft.messageMode=input.value;render();}return;}if (menuModelUI.input(input) || attachmentsUI.input(input) || contextUI.input(input) || waitingUI.input(input) || readingsUI.input(input) || sourceUI.input(input) || extractionUI.input(input) || tabularUI.input(input) || modelRowsUI.input(input) || modelChecksUI.input(input)) return;
  if (input.closest('#goal-revision-form')) {const edit = draftFor()?.goalEdit;if (edit) {if (input.name === 'goal_text') edit.goal_text = input.value;else if (input.name === 'recheck_field') edit.recheck_fields = input.checked ? [...new Set([...edit.recheck_fields,input.value])] : edit.recheck_fields.filter(field=>field !== input.value);edit.dirty = true;}return;}
  if (input.id === 'service-followup-content') {const draft = state.serviceFollowupDrafts?.get(state.serviceCurrent?.id);if (draft) {draft.content = input.value;draft.dirty = true;}return;}
  const revisionForm = input.closest('[data-menu-revision]');if (revisionForm) {const id=revisionForm.dataset.menuRevision,draft = state.menuRevisionDrafts?.get(id);if(draft&&adoptionInput(input,state.menuFeedback.get(id)||[],draft)){if(input.dataset.adoptFeedback!==undefined)render();return;}if (draft && ['menu_date','meal_slot','dishes','notes'].includes(input.name)) {draft[input.name] = input.value;draft.dirty = true;}return;}
  const responseForm = input.closest('[data-cook-response-menu]');if (responseForm) {const menu = state.menus.find((item) => item.id === responseForm.dataset.cookResponseMenu),feedback = state.menuFeedback.get(menu?.id)?.find((item) => item.id === responseForm.dataset.cookResponseFeedback);if (menu && feedback) cookResponseDraft(menu,feedback).response_text = input.value;return;}
  if (input.closest('#share-form')) {const draft = shareDraftFor();if (!draft) return;if (input.name === 'dependency_ref') draft.dependency_refs = [...input.form.querySelectorAll('[name="dependency_ref"]:checked')].map((item) => item.value);else draft[input.name] = input.type === 'checkbox' ? input.checked : input.value;draft.dirty = true;$('#share-saved-label').textContent = '有未提交分享编辑；稿件固定版本保留';$('#share-saved-label').classList.add('dirty-note');return;}
  if (input.id === 'source-file') {importSourceFile(input);return;}
  if (checksInput(input) || journeyInput(input) || structureInput(input)) return;
  if (input.closest('#own-feedback-form')) {
    const record = state.ownFeedback?.get(state.current?.id);if (!record) return;
    const draft = ownFeedbackDraft(record);if (!draft.dirty) draft.baseVersion = record.expected_version;draft[input.name] = input.value;draft.dirty = true;
    $('#own-feedback-saved').textContent = '有未提交的更正，当前投影仍保留原记录';$('#own-feedback-saved').classList.add('dirty-note');return;
  }
  if (input.closest('#service-submit-form')) {
    const draft = serviceSubmitDraft(state.current);draft[input.name] = input.value;
    if (input.name === 'permission_status') input.form.elements.permission_note.required = input.value === 'reported_permission';
    const field = state.current.domain === 'repair' ? 'problem' : 'scope';
    $('#service-share-preview').innerHTML = projectionMarkup({domain:state.current.domain,location:state.current.facts?.location?.value,request_text:state.current.facts?.[field]?.value,access_window:state.current.facts?.access_window?.status === 'confirmed' ? state.current.facts.access_window.value : '',request_kind:state.current.facts?.request_kind?.status === 'confirmed' ? state.current.facts.request_kind.value : 'unknown',contact:draft.contact,notes:draft.notes,entry_permission:{status:draft.permission_status,note:draft.permission_note}});
    return;
  }
  if (input.closest('.service-actions')) {if (state.serviceCurrent) serviceDraft(state.serviceCurrent)[input.name] = input.value;return;}
  if (input.id === 'menu-public-notes') {draftFor().publicNotes = input.value;return;}
  const feedbackForm = input.closest('[data-feedback-menu]');
  if (feedbackForm) {const menu = state.menus.find((item) => item.id === feedbackForm.dataset.feedbackMenu);if (menu) feedbackDraft(menu)[input.name] = input.value;return;}
  if (input.closest('#account-form')) {
    state.accountDraft ||= {username:'',display_name:'',capabilities:[]};
    if (input.name === 'capability') state.accountDraft.capabilities = [...input.form.querySelectorAll('[name="capability"]:checked')].map((item) => item.value);
    else state.accountDraft[input.name] = input.value;
    return;
  }
  if (input.dataset?.rowField) {
    const row = ensureRows().find((item) => item.id === input.closest('[data-row-id]')?.dataset.rowId);
    if (row) row[input.dataset.rowField] = input.value;
    const label = $('#rows-saved-label');if (label) {label.textContent = '表格有未保存编辑；计算仍来自上次保存';label.classList.add('dirty-note');}
    const warning = $('#calculation-draft-warning');if (warning) warning.hidden = false;
    return;
  }
  if (input.closest('#evidence-form')) {draftFor().evidence[input.name] = input.value;return;}
  if (privateContextUI.input(input)) return;
  if (relatedTasksUI.input(input)) return;
  if (modelJourneyUI.input(input)) return;
  if (mealSummariesUI.input(input)) return;
  if (expenseJourneyUI.input(input)) return;
  if (rowReportsUI.input(input)) return;
  if (materialNodesUI.input(input)) return;
  if (materialRecordsUI.input(input)) return;
  if (input.id === 'translation-language') {draftFor().translationLanguage = input.value;return;}
  if (input.id === 'artifact-select') {selectArtifact(input.value);return;}
  if(input.closest('#category-form')){draftFor().categoryDomain=input.value;return;}
  if (input.id === 'prepare-mode') {draftFor().prepareMode = input.value;return;}
  if (input.closest('#create-form')) {
    state.composer[input.name] = input.type === 'checkbox' ? input.checked : input.value;
    if (input.name === 'source_kind') input.form.elements.reported_by.required = input.value === 'oral_note';
    if (input.name === 'initial_mode') render();
  } else if (input.closest('#facts-form')) draftFor().factChanges[input.name] = input.value;
  else if (input.id === 'artifact-editor') {
    const draft = draftFor(), artifact = editingArtifact(state.current);
    if (draft.artifactText === null) {draft.artifactId = artifact.id;draft.artifactVersion = artifact.version;}
    draft.artifactText = input.value;
    $('#saved-label').textContent = input.value === artifact.content ? `已保存 · 版本 ${artifact.version}` : '有未保存编辑';$('#saved-label').classList.toggle('dirty-note',input.value !== artifact.content);
  } else if (input.closest('#source-form')) {
    const draft = draftFor();draft[{text:'sourceText',kind:'sourceKind',reported_by:'reportedBy'}[input.name]] = input.value;
    if (input.name === 'text' && input.value === '') clearSourceFile(draft);
    if (input.name === 'kind') input.form.elements.reported_by.required = input.value === 'oral_note';
  }
});
$('#workspace').addEventListener('submit',(event) => {
  event.preventDefault();if (state.busy) return;
  const form = event.target, m = state.current;
  if (form.id === 'conversation-form' && m) {
    const draft=draftFor(),text=draft.messageText.trim(),mode=['paused','handoff'].includes(m.assistant_status)||m.model_disclosure_blocked?'save':draft.messageMode??(state.session?.model_configured?'model':'template');if(!text)return;
    mutate(`/api/matters/${encodeURIComponent(m.id)}/messages`,{expected_version:m.version,text,mode},{onSuccess:()=>{draft.messageText='';},message:mode==='save'?'补充已保存；未触发整理，原稿保留。':'补充已保存并开始整理；可以继续输入，原稿保留。'});return;
  }
  if (materialNodesUI.submit(form)) return;
  if (materialRecordsUI.submit(form)) return;
  if (form.id === 'extraction-form') {extractionUI.save();return;}
  if (form.dataset.sourceLifecycleId) {sourceUI.save(form);return;}
  if (form.id === 'readings-form') {readingsUI.save();return;}
  if (form.id === 'context-form') {contextUI.save();return;}
  if (form.id === 'waiting-form') {waitingUI.save();return;}
  if (form.id === 'goal-revision-form') {const draft = draftFor(),edit = draft.goalEdit;if (!m || !edit) return;mutate(`/api/matters/${encodeURIComponent(m.id)}/goal`,{expected_version:edit.expected_version,goal_text:edit.goal_text,recheck_fields:edit.recheck_fields},{onSuccess:()=>{draft.goalEdit = null;draft.goalEditorOpen = false;},message:'委托目标已修正；旧原文与人工稿保留，所选事实待重核，未调用模型或改变业务记录。'});return;}
  if (form.id === 'service-followup-form') {saveServiceFollowup();return;}
  if (form.dataset.menuRevision) {submitMenuRevision(form);return;}
  if (form.dataset.cookResponseMenu) {submitCookResponse(form);return;}
  if (form.id === 'share-form') {createSharing();return;}
  if (form.id === 'checks-form') {saveChecks();return;}
  if (form.id?.startsWith('journey-') && journeySubmit(form)) return;
  if (form.id === 'structure-form') {saveStructure();return;}
  if (form.id === 'own-feedback-form') {
    const record = state.ownFeedback?.get(m?.id);if (!record || !hasCapability('employee')) return;
    const draft = ownFeedbackDraft(record), selection = state.selection;
    if (!draft.feedback_text.trim()) {toast('请填写本人实际更正的意见。');return;}
    auxiliaryWrite(`/api/menu-feedback/${encodeURIComponent(record.id)}/revise`,{expected_version:draft.baseVersion ?? record.expected_version,feedback_text:draft.feedback_text.trim(),dish:draft.dish.trim(),dietary_constraint:draft.dietary_constraint.trim()},async (result) => {
      state.ownFeedback.set(record.id,result);state.ownFeedbackDrafts.set(record.id,{feedback_text:result.feedback_text,dish:result.dish || '',dietary_constraint:result.dietary_constraint || '',baseVersion:result.expected_version,dirty:false});
      toast(result.shared_with_publisher ?? result.shared ? '已明确更正提交给原厨师的意见。' : '本人私有更正已保存，共享仍保持撤回。');await syncFeedbackMatter(record.id,selection);refreshList();
    });return;
  }
  if (form.id === 'service-submit-form') {
    const draft = serviceSubmitDraft(m);if (!draft.clerk_id) {toast('请读取并选择一个实际综合经办账号。');return;}
    const selection = state.selection;
    auxiliaryWrite(`/api/matters/${encodeURIComponent(m.id)}/service/submit`,{expected_version:m.version,input_revision:m.revision_no,clerk_id:draft.clerk_id,contact:draft.contact.trim(),notes:draft.notes.trim(),entry_permission:{status:draft.permission_status,note:draft.permission_note.trim()}},async (result) => {
      draftFor(m).serviceSubmit = null;
      if (selection !== state.selection) return;state.current = null;state.view = 'services';state.serviceCurrent = result.service || result;state.lostServiceId = null;++state.selection;await refreshServices();toast('必要服务信息已交给选定经办，未共享私人原文或完整准备稿。');
    });return;
  }
  if (form.id?.startsWith('service-')) {
    const service = state.serviceCurrent;if (!service) return;const draft = serviceDraft(service);
    if (form.id === 'service-assign-form') {if (!draft.executor_id) {toast('请选择一个实际执行者账号。');return;}writeService('assign',{executor_id:draft.executor_id,note:draft.assign_note.trim()},['assign_note']);}
    else if (form.id === 'service-respond-form') {const command = event.submitter?.dataset.command;if (!command) return;if (['blocked','claimed_done'].includes(command) && !draft.respond_note.trim()) {toast('请先写明本人实际障碍或处理说明。');return;}writeService('respond',{assignment_epoch:service.assignment_epoch,command,note:draft.respond_note.trim(),...(draft.occurred_at.trim() ? {occurred_at:draft.occurred_at.trim()} : {})},['respond_note','occurred_at']);}
    else if (form.id === 'service-result-form') writeService('result',{assignment_epoch:service.assignment_epoch,result:draft.result,note:draft.result_note.trim()},['result_note']);
    else if (form.id === 'service-withdraw-form') writeService('withdraw',{note:draft.withdraw_note.trim()},['withdraw_note']);
    else if (form.id === 'service-revise-form') writeService('revise',{input_revision:Number(draft.input_revision),note:draft.revise_note.trim()},['input_revision','revise_note']);
    else if (form.id === 'service-statement-form') writeService('statement',{reported_by:draft.reported_by.trim(),note:draft.statement_note.trim(),...(draft.evidence_refs.trim() ? {evidence_refs:draft.evidence_refs.split('\n').map((ref) => ref.trim()).filter(Boolean)} : {})},['reported_by','statement_note','evidence_refs']);
    return;
  }
  if (form.id === 'auth-form') {authOperation(form);return;}
  if (form.id === 'account-form') {
    if (!state.session?.principal?.is_admin) return;
    const fields = new FormData(form), body = {username:String(fields.get('username') || '').trim(),display_name:String(fields.get('display_name') || '').trim(),capabilities:fields.getAll('capability')};
    state.activationToken = null;
    auxiliaryWrite('/api/admin/accounts',body,async (result) => {state.activationToken = result.activation_token || null;state.accountDraft = {username:'',display_name:'',capabilities:[]};await refreshAccounts();});return;
  }
  if (form.id === 'legacy-claim-form') {
    if (!state.session?.principal?.is_admin || !(state.legacySummary?.unassigned_count > 0)) return;
    const target = new FormData(form).get('target_account_id');
    if (!state.accounts.some((account) => account.id === target && account.state === 'active' && account.capabilities?.includes('clerk'))) {toast('请选择已激活且具有综合经办职责的账号。');return;}
    auxiliaryWrite('/api/admin/legacy/claim',{expected_count:state.legacySummary.unassigned_count,target_account_id:target},async (result) => {toast(`已明确认领 ${result.claimed_count} 件旧成果，原记录继续保留。`);await refreshAccounts();});return;
  }
  if (form.id === 'menu-publish-form') {
    if (!hasCapability('cook') || m?.domain !== 'menu') return;
    const draft = draftFor(), body = {expected_version:m.version,menu_date:m.facts?.menu_date?.value,meal_slot:m.facts?.meal_slot?.value,dishes:m.facts?.dishes?.value,notes:draft.publicNotes};
    auxiliaryWrite(`/api/matters/${encodeURIComponent(m.id)}/menu/publish`,body,async () => {draft.publicNotes = '';toast('菜单快照已发布给本工作区员工，未发布到微信；备餐与供餐仍须真实核对。');if (state.current?.id === m.id) await loadMatter(m.id);});return;
  }
  if (form.dataset.feedbackMenu) {
    const menu = state.menus.find((item) => item.id === form.dataset.feedbackMenu);if (!menu || !hasCapability('employee')) return;
    const draft = feedbackDraft(menu), body = {version:menu.version,feedback_text:draft.feedback_text.trim(),dish:draft.dish.trim(),dietary_constraint:draft.dietary_constraint.trim()};
    if (!body.feedback_text) {toast('请填写你自愿提供的实际意见。');return;}
    auxiliaryWrite(`/api/menu-publications/${encodeURIComponent(menu.id)}/feedback`,body,() => {state.feedbackDrafts.delete(`${menu.id}:${menu.version}`);toast('意见已提交给本菜单厨师，未向全员公开。');refreshList();});return;
  }
  if (form.id === 'create-form') {
    const c = state.composer;
    if (!c.goal_text.trim()) {toast('请先写下要办的事情。');return;}
    mutate('/api/matters',{...c,initial_mode:c.initial_mode??(state.session?.model_configured?'model':'template'),auto_prepare:(c.initial_mode??(state.session?.model_configured?'model':'template')) === 'model' ? false : c.auto_prepare,goal_text:c.goal_text.trim(),reported_by:c.reported_by.trim() || undefined},{onSuccess:() => {state.composer = {goal_text:'',domain:'auto',source_kind:'user_text',reported_by:'',auto_prepare:true,initial_mode:null,optionsOpen:false};},message:'事项已保存，可以随时回来续办。'});
  } else if(form.id==='category-form'&&m){
    const draft=draftFor();if(Object.keys(draft.factChanges).length||draft.rows!==null){toast('未保存事实或明细仍在原类别，请先保存或复制，再调整类别。');return;}
    mutate(`/api/matters/${encodeURIComponent(m.id)}/category`,{expected_version:m.version,input_revision:m.revision_no,domain:draft.categoryDomain??m.domain},{onSuccess:()=>{draft.categoryDomain=null;},message:'办理类别已调整，原话与人工稿保留，没有模型调用。'});
  } else if (form.id === 'facts-form') {
    const draft = draftFor();
    const fieldChanges = Object.fromEntries(Object.entries(draft.factChanges).filter(([key,value]) => value.trim() !== String(m.facts?.[key]?.value ?? '') || (value.trim() && m.facts?.[key]?.status !== 'confirmed')).map(([key,value]) => [key,value.trim() || null]));
    // Existing candidate text also requires a deliberate confirmation when saved.
    for (const key of Object.keys(labelsFor(m))) if (!Object.hasOwn(fieldChanges,key) && !Object.hasOwn(draft.factChanges,key) && m.facts?.[key]?.value && m.facts[key].status !== 'confirmed') fieldChanges[key] = m.facts[key].value;
    if (!Object.keys(fieldChanges).length) {toast('没有需要保存的事实变化。');return;}
    mutate(`/api/matters/${encodeURIComponent(m.id)}/facts/confirm`,{expected_version:m.version,field_changes:fieldChanges},{onSuccess:() => {draft.factChanges = {};draft.factsOpen = false;},message:'已保存核对事实，原成果是否需重整以当前修订为准。'});
  } else if (form.id === 'source-form') {
    const draft = draftFor();
    if (!draft.sourceText.trim()) return;
    const text = draft.sourceText.trim(),fileInfo = draft.sourceFileName && ['txt','md','csv','tsv'].includes(draft.sourceFileFormat) && Number.isInteger(draft.sourceFileBytes) && typeof draft.sourceFileOriginal === 'string' ? {name:draft.sourceFileName,format:draft.sourceFileFormat,reported_bytes:draft.sourceFileBytes,text_edited:text !== draft.sourceFileOriginal} : null;
    mutate(`/api/matters/${encodeURIComponent(m.id)}/sources`,{expected_version:m.version,text,kind:draft.sourceKind,reported_by:draft.reportedBy.trim() || undefined,...(fileInfo ? {file_info:fileInfo} : {})},{onSuccess:() => {draft.sourceText = '';draft.reportedBy = '';clearSourceFile(draft);},message:'补充原话已保存，尚未自动确认为事实。'});
  } else if (form.id === 'evidence-form') {
    const draft = draftFor(), evidence = Object.fromEntries(Object.entries(draft.evidence).map(([key,value]) => [key,value.trim()]));
    if (Object.values(evidence).some((value) => !value)) {toast('请填写环节、陈述人、发生日期／时间、来源位置和实际文字。');return;}
    mutate(`/api/matters/${encodeURIComponent(m.id)}/evidence`,{expected_version:m.version,...evidence,input_revision:m.revision_no},{onSuccess:() => {draft.evidence = {track:'',record_type:'statement',reported_by:'',occurred_at:'',source_id:'',source_position:'',text:''};},message:'已保存人工转录候选；系统未核真，业务状态仍待核。'});
  } else if (form.id === 'rows-form') {
    const draft = draftFor();
    if (draft.rows === null) {toast('明细没有待保存的修改。');return;}
    const rows = draft.rows.map((row) => Object.fromEntries(Object.entries(row).map(([key,value]) => [key,typeof value === 'string' ? value.trim() : value])));
    mutate(`/api/matters/${encodeURIComponent(m.id)}/rows/confirm`,{expected_version:draft.rowsBaseVersion ?? m.version,rows},{onSuccess:() => {draft.rows = null;},message:'已保存人工核对明细，计算结果已按来源和分组更新。'});
  }
});
$('#workspace').addEventListener('click',(event) => {
  const button = event.target.closest('button');if (!button || button.disabled) return;
  if (button.dataset.conversationArtifact && state.current) {
    const draft=draftFor(),artifact=editingArtifact();if(draft.artifactText!==null&&draft.artifactText!==artifact?.content){toast('当前正文有未保存修改；请先保存，原编辑保留。');draft.toolsOpen=true;render();$('#artifact-editor')?.focus();return;}
    draft.toolsOpen=true;selectArtifact(button.dataset.conversationArtifact);render();$('#artifact-editor')?.scrollIntoView({block:'center'});$('#artifact-editor')?.focus({preventScroll:true});return;
  }
  if (button.dataset.open) {const id=button.dataset.open;if(location.hash.slice(1)!==encodeURIComponent(id))location.hash=encodeURIComponent(id);else if(state.current?.id!==id)loadMatter(id);return;}
  const suggestion = button.dataset.suggestion;
  if (suggestion) {
    if (!state.catalog[suggestion]) return;
    state.composer.goal_text = `请帮我准备${domainLabel(suggestion)}，整理已有信息、缺项与需要真人处理的事项。`;state.composer.domain = suggestion;render();$('#goal').focus();return;
  }
  const action = button.dataset.action, m = state.current;if(action==='waiting-followup'&&m)draftFor().continuationOpen=true;if (privateContextUI.action(action) || relatedTasksUI.action(action,button) || menuModelUI.action(action,button) || attachmentsUI.action(action,button) || contextUI.action(action,button) || waitingUI.action(action,button) || readingsUI.action(action,button) || sourceUI.action(action,button) || extractionUI.action(action,button) || tabularUI.action(action,button) || modelRowsUI.action(action,button) || modelChecksUI.action(action,button) || modelPreviewUI.action(action,button) || sourceImpactUI.action(action,button)) return;
  if(action==='question-waiting'){saveQuestionWaiting(Number(button.dataset.questionIndex));return;}
  if(action==='open-continuation'&&m){draftFor().continuationOpen=true;render();$('#continuation-details')?.scrollIntoView({block:'start'});return;}
  if (action === 'open-title-context' && m) {draftFor().toolsOpen=true;render();const panel=$('.private-context-panel');if(panel){panel.open=true;panel.scrollIntoView({block:'start'});}return;}
  if (action === 'reload-menu-feedback') {const id = button.dataset.menuId || state.feedbackOpen;if (id) loadMenuFeedback(id,{adoptVersion:true});return;}
  if (action === 'refresh-share-recipients') {readShareRecipients();return;}
  if (action === 'reload-own-shares') {reloadConflict();return;}
  if (action === 'withdraw-share') {
    const share = state.ownShares?.get(m?.id)?.find((item) => item.id === button.dataset.shareId);if (!share || !sharingAllowed()) return;
    auxiliaryWrite(`/api/shares/${encodeURIComponent(share.id)}/withdraw`,{expected_version:share.version},async () => {toast('已撤回本工作区共享；旧读取与下载失效，已取出副本无法召回。');await readOwnShares(m.id);});return;
  }
  if (action === 'refresh-received-shares') {refreshReceivedShares();return;}
  if (action === 'open-received-share') {loadReceivedShare(button.dataset.shareId);return;}
  if (action === 'reload-received-share' || action === 'copy-received-share') {if (state.receivedShare) loadReceivedShare(state.receivedShare.id,{copy:action === 'copy-received-share'});return;}
  if (action === 'download-received-share') {downloadReceivedShare(button.dataset.format);return;}
  if (modelJourneyUI.action(action)) return;
  if (mealSummariesUI.action(action)) return;
  if (expenseJourneyUI.action(action)) return;
  if (rowReportsUI.action(action,button)) return;
  if (materialNodesUI.action(action,button)) return;
  if (materialRecordsUI.action(action)) return;
  if (action?.startsWith('checks-') && checksAction(action,button)) return;
  if (action?.startsWith('journey-') && journeyAction(action,button)) return;
  if (action?.startsWith('meeting-candidates-')) {if (!structuredDomain(m)) return;if (action === 'meeting-candidates-read') readMeetingCandidates();else if (action === 'meeting-candidates-append') appendMeetingCandidates();else if (action === 'meeting-candidates-clear') {meetingCandidateRecord(m.id).selected.clear();render();}return;}
  if (action?.startsWith('structure-')) {
    if (!structuredDomain(m)) return;
    structureState();const draft = state.structureDrafts.get(m.id),itemId = button.closest('[data-structure-id]')?.dataset.structureId,record = draft?.records.find((item) => item.id === itemId);
    if (action === 'structure-reload') {readStructure(m.id,{adoptVersion:true});return;}
    if (!draft) return;
    if (action === 'structure-render') {renderStructure();return;}
    if (action === 'structure-progress-save') {saveProgress(itemId);return;}
    if (action === 'structure-add' && draft.records.length < 100) draft.records.push(makeStructureRecord(m.domain,crypto.randomUUID()));
    else if (action === 'structure-remove' && record) {if (draft.original.some((item) => item.id === itemId) && !draft.removed_ids.includes(itemId)) draft.removed_ids.push(itemId);draft.records = draft.records.filter((item) => item.id !== itemId);}
    else if (action === 'structure-add-ref' && record && record.refs.length < 10) record.refs.push({source_id:'',start_line:'',end_line:''});
    else if (action === 'structure-remove-ref' && record) record.refs.splice(Number(button.dataset.refIndex),1);
    else return;
    markStructureDirty(draft);render();return;
  }
  if (action === 'reload-own-feedback') {if (m) readOwnFeedback(m.id,{adoptVersion:true});return;}
  if (action === 'withdraw-own-feedback') {
    const record = state.ownFeedback?.get(m?.id);if (!record || !hasCapability('employee')) return;
    const selection = state.selection;
    auxiliaryWrite(`/api/menu-feedback/${encodeURIComponent(record.id)}/withdraw`,{expected_version:record.expected_version},async (result) => {state.ownFeedback.set(record.id,result);const draft = state.ownFeedbackDrafts.get(record.id);if (draft) draft.baseVersion = result.expected_version;toast('已撤回给原厨师的意见共享；本人记录仍保留，后续更正不会重新公开。');await syncFeedbackMatter(record.id,selection);});return;
  }
  if (action === 'load-service-clerks') {loadServiceAccounts('clerk');return;}
  if (action === 'load-service-executors') {loadServiceAccounts('executor');return;}
  if (action === 'refresh-services') {refreshServices();return;}
  if (action === 'open-service') {loadService(button.dataset.serviceId);return;}
  if (action === 'reload-service') {if (state.serviceCurrent) loadService(state.serviceCurrent.id,{reload:true});return;}
  if (action === 'auth-tab') {state.authTab = state.authTab === 'activate' ? 'login' : 'activate';state.error = null;render();return;}
  if (action === 'dismiss-token') {state.activationToken = null;render();return;}
  if (['disable-account','reset-account'].includes(action)) {
    if (!state.session?.principal?.is_admin) return;
    const account = state.accounts.find((item) => item.id === button.dataset.accountId);
    if (!account || account.id === state.session.principal.id) return;
    state.activationToken = null;
    auxiliaryWrite(`/api/admin/accounts/${encodeURIComponent(account.id)}/control`,{expected_epoch:account.auth_epoch,command:action === 'disable-account' ? 'disabled' : 'reset'},async (result) => {state.activationToken = result.activation_token || null;await refreshAccounts();});return;
  }
  if (action === 'refresh-menus') {refreshMenus();return;}
  if (action === 'read-service-draft-history') {const service = state.serviceCurrent,draft = state.serviceFollowupDrafts?.get(service?.id);if (!service || !draft) return;const epoch = state.identityEpoch,selection = state.selection;request(`/api/services/${encodeURIComponent(service.id)}/draft?version=${encodeURIComponent(button.dataset.version)}`).then(result=>{if (epoch !== state.identityEpoch || selection !== state.selection || state.serviceCurrent?.id !== service.id) return;draft.historyPreview = result;render();}).catch(error=>{if (epoch === state.identityEpoch && selection === state.selection) {state.error = {message:error.message,status:error.status};render();}});return;}
  if (action === 'read-service-draft') {readServiceFollowup();return;}
  if (action === 'adopt-service-draft-base') {const draft = state.serviceFollowupDrafts?.get(state.serviceCurrent?.id);if (!draft?.remote) return;draft.version = draft.remote.version;draft.basis_service_version = draft.remote.current_service_version;draft.dirty = true;render();toast('已采用刚读回的基版，正文保留；请核对后保存。');return;}
  if (action === 'copy-service-draft') {const draft = state.serviceFollowupDrafts?.get(state.serviceCurrent?.id);if (draft) navigator.clipboard.writeText(draft.content).then(()=>toast('已复制当前跟进正文，未发送。')).catch(()=>toast('复制未完成，可在正文中手动选择复制。'));return;}
  if (action === 'revise-menu') {const menu = state.menus.find((item) => item.id === button.dataset.menuId);if (!menu || !hasCapability('cook') || menu.publisher_id !== state.session?.principal?.id || menu.status !== 'published') return;state.menuRevisionDrafts ||= new Map();if (!state.menuRevisionDrafts.has(menu.id)) state.menuRevisionDrafts.set(menu.id,{expected_version:menu.version,menu_date:menu.menu_date,meal_slot:menu.meal_slot,dishes:menu.dishes,notes:menu.notes,dirty:false});render();return;}
  if (['withdraw-menu','view-menu-feedback'].includes(action)) {
    const menu = state.menus.find((item) => item.id === button.dataset.menuId);
    if (!menu || !hasCapability('cook') || menu.publisher_id !== state.session?.principal?.id) return;
    if (action === 'withdraw-menu') {if (menu.status === 'withdrawn') return;auxiliaryWrite(`/api/menu-publications/${encodeURIComponent(menu.id)}/withdraw`,{expected_version:menu.version},async () => {state.menus = state.menus.filter((item) => item.id !== menu.id);state.menuFeedback.delete(menu.id);toast('已撤回本工作区菜单发布，既有下载副本无法召回。');await refreshMenus();});}
    else loadMenuFeedback(menu.id,{adoptVersion:true});
    return;
  }
  if (action === 'dismiss-error') {state.error = null;state.difference = null;render();}
  else if (action === 'retry') state.error?.retry?.();
  else if (action === 'reload-conflict') reloadConflict();
  else if (action === 'read-preparation-result') loadMatter(m.id);
  else if (action === 'edit-goal') {const draft = draftFor();draft.goalEditorOpen = !draft.goalEditorOpen;if (!draft.goalEdit) draft.goalEdit = {goal_text:m.goal_text,expected_version:m.version,recheck_fields:[],dirty:false};render();}
  else if (action === 'adopt-goal-base') {const edit = draftFor()?.goalEdit;if (edit) {edit.expected_version = m.version;edit.dirty = true;render();toast('采用当前已读基版，目标文字与待重核选择保留。');}}
  else if (action === 'toggle-facts') {draftFor().factsOpen = !draftFor().factsOpen;render();}
  else if (action === 'toggle-activity') {state.activityOpen = !state.activityOpen;if(state.activityOpen)draftFor().toolsOpen=true;render();}
  else if (action === 'prepare') prepare();
  else if (action === 'translate-artifact') translateArtifact();
  else if (action === 'toggle-workflow') mutate(`/api/matters/${encodeURIComponent(m.id)}/workflow`,{expected_version:m.version,enabled:!m.workflow?.enabled},{message:m.workflow?.enabled ? '已关闭后续持续整理；暂停按钮可停止已登记动作。' : '已开启持续本地模板整理，不自动调用模型。'});
  else if (action === 'add-row') {
    const rows = ensureRows(), unitKey = m.domain === 'expense' ? 'currency' : 'unit';
    if (rows.length >= 200) {toast('单事项最多 200 行，请按实际范围拆分核对。');return;}
    rows.push({id:crypto.randomUUID(),label:'',value:'',[unitKey]:'',period:'',source_id:'',source_position:''});draftFor().rowsOpen = true;render();
  }
  else if (action === 'remove-row') {const draft = draftFor();draft.rows = ensureRows().filter((row) => row.id !== button.dataset.rowId);render();}
  else if (action === 'use-server-rows') {if (confirm('采用服务器已保存明细会替换表格中的未保存编辑，确认采用吗？')) {draftFor().rows = null;render();}}
  else if (action === 'use-latest-artifact') {
    selectArtifact(latestArtifact(m).id);
  }
  else if (['pause','resume','handoff'].includes(action)) mutate(`/api/matters/${encodeURIComponent(m.id)}/control`,{expected_version:m.version,command:action},{message:{pause:'已暂停助理，不代表取消业务。',resume:'已恢复准备，可继续核对当前信息。',handoff:'已切换为人工接手，已有成果仍可编辑。'}[action]});
  else if (action === 'start-manual-artifact') {
    const draft = draftFor();
    const content = `# ${domainLabel(m.domain)}人工稿\n\n## 本次目标\n${m.goal_text}\n\n## 人工准备正文\n请在此编辑实际信息；未知项保留待核。`;
    mutate(`/api/matters/${encodeURIComponent(m.id)}/artifacts`,{expected_version:m.version,input_revision:m.revision_no,edited_content:content},{onSuccess:(fresh) => {draft.selectedArtifactId = fresh.saved_artifact_id;},message:'人工稿已建立并保存，可以直接编辑；助理状态保持，未调用模型。'});
  }
  else if (action === 'save-artifact') {
    const draft = draftFor(), artifact = editingArtifact(m);
    if (draft.artifactText === null || draft.artifactText === artifact.content) {toast('准备稿没有未保存的修改。');return;}
    mutate(`/api/artifacts/${encodeURIComponent(draft.artifactId || artifact.id)}`,{base_version:draft.artifactVersion ?? artifact.version,edited_content:draft.artifactText},{method:'PATCH',onSuccess:() => {draft.selectedArtifactId = draft.artifactId || artifact.id;draft.artifactText = null;draft.artifactId = null;draft.artifactVersion = null;},message:'准备稿编辑已保存。'});
  } else if (action === 'copy-artifact') copyArtifact();
  else if (action === 'download-rows-csv') downloadRowsCsv();
  else if (action === 'download-artifact') downloadArtifact();
  else if (action === 'download-artifact-md') downloadArtifact('md');
  else if (action === 'download-artifact-docx') downloadArtifact('docx');
});
$('#worklist').addEventListener('click',(event) => {const button = event.target.closest('[data-open]');if (button) {const id = button.dataset.open;if (location.hash.slice(1) !== encodeURIComponent(id)) location.hash = encodeURIComponent(id);else if (state.current?.id !== id) loadMatter(id);}});
$('#search-sources').addEventListener('click',searchOwnSources);
$('#source-search-results').addEventListener('click',async (event) => {
  if (event.target.closest('[data-source-search-close]')) {state.sourceSearch = null;++state.sourceSearchSerial;renderSourceSearch();return;}
  const button = event.target.closest('[data-source-search-hit]');if (!button || state.busy) return;
  const hit = state.sourceSearch?.items?.[Number(button.dataset.sourceSearchHit)];if (!hit) return;
  const epoch = state.identityEpoch;state.sourceHit = hit;history.replaceState(null,'',`#${encodeURIComponent(hit.matter_id)}`);await loadMatter(hit.matter_id);
  if (epoch === state.identityEpoch && state.current?.id === hit.matter_id) document.getElementById(`source-${hit.source_id}`)?.scrollIntoView({block:'center',behavior:'smooth'});
});
$('#search').addEventListener('input',(event) => {state.search = event.target.value;renderList();});
$('#domain-filter').addEventListener('change',(event) => {state.domainFilter = event.target.value;renderList();});
function showNew() {if (!canCreateMatter()) {render();return;}state.view = 'workspace';detailController?.abort();clearTimeout(pollTimer);++state.selection;state.current = null;state.loading = false;state.error = null;state.difference = null;render();$('#goal')?.focus();}
$('#new-matter').addEventListener('click',() => {history.pushState(null,'',location.pathname + location.search);showNew();});
window.addEventListener('hashchange',() => {let id;try {id = decodeURIComponent(location.hash.slice(1));} catch {id = ''; }if (id) loadMatter(id);else showNew();});
window.addEventListener('beforeunload',(event) => {if ([...(state.serviceFollowupDrafts?.values() || [])].some((draft) => draft.dirty) || [...(state.menuRevisionDrafts?.values() || [])].some((draft) => draft.dirty) || modelJourneyUI.isDirty() || mealSummariesUI.isDirty() || rowReportsUI.isDirty() || expenseJourneyUI.isDirty() || relatedTasksUI.isDirty() || privateContextUI.isDirty() || materialNodesUI.isDirty() || materialRecordsUI.isDirty() || attachmentsUI.isDirty() || remindersUI.isDirty() || tabularUI.isDirty() || modelRowsUI.isDirty() || extractionUI.isDirty() || sourceUI.isDirty() || readingsUI.isDirty() || contextUI.isDirty() || waitingUI.isDirty() || [...(state.menuResponseDrafts?.values() || [])].some((draft) => draft.response_text) || [...(state.shareDrafts?.values() || [])].some((draft) => draft.dirty) || [...(state.checksDrafts?.values() || [])].some((draft) => draft.dirty) || [...(state.journeyDrafts?.values() || [])].some(journeyDirty) || [...(state.structureDrafts?.values() || [])].some((draft) => draft.dirty) || [...(state.structureProgressDrafts?.values() || [])].some((drafts) => [...drafts.values()].some((draft) => draft.note || draft.reported_by || draft.occurred_at)) || [...(state.ownFeedbackDrafts?.values() || [])].some((draft) => draft.dirty) || [...(state.serviceDrafts?.entries() || [])].some(([id,draft]) => {const service = state.serviceCurrent?.id === id ? state.serviceCurrent : state.services?.find((item) => item.id === id);return Object.entries(draft).some(([key,value]) => key === 'result' ? value !== 'unverified' && value !== serviceTrack(service || {},'requester_result') : key === 'executor_id' ? value && value !== service?.executor_id : Boolean(value));}) || state.composer.goal_text || state.composer.reported_by || Object.values(state.accountDraft).some((value) => Array.isArray(value) ? value.length : Boolean(value)) || [...state.feedbackDrafts.values()].some((draft) => Object.values(draft).some(Boolean)) || [...state.drafts.values()].some((draft) => draft.messageText || draft.goalEdit?.dirty || draft.artifactText !== null || Object.keys(draft.factChanges).length || draft.sourceText || draft.publicNotes || Object.values(draft.serviceSubmit || {}).some((value) => value && value !== 'unknown') || draft.rows !== null || Object.entries(draft.evidence).some(([key,value]) => key === 'record_type' ? value !== 'statement' : Boolean(value)))) {event.preventDefault();event.returnValue = '';}});
async function startWorkspace() {
  if (!authenticated()) {state.loading = false;render();return;}
  const epoch = state.identityEpoch;state.loading = true;render();
  try {
    const catalog = await request('/api/catalog');if (epoch !== state.identityEpoch) return;
    state.catalog = catalog.domains || {};$('#domain-filter').innerHTML = domainOptions(state.domainFilter,true);
    if (canCreateMatter()) {await refreshList();if (epoch === state.identityEpoch) remindersUI.read();}
    if (epoch !== state.identityEpoch) return;
    state.loading = false;
    if (state.session?.principal?.is_admin && !canCreateMatter()) {state.view = 'accounts';await refreshAccounts();}
    else if (hasCapability('executor') && !canCreateMatter()) {state.view = 'services';await refreshServices();}
    else {let id;try {id = decodeURIComponent(location.hash.slice(1));} catch {id = ''; }if (id) await loadMatter(id);else render();}
  } catch (error) {if (epoch === state.identityEpoch) {state.loading = false;state.error = {message:error.message || '工作区暂不可读取。',retryable:true,retry:startWorkspace};render();}}
}
async function init() {
  const epoch = state.identityEpoch;
  try {state.session = await request('/api/session');if (epoch !== state.identityEpoch) return;if (authenticated()) await startWorkspace();else {state.loading = false;render();}}
  catch (error) {if (epoch === state.identityEpoch) {state.loading = false;state.error = {message:error.message || '服务暂不可连接。',retryable:true,retry:init};render();}}
}
function openView(view) {if (state.busy || !authenticated()) return;detailController?.abort();clearTimeout(pollTimer);++state.selection;state.current = null;state.error = null;state.difference = null;state.loading = false;state.view = view;history.replaceState(null,'',location.pathname + location.search);render();if (view === 'menus') refreshMenus();else if (view === 'accounts') refreshAccounts();else if (view === 'services') {state.serviceCurrent = null;state.lostServiceId = null;refreshServices();}else if (view === 'shares') {state.receivedShare = null;refreshReceivedShares();}else if (view === 'context') contextUI.read();}
$('#context-nav').addEventListener('click',() => {if (canCreateMatter()) openView('context');});
$('#shares-nav').addEventListener('click',() => {if (multiUser() && ['clerk','cook'].some(hasCapability)) openView('shares');});
$('#menus-nav').addEventListener('click',() => {if (['employee','cook'].some(hasCapability)) openView('menus');});
$('#services-nav').addEventListener('click',() => {if (['employee','clerk','executor'].some(hasCapability)) openView('services');});
$('#accounts-nav').addEventListener('click',() => {if (state.session?.principal?.is_admin) openView('accounts');});
$('#logout').addEventListener('click',() => logout());
window.addEventListener('focus',() => {ensureIdentity().catch(() => {});});
init();

$('#workspace').addEventListener('toggle',event=>materialRecordsUI.toggle(event.target),true);

$('#workspace').addEventListener('toggle',event=>materialNodesUI.toggle(event.target),true);

$('#workspace').addEventListener('toggle',event=>rowReportsUI.toggle(event.target),true);

$('#workspace').addEventListener('toggle',event=>modelJourneyUI.toggle(event.target),true);

$('#workspace').addEventListener('toggle',event=>mealSummariesUI.toggle(event.target),true);

$('#workspace').addEventListener('toggle',event=>expenseJourneyUI.toggle(event.target),true);
