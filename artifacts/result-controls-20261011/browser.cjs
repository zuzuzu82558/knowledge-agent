const {chromium}=require('../../.tmp/browser/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('fs');
const base='http://127.0.0.1:18773',report={checks:[],errors:[]};
(async()=>{
 const b=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 const context=await b.newContext({viewport:{width:1440,height:1000},permissions:['clipboard-read','clipboard-write']});
 const p=await context.newPage();p.on('pageerror',e=>report.errors.push(String(e)));
 const check=n=>report.checks.push(n);
 const query=async()=>{await p.locator('#question').fill('TreeSignal');const promise=p.waitForResponse(r=>r.url().endsWith('/api/query'));await p.locator('#askButton').click();const result=await(await promise).json();await p.waitForFunction(id=>document.getElementById('trace').textContent.includes(id),result.run_id);return result;};
 try{
  await p.goto(base);await p.waitForFunction(()=>document.getElementById('mode').textContent.includes('离线'));
  for(const [name,value] of [['章节树.md',875],['指标说明.txt',998]]){
   await p.locator('#openUpload').click();
   const text='# 研究资料\nTreeSignal年度收入'+value+'万元。\n'+Array.from({length:35},(_,i)=>`│ ├─ TreeSignal 核验项目${i}：原文保留金额${value}万元。`).join('\n');
   await p.locator('#file').setInputFiles({name,mimeType:'text/plain',buffer:Buffer.from(text)});
   await p.locator('#uploadSubmit').click();await p.waitForFunction(()=>!document.getElementById('uploadDialog').open);
   await p.waitForFunction(name=>[...document.querySelectorAll('.document')].some(n=>n.textContent.includes(name)&&!n.querySelector('input').disabled),name);
  }
  for(const c of await p.locator('[name=doc]').all())await c.check();
  const result=await query();assert(result.evidence.length>=2);
  assert.equal(await p.locator('.excerpt-record[open]').count(),0);
  assert.equal(await p.locator('.excerpt-record').count(),result.evidence.length);assert(await p.locator('.excerpt-preview').first().isVisible());
  const previewHeight=await p.locator('.excerpt-preview').first().evaluate(n=>n.getBoundingClientRect().height);assert(previewHeight<=110);check('long actual sources show bounded previews by default');
  await p.screenshot({path:__dirname+'/collapsed.png'});
  const first=p.locator('.excerpt-record').first();await first.locator('summary').click();assert(await first.evaluate(n=>n.open));
  assert((await first.locator('.excerpt').innerText()).includes(result.evidence[0].text));await first.locator('summary').focus();await p.keyboard.press('Enter');assert(await first.evaluate(n=>!n.open));check('click and keyboard toggle preserve full source');
  await p.locator('#toggleExcerpts').click();assert.equal(await p.locator('.excerpt-record[open]').count(),result.evidence.length);assert.equal(await p.locator('#toggleExcerpts').innerText(),'收起全部');
  await p.locator('#toggleExcerpts').click();assert.equal(await p.locator('.excerpt-record[open]').count(),0);check('expand all and collapse all');
  await p.locator('#copyAnswer').click();await p.waitForFunction(()=>document.getElementById('notice').textContent==='已复制回答。');
  const copied=await p.evaluate(()=>navigator.clipboard.readText());assert(copied.replace(/\r\n/g,'\n').includes(result.evidence[0].text.replace(/\r\n/g,'\n')));check('copy retains full sources while previews collapsed');
  await first.locator('summary').click();await first.locator('.citation').click();assert(await p.locator('#evidencePanel').isVisible());check('expanded citation still opens source panel');
  await p.locator('#clearCurrent').click();assert.equal(await p.locator('.excerpt-record').count(),0);assert.equal(await p.locator('#trace').innerText(),'');assert.equal(await p.locator('#evidence').innerText(),'');
  assert(await p.locator('#evidencePanel').isHidden());assert(await p.locator('#clearCurrent').isHidden());assert(await p.locator('#welcome').isVisible());
  assert.equal(await p.locator('[name=doc]:checked').count(),2);assert.equal(await p.locator('#question').inputValue(),'');assert.equal(await p.evaluate(()=>location.hash),'');
  assert.equal(await p.evaluate(()=>JSON.parse(sessionStorage.getItem('last-run'))),null);check('one click clears displayed result, trace, source panel and URL, preserves selection');
  await p.locator('#historyTab').click();const history=p.locator(`#history a[href="#run=${result.run_id}"]`);assert(await history.isVisible());await history.click();await p.waitForSelector('.excerpt-record');check('cleared run remains recoverable from history');
  await p.locator('#clearCurrent').click();await p.reload();await p.waitForSelector('#welcome');assert.equal(await p.locator('.excerpt-record').count(),0);assert.equal(await p.locator('[name=doc]:checked').count(),2);check('reload respects cleared display and retained selection');
  await query();let release;const gate=new Promise(resolve=>release=resolve);
  await p.route('**/api/query',async r=>{await gate;await r.continue();});await p.locator('#askButton').click();await p.waitForFunction(()=>document.getElementById('askButton').disabled);
  await p.locator('#clearCurrent').click();release();await p.waitForFunction(()=>!document.getElementById('askButton').disabled);assert.equal(await p.locator('.excerpt-record').count(),0);assert(await p.locator('#welcome').isVisible());
  await p.unroute('**/api/query');check('late in-flight query does not restore cleared result');
  await query();await p.setViewportSize({width:390,height:844});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth),390);
  assert(await p.locator('#clearCurrent').isVisible());await p.locator('#toggleExcerpts').click();await p.locator('#toggleExcerpts').click();await p.screenshot({path:__dirname+'/narrow.png',fullPage:true});
  await p.locator('#clearCurrent').click();assert(await p.locator('#welcome').isVisible());check('390px controls and clear action remain usable');
  assert.deepEqual(report.errors,[]);report.status='passed';console.log(report.checks.length+' checks passed');
 }catch(e){report.status='failed';report.error=String(e);throw e;}finally{fs.writeFileSync(__dirname+'/browser.json',JSON.stringify(report,null,2));await b.close();}
})().catch(e=>{console.error(e);process.exit(1)});
