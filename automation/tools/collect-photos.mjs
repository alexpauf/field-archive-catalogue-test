import {collectPhotos} from '../engine/photo_collector.mjs';
import fs from 'node:fs/promises';
import path from 'node:path';
const [repo,out]=process.argv.slice(2);
let models;
if(repo.endsWith(".json")){models=JSON.parse(await fs.readFile(repo)).models;}else{
const envelope=JSON.parse(await fs.readFile(path.join(repo,'catalogue/manifest.json')));
const m=JSON.parse(Buffer.from(envelope.payload,'base64'));
models=await Promise.all(m.modelIDs.map(k=>fs.readFile(path.join(repo,'catalogue',m.contentPath,'notices',k+'.json')).then(x=>JSON.parse(x))));
}
const result=await collectPhotos(out,{existingModels:models});
console.log(JSON.stringify(result.metrics));
