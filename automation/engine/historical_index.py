"""Bounded, evidence-preserving historical nomenclature intake. Never creates Identifier criteria."""
import copy, hashlib, json, re, unicodedata
from pathlib import Path

NOUNS = r'APRON|BALACLAVA|BELT|BLOUSE|BRASSIERE|BREECHES|CAP|CAPE|COAT|COVERALLS?|DRAWERS|DRESS|FROCK|GIRDLE|GLOVES?|HAT|HOOD|JACKET|JUMPER|LINER|MITTENS?|MUFFLER|NECKTIE|OVERALLS?|OVERCOAT|PAJAMAS|PARKA|PONCHO|PULLOVER|RAINCOAT|ROBE|SCARF|SHIRTS?|SHORTS|SKIRT|SLACKS|SMOCK|SOCKS|STOCKINGS|SUIT|SUSPENDERS|SWEATER|SWEATSHIRT|TROUSERS|TRUNKS|UNDERSHIRT|UNDERWEAR|UNIFORM|VEST|WAIST'
GARMENT = re.compile(r'^('+NOUNS+r')\b',re.I)
DENIED = re.compile(r'\b(?:SHOES?|BOOTS?|SHOEPACS?|OVERSHOES|GAITERS|LEGGINGS|HELMET|SLEEPING|TENT|BAG|CARTRIDGE|MAGAZINE|PISTOL|RIFLE|SLING|CANTEEN|TOOL|PACK|PACKBOARD|MUZZLE|BRITISH|CANADIAN|GERMAN|SOVIET|RUSSIAN|NORWEGIAN|FRENCH|JAPANESE|ENEMY|CIVILIAN|REPLICA|REPRODUCTION)\b',re.I)
_WORDS = None

def readable_ocr(name):
    global _WORDS
    if _WORDS is None:
        _WORDS={w.casefold() for w in Path('/usr/share/dict/words').read_text().splitlines()}
        _WORDS.update({'women','laundress','elastique','wac','usn','usmc','usaf','od','oz','em','wo'})
    def known(token):
        if token in _WORDS or len(token)==1:return True
        forms={token.rstrip('s'),token[:-2] if token.endswith('es') else token,token[:-2] if token.endswith('ed') else token,
               token[:-1] if token.endswith('d') else token}
        if forms&_WORDS:return True
        return any(token[:i] in _WORDS and token[i:] in _WORDS for i in range(3,len(token)-2))
    return all(known(t.casefold()) for t in re.findall('[A-Za-z]+',name))

def normalized(value):
    value=unicodedata.normalize('NFKD',value).replace('’',"'").replace('‐','-').replace('–','-')
    return ' '.join(value.replace('*','').split()).strip(' ,.;:')

def canonical(value):
    value=normalized(value).casefold()
    value=re.sub(r"\b(?:man's|men's)\b",'man',value)
    value=re.sub(r"\b(?:woman's|women's|women)\b",'woman',value)
    value=re.sub(r'\bm[ -](\d{4})\b',r'm\1',value)
    value=re.sub(r"\b(?:o's|o’s)\b",'officer',value)
    # Possessive grammar does not establish a distinct historical article.
    value=re.sub(r"\b([a-z]+)'s(?=\W|$)",r'\1',value)
    value=re.sub(r"\b([a-z]+s)'(?=\W|$)",r'\1',value)
    words=re.findall(r'[a-z0-9]+',value)
    singular={'gloves':'glove','mittens':'mitten','shirts':'shirt','coveralls':'coverall','overalls':'overall'}
    words=[singular.get(w,w) for w in words]
    return words[0]+' '+' '.join(sorted(words[1:])) if words else ''

