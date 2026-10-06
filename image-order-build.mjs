/** Keep the existing page text and map annotations; normalize only media markup. */
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';

const VOID=new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);
const decode=s=>(s||'').replace(/&(?:amp|quot|apos|lt|gt|#39|#x[\da-f]+|#\d+);/gi,v=>{
  const named={'&amp;':'&','&quot;':'"','&apos;':"'",'&#39;':"'",'&lt;':'<','&gt;':'>'};
  return named[v]??String.fromCodePoint(v.toLowerCase().startsWith('&#x')?parseInt(v.slice(3,-1),16):parseInt(v.slice(2,-1),10));
});
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;');
const plain=s=>decode(s.replace(/<[^>]+>/g,' ')).replace(/\s+/g,' ').trim();
const attrs=tag=>Object.fromEntries([...tag.replace(/^<\/?[\w:-]+/,'').matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)].map(m=>[m[1].toLowerCase(),decode(m[2]??m[3]??m[4]??'')]));
export function parseHTML(source){
  const nodes=[],stack=[];
  const re=/<!--[\s\S]*?-->|<(script|style)\b(?:"[^"]*"|'[^']*'|[^'">])*?>[\s\S]*?<\/\1\s*>|<\/?[a-zA-Z][\w:-]*(?:"[^"]*"|'[^']*'|[^'">])*?>/gi;
  for(const m of source.matchAll(re)){
    const token=m[0];if(token.startsWith('<!--'))continue;
    const tag=token.match(/^<\/?([\w:-]+)/)[1].toLowerCase();
    if(token.startsWith('</')){
      const at=stack.findLastIndex(n=>n.tag===tag);if(at<0)continue;
      for(const n of stack.splice(at)){n.closeStart=m.index;n.end=m.index+token.length;}
    }else{
      const n={tag,start:m.index,openEnd:m.index+token.length,end:m.index+token.length,closeStart:m.index+token.length,attrs:attrs(token),parent:stack.at(-1),children:[]};
      n.parent?.children.push(n);nodes.push(n);
      if(m[1]){n.raw=true;continue;}
      if(!VOID.has(tag)&&!token.endsWith('/>'))stack.push(n);
    }
  }
  return nodes;
}
const ancestors=n=>{const a=[];for(let p=n.parent;p;p=p.parent)a.push(p);return a;};
export function imageRole(n){
  let own=(n.attrs.src+' '+(n.attrs.alt||'')+' '+(n.attrs.class||'')).toLowerCase();try{own=decodeURIComponent(own)}catch{}
  if(/representative|\/rep-|대표/.test(own)||n.attrs['data-image-role']==='representative')return 'representative';
  if(/지도|\/maps?\/|\/map\/|map[-_\s.]|[-_\s]map\b/.test(own))return 'map';
  if(Number(n.attrs.height)>3000||/\/body\/|\/common\/(?:local|seoul|3957)|bodyimage|poster/.test(own)||(/본문/.test(own)&&!/학습 공간|학습 환경|교실|사진|시설/.test(own)))return 'body';
  return 'other';
}
export function targetPage(name){
  const parts=decodeURIComponent(name).split('/').filter(Boolean);if(parts.at(-1)==='index.html')parts.pop();
  if(parts[0]==='지점안내')return parts.length>=3;
  return ['과목별학원','전국학원','전국센터'].includes(parts[0])&&parts.length>=3;
}
function blockFor(image){
  let block=image;
  for(let p=image.parent;p;p=p.parent){
    if(['figure','picture','a'].includes(p.tag))block=p;
    if(['section','main','details'].includes(p.tag))break;
    if(p.tag==='figure')break;
  }
  return block;
}
function setTagAttribute(tag,key,value){
  const re=new RegExp('\\s'+key+'(?:\\s*=\\s*(?:"[^"]*"|\'[^\']*\'|[^\\s>]+))?','i');
  return re.test(tag)?tag.replace(re,' '+key+'="'+esc(value)+'"'):tag.replace(/\s*\/?>$/,' '+key+'="'+esc(value)+'">');
}
const markImage=(chunk,role)=>chunk.replace(/<img\b(?:"[^"]*"|'[^']*'|[^'">])*?>/i,tag=>setTagAttribute(tag,'data-media-role',role));
const STYLE='<style data-image-order="20261006">.ordered-image-sequence{display:flex!important;flex-direction:column;gap:24px;grid-column:1/-1;min-width:0;width:100%;max-width:918px;margin:0 auto}.ordered-image-sequence>figure,.ordered-image-sequence>picture{width:100%;min-width:0;max-width:100%;margin-left:0;margin-right:0}.ordered-image-sequence [data-media-role="body"]{display:block!important;width:100%!important;height:auto!important;max-height:none!important;object-fit:contain!important}.ordered-image-sequence [data-media-role="map"]{max-width:100%}.ordered-image-sequence [hidden]{display:none!important}</style>';
export function mediaContext(root,manifest){
  const names=Object.keys(manifest.files);
  const representatives=names.filter(n=>/\.(?:gif|webp|png|jpe?g)$/i.test(n)&&/(?:\/representative\/|\/rep-)/.test(n)).sort();
  if(!representatives.length)throw Error('No reviewed representative assets');
  const reviewFile=path.join(root,'image-order-review.json');
  const review=fs.existsSync(reviewFile)?JSON.parse(fs.readFileSync(reviewFile,'utf8')):{missingMaps:[]};
  return {root,names:new Set(names),representatives,missingMaps:new Set(review.missingMaps)};
}
export function normalizeMediaHTML(source,name,context){
  if(!targetPage(name))return {html:source,changed:false};
  if(source.includes('data-image-order="sequence-v1"'))return {html:source,changed:false};
  const nodes=parseHTML(source),images=nodes.filter(n=>n.tag==='img'&&ancestors(n).some(p=>p.tag==='main'));
  const bodies=images.filter(n=>imageRole(n)==='body'),maps=images.filter(n=>imageRole(n)==='map');
  if(bodies.length>1||maps.length>1)throw Error('Multiple body/map images need review: '+name+' '+bodies.length+'/'+maps.length);
  if(!maps.length&&!context.missingMaps.has(name))throw Error('Missing mapped image; refusing an unrelated replacement: '+name);
  const body=bodies[0],map=maps[0],repImages=nodes.filter(n=>n.tag==='img'&&imageRole(n)==='representative');
  const h1=nodes.find(n=>n.tag==='h1'),label=h1?plain(source.slice(h1.openEnd,h1.closeStart)):'학습 안내';
  const selected=repImages[0];
  const pick=parseInt(createHash('sha256').update(name).digest('hex').slice(0,8),16)%context.representatives.length;
  const repSrc=selected?.attrs.src||'/'+context.representatives[pick];
  const rep='<img hidden aria-hidden="true" data-media-role="representative" src="'+esc(repSrc)+'" alt="'+esc(label+' 대표이미지')+'" width="'+(selected?.attrs.width||511)+'" height="'+(selected?.attrs.height||511)+'" style="display:none" loading="lazy" decoding="async">';
  let bodyHTML,addedBody=false;
  if(body)bodyHTML=markImage(source.slice(blockFor(body).start,blockFor(body).end),'body');
  else {
    const parts=decodeURIComponent(name).split('/');
    if(parts[0]!=='지점안내')throw Error('Body region mapping requires review: '+name);
    const asset='assets/centers/common/'+(parts[1]==='서울'?'seoul':'local')+'6839.webp';
    if(!context.names.has(asset))throw Error('Regional body asset not in reviewed release: '+asset);
    bodyHTML='<figure class="ordered-body"><img data-media-role="body" src="/'+asset+'" alt="'+esc(label+' 본문 학습 안내')+'" width="918" height="16116" loading="lazy" decoding="async"></figure>';
    addedBody=true;
  }
  const mapBlock=map?blockFor(map):undefined,mapHTML=mapBlock?markImage(source.slice(mapBlock.start,mapBlock.end),'map'):'';
  // A facility photo previously labelled as body remains available after the map.
  const photo=!body?images.find(n=>imageRole(n)==='other'&&/본문/.test(n.attrs.alt||'')):undefined;
  const photoBlock=photo?blockFor(photo):undefined;
  const photoHTML=photoBlock?source.slice(photoBlock.start,photoBlock.end):'';
  const blocks=[...(body?[blockFor(body)]:[]),...(mapBlock?[mapBlock]:[]),...(photoBlock?[photoBlock]:[]),...repImages.map(blockFor)];
  const unique=[...new Set(blocks)].filter(a=>!blocks.some(b=>b!==a&&b.start<=a.start&&b.end>=a.end));
  const anchor=body?blockFor(body):photoBlock||mapBlock||(selected?blockFor(selected):undefined);
  if(!anchor)throw Error('No safe media insertion anchor: '+name);
  const replacement='<div class="ordered-image-sequence" data-image-order="sequence-v1"'+(!map?' data-map-status="unconfirmed"':'')+'>'+rep+bodyHTML+mapHTML+photoHTML+'</div>';
  const edits=unique.map(b=>({start:b.start,end:b.end,value:b===anchor?replacement:''}));
  // Preserve each disclosure's content and IDs while removing its image-view button.
  const gates=[...new Set([...(body?ancestors(body):[]),...(map?ancestors(map):[]),...(photo?ancestors(photo):[]),...ancestors(anchor)].filter(n=>n.tag==='details'))];
  for(const gate of gates){
    edits.push({start:gate.start,end:gate.openEnd,value:source.slice(gate.start,gate.openEnd).replace(/^<details\b/i,'<div').replace(/\sopen(?:="[^"]*")?/i,'')});
    edits.push({start:gate.closeStart,end:gate.end,value:'</div>'});
    for(const child of gate.children.filter(n=>n.tag==='summary'))edits.push({start:child.start,end:child.end,value:''});
  }
  edits.sort((a,b)=>b.start-a.start);
  let last=source.length,html=source;
  for(const edit of edits){if(edit.end>last)throw Error('Overlapping media changes: '+name);html=html.slice(0,edit.start)+edit.value+html.slice(edit.end);last=edit.start;}
  html=html.replace(/<\/head\s*>/i,STYLE+'$&');
  return {html,changed:html!==source,addedBody,addedRepresentative:!selected,unfolded:gates.length,missingMap:!map};
}
export function normalizeDirectory(root,output){
  const manifest=JSON.parse(fs.readFileSync(path.join(root,'release-public-manifest.json'),'utf8'));
  const context=mediaContext(root,manifest),report={pages:0,changed:0,addedBody:0,addedRepresentative:0,unfolded:0,missingMap:0,errors:[]};
  for(const name of Object.keys(manifest.files).filter(n=>n.endsWith('.html')&&targetPage(n))){
    report.pages++;
    try{
      const file=path.join(output,name),source=fs.readFileSync(file,'utf8'),result=normalizeMediaHTML(source,name,context);
      if(result.changed){fs.writeFileSync(file,result.html);report.changed++;for(const key of ['addedBody','addedRepresentative','unfolded','missingMap'])report[key]+=Number(result[key]||0);}
    }catch(error){report.errors.push({name,error:String(error)});}
  }
  if(report.errors.length)throw Error(JSON.stringify(report));
  return report;
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const root=path.dirname(fileURLToPath(import.meta.url));
  console.log(JSON.stringify(normalizeDirectory(root,path.resolve(root,process.argv[2]||'.public-release'))));
}
