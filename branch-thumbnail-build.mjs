/** Apply fixed reviewed branch photos or explicitly authorized shared reference photos. */
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {parseHTML} from './image-order-build.mjs';
const root=path.dirname(fileURLToPath(import.meta.url));
const config=JSON.parse(fs.readFileSync(path.join(root,'branch-thumbnail-data.json'),'utf8'));
const output=path.join(root,'.public-release');
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;');
const inside=(n,parent)=>{for(let p=n.parent;p;p=p.parent)if(p===parent)return true;return false};
const set=(tag,key,value)=>{const re=new RegExp('\\s'+key+'(?:\\s*=\\s*(?:"[^"]*"|\'[^\']*\'|[^\\s>]+))?','i');return re.test(tag)?tag.replace(re,' '+key+'="'+esc(value)+'"'):tag.replace(/\s*\/?>$/,' '+key+'="'+esc(value)+'">')};
const local=v=>{try{return decodeURIComponent(new URL(v,config.domain).pathname)}catch{return ''}};
const rewrite=(s,edits)=>{edits.sort((a,b)=>b.start-a.start);let last=s.length;for(const e of edits){if(e.end>last)throw Error('Overlapping thumbnail edits');s=s.slice(0,e.start)+e.value+s.slice(e.end);last=e.start}return s};
const style='<style data-branch-photo-style="20261009">.branch-photo-bottom{padding-top:32px;padding-bottom:32px}.branch-photo-bottom figure{max-width:900px;margin:16px auto}.branch-photo-bottom img{display:block;width:100%;height:auto;max-height:none;object-fit:contain}.branch-photo-bottom figcaption{margin-top:12px;color:#4b5563}.branch-photo-bottom h2{margin-bottom:12px}</style>';
function schemaImage(data,photo,date){
 if(Array.isArray(data)){for(const d of data)schemaImage(d,photo,date);return}
 if(!data||typeof data!=='object')return;
 if(data['@graph'])schemaImage(data['@graph'],photo,date);
 const types=[data['@type']].flat();
 if(types.some(t=>['WebPage','Article'].includes(t))){
  data.primaryImageOfPage={'@type':'ImageObject',url:photo.url,contentUrl:photo.url,width:photo.width,height:photo.height,caption:photo.caption};
  if('image' in data)data.image=photo.url;
  if('thumbnailUrl' in data)data.thumbnailUrl=photo.url;
  if('dateModified' in data)data.dateModified=date;
 }
 // A shared reference illustrates the page, without claiming to depict this center.
 if(types.includes('EducationalOrganization')&&'image' in data){
  if(photo.role==='shared-reference')delete data.image;else data.image=[photo.url];
 }
}
export function transform(source,plan,photo){
 if(source.includes('data-branch-representative'))throw Error('Thumbnail stage requires fresh public output: '+plan.file);
 const nodes=parseHTML(source),head=nodes.find(n=>n.tag==='head'),main=nodes.find(n=>n.tag==='main');
 if(!head||!main)throw Error('Missing initial head/main: '+plan.file);
 const edits=[];
 const date=plan.date||config.date;
 const shared=photo.role==='shared-reference';
 const note=shared?'<p data-branch-photo-reference>'+esc('공용 자료의 사진으로, '+plan.branch+'의 실제 공간과 다를 수 있습니다.')+'</p>':'';
 const metadata=nodes.filter(n=>n.tag==='meta'&&inside(n,head)&&(/^(?:og:image)(?::.*)?$/.test(n.attrs.property||'')||/^twitter:image(?::.*)?$/.test(n.attrs.name||'')||/^twitter:image(?::.*)?$/.test(n.attrs.property||'')));
 for(const n of metadata)edits.push({start:n.start,end:n.end,value:''});
 const type=photo.path.endsWith('.png')?'image/png':photo.path.endsWith('.webp')?'image/webp':'image/jpeg';
 const meta='<meta property="og:image" content="'+esc(photo.url)+'"><meta property="og:image:width" content="'+photo.width+'"><meta property="og:image:height" content="'+photo.height+'"><meta property="og:image:type" content="'+type+'"><meta property="og:image:alt" content="'+esc(photo.caption)+'"><meta name="twitter:image" content="'+esc(photo.url)+'"><meta name="twitter:image:alt" content="'+esc(photo.caption)+'">';
 edits.push({start:head.closeStart,end:head.closeStart,value:meta+(plan.mode==='add-bottom'?style:'')});
 const images=nodes.filter(n=>n.tag==='img'&&inside(n,main));let removedHidden=0;
 for(const n of images){
  const p=local(n.attrs['data-preserved-src']||n.attrs.src||'');
  const generated=n.attrs['data-media-role']==='representative'&&'hidden' in n.attrs;
  const legacy='hidden' in n.attrs&&n.attrs['aria-hidden']==='true'&&n.attrs.width==='511'&&n.attrs.height==='511'&&/^\/assets\/branches\/[a-f0-9]+\.gif$/.test(p)&&!n.attrs.alt;
  if(generated||legacy){edits.push({start:n.start,end:n.end,value:''});removedHidden++}
 }
 if(plan.mode==='reuse-existing'){
  const n=images[plan.originalPosition];
  if(!n||local(n.attrs.src)!==photo.path||'hidden' in n.attrs)throw Error('Existing photo position changed: '+plan.file);
  let tag=source.slice(n.start,n.openEnd);
  for(const [k,v] of Object.entries({src:photo.path+'?v='+photo.sha256.slice(0,16),alt:photo.caption,width:photo.width,height:photo.height,'data-branch-representative':date}))tag=set(tag,k,v);
  if(shared)tag=set(tag,'style',(n.attrs.style||'')+';height:auto;aspect-ratio:'+photo.width+'/'+photo.height+';object-fit:contain;max-height:none');
  edits.push({start:n.start,end:n.openEnd,value:tag});
  let figure=n;while(figure.parent&&figure.tag!=='figure'&&figure.tag!=='main')figure=figure.parent;
  if(figure.tag==='figure'){
   const cap=nodes.find(c=>c.tag==='figcaption'&&inside(c,figure));
   if(cap){
    edits.push({start:cap.openEnd,end:cap.closeStart,value:esc(photo.caption)});
    if(note)edits.push({start:cap.end,end:cap.end,value:note});
   }else edits.push({start:figure.closeStart,end:figure.closeStart,value:'<figcaption data-branch-photo-caption>'+esc(photo.caption)+'</figcaption>'+note});
  }
 }else if(plan.mode==='add-bottom'){
  if(images.some(n=>local(n.attrs.src)===photo.path))throw Error('Refusing duplicate existing visible photo: '+plan.file);
  const src=photo.path+'?v='+photo.sha256.slice(0,16);
  const heading=shared?'학습 공간 참고 사진':plan.branch+' 사진';
  const block='<section class="branch-photo-bottom container" data-branch-photo-bottom><h2>'+esc(heading)+'</h2><figure><a class="quality-zoom-link" data-quality-zoom data-original-width="'+photo.width+'" data-original-height="'+photo.height+'" href="'+esc(src)+'" target="_blank" rel="noopener" aria-label="'+esc(photo.caption+' 확대 보기')+'"><img data-branch-representative="'+date+'" src="'+esc(src)+'" alt="'+esc(photo.caption)+'" width="'+photo.width+'" height="'+photo.height+'" loading="lazy" decoding="async"></a><figcaption>'+esc(photo.caption)+'</figcaption>'+note+'</figure></section>';
  edits.push({start:main.closeStart,end:main.closeStart,value:block});
 }else throw Error('Unreviewed placement mode');
 // parseHTML treats script blocks as raw tokens; read JSON-LD bodies explicitly.
 for(const match of source.matchAll(/<script\b[^>]*\btype\s*=\s*(?:"application\/ld\+json"|'application\/ld\+json')[^>]*>([\s\S]*?)<\/script\s*>/gi)){
  const raw=match[1],start=match.index+match[0].indexOf('>')+1,d=JSON.parse(raw);schemaImage(d,photo,date);const value=JSON.stringify(d).replaceAll('<','\\u003c');
  if(value!==raw)edits.push({start,end:start+raw.length,value});
 }
 return {html:rewrite(source,edits),removedHidden,edits:edits.map(e=>({start:e.start,end:e.end,before:source.slice(e.start,e.end),after:e.value}))};
}
async function build(){
for(const photo of Object.values(config.images)){
 const data=fs.readFileSync(path.join(output,photo.path.slice(1)));
 if(createHash('sha256').update(data).digest('hex')!==photo.sha256)throw Error('Reviewed photo changed: '+photo.path);
 if(!(photo.width>150&&photo.height>150&&data.length>=5000&&photo.width/photo.height<=3))throw Error('Photo fails official dimensions: '+photo.path);
}
const report={changed:0,reused:0,addedBottom:0,hiddenRemovalPages:0,hiddenElementsRemoved:0,uniqueImages:Object.keys(config.images).length};
let cursor=0;const entries=Object.entries(config.pages);
const audit=process.env.BRANCH_THUMBNAIL_AUDIT?{}:null;
await Promise.all(Array.from({length:12},async()=>{while(cursor<entries.length){
 const [route,plan]=entries[cursor++],file=path.join(output,plan.file);const before=await fs.promises.readFile(file,'utf8');
 const result=transform(before,plan,config.images[plan.imagePath]);await fs.promises.writeFile(file,result.html);
 report.changed++;if(plan.mode==='reuse-existing')report.reused++;else report.addedBottom++;
 if(result.removedHidden)report.hiddenRemovalPages++;report.hiddenElementsRemoved+=result.removedHidden;
 if(audit)audit[route]={beforeSha256:createHash('sha256').update(before).digest('hex'),afterSha256:createHash('sha256').update(result.html).digest('hex'),edits:result.edits};
}}));
const sitemapFile=path.join(output,'sitemap.xml');let sitemap=fs.readFileSync(sitemapFile,'utf8');
sitemap=sitemap.replace(/<url>\s*<loc>([^<]+)<\/loc>([\s\S]*?)<\/url>/g,(whole,url,tail)=>{
 if(!config.pages[local(url)])return whole;
 const lm='<lastmod>'+(config.pages[local(url)].date||config.date)+'</lastmod>';return '<url><loc>'+url+'</loc>'+(tail.includes('<lastmod>')?tail.replace(/<lastmod>[^<]*<\/lastmod>/,lm):lm+tail)+'</url>';
});fs.writeFileSync(sitemapFile,sitemap);
if(audit)fs.writeFileSync(process.env.BRANCH_THUMBNAIL_AUDIT,JSON.stringify(audit));
console.log(JSON.stringify(report));
}
if(path.resolve(process.argv[1]||'')===fileURLToPath(import.meta.url))await build();
