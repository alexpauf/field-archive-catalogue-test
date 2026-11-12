#!/usr/bin/env python3
"""Bounded weekly orchestration around the Phase 49–53 engines. No app or backup access."""
import argparse, base64, copy, datetime, fcntl, hashlib, json, os, re, shutil, stat, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'engine'))
from admission import admit_partial
from pipeline import run as documentary_run
from historical_index import merge_index, extract_network_capture
import designation_gate as documentary_gate


def digest(data): return hashlib.sha256(data).hexdigest()
def encoded(value): return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.pending');temp.write_bytes(encoded(value)+b'\n');temp.replace(path)
def command(args, **kw): return subprocess.run(list(map(str,args)), check=True, capture_output=True, text=True, **kw)
def read_baseline(repo):
    cat=repo/'catalogue'; envelope=json.loads((cat/'manifest.json').read_bytes())
    m=json.loads(base64.b64decode(envelope['payload'],validate=True))
    if not re.fullmatch(r'releases/v[0-9]+-[a-f0-9]{20}/',m['contentPath']): raise ValueError('Unsafe baseline version')
    models=[]; images={}
    for f in m['files']:
        if not re.fullmatch(r'(notices/[a-z0-9-]+\.json|references\.json|images/[A-Za-z0-9_-]+\.(jpg|jpeg|png))',f['path']): raise ValueError('Unsafe baseline path')
        data=(cat/m['contentPath']/f['path']).read_bytes()
        if len(data)!=f['bytes'] or digest(data)!=f['sha256']: raise ValueError('Corrupt baseline')
        if f['path'].startswith('notices/'): models.append(json.loads(data))
        if f['path'].startswith('images/'): images[Path(f['path']).name]=data
    if len(models)!=len(m['modelIDs']) or {x['id'] for x in models}!=set(m['modelIDs']): raise ValueError('Baseline models mismatch')
    return m,models,images


def fact_identity(fact):
    # Evidence IDs change with capture provenance; they are not a new historical fact.
    # Keep the field, value (including scope), source identities and all other data.
    stable=copy.deepcopy(fact)
    stable['label']=re.sub(r' · [a-f0-9]{6}$','',stable['label'])
    stable['sources']=sorted(set(stable['sources']))
    return encoded(stable)


def seed_research_state(repo, intake):
    checkpoint=repo/'automation/state/checkpoint.json'
    if (intake/'state.json').exists() or not checkpoint.exists(): return
    if checkpoint.stat().st_size>512_000: raise ValueError('Research checkpoint budget exceeded')
    prior=json.loads(checkpoint.read_text());documents={}
    for item in prior.get('documentQueue',[])[:500]:
        key=item.get('id','')
        if not re.fullmatch(r'ia:[A-Za-z0-9_.-]+',key):raise ValueError('Unsafe research document identity')
        if 'sourceID' in item and not re.fullmatch(r'[a-z0-9-]+',item['sourceID']):raise ValueError('Unsafe research source identity')
        if 'title' in item and (not isinstance(item['title'],str) or len(item['title'])>240):raise ValueError('Research title budget exceeded')
        documents[key]={k:item[k] for k in ['id','status','firstSeen','lastSeen','sourceID','title','blockedUntil','fileDescriptor','indexedVersion'] if k in item}
    for key in prior.get('knownReferences',[])[:1000]:
        if not re.fullmatch(r'ia:[A-Za-z0-9_.-]+',key):raise ValueError('Unsafe research reference')
        documents.setdefault(key,{'id':key,'status':'seen-before'})
    cursors=prior.get('discoveryCursors',{})
    if any(not re.fullmatch(r'[a-z0-9-]+',key) or not re.fullmatch(r'[a-f0-9]{64}',value.get('fingerprint','')) or not isinstance(value.get('nextPage'),int) or not 1<=value['nextPage']<=10000 for key,value in cursors.items()):raise ValueError('Invalid discovery cursor')
    primary=prior.get('primaryProgress',{})
    for key,value in primary.items():
        if not re.fullmatch(r'[a-z0-9-]+',key) or not re.fullmatch(r'[a-f0-9]{64}',value.get('sha256','')) or not isinstance(value.get('nextPage'),int) or not 1<=value['nextPage']<=10001:raise ValueError('Invalid primary-source cursor')
    save(intake/'state.json',{'schemaVersion':1,'http':{},'documents':documents,'discoveryCursors':cursors,'primaryProgress':primary,'sourceCooldowns':prior.get('sourceCooldowns',{}),'sourceTurn':int(prior.get('sourceTurn',0))%1000})