def family(name):
    if re.search(r'\bUniform\b',name,re.I):return 'coveralls'
    match=GARMENT.match(name) or re.search(r'\b('+NOUNS+r')\b',name,re.I)
    if not match:raise ValueError('Garment designation absent')
    noun=match.group(1).upper()
    if noun=='SHIRTS':noun='SHIRT'
    if noun in {'PARKA','OVERCOAT','RAINCOAT','CAPE','PONCHO'}:return 'outerwear'
    if noun in {'TROUSERS','SLACKS','BREECHES','SHORTS'}:return 'trousers'
    if noun in {'COVERALL','COVERALLS','OVERALL','OVERALLS','SUIT'}:return 'coveralls'
    if noun in {'CAP','HAT','HOOD','BALACLAVA'}:return 'headwear'
    if noun in {'GLOVE','GLOVES','MITTEN','MITTENS'}:return 'handwear'
    if noun in {'DRAWERS','UNDERSHIRT','UNDERWEAR','BRASSIERE','GIRDLE','PAJAMAS','SOCKS','STOCKINGS','TRUNKS'}:return 'underwear'
    if noun=='LINER':return 'liners'
    if noun in {'MUFFLER','NECKTIE','SCARF','BELT','SUSPENDERS'}:return 'accessories'
    if noun in {'DRESS','SKIRT','ROBE','WAIST'}:return 'servicewear'
    if noun in {'SHIRT','JUMPER','SWEATER','PULLOVER','SWEATSHIRT'}:return 'utilityTop'
    if noun in {'APRON','FROCK','SMOCK'}:return 'workwear'
    if noun in {'JACKET','COAT','VEST','BLOUSE'}:
        if re.search(r'\b(?:WAC|NURSE|OFFICER|SERVICE|DRESS)\b',name,re.I):return 'servicewear'
        return 'fieldJacket' if re.search(r'FIELD|COMBAT|HERRINGBONE|MOUNTAIN|PARACHUTE|JUNGLE',name,re.I) else 'outerwear'
    raise ValueError('Unsupported garment noun')

def acceptable(name,descriptive=False):
    if not (GARMENT.match(name) or descriptive and re.search(r'\b(?:'+NOUNS+r')\b',name,re.I)) or DENIED.search(name) or len(name)>180:return False
    if name.startswith('SUSPENDERS') and 'TROUSERS' not in name:return False
    if re.search(r'(?:, O|, AVIATION|HERRINGBONE)$',name):return False
    if re.search(r'\b(?:SIZE|SIZES|SMALL|MEDIUM|LARGE|REPAIR|REPAIRS|INSPECTION|INSPECT|STITCHING|MEASUREMENTS|CONTINUED|CLASSIFICATION)\b',name,re.I):return False
    # Generic headings and incomplete wrap fragments are not garment references.
    if len(re.findall(r'[A-Za-z0-9]+',name))<2:return False
    if name.rstrip().endswith((',', ';', '-', ' AND', ' OR')):return False
    return True

def uppercase_heading(value):
    letters=re.findall('[A-Za-z]',value)
    return bool(letters) and sum(c.isupper() for c in letters)/len(letters)>=0.78

def evidence(profile,page,name,quote,kind,**extra):
    return {'designation':name,'sourceID':profile['id'],'sourceURL':profile['url'],'sourceTitle':profile['title'],
            'institution':profile['institution'],'documentYear':profile['year'],'workID':profile['workID'],
            'documentSHA256':profile['sha256'],'page':page,'kind':kind,'quote':quote,
            'quoteSHA256':hashlib.sha256(quote.encode()).hexdigest(),**({'bibliographicProof':profile['bibliographicProof']} if profile.get('bibliographicProof') else {}),**extra}

