/** Preserve URLs and media while reconciling reviewed branch facts and study pathways. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseHTML} from './image-order-build.mjs';
const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const text=s=>s.replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim();
const inside=(n,p)=>{for(let a=n.parent;a;a=a.parent)if(a===p)return true;return false};
const has=(n,c)=>(n.attrs.class||'').split(/\s+/).includes(c);
const SUBJECTS=['국어','영어','수학','과학','사회'];
const QUESTIONS={
 국어:'지문에서 답의 근거가 되는 문장과 학생이 쓴 답을 함께 표시하세요. 내용을 찾는 과정과 자기 말로 설명하는 과정 중 어디에서 막혔는지 나누어 보세요.',
 영어:'읽거나 해석하기 어려웠던 문장을 고르고 모르는 낱말, 문장 구조, 문맥 이해를 구분하세요. 도움 없이 설명한 부분과 질문이 남은 부분을 표시해 주세요.',
 수학:'최근 오답의 풀이를 지우지 말고 처음 막힌 줄과 사용한 개념을 표시하세요. 개념 선택, 조건 해석, 계산 중 무엇을 다시 확인해야 하는지 적어 보세요.',
 과학:'학교에서 배우는 과목명과 단원을 먼저 확인하세요. 개념 설명, 단위, 표·그래프 해석, 실험 결과의 근거 중 어려운 부분을 실제 과제에 표시하세요.',
 사회:'사회·역사·한국사와 선택 과목명을 구분하세요. 용어의 뜻, 자료의 근거, 사건의 순서 중 혼동한 부분을 교과서 쪽수와 함께 기록해 주세요.'
};
function gradeList(value){const out=[];for(const m of value.matchAll(/([초중고])([1-6])(?:~([초중고])?([1-6]))?/g)){if(m[3]&&m[3]!==m[1])throw Error('Unreviewed cross-stage grades');for(let n=+m[2];n<=+(m[4]||m[2]);n++)out.push(m[1]+n)}return [...new Set(out)]}
function scope(h1,isRoot){if(isRoot)return {stage:null,subjects:SUBJECTS};return {stage:/초등/.test(h1)?'초등':/중학생|중등/.test(h1)?'중등':/고등/.test(h1)?'고등':null,subjects:/국영수/.test(h1)?['국어','영어','수학']:/영수|영어수학/.test(h1)?['영어','수학']:SUBJECTS.filter(s=>h1.includes(s)).length?SUBJECTS.filter(s=>h1.includes(s)):['국어','영어','수학']}}
function apply(source,patches){patches.sort((a,b)=>b.start-a.start);let last=source.length;for(const p of patches){if(p.end>last)throw Error('Overlapping evidence edits');source=source.slice(0,p.start)+p.html+source.slice(p.end);last=p.start}return source}
export function improve(source,route,review,b,curriculum,date){
 if(source.includes('data-content-evidence="2026-10-14"'))throw Error('Fresh full build required');
 const ns=parseHTML(source),aside=ns.find(n=>n.tag==='aside'&&n.attrs['data-quality-branch']===b.path);
 if(!aside)throw Error('Missing existing plan '+route);
 if(ns.some(n=>n.tag==='img'&&inside(n,aside)))throw Error('Unexpected media in plan');
 const root=route===b.path,h1=ns.find(n=>n.tag==='h1'),label=text(source.slice(h1.openEnd,h1.closeStart));
 const selected=scope(label,root),patches=[],annotated=new Set();let supplemented=0,removedDuplicateNotes=0;
 // Only replace previously unconfirmed course entries where the source adds useful conditions.
 for(const ul of ns.filter(n=>n.tag==='ul'&&has(n,'sc-course-list'))){
  for(const li of ns.filter(n=>n.tag==='li'&&n.parent===ul)){
   const subjectNode=ns.find(n=>n.tag==='strong'&&inside(n,li));if(!subjectNode)continue;
   const subject=text(source.slice(subjectNode.openEnd,subjectNode.closeStart)),c=review.courses[subject];
   if(!c||c.kind==='listed'||c.kind==='unconfirmed')continue;
   const old=text(source.slice(li.openEnd,li.closeStart));if(!/확인|문의|미확정/.test(old))continue;
   patches.push({start:li.start,end:li.end,html:'<li data-evidence-course="'+esc(subject)+'"><strong>'+esc(subject)+'</strong><span>'+esc(c.summary)+'</span><small>'+esc(c.question)+'</small></li>'});supplemented++;annotated.add(subject);
  }
 }
 const groups=[];let links=0;
 for(const subject of selected.subjects){
  const c=review.courses[subject];const available=gradeList(b.courses[subject]||'').map(grade=>curriculum[(grade[0]==='초'?'초등':grade[0]==='중'?'중등':'고등')+'/'+grade+'/'+subject]).filter(c=>c&&(!selected.stage||c.path.includes('/'+selected.stage+'/')));
  if(!available.length&&!root){groups.push('<div class="evidence-subject"><h4>'+esc(subject)+' 상담 준비</h4><p>'+esc(QUESTIONS[subject])+'</p><p>'+esc(annotated.has(subject)?'위 수강 범위에 적힌 조건을 먼저 확인하고 학생의 학년과 희망 수업을 전달해 주세요.':c.kind==='listed'?'이 안내의 학교 단계에 맞는 개설 학년은 별도 확인이 필요합니다.':c.summary)+'</p></div>');continue}
  if(!available.length)continue;
  links+=available.length;
  groups.push('<div class="evidence-subject"><h4>'+esc(subject+(selected.stage?' · '+selected.stage:''))+' 공부 자료</h4>'+(!root?'<p>'+esc(QUESTIONS[subject])+'</p>':'')+'<nav class="quality-curriculum-links" aria-label="'+esc(b.name+' '+subject+' 학년별 학습 자료')+'">'+available.map(c=>'<a href="'+esc(c.path)+'">'+esc(c.grade+' '+subject)+'</a>').join('')+'</nav></div>');
 }
 const others=ns.filter(n=>n.tag==='p'&&!inside(n,aside));
 const notes=(b.sourceNotes||[]).filter(note=>{const duplicate=others.some(n=>text(source.slice(n.openEnd,n.closeStart))===note);if(duplicate)removedDuplicateNotes++;return !duplicate});
 let body='<aside class="quality-local-plan" data-quality-plan data-quality-branch="'+esc(b.path)+'" data-content-evidence="2026-10-14"'+(root?' id="branch-study-paths"':'')+'>';
 body+='<style>[data-content-evidence] .quality-curriculum-links{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px}[data-content-evidence] .quality-curriculum-links a{width:auto;min-height:44px;box-sizing:border-box;display:inline-flex;align-items:center;justify-content:center;margin:0;padding:10px 12px}[data-content-evidence] h4{margin:18px 0 8px}[data-evidence-teachers]{padding-left:20px;line-height:1.8}#branch-study-paths,#teacher-introductions{scroll-margin-top:180px}</style>';
 body+='<h3>'+esc(root?b.name+' 학년·과목별 공부 준비':b.name+' 상담에 가져갈 학습 기록')+'</h3>';
 body+='<p>'+esc(root?'학생의 학년을 골라 공부 순서와 기초·표준·심화 예시를 살펴보세요.':'이 페이지의 '+(selected.stage?selected.stage+' ':'')+selected.subjects.join('·')+' 학습 고민을 실제 답안과 연결해 보세요.')+' 자료의 학년 표기를 바탕으로 연결한 공부 참고 자료이며, 현재 모집·시간표·담당 교사는 상담에서 확인해 주세요.</p>';
 body+=groups.join('');
 if(!groups.length)body+='<p>희망 과목과 학년을 먼저 확인한 뒤 학생의 학교 진도에 맞는 자료를 선택하세요. 최근 교재의 단원명과 혼자 푼 답안을 준비하면 상담 범위를 정하기 쉽습니다.</p>';
 if(notes.length)body+='<div class="quality-course-conditions"><h4>'+esc(b.name)+' 수강 전 확인할 조건</h4>'+notes.map(n=>'<p>'+esc(n)+'</p>').join('')+'</div>';
 if(root&&review.staff.length){
  const teacherSection=ns.find(n=>n.attrs.id==='teacher-introductions');
  const preview=teacherSection&&ns.find(n=>has(n,'td-connect-preview')&&inside(n,teacherSection));
  if(!preview)throw Error('Missing original teacher preview '+route);
  patches.push({start:preview.start,end:preview.end,html:'<ul class="td-connect-preview" data-evidence-teachers>'+review.staff.map(s=>'<li><a href="'+esc(review.teacher)+'#profile-'+s.id+'">'+esc(s.name)+' 선생님</a> · '+esc(s.theme)+'</li>').join('')+'</ul>'});
 }
 if(review.staff.length)body+='<nav class="quality-curriculum-links"><a href="#teacher-introductions">'+esc(b.name)+' 선생님 소개와 상담 질문 연결하기</a></nav>';
 body+='<p>상담 후에는 학생이 혼자 해 볼 과제, 확인할 답안, 다음에 질문할 내용을 한 줄씩 남기세요. 설명을 들은 직후의 이해와 혼자 다시 해결한 결과를 구분하면 다음 공부를 정하기 쉽습니다.</p></aside>';
 patches.push({start:aside.start,end:aside.end,html:body});
 let result=apply(source,patches).replace(/("dateModified"\s*:\s*")[^"]+("|$)/g,'$1'+date+'$2');
 return {html:result,root,links,supplemented,removedDuplicateNotes};
}
export async function build(){
 const root=path.dirname(fileURLToPath(import.meta.url)),pub=path.join(root,'.public-release');
 const data=JSON.parse(await fs.readFile(path.join(root,'content-evidence-data.json'),'utf8')),facts=JSON.parse(await fs.readFile(path.join(root,'crawl-quality-data.json'),'utf8'));
 const routes=Object.entries(data.routes),report={pages:0,branches:0,details:0,links:0,supplementedCourseEntries:0,removedDuplicateNotes:0};let cursor=0;
 await Promise.all(Array.from({length:10},async()=>{while(cursor<routes.length){const [route,branch]=routes[cursor++],file=path.join(pub,route.slice(1),'index.html');const before=await fs.readFile(file,'utf8');const r=improve(before,route,data.branches[branch],facts.branches[branch],facts.curriculum,data.date);await fs.writeFile(file,r.html);report.pages++;report[r.root?'branches':'details']++;report.links+=r.links;report.supplementedCourseEntries+=r.supplemented;report.removedDuplicateNotes+=r.removedDuplicateNotes}}));
 const targets=new Set(routes.map(([r])=>r));const config=JSON.parse(await fs.readFile(path.join(root,'search-discovery-config.json'),'utf8'));
 for(const name of ['sitemap.xml',...config.groups.map(g=>g.file)]){const f=path.join(pub,name),s=await fs.readFile(f,'utf8');const next=s.replace(/<url>\s*<loc>([^<]+)<\/loc>([\s\S]*?)<\/url>/g,(all,loc,tail)=>{if(!targets.has(decodeURIComponent(new URL(loc).pathname)))return all;const lm='<lastmod>'+data.date+'</lastmod>';return '<url><loc>'+loc+'</loc>'+(tail.includes('<lastmod>')?tail.replace(/<lastmod>[^<]*<\/lastmod>/,lm):lm+tail)+'</url>'});if(next!==s)await fs.writeFile(f,next)}
 return report;
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))console.log(JSON.stringify(await build()));
