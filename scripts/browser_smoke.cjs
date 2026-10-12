// Built-in Node WebSocket + CDP. Explicit synthetic UI fixtures; no real web/model calls.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const [base,debugPort,output]=process.argv.slice(2),report={checks:[],errors:[],fixture:'isolated_offline_HTTP_plus_explicit_UI_fixtures'};
const delay=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 let ws,pending=new Map(),serial=0;
 function call(method,params={}){return new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});}
 async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.text+': '+JSON.stringify(r.exceptionDetails.exception));return r.result.value;}
 async function until(expression){for(let i=0;i<150;i++){if(await evaluate(expression))return;await delay(100);}throw Error('UI wait timed out: '+expression);}
 const check=name=>report.checks.push(name);
 try{
  let targets;
  for(let i=0;i<100;i++){try{targets=await(await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();if(targets.some(t=>t.type==='page'))break;}catch{}await delay(100);}
  ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
  await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});
  ws.onmessage=event=>{
   const data=JSON.parse(event.data);
   if(data.id){const p=pending.get(data.id);pending.delete(data.id);if(data.error)p.reject(Error(JSON.stringify(data.error)));else p.resolve(data.result);}
   else if(data.method==='Runtime.exceptionThrown')report.errors.push(data.params.exceptionDetails.text);
   else if(data.method==='Fetch.requestPaused'){
    const url=data.params.request.url;
    call(url.startsWith(base+'/')||url.startsWith('data:')?'Fetch.continueRequest':'Fetch.failRequest',
      url.startsWith(base+'/')||url.startsWith('data:')?{requestId:data.params.requestId}:{requestId:data.params.requestId,errorReason:'BlockedByClient'}).catch(()=>{});
   }
  };
  await call('Runtime.enable');await call('Page.enable');await call('Fetch.enable',{patterns:[{urlPattern:'*'}]});
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:base});await until("document.getElementById('mode')?.textContent.includes('离线')");
  assert(await evaluate("document.getElementById('askButton').disabled"));check('document scope requires selected ready documents');
  await evaluate("document.getElementById('sourceScope').value='web';document.getElementById('sourceScope').dispatchEvent(new Event('change'))");
  assert(!(await evaluate("document.getElementById('askButton').disabled")));check('web scope allows question without uploads');
  assert(await evaluate("document.getElementById('welcomeTitle').textContent.includes('搜索公开网页')"));check('web welcome gives a question entry instead of upload instructions');
  await evaluate("document.getElementById('question').value='公开资料';document.getElementById('ask').requestSubmit()");
  await until("document.getElementById('trace').textContent.includes('network_disabled_offline')");check('actual offline HTTP path reports network disabled');
  const originalRun=await evaluate('current.run_id');
  await until("!document.getElementById('askButton').dataset.busy");
  assert(await evaluate("document.getElementById('answer').textContent.includes('网页正文') && document.getElementById('history').textContent.includes('联网搜索')"));check('web failure and history use web scope labels');
  assert(await evaluate("document.getElementById('strategy').disabled"));check('web-only scope disables irrelevant local retrieval control');
  await evaluate("document.getElementById('sourceScope').value='documents';mode='local';document.getElementById('workflow').value='agent';document.getElementById('strategy').value='graph';syncQueryOptions()");
  assert(await evaluate("document.getElementById('strategy').value==='hybrid' && document.querySelector('#strategy [value=multi]').disabled && document.querySelector('#strategy [value=graph]').disabled && document.getElementById('routeButton').disabled"));check('agent controls cannot select backend-rejected route or strategy combinations');
  await evaluate("mode='offline';syncQueryOptions()");
  assert(await evaluate("document.getElementById('workflow').value==='fixed' && document.getElementById('rerank').disabled"));check('offline mode clears unsupported model controls');
  await evaluate("document.getElementById('openUpload').click();const transfer=new DataTransfer();transfer.items.add(new File(['ReviewRevenue999 income 120 million.'], 'browser-review.txt',{type:'text/plain'}));document.getElementById('file').files=transfer.files;document.getElementById('file').dispatchEvent(new Event('change'));document.getElementById('upload').requestSubmit()");
  await until("!!document.querySelector('[name=doc]:not(:disabled)')");check('real browser upload parses and indexes an isolated document');
  await evaluate("const doc=document.querySelector('[name=doc]:not(:disabled)');if(!doc.checked)doc.click();document.getElementById('sourceScope').value='combined';document.getElementById('sourceScope').dispatchEvent(new Event('change'));document.getElementById('question').value='ReviewRevenue999';document.getElementById('ask').requestSubmit()");
  await until("current?.scope==='combined' && current?.evidence?.length>0 && !document.getElementById('askButton').dataset.busy");
  assert(await evaluate("document.getElementById('answer').textContent.includes('120 million') && current.warnings.some(w=>w.includes('network_disabled_offline'))"));check('real combined query retains document evidence when online access is unavailable');
  const localEvidenceId=await evaluate('current.evidence[0].id'),localDocumentId=await evaluate('current.snapshot.documents[0]');
  let combinedImage=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(output,'combined.png'),Buffer.from(combinedImage.data,'base64'));
  const fixture={run_id:'browser-synthetic',question:'显式合成网页展示测试',mode:'offline',scope:'web',workflow:'fixed',
   snapshot:{documents:[],generations:[],versions:[]},status:'offline_extracts',claims:[],warnings:[],elapsed_ms:1,
   evidence:[{id:'web:ui-fixture',source_type:'web',title:'测试网页 <img src=x>',url:'https://fixture.example/page',
     fetched_at:'2026-10-11T00:00:00Z',published_at:null,read_status:'body_read',text:'这是正文快照，保持历史引用。',headings:[],locations:[]}],
   online:{pages:[{url:'https://fixture.example/fail',title:'读取失败',read_status:'body_unavailable',snippet:'未核对摘要',error:'synthetic_timeout'}]},
   retrieval:{branches:{},fused:[],candidate_pool:['web:ui-fixture'],pool_decisions:[{id:'excluded-local',selection:'discarded',reason:'candidate_pool_limit'}]},context:{decisions:[{id:'web:ui-fixture',selection:'selected',reason:'full_evidence_fits'}]}};
  await evaluate(`renderResult(${JSON.stringify(fixture)})`);
  assert(await evaluate("document.getElementById('evidence').textContent.includes('获取 2026-10-11') && document.getElementById('evidence').textContent.includes('未经原文核对')"));
  assert.equal(await evaluate("document.querySelectorAll('#evidence img').length"),0);check('web card labels fetched/published time and failed-read snippets; title rendered safely');
  assert(await evaluate("document.getElementById('retrievalDebug').textContent.includes('full_evidence_fits')"));check('retrieval debug shows selection reason');
  assert(await evaluate("document.getElementById('retrievalDebug').textContent.includes('candidate_pool_limit')"));check('retrieval debug explains candidates excluded before reranking');
  await evaluate(`renderResult({...${JSON.stringify(fixture)},status:'generation_failed',claims:[],generation_error:{code:'invalid_citation'},warnings:['read_page_failed:non_public_address_denied','read_page_failed:TimeoutError','AnswerValidationError:保留证据，不返回模拟回答']})`);
  assert(await evaluate("document.getElementById('answer').textContent.includes('不属于本次证据的引用') && document.getElementById('answer').textContent.includes('部分网页正文未能读取') && !document.getElementById('answer').textContent.includes('详细原因：read_page_failed')"));check('generation error is distinct from partial page read warning');
  assert.equal(await evaluate("document.getElementById('answer').textContent.split('部分网页正文未能读取').length-1"),1);check('multiple page failures show one warning while retaining trace');
  await evaluate(`renderResult({...${JSON.stringify(fixture)},status:'generation_failed',claims:[],warnings:['ValueError:保留证据，不返回模拟回答']})`);
  assert(await evaluate("document.getElementById('answer').textContent.includes('无法确定具体的生成失败原因')"));check('legacy failure does not invent a diagnosis');
  await evaluate(`renderResult({...${JSON.stringify(fixture)},status:'refused',claims:[],warnings:[]})`);
  assert(await evaluate("document.getElementById('answer').textContent.includes('现有证据不足以回答')"));check('refusal with fetched evidence explains insufficient support');
  await evaluate(`renderResult(${JSON.stringify(fixture)})`);
  await evaluate("window.fixtureImports=0;window.originalFetch=window.fetch;window.fetch=(url,opt)=>{if(String(url).includes('/api/web-evidence/web%3Aui-fixture/save')){fixtureImports++;return Promise.resolve(new Response(JSON.stringify({document_id:'fixture'}),{status:202,headers:{'Content-Type':'application/json'}}));}return originalFetch(url,opt);}");
  assert.equal(await evaluate('fixtureImports'),0);check('web evidence is not automatically imported');
  await evaluate("document.getElementById('showEvidence').click();document.querySelector('#evidence button').click()");
  await until('fixtureImports===1');check('manual save explicitly posts snapshot import');
  let image=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(output,'desktop.png'),Buffer.from(image.data,'base64'));
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
  await evaluate("document.getElementById('closeEvidence').click();document.getElementById('retrievalDebug').open=true");
  assert(await evaluate('document.documentElement.scrollWidth<=window.innerWidth'));check('390px scope controls and debug table do not overflow viewport');
  image=await call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(output,'mobile.png'),Buffer.from(image.data,'base64'));
  await evaluate(`renderResult({...${JSON.stringify(fixture)},workflow:'agent',status:'agent_stopped'});document.getElementById('rerun').click()`);
  assert(await evaluate("document.getElementById('answer').textContent.includes('Agent 已停止') && document.getElementById('notice').textContent.includes('需要配置模型')"));check('offline rerun of historical Agent does not silently change workflow');
  await evaluate("document.getElementById('clearCurrent').click()");
  assert(await evaluate("!document.getElementById('retrievalDebug') && !document.getElementById('evidence').textContent"));check('clear result also clears retrieval debug and source cards');
  await evaluate(`loadRun(${JSON.stringify(originalRun)})`);
  await evaluate("document.getElementById('sourceScope').value='documents';document.getElementById('sourceScope').dispatchEvent(new Event('change'));document.getElementById('rerun').click()");
  await until(`current?.run_id!==${JSON.stringify(originalRun)} && !!current?.run_id`);
  assert.equal(await evaluate("document.getElementById('sourceScope').value"),'web');check('historical rerun restores the original web scope');
  await until("!document.getElementById('askButton').dataset.busy");
  await evaluate(`window.beforeDuplicateFetch=window.fetch;window.queryAttempts=0;window.fetch=(url,opt)=>{if(url==='/api/query'){queryAttempts++;return new Promise(resolve=>{window.finishQuery=()=>resolve(new Response(JSON.stringify(${JSON.stringify(fixture)}),{status:200}));});}return beforeDuplicateFetch(url,opt);};document.getElementById('ask').requestSubmit();document.getElementById('ask').requestSubmit()`);
  assert.equal(await evaluate('queryAttempts'),1);check('double submit does not duplicate query work');
  await evaluate('finishQuery()');await until("!document.getElementById('askButton').dataset.busy");
  await evaluate('window.fetch=beforeDuplicateFetch');
  await call('Page.navigate',{url:base+'/evidence/'+localEvidenceId});
  await until("document.getElementById('sourceText')?.textContent.includes('ReviewRevenue999')");check('real citation link loads the stored document version');
  await evaluate("window.normalSourceFetch=window.fetch;window.fetch=()=>Promise.resolve(new Response('{}',{status:503}));refreshSource()");
  await until("document.getElementById('source').hidden");
  await evaluate('window.fetch=normalSourceFetch;refreshSource()');
  await until("!document.getElementById('source').hidden && document.getElementById('sourceText').textContent.includes('120 million')");check('citation view recovers after a transient HTTP failure');
  await evaluate(`fetch('/api/documents/${localDocumentId}',{method:'DELETE'}).then(()=>refreshSource())`);
  assert(await evaluate("document.getElementById('source').hidden && !document.getElementById('sourceText').textContent"));check('deleted source no longer remains visible on citation page');
  const preload=await call('Page.addScriptToEvaluateOnNewDocument',{source:`const originalFetch=window.fetch;window.fetch=(url,opt)=>String(url)==='/api/evidence/web%3Aui-fixture'?Promise.resolve(new Response(JSON.stringify(${JSON.stringify(fixture.evidence[0])}),{status:200})):originalFetch(url,opt);`});
  await call('Page.navigate',{url:base+'/evidence/web%3Aui-fixture'});
  await until("document.getElementById('sourceName')?.textContent.includes('测试网页')");
  assert(await evaluate("document.getElementById('sourceText').textContent.includes('历史引用') && document.getElementById('sourceDownload').href==='https://fixture.example/page'"));check('percent-encoded web evidence ID opens snapshot without double encoding');
  await call('Page.removeScriptToEvaluateOnNewDocument',{identifier:preload.identifier});
  assert.deepEqual(report.errors,[]);report.status='passed';
 }catch(e){report.status='failed';report.error=String(e);process.exitCode=1;}
 finally{fs.writeFileSync(path.join(output,'browser.json'),JSON.stringify(report,null,2));if(ws&&ws.readyState===1){try{await call('Browser.close');}catch{}ws.close();}}
 console.log(JSON.stringify(report));
})().catch(e=>{console.error(String(e));process.exit(1)});