def extract_pages(profile,pages):
    if profile.get('synthetic') or profile.get('testOnly') or any(p.get('synthetic') for p in pages):raise ValueError('Synthetic source cannot enter the historical catalogue')
    rows=[];rejections=[]
    for page in pages:
        if not profile.get('firstPage',1)<=page['page']<=profile.get('lastPage',800):continue
        if profile['structure']=='inspection-headings':
            lines=page['text'].splitlines()
            for i,line in enumerate(lines):
                if not re.fullmatch(r'LIN [A-Z]\d{5}|No Assigned LIN',line.strip()):continue
                if i==0:continue
                heading=re.sub(r'^\d+[.)]\s*','',lines[i-1]).strip()
                name=normalized(heading.split('; Chapter ')[0])
                if not acceptable(name):continue
                nearby='\n'.join(lines[i-1:i+1]);lin=re.search(r'\bLIN\s+([A-Z]\d{5})\b',nearby)
                if not lin and 'No Assigned LIN' not in nearby:continue
                rows.append(evidence(profile,page['page'],name,nearby,'inspection-heading',lin=lin[1] if lin else None))
        elif profile['structure']=='issue-list':
            for line in page['text'].splitlines():
                name=normalized(re.sub(r'\s*\(\d+ pr\)\s*$', '',line))
                if not re.match(r'^(?:'+NOUNS+r'),\s*',name,re.I) or not acceptable(name):continue
                rows.append(evidence(profile,page['page'],name,line,'named-issue-list'))
        elif profile['structure']=='uniform-table':
            if 'COMPOSITION OF UNI' not in page['text'] or 'APPENDIX B' not in page['text']:continue
            regions=page.get('regions',[])
            for i,box in enumerate(regions):
                initial=box['text'].strip()
                if not re.match(r'^(?:'+NOUNS+r'),\s*',initial,re.I):continue
                used=[box];bottom=box['y'];name=initial
                for other in regions[i+1:i+16]:
                    gap=bottom-other['y'];value=other['text'].strip()
                    if not 0.003<gap<0.026 or abs(other['x']-box['x'])>0.018:continue
                    if GARMENT.match(value) or DENIED.search(value) or re.fullmatch(r'[\d•*]+',value):break
                    if not re.search('[A-Za-z]',value):continue
                    name=(name[:-1]+value) if name.endswith('-') and value[:1].islower() else name+' '+value
                    used.append(other);bottom=other['y']
                name=normalized(name)
                if not acceptable(name) or not readable_ocr(name) or re.search(r'\b\d+[A-Za-z]+\d*\b',name):
                    rejections.append({'page':page['page'],'name':name,'reason':'table-name-incomplete-or-OCR'});continue
                rows.append(evidence(profile,page['page'],name,'\n'.join(b['text'] for b in used),'uniform-table-heading'))
        elif profile['structure']=='stock-headings':
            regions=page.get('regions',[])
            for i,box in enumerate(regions):
                initial=box['text'].strip()
                if not re.match(r'^(?:'+NOUNS+r'),\s*',initial) or not uppercase_heading(initial):continue
                # Keep wrapped headings in their own visual column. Do not concatenate adjacent garments.
                name=initial;used=[box];bottom=box['y']
                for next_box in regions[i+1:i+12]:
                    value=next_box['text'].strip();gap=bottom-next_box['y']
                    center=box['x']+box['w']/2
                    if not 0.002<gap<0.026 or not next_box['x']-0.025<=center<=next_box['x']+next_box['w']+0.025:continue
                    if re.match(r'^Stock\b',value,re.I) or GARMENT.match(value) or not uppercase_heading(value):break
                    if re.search(r'\b(?:STANDARD|SPEC|WT|FIG|STOCK)\b',value):break
                    name+=' '+value;used.append(next_box);bottom=next_box['y']
                # Only a nearby stock-number caption establishes a catalogue item.
                stocks=[b for b in regions if re.match(r'^Stock No[.,:]?',b['text'],re.I)
                        and 0<bottom-b['y']<0.055 and abs((b['x']+b['w']/2)-(box['x']+box['w']/2))<0.13]
                if not stocks:continue
                name=normalized(name)
                if not acceptable(name):rejections.append({'page':page['page'],'name':name,'reason':'incomplete-or-excluded'});continue
                confidence=min(b['confidence'] for b in used)
                if confidence<0.98:rejections.append({'page':page['page'],'name':name,'reason':'OCR-confidence'});continue
                if not readable_ocr(name):rejections.append({'page':page['page'],'name':name,'reason':'unrecognized-OCR-word'});continue
                quote='\n'.join([b['text'] for b in used]+[min(stocks,key=lambda b:abs(bottom-b['y']))['text']])
                rows.append(evidence(profile,page['page'],name,quote,'stock-heading',ocrConfidence=confidence,
                                     boxes=[{k:b[k] for k in ['x','y','w','h']} for b in used]))
        else:raise ValueError('Unknown structural parser')
    unique={}
    for row in rows:
        key=(canonical(row['designation']),row['sourceID'],row['page'])
        unique.setdefault(key,row)
    return list(unique.values()),rejections

