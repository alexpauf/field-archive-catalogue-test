"""Fail-closed documentary boundary: a research artifact cannot approve its own contents."""
import argparse,base64,copy,hashlib,json,re,unicodedata
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class DocumentaryRejection(ValueError):pass
def encoded(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
def digest(value):return hashlib.sha256(encoded(value)).hexdigest()
def read(path):return json.loads(Path(path).read_bytes())
def write(path,value):Path(path).write_bytes(encoded(value)+b'\n')
def lexical(value):
 value=unicodedata.normalize('NFKD',value).casefold().replace('’',"'")
 value=re.sub(r"\b([a-z]+)'s\b",r'\1',value)
 singular={'gloves':'glove','mittens':'mitten','shirts':'shirt','coveralls':'coverall','overalls':'overall'}
 return ' '.join(sorted(singular.get(w,w) for w in re.findall(r'[a-z0-9]+',value)))
def synthetic(value):
 if isinstance(value,dict):return any(k in ['synthetic','testOnly'] and v is True or synthetic(v) for k,v in value.items())
 if isinstance(value,list):return any(map(synthetic,value))
 return False
def load_policy():
 config=ROOT/'config';policy=read(config/'designation-policy.json')
 for name,key in [('approved-lot.json','lotFileSHA256'),('designation-register.json','registerFileSHA256')]:
  data=(config/name).read_bytes()
  if hashlib.sha256(data).hexdigest()!=policy[key]:raise DocumentaryRejection('Trusted documentary approval file changed')
 body=read(config/'approved-lot.json');register=read(config/'designation-register.json')
 models=body['models'];proofs={p['id']:p for p in register['proofs']}
 if synthetic(body) or len(models)!=policy['noticeCount'] or {m['id'] for m in models}!=set(proofs):raise DocumentaryRejection('Approval lot and proof register disagree')
 groups={};eligible=[]
 for m in models:
  if m['publication']['identifierEligible']:eligible.append(m['id'])
  elif m['criteria']:raise DocumentaryRejection('Non-Identifier model contains criteria')
  if not proofs[m['id']]['originalDesignations'] or not proofs[m['id']]['sourceURLs']:raise DocumentaryRejection('Original designation or traceable source absent')
  for name in [m['name'],*m.get('indexMetadata',{}).get('aliases',[])]:
   k=lexical(name)
   if k in groups and groups[k]!=m['id']:raise DocumentaryRejection('Duplicate original designation')
   groups[k]=m['id']
 if sorted(eligible)!=sorted(policy['protectedIdentifierCriteria']):raise DocumentaryRejection('Identifier eligibility changed')
 for m in models:
  if m['id'] in policy['protectedIdentifierCriteria'] and digest(m['criteria'])!=policy['protectedIdentifierCriteria'][m['id']]:raise DocumentaryRejection('Protected Identifier criteria changed')
 return policy,body,register

def check_body(body,images=None):
 policy,approved,register=load_policy()
 if body.get('minimumReaderVersion',3)!=3:raise DocumentaryRejection('Unexpected minimum reader version')
 if synthetic(body) or not isinstance(body.get('models'),list):raise DocumentaryRejection('Synthetic or malformed catalogue')
 if digest(sorted(body['models'],key=lambda m:m['id']))!=digest(approved['models']):raise DocumentaryRejection('Catalogue contains an unapproved, missing or altered documentary record')
 if images:
  root=Path(images);actual={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.is_file()}
  if actual!=policy['imageSHA256']:raise DocumentaryRejection('Illustrations differ from approved rights-and-credit-preserving lot')
 return {'schemaVersion':1,'passed':True,'policyID':policy['id'],'policySHA256':digest(policy),'modelsSHA256':digest(approved['models']),'notices':len(approved['models']),'imageSHA256':policy['imageSHA256']}

def verify_receipt(body,images,receipt):
 expected=check_body(body,images)
 if read(receipt)!=expected:raise DocumentaryRejection('Documentary receipt absent, stale or forged')
 return expected

def quarantine_models(incoming):
 """A new or altered description stays outside the sealed lot; valid records remain available."""
 _,approved,_=load_policy();known={m['id']:m for m in approved['models']};quarantine=[]
 for m in incoming:
  mid=m.get('id')
  if mid not in known or digest(m)!=digest(known[mid]):
   quarantine.append({'modelID':mid,'designation':m.get('name'),'reason':'Complete original designation / exact reviewed description not qualified for this lot','sourceURLs':[s.get('url') for s in m.get('sources',[])],'recordSHA256':digest(m)})
 return copy.deepcopy(approved['models']),quarantine

def filter_attestations(rows):
 _,_,register=load_policy();allowed={digest(a) for p in register['proofs'] for a in p.get('attestations',[])};good=[];pending=[]
 for row in rows:
  if digest(row) in allowed and not synthetic(row):good.append(row)
  else:pending.append({'designation':row.get('designation'),'sourceURL':row.get('sourceURL'),'documentSHA256':row.get('documentSHA256'),'page':row.get('page'),'reason':'Full heading not covered by verified source-boundary approval','evidenceSHA256':digest(row)})
 return good,pending

def next_sequence(repo):
 m=read(Path(repo)/'catalogue/manifest.json');active=json.loads(base64.b64decode(m['payload'],validate=True))['sequence'];seen=[active]
 for p in (Path(repo)/'catalogue/releases').iterdir():
  match=re.fullmatch(r'v([1-9][0-9]*)-[a-f0-9]{20}',p.name)
  if match and p.is_dir() and not p.is_symlink():seen.append(int(match[1]))
 return max(seen)+1

def verify_distribution(root):
 policy,_,_=load_policy();root=Path(root);env=read(root/'manifest.json');m=json.loads(base64.b64decode(env['payload'],validate=True));models=[];images={}
 if m.get('minimumReaderVersion')!=3 or m.get('schemaVersion')!=2:raise DocumentaryRejection('Unexpected signed reader contract')
 for item in m['files']:
  path=item['path']
  if not re.fullmatch(r'notices/[a-z0-9-]+\.json|images/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png)|references\.json',path):raise DocumentaryRejection('Invalid release path')
  data=(root/m['contentPath']/path).read_bytes()
  if len(data)!=item['bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:raise DocumentaryRejection('Signed release bytes changed')
  if path.startswith('notices/'):models.append(json.loads(data))
  if path.startswith('images/'):images[Path(path).name]=hashlib.sha256(data).hexdigest()
 result=check_body({'models':models})
 if images!=policy['imageSHA256'] or len(m['modelIDs'])!=result['notices']:raise DocumentaryRejection('Release inventory differs from approved lot')
 return result

def main():
 ap=argparse.ArgumentParser();ap.add_argument('operation',choices=['check','receipt','distribution']);ap.add_argument('--candidate',type=Path);ap.add_argument('--images',type=Path);ap.add_argument('--receipt',type=Path);ap.add_argument('--root',type=Path);a=ap.parse_args()
 if a.operation=='distribution':result=verify_distribution(a.root)
 elif a.operation=='receipt':result=verify_receipt(read(a.candidate),a.images,a.receipt)
 else:
  result=check_body(read(a.candidate),a.images)
  if a.receipt:write(a.receipt,result)
 print('PASS · documentary designation gate: '+str(result['notices'])+' exact approved records; no unqualified heading.')
if __name__=='__main__':main()
