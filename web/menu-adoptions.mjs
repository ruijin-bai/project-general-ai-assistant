const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function adoptionSelectionMarkup(menu,items,draft,disabled){
  const selected=draft.adoptions||{};
  const orphaned=Object.entries(selected).filter(([id,value])=>value.chosen&&!items.some(item=>item.id===id));
  return `<fieldset><legend>可选：本人明确在这次新版菜单采用的意见</legend><p class="scope-note">不强制回应或采用任何意见。默认不选；选择后写明新版菜单对应做法，随本人明确发布一次保存。声明只给这条意见的员工及对应厨师看，公开菜品／备注请直接填写，不自动带入意见、身份或过敏资料；实际备餐、供餐和安全另核。</p>${items.map(item=>{const value=selected[item.id];return `<article class="evidence-record"><label class="field-label"><input type="checkbox" data-adopt-feedback="${esc(item.id)}" ${value?.chosen?'checked':''} ${disabled}> 本人在新版采用这条意见：${esc(item.feedback_text)}</label>${item.dietary_constraint?`<p class="dirty-note">原明确约束仍须单独核对：${esc(item.dietary_constraint)}</p>`:''}${value?.chosen?`<label class="field-label">这条意见在新版的本人采用说明（仅对应员工和厨师）<textarea data-adopt-note="${esc(item.id)}" maxlength="3000" required ${disabled}>${esc(value.note)}</textarea></label><p class="scope-note">声明基于意见更正编号 ${esc(value.feedback_correction_count)}，当前已读 ${esc(item.correction_count??0)}。${value.feedback_correction_count!==(item.correction_count??0)?'意见已变；核对后重新勾选当前原话，原说明保留。':''}</p>`:''}</article>`;}).join('')||'<p class="small muted">尚未读取本菜单当前共享意见；可先读取意见，或直接修订而不登记采用。</p>'}${orphaned.map(([id,value])=>`<p class="dirty-note">所选共享意见当前不可读，不能继续按旧范围发布。本人尚未保存说明：${esc(value.note)}</p><label class="field-label"><input type="checkbox" data-adopt-feedback="${esc(id)}" checked ${disabled}> 保留这条当前不可读意见的选择（取消后可继续普通修订）</label>`).join('')}</fieldset>`;
}
export function adoptionInput(element,items,draft){
  if(element.dataset.adoptFeedback===undefined&&element.dataset.adoptNote===undefined)return false;
  draft.adoptions||={};
  if(element.dataset.adoptFeedback!==undefined){
    const id=element.dataset.adoptFeedback,item=items.find(item=>item.id===id),old=draft.adoptions[id];
    if(element.checked&&item)draft.adoptions[id]={chosen:true,feedback_correction_count:item.correction_count??0,note:old?.note||''};
    else if(old)old.chosen=false;
  }else{const value=draft.adoptions[element.dataset.adoptNote];if(value)value.note=element.value;}
  draft.dirty=true;return true;
}
export function adoptionPayload(draft){return Object.entries(draft.adoptions||{}).filter(([,value])=>value.chosen).map(([feedback_id,value])=>({feedback_id,feedback_correction_count:value.feedback_correction_count,note:value.note.trim()}));}
export function adoptionHistoryMarkup(records=[]){
  if(!records.length)return '';
  return `<div class="cook-adoption-history"><h3>厨师在新版菜单的本人采用声明</h3>${records.map(record=>{const menu=record.target_menu_snapshot;return `<article class="evidence-record"><strong>${esc(record.cook_display_name)} · ${record.old_feedback_scope?'旧意见范围的历史采用声明':'针对当前意见范围的采用声明'}</strong><p>${esc(record.note)}</p><p class="small">对应已保存菜单：${esc(menu.menu_date)}／${esc(menu.meal_slot)}／版本 ${esc(menu.version)}${menu.status==='withdrawn'?'（已被修订或撤回，固定版本保留）':''}</p><p>该版本公开菜品：${esc(menu.dishes)}</p><p class="scope-note">原意见版本 ${esc(record.publication_version)}／更正编号 ${esc(record.feedback_correction_count)} · ${esc(record.recorded_at)}<br>${esc(record.scope)}</p></article>`;}).join('')}</div>`;
}
