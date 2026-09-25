/** Reviewed content summaries only: no body copy, URLs, dates or index policy changes. */
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url));
const config=JSON.parse(fs.readFileSync(path.join(root,'seo-descriptions.json'),'utf8').replace(/^\uFEFF/,''));
const fields=new Set(['description','og:description','twitter:description']);
const pageTypes=new Set(['WebPage','CollectionPage','Article','BlogPosting','AboutPage','ContactPage']);
const decode=s=>s.replace(/&(?:amp|quot|apos|lt|gt|#39|#x[\da-f]+|#\d+);/gi,v=>{
  const named={'&amp;':'&','&quot;':'"','&apos;':"'",'&#39;':"'",'&lt;':'<','&gt;':'>'};
  if(named[v])return named[v];
  const n=v.toLowerCase().startsWith('&#x')?parseInt(v.slice(3,-1),16):parseInt(v.slice(2,-1),10);
  return Number.isFinite(n)?String.fromCodePoint(n):v;
});
const escape=s=>s.replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const attrs=t=>Object.fromEntries([...t.matchAll(/([\w:-]+)\s*=\s*(["'])([\s\S]*?)\2/g)].map(m=>[m[1].toLowerCase(),decode(m[3])]));
const keyFor=u=>{try{return decodeURIComponent(new URL(u,'https://example.invalid').pathname).replace(/\/$/,'')||'/';}catch{return undefined;}};
const same=(a,b)=>{try{const x=new URL(a,b),y=new URL(b);return x.origin===y.origin&&keyFor(x.href)===keyFor(y.href);}catch{return false;}};
const scripts=/(<script\b[^>]*type=["']application\/ld\+json["'][^>]*>)([\s\S]*?)(<\/script>)/gi;
function isPage(n,canonical){
  const types=Array.isArray(n['@type'])?n['@type']:[n['@type']];
  const ids=[n.url,n['@id'],typeof n.mainEntityOfPage==='string'?n.mainEntityOfPage:n.mainEntityOfPage?.['@id']];
  return types.some(t=>pageTypes.has(t))&&ids.some(id=>typeof id==='string'&&same(id,canonical));
}
function visit(n,canonical,fn){
  if(!n||typeof n!=='object')return;
  if(Array.isArray(n)){n.forEach(v=>visit(v,canonical,fn));return;}
  if(isPage(n,canonical))fn(n);
  Object.values(n).forEach(v=>visit(v,canonical,fn));
}
function protectedHTML(html,canonical){
  return html.replace(/<head\b[^>]*>[\s\S]*?<\/head>/i,h=>h.replace(/<meta\b[^>]*>/gi,t=>{
    const a=attrs(t);return fields.has((a.name||a.property||'').toLowerCase())?'':t;
  })).replace(scripts,(_,start,body,end)=>{
    const data=JSON.parse(body);visit(data,canonical,n=>{delete n.description;});
    return start+JSON.stringify(data)+end;
  });
}
export function transform(html){
  const head=html.match(/<head\b[^>]*>[\s\S]*?<\/head>/i)?.[0];
  if(!head)return {html,skip:true};
  const tags=[...head.matchAll(/<meta\b[^>]*>/gi)].map(m=>attrs(m[0]));
  if(tags.some(a=>['robots','googlebot','naverbot','yeti'].includes((a.name||'').toLowerCase())&&/noindex/i.test(a.content||'')))return {html,skip:true};
  const canonical=[...head.matchAll(/<link\b[^>]*>/gi)].map(m=>attrs(m[0])).find(a=>a.rel==='canonical')?.href;
  if(!canonical)return {html,skip:true};
  const key=keyFor(canonical),entry=config.pages[key];
  if(!entry)return {html,skip:true,key};
  const description=entry.description,old=tags.find(a=>a.name==='description')?.content||'';
  if(!description||[...description].length>80||!/[.!?]$/.test(description))throw Error(`Review complete description (1–80 chars): ${canonical}`);
  if(old&&old!==description&&!entry.sources.includes(old))throw Error(`Description source changed; editorial review required: ${canonical}`);
  const seen=new Set();
  let nextHead=head.replace(/<meta\b[^>]*>/gi,t=>{
    const a=attrs(t),field=(a.name||a.property||'').toLowerCase();
    if(!fields.has(field))return t;
    if(seen.has(field))return '';seen.add(field);
    if(a.content===description)return t;
    if(/\bcontent\s*=\s*(["'])[\s\S]*?\1/i.test(t))return t.replace(/\bcontent\s*=\s*(["'])[\s\S]*?\1/i,`content="${escape(description)}"`);
    return `<meta ${field==='og:description'?'property':'name'}="${field}" content="${escape(description)}">`;
  });
  // No extra whitespace is introduced, so protected body/head bytes are identical.
  const missing=[...fields].filter(f=>!seen.has(f)).map(f=>`<meta ${f==='og:description'?'property':'name'}="${f}" content="${escape(description)}">`).join('');
  nextHead=nextHead.replace(/<\/head>/i,missing+'</head>');
  let nodes=0;
  const next=html.replace(head,nextHead).replace(scripts,(all,start,body,end)=>{
    const data=JSON.parse(body);let changed=false;
    visit(data,canonical,n=>{if(n.description!==description){n.description=description;changed=true;nodes++;}});
    return changed?start+JSON.stringify(data).replaceAll('<','\\u003c')+end:all;
  });
  if(protectedHTML(next,canonical)!==protectedHTML(html,canonical))throw Error(`Out-of-scope change: ${canonical}`);
  return {html:next,changed:next!==html,key,changedNodes:nodes};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const output=path.resolve(root,process.argv.find(a=>a.startsWith('--root='))?.slice(7)||'.');
  const check=process.argv.includes('--check');
  const skip=new Set(['node_modules','assets','tools','scripts','tmp','work','reports','outputs','records','dist','public','generated_article_txt','__pycache__']);
  let pages=0,changed=0,skipped=0,nodes=0;const errors=[];
  function processFile(file){try{
    const before=fs.readFileSync(file,'utf8'),r=transform(before);
    if(r.skip){skipped++;return;}pages++;nodes+=r.changedNodes||0;
    if(r.changed){changed++;if(!check)fs.writeFileSync(file,r.html,'utf8');}
  }catch(e){errors.push({file,message:e.message});}}
  function walk(dir){for(const e of fs.readdirSync(dir,{withFileTypes:true})){
    if(e.name.startsWith('.'))continue;const p=path.join(dir,e.name);
    if(e.isDirectory()){if(!skip.has(e.name))walk(p);}else if(e.name.endsWith('.html'))processFile(p);
  }}
  const list=process.argv.find(a=>a.startsWith('--files-file='));
  if(list){for(const rel of JSON.parse(fs.readFileSync(list.slice(13),'utf8'))){
    const p=path.resolve(output,rel),inside=path.relative(output,p);
    if(inside.startsWith('..')||path.isAbsolute(inside)||!p.endsWith('.html'))throw Error('Unsafe scoped HTML path');processFile(p);
  }}else walk(output);
  console.log(JSON.stringify({pages,changed,skipped,changedNodes:nodes,check,errors}));
  if(errors.length||(check&&changed))process.exitCode=1;
}
