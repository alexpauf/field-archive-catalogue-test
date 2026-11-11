/** Phase 50 HTTP transport and discovery adapter for the EXISTING Python pipeline.
 * No model IDs, document IDs, historical notices or predicted feature values here.
 * Native fetch only; no proxy, authentication, CAPTCHA handling or browser scraping.
 */
import * as fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {getHeapStatistics} from 'node:v8';

const sha=(b,algorithm='sha256')=>crypto.createHash(algorithm).update(b).digest('hex');
const jsonBytes=x=>Buffer.from(JSON.stringify(x,null,2)+'\n');
const array=x=>x==null?[]:Array.isArray(x)?x:[x];
const text=x=>array(x).join(' ');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const now=()=>new Date().toISOString();
async function readJSON(file,fallback=null){try{return JSON.parse(await fs.readFile(file,'utf8'));}catch(e){if(e.code==='ENOENT')return fallback;throw e;}}
async function atomic(file,data){await fs.mkdir(path.dirname(file),{recursive:true});const temp=file+'.pending-'+crypto.randomBytes(6).toString('hex');try{await fs.writeFile(temp,data);await fs.rename(temp,file);}finally{await fs.rm(temp,{force:true});}}

export function robotRules(body,agent){
  const groups=[];let group=null,hasRules=false;
  for(const raw of body.split(/\r?\n/)){
    const line=raw.replace(/#.*/,'').trim();if(!line)continue;
    const i=line.indexOf(':');if(i<0)continue;
    const key=line.slice(0,i).trim().toLowerCase(),value=line.slice(i+1).trim();
    if(key==='user-agent'){
      if(!group||hasRules){group={agents:[],rules:[],delay:0};groups.push(group);hasRules=false;}
      group.agents.push(value.toLowerCase());
    }else if(group){hasRules=true;if(['allow','disallow'].includes(key)&&value)group.rules.push({allow:key==='allow',pattern:value});if(key==='crawl-delay'&&Number.isFinite(Number(value)))group.delay=Number(value)*1000;}
  }
  const a=agent.toLowerCase();const matches=groups.map(g=>({g,n:Math.max(-1,...g.agents.map(x=>x==='*'?0:a.includes(x)?x.length:-1))}));
  const best=Math.max(-1,...matches.map(x=>x.n));
  const selected=matches.filter(x=>x.n===best&&best>=0).map(x=>x.g);
  return {rules:selected.flatMap(x=>x.rules),delay:Math.max(0,...selected.map(x=>x.delay))};
}
export function robotAllows(policy,url){
  const u=new URL(url),target=u.pathname+u.search;
  const candidates=policy.rules.filter(r=>{
    const escaped=r.pattern.replace(/[.+?^${}()|[\]\\]/g,'\\$&').replace(/\*/g,'.*').replace(/\\\$$/,'$');
    return new RegExp('^'+escaped).test(target);
  }).sort((a,b)=>b.pattern.length-a.pattern.length||Number(b.allow)-Number(a.allow));
  return candidates.length===0||candidates[0].allow;
}

class HTTPFailure extends Error {constructor(message,status=0){super(message);this.status=status;}}
export class Session {
  constructor(config,root,transport=fetch){this.config=config;this.root=root;this.fetch=transport;this.state={schemaVersion:1,http:{},documents:{}};this.lastRequest={};this.robots={};this.events=[];this.metrics={networkRequests:0,networkBytes:0,cacheHits:0,notModified:0,documentDownloads:0,unchangedDocuments:0,discoveredReferences:0,newReferences:0,eligibleDocuments:0,errors:0,rightsRejected:0};}
  async open(){await fs.mkdir(this.root,{recursive:true});this.lock=await fs.open(path.join(this.root,'.collect.lock'),'wx');try{this.state=await readJSON(path.join(this.root,'state.json'),this.state);this.state.sourceCooldowns??={};}catch(e){await this.close();throw e;}}
  async close(){if(this.lock){await this.lock.close();await fs.rm(path.join(this.root,'.collect.lock'),{force:true});}}
  async checkpoint(){await atomic(path.join(this.root,'state.json'),jsonBytes(this.state));}
  allowed(url,source){
    const u=new URL(url),origin=new URL(source.origin);
    if(u.username||u.password)throw new HTTPFailure('Credentials in URL forbidden');
    if(this.config.testMode&&u.origin===origin.origin&&['127.0.0.1','localhost'].includes(u.hostname))return true;
    if(u.protocol!=='https:')throw new HTTPFailure('HTTPS required');
    if(source.adapter==='internet-archive'&&(u.hostname==='archive.org'||u.hostname.endsWith('.archive.org')))return true;
    if(source.adapter==='loc'&&u.hostname==='www.loc.gov')return true;
    // Photo discovery uses the same bounded/cache/robots transport, without widening text sources.
    if(source.adapter==='commons-media'&&['commons.wikimedia.org','upload.wikimedia.org'].includes(u.hostname))return true;
    if(source.adapter==='museum-iiif'&&['apicollections.parismusees.paris.fr','digital.library.unt.edu'].includes(u.hostname))return true;
    throw new HTTPFailure('URL outside source allowlist');
  }
  async body(response){
    const declared=Number(response.headers.get('content-length')||0);
    if(declared>this.config.maxBytesPerResponse)throw new HTTPFailure('Declared response exceeds byte limit');
    const parts=[];let size=0;
    const consume=value=>{size+=value.length;if(size>this.config.maxBytesPerResponse||this.metrics.networkBytes+size>this.config.maxRunBytes)throw new HTTPFailure('Response/run byte limit');parts.push(Buffer.from(value));};
    if(response.body?.getReader){const reader=response.body.getReader();try{for(;;){const {done,value}=await reader.read();if(done)break;consume(value);}}catch(e){await reader.cancel().catch(()=>{});throw e;}}
    else consume(new Uint8Array(await response.arrayBuffer()));
    this.metrics.networkBytes+=size;return Buffer.concat(parts);
  }
  async raw(url,source,headers={}){
    this.allowed(url,source);
    if(this.metrics.networkRequests>=this.config.maxRequests)throw new HTTPFailure('Request budget exhausted');
    const host=new URL(url).hostname;
    const wait=Math.max(0,Math.max(source.delayMs||0,this.robots[new URL(url).origin]?.delay||0)-(Date.now()-(this.lastRequest[host]||0)));
    if(wait)await sleep(wait);
    this.lastRequest[host]=Date.now();this.metrics.networkRequests++;
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),this.config.timeoutMs);
    try{
      const response=await this.fetch(url,{headers:{'User-Agent':this.config.userAgent,'Accept-Encoding':'identity',...headers},redirect:'manual',signal:controller.signal});
      const data=await this.body(response);
      this.events.push({kind:'http',url,status:response.status,bytes:data.length,at:now()});
      return {response,data};
    }catch(e){this.events.push({kind:'network-error',url,error:e.message,at:now()});throw e;}
    finally{clearTimeout(timer);}
  }
  async policy(url,source){
    const origin=new URL(url).origin;if(this.robots[origin])return this.robots[origin];
    const robotsURL=origin+'/robots.txt';
    const cached=this.state.http[robotsURL];
    let body;
    if(cached&&Date.now()-Date.parse(cached.checkedAt)<86400000){body=(await fs.readFile(path.join(this.root,'http',cached.sha256))).toString();this.metrics.cacheHits++;}
    else{
      const {response,data}=await this.raw(robotsURL,source);
      if(response.status===404||response.status===410)body='';
      else if(response.status===200)body=data.toString();
      else throw new HTTPFailure('robots.txt unavailable: '+response.status,response.status);
      const b=Buffer.from(body),h=sha(b);await atomic(path.join(this.root,'http',h),b);this.state.http[robotsURL]={sha256:h,checkedAt:now()};await this.checkpoint();
    }
    const policy=robotRules(body,this.config.userAgent);this.robots[origin]=policy;return policy;
  }
  async request(url,source,{ttl=0,expected='json',hops=0}={}){
    this.allowed(url,source);if(hops>4)throw new HTTPFailure('Too many redirects');
    const policy=await this.policy(url,source);if(!robotAllows(policy,url))throw new HTTPFailure('robots.txt forbids '+url);
    const old=this.state.http[url];
    if(old&&Date.now()-Date.parse(old.checkedAt)<ttl*1000){this.metrics.cacheHits++;return {data:await fs.readFile(path.join(this.root,'http',old.sha256)),sha256:old.sha256,cached:true,finalURL:old.finalURL||url};}
    const headers={};if(old?.etag)headers['If-None-Match']=old.etag;if(old?.lastModified)headers['If-Modified-Since']=old.lastModified;
    const {response,data}=await this.raw(url,source,headers);
    if([301,302,303,307,308].includes(response.status)){
      const target=new URL(response.headers.get('location'),url).href;this.allowed(target,source);
      // Follow only the server's declared redirect. No alternate hosts on failure.
      const result=await this.request(target,source,{ttl,expected,hops:hops+1});
      this.state.http[url]={...(this.state.http[target]||{}),sha256:result.sha256,checkedAt:now(),finalURL:result.finalURL};await this.checkpoint();return result;
    }
    if(response.status===304&&old){this.metrics.notModified++;old.checkedAt=now();await this.checkpoint();return {data:await fs.readFile(path.join(this.root,'http',old.sha256)),sha256:old.sha256,cached:true,finalURL:url};}
    if(response.status!==200){
      const error=new HTTPFailure('HTTP '+response.status+'; no bypass or immediate retry',response.status);
      const retry=response.headers.get('retry-after');
      error.retryAfterMs=Math.max(3600000,Number.isFinite(Number(retry))?Number(retry)*1000:Math.max(0,Date.parse(retry)-Date.now())||0);
      throw error;
    }
    const type=response.headers.get('content-type')||'';
    if(expected==='json'){if(!/json/.test(type))throw new HTTPFailure('Expected JSON; HTML/challenge refused');JSON.parse(data.toString());}
    if(expected==='text'&&(/html/.test(type)||/^\s*<!doctype html/i.test(data.toString().slice(0,120))))throw new HTTPFailure('HTML/challenge refused for OCR');
    const h=sha(data);await atomic(path.join(this.root,'http',h),data);
    this.state.http[url]={sha256:h,checkedAt:now(),etag:response.headers.get('etag'),lastModified:response.headers.get('last-modified'),finalURL:url};await this.checkpoint();
    return {data,sha256:h,cached:false,finalURL:url};
  }
  async error(where,error){this.metrics.errors++;this.events.push({kind:'error',where,error:error.message,status:error.status||0,at:now()});await this.checkpoint();}
}

