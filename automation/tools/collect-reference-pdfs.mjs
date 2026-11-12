import {Session} from '../engine/network_collector.mjs';
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
const [configPath,root,extractor]=process.argv.slice(2);
if(!path.isAbsolute(extractor))throw Error('Absolute trusted PDF extractor required');
const config=JSON.parse(await fs.readFile(configPath));
const session=new Session({userAgent:config.userAgent,maxRequests:12,maxBytesPerResponse:32_000_000,maxRunBytes:26_000_000,timeoutMs:20000},root);
await session.open();const outcomes=[];
try {
 for(const doc of config.referenceDocuments||[]){
  try{
  if(!/^[a-z0-9-]+$/.test(doc.id)||!/^https:\/\//.test(doc.url))throw Error('Invalid reference profile');
  const source={id:doc.id,adapter:'federal-pdf',origin:new URL(doc.url).origin,delayMs:700};
  let rightsEvidence=doc.url;
  if(doc.metadataURL){
   const metadata=await session.request(doc.metadataURL,source,{expected:'html',ttl:604800});
   if(!doc.rightsTemplate||!metadata.data.toString().includes(doc.rightsTemplate))throw Error('Explicit federal metadata license absent');
   rightsEvidence=doc.metadataURL;
  }
  const raw=await session.request(doc.url,source,{expected:'binary',ttl:604800});
  if(raw.sha256!==doc.sha256)throw Error('Pinned federal reference changed; review required');
  const file=path.resolve(root,'http',raw.sha256);
  const pages=JSON.parse(execFileSync(extractor,[file],{encoding:'utf8',timeout:60000,maxBuffer:6_000_000}));
  const front=pages.slice(0,6).map(p=>p.text).join(' ').replace(/\s+/g,' ');
  if(doc.authorityTokens&&!doc.authorityTokens.every(t=>front.includes(t)))throw Error('Government agency front matter absent');
  const lines=[];for(const page of pages)for(const line of page.text.split(/\r?\n/).filter((x,i,a)=>i!==a.length-1||x!==''))lines.push(`L${lines.length}@P${page.page}: ${line}`);
  // JSON from the native extractor is converted deterministically, then independently replayed by pipeline.load_corpus.
  const capture={id:doc.id,url:doc.url,title:doc.title,institution:doc.institution,independence:doc.independence,kind:'institutional_survey',adapter:'federal-pdf-v1',scope:'document',rights:'US-federal-text-only',rightsEvidence,publicationYear:doc.publicationYear,retrievedAt:new Date().toISOString(),captureMethod:'native-PDFKit-text-with-original-pages',partial:false,rawRelativePath:'http/'+raw.sha256,rawHTTPBodySHA256:raw.sha256,toolResponse:{content:[{type:'text',text:lines.join('\n')}]}};
  await fs.mkdir(path.join(root,'corpus'),{recursive:true});await fs.writeFile(path.join(root,'corpus',doc.id+'.json'),JSON.stringify(capture,null,2));
  outcomes.push({id:doc.id,pages:pages.length,bytes:raw.data.length,sha256:raw.sha256,textOnly:true});
  }catch(error){await session.error(doc.id,error);outcomes.push({id:doc.id,status:'deferred-error',error:error.message});}
 }
 const report={outcomes,metrics:session.metrics};await fs.writeFile(path.join(root,'full-reference-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await session.close();}
