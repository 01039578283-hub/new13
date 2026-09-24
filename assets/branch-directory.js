document.addEventListener('DOMContentLoaded',()=>{
  const form=document.querySelector('[data-branch-search]');
  if(form){
    const cards=[...document.querySelectorAll('[data-branch-card]')];
    const groups=[...document.querySelectorAll('[data-branch-region]')];
    const status=document.querySelector('[data-branch-status]');
    const empty=document.querySelector('[data-branch-empty]');
    const normalized=s=>s.normalize('NFKC').toLowerCase().replace(/\s+/g,'');
    const data=cards.map(el=>({el,text:normalized(el.dataset.search||''),region:el.dataset.region,subjects:JSON.parse(el.dataset.subjects||'{}')}));
    const apply=()=>{
      const values=new FormData(form),query=normalized(String(values.get('query')||'')),region=String(values.get('region')||''),subject=String(values.get('subject')||''),grade=String(values.get('grade')||'');
      let count=0;
      for(const d of data){
        const courseMatch=subject?(d.subjects[subject]?.length>0&&(!grade||d.subjects[subject].includes(grade))):(!grade||Object.values(d.subjects).some(g=>g.includes(grade)));
        const match=(!query||d.text.includes(query))&&(!region||d.region===region)&&courseMatch;
        d.el.hidden=!match;if(match)count++;
      }
      groups.forEach((g,i)=>{g.hidden=![...g.querySelectorAll('[data-branch-card]')].some(el=>!el.hidden);g.open=(query||region||subject||grade)?!g.hidden:i===0;});
      status.textContent=`조건에 맞는 지점 ${count}곳`;
      empty.hidden=count!==0;
    };
    form.addEventListener('input',apply);form.addEventListener('change',apply);
    form.addEventListener('submit',e=>e.preventDefault());
    form.addEventListener('reset',()=>requestAnimationFrame(apply));apply();
  }
  document.querySelectorAll('[data-official-video]').forEach(button=>button.addEventListener('click',()=>{
    const id=button.dataset.officialVideo;if(!/^[A-Za-z0-9_-]{11}$/.test(id||''))return;
    const frame=document.createElement('iframe');frame.src=`https://www.youtube-nocookie.com/embed/${id}?rel=0`;
    frame.title=button.dataset.videoTitle||'공식 학습코칭 소개 영상';frame.allow='accelerometer; encrypted-media; gyroscope; picture-in-picture; fullscreen';frame.allowFullscreen=true;frame.referrerPolicy='strict-origin-when-cross-origin';
    button.parentElement.replaceChildren(frame);
  }));
});
