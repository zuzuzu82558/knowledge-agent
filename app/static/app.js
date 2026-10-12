const $ = id => document.getElementById(id);
const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const states = {ready:'已就绪', completed:'已完成', queued:'等待处理', running:'正在处理', failed:'失败', interrupted:'已中断，可重试', pending:'等待处理', building:'正在建立', parse:'解析文档', embedding:'准备检索', index:'建立索引', offline_extracts:'原文摘录（离线）', answered:'已回答', refused:'没有足够证据', summarized:'全文洞察已完成', partial_summary:'全文洞察部分完成', not_built:'尚未构建', partial:'部分完成', not_in_m1:'尚未构建', generation_failed:'回答生成失败，已保留证据', retrieval_failed:'检索失败', request_timeout:'处理超时', dense_unavailable:'当前模式不支持纯向量检索', context_budget_exceeded:'证据超出上下文预算'};
const label = value => states[value] || value || '未开始';
states.agent_stopped='Agent 已停止，保留已取得的证据';
function errorText(code) {
  if (/^read_page_failed:/.test(code)) return '部分网页正文未能读取，已跳过这些网页；其他可用证据仍会保留。具体页面和原因见“查看来源”。';
  if (/non_public_address_denied/.test(code)) return '该网址解析到非公网地址，出于安全限制未读取正文。';
  if (code.includes('excerpt 记录')) return '部分正文过长，本次使用相关摘录；可在来源中核对完整资料。';
  if (/parse_failed|no_text/.test(code)) return '文件无法解析。请检查文件是否损坏、是否含可读取的文字，修正后重新上传；重复重试同一个坏文件通常无效。';
  if (/Failed to fetch|NetworkError/.test(code)) return '连接中断，请确认本机服务已启动，再点击“刷新状态”。';
  if (/offline_has|dense_unavailable|rerank_not_configured/.test(code)) return '当前离线模式不提供此能力。可以继续使用原文检索；模型能力需要先配置服务。';
  if (/network_disabled_offline/.test(code)) return '离线模式不发起联网搜索，请切换“我的资料”检索文档。';
  if (/search_not_configured/.test(code)) return '搜索服务尚未配置；本次保留可用的文档证据。';
  if (/graph_not_complete|graph_failed/.test(code)) return '图谱尚不可用，本次已改用文档检索。配置模型后可构建或重试图谱。';
  if (/evidence_deleted|run_missing|run_scope_deleted|document_deleted/.test(code)) return '该记录的来源已删除或不可用，请选择其他文档重新提问。';
  if (/too_many_chunks/.test(code)) return '文档内容超过处理上限。请拆分文档后重新上传，或联系维护者调整上限。';
  if (/document_not_ready|job_already_running/.test(code)) return '文档正在处理或尚未就绪，请等待任务完成。';
  if (/upload_too_large|request_too_large/.test(code)) return '上传文件超过大小上限，请缩小文件后重试。';
  if (/empty_file/.test(code)) return '文件为空，请选择包含正文的文件。';
  if (/deadline|timeout/i.test(code)) return '处理超时。请稍后重试，或缩小文档范围。';
  if (/embedding_failed/.test(code)) return '向量服务暂不可用，本次使用词项检索，已保留可用证据。';
  if (/fallback_model_used/.test(code)) return '主模型暂不可用，本次使用备用模型，未缓存该结果。';
  return /[\u4e00-\u9fff]/.test(code) ? code : '操作未完成，请重试或检查服务配置。详细原因：' + code;
}
async function api(path, options={}) { const r=await fetch(path,options); const data=await r.json(); if(!r.ok) throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail)); return data; }
function notice(text) { $('notice').textContent=text; }
function report(e) { notice(errorText(e.message)); if($('uploadDialog').open) $('uploadStatus').textContent=errorText(e.message); }
function readSession(key, fallback) { try { return JSON.parse(sessionStorage.getItem(key)) ?? fallback; } catch { return fallback; } }
function saveSession(key, value) { try { sessionStorage.setItem(key,JSON.stringify(value)); } catch {} }
let selected=new Set(readSession('selected-documents',[])), documents=new Map(), current=null, mode='offline', refreshSerial=0, uploadLimit=20, uploadBusy=false, resultEpoch=0;
const documentNodes=new Map(), jobNodes=new Map(), historyNodes=new Map();
function saveSelected() { saveSession('selected-documents',[...selected]); updateSelection(); }
function clearResult(message) { resultEpoch++;current=null; saveSession('last-run',null); history.replaceState(null,'',location.pathname); $('answer').replaceChildren(el('p',message,'empty')); $('evidence').replaceChildren(); $('retrievalDebug')?.remove();$('trace').textContent=''; $('resultActions').hidden=true;$('runDetails').hidden=true;$('showEvidence').hidden=true;$('evidencePanel').hidden=true;$('clearCurrent').hidden=true;$('toggleExcerpts').hidden=true;highlightHistory(); }
function locationText(locations) {
  return (locations||[]).map(l=>[
    l.page&&`第 ${l.page} 页`,l.slide&&`第 ${l.slide} 张幻灯片`,l.sheet&&`工作表“${l.sheet}”`,
    l.paragraph&&`第 ${l.paragraph} 个正文项`,l.table&&`表 ${l.table}`,l.row&&`第 ${l.row} 行`,
    l.line_start&&`文本行 ${l.line_start}${l.line_end&&l.line_end!==l.line_start?'–'+l.line_end:''}`,
    l.element&&`正文位置 ${l.element}`].filter(Boolean).join(' · ')).join('；');
}
function openEvidence(id) { $('evidencePanel').hidden=false; const target=id?$('e-'+id):$('closeEvidence');if(target){target.tabIndex=-1;target.focus();target.scrollIntoView({block:'nearest'});} }
function citationLinks(target, ids) { for(const id of ids||[]) { const index=(current?.evidence||[]).findIndex(e=>e.id===id);const a=el('a',`[${index>=0?index+1:'来源'}]`,'citation'); a.href='#e-'+id;a.onclick=e=>{e.preventDefault();openEvidence(id);};target.append(a,document.createTextNode(' ')); } }
function updateResultVersion() {
  if(!current || !$('resultVersion')) return;
  const stale=current.snapshot.documents.some(id=>!current.snapshot.generations.includes(documents.get(id)?.active));
  $('resultVersion').hidden=!stale;
}
function renderResult(r) {
  current=r;$('clearCurrent').hidden=false;saveSession('last-run',r.run_id);history.replaceState(null,'','#run='+encodeURIComponent(r.run_id));
  $('answer').replaceChildren(el('p',r.question,'question-title'),el('h2',label(r.status)));$('resultActions').hidden=false;$('runDetails').hidden=false;$('showEvidence').hidden=!(r.evidence||[]).length&&!(r.online?.pages||[]).length;$('showEvidence').textContent=`查看来源 · ${(r.evidence||[]).length}`;$('evidencePanel').hidden=true;
  const names=r.snapshot.documents.map(id=>documents.get(id)?.name||r.document_names?.[id]||'历史文档');
  $('answer').append(el('p','本次范围：'+(r.scope==='web'?'联网搜索':names.join('、')+(r.scope==='combined'?' + 联网':'')),'location'));
  const versionNote=el('p','这是历史版本的结果。当前文档已更新，重新提问可使用最新版本。');versionNote.id='resultVersion';$('answer').append(versionNote);updateResultVersion();
  for(const c of r.claims||[]) { const p=el('p',c.text+' ');citationLinks(p,c.citations);$('answer').append(p); }
  if(r.coverage) $('answer').append(el('p',`已解析章节访问 ${r.coverage.complete_sections}/${r.coverage.total_sections} · 已解析原文块 ${r.coverage.visited_chunks}/${r.coverage.total_chunks}。此比例不代表源文件内容已完整提取，请同时查看解析质量。`));
  if(r.insights) {
    for(const [key,title] of [['core_viewpoints','核心观点'],['risk_points','风险点'],['key_data','关键数据']]) {
      if(!r.insights[key]?.length) continue;
      $('answer').append(el('h3',title));
      for(const item of r.insights[key]) { const p=el('p',item.text+(item.basis?`（${item.basis==='explicit'?'原文明示':'分析推断'}）`:'')+' ');citationLinks(p,item.citations);$('answer').append(p); }
    }
    if(r.insights.status!=='ready') $('answer').append(el('p',r.mode==='offline'?'离线模式展示原文与覆盖信息，不生成全文结论。':'全文结论尚未完整生成，已保留章节结果；可重新执行全文洞察。'));
  }
  if(r.mode==='offline' && (r.evidence||[]).length) {
    $('answer').append(el('p','当前展示原文摘录，未使用模型生成回答。点击摘录可展开核对。','location'));
    for(const [index,ev] of r.evidence.entries()){
      const record=el('details',undefined,'excerpt-record'),summary=el('summary'),header=el('span',undefined,'excerpt-heading');
      const name=ev.source_type==='web'?'网页 · '+ev.title:ev.document_name||documents.get(ev.document)?.name||r.document_names?.[ev.document]||'历史文档';
      const title=el('span',`[${index+1}] ${name}`,'excerpt-name');title.title=name;
      const action=el('span','展开原文','excerpt-toggle');header.append(title,action);
      const preview=el('span',ev.text.slice(0,280)+(ev.text.length>280?'…':''),'excerpt-preview');
      summary.append(header,preview);
      const location=el('p',[ev.headings.join(' / '),locationText(ev.locations)].filter(Boolean).join(' · '),'location');
      const quote=el('pre',ev.text+' ','excerpt');citationLinks(quote,[ev.id]);record.append(summary,location,quote);
      record.addEventListener('toggle',()=>{action.textContent=record.open?'收起原文':'展开原文';updateExcerptToggle();});
      $('answer').append(record);
    }
  }
  updateExcerptToggle();
  if(!(r.evidence||[]).length && !(r.claims||[]).length) $('answer').append(el('p',r.status==='context_budget_exceeded'?'已取得资料，但当前证据预算无法容纳，详情见检索调试。':r.scope==='web'?'没有取得可用于回答的网页正文。请查看搜索或读取失败原因。':r.scope==='combined'?'所选文档和已读取网页中没有足够依据。请修改问题或调整资料范围。':'所选文档中未找到足够依据。请修改问题或调整文档范围。'));
  if(r.status==='generation_failed') {
    const reasons={answer_invalid_json:'模型没有返回可解析的 JSON。',answer_contract:'模型返回的答案结构不符合约定。',invalid_citation:'模型返回了缺失或不属于本次证据的引用。',answer_truncated:'模型输出达到长度上限，答案未完整生成。',generation_request_failed:'生成请求失败，详情见运行记录。'};
    $('answer').append(el('p',(reasons[r.generation_error?.code]||'这条历史记录仅保存了错误类型，无法确定具体的生成失败原因。')+' 已保留证据，未显示未经校验的回答。'));
  }
  if(r.status==='refused' && (r.evidence||[]).length) $('answer').append(el('p','已读取资料，但模型判断现有证据不足以回答该问题。可查看来源后调整问题。'));
  const shownWarnings=new Set();
  for(const w of r.warnings||[]) { if(r.mode==='offline' && w.includes('离线词项检索')) continue; if(r.status==='generation_failed'&&w.includes('保留证据，不返回模拟回答')) continue; const message=errorText(w);if(shownWarnings.has(message))continue;shownWarnings.add(message);$('answer').append(el('p',message)); }
  if(r.graph_paths?.length) { const details=el('details');details.append(el('summary','关系路径及逐步来源'));for(const path of r.graph_paths) { const list=el('ol');for(const f of path) { const item=el('li',`${f.subject.name} → ${f.relation} → ${f.object.name}：${f.quote} `);const a=el('a','核对原文');a.href='/evidence/'+f.chunk;a.target='_blank';a.rel='noopener';item.append(a);list.append(item); }details.append(list); }$('answer').append(details); }
  $('evidence').replaceChildren();
  for(const ev of r.evidence||[]) { const card=el('div',undefined,'evidence');card.id='e-'+ev.id;
    const source=ev.source_type==='web'?`网页 · ${ev.title} · 获取 ${ev.fetched_at} · 发布 ${ev.published_at||'未知'}`:`文档 · ${ev.document_name||documents.get(ev.document)?.name||r.document_names?.[ev.document]||'历史文档'} · 版本 ${ev.version.slice(0,8)}`;
    card.append(el('div',source,'location'),el('div',[ev.headings.join(' / '),locationText(ev.locations)].filter(Boolean).join(' · '),'location'),el('pre',ev.text));
    if(ev.source_type==='web') {
      const site=el('a','打开当前网页（内容可能已变化）');site.href=ev.url;site.target='_blank';site.rel='noopener noreferrer';
      const save=el('button','保存这份快照到我的资料','secondary');save.type='button';save.onclick=async()=>{save.disabled=true;try{await api('/api/web-evidence/'+encodeURIComponent(ev.id)+'/save',{method:'POST'});notice('快照已保存，正在建立文档索引。');await refresh();}catch(e){report(e);save.disabled=false;}};
      card.append(site,save);if(ev.truncated)card.append(el('p','正文快照因长度上限被截断。','location'));
    }
    if(ev.excerpt) card.append(el('p',`显示原块字符 ${ev.excerpt.start}–${ev.excerpt.end} 的摘录，详情可核对完整原块。`,'location'));
    const a=el('a',ev.source_type==='web'?'核对当时的正文快照':'查看此版本的原文记录');a.href='/evidence/'+ev.id;a.target='_blank';a.rel='noopener';card.append(a);$('evidence').append(card);
  }
  for(const page of r.online?.pages||[])if(page.read_status!=='body_read') {
    const card=el('div',undefined,'evidence');card.append(el('h3','网页正文未读取 · '+page.title),el('p',page.url),el('p','以下仅为搜索发现摘要，未经原文核对，未用于回答：'),el('p',page.snippet||''),el('p',errorText(page.error||page.read_status)));$('evidence').append(card);
  }
  renderDebug(r);
  highlightHistory();$('trace').textContent=JSON.stringify(r,null,2);notice(`已完成 · ${r.elapsed_ms} ms${r.cache?.status==='exact'?' · 复用已保存答案':r.cache?.status==='semantic'?' · 复用相同条件答案':''}`);
}
async function loadRun(id) { const epoch=++resultEpoch;try { const r=await api('/api/runs/'+encodeURIComponent(id));if(epoch!==resultEpoch)return;$('question').value=r.question||'';renderResult(r);toggleLibrary(false); } catch(e) { if(epoch!==resultEpoch)return;clearResult(errorText(e.message));report(e); } }
function newDocumentNode(id) {
  const row=el('div',undefined,'document'),name=el('label'),check=el('input'),title=el('span'),state=el('div'),task=el('div',undefined,'job');
  check.type='checkbox';check.name='doc';check.value=id;check.onchange=()=>{if(check.checked&&selected.size>=30){check.checked=false;notice('一次最多选择 30 篇文档。');return;}check.checked?selected.add(id):selected.delete(id);saveSelected();};name.append(check,title);row.append(name,state,task);
  const menu=el('details',undefined,'document-menu'),toggle=el('summary','⋯');toggle.setAttribute('aria-label','文档更多操作');const items=el('div',undefined,'menu-items');menu.append(toggle,items);row.append(menu);const buttons={};
  for(const [action,text] of [['quality','版本 / 解析质量'],['rebuild','重建索引'],['graph','构建 / 重试图谱'],['remove','删除']]) { const b=el('button',text,'secondary');b.type='button';buttons[action]=b;items.append(b); }
  buttons.quality.onclick=async()=>{try { const rows=await api(`/api/documents/${id}/versions`);$('versions').replaceChildren(el('h2',documents.get(id).name+' · 版本与解析质量'));for(const v of rows) { const box=el('div',undefined,'evidence');box.append(el('h3',`${v.active?'当前使用':'保留版本'} · ${v.version_id.slice(0,8)} ${v.suffix||''}`),el('p',`索引：${label(v.vector_status)} · 图谱：${label(v.graph_status)} · 全文：${label(v.summary_status)}`));if(v.quality) { for(const w of v.quality.warnings) box.append(el('p',w));box.append(el('p','抽取统计：'+Object.entries(v.quality.coverage).map(([k,value])=>`${({pages:'物理页',text_pages:'含文字页',lines:'文本行',sheets:'工作表',slides:'幻灯片',body_items:'正文项',elements:'正文块'})[k]||k} ${value}`).join('，'))); }const a=el('a','下载此版本原文件');a.href='/api/versions/'+v.version_id+'/file';box.append(a);$('versions').append(box); }$('versionsPanel').hidden=false;$('versionsPanel').scrollIntoView({block:'nearest'}); }catch(e){report(e);} };
  buttons.rebuild.onclick=async()=>{buttons.rebuild.dataset.busy='true';buttons.rebuild.disabled=true;notice('正在安排重建，原有可用版本继续保留。');try{await api(`/api/documents/${id}/rebuild`,{method:'POST'});await refresh();}catch(e){report(e);}finally{delete buttons.rebuild.dataset.busy;await refresh();}};
  buttons.graph.onclick=async()=>{buttons.graph.dataset.busy='true';buttons.graph.disabled=true;notice('正在构建图谱，请等待。');try{const r=await api(`/api/documents/${id}/graph`,{method:'POST'});notice(`图谱${label(r.status)} · ${r.facts} 条带来源事实`);}catch(e){report(e);}finally{delete buttons.graph.dataset.busy;await refresh();}};
  buttons.remove.onclick=async()=>{const d=documents.get(id);if(!d.deleted&&!confirm(`删除“${d.name}”及所有版本？`))return;try{const r=await api(`/api/documents/${id}`,{method:'DELETE'});if(current?.snapshot.documents.includes(id))clearResult('来源已删除，请选择其他文档重新提问。');selected.delete(id);saveSelected();$('versionsPanel').hidden=true;$('versions').replaceChildren();notice(r.status==='deleted'?'文档已删除。':'文档已屏蔽；存储清理未完成，可点击“重试清理”。');await refresh();}catch(e){report(e);}};
  const retry=el('button','重试','text-button');retry.hidden=true;retry.onclick=async()=>{try{await api(`/api/jobs/${documents.get(id).job_id}/retry`,{method:'POST'});await refresh();}catch(e){report(e);}};row.append(retry);const node={row,check,title,state,task,buttons,retry};documentNodes.set(id,node);$('documents').append(row);return node;
}
async function refresh() {
  const serial=++refreshSerial;
  try {
    const [docs,jobs,health,runs]=await Promise.all([api('/api/documents'),api('/api/jobs'),api('/api/health'),api('/api/runs')]);
    if(serial!==refreshSerial)return;
    uploadLimit=health.max_upload_mb||20;$('uploadLimit').textContent=uploadLimit;updateUploadSelection();mode=health.mode;documents=new Map(docs.map(d=>[d.id,d]));$('mode').textContent=mode==='offline'?'● 离线模式':'● 模型模式';$('connection').textContent='服务连接正常';
    const dense=$('strategy').querySelector('[value=dense]');dense.disabled=mode==='offline';if(dense.disabled&&$('strategy').value==='dense')$('strategy').value='hybrid';
    $('capabilities').textContent=mode==='offline'?'当前可检索原文、查看章节覆盖；联网和受控 Agent 需要配置服务。':health.online_configured?'模型与搜索已配置，真实质量仍需验收。':'模型模式；联网搜索服务尚未配置。';
    $('workflow').querySelector('[value=agent]').disabled=mode==='offline';
    for(const d of docs) { const n=documentNodes.get(d.id)||newDocumentNode(d.id),ready=!d.deleted&&d.vector_status==='ready'&&d.config===health.config;
      n.title.textContent=d.name;n.check.disabled=!ready;n.check.checked=ready&&selected.has(d.id);
      n.state.textContent=d.deleted?'已屏蔽，等待清理':d.config&&d.config!==health.config?'解析或索引规则已更新，请重建索引':ready?'可提问':d.job_status?label(d.job_status):'尚未处理';
      n.task.textContent=d.job_status?`最新任务：${label(d.job_status)}${d.job_error?' · '+errorText(d.job_error):''}${ready&&['failed','interrupted'].includes(d.job_status)?' 原版本仍可提问。':''}`:'';
      n.task.hidden=!['failed','interrupted','queued','running'].includes(d.job_status);n.retry.hidden=!['failed','interrupted'].includes(d.job_status);
      n.buttons.remove.textContent=d.deleted?'重试清理':'删除';
      for(const key of ['quality','rebuild','graph'])n.buttons[key].hidden=!!d.deleted;
      n.buttons.rebuild.disabled=!!n.buttons.rebuild.dataset.busy||!d.active||['queued','running'].includes(d.job_status);
      n.buttons.graph.disabled=!!n.buttons.graph.dataset.busy||!ready||mode==='offline';n.buttons.graph.title=mode==='offline'?'需配置模型后才能构建图谱':'';
    }
    for(const [id,n] of documentNodes)if(!documents.has(id)){n.row.remove();documentNodes.delete(id);selected.delete(id);}
    saveSelected();
    const options=new Map([...$('version').options].map(o=>[o.value,o]));
    for(const d of docs.filter(d=>!d.deleted)) { let o=options.get(d.id);if(!o){o=new Option('',d.id);$('version').add(o);}o.textContent='更新版本：'+d.name; }
    for(const [id,o] of options)if(id&&(!documents.has(id)||documents.get(id).deleted))o.remove();
    const visibleJobs=new Set(jobs.slice(0,12).map(j=>j.id));
    for(const j of jobs.slice(0,12)) { let n=jobNodes.get(j.id);if(!n){const row=el('div',undefined,'job'),text=el('span'),retry=el('button','重试');retry.onclick=async()=>{try{await api(`/api/jobs/${j.id}/retry`,{method:'POST'});await refresh();}catch(e){report(e);}};row.append(text,retry);n={row,text,retry};jobNodes.set(j.id,n);$('jobs').append(row);}n.text.textContent=`${j.document_name} · ${label(j.status)} / ${label(j.stage)}${j.error?' · '+errorText(j.error):''}`;n.retry.hidden=!['failed','interrupted'].includes(j.status); }
    for(const [id,n] of jobNodes)if(!visibleJobs.has(id)){n.row.remove();jobNodes.delete(id);}
    const runIds=new Set(runs.map(r=>r.run_id));
    for(const r of [...runs].reverse()){
      let a=historyNodes.get(r.run_id);
      if(!a){a=el('a');a.href='#run='+encodeURIComponent(r.run_id);a.onclick=e=>{e.preventDefault();loadRun(r.run_id);};a.append(el('span','','history-question'),el('span','','history-meta'));historyNodes.set(r.run_id,a);$('history').prepend(a);}
      const names=Object.values(r.documents||{}),time=historyTime(r.created_at),scope=r.scope==='web'?'联网搜索':(names.join('、')||'历史文档')+(r.scope==='combined'?' + 联网':''),workflow=r.workflow==='agent'?' · Agent':'';
      a.firstChild.textContent=r.question;a.lastChild.textContent=`${scope}${workflow} · ${time}`;
      a.title=`${r.question}\n${scope}${workflow}\n${r.created_at?new Date(r.created_at).toLocaleString('zh-CN'):'较早记录（未保存时间）'}`;
    }
    highlightHistory();
    for(const [id,a] of historyNodes)if(!runIds.has(id)){a.remove();historyNodes.delete(id);}
    $('historyEmpty').hidden=!!runs.length;
    if(current&&current.snapshot.documents.some(id=>!documents.has(id)||documents.get(id).deleted))clearResult('该结果的来源已删除，已停止显示。');
    syncQueryOptions();updateResultVersion();updateSelection();filterDocuments();$('documentsEmpty').hidden=!!docs.length;$('rerun').textContent=mode==='offline'?'重新检索':'重新生成';
  }catch(e){if(serial===refreshSerial){$('connection').textContent=errorText(e.message);$('mode').textContent='连接中断 · 点击查看';}}
}
$('refresh').onclick=refresh;
$('closeVersions').onclick=()=>{$('versionsPanel').hidden=true;};
$('upload').onsubmit=async e=>{
  e.preventDefault();
  if(uploadBusy)return;
  const file=$('file').files[0],error=uploadFileError(file);
  if(error){$('uploadStatus').textContent=error;return;}
  setUploadBusy(true);$('uploadStatus').textContent='正在上传，请稍候…';notice('正在上传，请等待处理状态更新。');
  try{
    const data=new FormData();data.append('file',file);if($('version').value)data.append('document_id',$('version').value);
    const r=await api('/api/documents',{method:'POST',body:data});
    $('file').value='';$('uploadDialog').close();
    notice(r.idempotent?'文件已存在，复用已有版本。请勾选已就绪文档后提问。':'上传成功，处理完成后请勾选文档并提问。');
    await refresh();
  }catch(err){report(err);}finally{setUploadBusy(false);}
};
$('ask').onsubmit=async e=>{
 e.preventDefault();if($('askButton').dataset.busy)return;syncQueryOptions();const scope=$('sourceScope').value,ids=scope==='web'?[]:[...document.querySelectorAll('[name=doc]:checked:not(:disabled)')].map(x=>x.value);
 if(scope!=='web'&&!ids.length){notice('请先勾选已就绪的文档。');return;}
 const epoch=++resultEpoch,progress_id=crypto.randomUUID();closeComposerMenus();$('askButton').dataset.busy='true';$('askButton').disabled=true;notice('正在处理资料…');
 const stages={waiting:'等待开始',queued:'排队中',retrieve_local:'检索文档',search_web:'搜索网页',read_page:'读取网页',read_pages:'读取网页正文',agent_decision:'模型选择下一步',generation:'依据证据生成回答',finished:'处理完成'};
 const timer=setInterval(async()=>{try{const p=await api('/api/query-progress/'+progress_id);if(epoch===resultEpoch&&$('askButton').dataset.busy)notice((stages[p.stage]||p.stage)+(p.total?` · ${p.completed||0}/${p.total}`:''));}catch{}},700);
 try{const r=await api('/api/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:$('question').value,document_ids:ids,scope,workflow:$('workflow').value,progress_id,route:scope==='documents'?$('route').value:'qa',strategy:$('strategy').value,bypass_cache:$('bypass').checked,rerank:$('rerank').checked})});if(epoch!==resultEpoch)return;$('bypass').checked=false;renderResult(r);await refresh();}
 catch(err){if(epoch===resultEpoch)report(err);}finally{clearInterval(timer);delete $('askButton').dataset.busy;updateSelection();}
};
window.addEventListener('hashchange',()=>{if(location.hash.startsWith('#run='))loadRun(decodeURIComponent(location.hash.slice(5)));});
(async()=>{await refresh();const run=location.hash.startsWith('#run=')?decodeURIComponent(location.hash.slice(5)):readSession('last-run',null);if(run)await loadRun(run);})();
setInterval(refresh,5000);

function updateSelection() {
 const chosen=[...document.querySelectorAll('[name=doc]:checked:not(:disabled)')];
 $('selectionCount').textContent=`已选 ${chosen.length} 篇文档`;$('selectDocuments').hidden=!!chosen.length;$('scopeDetails').hidden=!chosen.length;if(!chosen.length)$('scopeDetails').open=false;
 $('selectedNames').replaceChildren(...chosen.map(c=>el('p',documents.get(c.value)?.name||c.value)));
 const web=$('sourceScope').value==='web';
 $('selectDocuments').hidden=web||!!chosen.length;$('scopeDetails').hidden=web||!chosen.length;
 $('clearSelection').hidden=web||!chosen.length;$('askButton').disabled=(!web&&!chosen.length)||!!$('askButton').dataset.busy;updateWelcome(chosen.length);
}
function syncQueryOptions(){
 if(mode==='offline'&&$('workflow').value==='agent')$('workflow').value='fixed';
 const scope=$('sourceScope').value,agent=$('workflow').value==='agent';
 if(scope!=='documents'||agent)setTask('qa');
 $('routeButton').disabled=scope!=='documents'||agent;
 for(const option of $('strategy').options)option.disabled=(option.value==='dense'&&mode==='offline')||(agent&&['multi','graph'].includes(option.value));
 if($('strategy').selectedOptions[0]?.disabled)$('strategy').value='hybrid';
 $('strategy').disabled=scope==='web';
 $('strategy').title=scope==='web'?'联网搜索不使用本地向量检索策略':'';
 $('rerank').disabled=mode==='offline';
 if(mode==='offline')$('rerank').checked=false;
 $('question').placeholder=scope==='web'?'搜索公开网页并依据正文回答…':scope==='combined'?'结合所选文档和公开网页提问…':'针对所选资料提问…';
}
$('sourceScope').onchange=()=>{syncQueryOptions();updateSelection();notice($('sourceScope').value==='documents'?'仅使用所选文档。':'联网结果需读取正文后才能作为回答证据；离线模式不发起网络请求。');};
$('workflow').onchange=syncQueryOptions;
$('strategy').onchange=syncQueryOptions;

function renderDebug(r){
 let panel=$('retrievalDebug');if(!panel){panel=el('details');panel.id='retrievalDebug';$('runDetails').before(panel);}panel.replaceChildren(el('summary','检索调试：分支 → 融合 → 重排 → 上下文'));
 const table=el('table'),head=el('tr');for(const title of ['证据','分支排名','融合分数','重排位置','上下文处理'])head.append(el('th',title));table.append(head);
 const context=new Map((r.context?.decisions||[]).map(d=>[d.id,d])),fused=new Map((r.retrieval?.agent_fused||r.retrieval?.fusion_candidates||r.retrieval?.fused||[]).map(h=>[h.id,h])),pool=new Map((r.retrieval?.pool_decisions||[]).map(d=>[d.id,d]));
 const ids=[...new Set([...(r.retrieval?.candidate_pool||[]),...fused.keys(),...pool.keys()])];
 for(const id of ids){const row=el('tr'),hit=fused.get(id),decision=context.get(id)||pool.get(id),rank=r.rerank?.ids?.indexOf(id);row.append(el('td',id.startsWith('web:')?'网页 '+id.slice(4,12):id.slice(0,8)),el('td',Object.entries(hit?.branches||{}).map(([name,b])=>name+': '+b.rank).join(' / ')||(id.startsWith('web:')?'联网发现':'文档检索')),el('td',hit?.rrf?.toFixed(5)||'—'),el('td',rank>=0?String(rank+1):'未重排'),el('td',decision?decision.selection+' · '+decision.reason:'候选池外'));table.append(row);}panel.append(table);
 if(r.retrieval?.supplemental?.length)panel.append(el('p','补充查询：'+r.retrieval.supplemental.map(s=>s.query).join('；')));
 if(r.rerank?.input)panel.append(el('p',`重排输入 ${r.rerank.input.input_bytes} 字节 · ${r.rerank.input.decisions.filter(d=>d.selection==='excerpt').length} 条摘录；完整原文见来源快照。`));
 if(r.agent){panel.append(el('p','Agent 停止条件：'+r.agent.stop_reason));for(const d of r.agent.decisions)panel.append(el('pre',JSON.stringify(d,null,2)));}
 if(r.tool_trace?.length){panel.append(el('h3','工具执行'));for(const t of r.tool_trace)panel.append(el('pre',JSON.stringify(t,null,2)));}
}
function filterDocuments(){const q=$('documentSearch').value.trim().toLowerCase();for(const [id,n] of documentNodes)n.row.hidden=!documents.get(id)?.name.toLowerCase().includes(q);}
$('documentSearch').oninput=filterDocuments;
$('clearSelection').onclick=()=>{selected.clear();for(const n of documentNodes.values())n.check.checked=false;saveSelected();};
for(const name of ['documents','history'])$(name+'Tab').onclick=()=>switchLibraryTab(name);
$('openUpload').onclick=()=>{$('uploadStatus').textContent='';updateUploadSelection();$('uploadTargetHint').hidden=!$('version').value;$('uploadDialog').showModal();($('file').files.length?$('replaceFile'):$('chooseFile')).focus();};
$('closeUpload').onclick=$('cancelUpload').onclick=()=>{if(!uploadBusy)$('uploadDialog').close();};
$('showEvidence').onclick=()=>openEvidence();
$('closeEvidence').onclick=()=>{$('evidencePanel').hidden=true;$('showEvidence').focus();};
document.addEventListener('keydown',e=>{if(e.key==='Escape'){$('evidencePanel').hidden=true;$('versionsPanel').hidden=true;}});
$('copyAnswer').onclick=async()=>{try{await navigator.clipboard.writeText(current?.mode==='offline'?[(current.question||''),...(current.evidence||[]).map(e=>`${e.document_name||'来源文档'}\n${e.text}`)].join('\n\n'):$('answer').innerText);notice('已复制回答。');}catch{notice('无法自动复制，请选中回答文字复制。');}};
$('rerun').onclick=()=>{if(!current||$('askButton').dataset.busy)return;if(current.workflow==='agent'&&mode==='offline'){notice('这条记录使用受控 Agent，需要配置模型后才能按原流程重试。');return;}$('question').value=current.question;const scope=current.scope||'documents',chosen=scope==='web'?[]:[...document.querySelectorAll('[name=doc]:checked:not(:disabled)')].map(c=>c.value);if(chosen.length!==current.snapshot.documents.length||!chosen.every(id=>current.snapshot.documents.includes(id))){notice('当前选择与这条历史记录的范围不同，请确认范围后点击发送。');return;}$('sourceScope').value=scope;$('workflow').value=current.workflow||'fixed';setTask(current.route||'qa');$('strategy').value=current.strategy||'hybrid';$('rerank').checked=!!current.policy?.rerank_requested;$('bypass').checked=true;$('sourceScope').dispatchEvent(new Event('change'));$('ask').requestSubmit();};

function toggleLibrary(open){document.body.classList.toggle('library-open',open);$('toggleLibrary').setAttribute('aria-expanded',String(open));}
$('toggleLibrary').onclick=()=>toggleLibrary(!document.body.classList.contains('library-open'));
$('closeLibrary').onclick=()=>{toggleLibrary(false);$('toggleLibrary').focus();};
document.addEventListener('keydown',e=>{if(e.key==='Escape')toggleLibrary(false);});

function uploadFileError(file) {
  if(!file)return '请先选择文件。';
  if(!/\.(pdf|md|txt|csv|docx|html|htm|pptx|xlsx|xls)$/i.test(file.name))return '暂不支持此文件格式，请选择下方列出的文档格式。';
  if(!file.size)return '文件为空，请更换包含正文的文件。';
  if(file.size>uploadLimit*1024*1024)return `文件超过 ${uploadLimit} MB，请缩小文件后重新上传。`;
  return '';
}
function updateUploadSelection() {
  const file=$('file').files[0];
  $('dropZone').hidden=!!file;$('selectedFile').hidden=!file;
  if(file){
    $('selectedFileName').textContent=file.name;$('selectedFileName').title=file.name;
    $('selectedFileSize').textContent=file.size>=1024*1024?`${(file.size/(1024*1024)).toFixed(1)} MB`:file.size>=1024?`${(file.size/1024).toFixed(1)} KB`:`${file.size} B`;
    $('selectedFileType').textContent=file.name.split('.').pop().slice(0,5).toUpperCase();
    $('selectedFile').classList.toggle('invalid-file',!!uploadFileError(file));
  }
  $('uploadSubmit').disabled=uploadBusy||!!uploadFileError(file);
  if(file&&!uploadBusy&&uploadFileError(file))$('uploadStatus').textContent=uploadFileError(file);
}
function setUploadBusy(busy) {
  uploadBusy=busy;$('upload').setAttribute('aria-busy',String(busy));
  for(const id of ['chooseFile','replaceFile','removeFile','version','file','closeUpload','cancelUpload'])$(id).disabled=busy;
  $('uploadSubmit').textContent=busy?'正在上传…':'开始上传';updateUploadSelection();
}
$('chooseFile').onclick=$('replaceFile').onclick=()=>{if(!uploadBusy)$('file').click();};
$('file').onchange=()=>{$('uploadStatus').textContent='';updateUploadSelection();};
$('removeFile').onclick=()=>{if(uploadBusy)return;$('file').value='';$('uploadStatus').textContent='';updateUploadSelection();$('chooseFile').focus();};
$('version').onchange=()=>{$('uploadTargetHint').hidden=!$('version').value;};
$('uploadDialog').addEventListener('cancel',e=>{if(uploadBusy)e.preventDefault();});
$('uploadDialog').addEventListener('close',()=>{for(const id of ['dropZone','selectedFile'])$(id).classList.remove('drag-over');});
for(const id of ['dropZone','selectedFile']) {
  const zone=$(id);
  zone.addEventListener('dragover',e=>{e.preventDefault();if(!uploadBusy){zone.classList.add('drag-over');e.dataTransfer.dropEffect='copy';}});
  zone.addEventListener('dragleave',e=>{if(!zone.contains(e.relatedTarget))zone.classList.remove('drag-over');});
  zone.addEventListener('drop',e=>{
    e.preventDefault();zone.classList.remove('drag-over');if(uploadBusy)return;
    const files=e.dataTransfer.files;
    if(files.length!==1){$('uploadStatus').textContent='每次请选择一个文件，多篇文档可以依次上传。';return;}
    const transfer=new DataTransfer();transfer.items.add(files[0]);$('file').files=transfer.files;
    $('uploadStatus').textContent='';updateUploadSelection();
    (uploadFileError(files[0])?$('replaceFile'):$('uploadSubmit')).focus();
  });
}
// Keep accidental drops outside the file area from navigating away from the page.
$('uploadDialog').addEventListener('dragover',e=>e.preventDefault());
$('uploadDialog').addEventListener('drop',e=>e.preventDefault());

function switchLibraryTab(name){for(const tab of ['documents','history']){$(tab+'View').hidden=tab!==name;$(tab+'Tab').setAttribute('aria-pressed',String(tab===name));}}
function chooseDocuments(){
 closeComposerMenus();$('scopeDetails').open=false;switchLibraryTab('documents');$('documentSearch').value='';filterDocuments();
 if(matchMedia('(max-width:700px)').matches)toggleLibrary(true);
 const check=[...documentNodes.values()].find(n=>!n.check.disabled&&!n.check.checked)?.check;
 (check||$('documentSearch')).focus();
}
$('selectDocuments').onclick=$('changeDocuments').onclick=chooseDocuments;
function updateWelcome(count){
 if(current||!$('welcome'))return;
 const docs=[...documents.values()].filter(d=>!d.deleted),ready=[...documentNodes.values()].some(n=>!n.check.disabled);
 let title,description,action;
 if($('sourceScope').value==='web'){title='搜索公开网页，核对回答依据';description=mode==='offline'?'此范围无需上传文档。配置模型与搜索服务后即可联网提问。':'无需上传文档，直接输入问题；答案优先依据读取到的网页正文。';action='开始提问';}
 else if(!docs.length){title='上传第一篇文档';description='添加资料后，就可以提问并核对原文。';action='上传文档';}
 else if(count){title='想了解这些文档的什么内容？';description=`已选 ${count} 篇文档，在下方输入问题开始探索。`;action='开始提问';}
 else if(ready){title='选择文档开始提问';description='已有资料可用，先确定这次提问的文档范围。';action='选择文档';}
 else {const busy=docs.some(d=>['queued','running'].includes(d.job_status));title=busy?'文档正在处理':'文档还未就绪';description=busy?'处理完成后，即可选择文档提问。':'请查看文档状态，重试处理或重新上传。';action='查看文档状态';}
 $('welcomeTitle').textContent=title;$('welcomeDescription').textContent=description;$('welcomeAction').textContent=action;$('welcomeAction').hidden=false;
 $('welcomeAction').onclick=()=>{if($('sourceScope').value==='web'||count)$('question').focus();else if(!docs.length)$('openUpload').click();else chooseDocuments();};
}
function historyTime(value){
 if(!value)return '较早记录';const date=new Date(value);if(Number.isNaN(date.getTime()))return '较早记录';
 const now=new Date(),today=date.toDateString()===now.toDateString();
 return (today?'今天 ':date.toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'})+' ')+date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
}
function highlightHistory(){for(const [id,a] of historyNodes){if(id===current?.run_id)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');}}
function closeComposerMenus(restoreFocus=false){
 for(const [panel,trigger] of [['routeMenu','routeButton'],['advancedPanel','advancedButton']]){
  if(!$(panel).hidden&&restoreFocus)$(trigger).focus();$(panel).hidden=true;$(trigger).setAttribute('aria-expanded','false');
 }
}
function toggleComposerMenu(panel,trigger){
 const open=$(panel).hidden;closeComposerMenus();if(!open)return;
 $(panel).hidden=false;$(trigger).setAttribute('aria-expanded','true');
 if(panel==='routeMenu')$('routeMenu').querySelector('[aria-checked="true"]').focus();
}
function setTask(value){
 const options=[...$('routeMenu').querySelectorAll('[data-route]')],chosen=options.find(o=>o.dataset.route===value)||options[0];
 $('route').value=chosen.dataset.route;$('routeLabel').textContent=chosen.querySelector('span').textContent;
 for(const o of options){o.setAttribute('aria-checked',String(o===chosen));o.tabIndex=o===chosen?0:-1;}
}
$('routeButton').onclick=()=>toggleComposerMenu('routeMenu','routeButton');
$('advancedButton').onclick=()=>toggleComposerMenu('advancedPanel','advancedButton');
for(const option of $('routeMenu').querySelectorAll('[data-route]'))option.onclick=()=>{setTask(option.dataset.route);closeComposerMenus(true);};
$('routeButton').addEventListener('keydown',e=>{if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();if($('routeMenu').hidden)toggleComposerMenu('routeMenu','routeButton');}});
$('routeMenu').addEventListener('keydown',e=>{
 const options=[...$('routeMenu').querySelectorAll('[data-route]')],index=options.indexOf(document.activeElement);
 if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?options.length-1:(index+(e.key==='ArrowDown'?1:-1)+options.length)%options.length;options[next].focus();}
 if(e.key==='Tab')closeComposerMenus(true);
});
document.addEventListener('click',e=>{if(!e.target.closest('#routePicker,#advancedPicker'))closeComposerMenus();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeComposerMenus(true);});
for(const id of ['routePicker','advancedPicker'])$(id).addEventListener('focusout',()=>setTimeout(()=>{if(!$(id).contains(document.activeElement)){const panel=id==='routePicker'?'routeMenu':'advancedPanel',trigger=id==='routePicker'?'routeButton':'advancedButton';$(panel).hidden=true;$(trigger).setAttribute('aria-expanded','false');}},0));
setTask('qa');

function updateExcerptToggle(){
 const records=[...document.querySelectorAll('#answer .excerpt-record')];
 $('toggleExcerpts').hidden=!records.length;$('toggleExcerpts').textContent=records.length&&records.every(r=>r.open)?'收起全部':'展开全部';
}
$('toggleExcerpts').onclick=()=>{
 const records=[...document.querySelectorAll('#answer .excerpt-record')],open=!records.every(r=>r.open);
 for(const record of records)record.open=open;updateExcerptToggle();
 if(!open)document.querySelector('.reading').scrollTop=0;
};
$('clearCurrent').onclick=()=>{
 closeComposerMenus();clearResult('');
 const welcome=el('div',undefined,'welcome');welcome.id='welcome';
 const mark=el('span','✦','welcome-mark');mark.setAttribute('aria-hidden','true');
 const title=el('h2');title.id='welcomeTitle';const description=el('p');description.id='welcomeDescription';
 const action=el('button','选择文档','secondary');action.id='welcomeAction';action.type='button';
 welcome.append(mark,title,description,action);$('answer').replaceChildren(welcome);
 $('question').value='';$('versionsPanel').hidden=true;$('runDetails').open=false;
 document.querySelector('.reading').scrollTop=0;updateSelection();
 notice('当前结果已清空，可在“最近问答”中重新查看。');$('question').focus();
};
