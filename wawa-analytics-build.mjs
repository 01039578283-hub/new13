/** Final HTML pass: content, URLs, metadata and layout remain byte-for-byte intact. */
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
const site = process.argv[2];
if(!/^wawa-\d{2}$/.test(site || '')) throw Error('A registered public site ID is required');
const root=path.resolve(process.argv[3] || '.');
const check=process.argv.includes('--check');
const tag=`<script defer src="https://wawa-visit-collector.clean-peach-8202.chatgpt.site/tracker.js" data-site="${site}" crossorigin="anonymous" referrerpolicy="no-referrer"></script>`;
const skip=new Set(['node_modules','tools','scripts','tmp','temp','audit','audit-output','reports','backups','backup','source','src','drizzle','test','tests']);
let documents=0, changed=0, present=0, skipped=0;
const errors=[];
const allowed=f=>!f.split(/[\\/]/).some(p=>p.startsWith('.')||skip.has(p));
async function files() {
  if(root===process.cwd() && fs.existsSync(path.join(root,'.git'))) {
    const names=execFileSync('git',['ls-files','-z','--','*.html'],{cwd:root,maxBuffer:32*1024*1024,encoding:'utf8'}).split('\0').filter(Boolean);
    return names.filter(allowed).map(f=>path.join(root,f));
  }
  const queue=[root], result=[];
  while(queue.length) {
    const dirs=queue.splice(0,32);
    await Promise.all(dirs.map(async dir=>{
      for(const e of await fs.promises.readdir(dir,{withFileTypes:true})) {
        if(e.isSymbolicLink()) continue;
        if(e.isDirectory()) {if(!e.name.startsWith('.')&&!skip.has(e.name)) queue.push(path.join(dir,e.name));}
        else if(e.name.endsWith('.html')) result.push(path.join(dir,e.name));
      }
    }));
  }
  return result;
}
async function update(file) {
    const original=await fs.promises.readFile(file), html=original.toString('utf8');
    if(!Buffer.from(html).equals(original)) {errors.push(`Non-UTF8 HTML: ${path.relative(root,file)}`);return;}
    if(!/<html[\s>]/i.test(html)) {skipped++;return;}
    if(!/<\/head\s*>/i.test(html)) {errors.push(`Missing head: ${path.relative(root,file)}`);return;}
    documents++;
    const tags=html.match(/<script\b[^>]*wawa-visit-collector[^>]*>[\s\S]*?<\/script>/gi)||[];
    if(tags.length) {
      if(tags.length!==1 || tags[0]!==tag) errors.push(`Unexpected tracker: ${path.relative(root,file)}`);
      else present++;
      return;
    }
    const next=html.replace(/<\/head\s*>/i,m=>tag+m);
    if(next.replace(tag,'')!==html) throw Error('Content preservation failed');
    if(!check) await fs.promises.writeFile(file,next,'utf8');
    changed++;
}
const pages=await files();
for(let i=0;i<pages.length;i+=32) await Promise.all(pages.slice(i,i+32).map(update));
if(errors.length || !documents) throw Error(JSON.stringify({site,errors:errors.slice(0,20),documents}));
console.log(JSON.stringify({site,mode:check?'check':'installed',documents,changed,present,skipped,bytesAddedPerPage:Buffer.byteLength(tag),contentPreserved:true}));