export function rightsGate(meta){
  const md=meta.metadata||{};
  if(meta.is_dark||meta.is_restricted||['true','1'].includes(String(md['access-restricted-item']).toLowerCase())||array(md.collection).some(x=>['inlibrary','printdisabled'].includes(x)))return {allowed:false,reason:'restricted item'};
  if(!/^(?:United States[., ]|U\.S\.|US Army|Department of the Army)/i.test(text(md.creator)))return {allowed:false,reason:'government authorship not established by metadata'};
  let licence=text(md.licenseurl).replace(/^http:/,'https:');
  // Public-domain metadata is not required for federal TEXT attributed by the
  // National Library of Medicine. This exception never permits images, and the
  // collected OCR must also contain the government author on its front matter.
  const federalNLM=!licence&&array(md.collection).includes('usnationallibraryofmedicine');
  if(!['https://creativecommons.org/publicdomain/mark/1.0/','https://creativecommons.org/publicdomain/zero/1.0/'].includes(licence)&&!federalNLM)return {allowed:false,reason:'no accepted rights basis for text'};
  if(federalNLM)licence='US-federal-text-only; 17-USC-105; NLM attribution; no media licence';
  if(!/\b(army|war department|air force|air corps)\b/i.test(text(md.creator)))return {allowed:false,reason:'not US military author'};
  const year=Number(text(md.year||md.date).slice(0,4));if(!(year>=1940&&year<=1999))return {allowed:false,reason:'publication outside configured historical interval'};
  if(/seven allies|insect repellent|ordnance|medical evacuation/i.test(text(md.title)))return {allowed:false,reason:'topic outside garment-construction experiment'};
  return {allowed:true,licence,year,requiresFederalFrontMatter:federalNLM,authority:'government-primary-text-in-third-party-repository; metadata attribution not authenticated'};
}

