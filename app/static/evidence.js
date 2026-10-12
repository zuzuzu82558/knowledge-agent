const $=id=>document.getElementById(id);
const id=decodeURIComponent(location.pathname.split('/').pop());
const names={page:'物理页',slide:'幻灯片',sheet:'工作表',paragraph:'正文项',table:'表格',row:'行',line_start:'起始文本行',line_end:'结束文本行',header_line:'表头行',element:'正文位置',shape:'形状'};
let shown=false;
async function refreshSource(){
 try{
  const response=await fetch('/api/evidence/'+encodeURIComponent(id));
  if(!response.ok){shown=false;$('source').hidden=true;$('sourceText').textContent='';$('sourceJson').textContent='';$('sourceDownload').removeAttribute('href');$('sourceStatus').textContent='来源已删除或不可用，不能继续核对。';return;}
  const ev=await response.json();$('sourceStatus').textContent='来源可用。以下为所引用历史版本，不会自动跳转到新版。';
  if(shown)return;
  const web=ev.source_type==='web';
  $('sourceLocation').replaceChildren();
  $('sourceName').textContent=web?'网页 · '+ev.title:ev.document_name||'历史文档';$('sourceVersion').textContent=web?'获取时间：'+ev.fetched_at+' · 发布时间：'+(ev.published_at||'未知'):'内容版本：'+ev.version.slice(0,8);
  $('sourceHeading').textContent=ev.headings.join(' / ')||'未识别章节标题';$('sourceText').textContent=ev.text;
  for(const loc of ev.locations){for(const [key,value] of Object.entries(loc)){if(!names[key])continue;const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=names[key];dd.textContent=String(value);$('sourceLocation').append(dt,dd);}}
  if(web){$('sourceDownload').textContent='打开当前网页（可能已变化）';$('sourceDownload').href=ev.url;$('sourceDownload').target='_blank';$('sourceDownload').rel='noopener noreferrer';}
  else $('sourceDownload').href='/api/versions/'+encodeURIComponent(ev.version)+'/file';
  $('sourceJson').textContent=JSON.stringify(ev,null,2);$('source').hidden=false;shown=true;
 }catch{$('sourceStatus').textContent='连接中断，暂不能确认来源状态。请检查服务连接，本页会自动重试。';}
}
refreshSource();setInterval(refreshSource,5000);