def reference_model(rows):
    name=rows[0]['designation'];key=variant_group(name)
    mid='ref-'+re.sub('[^a-z0-9]+','-',key).strip('-')[:75]+'-'+hashlib.sha256(key.encode()).hexdigest()[:10]
    years=sorted({r.get('historicalYear',r['documentYear']) for r in rows});by_source={}
    for row in rows:by_source.setdefault(row['sourceID'],[]).append(row)
    sources=[{'id':sid,'institution':rs[0]['institution'],'title':rs[0]['sourceTitle'],'url':rs[0]['sourceURL'],
              'locator':'; '.join(sorted({r.get('locator','PDF p. '+str(r['page'])) for r in rs})),
              'summary':'Désignation attestée dans le document de '+str(rs[0]['documentYear'])+'. La date du document ne constitue pas une période de service. Les limites de la notice restent indiquées ci-dessous.'} for sid,rs in sorted(by_source.items())]
    model={'id':mid,'name':name,'shortName':name,'family':family(name),'period':'Attesté en '+', '.join(map(str,years)),
            'indexPeriod':' · '.join(map(str,years)),'intro':'Référence historique attestée dans les sources citées. Les caractéristiques et variantes restent à documenter.',
            'sections':[{'title':'Désignations attestées','facts':[{'label':'Désignation '+str(i+1),'value':alias,'sources':sorted({r['sourceID'] for r in rows if r['designation']==alias})}
                         for i,alias in enumerate(sorted({r['designation'] for r in rows}))]}],
            'illustration':{'title':'Aucune photographie vérifiée disponible','credit':'Sans image','license':'Sans objet','url':sources[0]['url']},
            'sources':sources,'criteria':[], 'publication':{'documentationLevel':'reference','identifierEligible':False,'identitySources':sorted(by_source),'summarySources':sorted(by_source),
            'unknowns':['Période de service et caractéristiques discriminantes non établies.','Cette référence n’est pas utilisée par Identifier.']},
            'indexMetadata':{'years':years,'aliases':sorted({r['designation'] for r in rows}), 'attestations':rows,'branch':'US Army','variantGroup':variant_group(name)}}
    logistics=sorted({(r['lin'],r['sourceID']) for r in rows if r.get('lin')})
    if logistics:
        model['sections'].append({'title':'Informations documentées','facts':[{'label':'Identifiant logistique '+str(i+1),'value':'LIN '+lin,'sources':[sid]} for i,(lin,sid) in enumerate(logistics)]})
        model['publication']['documentationLevel']='documented'
        model['intro']='Référence historique attestée. Les identifiants logistiques reproduits proviennent des documents cités ; ils ne constituent pas un critère Identifier.'
    dated=[r for r in rows if r.get('historicalYear')]
    if dated:
        model['sections'].append({'title':'Jalons historiques documentés','facts':[{'label':'Introduction documentée '+str(r['historicalYear']),'value':r['quote'],'sources':[r['sourceID']]} for r in dated]})
        model['publication']['documentationLevel']='documented'
        model['period']='Introduction documentée en '+', '.join(map(str,years))
        model['intro']='Une introduction historique est explicitement datée dans la source citée. Les caractéristiques discriminantes restent à documenter.'
    if any(r['kind']=='primary-test-report-heading' for r in rows):
        model['publication']['unknowns'].append('Attestation dans un rapport d’essai : adoption et diffusion non établies.')
    return model

def variant_group(name):
    # Group navigation only: an uncertain variant never silently acquires another model's identity.
    value=re.sub(r'\bod\s*(?:no\.?\s*)?\d*\b',' ',name,flags=re.I)
    value=re.sub(r'\b(?:khaki|olive|drab|white|blue|black|green|tan|beige|light[- ]shade|dark[- ]shade|no\.?\s*\d+)\b',' ',value,flags=re.I)
    return canonical(value)

