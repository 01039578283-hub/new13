/** Publish an explicit static-file allowlist, never the source-project root. */
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.dirname(fileURLToPath(import.meta.url));
const output=path.resolve(root,'public');
const marker=path.join(root,'.smallclass-public-output.json');
const owner='smallclass-static-public-output-v1';
if(path.dirname(output)!==root || path.basename(output)!=='public')throw Error('Unsafe output path');
if(fs.existsSync(output)){
  if(fs.lstatSync(output).isSymbolicLink())throw Error('Output must not be a symlink');
  if(!fs.existsSync(marker) || JSON.parse(fs.readFileSync(marker,'utf8')).owner!==owner)throw Error('Refusing to replace an unowned public directory');
  // Both the absolute target and ownership are checked before recursive cleanup.
  fs.rmSync(output,{recursive:true});
}
fs.mkdirSync(output);
fs.writeFileSync(marker,JSON.stringify({owner})+'\n');
const folders=['assets','교육정보','상담문의','전국학원','지점안내','학원소개'];
const rootNames=['index.html','404.html','robots.txt','llms.txt','sitemap.xml','rss.xml'];
for(const n of fs.readdirSync(root))if(/^(?:google[a-z\d]+|naver[a-z\d]+)\.html$/i.test(n))rootNames.push(n);
const mediaExtensions=new Set(['.css','.js','.png','.jpg','.jpeg','.webp','.gif','.svg','.ico','.avif','.woff','.woff2','.ttf','.otf']);
let files=0,bytes=0;
function copy(relative){
  const source=path.resolve(root,relative),target=path.resolve(output,relative);
  if(!source.startsWith(root+path.sep) || !target.startsWith(output+path.sep))throw Error('Invalid public path');
  if(fs.lstatSync(source).isSymbolicLink())throw Error('Symlinks are not public assets');
  fs.mkdirSync(path.dirname(target),{recursive:true});fs.copyFileSync(source,target);files++;bytes+=fs.statSync(target).size;
}
function visit(relative){
  for(const entry of fs.readdirSync(path.join(root,relative),{withFileTypes:true})){
    if(entry.name.startsWith('.'))continue;
    const name=path.join(relative,entry.name);
    if(entry.isSymbolicLink())throw Error('Symlinks are not public content');
    if(entry.isDirectory()){visit(name);continue;}
    const ext=path.extname(entry.name).toLowerCase();
    if(relative.split(path.sep)[0]==='assets'){
      if(mediaExtensions.has(ext))copy(name);
    }else if(entry.name==='index.html')copy(name);
  }
}
for(const n of rootNames)copy(n);
for(const n of folders)visit(n);
for(const name of ['tools','scripts','tmp','seo-descriptions.json','.env.local'])if(fs.existsSync(path.join(output,name)))throw Error('Non-public file included');
console.log(JSON.stringify({output:'public',files,bytes,sourceFilesPublished:false}));
