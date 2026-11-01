// Selection offsets use Unicode code points, matching Python's saved source offsets.
let personalSelection=null,markNoticeTimer;
function markNotice(message){$('mark-notice').textContent=message;$('mark-notice').hidden=false;clearTimeout(markNoticeTimer);markNoticeTimer=setTimeout(()=>$('mark-notice').hidden=true,5000);}
document.addEventListener('selectionchange',()=>{
 const selection=window.getSelection();
 if(!selection||selection.isCollapsed||!selection.rangeCount){personalSelection=null;$('selection-tools').hidden=true;return;}
 const range=selection.getRangeAt(0),element=node=>node.nodeType===1?node:node.parentElement;
 const start=element(range.startContainer).closest('.en'),end=element(range.endContainer).closest('.en');
 if(!start||start!==end){personalSelection=null;$('selection-tools').hidden=true;return;}
 const article=start.closest('[data-row]');if(!article)return;
 const prefix=range.cloneRange();prefix.selectNodeContents(start);prefix.setEnd(range.startContainer,range.startOffset);
 const offset=Array.from(prefix.toString()).length,text=range.toString();if(!text.trim())return;
 personalSelection={row:article.dataset.row,start:offset,end:offset+Array.from(text).length,text};
 $('selection-text').textContent=text;$('selection-tools').hidden=false;
});
for(const kind of ['word','sentence']){
 const button=$('save-'+kind);button.onpointerdown=event=>event.preventDefault();
 button.onclick=async()=>{
  if(!personalSelection)return;const selection={...personalSelection,kind};
  $('save-word').disabled=true;$('save-sentence').disabled=true;
  try{const result=await post('save-mark',selection);data.marks=result.marks;window.getSelection().removeAllRanges();personalSelection=null;$('selection-tools').hidden=true;render();markNotice(result.message);}
  catch(error){markNotice(error.message);}
  finally{$('save-word').disabled=false;$('save-sentence').disabled=false;}
 };
}
