#!/usr/bin/env python3
"""Approved GitHub-only publishing gate; invoked by the prepared scheduled workflow."""
import argparse,base64,datetime,hashlib,json,os,re,shutil,subprocess
from pathlib import Path

REPOSITORY='alexpauf/field-archive-catalogue-test'
def git(repo,*args,env=None):return subprocess.run(['git','-C',str(repo),*map(str,args)],env=env,check=True,capture_output=True,text=True).stdout.strip()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--repo',type=Path,required=True);ap.add_argument('--work',type=Path,required=True);a=ap.parse_args();repo=a.repo.resolve();work=a.work.resolve()
 if os.environ.get('FIELDARCHIVE_WEEKLY_APPROVED')!='true' or os.environ.get('GITHUB_REPOSITORY')!=REPOSITORY or os.environ.get('GITHUB_REF')!='refs/heads/main' or os.environ.get('GITHUB_EVENT_NAME') not in ['schedule','workflow_dispatch']:raise ValueError('Automatic GitHub publication has not been explicitly enabled')
 remote=git(repo,'remote','get-url','origin').rstrip('/')
 if remote.endswith('.git'):remote=remote[:-4]
 if remote!='https://github.com/'+REPOSITORY:raise ValueError('Unexpected publication repository')
 if git(repo,'status','--porcelain'):raise ValueError('Dirty publication checkout')
 result=json.loads((work/'result.json').read_text());targets=[]
 if result['status']=='valid-change':
  dist=work/'distribution';envelope=json.loads((dist/'manifest.json').read_text());m=json.loads(base64.b64decode(envelope['payload'],validate=True))
  old=json.loads(base64.b64decode(json.loads((repo/'catalogue/manifest.json').read_text())['payload'],validate=True))
  if m['sequence']!=old['sequence']+1 or not re.fullmatch(r'releases/v[0-9]+-[a-f0-9]{20}/',m['contentPath']):raise ValueError('Sequence or content path changed')
  # Native reader validation is a prerequisite; only its exact output is publishable.
  proof=json.loads((work/'candidate-validation/validation.json').read_text())
  if not proof['signedAndCompatible'] or proof['sequence']!=m['sequence'] or proof['manifestSHA256']!=hashlib.sha256((dist/'manifest.json').read_bytes()).hexdigest():raise ValueError('Native compatibility proof absent')
  for f in m['files']:
   if not re.fullmatch(r'(notices/[a-z0-9-]+\.json|references\.json|images/[A-Za-z0-9_-]+\.(jpg|jpeg|png))',f['path']):raise ValueError('Disallowed release file')
   data=(dist/m['contentPath']/f['path']).read_bytes()
   if len(data)!=f['bytes'] or hashlib.sha256(data).hexdigest()!=f['sha256']:raise ValueError('Release changed after validation')
   relative='catalogue/'+m['contentPath']+f['path'];dest=repo/relative
   if dest.exists() and dest.read_bytes()!=data:raise ValueError('Immutable release collision')
   dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data);targets.append(relative)
  (repo/'catalogue/manifest.json').write_bytes((dist/'manifest.json').read_bytes());targets.append('catalogue/manifest.json')
 elif result['status']!='unchanged':raise ValueError('No successful documentary result')
 # A bounded provenance checkpoint records actual research even when no release changes.
 # It is not a new catalogue version and contains no OCR or private information.
 state='automation/state/checkpoint.json';dest=repo/state;dest.parent.mkdir(parents=True,exist_ok=True)
 prior=json.loads(dest.read_text()) if dest.exists() else {}
 intake=work/'checkpoint-input.json';documents={key:None for key in json.loads(intake.read_text()).get('knownReferences',[])} if intake.exists() else {}
 known=sorted(set(prior.get('knownReferences',[]))|set(documents))
 checkpoint={'checkedAt':result['checkedAt'],'catalogueSequence':result['sequence'],'catalogueChanged':result['signed'],'knownReferences':known,'newReferencesSincePreviousCheckpoint':len(set(documents)-set(prior.get('knownReferences',[]))),'photoResearchQueue':result['photoResearchQueue'],'photoResearch':result['photoMetrics'],'documentaryMetrics':{k:v for k,v in result['sourceMetrics'].items() if k not in ['catalogue','heapNote']},'runID':os.environ.get('GITHUB_RUN_ID')}
 checkpoint['documentaryErrors']=result.get('sourceErrors',[])
 dest.write_text(json.dumps(checkpoint,indent=2)+'\n');targets.append(state)
 git(repo,'add','--',*targets)
 if set(git(repo,'diff','--cached','--name-only').splitlines())-set(targets):raise ValueError('Unexpected staged paths')
 if not git(repo,'diff','--cached','--name-only'):return
 git(repo,'-c','user.name=FIELD ARCHIVE catalogue','-c','user.email=fieldarchive-catalogue@users.noreply.github.com','commit','-m','Weekly verified catalogue' if result['signed'] else 'Weekly documentary research checkpoint; catalogue unchanged')
 token=os.environ.get('GITHUB_TOKEN','')
 if not token:raise ValueError('Ephemeral repository token absent')
 env=dict(os.environ);env.pop('GIT_TRACE',None);env.pop('GIT_CURL_VERBOSE',None)
 env.update(GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',GIT_CONFIG_VALUE_0='AUTHORIZATION: basic '+base64.b64encode(('x-access-token:'+token).encode()).decode())
 # One normal fast-forward push makes the release tree and manifest atomic. Never force.
 git(repo,'push','origin','HEAD:refs/heads/main',env=env)
 print('Verified catalogue/checkpoint published. Existing release directories retained.')
if __name__=='__main__':main()
