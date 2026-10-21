/** Source-bounded study comparisons, consultation records and branch identities. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {parseHTML} from './image-order-build.mjs';
const root=path.dirname(fileURLToPath(import.meta.url));
const data=JSON.parse(await fs.readFile(path.join(root,'search-content-data.json'),'utf8'));
const facts=JSON.parse(await fs.readFile(path.join(root,'crawl-quality-data.json'),'utf8'));
const pub=path.join(root,'.public-release');
const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const plain=s=>s.replace(/<[^>]+>/g,' ').replace(/&(?:amp|lt|gt|quot);/g,m=>({'&amp;':'&','&lt;':'<','&gt;':'>','&quot;':'"'}[m])).replace(/\s+/g,' ').trim();
const inside=(n,p)=>{for(let a=n.parent;a;a=a.parent)if(a===p)return true;return false};
const has=(n,c)=>(n.attrs.class||'').split(/\s+/).includes(c);
const url=r=>data.domain+r.split('/').map(encodeURIComponent).join('/');
const attr=(tag,k,v)=>{const re=new RegExp('\\s'+k+'(?:\\s*=\\s*(?:"[^"]*"|\'[^\']*\'|[^\\s>]+))?','i');return re.test(tag)?tag.replace(re,' '+k+'="'+esc(v)+'"'):tag.replace(/\s*\/?>$/,' '+k+'="'+esc(v)+'">')};
function apply(source,edits){edits.sort((a,b)=>b.start-a.start);let previous=source.length;for(const e of edits){if(e.end>previous)throw Error('Overlapping content edits');source=source.slice(0,e.start)+e.value+source.slice(e.end);previous=e.start}return source}
function schemaNodes(object){return Array.isArray(object)?object:object['@graph']||[object]}
const type=(n,t)=>[].concat(n['@type']||[]).includes(t);
const PROMPTS={
 국어:{record:'답의 근거로 고른 문장과 학생이 쓴 답을 함께 표시하세요.',question:'근거를 찾는 단계와 자기 말로 설명하는 단계 중 어디부터 연습해야 하나요?'},
 영어:{record:'해석이 막힌 문장에서 모르는 낱말·문장 구조·문맥을 구분해 표시하세요.',question:'어휘를 알고도 읽기 어려운 이유를 어떤 과제로 확인할 수 있나요?'},
 수학:{record:'틀린 풀이를 지우지 말고 조건 해석·개념 선택·계산 중 처음 멈춘 줄을 표시하세요.',question:'현재 단원을 다시 풀기 전에 연결되는 어떤 개념부터 확인해야 하나요?'},
 과학:{record:'학교 과목명과 단원, 설명이 막힌 개념 또는 표·그래프를 준비하세요.',question:'개념 설명과 자료 해석 중 어떤 활동부터 확인해야 하나요?'},
 사회:{record:'교과서의 용어·사건·자료 중 혼동한 부분과 쪽수를 적으세요.',question:'사회·역사·한국사와 선택 과목 중 실제로 상담할 과목과 범위는 무엇인가요?'}
};
function table(headers,rows,caption,ids=[]){return '<div class="search-content-table-wrap"><table class="search-content-table"><caption>'+esc(caption)+'</caption><thead><tr>'+headers.map(s=>'<th scope="col">'+esc(s)+'</th>').join('')+'</tr></thead><tbody>'+rows.map((row,i)=>'<tr'+(ids[i]?' id="'+esc(ids[i])+'"':'')+'>'+row.map((s,j)=>'<'+(j?'td':'th scope="row"')+(j?' data-label="'+esc(headers[j])+'"':'')+'>'+s+'</'+(j?'td':'th')+'>').join('')+'</tr>').join('')+'</tbody></table></div>'}
function studyExamples(b,p,subject){
 if(!p.stage)return '';
 const prefix=p.stage==='초등'?'초':p.stage==='중등'?'중':'고';
 const raw=b.courses[subject]||'',grades=[];
 for(const m of raw.matchAll(/([초중고])([1-6])(?:~([초중고])?([1-6]))?/g))if(m[1]===prefix)for(let n=+m[2];n<=+(m[4]||m[2]);n++)grades.push(prefix+n);
 const found=[...new Set(grades)].map(g=>facts.curriculum[p.stage+'/'+g+'/'+subject]).filter(Boolean);
 if(!found.length)return '';
 const chosen=found.length>3?[found[0],found[Math.floor((found.length-1)/2)],found.at(-1)]:found;
 return '<ul class="search-content-examples">'+chosen.map(c=>'<li><a href="'+esc(c.path)+'">'+esc(c.grade+' '+subject)+'</a>: '+esc(c.task)+'</li>').join('')+'</ul>';
}
function localChange(source,route,p){
 const ns=parseHTML(source),b=facts.branches[p.branch],main=ns.find(n=>n.tag==='main');
 if(!b||!main)throw Error('Missing reviewed local branch '+route);
 const candidates=p.kind==='selection'?['national-section-07']:p.kind==='branch'?['consultation-preparation']:['feedback-preparation','consultation-preparation','consultation-checklist','consultation','class-comparison'];
 let section=candidates.map(id=>ns.find(n=>n.tag==='section'&&n.attrs.id===id)).find(Boolean);
 if(!section)section=ns.find(n=>n.tag==='section'&&inside(n,main)&&!ns.some(x=>inside(x,n)&&['img','iframe','details'].includes(x.tag))&&ns.some(x=>x.tag==='h2'&&inside(x,n)&&/상담.*(?:질문|준비|점검)|상담에서 확인/.test(plain(source.slice(x.openEnd,x.closeStart)))));
 if(!section)throw Error('No existing consultation section '+route);
 if(ns.some(n=>inside(n,section)&&['img','iframe'].includes(n.tag)))throw Error('Media in replaced section '+route);
 const heading=ns.find(n=>n.tag==='h2'&&inside(n,section)),oldIds=ns.filter(n=>inside(n,section)&&n.attrs.id&&n!==heading).map(n=>n.attrs.id);
 const subject=p.subjects.length?p.subjects:['국어','영어','수학'];
 let title,body;
 if(p.kind==='branch'){
  title=b.name+' 상담에서 확인할 수강 조건과 준비 자료';
  const listed=Object.entries(b.courses).filter(([,v])=>!v.includes('확인')).map(([s])=>s);
  const firstStaff=b.staff[0];
  const rows=[
   ['수강 대상',esc('학생의 학년과 희망 과목을 적고 위 개설 학년표와 대조하세요.'),esc((listed.length?'자료에 학년이 표기된 '+listed.join('·')+'부터 확인하되, ':'')+'현재 모집 여부·시간표·실제 정원과 수업 시간을 지점에 질문하세요.')],
   ['학습 상태',esc('최근 시험지나 숙제에서 혼자 푼 문제와 도움받은 문제를 나누고 원래 풀이를 남기세요.'),esc('막힌 한 문제에서 필요한 설명, 혼자 다시 풀 과제, 다음에 점검할 기록을 답변으로 받아 적으세요.')],
   ['지도와 비용',firstStaff?'소개글의 <a href="'+esc(b.teacher)+'#profile-'+firstStaff.id+'">'+esc(firstStaff.name)+' 선생님 지도 방향</a>에서 관심이 간 내용을 질문으로 준비하세요.':esc('질문할 기회와 과제 조정 방식, 수업 후 받을 피드백을 질문으로 준비하세요.'),esc('소개된 교사의 실제 담당 여부와 과목·횟수·시간·교재·추가 프로그램을 포함한 최종 교습비를 확인하세요.')]
  ];
  body='<p>확인한 위치·수강 범위와 학생의 실제 답안을 연결해 상담할 내용을 정리하세요. 상담 뒤에는 지점에서 받은 답과 학생이 혼자 해 볼 과제를 각각 남기면 비교하기 쉽습니다.</p>'+table(['확인할 항목','가져갈 자료','상담에서 받아 적을 답'],rows,b.name+' 방문 상담 준비',oldIds);
 }else{
  title=p.kind==='selection'?p.label+' 선택 기준과 비교 기록':b.name+' 상담에 가져갈 '+(p.stage?p.stage+' ':'')+subject.join('·')+' 학습 기록';
  const rows=subject.map(s=>{
   const prompt=PROMPTS[s];if(!prompt)throw Error('Unreviewed subject '+s);
   const example=studyExamples(b,p,s);
   return p.kind==='selection'?[esc(s),esc(prompt.record),esc(prompt.question)+example]:[esc(s),esc(prompt.record)+example,esc(prompt.question)];
  });
  body='<p>'+esc(p.kind==='selection'?'다른 수업을 비교할 때도 같은 학생 자료와 질문을 사용하세요. 답변의 구체성, 실제 과제 조정 방법, 다음 학습에서 확인할 기록을 나란히 적어 보세요.':'이 기록은 학생이 작성할 상담 준비 예시입니다. 최근 문제와 풀이를 가져가 학생의 설명이 막히는 지점부터 확인하세요.')+'</p>'+
   table(p.kind==='selection'?['과목','같은 조건으로 준비할 자료','수업을 비교하며 물어볼 질문']:['과목','학생이 남길 학습 기록','상담할 질문'],rows,p.kind==='selection'?'학생 답안으로 수업을 비교하는 질문표':'학생 답안과 상담 질문을 연결하는 기록표',oldIds);
  if(subject.some(s=>studyExamples(b,p,s)))body+='<p class="search-content-note">학년별 항목은 제공된 커리큘럼의 공부 점검 예시입니다. 해당 단원의 현재 개설 수업이나 담당 교사를 뜻하지 않습니다. 학생의 실제 학교 진도에 맞는 학년 자료를 선택하세요.</p>';
  const links=[];
  if(p.counterpart)links.push('<a href="'+esc(p.counterpart)+'">'+esc(p.kind==='selection'?b.name+'에서 상담할 학생 기록과 수강 안내':p.neighborhood+'에서 수업을 비교할 선택 기준')+'</a>');
  if(p.kind==='selection')links.push('<a href="'+esc(b.path)+'">'+esc(b.name+' 등록 정보·개설 학년·위치')+'</a>');
  else links.push('<a href="'+esc(b.path)+'#courses">'+esc(b.name+'의 전체 과목별 개설 학년')+'</a>');
  body+='<nav class="search-content-links" aria-label="'+esc(p.kind==='selection'?'선택 기준에서 실제 지점 상담으로':'상담 준비와 학원 선택 기준')+'">'+links.join('')+'</nav>';
 }
 body=oldIds.filter(id=>!body.includes('id="'+esc(id)+'"')).map(id=>'<span id="'+esc(id)+'"></span>').join('')+body;
 const h2='<h2'+(heading?.attrs.id?' id="'+esc(heading.attrs.id)+'"':'')+'>'+esc(title)+'</h2>';
 const patches=[{start:section.openEnd,end:section.closeStart,value:h2+body}];
 const guide=ns.find(n=>n.tag==='section'&&n.attrs.id==='page-guide'),toc=guide&&ns.find(n=>n.tag==='nav'&&inside(n,guide));
 if(toc&&!ns.some(n=>n.tag==='a'&&inside(n,toc)&&n.attrs.href==='#'+section.attrs.id))patches.push({start:toc.closeStart,end:toc.closeStart,value:'<a href="#'+esc(section.attrs.id)+'">'+esc(p.kind==='selection'?'수업 선택·비교표':'학생 기록·상담표')+'</a>'});
 // Only repeated plain paragraphs in later FAQ answers are consolidated. Keep facts,
 // images, maps and the original early explanation in their current positions.
 const first=new Map();let duplicateParagraphs=0;
 for(const n of ns.filter(n=>n.tag==='p'&&inside(n,main)&&!inside(n,section))){
  const value=plain(source.slice(n.openEnd,n.closeStart));if(value.length<50)continue;
  if(first.has(value)){
   let details=n.parent;while(details&&details.tag!=='details'&&details!==main)details=details.parent;
   const owner=details?.tag==='details'&&ns.find(x=>x.tag==='summary'&&inside(x,details));
   if(owner&&!ns.some(x=>inside(x,n)&&['img','iframe','a','time'].includes(x.tag))){
    const earlier=first.get(value),parent=earlier.parent;
    if(parent?.attrs.id)patches.push({start:n.start,end:n.end,value:'<p>위 <a href="#'+esc(parent.attrs.id)+'">'+esc(ns.find(x=>/^h[234]$/.test(x.tag)&&inside(x,parent))?plain(source.slice(ns.find(x=>/^h[234]$/.test(x.tag)&&inside(x,parent)).openEnd,ns.find(x=>/^h[234]$/.test(x.tag)&&inside(x,parent)).closeStart)):'학습 점검 안내')+'</a>의 과제와 학생 답안을 대조하고, 설명이 막힌 부분을 상담 질문으로 남기세요.</p>'}),duplicateParagraphs++;
   }
  }else first.set(value,n);
 }
 const dateNodes=ns.filter(n=>n.tag==='p'&&has(n,'sc-date')&&!inside(n,section));
 for(const n of dateNodes)if(/내용 검토/.test(source.slice(n.openEnd,n.closeStart)))patches.push({start:n.openEnd,end:n.closeStart,value:'자료: 센터 제공 등록·수강 정보와 교사 소개. 자료 대조 <time datetime="'+data.date+'">'+data.date+'</time> · 현재 모집·시간표와 최종 수강 조건은 상담에서 확인해 주세요.'});
 return {html:apply(source,patches),duplicateParagraphs,section:section.attrs.id};
}
function teacherChange(source,route,t){
 const ns=parseHTML(source),related=ns.find(n=>n.tag==='section'&&has(n,'td-related'));
 if(!related)throw Error('Missing existing teacher related section '+route);
 const info=t.identity;let body='<h2>'+esc(t.name)+' 교사 소개와 상담할 지점 확인</h2>';
 if(info){
  body+='<dl class="search-content-facts"><div><dt>등록 학원명</dt><dd>'+esc(info.registeredName)+'</dd></div><div><dt>자료상 주소</dt><dd>'+esc(info.address)+'</dd></div>'+(info.registration?'<div><dt>등록 정보</dt><dd>'+esc(info.registration)+'</dd></div>':'')+'</dl>';
  if(Object.keys(info.courses||{}).length)body+='<h3>센터 자료에 표기된 학년</h3><ul>'+Object.entries(info.courses).map(([s,g])=>'<li><strong>'+esc(s)+'</strong> '+esc(g)+'</li>').join('')+'</ul>';
  body+='<p>'+esc(info.condition)+'</p>';
 }else body+='<p>제공된 교사 소개의 지점명은 '+esc(t.name)+'입니다. 같은 이름의 다른 지점과 혼동하지 않도록 상담 전에 등록 학원명·주소·현재 담당 과목을 확인해 주세요.</p>';
 body+='<h3>소개글에서 상담 질문으로 옮길 내용</h3><ul class="search-content-teacher-questions">'+t.staff.map(s=>'<li><a href="#profile-'+s.id+'">'+esc(s.name)+' 선생님 소개</a>의 <strong>'+esc(s.theme.split(' / ')[0])+'</strong>에서 관심이 간 부분을 고르고, 학생이 실제로 풀었던 문제에 어떻게 적용할지 질문하세요.</li>').join('')+'</ul>';
 body+='<p class="search-content-note">소개글의 지도 방향과 현재 배정은 별개입니다. 담당 과목·학년·수업 가능 여부는 지점에 확인하세요. 프로필의 소개용 이미지는 실제 교사 사진과 구분해 보세요.</p>';
 const oldNav=ns.find(n=>n.tag==='nav'&&inside(n,related));
 if(oldNav)body+=source.slice(oldNav.start,oldNav.end);
 body+='<p class="search-content-note">자료: 제공된 교사 소개와 '+(info?'센터 등록·수강 정보':'지점별 소개 자료')+'. 자료 대조 <time datetime="'+data.date+'">'+data.date+'</time>.</p>';
 const edits=[{start:related.openEnd,end:related.closeStart,value:body},{start:related.start,end:related.openEnd,value:attr(source.slice(related.start,related.openEnd),'id','teacher-center-info')}];
 const actions=ns.find(n=>n.tag==='div'&&has(n,'td-head-actions'));
 if(actions)edits.push({start:actions.closeStart,end:actions.closeStart,value:'<a class="td-quiet-link" href="#teacher-center-info">등록 학원·상담 질문 확인</a>'});
 return apply(source,edits);
}
function consolidate(source){
 const ns=parseHTML(source),main=ns.find(n=>n.tag==='main');if(!main)return {html:source,count:0};
 const edits=[];let count=0;
 for(const [i,value] of (data.consolidateTexts||[]).entries()){
  const matches=ns.filter(n=>n.tag==='p'&&inside(n,main)&&plain(source.slice(n.openEnd,n.closeStart))===value);
  if(matches.length<2)continue;
  const first=matches.find(n=>{for(let p=n.parent;p;p=p.parent)if(p.tag==='details')return false;return true})||matches[0];
  const id=first.attrs.id||'study-note-'+(i+1);
  if(!first.attrs.id)edits.push({start:first.start,end:first.openEnd,value:attr(source.slice(first.start,first.openEnd),'id',id)});
  for(const n of matches)if(n!==first){edits.push({start:n.start,end:n.end,value:'<p><a href="#'+esc(id)+'">'+(value.includes('일대일')?'해당 지점의 수강 조건':'학습 범위의 점검 방법')+'</a>을 확인해 주세요.</p>'});count++}
 }
 return {html:apply(source,edits),count};
}
function readingChange(source){
 const ns=parseHTML(source),main=ns.find(n=>n.tag==='main');if(!main)return {html:source,parts:[]};
 const candidates=ns.filter(n=>n.tag==='h2'&&inside(n,main)&&!/함께|이어서|더 살펴볼|참고 자료|지점 안내와|도움받을 질문|관련/.test(plain(source.slice(n.openEnd,n.closeStart))));
 if(candidates.length<3)return {html:source,parts:[]};
 const selected=candidates.slice(0,5),patches=[],parts=[];
 for(const [i,n] of selected.entries()){
  const target=n.attrs.id||'study-reading-'+(i+1);
  if(!n.attrs.id)patches.push({start:n.start,end:n.openEnd,value:attr(source.slice(n.start,n.openEnd),'id',target)});
  parts.push({id:target,name:plain(source.slice(n.openEnd,n.closeStart))});
 }
 const nav='<nav class="search-content-reading" aria-label="이 글에서 필요한 내용 찾기"><strong>필요한 내용부터 읽어 보세요.</strong><ol>'+parts.map(p=>'<li><a href="#'+esc(p.id)+'">'+esc(p.name)+'</a></li>').join('')+'</ol></nav>';
 const firstAnswer=ns.find(n=>n.attrs.id==='first-answer'&&inside(n,main));
 const at=firstAnswer?firstAnswer.end:selected[0].start;
 patches.push({start:at,end:at,value:nav});return {html:apply(source,patches),parts};
}
function metadata(source,route,p,t,parts){
 let description;
 if(p&&p.kind!=='branch'){
  const b=facts.branches[p.branch];
  description=p.kind==='selection'?p.label+' 선택 기준과 비교 질문을 살펴보고, '+b.name+'의 수강 범위·위치를 확인하세요.':p.label+' 상담에 필요한 학습 기록과 질문, '+b.name+'의 개설 범위·위치를 확인하세요.';
  if(description.length>80)description=p.label+(p.kind==='selection'?' 선택에 필요한 비교 기준과 실제 연결 지점의 수강 조건을 확인하세요.':' 상담에 필요한 학습 기록과 실제 연결 지점의 수강 조건을 확인하세요.');
 }else if(t)description=t.name+' 교사 소개 '+t.staff.length+'건과 '+(t.identity?'등록 학원·주소':'상담 준비')+'를 살펴보고, 지도 방향에 맞는 상담 질문을 준비하세요.';
 if(description&&description.length>80)throw Error('Description exceeds 80 characters '+route);
 const ns=parseHTML(source),patches=[],head=ns.find(n=>n.tag==='head'),canonical=ns.find(n=>n.tag==='link'&&n.attrs.rel==='canonical')?.attrs.href;
 if(!canonical||!head)throw Error('Missing canonical metadata '+route);
 if(description)for(const n of ns.filter(n=>n.tag==='meta'&&inside(n,head)&&['description','og:description','twitter:description'].includes(n.attrs.name||n.attrs.property)))patches.push({start:n.start,end:n.openEnd,value:attr(source.slice(n.start,n.openEnd),'content',description)});
 const bodyMain=ns.find(n=>n.tag==='main');
 const answer=ns.find(n=>n.tag==='p'&&inside(n,bodyMain)&&plain(source.slice(n.openEnd,n.closeStart)).length>=60&&!has(n,'search-content-note'));
 // The media parser treats raw script blocks as opaque tokens. Read JSON-LD
 // directly so attributes inside JSON cannot be mistaken for HTML attributes.
 for(const match of source.slice(head.openEnd,head.closeStart).matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)){
  if(!/\btype\s*=\s*["']application\/ld\+json["']/i.test(match[1]))continue;
  let object=JSON.parse(match[2]);
  const nodes=schemaNodes(object);
  for(const item of nodes){
   if(['WebPage','CollectionPage','Article'].some(v=>type(item,v))){
    if(description)item.description=description;
    item.dateModified=data.date;
    if(answer)item.abstract=plain(source.slice(answer.openEnd,answer.closeStart));
    if(parts.length&&type(item,'Article'))item.hasPart=parts.map(p=>({'@type':'WebPageElement','@id':canonical+'#'+p.id,name:p.name}));
    if(t?.identity)item.about={'@id':t.identity.path?url(t.identity.path)+'#center':canonical+'#center'};
   }
   if(type(item,'FAQPage'))for(const q of item.mainEntity||[]){
    const summary=ns.find(n=>n.tag==='summary'&&plain(source.slice(n.openEnd,n.closeStart))===q.name);
    if(summary?.parent?.tag==='details'&&q.acceptedAnswer)q.acceptedAnswer.text=plain(source.slice(summary.end,summary.parent.closeStart));
   }
  }
  if(t?.identity){
   const info=t.identity,entityId=info.path?url(info.path)+'#center':canonical+'#center';
   if(!nodes.some(n=>n['@id']===entityId)){
    nodes.push({'@type':'EducationalOrganization','@id':entityId,name:info.registeredName,alternateName:info.name,url:info.path?url(info.path):canonical,address:{'@type':'PostalAddress',streetAddress:info.address,addressCountry:'KR'}});
    if(!Array.isArray(object)&&!object['@graph'])object={'@context':object['@context']||'https://schema.org','@graph':nodes};
   }
  }
  const start=head.openEnd+match.index+match[0].indexOf('>')+1;
  patches.push({start,end:start+match[2].length,value:JSON.stringify(object).replaceAll('<','\\u003c')});
 }
 return apply(source,patches);
}
export function improve(source,route){
 if(source.includes('data-search-content="'+data.date+'"'))throw Error('Fresh content input required '+route);
 const p=data.routes[route],t=data.teachers[route],reading=data.readingRoutes.includes(route);let result=source,duplicateParagraphs=0,parts=[],section=null;
 if(p){const r=localChange(result,route,p);result=r.html;duplicateParagraphs=r.duplicateParagraphs;section=r.section}
 if(t)result=teacherChange(result,route,t);
 if(reading){const r=readingChange(result);result=r.html;parts=r.parts}
 if(data.thumbnailAlts?.[route]){
  const ns=parseHTML(result),patches=[];
  for(const n of ns.filter(n=>n.tag==='img'&&!n.attrs.alt&&n.attrs.src)){
   const key=decodeURIComponent(new URL(n.attrs.src,data.domain).pathname),alt=data.thumbnailAlts[route][key];
   if(alt)patches.push({start:n.start,end:n.openEnd,value:attr(result.slice(n.start,n.openEnd),'alt',alt)});
  }
  result=apply(result,patches);
 }
 const consolidated=consolidate(result);result=consolidated.html;duplicateParagraphs+=consolidated.count;
 if(result===source)return {html:source,changed:false};
 result=metadata(result,route,p,t,parts);
 result=result.replace(/<main\b/i,'<main data-search-content="'+data.date+'"');
 result=result.replace(/<\/head>/i,'<link data-search-content-style rel="stylesheet" href="'+data.css+'">$&');
 return {html:result,changed:true,kind:p?.kind||(t?'teacher':reading?'reading':'thumbnail'),duplicateParagraphs,section,readingLinks:parts.length};
}
for(const [name,content] of Object.entries(data.generatedAssets)){
 if(!/^assets\/quality\/[a-f0-9]{20}\.css$/.test(name)||!name.includes(createHash('sha256').update(content).digest('hex').slice(0,20)))throw Error('Unreviewed content asset');
 await fs.mkdir(path.dirname(path.join(pub,name)),{recursive:true});await fs.writeFile(path.join(pub,name),content);
}
if(!/^[a-f0-9]{32}$/.test(data.indexNow.key)||data.indexNow.file!==data.indexNow.key+'.txt')throw Error('Invalid IndexNow ownership file');
await fs.writeFile(path.join(pub,data.indexNow.file),data.indexNow.key);
const all=[...new Set([...Object.keys(data.routes),...Object.keys(data.teachers),...data.readingRoutes,...Object.keys(data.thumbnailAlts||{})])];
const selector=process.argv.find(s=>s.startsWith('--sample-file='));const selected=selector?JSON.parse(await fs.readFile(selector.slice(14),'utf8')):all;
if(selected.some(r=>!all.includes(r)))throw Error('Unknown content route');
const changed=new Set(),report={pages:0,kinds:{},duplicateParagraphs:0,readingLinks:0,sections:{}};
let cursor=0;await Promise.all(Array.from({length:6},async()=>{while(cursor<selected.length){const route=selected[cursor++],file=path.join(pub,route.slice(1),'index.html'),before=await fs.readFile(file,'utf8'),r=improve(before,route);if(!r.changed)continue;await fs.writeFile(file,r.html);changed.add(route);report.pages++;report.kinds[r.kind]=(report.kinds[r.kind]||0)+1;report.duplicateParagraphs+=r.duplicateParagraphs||0;report.readingLinks+=r.readingLinks||0;if(r.section)report.sections[r.section]=(report.sections[r.section]||0)+1;}}));
const sitemapFiles=['sitemap.xml',...(await fs.readdir(path.join(pub,'sitemaps'))).filter(s=>s.endsWith('.xml')&&!s.startsWith('index')).map(s=>'sitemaps/'+s)];
for(const name of sitemapFiles){const file=path.join(pub,name),before=await fs.readFile(file,'utf8'),after=before.replace(/<url>[\s\S]*?<\/url>/g,block=>{const loc=block.match(/<loc>([^<]+)<\/loc>/);if(!loc||!changed.has(decodeURIComponent(new URL(loc[1]).pathname)))return block;return block.replace(/<lastmod>[^<]*<\/lastmod>/,'<lastmod>'+data.date+'</lastmod>')});if(after!==before)await fs.writeFile(file,after)}
// RSS article text has not been edited; keep its actual publication content and dates.
console.log(JSON.stringify(report));