def seed_photo_state(repo,photos):
    checkpoint=repo/'automation/state/checkpoint.json'
    if (photos/'state.json').exists() or not checkpoint.exists():return
    prior=json.loads(checkpoint.read_text());photo=prior.get('photoProgress',{})
    frontier=photo.get('categoryFrontier',[])[:100]
    if any(not isinstance(url,str) or not re.fullmatch(r'https://commons\.wikimedia\.org/wiki/Category:[^\s]+',url) for url in frontier):raise ValueError('Unsafe photo category state')
    index=photo.get('index',[])[:600]
    if len(encoded(index))>200_000:raise ValueError('Photo discovery index budget exceeded')
    for row in index:
        if not re.fullmatch(r'https://commons\.wikimedia\.org/wiki/File:[^\s]+',row.get('url','')) or not re.fullmatch(r'[a-f0-9]{64}',row.get('indexSHA256','')):raise ValueError('Unsafe photo index entry')
    save(photos/'state.json',{'schemaVersion':1,'http':{},'documents':{},'photoProgress':{'categoryFrontier':frontier,'categoryOffset':int(photo.get('categoryOffset',0))%10000,'modelOffset':int(photo.get('modelOffset',0))%10000,'index':index}})


def merge(models, drafts, evidence):
    result=copy.deepcopy(models);by_id={x['id']:x for x in result};decisions=[]
    gates={x['id']:x for x in evidence['decisions']}
    for draft in drafts:
        # Reuse the existing partial gate; an identity conflict never becomes a model.
        partial,reasons=admit_partial(draft,evidence)
        strict=gates[draft['id']]['status']=='publishable'
        if partial is None and not strict:
            decisions.append({'id':draft['id'],'decision':'pending','reasons':reasons});continue
        key={'og107-utility':'og107-utility-top'}.get(draft['id'],draft['id'])
        if key not in by_id:
            # Every newly discovered model starts in Explorer only. No automatic promotion.
            if partial is None:
                decisions.append({'id':key,'decision':'pending','reasons':reasons});continue
            admitted=copy.deepcopy(partial)
            admitted['id']=key
            result.append(admitted);by_id[key]=admitted
            decisions.append({'id':key,'decision':'new-verified-model','identifierEligible':bool(admitted['criteria'])});continue
        current=by_id[key]; old_criteria=copy.deepcopy(current['criteria']);old_publication=copy.deepcopy(current.get('publication'))
        # Existing editorial identity, variants, summary and Identifier criteria are never replaced.
        existing_urls={s['url']:s['id'] for s in current['sources']};mapping={};added=[]
        for source in draft['sources']:
            sid=existing_urls.get(source['url']) or 'auto-'+digest(source['url'].encode())[:16]
            mapping[source['id']]=sid
            if source['url'] not in existing_urls:
                item=copy.deepcopy(source);item['id']=sid;current['sources'].append(item);existing_urls[source['url']]=sid;added.append(source['url'])
        sections=current['sections'];section=next((s for s in sections if s['title']=='Compléments documentaires automatiques'),None)
        known={fact_identity(f) for s in sections for f in s['facts']};facts=[]
        for source_section in draft['sections']:
            for original in source_section['facts']:
                fact=copy.deepcopy(original);fact['sources']=[mapping[x] for x in fact['sources']]
                if fact_identity(fact) not in known: facts.append(fact);known.add(fact_identity(fact))
        if facts:
            if section is None:section={'title':'Compléments documentaires automatiques','facts':[]};sections.append(section)
            section['facts'].extend(facts)
        assert current['criteria']==old_criteria and current.get('publication')==old_publication
        decisions.append({'id':key,'decision':'verified-enrichment' if added or facts else 'unchanged','addedReferences':added,'addedFacts':len(facts)})
    return result,decisions


