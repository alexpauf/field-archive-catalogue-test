#!/usr/bin/env python3
"""Phase 49. Deterministic evidence extraction, never imports an existing notice.

Only reads corpus/*.json. No app, SwiftData, iCloud, device, network or AI access.
The source adapters below are intentionally narrow and fail closed.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import html
import json
from pathlib import Path
import re
import resource
import time
import unicodedata

ROOT = Path(__file__).resolve().parent
RULESET = 'phase49-rules-1'
LINE = re.compile(r'^L(\d+)(?:@P([\d-]+))?: (.*)$', re.M)
TOKEN = re.compile(r'\bM[\s-]?(19\d{2}|\d{2})(?!\d)', re.I)
FIELDS = ['officialDesignation', 'aliases', 'family', 'function', 'period',
          'construction', 'fabric', 'color', 'closure', 'pockets', 'collar', 'hood',
          'lining', 'markings', 'stock', 'contract', 'variants', 'neighbors']
LABELS = {'officialDesignation': 'Désignation documentaire', 'family': 'Famille',
          'period': 'Jalon documenté', 'construction': 'Coupe et réglages',
          'fabric': 'Matière', 'color': 'Couleur', 'closure': 'Fermeture',
          'pockets': 'Poches', 'hood': 'Capuche', 'lining': 'Doublure',
          'stock': 'Référence de stock', 'markings': 'Marquages',
          'variants': 'Variantes', 'neighbors': 'Modèles voisins'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode(value))


def plain(text):
    text = re.sub(r'【\d+†([^†】]+)(?:†[^】]*)?】', r'\1', text)
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text))).strip()


def norm(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower())
                   if not unicodedata.combining(c))


def model_id(year):
    # Existing stable app identifier is preserved. This is identity, not content.
    return 'm65-field-jacket' if year == 1965 else f'm{year}-field-jacket'


def load_corpus(folder):
    docs = []
    for path in sorted(folder.glob('*.json')):
        doc = json.loads(path.read_text())
        if doc.get('ingestAllowed') is False:
            continue
        if doc.get('adapter')=='federal-pdf-v1':
            from federal_pdf import validate_capture
            validate_capture(doc, folder)
        if doc.get('adapter')=='military-ocr-v1':
            raw_path=doc.get('rawRelativePath','')
            if not re.fullmatch(r'http/[a-f0-9]{64}',raw_path):
                raise ValueError('Unsafe or missing original OCR path')
            raw=(folder.parent/raw_path).read_bytes()
            if digest(raw)!=doc['rawHTTPBodySHA256'] or hashlib.sha1(raw).hexdigest()!=doc['documentSHA1']:
                raise ValueError('Original OCR integrity failure')
            page=1;rendered=[]
            for n,line in enumerate(re.split(r'\r?\n',raw.decode('utf-8'))):
                page+=line.count('\f')
                rendered.append(f'L{n}@P{page}: '+line.replace('\f',' '))
            if doc['toolResponse']['content'][0]['text']!='\n'.join(rendered):
                raise ValueError('Rendered OCR differs from downloaded original')
            metadata_path=folder.parent/'http'/doc['metadataSHA256']
            metadata_bytes=metadata_path.read_bytes()
            if digest(metadata_bytes)!=doc['metadataSHA256']:
                raise ValueError('Metadata integrity failure')
            metadata=json.loads(metadata_bytes)
            file=next((f for f in metadata['files'] if f['name']==doc['documentFile']),None)
            if file is None or int(file['size'])!=len(raw) or file.get('sha1')!=doc['documentSHA1']:
                raise ValueError('Downloaded OCR does not match source metadata')
        text = '\n'.join(b['text'] for b in doc['toolResponse']['content'] if b['type'] == 'text')
        doc['lines'] = {int(n): {'page': p or None, 'text': plain(t), 'raw': t}
                        for n, p, t in LINE.findall(text)}
        doc['captureSHA256'] = digest(path.read_bytes())
        doc['captureFile'] = path.name
        if not doc['lines']:
            raise ValueError(f'Empty or failed capture: {path}')
        docs.append(doc)
    return docs


def window(doc, start, end):
    return ' '.join(doc['lines'][n]['text'] for n in range(start,end+1) if n in doc['lines'])


def discover(docs):
    mentions, excluded = [], []
    # No list of desired models: derive identities from model tokens + garment context.
    seen = set()
    for d in docs:
        if d['kind'] == 'media_manifest':
            continue  # alternate encoding of the same museum object, not corroboration
        for n, line in d['lines'].items():
            for m in TOKEN.finditer(line['text']):
                year = int(m[1]) if len(m[1]) == 4 else 1900 + int(m[1])
                if not 1940 <= year <= 1999:
                    continue
                context = window(d, n, n + 2)
                prefix = line['text'][max(0, m.start()-24):m.start()]
                tail = context[m.end():m.end()+65]
                if re.match(r'\s*(?:field )?cap\b', tail, re.I):
                    excluded.append({'source': d['id'], 'line': n, 'token': m[0],
                                     'reason': 'headwear: separate component, adapter not supported'})
                    continue
                garment = re.search(r'field.{0,12}jacket|jacket.{0,12}field', tail, re.I)
                heading = re.search(r'jacket,?\s*field,?\s*$', prefix, re.I)
                if not (garment or heading):
                    continue
                # A variant on the following line may belong to another object.
                local_tail = line['text'][m.end():]
                suffix = re.search(r'\b(MQ[ -]?1|women[’\x27]?s)\b', local_tail, re.I)
                key = model_id(year)
                if suffix:
                    key += '-' + re.sub('[^a-z0-9]+', '-', suffix[0].lower()).strip('-')
                sig = (d['independence'], n, m[0].lower())
                if sig in seen:
                    continue
                seen.add(sig)
                mentions.append({'id': key, 'year': year, 'alias': m[0], 'source': d['id'],
                                 'independence': d['independence'], 'line': n, 'context': context})
        # A utility candidate requires the noun: OG107 alone is never a garment ID.
        for n, line in d['lines'].items():
            context = window(d, n, n + 2)
            if re.search(r'(?:olive green\s*shade\s*107|OG[ -]?107)', context, re.I) and re.search(r'utility', context, re.I):
                sig = (d['independence'], n, 'utility')
                if sig not in seen:
                    seen.add(sig)
                    mentions.append({'id': 'og107-utility', 'year': None, 'alias': 'OG-107 Utility',
                                     'source': d['id'], 'independence': d['independence'],
                                     'line': n, 'context': context})
    from documentary_ocr import discover as discover_ocr
    extra, other_excluded = discover_ocr(docs, model_id, window)
    mentions.extend(extra)
    excluded.extend(other_excluded)
    from federal_pdf import corroborate
    mentions.extend(corroborate(docs, mentions, window))
    return mentions, excluded


def proof(doc, start, end):
    present = [n for n in doc['lines'] if start <= n <= end]
    if not present or len(present) != end-start+1:
        raise ValueError('Missing evidence lines')
    return {'source': doc['id'], 'url': doc['url'], 'captureFile': doc['captureFile'],
            'captureSHA256': doc['captureSHA256'], 'lineStart': start, 'lineEnd': end,
            'pages': sorted(set(doc['lines'][n]['page'] for n in present if doc['lines'][n]['page'])),
            'excerpt': window(doc, start, end), 'independence': doc['independence']}


def extract(docs, mentions):
    ids = {m['id'] for m in mentions}
    claims = []
    issues = []

    def add(key, field, value, doc, a, b=None, scope='model', rule='lexical-v1'):
        if key not in ids:
            return
        ev = proof(doc, a, a if b is None else b)
        claim = {'model': key, 'field': field, 'value': value, 'scope': scope,
                 'rule': rule, 'evidence': [ev]}
        claim['id'] = digest(encode(claim))[:20]
        if not any(c['id'] == claim['id'] for c in claims):
            claims.append(claim)

    # Adapters recognize source layouts; fixed line numbers are never evidence selectors.
    # Every emission is conditional on text found in the current source capture.
    for d in docs:
        if d['kind'] == 'media_manifest':
            continue
        lines = d['lines']
        for n, line in lines.items():
            t = norm(line['text'])
            if d['kind'] == 'primary_manual':
                mt = re.search(r'^11\.\s*(jacket, field, m[ -]?(19\d\d))', line['text'], re.I)
                if mt:
                    key = model_id(int(mt[2]))
                    add(key, 'officialDesignation', mt[1].rstrip('.'), d, n, rule='tm-section-heading-v1')
                    add(key, 'family', 'fieldJacket', d, n)
                    for k in range(n+1, min(n+8, max(lines)+1)):
                        if k not in lines or re.match(r'\d+\.\d*\.?\s+JACKET', lines[k]['text']):
                            break
                        tx = norm(lines[k]['text'])
                        if 'loose' in tx and 'constrict' in tx:
                            add(key, 'construction', 'Coupe ample pour superposition des couches', d, k)
                        if 'buttons at the wrists and neck' in tx and 'waist drawstrings' in tx:
                            add(key, 'construction', 'Réglages boutonnés au cou et aux poignets ; cordon de taille', d, k)
            if d['kind'] == 'museum_object':
                whole = window(d, min(lines), max(lines))
                mt = re.search(r'JACKET, FIELD, M-(19\d\d)', whole)
                if not mt:
                    continue
                key = model_id(int(mt[1]))
                scope = d['scope']
                if 'veste dite' in t:
                    if 'quatre poches' in t:
                        add(key, 'pockets', 'Quatre poches avant', d, n, scope=scope)
                    if 'coton satine' in t:
                        add(key, 'fabric', 'Coton satiné', d, n, scope=scope)
                    if 'olive drab no 7' in t:
                        add(key, 'color', 'Olive Drab No 7', d, n, scope=scope)
                    if 'sept boutons sous bande' in t:
                        add(key, 'closure', 'Sept boutons sous patte', d, n, scope=scope)
                    if 'doublure en coton' in t:
                        add(key, 'lining', 'Doublure de coton', d, n, scope=scope)
                    date = re.search(r'modele adopte en ([a-z]+ 19\d\d)', t)
                    if date:
                        add(key, 'period', {'event': 'adoption', 'subject': 'jacket', 'date': date[1]}, d, n)
                if 'etiquette' in t and '36s' in t:
                    # Extract the literal size; no model-level label interpretation.
                    size = re.search(r'"([^\"]+)"', line['text'])
                    if size:
                        add(key, 'markings', size[1], d, n, scope=scope)
                stock = re.search(r'STOCK NO ([\dA-Z-]+)', line['text'])
                if stock:
                    add(key, 'stock', stock[1], d, n, scope=scope)
            if d['kind'] != 'institutional_survey':
                continue
            # Resolve named paragraphs, not arbitrary nearest model mentions.
            if 'm-1943 field ensemble' in t:
                a, b = max(min(lines), n-1), min(max(lines), n+4)
                ctx = norm(window(d, a, b))
                key = model_id(1943)
                if 'cotton sateen' in ctx:
                    add(key, 'fabric', 'Coton satiné', d, a, b, scope='ensemble-with-explicit-jacket')
                if 'four-pocket field jacket' in ctx:
                    add(key, 'pockets', 'Quatre poches avant', d, a, b)
                if 'detachable jacket hood' in ctx:
                    add(key, 'hood', 'Capuche séparée', d, a, b)
                if 'november 1943' in ctx:
                    add(key, 'period', {'event': 'standardization', 'subject': 'ensemble', 'date': 'novembre 1943'}, d, a, n)
            if re.search(r'the m65 was made', t):
                key = model_id(1965)
                # End at the cuff continuation; never include subsequent cap/UCP paragraphs.
                end = next((k for k in range(n, min(n+20,max(lines))+1)
                            if k in lines and 'velcro' in norm(lines[k]['text'])), None)
                if end is None:
                    continue
                ctx = norm(window(d, n, end))
                if 'cotton and nylon' in ctx:
                    add(key, 'fabric', 'Coton et nylon', d, n, min(n+2,end))
                if 'olive green' in ctx:
                    add(key, 'color', 'Vert olive', d, n, min(n+2,end), scope='variant:olive')
                if 'zippered' in ctx and 'snap fasteners' in ctx:
                    add(key, 'closure', 'Glissière sous patte à pressions', d, n, end)
                if 'four front' in ctx and 'pockets' in ctx:
                    add(key, 'pockets', 'Quatre poches avant', d, n, end)
                if 'collar had a hood inside' in ctx:
                    add(key, 'hood', 'Logée dans le col', d, n, end)
                if 'velcro' in ctx:
                    add(key, 'construction', 'Poignets réglés par bande autoagrippante', d, n, end)
            if 'beginning in 1966' in t:
                ctx = norm(window(d, n, min(n+6,max(lines))))
                if 'm65' in ctx and 'inventory' in ctx:
                    add(model_id(1965), 'period', {'event':'inventory-entry', 'subject':'jacket', 'date':'1966'}, d, n, n+6)
            if 'm1951 field jacket being' in t:
                ctx = norm(window(d, n, min(n+2,max(lines))))
                date = re.search(r'\b(19\d\d)\b', ctx.replace('m1951',''))
                if date:
                    add(model_id(1951), 'period', {'event':'wear-observed', 'subject':'jacket', 'date':date[1]}, d, n, n+2)
            if 'cotton utility uniform was introduced' in t and '107' in t:
                add('og107-utility', 'fabric', 'Coton', d, n, scope='ensemble')
                date = re.search(r'introduced in (19\d\d)', t)
                if date:
                    add('og107-utility', 'period', {'event':'introduction', 'subject':'utility-ensemble', 'date':date[1]}, d, n, scope='ensemble')
            if 'cotton sateen shirt replaced the utility jacket' in t:
                add('og107-utility', 'fabric', 'Coton satiné', d, n, n+1, scope='variant:utility-shirt-1963')
                add('og107-utility', 'variants', 'Chemise remplaçant la veste utilitaire ; spécification du 12 avril 1963', d, n, n+1, scope='variant:utility-shirt-1963')
            if 'pockets now had pointed pocket flaps' in t and 'buttoned cuffs' in t:
                add('og107-utility', 'variants', 'Révision de novembre 1964 : rabats pointus et poignets boutonnés', d, max(min(lines),n-2), n, scope='variant:utility-shirt-1964')

    from documentary_ocr import extract as extract_ocr
    extra_claims, extra_issues = extract_ocr(docs, mentions, proof, digest, encode)
    claims.extend(extra_claims)
    issues.extend(extra_issues)
    # Identity is derived from actual mentions, not an authored title table.
    for key in sorted(ids):
        ms = [m for m in mentions if m['id'] == key]
        for m in ms:
            d = next(d for d in docs if d['id'] == m['source'])
            add(key, 'family', m.get('family', 'utilityTop' if key.startswith('og') else 'fieldJacket'), d, m['line'], min(m['line']+2,max(d['lines'])), scope='classification', rule='garment-noun-v1')
        if key == 'og107-utility':
            issues.append({'model':key,'kind':'identity-ambiguity',
                           'detail':'OG-107 is a shade shared by utility coats, utility shirts and tropical clothing. A single garment identity is not established.'})
    # Same passage exposed by overlapping captures is one proof, not two sources.
    merged = {}
    for c in claims:
        key = (c['model'], c['field'], c['scope'], json.dumps(c['value'], sort_keys=True))
        if key not in merged:
            merged[key] = c
        else:
            target = merged[key]
            for ev in c['evidence']:
                signature = (ev['independence'], ev['lineStart'], ev['lineEnd'], ev['excerpt'])
                if not any(signature == (e['independence'], e['lineStart'], e['lineEnd'], e['excerpt']) for e in target['evidence']):
                    target['evidence'].append(ev)
    claims = list(merged.values())
    for c in claims:
        c['id'] = digest(encode({k:v for k,v in c.items() if k!='id'}))[:20]
    issues=list({json.dumps(item,sort_keys=True):item for item in issues}.values())
    return claims, issues


def conflicts(claims):
    # Exact scope AND event/subject are required. No manufactured date conflicts.
    groups = defaultdict(list)
    for c in claims:
        if c['field'] not in {'closure','pockets','hood','period'}:
            continue
        v = c['value']
        subkey = (v['event'], v['subject']) if isinstance(v,dict) else ()
        groups[(c['model'],c['field'],c['scope'],subkey)].append(c)
    result=[]
    for key, cs in groups.items():
        values = {json.dumps(c['value'],sort_keys=True) for c in cs}
        if len(values)>1:
            result.append({'model':key[0],'field':key[1],'scope':key[2],
                           'claims':[c['id'] for c in cs], 'kind':'unresolved-contradiction'})
    return result


def validate_claim(c, docs):
    by_id = {d['id']: d for d in docs}
    if not c.get('evidence'):
        raise ValueError('Unsourced claim')
    for ev in c['evidence']:
        d = by_id[ev['source']]
        if ev['captureSHA256'] != d['captureSHA256'] or ev['url'] != d['url']:
            raise ValueError('Source mismatch')
        if ev['excerpt'] != window(d, ev['lineStart'], ev['lineEnd']):
            raise ValueError('Evidence mismatch')


def status(key, cs, mentions, problems):
    families={m['independence'] for m in mentions if m['id']==key}
    reasons=[]
    if not cs or any(p['kind'] in {'out-of-scope', 'rights-prohibited', 'forged-reference'} for p in problems):
        return 'rejected', ['out of scope, prohibited provenance, invalid reference or no extractable evidence']
    if any(p['kind']=='unresolved-contradiction' for p in problems):
        return 'conflictual', ['same-field same-scope contradiction']
    if any(p['kind']=='identity-ambiguity' for p in problems):
        return 'conflictual', ['identity must be split into garment/variant before publication']
    if len(families)<2:
        reasons.append('fewer than two independent documentary works')
    if not any(c['field']=='officialDesignation' and c['scope']=='model' for c in cs):
        reasons.append('official designation not extracted from a primary heading')
    structural={c['field'] for c in cs if c['scope']=='model' and c['field'] in {'closure','pockets','hood','construction'}}
    if len(structural)<2:
        reasons.append('fewer than two explicit model-level structural fields')
    if not any(c['field']=='period' for c in cs):
        reasons.append('no typed date event extracted')
    return ('provisional',reasons) if reasons else ('publishable', ['all mandatory gates satisfied'])


def media(docs):
    result=[]
    for d in docs:
        if d['kind']!='media_manifest':
            continue
        raw='\n'.join(v['raw'] for v in d['lines'].values())
        # The web renderer inserts a literal newline inside one JSON string.
        # This is a rendered capture, not the byte-identical HTTP response.
        manifest=json.loads(raw, strict=False)
        for seq in manifest.get('sequences',[]):
            for canvas in seq.get('canvases',[]):
                meta={x['label']:x['value'] for x in canvas.get('metadata',[])}
                license_url=meta.get('Copyright')
                credit=meta.get('Droits','')
                if license_url!='https://creativecommons.org/publicdomain/zero/1.0/' or not credit.startswith('CC0 '):
                    continue
                for im in canvas.get('images',[]):
                    url=im['resource']['@id']
                    if url.startswith('https://apicollections.parismusees.paris.fr/'):
                        result.append({'id':canvas['@id'],'title':manifest['label'],
                                       'url':url,'recordURL':d['url'],'credit':credit,
                                       'license':'CC0','licenseURL':license_url,
                                       'rightsSourceSHA256':d['captureSHA256'],
                                       'downloaded':False,'scope':'museum-object:2002.1743'})
    return result


def render_notice(key, cs, ms, docs, state, reasons, images, quality=False):
    year=ms[0]['year']
    title=f'M-{year} Field Jacket' if year else 'OG-107 Utility — identité à préciser'
    if ms[0].get('designation'):
        title=ms[0]['designation']
    name=title
    facts=[]
    for c in cs:
        if c['field']=='family':
            continue
        value=c['value']
        if isinstance(value,dict):
            value=f"{value['subject']} · {value['event']} · {value['date']}"
        facts.append({'label':f"{LABELS.get(c['field'], c['field'])} · {c['id'][:6]}",
                      'value':f"{value} (portée : {c['scope']})",'sources':sorted({ev['source'] for ev in c['evidence']})})
    used={ev['source'] for c in cs for ev in c['evidence']}
    refs=[]
    for d in docs:
        if d['id'] not in used:
            continue
        evs=[e for c in cs for e in c['evidence'] if e['source']==d['id']]
        page_label=' · repère OCR P (pagination non vérifiée) ' if quality and d.get('adapter')=='military-ocr-v1' else ' · PDF P'
        locators=sorted({f"L{e['lineStart']}–L{e['lineEnd']}"+(page_label+','.join(e['pages']) if e['pages'] else '') for e in evs})
        summary='Capture documentaire partielle du 11 novembre 2026. Preuves détaillées dans le catalogue documentaire.'
        if d.get('adapter')=='military-ocr-v1':
            summary=f"OCR collecté automatiquement le {d['retrievedAt'][:10]}. Date de publication : {d['publicationYear']}. La datation de publication ne vaut pas période de service."
        refs.append({'id':d['id'],'institution':d['institution'],'title':d['title'],
                     'locator':'; '.join(locators),'summary':summary, 'url':d['url']})
    criteria=[]
    mapping={'pockets':('Quatre poches avant',['Quatre poches avant'],[]),
             'hood':None,'closure':None}
    for field in mapping:
        eligible=[c for c in cs if c['field']==field and c['scope']=='model']
        if not eligible:
            continue
        values={c['value'] for c in eligible}
        if len(values)!=1:
            continue
        value=next(iter(values))
        concordant={'Quatre poches avant':['Quatre poches avant'],
                    'Capuche séparée':['Extérieure uniquement'],
                    'Logée dans le col':['Logée dans le col'],
                    'Glissière sous patte à pressions':['Glissière sous patte à pressions']}.get(value)
        if concordant:
            criteria.append({'field':field,'expected':value,'sources':sorted({e['source'] for c in eligible for e in c['evidence']}),
                             'concordant':concordant,'contradictory':[]})
    # No negative inference: absence of matching data never becomes a contradiction.
    illustration={'title':'Aucune photographie redistribuée dans ce prototype',
                  'credit':'Sans image', 'license':'Sans objet', 'url':refs[0]['url']}
    if key.startswith('m1943') and images:
        im=images[0]
        illustration={k:im[k] for k in ['title','credit','license','licenseURL']}
        illustration['url']=im['recordURL']
    periods=[f"{c['value']['subject']} · {c['value']['event']} : {c['value']['date']}" for c in cs if c['field']=='period']
    return {'id':key,'name':name,'shortName':ms[0].get('designation',f'M-{year}' if year else 'OG-107 Utility'),
            'family':ms[0].get('family','utilityTop' if year is None else 'fieldJacket'),
            'indexPeriod':periods[0] if periods else 'Période non établie',
            'period':' ; '.join(periods) if periods else 'Période non établie',
            'intro':f'Notice extraite automatiquement. Statut : {state}. Les observations limitées à un exemplaire sont signalées dans chaque fait.',
            'sections':[{'title':'Informations documentées','facts':facts}],
            'illustration':illustration,'sources':refs,'criteria':criteria}


def run(corpus, out, quality=False, support_corpora=()):
    started=time.perf_counter()
    previous_manifest = json.loads((out/'distribution/manifest.json').read_bytes()) if (out/'distribution/manifest.json').exists() else None
    docs=load_corpus(corpus)
    for folder in support_corpora:
        for doc in load_corpus(folder):
            if any(d['id']==doc['id'] for d in docs):
                raise ValueError('Duplicate source ID across corpora')
            docs.append(doc)
    mentions, excluded=discover(docs)
    claims, issues=extract(docs, mentions)
    if quality:
        from documentary_quality import improve
        mentions, claims, issues=improve(docs,mentions,claims,issues)
    from federal_pdf import identity_claims
    claims.extend(identity_claims(docs, mentions, proof, digest, encode))
    for c in claims:
        validate_claim(c,docs)
        if quality:
            from documentary_quality import view, validate_warrant
            for ev in c['evidence']:
                if 'parsingExcerpt' in ev:
                    d=next(d for d in docs if d['id']==ev['source'])
                    if ev['parsingExcerpt']!=view(d,ev['lineStart'],ev['lineEnd'])[0]:
                        raise ValueError('Parsing view differs from original evidence')
            if c.get('warrant'):
                validate_warrant(c)
    issues+=conflicts(claims)
    images=media(docs)
    groups=defaultdict(list)
    for m in mentions:
        groups[m['id']].append(m)
    notices=[]
    drafts=[]
    decisions=[]
    for key, ms in sorted(groups.items()):
        cs=[c for c in claims if c['model']==key]
        probs=[p for p in issues if p['model']==key]
        st,reasons=status(key,cs,mentions,probs)
        decision={'id':key,'status':st,'reasons':reasons,
                  'independentWorks':sorted({m['independence'] for m in ms}),
                  'missingFields':[f for f in FIELDS if not any(c['field']==f for c in cs)]}
        decisions.append(decision)
        notice=render_notice(key,cs,ms,docs,st,reasons,images,quality)
        drafts.append(notice)
        if st=='publishable':
            notices.append(notice)
    source_meta=[{k:v for k,v in d.items() if k not in {'toolResponse','lines'}} for d in docs]
    ruleset=RULESET+'+phase51-evidence-1' if quality else RULESET
    evidence={'schemaVersion':1,'ruleset':ruleset,'sources':source_meta,'mentions':mentions,
              'excludedMentions':excluded,'claims':claims,'issues':issues,'decisions':decisions,'media':images}
    save(out/'documentary-catalogue.json',evidence)
    save(out/'explorer-drafts.json',{'version':1,'models':drafts})
    save(out/'explorer-publishable.json',{'version':1,'models':notices})
    duplicate_groups=[{'id':key,'mentions':len(ms),'aliases':sorted({m['alias'] for m in ms}),
                       'mergedMentions':max(0,len(ms)-1)} for key,ms in sorted(groups.items()) if len(ms)>1]
    save(out/'duplicates.json',duplicate_groups)
    files=[]
    # Distribution directory contains ONLY publishable notices and their references.
    public=out/'distribution'
    public.mkdir(parents=True,exist_ok=True)
    # Rebuild cleanly within the prototype output, with no stale withdrawn notice.
    for old in (public/'notices').glob('*.json') if (public/'notices').exists() else []:
        old.unlink()
    for notice in notices:
        path=public/'notices'/(notice['id']+'.json')
        save(path,notice)
        files.append({'path':path.relative_to(public).as_posix(),'sha256':digest(path.read_bytes()),'bytes':path.stat().st_size})
    refs=[s for s in source_meta if any(s['id'] in {r['id'] for n in notices for r in n['sources']} for _ in [0])]
    save(public/'references.json',refs)
    p=public/'references.json'
    files.append({'path':'references.json','sha256':digest(p.read_bytes()),'bytes':p.stat().st_size})
    version=digest(encode(files))[:20]
    manifest={'schemaVersion':1,'minimumReaderVersion':1,'catalogueVersion':version,
              'ruleset':ruleset,'files':files,'modelIDs':[n['id'] for n in notices],
              'trust':'UNSIGNED-LOCAL-PROTOTYPE-ONLY','generatedFrom':'2026-11-11 snapshots'}
    save(public/'manifest.json',manifest)
    history_path=out/'history.json'
    history=json.loads(history_path.read_bytes()) if history_path.exists() else []
    if previous_manifest is None or previous_manifest['catalogueVersion']!=version:
        old={f['path']:f['sha256'] for f in previous_manifest['files']} if previous_manifest else {}
        new={f['path']:f['sha256'] for f in files}
        history.append({'from':previous_manifest['catalogueVersion'] if previous_manifest else None,
                        'to':version,'ruleset':ruleset,
                        'added':sorted(set(new)-set(old)), 'removed':sorted(set(old)-set(new)),
                        'changed':sorted(k for k in set(new)&set(old) if new[k]!=old[k])})
        save(history_path,history)
    counts=Counter(d['status'] for d in decisions)
    metrics={'modelsDiscovered':len(groups),'noticesGenerated':len(drafts),
             'publishable':counts['publishable'],'provisional':counts['provisional'],
             'conflictual':counts['conflictual'],'rejected':counts['rejected'],
             'uncertain':counts['provisional']+counts['conflictual'],
             'modelMentions':len(mentions),'duplicateGroups':len(duplicate_groups),
             'mergedMentions':sum(g['mergedMentions'] for g in duplicate_groups),
             'confirmedDocumentaryContradictions':sum(p['kind']=='unresolved-contradiction' for p in issues),
             'identityAmbiguities':sum(p['kind']=='identity-ambiguity' for p in issues),
             'claims':len(claims),'licensedPhotoReferences':len(images),'downloadedPhotos':0,
             'sourceCaptures':len(docs),'independentWorks':len({d['independence'] for d in docs if d['kind']!='media_manifest'}),
             'excludedCaptures':sum(json.loads(p.read_text()).get('ingestAllowed') is False for folder in [corpus,*support_corpora] for p in folder.glob('*.json')),
             'corpusBytes':sum(p.stat().st_size for folder in [corpus,*support_corpora] for p in folder.glob('*.json')),
             'elapsedSeconds':round(time.perf_counter()-started,6),
             'processMaxRSSRaw':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
             'processMaxRSSNote':'bytes on macOS, KiB on Linux',
             'additionalPaidServiceCostEUR':0,'livePythonNetworkCollectionDemonstrated':False,
             'catalogueVersion':version}
    save(out/'metrics.json',metrics)
    print(json.dumps(metrics,ensure_ascii=False,indent=2))
    return evidence,manifest,metrics


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--corpus',type=Path,default=ROOT/'corpus')
    ap.add_argument('--output',type=Path,default=ROOT/'output')
    ap.add_argument('--quality',action='store_true',help='Phase 51 bounded extraction; mandatory admission gates unchanged')
    ap.add_argument('--support-corpus',type=Path,action='append',default=[])
    args=ap.parse_args()
    run(args.corpus,args.output,args.quality,args.support_corpus)