def group_variants(attestations):
    grouped={}
    for row in attestations:
        key=variant_group(row['designation'])
        grouped.setdefault(key,{})[(row['sourceID'],row['page'],row['quoteSHA256'],row['designation'])]=row
    return [reference_model(list(grouped[k].values())) for k in sorted(grouped)]

def extract_network_capture(doc):
    """Only numbered primary nomenclature headings; never generic prose mentions."""
    if doc.get('synthetic') or doc.get('testOnly'):raise ValueError('Synthetic documentary capture is forbidden')
    if doc.get('adapter')=='federal-pdf-v1':return extract_dated_introductions(doc)
    if doc.get('adapter')!='military-ocr-v1' or not 1940<=doc.get('publicationYear',0)<=1999:return []
    if re.search(r'\bhistor(?:y|ical)|\bsurvey|\bretrospective|\bcostume',doc['title'],re.I):return []
    text=doc['toolResponse']['content'][0]['text']
    profile={'id':doc['id']+'-'+doc['rawHTTPBodySHA256'][:10],'title':doc['title'],'institution':doc['institution'],'url':doc['url'],
             'year':doc['publicationYear'],'workID':doc['independence'],'sha256':doc['rawHTTPBodySHA256']}
    rows=[]
    lines=text.splitlines()
    for i,line in enumerate(lines):
        match=re.match(r'L(\d+)@P(\d+):\s*(\d+)\.\s+(.+)',line)
        if not match:continue
        title=match[4];quotation=title
        for following in lines[i+1:i+3]:
            value=re.sub(r'^L\d+@P\d+:\s*','',following).strip()
            if not value:continue
            qualifier=re.match(r'\(Type [A-Z]-\d{1,3}[a-z]?\)',value)
            if qualifier and '(Type ' not in title:title+=' '+qualifier[0];quotation+='\n'+value
            break
        # A heading naming two garments supplies two attestations, never one invented composite item.
        for part in re.split(r',\s+AND\s+(?=(?:'+NOUNS+r'),)',title):
            name=normalized(part)
            if not acceptable(name) or not uppercase_heading(name) or not readable_ocr(name):continue
            kind='primary-test-report-heading' if re.search(r'\btests?\b|\btrials?\b|experimental',doc['title'],re.I) else 'numbered-primary-heading'
            rows.append(evidence(profile,int(match[2]),name,quotation,kind,locator='OCR L'+match[1]+' (pagination non vérifiée)'))
    return rows

def extract_dated_introductions(doc):
    """Explicit Army introduction + named item + in-scope year, including later institutional histories."""
    if not doc.get('rightsEvidence') or doc.get('rights')!='US-federal-text-only':return []
    profile={'id':doc['id']+'-'+doc['rawHTTPBodySHA256'][:10],'title':doc['title'],'institution':doc['institution'],'url':doc['url'],
             'year':doc['publicationYear'],'workID':doc['independence'],'sha256':doc['rawHTTPBodySHA256']}
    rows=[]
    for line in doc['toolResponse']['content'][0]['text'].splitlines():
        match=re.match(r'L\d+@P(\d+):\s*(On [A-Z][a-z]+ \d{1,2}, (19\d{2}) the Army introduced the ([A-Z][a-z]+(?: [A-Z][a-z]+){1,5}) (?:or|in|on) [^.]+\.)',line)
        if not match or not 1940<=int(match[3])<=1999 or not acceptable(match[4],descriptive=True):continue
        rows.append(evidence(profile,int(match[1]),match[4],match[2],'explicit-dated-introduction',historicalYear=int(match[3])))
    return rows

