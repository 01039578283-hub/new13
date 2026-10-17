/** Add verified directions without altering existing media, copy or representative metadata. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {parseHTML} from './image-order-build.mjs';
const root=path.dirname(fileURLToPath(import.meta.url)),pub=path.join(root,'.public-release');
const config=JSON.parse(await fs.readFile(path.join(root,'kakao-directions-data.json'),'utf8'));
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const digest=s=>createHash('sha256').update(s).digest('hex');
const safeAsset=/^assets\/kakao-directions\/[a-z]+-[a-f0-9]{16}\.(?:css|js|html|json)$/;
for(const [name,content] of Object.entries(config.assets)){
 if(!safeAsset.test(name)||!name.includes('-'+digest(content).slice(0,16)+'.'))throw Error('Invalid generated map asset '+name);
 const dest=path.join(pub,name);await fs.mkdir(path.dirname(dest),{recursive:true});await fs.writeFile(dest,content);
}
function patches(source,edits){edits.sort((a,b)=>b.start-a.start);let boundary=source.length;for(const p of edits){if(p.end>boundary)throw Error('Overlapping map edits');source=source.slice(0,p.start)+p.text+source.slice(p.end);boundary=p.start;}return source;}
export function addDirections(source,route){
 const branch=config.routes[route],members=config.hubs[route];if(!branch&&!members)return source;
 if(source.includes('data-kd-version='))throw Error('Fresh full build required for directions');
 const nodes=parseHTML(source),edits=[];let section;
 if(branch){
  const map=config.maps[branch];if(!map)throw Error('Missing verified map '+route);
  const images=nodes.filter(n=>n.tag==='img'&&n.attrs['data-media-role']==='map');if(images.length!==1)throw Error('Expected one original map '+route);
  let figure=images[0].parent;while(figure&&figure.tag!=='figure')figure=figure.parent;if(!figure)throw Error('Missing map figure '+route);
  section='<section class="kd-directions" id="branch-directions" aria-labelledby="branch-directions-title" data-kd-version="'+config.date+'" data-kd-branch="'+esc(branch)+'"><h2 id="branch-directions-title">찾아오시는 길</h2>'+map.html+'</section>';
  edits.push({start:figure.end,end:figure.end,text:section});
 }else{
  const options=members.map(p=>'<option value="'+esc(config.maps[p].entry)+'">'+esc(config.maps[p].branch)+' · '+esc(p.split('/')[2])+'</option>').join('');
  section='<section class="kd-directions" id="branch-directions" aria-labelledby="branch-directions-title" data-kd-version="'+config.date+'" data-kd-hub><h2 id="branch-directions-title">지점별 찾아오시는 길</h2><p class="kd-intro">방문할 지점을 선택하면 위치 지도와 주변 교통편을 이 페이지에서 확인할 수 있습니다.</p><label for="kd-branch-select">위치를 확인할 지점</label><select id="kd-branch-select" data-kd-select><option value="">지점을 선택해 주세요</option>'+options+'</select><div data-kd-result aria-live="polite"></div><noscript><p>아래 지점 소개에서 주소와 지도 이미지를 확인해 주세요.</p></noscript></section>';
  const main=nodes.find(n=>n.tag==='main');if(!main)throw Error('Missing main '+route);edits.push({start:main.closeStart,end:main.closeStart,text:section});
 }
 const toc=nodes.find(n=>n.tag==='nav'&&['페이지 목차','지점 탐색 목차'].includes(n.attrs['aria-label']));
 if(toc)edits.push({start:toc.closeStart,end:toc.closeStart,text:'<a data-kd-toc href="#branch-directions">찾아오시는 길</a>'});
 const head=nodes.find(n=>n.tag==='head');edits.push({start:head.closeStart,end:head.closeStart,text:'<link data-kd-style rel="stylesheet" href="'+config.css+'"><script data-kd-script defer src="'+config.js+'"></script>'});
 return patches(source,edits).replace(/("dateModified"\s*:\s*")[^"]*(")/g,'$1'+config.date+'$2');
}
const allRoutes=[...Object.keys(config.routes),...Object.keys(config.hubs)];
const selected=process.env.KAKAO_DIRECTIONS_ROUTES?JSON.parse(process.env.KAKAO_DIRECTIONS_ROUTES):allRoutes;
if(selected.some(r=>!allRoutes.includes(r)))throw Error('Unreviewed directions route');
const changed=new Set(selected);let pages=0;
let cursor=0,all=[...changed];await Promise.all(Array.from({length:10},async()=>{while(cursor<all.length){const route=all[cursor++],file=path.join(pub,route.slice(1),'index.html');const source=await fs.readFile(file,'utf8');await fs.writeFile(file,addDirections(source,route));pages++;}}));
const sitemapFiles=['sitemap.xml',...(await fs.readdir(path.join(pub,'sitemaps'))).filter(n=>n.endsWith('.xml')&&!n.startsWith('index')).map(n=>'sitemaps/'+n)];
for(const name of sitemapFiles){const file=path.join(pub,name);let xml=await fs.readFile(file,'utf8');xml=xml.replace(/<url>[\s\S]*?<\/url>/g,block=>{const loc=block.match(/<loc>([^<]+)<\/loc>/);if(!loc||!changed.has(decodeURIComponent(new URL(loc[1]).pathname)))return block;return block.replace(/<lastmod>[^<]*<\/lastmod>/,'<lastmod>'+config.date+'</lastmod>');});await fs.writeFile(file,xml);}
console.log(JSON.stringify({pages,details:Object.keys(config.routes).length,branches:Object.keys(config.maps).length,hubs:Object.keys(config.hubs).length,assets:Object.keys(config.assets).length}));
