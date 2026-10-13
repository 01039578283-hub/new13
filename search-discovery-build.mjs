/** Evidence-bounded branch navigation and supplementary, measurable sitemaps. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseHTML} from './image-order-build.mjs';
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const inside=(n,p)=>{for(let a=n.parent;a;a=a.parent)if(a===p)return true;return false};
function grades(value){
 const found=new Set();
 for(const m of String(value).matchAll(/([초중고])([1-6])(?:~([초중고])?([1-6]))?/g)){
  if(m[3]&&m[3]!==m[1])throw Error('Cross-stage range needs review');
  const end=Number(m[4]||m[2]);
  if(end<Number(m[2])||end>(m[1]==='초'?6:3))throw Error('Invalid grade range');
  for(let n=Number(m[2]);n<=end;n++)found.add(m[1]+n);
 }
 return [...found];
}
export function branchNavigation(source,branch,facts,date){
 if(source.includes('data-branch-study-paths'))return {html:source,alreadyBuilt:true};
 const nodes=parseHTML(source);
 const aside=nodes.find(n=>n.tag==='aside'&&n.attrs['data-quality-branch']===branch.path);
 if(!aside)throw Error('Missing reviewed branch learning block: '+branch.path);
 const children=nodes.filter(n=>n.parent===aside);
 const tail=children.find(n=>n.attrs.class?.includes('quality-course-conditions') || (n.tag==='p'&&/^(?:소개 자료의 |학교명만으로 )/.test(source.slice(n.openEnd,n.closeStart))));
 if(!tail)throw Error('Missing preserved branch conditions or staff: '+branch.path);
 const groups=[];const links=[];
 for(const subject of ['국어','영어','수학','과학','사회']){
  const available=grades(branch.courses[subject]||'').map(grade=>{
   const stage=grade[0]==='초'?'초등':grade[0]==='중'?'중등':'고등';return facts.curriculum[stage+'/'+grade+'/'+subject];
  }).filter(Boolean);
  if(!available.length)continue;
  links.push(...available.map(c=>c.path));
  groups.push('<div class="branch-study-subject"><p><strong>'+esc(subject)+'</strong> · 학생의 학년을 선택하세요.</p><nav class="quality-curriculum-links" aria-label="'+esc(branch.name+' '+subject+' 학년별 공부 순서')+'">'+available.map(c=>'<a href="'+esc(c.path)+'">'+esc(c.grade+' '+c.subject)+'</a>').join('')+'</nav></div>');
 }
 const intro=groups.length?'<p>아래 링크는 '+esc(branch.name)+'의 제공 자료에 학년이 표기된 과목과 연결한 <strong>학습 계획 참고 자료</strong>입니다. 학년을 선택하면 학습 중점, 기초·표준·심화 예시와 확인 과제를 볼 수 있습니다. 현재 모집 여부와 시간표, 실제 담당 선생님은 상담에서 확인해 주세요.</p>':'<p>'+esc(branch.name)+'의 제공 자료만으로 과목별 개설 학년을 확정하기 어렵습니다. 희망 학년·과목을 먼저 지점에 확인하고 학생의 학교 진도에 맞는 학습 자료를 선택해 주세요.</p>';
 const body='<h3>'+esc(branch.name)+' 학년·과목별 공부 준비</h3><div data-branch-study-paths>'+intro+groups.join('')+'<p>선택한 자료의 첫 내용부터 모두 공부하기보다, 최근 답안에서 막힌 부분을 찾고 관련 단원의 확인 과제와 비교하세요. 상담에서는 혼자 해결한 부분과 설명이 필요한 부분을 나눠 보여 주세요.</p></div>';
 const style='<style>#branch-study-paths{scroll-margin-top:180px}[data-branch-study-paths] .quality-curriculum-links{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:8px}[data-branch-study-paths] .quality-curriculum-links a{width:auto;margin:0;min-height:44px;display:flex;align-items:center;justify-content:center;padding:10px 12px;box-sizing:border-box}</style>';
 let result=source.slice(0,aside.openEnd-1)+' id="branch-study-paths">'+style+body+source.slice(tail.start);
 const updatedNodes=parseHTML(result);const guide=updatedNodes.find(n=>n.attrs.id==='page-guide');
 const navigation=guide&&updatedNodes.find(n=>n.tag==='nav'&&inside(n,guide));
 if(!navigation)throw Error('Missing branch table of contents: '+branch.path);
 result=result.slice(0,navigation.closeStart)+'<a href="#branch-study-paths">학습 자료·상담 준비</a>'+result.slice(navigation.closeStart);
 result=result.replace(/("dateModified"\s*:\s*")[^"]+("|$)/g,'$1'+date+'$2');
 return {html:result,links,subjects:groups.length};
}
export async function build(root=path.dirname(fileURLToPath(import.meta.url)),output=path.join(root,'.public-release')){
 const config=JSON.parse(await fs.readFile(path.join(root,'search-discovery-config.json'),'utf8'));
 const facts=JSON.parse(await fs.readFile(path.join(root,'crawl-quality-data.json'),'utf8'));
 const changed=new Set();let links=0,withoutConfirmedGrades=0;
 for(const route of config.branchRoutes){
  const branch=facts.branches[route];if(!branch||branch.path!==route)throw Error('Unconfirmed branch '+route);
  const file=path.join(output,route.slice(1),'index.html');const before=await fs.readFile(file,'utf8');
  const result=branchNavigation(before,branch,facts,config.date);
  if(result.html!==before)await fs.writeFile(file,result.html);
  changed.add(route);links+=(result.links||[]).length;if(result.subjects===0)withoutConfirmedGrades++;
 }
 const sitemapFile=path.join(output,'sitemap.xml');const original=await fs.readFile(sitemapFile,'utf8');
 const entries=new Map();
 const sitemap=original.replace(/<url>\s*<loc>([^<]+)<\/loc>([\s\S]*?)<\/url>/g,(whole,loc,tail)=>{
  const url=new URL(loc);if(url.origin!==config.domain)throw Error('Unexpected sitemap domain');
  const route=decodeURIComponent(url.pathname);if(entries.has(route))throw Error('Duplicate canonical URL '+route);
  if(changed.has(route)){const lastmod='<lastmod>'+config.date+'</lastmod>';whole='<url><loc>'+loc+'</loc>'+(tail.includes('<lastmod>')?tail.replace(/<lastmod>[^<]*<\/lastmod>/,lastmod):lastmod+tail)+'</url>'}
  entries.set(route,whole);return whole;
 });
 if(sitemap!==original)await fs.writeFile(sitemapFile,sitemap);
 const seen=new Set();const generated=[];
 for(const group of config.groups){
  if(!/^sitemaps\/[a-z0-9-]+\.xml$/.test(group.file)||!config.generatedFiles.includes(group.file))throw Error('Unsafe generated sitemap path');
  const items=group.routes.map(route=>{if(seen.has(route)||!entries.has(route))throw Error('Sitemap partition mismatch '+route);seen.add(route);return entries.get(route)});
  const xml='<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'+items.join('\n')+'\n</urlset>\n';
  const dest=path.join(output,group.file);await fs.mkdir(path.dirname(dest),{recursive:true});await fs.writeFile(dest,xml);generated.push(group.file);
 }
 if(seen.size!==entries.size)throw Error('Supplementary sitemap omits canonical URLs');
 // Existing /sitemap.xml remains available to previously configured consumers.
 // No artificial priority or current-date lastmod is applied to unchanged pages.
 const index='<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'+generated.map(file=>'<sitemap><loc>'+config.domain+'/'+file+'</loc></sitemap>').join('\n')+'\n</sitemapindex>\n';
 await fs.writeFile(path.join(output,config.index),index);
 return {branchPages:changed.size,gradeLinks:links,withoutConfirmedGrades,supplementarySitemaps:generated.length,canonicalURLs:seen.size};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))console.log(JSON.stringify(await build()));
