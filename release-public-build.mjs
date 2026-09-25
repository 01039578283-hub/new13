/** Reproduce only the reviewed public snapshot. No authoring files are served. */
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url));
const output=path.join(root,'.public-release');
const manifest=JSON.parse(fs.readFileSync(path.join(root,'release-public-manifest.json'),'utf8'));
const files=Object.entries(manifest.files);
const selected=new Set(files.map(([name])=>name));
if(fs.existsSync(output)) {
  function inspect(dir) {
    for(const entry of fs.readdirSync(dir,{withFileTypes:true})) {
      if(entry.isSymbolicLink()) throw Error('Output symlink is not allowed');
      const full=path.join(dir,entry.name);
      if(entry.isDirectory())inspect(full);
      else if(!selected.has(path.relative(output,full).replaceAll('\\','/')))throw Error('Unexpected existing output '+full);
    }
  }
  inspect(output);
}
let cursor=0;
await Promise.all(Array.from({length:12},async()=>{
  while(cursor<files.length){
    const [name,hash]=files[cursor++];
    const input=path.resolve(root,name),dest=path.resolve(output,name);
    if(!input.startsWith(root+path.sep)||!dest.startsWith(output+path.sep)||name.split('/').some(p=>p.startsWith('.')))throw Error('Unsafe public path '+name);
    if(fs.lstatSync(input).isSymbolicLink())throw Error('Source symlink '+name);
    const bytes=await fs.promises.readFile(input);
    if(createHash('sha256').update(bytes).digest('hex')!==hash)throw Error('Reviewed file changed; refresh release manifest: '+name);
    await fs.promises.mkdir(path.dirname(dest),{recursive:true});
    await fs.promises.writeFile(dest,bytes);
  }
}));
console.log(JSON.stringify({publicFiles:files.length,sitemapPages:manifest.sitemapPages,output:'.public-release',privateSourcesIncluded:false}));
