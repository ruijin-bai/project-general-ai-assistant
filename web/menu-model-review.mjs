const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const statuses={queued:'已登记，等待准备',running:'交融正在准备',completed:'候选已保存，语义待核',failed:'准备未完成，人工路径可继续',cancelled:'已停止，未继续发布'};
const controls={idle:'可准备',waiting:'等待本人核对',processing:'正在准备',paused:'已暂停',handoff:'已人工接手'};
export function createMenuModelReview({getState,request,post,render,toast,adoptFeedback,getReplyDraft}) {
  const records=new Map();
  const recordFor=id=>{if(!records.has(id))records.set(id,{data:null,selected:new Set(),constraints:false,reading:false,error:null});return records.get(id);};
  const owned=(id,epoch,selection)=>getState().identityEpoch===epoch&&getState().selection===selection&&getState().view==='menus'&&getState().menus.some(menu=>menu.id===id&&menu.publisher_id===getState().session?.principal?.id);
  async function read(id,{fillId=null}={}){
    const record=recordFor(id);if(record.reading)return;
    const epoch=getState().identityEpoch,selection=getState().selection;record.reading=true;record.error=null;render();
    try {
      const [data,feedback]=await Promise.all([request(`/api/menu-publications/${encodeURIComponent(id)}/model-review`),request(`/api/menu-publications/${encodeURIComponent(id)}/feedback`)]);
      if(!owned(id,epoch,selection))return;
      if(data.inputs.length!==feedback.items.length||data.inputs.some(input=>!feedback.items.some(item=>item.id===input.id&&(item.correction_count??0)===input.correction_count)))throw {message:'读取期间共享意见已变化，请重读核对；本人回应保留。'};
      record.data=data;record.selected=new Set([...record.selected].filter(id=>data.inputs.some(item=>item.id===id)));adoptFeedback(id,feedback.items);
      if(fillId){
        const reply=data.review?.replies.find(item=>item.ref.id===fillId),menu=getState().menus.find(menu=>menu.id===id),item=feedback.items.find(item=>item.id===fillId);
        if(data.batch?.stale||!reply||!item||!data.available){toast('意见已更正、撤共享或候选未就绪，停止沿旧依据填入；本人文字保留。');return;}
        const draft=getReplyDraft(menu,item);
        if(draft.response_text.trim()){toast('已保留现有人工回应；请直接在原编辑框修改。');return;}
        if(draft.expected_publication_version!==data.publication_version||draft.feedback_correction_count!==reply.ref.correction_count){toast('原回应基版已变化，先读当前意见与差异；已有文字保留。');return;}
        draft.response_text=reply.text;toast('候选已填入空白回应框；请核对和编辑，明确保存后才成为厨师本人文字声明。');
      }
    }catch(error){if(owned(id,epoch,selection)){record.error=error.message||'候选暂未读回，人工路径保留。';record.data=null;if([401,403,404].includes(error.status))adoptFeedback(id,[]);}}
    finally{if(owned(id,epoch,selection)){record.reading=false;render();}}
  }
  function start(id){
    const record=recordFor(id),data=record.data;if(!data||!record.selected.size)return;
    const epoch=getState().identityEpoch,selection=getState().selection;
    post(`/api/menu-publications/${encodeURIComponent(id)}/model-review`,{expected_version:data.expected_version,publication_version:data.publication_version,basis_token:data.basis_token,selected_ids:[...record.selected],include_constraints:record.constraints},async()=>{if(!owned(id,epoch,selection))return;toast('已登记本次明确选择；不会自动回应或改菜单，读取状态查看候选。');await read(id);});
  }
  function markup(menu,items){
    const record=recordFor(menu.id),data=record.data,disabled=getState().busy||record.reading?'disabled':'',inputs=data?.inputs||[],current=!data||inputs.length===items.length&&inputs.every(input=>items.some(item=>item.id===input.id&&(item.correction_count??0)===input.correction_count));
    const stopped=data&&['paused','handoff','processing'].includes(data.assistant_status),batch=data?.batch,review=current&&menu.status==='published'&&!batch?.stale?data?.review:null;
    const refText=ref=>{const item=items.find(item=>item.id===ref.id);return item?`原话：${item.feedback_text}（更正编号 ${ref.correction_count}）`:'原话当前不可读';};
    return `<section class="evidence-record"><h3>所选意见的模型归纳与回应候选</h3><p class="scope-note">明确选择后才调用真实交融；默认不选意见、不发送饮食约束。只发所选原话、相关菜品和公开菜单日期／餐次／菜品，不发账号身份、私人事项或来源。已发送内容无法从模型端召回；本地撤共享或更正后旧候选停止使用。仍须逐条核对少数意见与安全。</p><button class="button quiet" data-action="menu-model-read" data-menu-id="${esc(menu.id)}" ${disabled}>读取选择依据与已保存候选（不调用）</button>${record.error?`<p class="dirty-note">${esc(record.error)}</p>`:''}${data?`<p class="scope-note">原菜单准备事项：${esc(controls[data.assistant_status]||data.assistant_status)}；${stopped?'请在原菜单事项恢复或等待当前准备结束，人工回应可继续。':''}</p>${!current?'<p class="dirty-note">意见已变化，请重读；旧候选停止显示，原人工文字保留。</p>':''}${items.map(item=>`<label class="field-label"><input type="checkbox" data-menu-model-select="${esc(item.id)}" data-menu-id="${esc(menu.id)}" ${record.selected.has(item.id)?'checked':''} ${disabled}> 选择原话：${esc(item.feedback_text)}${item.dish?` · 菜品：${esc(item.dish)}`:''}</label>`).join('')}<label class="field-label"><input type="checkbox" data-menu-model-constraints="${esc(menu.id)}" ${record.constraints?'checked':''} ${disabled}> 本次明确发送所选意见中的过敏／忌口原话给交融（可选）</label><p class="scope-note">最多8条；未发送约束不表示无过敏。原有个体约束继续在下方单独显示，不能由多数意见覆盖。</p><button class="button" data-action="menu-model-start" data-menu-id="${esc(menu.id)}" ${disabled||stopped||!current||!data.available||menu.status!=='published'||!record.selected.size||!getState().session?.model_configured?'disabled':''}>用真实交融归纳本次所选意见</button>${batch?`<p class="${batch.stale?'dirty-note':'scope-note'}">${esc(statuses[batch.status]||batch.status)}${batch.stale?' · 意见或菜单依据已变化，旧批次不可继续使用。':` · 本批 ${esc(batch.selected_count)} 条／${esc(batch.contributor_count)} 个不同提交账号；不代表全员。${batch.include_constraints?'本批明确发送了约束。':'本批未发送约束，安全须另核。'}`}${batch.error?` · ${esc(batch.error)}`:''}</p>`:''}${review?`<p class="scope-note">${esc(review.content)} · ${esc(review.model)}；下面均为候选，程序只核引用与覆盖，语义仍待核。</p>${review.themes.map(theme=>`<article class="evidence-record"><strong>候选观点：${esc(theme.title)}</strong><p>${esc(theme.text)}</p>${theme.refs.map(ref=>`<p class="small muted">${esc(refText(ref))}</p>`).join('')}</article>`).join('')}${review.replies.map(reply=>`<article class="evidence-record"><strong>待核回应候选</strong><p class="small muted">${esc(refText(reply.ref))}</p><p>${esc(reply.text)}</p><button class="button quiet" data-action="menu-model-fill" data-menu-id="${esc(menu.id)}" data-feedback-id="${esc(reply.ref.id)}" ${disabled}>核对后填入这条意见的空白回应框</button></article>`).join('')}`:''}`:''}</section>`;
  }
  return {markup,clear:()=>records.clear(),invalidate:(id,items)=>{const record=records.get(id),data=record?.data;if(data&&(data.inputs.length!==items.length||data.inputs.some(input=>!items.some(item=>item.id===input.id&&(item.correction_count??0)===input.correction_count)))){record.data=null;record.selected.clear();}},
    input:element=>{if(element.dataset.menuModelSelect!==undefined){const record=recordFor(element.dataset.menuId);element.checked?record.selected.add(element.dataset.menuModelSelect):record.selected.delete(element.dataset.menuModelSelect);render();return true;}if(element.dataset.menuModelConstraints!==undefined){recordFor(element.dataset.menuModelConstraints).constraints=element.checked;return true;}return false;},
    action:(action,button)=>{if(action==='menu-model-read'){read(button.dataset.menuId);return true;}if(action==='menu-model-start'){start(button.dataset.menuId);return true;}if(action==='menu-model-fill'){read(button.dataset.menuId,{fillId:button.dataset.feedbackId});return true;}return false;}};
}