function workIdentity(md,ocr){
  const input=text(md.title)+' '+ocr.slice(0,12000);
  const match=input.match(/\b(FM|TM|TC)\s*[- ]?(\d{1,2})\s*[-–]\s*(\d{1,3})\b/i);
  if(match)return (/war department/i.test(text(md.creator))?'war-department':'army')+'-'+match[1].toLowerCase()+'-'+Number(match[2])+'-'+Number(match[3]);
  return 'government-title-'+sha(Buffer.from(text(md.title).toLowerCase().replace(/[^a-z0-9]+/g,' '))).slice(0,20);
}

async function archiveSource(session,source){
  const refs=[];
  for(let page=1;page<=(source.maxPages||1);page++){
    const url=source.origin+'/advancedsearch.php?'+new URLSearchParams({q:source.query,output:'json',rows:String(source.rows||20),page:String(page),'fl[]':'identifier,title,year,creator',sort:'identifier asc'});
    const result=await session.request(url,source);const j=JSON.parse(result.data);
    if(!j.response||!Array.isArray(j.response.docs))throw new HTTPFailure('Unknown discovery response');
    session.events.push({kind:'discovery-index',source:source.id,url,total:j.response.numFound,returned:j.response.docs.length,sha256:result.sha256,at:now()});
    for(const row of j.response.docs){if(!/^[a-zA-Z0-9_.-]+$/.test(row.identifier||''))continue;refs.push({...row,discoveredVia:url,indexSHA256:result.sha256});}
    if(j.response.docs.length<(source.rows||20))break;
  }
  let accepted=0;
  for(const ref of refs){
    session.metrics.discoveredReferences++;
    const id='ia:'+ref.identifier,old=session.state.documents[id];
    if(!old)session.metrics.newReferences++;
    const record={...old,id,provider:'internet-archive',identifier:ref.identifier,recordURL:source.origin+'/details/'+encodeURIComponent(ref.identifier),discoveredVia:ref.discoveredVia,indexSHA256:ref.indexSHA256,firstSeen:old?.firstSeen||now(),lastSeen:now()};
    session.state.documents[id]=record;await session.checkpoint();
    if(source.excludeTitlePattern&&new RegExp(source.excludeTitlePattern,'i').test(text(ref.title))){
      record.status='excluded-source-topic';session.events.push({kind:'source-topic-excluded',id,title:ref.title,pattern:source.excludeTitlePattern});await session.checkpoint();continue;
    }
    if(Date.parse(record.blockedUntil||'')>Date.now()){
      session.events.push({kind:'document-cooldown',id,until:record.blockedUntil});continue;
    }
    try{
      const metadataURL=source.origin+'/metadata/'+encodeURIComponent(ref.identifier);
      const met=await session.request(metadataURL,source,{ttl:source.metadataTTLSeconds||0});const data=JSON.parse(met.data);
      const gate=rightsGate(data);record.rights=gate;record.metadataURL=metadataURL;record.metadataSHA256=met.sha256;
      if(!gate.allowed){session.metrics.rightsRejected++;record.status='excluded';
        // A newly denied licence/access must not leave an old document active.
        if(record.corpusFile){const active=path.join(session.root,record.corpusFile);try{await fs.mkdir(path.join(session.root,'quarantine'),{recursive:true});await fs.rename(active,path.join(session.root,'quarantine',path.basename(active)));}catch(e){if(e.code!=='ENOENT')throw e;}delete record.corpusSHA256;}
        await session.checkpoint();continue;}
      const f=(data.files||[]).filter(x=>x.name?.endsWith('_djvu.txt')&&!x.private&&Number(x.size)>0&&Number(x.size)<=session.config.maxBytesPerResponse).sort((a,b)=>a.name.localeCompare(b.name))[0];
      if(!f){record.status='pending-no-permitted-ocr';await session.checkpoint();continue;}
      session.metrics.eligibleDocuments++;
      const descriptor={name:f.name,size:Number(f.size),sha1:f.sha1||null};
      const corpusName='ia-'+sha(Buffer.from(ref.identifier)).slice(0,20)+'.json';const corpusFile=path.join(session.root,'corpus',corpusName);
      if(JSON.stringify(old?.rejectedFileDescriptor)===JSON.stringify(descriptor)){
        record.status='pending-author-unclear';session.events.push({kind:'unchanged-validation-rejection',id,reason:old.rejectionReason});await session.checkpoint();continue;
      }
      if(old?.fileDescriptor&&JSON.stringify(old.fileDescriptor)===JSON.stringify(descriptor)&&old.corpusSHA256){
        try{const b=await fs.readFile(corpusFile);if(sha(b)===old.corpusSHA256){session.metrics.unchangedDocuments++;record.status='unchanged';await session.checkpoint();continue;}}catch(e){if(e.code!=='ENOENT')throw e;}
      }
      if(accepted>=source.maxDocuments){record.status='pending-run-document-budget';await session.checkpoint();continue;}accepted++;
      const downloadURL=source.origin+'/download/'+encodeURIComponent(ref.identifier)+'/'+encodeURIComponent(f.name);
      let result;
      try{result=await session.request(downloadURL,source,{expected:'text'});}
      catch(error){
        // Official metadata exposes storage replicas. Only a transport failure,
        // never a denied HTTP/robots/size/integrity response, permits ONE replica.
        if(error instanceof HTTPFailure)throw error;
        const host=data.d1,dir=data.dir;
        if(typeof host!=='string'||!host.endsWith('.archive.org')||!/^\/[0-9]+\/items\/[A-Za-z0-9_.-]+$/.test(dir||''))throw error;
        const replica='https://'+host+dir+'/'+encodeURIComponent(f.name);
        session.events.push({kind:'official-storage-replica',from:downloadURL,to:replica,reason:'transport failure only',metadataURL,at:now()});
        result=await session.request(replica,source,{expected:'text'});
      }
      if(result.data.length!==descriptor.size)throw new HTTPFailure('OCR length does not match repository metadata');
      if(descriptor.sha1&&sha(result.data,'sha1')!==descriptor.sha1)throw new HTTPFailure('OCR SHA-1 does not match repository metadata');
      const ocr=result.data.toString('utf8');if(ocr.includes('\u0000')||ocr.trim().length<100)throw new HTTPFailure('Invalid/empty OCR text');
      if(gate.requiresFederalFrontMatter&&!/WAR\s+DEPARTMENT|DEPARTMENT\s+OF\s+THE\s+ARMY/i.test(ocr.slice(0,12000))){
        record.status='pending-author-unclear';record.rejectedFileDescriptor=descriptor;record.rejectionReason='Federal author not confirmed in OCR front matter';session.events.push({kind:'validation-rejection',id,reason:record.rejectionReason});await session.checkpoint();continue;
      }
      const work=workIdentity(data.metadata,ocr);
      // Native OCR line numbers preserve form-feed page boundaries where present.
      let page=1;const numbered=ocr.split(/\r?\n/).map((line,n)=>{page+=(line.match(/\f/g)||[]).length;return 'L'+n+'@P'+page+': '+line.replace(/\f/g,' ');}).join('\n');
      const capture={id:'ia-'+sha(Buffer.from(ref.identifier)).slice(0,20),url:downloadURL,title:text(data.metadata.title),institution:text(data.metadata.creator),independence:work,kind:'primary_manual_network',adapter:'military-ocr-v1',scope:'document',rights:gate.licence,publicationYear:gate.year,retrievedAt:now(),captureMethod:'native-fetch-original-ocr-bytes',partial:false,sourceRecordURL:record.recordURL,discoveredVia:ref.discoveredVia,metadataURL,metadataSHA256:met.sha256,rawHTTPBodySHA256:result.sha256,rawRelativePath:'http/'+result.sha256,documentFile:f.name,documentSHA1:descriptor.sha1,authority:gate.authority,toolResponse:{content:[{type:'text',text:numbered}]}};
      const b=jsonBytes(capture);await atomic(corpusFile,b);
      record.fileDescriptor=descriptor;record.workIdentity=work;record.corpusFile='corpus/'+corpusName;record.corpusSHA256=sha(b);record.rawSHA256=result.sha256;record.collectedAt=capture.retrievedAt;record.status='collected';delete record.error;
      session.metrics.documentDownloads++;await session.checkpoint();
    }catch(e){record.error=e.message;record.status=old?.corpusSHA256?'pending-retained-last-good':'pending-error';if([401,403,429].includes(e.status))record.blockedUntil=new Date(Date.now()+(e.retryAfterMs||3600000)).toISOString();await session.error(id,e);}
  }
}

