/** Search descriptions only. Page copy, URLs, dates and images are not edited. */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const config = JSON.parse(fs.readFileSync(path.join(root, 'seo-descriptions.json'), 'utf8'));
const decode = (s) => s.replace(/&(?:amp|quot|apos|lt|gt|#39|#x[\da-f]+|#\d+);/gi, (v) => {
  const named = { '&amp;':'&', '&quot;':'"', '&apos;':"'", '&#39;':"'", '&lt;':'<', '&gt;':'>' };
  if (named[v]) return named[v];
  const code = v.toLowerCase().startsWith('&#x') ? parseInt(v.slice(3,-1),16) : parseInt(v.slice(2,-1),10);
  return Number.isFinite(code) ? String.fromCodePoint(code) : v;
});
const escape = (s) => s.replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const keyFor = (u) => decodeURIComponent(new URL(u, 'https://example.invalid').pathname).replace(/\/$/, '') || '/';
const attrs = (tag) => Object.fromEntries([...tag.matchAll(/([\w:-]+)\s*=\s*(["'])([\s\S]*?)\2/g)].map((m) => [m[1].toLowerCase(), decode(m[3])]));
const tagPattern = /<meta\b[^>]*>/gi;
const fields = new Set(['description', 'og:description', 'twitter:description']);

function canonical(html) {
  for (const tag of html.matchAll(/<link\b[^>]*>/gi)) { const a=attrs(tag[0]); if(a.rel==='canonical') return a.href; }
  return '';
}
function withoutDescriptions(html) {
  // This comparison intentionally covers the entire body byte for byte.
  return html.replace(/<head\b[^>]*>[\s\S]*?<\/head>/i,(head)=>head.replace(tagPattern,(tag)=>{
    const a=attrs(tag);return fields.has((a.name||a.property||'').toLowerCase())?'':tag;
  }));
}
export function transform(html) {
  const headMatch=html.match(/<head\b[^>]*>[\s\S]*?<\/head>/i);
  if(!headMatch)return {html,skip:true};
  const head=headMatch[0], url=canonical(head);
  if(!url || /<meta\b(?=[^>]*(?:name|property)=["']robots["'])(?=[^>]*noindex)[^>]*>/i.test(head))return {html,skip:true};
  const key=keyFor(url), entry=config.pages[key];
  let old='';for(const tag of head.matchAll(tagPattern)){const a=attrs(tag[0]);if(a.name==='description'){old=a.content||'';break;}}
  const description=entry?.description ?? old;
  if(!description || [...description].length>80) throw new Error(`Review description (1–80 characters): ${url}`);
  if(entry && !entry.sources.includes(old) && old!==description) throw new Error(`Description source changed; review seo-descriptions.json: ${url}`);
  const seen=new Set();
  const updated=head.replace(tagPattern,(tag)=>{
    const a=attrs(tag), field=(a.name||a.property||'').toLowerCase();
    if(!fields.has(field))return tag;
    if(seen.has(field))return '';
    seen.add(field);
    return tag.replace(/\bcontent\s*=\s*(["'])[\s\S]*?\1/i,`content="${escape(description)}"`);
  });
  if(!seen.has('description') || !seen.has('og:description')) throw new Error(`Missing required description tag: ${url}`);
  const next=html.replace(head,updated);
  if(withoutDescriptions(next)!==withoutDescriptions(html))throw new Error(`Out-of-scope modification: ${url}`);
  return {html:next,changed:next!==html,key};
}

if(process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const output=path.resolve(root,process.argv.find((a)=>a.startsWith('--root='))?.slice(7)||'.');
  const check=process.argv.includes('--check');
  const skip=new Set(['node_modules','assets','tools','scripts','tmp','work','reports','outputs','records','dist','public','generated_article_txt','__pycache__']);
  let count=0,changed=0,skipped=0;const errors=[];
  function processFile(file){
    try{
      const original=fs.readFileSync(file,'utf8');const result=transform(original);
      if(result.skip){skipped++;return;}count++;
      if(result.changed){changed++;if(!check)fs.writeFileSync(file,result.html,'utf8');}
    }catch(e){errors.push({file,message:e.message});}
  }
  function visit(dir){
    for(const e of fs.readdirSync(dir,{withFileTypes:true})){
      if(e.name.startsWith('.'))continue;
      const file=path.join(dir,e.name);
      if(e.isDirectory()){if(!skip.has(e.name))visit(file);continue;}
      if(!e.name.endsWith('.html'))continue;
      processFile(file);
    }
  }
  const fileList=process.argv.find(a=>a.startsWith('--files-file='));
  if(fileList){
    for(const relative of JSON.parse(fs.readFileSync(fileList.slice(13),'utf8'))){
      const file=path.resolve(output,relative),inside=path.relative(output,file);
      if(inside.startsWith('..') || path.isAbsolute(inside) || !file.endsWith('.html'))throw new Error('Invalid scoped HTML file');
      processFile(file);
    }
  }else visit(output);
  console.log(JSON.stringify({pages:count,changed,skipped,check,errors}));
  if(errors.length || (check&&changed))process.exitCode=1;
}
