const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Use only the current, private cook projection. Exact statements are never removed.
export function reviewFeedback(items){
  const groups=new Map(),duplicates=new Map(),constraints=[];
  for(const item of items){
    const dish=item.dish||'';if(!groups.has(dish))groups.set(dish,{dish,items:[],accounts:new Set(),responded:0,texts:new Set()});
    const group=groups.get(dish);group.items.push(item);group.accounts.add(item.contributor_id);
    group.responded+=(item.responses||[]).some(response=>!response.old_feedback_scope&&response.feedback_correction_count===item.correction_count)?1:0;
    const signature=JSON.stringify([dish,item.feedback_text,item.dietary_constraint||'']);group.texts.add(signature);
    if(!duplicates.has(signature))duplicates.set(signature,[]);duplicates.get(signature).push(item);
    if(item.dietary_constraint)constraints.push(item);
  }
  const values=[...groups.values()].map(group=>({dish:group.dish,count:group.items.length,accounts:group.accounts.size,responded:group.responded,textGroups:group.texts.size}));
  return {count:items.length,accounts:new Set(items.map(item=>item.contributor_id)).size,responded:values.reduce((n,group)=>n+group.responded,0),groups:values,
    duplicates:[...duplicates.values()].filter(rows=>rows.length>1).map(rows=>({rows,accounts:new Set(rows.map(item=>item.contributor_id)).size})),constraints};
}
export function feedbackReviewMarkup(menu,items){
  const review=reviewFeedback(items);
  return `<div class="menu-feedback-review"><h3>本次已读自愿意见核对</h3><p class="small"><strong>${esc(menu.menu_date)}／${esc(menu.meal_slot)}／所看版本 ${esc(menu.version)}</strong> · ${review.count} 条意见 · ${review.accounts} 个提交账号 · 当前意见范围有文字回应 ${review.responded} 条</p><p class="scope-note">仅当前厨师可读的未撤共享意见；账号数不是全员人数，无应报基线，不核未反馈人员。逐字分组供查找，保留每条原话；语义分歧须核原话，不推断赞成、多数、采用或供餐。下方原意见与回应仍可逐条操作。</p>${review.groups.length?`<div class="table-scroll"><table class="rows-table"><thead><tr><th>原述菜品</th><th>意见条数</th><th>提交账号</th><th>当前文字回应</th><th>逐字不同原述</th></tr></thead><tbody>${review.groups.map(group=>`<tr><td>${esc(group.dish||'未指定菜品，不推归属')}</td><td>${group.count}</td><td>${group.accounts}</td><td>${group.responded}</td><td>${group.textGroups}组</td></tr>`).join('')}</tbody></table></div>`:'<p class="small muted">尚无当前可读自愿意见，不能解释为无意见。</p>'}${review.duplicates.length?`<details><summary class="details-toggle">同菜品、原话及约束逐字相同 ${review.duplicates.length} 组（原记录不去重）</summary>${review.duplicates.map(group=>`<article class="evidence-record"><strong>${esc(group.rows[0].dish||'未指定菜品')} · ${group.rows.length}条／${group.accounts}个账号</strong><p>${esc(group.rows[0].feedback_text)}</p><p class="small">${group.rows.map(item=>`${esc(item.submitted_by||'提交者待核')} · 意见更正编号${item.correction_count}`).join('；')}</p>${group.rows[0].dietary_constraint?`<p class="small">原约束：${esc(group.rows[0].dietary_constraint)}</p>`:''}</article>`).join('')}</details>`:''}${review.constraints.length?`<div class="evidence-record"><h4>逐条单独保留的本人饮食约束 ${review.constraints.length} 条</h4><p class="scope-note">仅对应厨师可读，不以重复或多数覆盖，不证明厨房已保障安全。</p>${review.constraints.map(item=>`<p><strong>${esc(item.submitted_by||'提交者待核')}／${esc(item.dish||'菜品未指定')}：</strong>${esc(item.dietary_constraint)}</p>`).join('')}</div>`:''}</div>`;
}