def admit_photos(models,intake,root,images):
    decisions=[];by_id={x['id']:x for x in models}
    for row in intake.get('candidates',[]):
        reason=None
        if row.get('modelID') not in by_id: reason='unknown-model'
        elif not row.get('license',{}).get('allowed') or not row.get('identity',{}).get('allowed'):reason='rights-or-identity-not-established'
        elif not row.get('downloaded'):reason='original-not-verified'
        elif row.get('view') not in ['front','back','label','construction','general-unconfirmed']:reason='unsupported-photo-view'
        elif by_id[row['modelID']]['illustration'].get('filename'):reason='existing-main-photo-preserved'
        elif any(k not in row for k in ['localPath','sha256','sourceSHA1','sourceBytes','metadataSHA256','author','sourceURL','originalURL']):reason='incomplete-photo-provenance'
        if reason:decisions.append({'id':row.get('id'),'decision':'pending','reason':reason});continue
        relative=row['localPath']
        if not re.fullmatch(r'images/commons-[0-9]+\.jpg',relative):raise ValueError('Unsafe photograph path')
        data=(root/relative).read_bytes()
        if digest(data)!=row['sha256'] or hashlib.sha1(data).hexdigest()!=row['sourceSHA1'] or len(data)!=row['sourceBytes']:raise ValueError('Photograph integrity failure')
        licence=row['license'];allowed={'CC BY 3.0','CC BY 4.0','CC BY-SA 4.0','CC BY-SA 3.0','CC0 1.0'}
        if licence.get('license') not in allowed or not row.get('author') or not row.get('metadataSHA256'):raise ValueError('Incomplete photograph provenance')
        model=by_id[row['modelID']];name='catalogue-'+digest(model['id'].encode())[:16]+'-'+row['id']+'.jpg';images[name]=data
        sid='photo-'+row['id'];model['sources'].append({'id':sid,'title':row['title'],'institution':row['author']+' / Wikimedia Commons','url':row['sourceURL'],'locator':'Source metadata SHA-256 '+row['metadataSHA256'],'summary':'Attribution explicite par la source ; variante non établie. Aucun critère Identifier déduit de la photographie.'})
        view={'front':'vue de face selon la source','back':'vue de dos selon la source','label':'étiquette selon la source','construction':'détail selon la source','general-unconfirmed':'vue non précisée'}[row['view']]
        model['illustration']={'filename':'images/'+name,'title':model['name']+' · '+view,'credit':(row.get('attribution') or row['author'])+' / Wikimedia Commons · original non modifié','license':licence['license'],'licenseURL':licence['licenseURL'],'url':row['sourceURL'],'provenance':{'requiredAttribution':row.get('attribution',row['author']),'institution':licence.get('credit','Wikimedia Commons'),'license':licence,'author':row['author'],'sourceURL':row['sourceURL'],'originalURL':row['originalURL'],'sha256':row['sha256'],'sourceSHA1':row['sourceSHA1'],'metadataSHA256':row['metadataSHA256'],'variant':'Non établie','view':row['view'],'collectionPhoto':False}}
        decisions.append({'id':row['id'],'decision':'verified-documentary-photo','modelID':model['id']})
    return decisions


def publish_local(repo,distribution):
    """Publish atomically in a test Git catalogue. No network remote is accepted."""
    remote=command(['git','-C',repo,'remote','get-url','origin']).stdout.strip()
    if not Path(remote).is_absolute() or not Path(remote).is_dir():raise ValueError('Local test publication requires an absolute local Git remote')
    cat=repo/'catalogue'; envelope=json.loads((distribution/'manifest.json').read_text());manifest=json.loads(base64.b64decode(envelope['payload']))
    targets=[]
    for f in manifest['files']:
        rel=manifest['contentPath']+f['path'];dest=cat/rel
        if dest.exists() and dest.read_bytes()!=(distribution/rel).read_bytes():raise ValueError('Immutable version already exists with different bytes')
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(distribution/rel,dest);targets.append('catalogue/'+rel)
    shutil.copyfile(distribution/'manifest.json',cat/'manifest.json');targets.append('catalogue/manifest.json')
    command(['git','-C',repo,'add','--',*targets])
    staged=command(['git','-C',repo,'diff','--cached','--name-only']).stdout.splitlines()
    if set(staged)-set(targets):raise ValueError('Unexpected staged file')
    if staged:
        command(['git','-C',repo,'-c','user.name=Field Archive test','-c','user.email=fieldarchive-test@localhost','commit','-m',f"Catalogue local vérifié {manifest['sequence']}"])
        command(['git','-C',repo,'push','origin','HEAD:main'])
    return command(['git','-C',repo,'rev-parse','HEAD']).stdout.strip()


