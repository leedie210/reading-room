const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let books=[],marks=[],mode=new URLSearchParams(location.search).get('view')||'shelf';
async function get(route){const response=await fetch(route);if(!response.ok)throw Error(await response.text());return response.json();}
function stamp(time){if(time===null||time===undefined)return '无时间轴';const seconds=Math.floor(time);return String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');}
async function openMaterial(id,row){$('status').textContent='正在打开阅读素材…';try{const result=await get('library-open?material='+encodeURIComponent(id)),url=new URL(result.url,location.href);if(row)url.searchParams.set('row',row);location.href=url.href;}catch(error){$('status').textContent=error.message;}}
function draw(){
 if(!['shelf','word','sentence'].includes(mode))mode='shelf';
 document.querySelectorAll('[data-view]').forEach(link=>{if(link.dataset.view===mode)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
 const count=new Set(marks.filter(m=>m.kind==='word').map(m=>m.text.trim().replace(/\s+/g,' ').toLowerCase())).size;
 $('word-count').textContent=count;$('sentence-count').textContent=marks.filter(m=>m.kind==='sentence').length;
 $('books').hidden=mode!=='shelf';$('collection').hidden=mode==='shelf';$('export').hidden=mode==='shelf';
 if(mode==='shelf'){
  $('heading').textContent='把值得重读的，留在这里。';$('description').textContent=`${books.length} 份英语素材 · 点击封面进入阅读`;$('eyebrow').textContent='YOUR PERSONAL LIBRARY';
  $('books').innerHTML=books.map(b=>`<button class="book-card" data-book="${esc(b.id)}"><div class="book-cover">${b.cover?`<img src="library-cover?material=${encodeURIComponent(b.id)}" alt="${esc(b.title)}的视频封面">`:`<span>${esc(b.title)}</span>`}</div><h2>${esc(b.title)}</h2><p>${esc(b.platform)} · ${b.has_reading?'阅读正文已保存':'待生成阅读文本'}</p></button>`).join('')||'<p class="shelf-empty">在素材工具中勾选“作为英语学习素材”，就会出现在这里。</p>';
  document.querySelectorAll('[data-book]').forEach(button=>button.onclick=()=>openMaterial(button.dataset.book));
  document.querySelectorAll('.book-cover img').forEach(img=>img.onerror=()=>{const fallback=document.createElement('span');fallback.textContent=img.alt.replace(/的视频封面$/,'');img.replaceWith(fallback);});
 }else{
  $('heading').textContent=mode==='word'?'我的重点词汇':'我留下的句子';$('description').textContent='保留原文，也保留你遇见它的地方。';$('eyebrow').textContent='COLLECTED IN CONTEXT';$('export').href='marks-export?kind='+mode;$('export').download=(mode==='word'?'重点词汇':'标记句段')+'.md';
  const groups=new Map();for(const mark of marks.filter(m=>m.kind===mode)){const key=mode==='word'?mark.text.trim().replace(/\s+/g,' ').toLowerCase():mark.id;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(mark);}
  $('collection').innerHTML=[...groups.values()].map(items=>`<section class="collection-group"><h2>${esc(items[0].text)}</h2>${items.map(m=>`<blockquote>${esc(m.context)}</blockquote><p class="source">${esc(m.title)} · #${String(m.sequence).padStart(3,'0')} · ${stamp(m.video_time)} ${m.valid?'':' · 原文已更新，保留标记时上下文'}<br><button data-material="${esc(m.material_id)}" data-row="${esc(m.row_id)}">回到原文 ↗</button></p>`).join('')}</section>`).join('')||'<p class="shelf-empty">还没有标记。打开一本素材，选中英文后存为词汇或句段。</p>';
  document.querySelectorAll('[data-material]').forEach(button=>button.onclick=()=>openMaterial(button.dataset.material,button.dataset.row));
 }
}
document.querySelectorAll('[data-view]').forEach(link=>link.onclick=event=>{event.preventDefault();mode=link.dataset.view;history.replaceState(null,'','shelf'+(mode==='shelf'?'':'?view='+mode));draw();});
(async()=>{[books,marks]=await Promise.all([get('shelf-data'),get('library-marks')]);draw();})().catch(error=>$('status').textContent=error.message);
