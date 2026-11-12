#!/bin/bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
automation_root="$(cd "$(dirname "$0")/.." && pwd)"
research_work="${RUNNER_TEMP:?}/fieldarchive-research"
mode="${1:?research, validate, sign or verify}"
case "$mode" in
research)
    swiftc -parse-as-library "$automation_root/tools/PDFText.swift" -o "$RUNNER_TEMP/fieldarchive-pdf-text"
    export FIELDARCHIVE_PDF_TEXT="$RUNNER_TEMP/fieldarchive-pdf-text"
    swiftc -swift-version 6 "$automation_root"/tools/native/*.swift -o "$RUNNER_TEMP/fieldarchive-reader"
    python3 "$automation_root/weekly.py" --repo "$GITHUB_WORKSPACE" --work "$research_work" \
      --reader "$RUNNER_TEMP/fieldarchive-reader" --trust "$automation_root/config/Distribution.json" --live --prepare-only
    python3 - "$research_work" "$RUNNER_TEMP/fieldarchive-transfer" <<'PY'
from pathlib import Path
import hashlib,json,shutil,sys
work,out=map(Path,sys.argv[1:]);out.mkdir()
result=json.loads((work/'result.json').read_text());shutil.copyfile(work/'result.json',out/'result.json')
shutil.copyfile(work/'designation-quarantine.json',out/'designation-quarantine.json')
state=json.loads((work/'intake/state.json').read_text())
documents=sorted(state.get('documents',{}).values(),key=lambda d:(not d.get('status','').startswith('pending'),d.get('firstSeen','')))
photo_state=json.loads((work/'photos/state.json').read_text())
(out/'checkpoint-input.json').write_text(json.dumps({'knownReferences':sorted(state.get('documents',{}))[:1000],'discoveryCursors':state.get('discoveryCursors',{}),'sourceTurn':state.get('sourceTurn',0),'sourceCooldowns':state.get('sourceCooldowns',{}),'primaryProgress':state.get('primaryProgress',{}),'photoProgress':photo_state.get('photoProgress',{}),'documentQueue':[{k:d[k] for k in ['id','status','firstSeen','lastSeen','sourceID','title','blockedUntil','fileDescriptor','indexedVersion'] if k in d} for d in documents[:500]]}))
if result['status']=='valid-change':
 shutil.copyfile(work/'candidate.json',out/'candidate.json');shutil.copytree(work/'images',out/'images')
files={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file()}
(out/'transfer-sha256.json').write_text(json.dumps(files,sort_keys=True))
if sum(p.stat().st_size for p in out.rglob('*') if p.is_file())>64_000_000:raise SystemExit('Transfer budget exceeded')
PY
    ;;
validate)
    # This fresh runner never executes code received in the artifact.
    python3 - "$RUNNER_TEMP/fieldarchive-transfer" "$research_work" <<'PY'
from pathlib import Path
import hashlib,json,re,shutil,sys
source,work=map(Path,sys.argv[1:]);work.mkdir()
files=list(source.rglob('*'));allow=r'(result\.json|candidate\.json|checkpoint-input\.json|transfer-sha256\.json|designation-quarantine\.json|images/[A-Za-z0-9_-]+\.(jpg|jpeg|png))'
if any(p.is_symlink() or (p.is_file() and not re.fullmatch(allow,str(p.relative_to(source)))) for p in files):raise SystemExit('Unexpected transfer path')
if sum(p.stat().st_size for p in files if p.is_file())>64_000_000:raise SystemExit('Transfer budget exceeded')
manifest=json.loads((source/'transfer-sha256.json').read_text())
actual={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file() and p.name!='transfer-sha256.json'}
if actual!=manifest:raise SystemExit('Transfer integrity failure')
for name in ['result.json','checkpoint-input.json','designation-quarantine.json']:shutil.copyfile(source/name,work/name)
result=json.loads((work/'result.json').read_text())
if result['status'] not in ['unchanged','valid-change'] or result['signed']:raise SystemExit('Unexpected research result')
if result['status']=='valid-change':
 body=json.loads((source/'candidate.json').read_text());raw=json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
 if len(raw)>4_000_000 or not 1<=len(body['models'])<=1000:raise SystemExit('Candidate budget exceeded')
 (work/'candidate.json').write_bytes(raw);shutil.copytree(source/'images',work/'images')
PY
    swiftc -parse-as-library "$automation_root/tools/Publisher.swift" -o "$RUNNER_TEMP/fieldarchive-publisher"
    swiftc -swift-version 6 "$automation_root"/tools/native/*.swift -o "$RUNNER_TEMP/fieldarchive-reader"
    "$RUNNER_TEMP/fieldarchive-reader" validate "$GITHUB_WORKSPACE/catalogue" "$automation_root/config/Distribution.json" "$research_work/baseline-validation"
    if [ -f "$research_work/candidate.json" ]; then
      python3 "$automation_root/engine/designation_gate.py" check --candidate "$research_work/candidate.json" --images "$research_work/images" --receipt "$research_work/documentary-validation.json"
    fi
    # Complete native validation with a disposable key, before the production secret exists.
    if python3 -c 'import json,sys;sys.exit(json.load(open(sys.argv[1]))["status"]!="valid-change")' "$research_work/result.json"; then
      test_private="$(mktemp -d "$RUNNER_TEMP/fieldarchive-unsigned-check.XXXXXX")"
      trap 'rm -rf "$test_private"' EXIT
      "$RUNNER_TEMP/fieldarchive-publisher" keygen "$test_private/key" "$test_private/trust.json"
      sequence="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["sequence"])' "$research_work/result.json")"
      "$RUNNER_TEMP/fieldarchive-publisher" package "$research_work/candidate.json" "$research_work/images" "$research_work/test-distribution" "$test_private/key" "$sequence"
      "$RUNNER_TEMP/fieldarchive-reader" validate "$research_work/test-distribution" "$test_private/trust.json" "$research_work/unsigned-native-validation"
      python3 - "$research_work" <<'PY'
from pathlib import Path
import hashlib,json,sys
work=Path(sys.argv[1]);paths=[work/'candidate.json',*sorted((work/'images').glob('*'))]
(work/'unsigned-input-sha256.json').write_text(json.dumps({str(p.relative_to(work)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},sort_keys=True))
PY
    fi
    ;;
sign)
    # The network collector ran on another runner and never had this secret.
    python3 - "$research_work" <<'PY'
from pathlib import Path
import hashlib,json,sys
work=Path(sys.argv[1]);result=json.loads((work/'result.json').read_text())
if result['status']=='valid-change':
 hashes=json.loads((work/'unsigned-input-sha256.json').read_text())
 if any(hashlib.sha256((work/path).read_bytes()).hexdigest()!=value for path,value in hashes.items()):raise SystemExit('Validated signing input changed')
PY
    if python3 -c 'import json,sys;sys.exit(json.load(open(sys.argv[1]))["status"]!="valid-change")' "$research_work/result.json"; then
      python3 "$automation_root/engine/designation_gate.py" receipt --candidate "$research_work/candidate.json" --images "$research_work/images" --receipt "$research_work/documentary-validation.json"
      secret_root="$(mktemp -d "$RUNNER_TEMP/fieldarchive-key.XXXXXX")"
      trap 'rm -rf "$secret_root"' EXIT
      chmod 700 "$secret_root"
      python3 - "$secret_root/signing.key" <<'PY'
import base64,os,sys
key=base64.b64decode(os.environ.pop('FIELDARCHIVE_SIGNING_KEY_B64'),validate=True)
if len(key)!=32:raise SystemExit('Invalid signing key length')
fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'wb') as output:output.write(key)
PY
      unset FIELDARCHIVE_SIGNING_KEY_B64
      sequence="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["sequence"])' "$research_work/result.json")"
      "$RUNNER_TEMP/fieldarchive-publisher" package "$research_work/candidate.json" "$research_work/images" "$research_work/distribution" "$secret_root/signing.key" "$sequence"
    else
      unset FIELDARCHIVE_SIGNING_KEY_B64
      echo 'No catalogue change; signing skipped.'
    fi
    ;;
verify)
    if python3 -c 'import json,sys;sys.exit(json.load(open(sys.argv[1]))["status"]!="valid-change")' "$research_work/result.json"; then
      # The production signing process has exited and its private file has been removed.
      "$RUNNER_TEMP/fieldarchive-reader" validate "$research_work/distribution" "$automation_root/config/Distribution.json" "$research_work/candidate-validation"
      python3 "$automation_root/engine/designation_gate.py" distribution --root "$research_work/distribution"
      python3 - "$research_work/result.json" <<'PY'
from pathlib import Path
import json,sys
p=Path(sys.argv[1]);r=json.loads(p.read_text());r['signed']=True;p.write_text(json.dumps(r,sort_keys=True))
PY
    fi
    ;;
*) exit 2 ;;
esac
