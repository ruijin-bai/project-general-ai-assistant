const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={destination:'目的地',departure_at:'计划离营',return_at:'计划返营',participants:'必要同行引用',purpose:'用途',meal_request:'餐次原述需求'};
export function createModelJourney({getState,request,render,toast,preserveArtifact,adoptRead,getPlan}) {
  const records=new Map(),current=()=>getState().current;
  const recordFor=id=>{if(!records.has(id))records.set(id,{data:null,selected:new Set(),reading:false,error:null,open:false});return records.get(id);};
  const owned=(id,epoch,selection)=>current()?.id===id&&getState().identityEpoch===epoch&&getState().selection===selection;
  async function read(){
    const matter=current();if(matter?.domain!=='travel')return;
    const record=recordFor(matter.id);if(record.reading)return;
    const epoch=getState().identityEpoch,selection=getState().selection;record.reading=true;record.error=null;record.open=true;render();
    try{
      const [data,fresh,journey]=await Promise.all([request(`/api/matters/${encodeURIComponent(matter.id)}/travel/model-candidates`),request(`/api/matters/${encodeURIComponent(matter.id)}`),request(`/api/matters/${encodeURIComponent(matter.id)}/journey`)]);
      if(!owned(matter.id,epoch,selection))return;
      if(data.version!==fresh.version||data.revision_no!==fresh.revision_no||journey.version!==fresh.version)throw {message:'读取期间行程已变化，请重读，人工输入保留。'};
      preserveArtifact();adoptRead(fresh,journey);record.data=data;record.selected=new Set([...record.selected].filter(id=>data.items.some(item=>item.id===id)));
    }catch(error){if(owned(matter.id,epoch,selection))record.error=error.message||'计划候选暂未读回，原稿保留。';}
    finally{if(owned(matter.id,epoch,selection)){record.reading=false;render();}}
  }
  function append(){
    const matter=current(),record=recordFor(matter.id),data=record.data,editor=getPlan();
    if(!data||!record.selected.size){toast('请明确选择要核对的计划候选，默认不选入。');return;}
    if(data.stale||data.version!==matter.version||data.revision_no!==matter.revision_no||editor?.baseVersion!==data.version||editor?.baseRevision!==data.revision_no){toast('行程或基版已变化；先明确读取计划与差异后核对，人工输入保留。');return;}
    let added=0,kept=0;
    for(const item of data.items.filter(item=>record.selected.has(item.id))){
      const field=editor.fields[item.field];
      if(!field||field.value?.trim()||field.status!=='unknown'||matter.facts?.[item.field]?.status==='confirmed'){kept++;continue;}
      editor.fields[item.field]={value:item.value,status:'candidate'};added++;
    }
    if(added){editor.dirty=true;record.selected.clear();}
    toast(`已填入 ${added} 个空白计划草稿字段，仍为候选；保留 ${kept} 个已有人工／已核值，需修改请沿计划直接编辑。`);render();
  }
  function markup(matter){
    if(matter.domain!=='travel')return '';
    const record=recordFor(matter.id),data=record.data,disabled=getState().busy||record.reading?'disabled':'';
    return `<section class="panel"><details id="model-journey-details" ${record.open?'open':''}><summary class="details-toggle">从原话读取已保存模型出行计划</summary><p class="scope-note">明确选择模型整理后再读取，此入口不再调用。候选先填空白计划草稿，不覆盖已有人工或已核值；本人核对营地出返语义后保存，日期比较沿程序，未知不补时区或实际出返。</p><button class="button" data-action="model-journey-read" ${disabled}>读取已保存模型出行计划（不再调用）</button>${record.error?`<p class="dirty-note">${esc(record.error)}</p>`:''}${data?`<p class="scope-note">${esc(data.scope)} · 批次修订 ${esc(data.basis_revision??'尚无')}／当前 ${esc(matter.revision_no)} · 保存基版 ${esc(data.version)}</p>${data.stale||data.version!==matter.version?'<p class="dirty-note">候选依据或事项基版已变化，请先核差异；旧候选与人工值保留。</p>':''}${data.items.map(item=>`<article class="evidence-record"><label><input type="checkbox" data-model-journey="${esc(item.id)}" ${record.selected.has(item.id)?'checked':''} ${disabled}> 选入：${esc(labels[item.field]||item.field)} · ${esc(item.value)}</label>${item.refs.map(ref=>`<p class="small muted">来源 ${esc(ref.source_id)} 第${ref.start_line}–${ref.end_line}行</p><pre>${esc(ref.excerpt)}</pre>`).join('')}</article>`).join('')||'<p class="small muted">没有可引用计划候选；相对时间等缺项保持待核，可沿计划人工填实际信息。</p>'}<button class="button" data-action="model-journey-append" ${disabled||data.stale||data.version!==matter.version?'disabled':''}>把所选候选填入空白计划草稿</button>`:''}</details></section>`;
  }
  return {markup,input:element=>{if(element.dataset.modelJourney===undefined||current()?.domain!=='travel')return false;const record=recordFor(current().id);element.checked?record.selected.add(element.dataset.modelJourney):record.selected.delete(element.dataset.modelJourney);return true;},
    action:name=>{if(name==='model-journey-read'){read();return true;}if(name==='model-journey-append'){append();return true;}return false;},
    toggle:element=>{if(element.isConnected&&element.id==='model-journey-details'&&current())recordFor(current().id).open=element.open;},
    clear:()=>records.clear(),isDirty:()=>[...records.values()].some(record=>record.selected.size)};
}
