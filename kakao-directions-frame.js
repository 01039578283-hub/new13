/* The original Kakao snippet renders inside this isolated document. */
addEventListener('DOMContentLoaded',()=>{
 const root=document.querySelector('.root_daum_roughmap');if(!root)return;
 const status=document.querySelector('.kd-frame-status');let ready=false;
 function clean(){
  root.querySelectorAll('.cont,.wrap_btn_roadview').forEach(n=>n.remove());
  for(const a of root.querySelectorAll('a')){a.removeAttribute('href');a.removeAttribute('target');a.removeAttribute('onclick');if(a.tabIndex!==-1)a.tabIndex=-1;for(const img of a.querySelectorAll('img'))if(/Kakao|카카오/i.test(img.alt))img.alt='카카오맵';}
  if(!ready&&root.querySelector('.wrap_map button')&&[...root.querySelectorAll('.wrap_map img')].some(i=>i.complete&&i.naturalWidth>0)){
   ready=true;if(status)status.hidden=true;parent.postMessage({type:'smallclass-map-ready'},'*');
  }
 }
 const observer=new MutationObserver(clean);observer.observe(root,{childList:true,subtree:true,attributes:true,attributeFilter:['href','target']});
 function stay(e){if(e.target instanceof Element&&e.target.closest('a')){e.preventDefault();e.stopImmediatePropagation();}}
 root.addEventListener('click',stay,true);root.addEventListener('auxclick',stay,true);root.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' ')stay(e);},true);
 clean();root.addEventListener('load',clean,true);
 setTimeout(()=>{if(!ready&&status)status.textContent='지도를 불러오지 못했습니다. 기존 지도 이미지와 아래 주소를 확인해 주세요.';},15000);
});
