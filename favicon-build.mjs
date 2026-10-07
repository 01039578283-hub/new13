/** Run the unchanged public pipeline, then normalize only favicon links. */
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const root=path.dirname(fileURLToPath(import.meta.url));
const config=JSON.parse(fs.readFileSync(path.join(root,'favicon-config.json'),'utf8'));
const output=path.resolve(root,config.outputDirectory);
if(!output.startsWith(root+path.sep)||config.outputDirectory!=='.public-release')throw Error('Unreviewed public output');
if(!process.argv.includes('--apply-only')){
 for(const command of config.previousBuildCommand.split(/\s*&&\s*/)){
  const args=command.trim().split(/\s+/);if(args.shift()!=='node')throw Error('Unreviewed build command');
  const result=spawnSync(process.execPath,args,{cwd:root,stdio:'inherit'});if(result.error)throw result.error;if(result.status!==0)process.exit(result.status??1);
 }
}
const isIcon=tag=>{const rel=tag.match(/\brel\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i);return /^(?:icon|shortcut icon|apple-touch-icon|apple-touch-icon-precomposed)$/i.test((rel?.[1]??rel?.[2]??rel?.[3]??'').trim())};
const strip=s=>s.replace(/<link\b[^>]*>/gi,t=>isIcon(t)?'':t).replace(/\s+/g,' ');
const links='<link rel="icon" type="image/png" sizes="96x96" href="/assets/site-favicon.png"><link rel="apple-touch-icon" sizes="180x180" href="/assets/site-apple-touch-icon.png">';
for(const [name,expected] of Object.entries(config.assets)){
 const asset=path.join(root,name),bytes=fs.readFileSync(asset);if(createHash('sha256').update(bytes).digest('hex')!==expected)throw Error('Favicon integrity mismatch '+name);
 const dest=path.join(output,name);if(!fs.existsSync(dest))throw Error('Favicon is missing from public output '+name);
 if(!fs.readFileSync(dest).equals(bytes))throw Error('Public favicon differs '+name);
}
let pages=0,changed=0,added=0;
const files=[];function walk(dir){for(const e of fs.readdirSync(dir,{withFileTypes:true})){const p=path.join(dir,e.name);if(e.isSymbolicLink())throw Error('Unexpected output link');if(e.isDirectory())walk(p);else if(e.name.endsWith('.html'))files.push(p)}}walk(output);
let cursor=0;
await Promise.all(Array.from({length:12},async()=>{while(cursor<files.length){const f=files[cursor++],before=await fs.promises.readFile(f,'utf8');if(!/<html\b/i.test(before)||!/<\/head>/i.test(before))continue;
 const headEnd=before.search(/<\/head>/i),head=before.slice(0,headEnd),tail=before.slice(headEnd);let count=0;
 const clean=head.replace(/<link\b[^>]*>/gi,t=>isIcon(t)?(count++,''):t);const after=clean+links+tail;
 if(strip(before)!==strip(after))throw Error('Non-favicon content changed '+f);
 pages++;if(!count)added++;if(before!==after){await fs.promises.writeFile(f,after);changed++}
}}));
console.log(JSON.stringify({faviconSite:config.domain,pages,changed,previouslyMissing:added,onlyFaviconTagsChanged:true,bodyAndSeoPreserved:true}));
