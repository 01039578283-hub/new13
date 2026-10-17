/* Start one map near the viewport; hub selections replace the previous map. */
(()=>{'use strict';
 const slots=new WeakSet(),cache=new Map();
 function load(slot){
  if(!slot.isConnected||slot.dataset.state)return;
  const template=slot.querySelector('template');if(!template)return;
  slot.dataset.state='loading';slot.append(template.content.cloneNode(true));
  setTimeout(()=>{if(slot.isConnected&&slot.dataset.state!=='ready'){const p=slot.querySelector('.kd-pending');if(p)p.textContent='지도 로딩이 지연되고 있습니다. 기존 지도 이미지와 아래 주소를 확인해 주세요.';}},15000);
 }
 const observer='IntersectionObserver' in window?new IntersectionObserver(entries=>{for(const e of entries)if(e.isIntersecting){observer.unobserve(e.target);load(e.target);}},{rootMargin:'200px 0px',threshold:0}):null;
 function watch(root){for(const slot of root.querySelectorAll('.kd-map-slot'))if(!slots.has(slot)){slots.add(slot);if(observer)observer.observe(slot);else load(slot);}}
 addEventListener('message',event=>{
  if(event.data?.type!=='smallclass-map-ready')return;
  for(const frame of document.querySelectorAll('.kd-map-frame'))if(event.source===frame.contentWindow){const slot=frame.closest('.kd-map-slot');slot.dataset.state='ready';const p=slot.querySelector('.kd-pending');if(p)p.hidden=true;}
 });
 watch(document);
 for(const select of document.querySelectorAll('[data-kd-select]')){
  let version=0;const result=select.closest('.kd-directions').querySelector('[data-kd-result]');
  select.addEventListener('change',async()=>{
   const turn=++version,path=select.value;result.replaceChildren();if(!path)return;
   if(!/^\/assets\/kakao-directions\/entry-[a-f0-9]{16}\.json$/.test(path))return;
   result.textContent='지점 위치를 불러오고 있습니다.';
   try{
    if(!cache.has(path))cache.set(path,fetch(path,{credentials:'same-origin'}).then(r=>{if(!r.ok)throw Error('Map entry failed');return r.json();}).catch(e=>{cache.delete(path);throw e;}));
    const entry=await cache.get(path);if(turn!==version)return;
    result.innerHTML=entry.html;watch(result);
   }catch(e){if(turn===version)result.textContent='지점 위치를 불러오지 못했습니다. 지점 소개의 주소와 지도 이미지를 확인해 주세요.';}
  });
 }
})();