def prepare_reviewed_lot(repo,work,baseline_manifest,baseline,images,force=False):
    policy,body,register=documentary_gate.load_policy()
    if not force and encoded(sorted(baseline,key=lambda m:m['id']))==encoded(body['models']):return False
    # Initial delivery is the exact qualified snapshot, with no discovery or OCR rerun.
    intact=encoded(sorted(baseline,key=lambda m:m['id']))==encoded(body['models'])
    intake=work/'intake';seed_research_state(repo,intake)
    if not (intake/'state.json').exists():save(intake/'state.json',{'documents':{}})
    seed_photo_state(repo,work/'photos')
    if not (work/'photos/state.json').exists():save(work/'photos/state.json',{'photoProgress':{}})
    pending=json.loads((ROOT/'config/designation-quarantine.json').read_text())
    save(work/'designation-quarantine.json',pending)
    result={'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'baselineSequence':baseline_manifest['sequence'],
      'sourceMetrics':{'mode':'approved-lot-replay','networkRequests':0,'networkBytes':0,'discoveredReferences':0,'newReferences':0,'errors':0},
      'photoMetrics':{'models':len(body['models']),'modelsSearched':0,'downloaded':0},'sourceErrors':[],
      'photoResearchQueue':[{'modelID':m['id'],'missingViews':[] if m['illustration'].get('filename') else ['front']} for m in body['models']],
      'levels':{level:sum(m['publication']['documentationLevel']==level for m in body['models']) for level in ['reference','documented','identifiable']},
      'notices':len(body['models']),'networkIntakePerformedThisRun':False,'productionPublicationPerformed':False,'additionalPaidServiceCostEUR':0,
      'signed':False,'preparedWithoutSigningSecret':True,'documentaryPolicy':policy['id'],'designationQuarantineCount':len(pending),
      'status':'unchanged' if intact else 'valid-change','sequence':baseline_manifest['sequence'] if intact else documentary_gate.next_sequence(repo)}
    if not intact:
      save(work/'candidate.json',body);out=work/'images';out.mkdir(exist_ok=True)
      for name,data in images.items():(out/name).write_bytes(data)
      documentary_gate.check_body(body,out)
    save(work/'result.json',result);print(json.dumps({'status':result['status'],'notices':len(body['models']),'signed':False,'networkRequests':0}));return True

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,required=True);ap.add_argument('--work',type=Path,required=True)
    ap.add_argument('--publisher',type=Path);ap.add_argument('--key-file',type=Path);ap.add_argument('--reader',type=Path,required=True);ap.add_argument('--trust',type=Path,required=True)
    ap.add_argument('--prepare-only',action='store_true')
    ap.add_argument('--approved-only',action='store_true');ap.add_argument('--live',action='store_true');ap.add_argument('--intake',type=Path);ap.add_argument('--photo-intake',type=Path);ap.add_argument('--publish-local',action='store_true')
    a=ap.parse_args();repo=a.repo.resolve();work=a.work.resolve();work.mkdir(parents=True,exist_ok=True)
    # Enforce private-key permissions before a signing operation. Never print key data.
    key=None
    if not a.prepare_only:
        if not a.key_file or not a.publisher:raise ValueError('Signing tools absent')
        key=a.key_file.resolve();mode=key.stat().st_mode
        if not stat.S_ISREG(mode) or mode&0o077 or key.stat().st_size!=32:raise ValueError('Signing key must be a private 32-byte regular file')
    lock=work/'.run.lock'
    lock_handle=lock.open('a+')
    fcntl.flock(lock_handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
        command([a.reader,'validate',repo/'catalogue',a.trust,work/'baseline-validation'])
        m,baseline,images=read_baseline(repo)
        if a.prepare_only and prepare_reviewed_lot(repo,work,m,baseline,images,force=a.approved_only):return
        intake=a.intake.resolve() if a.intake else work/'intake'
        photos=a.photo_intake.resolve() if a.photo_intake else work/'photos'
        if a.live:
            seed_research_state(repo,intake)
            command(['node',ROOT/'engine/network_collector.mjs','--config',ROOT/'config/sources.json','--state',intake])
        if a.live:
            command(['node',ROOT/'tools/collect-reference-pdfs.mjs',ROOT/'config/sources.json',intake,os.environ['FIELDARCHIVE_PDF_TEXT']])
            command(['node',ROOT/'tools/collect-historical.mjs',ROOT/'config/historical-sources.json',intake,os.environ['FIELDARCHIVE_PDF_TEXT']])
        live_report=json.loads((intake/'last-run.json').read_text())
        if live_report['metrics']['mode']!='live-network':raise ValueError('No verified research intake')
        doc=work/'documentary';documentary_run(intake/'corpus',doc,quality=True,support_corpora=[ROOT/'engine/corpus'])
        evidence=json.loads((doc/'documentary-catalogue.json').read_text());drafts=json.loads((doc/'explorer-drafts.json').read_text())['models']
        models,decisions=merge(baseline,drafts,evidence)
        historical=intake/'historical-attestations.json'
        attestations=json.loads(historical.read_text()) if historical.exists() else []
        bootstrap=ROOT/'config/bootstrap-attestations.json'
        if bootstrap.exists():
            if bootstrap.stat().st_size>1_000_000:raise ValueError('Historical bootstrap exceeds budget')
            seed=json.loads(bootstrap.read_text())
            if seed.get('synthetic') or seed.get('schemaVersion')!=1:raise ValueError('Invalid historical bootstrap')
            attestations.extend(seed['attestations'])
            known={m['id'] for m in models}
            for model in seed.get('documentedModels',[]):
                if model['id'] in known:continue
                if model.get('criteria') or model['publication']['identifierEligible'] or model['publication'].get('documentationLevel') not in ['reference','documented']:raise ValueError('Bootstrap cannot grant Identifier admission')
                models.append(copy.deepcopy(model));known.add(model['id'])
        for capture in sorted((intake/'corpus').glob('*.json')):
            attestations.extend(extract_network_capture(json.loads(capture.read_text())))
        attestations,evidence_quarantine=documentary_gate.filter_attestations(attestations)
        models,index_decisions=merge_index(models,attestations)
        state_file=intake/'state.json'
        if state_file.exists():
            state=json.loads(state_file.read_text())
            for item in state.get('documents',{}).values():
                if item.get('corpusSHA256'):item['indexedVersion']='historical-index-v1'
            save(state_file,state)
        if a.live:
            seed_photo_state(repo,photos)
            save(work/'photo-models.json',{'models':models})
            command(['node',ROOT/'tools/collect-photos.mjs',work/'photo-models.json',photos])
        photo_report=json.loads((photos/'photo-intake.json').read_text())
        photo_decisions=admit_photos(models,photo_report,photos,images)
        models,model_quarantine=documentary_gate.quarantine_models(models)
        save(work/'designation-quarantine.json',evidence_quarantine+model_quarantine)
        documentary_gate.check_body({'models':models})
        result={'checkedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'baselineSequence':m['sequence'],'noticeDecisions':decisions,'photoDecisions':photo_decisions,'sourceMetrics':live_report['metrics'],'photoMetrics':photo_report['metrics'],'networkIntakePerformedThisRun':a.live,'additionalPaidServiceCostEUR':0,'productionPublicationPerformed':False}
        result['historicalDecisions']=index_decisions
        result['designationQuarantineCount']=len(evidence_quarantine)+len(model_quarantine)
        result['levels']={level:sum(m['publication'].get('documentationLevel')==level for m in models) for level in ['reference','documented','identifiable']}
        historical_report=intake/'historical-report.json'
        if historical_report.exists():result['historicalResearch']=json.loads(historical_report.read_text())
        reference_report=intake/'full-reference-report.json'
        if reference_report.exists():result['fullReferenceResearch']=json.loads(reference_report.read_text())
        result['sourceErrors']=[{k:e.get(k) for k in ['where','error','status','at']} for e in live_report.get('events',[]) if e.get('kind')=='error'][:30]
        result['photoResearchQueue']=[{'modelID':model['id'],'missingViews':([] if model['illustration'].get('filename') else ['front'])+['back','label','construction']} for model in models]
        if encoded(sorted(models,key=lambda x:x['id']))==encoded(sorted(baseline,key=lambda x:x['id'])):
            result.update(status='unchanged',sequence=m['sequence'],signed=False);save(work/'result.json',result);print(json.dumps({'status':'unchanged','signed':False}));return
        candidate=work/'candidate.json';save(candidate,{'version':1,'models':models});image_root=work/'images';image_root.mkdir(exist_ok=True)
        for name,data in images.items():(image_root/name).write_bytes(data)
        if a.prepare_only:
            result.update(status='valid-change',sequence=documentary_gate.next_sequence(repo),signed=False,notices=len(models),preparedWithoutSigningSecret=True)
            save(work/'result.json',result);print(json.dumps({'status':'valid-change','signed':False,'preparedWithoutSigningSecret':True}));return
        distribution=work/'distribution'
        if distribution.exists():raise ValueError('Fresh work directory required for a changed release')
        documentary_gate.check_body(json.loads(candidate.read_text()),image_root)
        command([a.publisher,'package',candidate,image_root,distribution,key,documentary_gate.next_sequence(repo)])
        command([a.reader,'validate',distribution,a.trust,work/'candidate-validation'])
        result.update(status='valid-change',sequence=documentary_gate.next_sequence(repo),signed=True,notices=len(models))
        if a.publish_local:result['localPublishedCommit']=publish_local(repo,distribution)
        save(work/'result.json',result);print(json.dumps({k:result[k] for k in ['status','sequence','signed','notices']}))
    finally:lock_handle.close()

if __name__=='__main__':main()