async function locSource(session,source){
  const url=source.origin+'/books/?'+new URLSearchParams({q:source.query,dates:'1940/1999',fo:'json',c:String(source.rows||5),at:'results,pagination'});
  const result=await session.request(url,source);const j=JSON.parse(result.data);
  if(!Array.isArray(j.results))throw new HTTPFailure('Unknown LOC discovery response');
  session.events.push({kind:'loc-index',source:source.id,url,count:j.results.length,at:now()});
  // Catalog references alone do not establish technical garment characteristics.
  for(const item of j.results){session.metrics.discoveredReferences++;session.events.push({kind:'bibliographic-reference',url:item.id,title:item.title,requiresItemRightsAndOCR:true});}
}

export async function run(config,root,{transport=fetch,extract=false}={}){
  const start=performance.now();root=path.resolve(root);
  const session=new Session(config,root,transport);await session.open();
  const runId=now().replace(/[:.]/g,'-')+'-'+crypto.randomBytes(3).toString('hex');
  try{
    for(const source of config.sources){
      if(!source.policyURL)throw new HTTPFailure('Source requires documented access policy');
      if(Date.parse(session.state.sourceCooldowns[source.id]||'')>Date.now()){
        session.events.push({kind:'source-cooldown',source:source.id,until:session.state.sourceCooldowns[source.id]});continue;
      }
      try{if(source.adapter==='internet-archive')await archiveSource(session,source);else if(source.adapter==='loc')await locSource(session,source);else throw new HTTPFailure('Unsupported source adapter');}
      catch(e){if([401,403,429].includes(e.status))session.state.sourceCooldowns[source.id]=new Date(Date.now()+(e.retryAfterMs||3600000)).toISOString();await session.error(source.id,e);}
    }
    let catalogue=null;
    if(extract){
      const {execFile}=await import('node:child_process');
      const script=path.join(path.dirname(fileURLToPath(import.meta.url)),'pipeline.py');
      await fs.mkdir(path.join(root,'corpus'),{recursive:true});
      const stdout=await new Promise((resolve,reject)=>execFile('python3',[script,'--corpus',path.join(root,'corpus'),'--output',path.join(root,'catalogue')],{maxBuffer:2000000},(error,stdout,stderr)=>error?reject(new Error(stderr||error.message)):resolve(stdout)));
      catalogue=JSON.parse(stdout);
    }
    const metrics={...session.metrics,uniqueReferencesInState:Object.keys(session.state.documents).length,activeDocumentCaptures:Object.values(session.state.documents).filter(d=>d.corpusSHA256).length,runId,startedAt:new Date(Date.now()-(performance.now()-start)).toISOString(),elapsedSeconds:(performance.now()-start)/1000,heapUsedBytes:getHeapStatistics().used_heap_size,heapNote:'JavaScript runtime heap at run end; includes host runtime overhead, not isolated collector RSS',additionalPaidServiceCostEUR:0,mode:config.testMode?'synthetic-transport-test':'live-network',configSHA256:sha(jsonBytes(config)),catalogue};
    await atomic(path.join(root,'runs',runId+'.json'),jsonBytes({metrics,events:session.events}));await atomic(path.join(root,'last-run.json'),jsonBytes({metrics,events:session.events}));
    return metrics;
  }finally{await session.close();}
}

if(typeof process!=='undefined'&&process.argv?.[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const args=process.argv.slice(2);const configPath=args[args.indexOf('--config')+1],statePath=args[args.indexOf('--state')+1];
  if(!args.includes('--config')||!args.includes('--state'))throw new Error('Usage: node network_collector.mjs --config FILE --state DIRECTORY');
  const result=await run(JSON.parse(await fs.readFile(configPath,'utf8')),statePath,{extract:args.includes('--extract')});console.log(JSON.stringify(result,null,2));
}
