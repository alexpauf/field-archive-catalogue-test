/** Photographic intake for the existing documentary pipeline. Never publishes or signs.
 * Metadata establishes identity; pixels are never used to infer a historical model.
 * Intake never publishes; weekly.py applies the separate fail-closed automatic admission rules.
 */
import {Session} from './network_collector.mjs';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
const hash=(b,a='sha256')=>crypto.createHash(a).update(b).digest('hex');
const clean=s=>String(s||'').replace(/<[^>]*>/g,' ').replace(/&amp;/g,'&').replace(/&#(\d+);/g,(_,n)=>String.fromCodePoint(Number(n))).replace(/&quot;/g,'"').replace(/\s+/g,' ').trim();
const normalized=s=>clean(s).toLowerCase().replace(/[–—]/g,'-');
export const targets = [
  {id:'m1943-field-jacket',query:'"M-1943" jacket',identity:'\\bm[ -]?1943\\b',garment:'jacket|veste|jaqueta'},
  {id:'m1951-field-jacket',query:'"M-1951" "field jacket"',identity:'\\bm[ -]?(1951|51)\\b',garment:'jacket|veste|jaqueta'},
  {id:'m65-field-jacket',query:'"M-65" "field jacket"',identity:'\\bm[ -]?(1965|65)\\b',garment:'jacket|coat|veste'},
  {id:'og107-utility-top',query:'"OG-107" (shirt OR utility)',identity:'\\bog[ -]?107\\b',garment:'shirt|utility|chemise'},
  {id:'jacket-combat-winter',query:'"winter" "combat" "jacket"',identity:'combat.{0,8}winter|winter.{0,8}combat',garment:'jacket|veste'}
];
export function targetsForModels(models){
  if(!models.length)return targets;
  return models.map(model=>{
    const known=targets.find(t=>t.id===model.id);if(known)return known;
    const name=clean(model.name);const tokens=name.match(/[A-Za-z0-9]+/g)||[];
    return {id:model.id,query:name,identity:tokens.map(t=>t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('[\\s,._-]+'),garment:'jacket|coat|shirt|veste|chemise'};
  }).filter(t=>t.identity.length>3);
}
export const views=[['general',''],['back','(back OR rear OR dos)'],['label','(label OR tag OR marking OR etiquette)'],['construction','(detail OR pocket OR cuff OR construction)']];
export function licenseGate(meta){
  const value=k=>clean(meta[k]?.value),url=value('LicenseUrl').replace(/^http:/,'https:').replace(/\/$/,'');
  const accepted=new Map([
    ['https://creativecommons.org/licenses/by-sa/4.0','CC BY-SA 4.0'],
    ['https://creativecommons.org/licenses/by-sa/3.0','CC BY-SA 3.0'],
    ['https://creativecommons.org/licenses/by/4.0','CC BY 4.0'],
    ['https://creativecommons.org/publicdomain/zero/1.0','CC0 1.0']
  ]);
  if(!accepted.has(url))return {allowed:false,reason:'No explicitly supported redistribution license on this image'};
  if(!value('Artist'))return {allowed:false,reason:'Image creator absent'};
  if(value('Restrictions')||value('Permission').match(/no redistribution|non.?commercial|all rights reserved/i))return {allowed:false,reason:'Conflicting image restriction'};
  return {allowed:true,license:accepted.get(url),licenseURL:url+'/',creator:value('Artist'),credit:value('Credit'),attributionRequired:value('AttributionRequired')==='true',requiredAttribution:value('Attribution')||value('Artist'),modifications:'Aucune ; fichier source conservé sans transformation.'};
}
export function identityGate(target,page,meta){
  const description=clean(meta.ImageDescription?.value),text=normalized(page.title+' '+description);
  if(/replica|reproduction|repro\b|prototype|experimental|airsoft|civilian|fashion|commercial|norwegian|norway|soviet|icelandic|german|parka|trousers|pants|rip.?stop|poplin|jungle|women|female/.test(text))return {allowed:false,reason:'Different model, nation, reproduction or separate garment'};
  if(!new RegExp(target.identity,'i').test(text)||!new RegExp(target.garment,'i').test(text))return {allowed:false,reason:'Exact garment designation missing from source metadata'};
  if(!/\bu[ .]?s[ .]?(?:a|army|military|\b)|united states|american|estatunidenc/.test(text))return {allowed:false,reason:'United States attribution not established'};
  return {allowed:true,designation:description,modelID:target.id,variant:'Non établie ; aucune généralisation des insignes ou détails du spécimen.',verificationLevel:'explicit-source-designation; individual-contract-not-verified',basis:'Description du fichier par son auteur ; aucune inférence d’identité depuis la ressemblance visuelle.'};
}
export function viewType(text){
  text=normalized(text);
  if(/\b(label|tag|marking|etiquette)\b/.test(text))return 'label';
  if((/\b(front|frontal)\b|vue de face/.test(text))&&!/\b(back|rear|dos)\b/.test(text))return 'front';
  if(/\b(back|rear|dos)\b/.test(text))return 'back';
  if(/\b(detail|cuff|pocket)\b/.test(text))return 'construction';
  return 'general-unconfirmed';
}
export function parseCommons(html,url){
  html=html.replace(/&#95;/g,'_').replace(/&amp;/g,'&');
  const cell=id=>clean(html.match(new RegExp('<td[^>]+id="'+id+'"[^>]*>[\\s\\S]*?</td>\\s*<td[^>]*>([\\s\\S]*?)</td>'))?.[1]);
  const span=cls=>clean(html.match(new RegExp('<span[^>]+class="'+cls+'"[^>]*>([\\s\\S]*?)</span>'))?.[1]);
  const title=clean(html.match(/<title>(.*?) - Wikimedia Commons<\/title>/)?.[1]);
  const original=html.match(/class="fullImageLink"[^>]*>\s*<a href="([^"]+)"/)?.[1];
  return {pageid:Number(html.match(/"wgArticleId":(\d+)/)?.[1]),title,imageinfo:[{
    url:original,descriptionurl:url,size:Number(html.match(/"property":"P3575"[\s\S]{0,200}?"amount":"\+(\d+)"/)?.[1]),
    sha1:html.match(/"property":"P4092"[\s\S]{0,200}?"value":"([a-f0-9]{40})"/)?.[1],
    width:Number(html.match(/data-file-width="(\d+)"/)?.[1]),height:Number(html.match(/data-file-height="(\d+)"/)?.[1]),
    extmetadata:{ImageDescription:{value:cell('fileinfotpl_desc')},Artist:{value:cell('fileinfotpl_aut')},Credit:{value:cell('fileinfotpl_src')},LicenseUrl:{value:span('licensetpl_link')},AttributionRequired:{value:span('licensetpl_attr_req')}}
  }]};
}
export async function collectPhotos(root,{downloadLimit=6,existingModels=[]}={}){
  const config={userAgent:'FieldArchive/0.1',maxRequests:40,maxRunBytes:40_000_000,maxBytesPerResponse:12_000_000,timeoutMs:20000};
  const source={id:'commons-open-images',adapter:'commons-media',origin:'https://commons.wikimedia.org',delayMs:200};
  const session=new Session(config,root);await session.open();const rows=[],queries=[],seen=new Map();let downloaded=0;
  try{
    // Public category inventories are crawlable; /w/api.php is not in robots.txt.
    // No API alternate host, authentication, proxy or denied-path retry is used.
    const index=[];
    for(const url of ['https://commons.wikimedia.org/wiki/Category:OG-107_utility_uniform','https://commons.wikimedia.org/wiki/Category:Uniforms_of_the_United_States_Army','https://commons.wikimedia.org/wiki/Category:Green_jackets']){
      try{const response=await session.request(url,source,{ttl:86400,expected:'html'});const html=response.data.toString();
        const gallery=html.slice(html.indexOf('id="mw-category-media"'));
        for(const match of gallery.matchAll(/href="(\/wiki\/File:[^"]+)"/g)){const candidateURL=new URL(match[1].replace(/&amp;/g,'&'),source.origin).href;
          if(!index.some(x=>x.url===candidateURL))index.push({url:candidateURL,title:decodeURIComponent(match[1].slice(6)).replaceAll('_',' '),discoveredVia:url,indexSHA256:response.sha256});}
      }catch(e){await session.error(url,e);if([401,403,429].includes(e.status))break;}
    }
    const searchTargets=targetsForModels(existingModels);
    const illustrated=new Set(existingModels.filter(m=>m.illustration?.filename).map(m=>m.id));
    search: for(const target of [...searchTargets].sort((a,b)=>Number(illustrated.has(a.id))-Number(illustrated.has(b.id)))){
      for(const [view,suffix] of views){
        const matches=index.filter(x=>new RegExp(target.identity,'i').test(x.title)&& (view==='general'||viewType(x.title)===view)).slice(0,8);
        const query={modelID:target.id,requestedView:view,method:'local-filter-of-allowed-category-inventories',results:matches.length};queries.push(query);
        try{
          for(const discovery of matches){
            const already=rows.find(x=>x.modelID===target.id&&x.sourceURL===discovery.url);if(already){already.foundFor.push(view);continue;}
            const response=await session.request(discovery.url,source,{ttl:86400,expected:'html'});const page=parseCommons(response.data.toString(),discovery.url);
            const info=page.imageinfo?.[0];if(!info)continue;
            const key=target.id+'#'+page.pageid;
            if(seen.has(key)){seen.get(key).foundFor.push(view);continue;}
            const meta=info.extmetadata||{},licence=licenseGate(meta),identity=identityGate(target,page,meta);
            const row={id:'commons-'+page.pageid,modelID:target.id,title:page.title,sourceURL:info.descriptionurl,originalURL:info.url,metadataURL:discovery.url,metadataSHA256:response.sha256,discoveredVia:discovery.discoveredVia,indexSHA256:discovery.indexSHA256,author:licence.creator||clean(meta.Artist?.value),license:licence,attribution:licence.requiredAttribution||'',identity,view:viewType(page.title+' '+clean(meta.ImageDescription?.value)),foundFor:[view],width:info.width,height:info.height,sourceSHA1:info.sha1,sourceBytes:info.size,status:!licence.allowed?'rights-rejected':!identity.allowed?'identity-rejected':'review-pending',downloaded:false};
            seen.set(key,row);rows.push(row);
            if(licence.allowed&&identity.allowed&&downloaded<downloadLimit&&info.size<=8_000_000&&/\.jpe?g(?:\?|$)/i.test(info.url)){
              try{
                const image=await session.request(info.url,source,{ttl:86400,expected:'binary'});
                if(image.data.length!==info.size||hash(image.data,'sha1')!==info.sha1)throw Error('Image differs from source byte count or SHA-1');
                if(image.data[0]!==255||image.data[1]!==216)throw Error('Not a JPEG image');
                const dest='images/'+row.id+'.jpg';await fs.mkdir(path.join(root,'images'),{recursive:true});await fs.writeFile(path.join(root,dest),image.data);
                row.downloaded=true;row.localPath=dest;row.sha256=image.sha256;downloaded++;
              }catch(e){row.downloadError=e.message;}
            }
          }
        }catch(e){query.error=e.message;await session.error(target.id+'#'+view,e);if([401,403,429].includes(e.status))break search;}
      }
    }
    // Follow institutional IIIF links already cited by the current catalogue.
    const institutional=[];
    for(const [modelID,url] of [['m1943-field-jacket','https://apicollections.parismusees.paris.fr/iiif/600000762/manifest'],['og107-utility-top','https://digital.library.unt.edu/ark:/67531/metadc2408800/manifest/']]){
      try{const response=await session.request(url,{id:'institutional-media',adapter:'museum-iiif',origin:new URL(url).origin,delayMs:200},{ttl:86400});const m=JSON.parse(response.data);const canvases=m.sequences?.flatMap(s=>s.canvases||[])||m.items||[];institutional.push({modelID,url,metadataSHA256:response.sha256,license:m.license||m.rights,rightsStatements:(m.metadata||[]).filter(x=>String(x.label).startsWith('rights')),canvases:canvases.map(c=>({label:c.label,images:c.images?.map(i=>i.resource?.['@id'])||[]})),status:'review-pending; no image license inferred from public access'});}catch(e){institutional.push({modelID,url,error:e.message});}
    }
    const report={schemaVersion:1,checkedAt:new Date().toISOString(),queries,candidates:rows,institutional,metrics:{models:searchTargets.length,viewSearches:queries.length,indexedFiles:index.length,candidates:rows.length,downloaded,licenseAccepted:rows.filter(x=>x.license.allowed).length,identityAccepted:rows.filter(x=>x.identity.allowed).length,...session.metrics},events:session.events,publicationPerformed:false};
    await fs.writeFile(path.join(root,'photo-intake.json'),JSON.stringify(report,null,2)+'\n');return report;
  }finally{await session.close();}
}
