// Reuses the existing bounded/cache/robots transport. No signing material is available here.
import {Session} from '../engine/network_collector.mjs';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
const [configFile,root,extractor]=process.argv.slice(2);
if(!path.isAbsolute(extractor))throw Error('Absolute native extractor required');
const config=JSON.parse(await fs.readFile(configFile));
if(!/^[a-z0-9-]+$/.test(config.parserVersion))throw Error('Invalid historical parser version');
const session=new Session(config.transport,root);await session.open();
session.state.primaryProgress??={};
const outcomes=[],attestations=[],rejections=[];let pagesLeft=config.maxPagesPerRun;
const python=fileURLToPath(new URL('../engine/historical_index.py',import.meta.url));
try{
 for(const profile of config.documents){
  if(!/^[a-z0-9-]+$/.test(profile.id)||!/^https:\/\//.test(profile.url)||!/^[a-f0-9]{64}$/.test(profile.sha256)
   ||profile.synthetic||profile.year<1940||profile.year>1999||profile.rights!=='US-federal-text-only; no image permission')throw Error('Invalid primary-source profile');
  if(profile.bibliographicProof&&(profile.bibliographicProof.kind!=='reviewed-institutional-bootstrap'
   ||profile.bibliographicProof.approvedDocumentSHA256!==profile.sha256||profile.bibliographicProof.year!==profile.year
   ||new URL(profile.bibliographicProof.url).hostname!=='collections.si.edu'))throw Error('Unapproved bibliographic bootstrap');
  const old=session.state.primaryProgress[profile.id];
  if(Date.parse(old?.blockedUntil||'')>Date.now()){outcomes.push({id:profile.id,status:'deferred-cooldown'});continue;}
  if(old?.sha256===profile.sha256&&old?.parserVersion===config.parserVersion&&old?.complete){outcomes.push({id:profile.id,status:'already-indexed'});continue;}
  if(pagesLeft<1||session.metrics.networkBytes+profile.bytes>session.config.maxRunBytes){outcomes.push({id:profile.id,status:'deferred-budget'});continue;}
  const source={id:profile.id,origin:new URL(profile.url).origin,adapter:'historical-primary',delayMs:1000};
  try{
   const raw=await session.request(profile.url,source,{expected:'binary',ttl:604800});
   if(raw.sha256!==profile.sha256||raw.data.length!==profile.bytes)throw Error('Approved historical document changed; excluded');
   const file=path.resolve(root,'http',raw.sha256);
   const front=JSON.parse(execFileSync(extractor,[file,'1',String(profile.authorityPages||2)],{encoding:'utf8',maxBuffer:8_000_000,timeout:120000}));
   const frontText=front.map(p=>p.text).join(' ').replace(/\s+/g,' ').toLowerCase();
   if(!profile.authorityTokens.every(t=>frontText.includes(t.toLowerCase())))throw Error('Government author/date not confirmed');
   const start=old?.sha256===profile.sha256&&old?.parserVersion===config.parserVersion?old.nextPage:profile.firstPage;
   const end=Math.min(profile.lastPage,start+pagesLeft-1);
   const pages=JSON.parse(execFileSync(extractor,[file,String(start),String(end)],{encoding:'utf8',maxBuffer:12_000_000,timeout:180000}));
   const folder=path.join(root,'historical');await fs.mkdir(folder,{recursive:true});
   const prefix=path.join(folder,profile.id+'-'+profile.sha256.slice(0,12)+'-'+config.parserVersion+'-p'+start+'-'+end);await fs.writeFile(prefix+'-pages.json',JSON.stringify(pages));await fs.writeFile(prefix+'-profile.json',JSON.stringify(profile));
   execFileSync('python3',[python,'--profile',prefix+'-profile.json','--pages',prefix+'-pages.json','--output',prefix+'-result.json'],{timeout:60000,stdio:'pipe'});
   const result=JSON.parse(await fs.readFile(prefix+'-result.json'));rejections.push(...result.rejections.map(r=>({...r,sourceID:profile.id})));
   pagesLeft-=pages.length;
   session.state.primaryProgress[profile.id]={sha256:profile.sha256,parserVersion:config.parserVersion,nextPage:end+1,complete:end>=profile.lastPage};
   await session.checkpoint();outcomes.push({id:profile.id,status:'indexed',pages:pages.length,start,end,attestations:result.attestations.length,complete:end>=profile.lastPage});
  }catch(error){
   if([401,403,429].includes(error.status))session.state.primaryProgress[profile.id]={sha256:profile.sha256,parserVersion:config.parserVersion,nextPage:old?.nextPage||profile.firstPage,complete:false,blockedUntil:new Date(Date.now()+(error.retryAfterMs||3600000)).toISOString()};
   await session.error(profile.id,error);outcomes.push({id:profile.id,status:'deferred-error',error:error.message});
  }
 }
 // Local retries recover completed ranges; a fresh runner takes prior ranges from the signed baseline.
 const folder=path.join(root,'historical');
 for(const file of await fs.readdir(folder).catch(()=>[])){
  const profile=config.documents.find(p=>file.startsWith(p.id+'-'+p.sha256.slice(0,12)+'-'+config.parserVersion+'-p')&&file.endsWith('-result.json'));
  if(profile){const result=JSON.parse(await fs.readFile(path.join(folder,file)));attestations.push(...result.attestations);}
 }
 const report={parserVersion:config.parserVersion,outcomes,metrics:session.metrics,primaryProgress:session.state.primaryProgress};
 await fs.writeFile(path.join(root,'historical-attestations.json'),JSON.stringify(attestations));
 await fs.writeFile(path.join(root,'historical-rejections.json'),JSON.stringify(rejections));
 await fs.writeFile(path.join(root,'historical-report.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify({outcomes,metrics:session.metrics}));
}finally{await session.close();}
