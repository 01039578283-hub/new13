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
// Only these reviewed generated sitemap paths may remain from an earlier build.
const discoveryConfig=JSON.parse(fs.readFileSync(path.join(root,'search-discovery-config.json'),'utf8'));
for(const name of discoveryConfig.generatedFiles){
  if(!/^sitemaps\/[a-z0-9-]+\.xml$/.test(name))throw Error('Unsafe generated path '+name);
  selected.add(name);
}
const directionsConfig=JSON.parse(fs.readFileSync(path.join(root,'kakao-directions-data.json'),'utf8'));
for(const name of Object.keys(directionsConfig.assets)){
  if(!/^assets\/kakao-directions\/[a-z]+-[a-f0-9]{16}\.(?:html|css|js|json)$/.test(name))throw Error('Unsafe directions output '+name);
  selected.add(name);
}
const contentConfig=JSON.parse(fs.readFileSync(path.join(root,'search-content-data.json'),'utf8'));
if(!/^[a-f0-9]{32}\.txt$/.test(contentConfig.indexNow.file)||contentConfig.indexNow.file!==contentConfig.indexNow.key+'.txt')throw Error('Unsafe IndexNow verification output');
selected.add(contentConfig.indexNow.file);
for(const [name,content] of Object.entries(contentConfig.generatedAssets)){
  if(!/^assets\/quality\/[a-f0-9]{20}\.css$/.test(name)||!name.includes(createHash('sha256').update(content).digest('hex').slice(0,20)))throw Error('Unsafe generated content asset '+name);
  selected.add(name);
}
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
function reviewedBytes(bytes,hash,name) {
  if(digest(bytes)===hash)return true;
  // Git converts working-tree CRLF to LF on Linux. Accept only that reversible
  // text-file representation change; binary assets still require exact bytes.
  if(!/\.(?:html|css|js|json|xml|txt|svg|webmanifest)$/i.test(name))return false;
  const lf=bytes.toString('utf8').replaceAll('\r\n','\n');
  if(manifest.textSha256?.[name] && digest(Buffer.from(lf))===manifest.textSha256[name])return true;
  return digest(Buffer.from(lf))===hash||digest(Buffer.from(lf.replaceAll('\n','\r\n')))===hash;
}
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
    if(!reviewedBytes(bytes,hash,name))throw Error('Reviewed file changed; refresh release manifest: '+name);
    await fs.promises.mkdir(path.dirname(dest),{recursive:true});
    // Git serves LF text on Linux. Normalize derived text before calculating
    // asset cache keys so Windows verification reproduces production URLs.
    const published=/\.(?:html|css|js|json|xml|txt|svg|webmanifest)$/i.test(name)?Buffer.from(bytes.toString('utf8').replaceAll('\r\n','\n')):bytes;
    await fs.promises.writeFile(dest,published);
  }
}));
console.log(JSON.stringify({publicFiles:files.length,sitemapPages:manifest.sitemapPages,output:'.public-release',privateSourcesIncluded:false}));