def merge_index(baseline,attestations):
    """Stable IDs and last-good content; enrichments never grant Identifier admission."""
    for row in attestations:
        target_year=row.get('historicalYear',row['documentYear'])
        if row.get('synthetic') or not 1940<=target_year<=1999 or not acceptable(row['designation'],descriptive=row['kind']=='explicit-dated-introduction'):raise ValueError('Invalid historical evidence')
        if row.get('historicalYear') and row['historicalYear']>row['documentYear']:raise ValueError('Historical introduction postdates the cited document')
        if row.get('historicalYear') and str(row['historicalYear']) not in row['quote']:raise ValueError('Historical event date absent from evidence')
        if not re.fullmatch('[a-f0-9]{64}',row['documentSHA256']) or not row['sourceURL'].startswith('https://'):raise ValueError('Invalid historical source')
        if not 0<len(row['quote'].encode())<=2048 or hashlib.sha256(row['quote'].encode()).hexdigest()!=row['quoteSHA256']:raise ValueError('Historical quote integrity failure')
        folded_quote=normalized(re.sub(r'(?<=[A-Za-z])-\s*\n\s*(?=[a-z])','',row['quote'])).casefold()
        if normalized(row['designation']).casefold() not in folded_quote:raise ValueError('Designation absent from original quotation')
    result=copy.deepcopy(baseline);targets={};decisions=[]
    for model in result:
        model['publication']['documentationLevel']=model['publication'].get('documentationLevel') or ('identifiable' if model['publication']['identifierEligible'] else 'documented')
        old_index=model.get('indexMetadata') or {}
        if old_index.get('variantGroup'):targets.setdefault(old_index['variantGroup'],model)
        names=[model['name'],model['shortName'],*old_index.get('aliases',[])]
        for section in model['sections']:
            for fact in section['facts']:
                if fact['label'].startswith('Désignation documentaire'):
                    names.append(fact['value'].split(' (portée :')[0])
        for name in names:
            if GARMENT.match(name):targets.setdefault(variant_group(name),model)
    for incoming in group_variants(attestations):
        key=incoming['indexMetadata']['variantGroup'];existing=targets.get(key)
        if existing is None:
            result.append(incoming);targets[key]=incoming;decisions.append({'id':incoming['id'],'decision':'created','level':incoming['publication']['documentationLevel']});continue
        before=json.dumps(existing,sort_keys=True,ensure_ascii=False)
        old_index=existing.get('indexMetadata')
        rows=(old_index or {}).get('attestations',[])+incoming['indexMetadata']['attestations']
        by_evidence={}
        for row in rows:by_evidence.setdefault((row['sourceID'],row['page'],row['quoteSHA256'],row['designation']),row)
        replacement=reference_model(list(by_evidence.values()))
        existing['indexMetadata']=replacement['indexMetadata']
        refs={s['id'] for s in existing['sources']}
        for source in replacement['sources']:
            if source['id'] not in refs:existing['sources'].append(source);refs.add(source['id'])
            else:
                prior=next(s for s in existing['sources'] if s['id']==source['id'])
                if prior['url']!=source['url']:raise ValueError('A historical source identity changed its URL')
                prior['locator']='; '.join(sorted(set(prior['locator'].split('; '))|set(source['locator'].split('; '))))
        if existing['id'].startswith('ref-'):
            # Regenerate only the automatically derived reference/documented content.
            for field in ['sections','period','indexPeriod','intro','publication']:existing[field]=replacement[field]
        else:
            # Historic editorial content and every original Identifier criterion remain untouched.
            section=next((s for s in existing['sections'] if s['title']=='Attestations historiques complémentaires'),None)
            if section is None:section={'title':'Attestations historiques complémentaires','facts':[]};existing['sections'].append(section)
            known={(f['value'],tuple(f['sources'])) for f in section['facts']}
            for row in replacement['indexMetadata']['attestations']:
                value=row['designation']+' · attesté en '+str(row['documentYear'])
                if (value,(row['sourceID'],)) not in known:
                    section['facts'].append({'label':'Attestation '+row['sourceID']+' '+hashlib.sha256(value.encode()).hexdigest()[:10], 'value':value,'sources':[row['sourceID']]});known.add((value,(row['sourceID'],)))
        decisions.append({'id':existing['id'],'decision':'unchanged' if before==json.dumps(existing,sort_keys=True,ensure_ascii=False) else 'enriched'})
    return result,decisions

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--profile',type=Path,required=True);ap.add_argument('--pages',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();rows,rejected=extract_pages(json.loads(args.profile.read_text()),json.loads(args.pages.read_text()))
    args.output.write_text(json.dumps({'attestations':rows,'rejections':rejected},ensure_ascii=False,sort_keys=True)+'\n')

def build_index(attestations):
    return merge_index([],attestations)[0]
