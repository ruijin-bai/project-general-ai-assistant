const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function createRelatedTasks({getState,request,post,render,toast,refreshMatter}) {
  const records=new Map();
  const rec=id=>{if(!records.has(id))records.set(id,{data:null,open:false,loading:false,stamp:null,error:null,drafts:new Map(),notes:new Map(),dirty:new Set()});return records.get(id);};
  async function read(id) {
    const state=getState(),r=rec(id),epoch=state.identityEpoch,selection=state.selection;if(r.loading)return;
    r.loading=true;
    try{const data=await request(`/api/matters/${encodeURIComponent(id)}/related-tasks`);if(epoch!==getState().identityEpoch||selection!==getState().selection||getState().current?.id!==id)return;r.data=data;r.error=null;}
    catch(error){if(epoch===getState().identityEpoch&&selection===getState().selection)r.error=error.message;}
    finally{r.loading=false;if(epoch===getState().identityEpoch&&selection===getState().selection&&getState().current?.id===id)render();}
  }
  function existingMarkup(m,r) {
    const data=r.data?.matches;if(!data)return '';const state=getState(),blocked=state.busy||data.stale;
    const suggestions=data.items.map(item=>{const saved=data.linked.find(link=>link.match_id===item.id&&link.action_id===data.action_id&&!link.withdrawn);
      return `<article class="evidence-record"><h3>可关联的已有事项 · 候选</h3><strong>${esc(item.target.goal_text)}</strong><p>${esc(item.reason)}</p>${item.refs.map(ref=>`<blockquote class="small">本事项原话第${ref.start_line}–${ref.end_line}行：${esc(ref.excerpt)}</blockquote>`).join('')}${saved?'<p class="scope-note">本人已保存这份关联；没有重复新建事项。</p>':`<label class="field-label" for="match-note-${esc(item.id)}">本人核对备注（可选）</label><textarea id="match-note-${esc(item.id)}" data-related-note="${esc(item.id)}" maxlength="2000">${esc(r.notes.get(item.id)||'')}</textarea><button class="button" data-action="related-tasks-match" data-match-id="${esc(item.id)}" ${blocked?'disabled':''}>核对后关联已有事项（不新建）</button>`}<button class="button quiet" data-open="${esc(item.target.matter_id)}">查看这份本人已有事项</button></article>`;}).join('');
    const history=data.linked.map(link=>`<article class="evidence-record"><strong>${esc(link.target.goal_text)}</strong><p>${esc(link.note||link.reason)}</p><p class="${link.stale||link.withdrawn?'dirty-note':'scope-note'}">${link.withdrawn?'本人已解除，历史保留':link.stale?'关联依据已变化，历史／待重核':'本人已核对并保存关联'}；两份事实与实际业务状态分别保留。</p><button class="button quiet" data-open="${esc(link.target.matter_id)}">打开已有事项</button>${!link.withdrawn?`<button class="button quiet" data-action="related-tasks-withdraw-match" data-link-id="${esc(link.link_id)}" ${state.busy?'disabled':''}>解除这份关联（历史保留）</button>`:''}</article>`).join('');
    return `${data.stale?'<p class="dirty-note">被参考标题或许可已变化；保留备注，旧建议停止确认，请重新核对。</p>':''}${suggestions}${history?`<h3>本人已保存的事项关联</h3>${history}`:''}`;
  }
  function markup(m) {
    const latest=m.actions?.filter(a=>a.kind==='prepare_model'&&a.status==='completed').at(-1);
    if(!latest&&!m.related_origin)return '';
    const state=getState(),r=rec(m.id),stamp=`${m.revision_no}:${latest?.id||''}`;
    if(r.stamp!==stamp){r.stamp=stamp;Promise.resolve().then(()=>read(m.id));}
    const data=r.data,stale=data?.stale||data&&data.input_revision!==m.revision_no;
    return `<section class="panel related-tasks-panel"><details class="related-review" ${r.open?'open':''}><summary class="details-toggle">${m.related_origin?'关联来源与独立续办':'助理发现的关联'}${data?` · 已有事项 ${data.matches?.items.length||0}／独立建议 ${data.items.length}${stale||data.matches?.stale?' · 待重核':''}`:' · 正在读取'}</summary><div class="panel-head"><h2>${m.related_origin?'独立事项与原话关联':'助理发现的关联事项'}</h2><button class="button quiet" data-action="related-tasks-read" ${state.busy||r.loading?'disabled':''}>重新读取建议（不调用模型）</button></div>${m.related_origin?`<p class="${m.related_origin.stale?'dirty-note':'scope-note'}">本事项由本人核对关联建议后独立保存，原事项与用餐／费用等实际状态分别办理。${m.related_origin.stale?'原关联依据已变化，新准备暂缓；已有人工稿保留，请回原事项核对。':''}</p><button class="button quiet" data-open="${esc(m.related_origin.matter_id)}">回到原事项核对</button><label class="field-label"><input type="checkbox" data-related-unlink-check> 本人已核对当前副本，解除关联后独立继续</label><button class="button quiet" data-action="related-tasks-unlink" ${state.busy?'disabled':''}>解除当前关联（原话与历史保留）</button>`:'<p class="scope-note">交融依据本事项原话发现的候选；不会按类别自动组合。可忽略，或修正目标后另存独立事项。不自动安排、扣餐或执行；没有读取其他人的事项。</p>'}${r.error?`<p class="dirty-note">${esc(r.error)}</p>`:''}${existingMarkup(m,r)}${stale?'<p class="dirty-note">原话或依据已变化，旧建议仅作历史；保留你的编辑，核对原事项后重新整理。</p>':''}${!data?'<p class="small muted">正在读取已保存建议…</p>':data.items.length?data.items.map(item=>{
      const saved=data.linked.find(link=>link.candidate_id===item.id&&link.action_id===data.action_id),text=r.drafts.get(item.id)??item.goal_text;
      return `<article class="evidence-record"><h3>${esc(state.catalog[item.domain]?.label||item.domain)} · 候选关联</h3><p>${esc(item.reason)}</p>${item.refs.map(ref=>`<blockquote class="small">原话第${ref.start_line}–${ref.end_line}行：${esc(ref.excerpt)}</blockquote>`).join('')}${saved?`<p>已另存：${esc(saved.goal_text)}</p><button class="button" data-open="${esc(saved.matter_id)}">打开独立事项</button>`:`<label class="field-label" for="related-goal-${esc(item.id)}">核对或修正独立事项的目标</label><textarea id="related-goal-${esc(item.id)}" data-related-goal="${esc(item.id)}" maxlength="2000" ${state.busy?'disabled':''}>${esc(text)}</textarea><button class="button" data-action="related-tasks-create" data-candidate-id="${esc(item.id)}" ${state.busy||stale?'disabled':''}>核对后另存独立事项</button>`}</article>`;
    }).join(''):`<p class="small muted">${data.matches?.items.length?'已有事项候选见上方，可核对后继续办理。':'本批没有已保存的关联建议；这不表示不存在关联。可补充原话或直接新建独立事项。'}</p>`}</details></section>`;
  }
  return {markup,input:el=>{if(el.dataset.relatedNote!==undefined&&getState().current){const r=rec(getState().current.id);r.notes.set(el.dataset.relatedNote,el.value);r.dirty.add(el.dataset.relatedNote);return true;}if(el.dataset.relatedGoal===undefined)return false;const m=getState().current;if(!m)return false;const r=rec(m.id);r.drafts.set(el.dataset.relatedGoal,el.value);r.dirty.add(el.dataset.relatedGoal);return true;},action:(name,button)=>{
    if(!name?.startsWith('related-tasks-'))return false;const state=getState(),m=state.current;if(!m)return true;
    if(name==='related-tasks-read'){read(m.id);return true;}
    if(name==='related-tasks-match'){
      const r=rec(m.id),data=r.data?.matches;if(!data||data.stale){toast('被参考事项或许可已变化，请保留备注并重新核对。');return true;}const id=button.dataset.matchId,selection=state.selection;
      post(`/api/matters/${encodeURIComponent(m.id)}/related-tasks/match`,{expected_version:m.version,input_revision:m.revision_no,action_id:data.action_id,match_id:id,note:r.notes.get(id)||''},async()=>{r.dirty.delete(id);toast('已有事项关联已保存，没有重复新建或修改对方。');await refreshMatter(m.id,selection);await read(m.id);});return true;
    }
    if(name==='related-tasks-withdraw-match'){
      const selection=state.selection;post(`/api/matters/${encodeURIComponent(m.id)}/related-tasks/withdraw-match`,{expected_version:m.version,link_id:button.dataset.linkId},async()=>{toast('当前关联已解除；两份事项与人工稿保留。');await refreshMatter(m.id,selection);await read(m.id);});return true;
    }
    if(name==='related-tasks-unlink'){
      const checkbox=document.querySelector('[data-related-unlink-check]');if(!checkbox?.checked){toast('请先本人核对当前副本，并勾选解除关联后的独立继续。');return true;}
      const selection=state.selection;post(`/api/matters/${encodeURIComponent(m.id)}/related-tasks/unlink`,{expected_version:m.version,input_revision:m.revision_no,acknowledged_copy_review:true},async()=>{toast('当前关联已解除，原话与历史保留；实际用餐安排未改变。');await refreshMatter(m.id,selection);await read(m.id);});return true;
    }
    const r=rec(m.id),data=r.data,item=data?.items.find(i=>i.id===button.dataset.candidateId);if(!item||data.stale||data.input_revision!==m.revision_no){toast('先核对当前原话与建议，人工编辑保留。');return true;}
    const goal=(r.drafts.get(item.id)??item.goal_text).trim();if(!goal){toast('请写明这份独立事项的目标。');return true;}
    const selection=state.selection;
    post(`/api/matters/${encodeURIComponent(m.id)}/related-tasks/create`,{expected_version:m.version,input_revision:data.input_revision,action_id:data.action_id,candidate_id:item.id,goal_text:goal},async()=>{r.dirty.delete(item.id);toast('独立事项与可编辑准备稿已保存，原事项保留；没有新增模型调用或真实安排。');await refreshMatter(m.id,selection);await read(m.id);});return true;
  },toggle:el=>{if(el.matches('.related-review')&&el.isConnected&&getState().current)rec(getState().current.id).open=el.open;},clear:()=>records.clear(),isDirty:()=>[...records.values()].some(r=>r.dirty.size)};
}
