"""Phase 50 extension of the Phase 49 pipeline: primary manual headings.

No model registry and no authored notices. Nomenclature is copied from the
document; field assertions require a matched passage in the same bounded section.
Unsupported garment families and unresolved OCR identities stay outside Explorer.
"""
import re
import unicodedata

VERSION='military-ocr-v1'


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC',value).split())


def headings(doc):
    lines=doc['lines']
    numbers=sorted(lines)
    boundaries=[]
    for n in numbers:
        t=normalized(lines[n]['text'])
        m=re.match(r'^(?:\d+(?:\.\d+)?|[IVXL]+)\.\s+(.+)$',t)
        if m:
            boundaries.append((n,m[1]))
    for index,(n,rest) in enumerate(boundaries):
        if not re.match(r'(?:JACKET|SHIRT|PARKA|OVERCOAT),\s',rest):
            continue
        title=re.split(r'[.*]',rest,maxsplit=1)[0].strip(' ,')
        letters=''.join(c for c in title if c.isalpha())
        if not letters.isupper() or len(title)>110 or len(title)<8:
            continue
        end=boundaries[index+1][0]-1 if index+1<len(boundaries) else min(n+200,max(numbers))
        yield {'start':n,'end':end,'designation':title,'rawHeading':rest}


def identity(title,doc,model_id):
    # Narrow OCR correction, accepted only if the literal corrected token also
    # occurs in the SAME document. The original heading remains in the proof.
    fixed=re.sub(r'\bM[Iil](\d{3})\b',r'M1\1',title)
    if fixed!=title:
        token=re.search(r'\bM(19\d\d)\b',fixed)
        if not token or not any(token[0] in x['text'].replace(' ','') for x in doc['lines'].values()):
            return None,'unresolved OCR designation'
    m=re.search(r'\bM[- ]?(19\d\d)\b',fixed)
    if m and fixed.startswith('JACKET, FIELD,'):
        year=int(m[1])
        if not 1940<=year<=1999:return None,'model year outside scope'
        return {'id':model_id(year),'year':year,'designation':fixed,'family':'fieldJacket'},None
    noun=fixed.split(',')[0]
    if noun not in {'JACKET','SHIRT'}:
        return None,'garment family requires a future native reader; no forced jacket mapping'
    key=re.sub('[^a-z0-9]+','-',fixed.lower()).strip('-')
    return {'id':key,'year':None,'designation':fixed,'family':'utilityTop' if noun=='SHIRT' else 'fieldJacket'},None


def discover(docs,model_id,window):
    mentions=[];excluded=[]
    for d in docs:
        if d.get('adapter')!=VERSION:continue
        for h in headings(d):
            item,reason=identity(h['designation'],d,model_id)
            if item is None:
                excluded.append({'source':d['id'],'line':h['start'],'token':h['designation'],'reason':reason})
                continue
            mentions.append({**item,'source':d['id'],'independence':d['independence'],
                             'alias':h['designation'],'line':h['start'],'sectionEnd':h['end'],
                             'context':window(d,h['start'],min(h['start']+3,h['end'])),
                             'origin':'automatic-primary-heading','adapter':VERSION})
        # Equipment-list nomenclature may describe a configuration rather than a
        # unique model. Keep it as a candidate; never infer M65 from OG107 alone.
        for n,line in d['lines'].items():
            t=normalized(line['text'])
            if re.match(r'^\(\d+\)\s+Coat\s+Man\.',t):
                end=n
                while end<n+5 and end+1 in d['lines'] and d['lines'][end+1]['text'].strip():
                    end+=1
                designation=re.sub(r'^\(\d+\)\s*','',normalized(window(d,n,end)))
                color=re.search(r'OG\s+\d{3}',designation)
                if color is None:continue
                base=designation[:color.end()]
                key=re.sub('[^a-z0-9]+','-',base.lower()).strip('-')
                mentions.append({'id':key,'year':None,'designation':base,'family':'fieldJacket',
                                 'source':d['id'],'independence':d['independence'],'alias':base,
                                 'line':n,'sectionEnd':end,'context':window(d,n,end),
                                 'origin':'configuration-list-not-unique-model','adapter':VERSION})
            # Test reports name aviation garments, but the current native family
            # enumeration has no aviation category. Retain discovery, not a false
            # classification or a universal assertion based on a proposed repair.
            aviation=re.match(r'^Jacket(?:,?\s*Flying,?)?(?:\s*\(Parka\))?[:,]?\s*\(?Type\s+([A-Z]-\d+[a-z]?)',t,re.I)
            if aviation:
                token=aviation[1]
                excluded.append({'source':d['id'],'line':n,'token':aviation[0],
                                 'candidateID':'aviation-jacket-'+token.lower(),
                                 'variantBaseToken':re.sub('[a-z]$','',token),
                                 'reason':'aviation family unsupported by native schema; test/configuration scope not generalized'})
    return mentions,excluded


def extract(docs,mentions,proof,digest,encode):
    claims=[];issues=[];by_id={d['id']:d for d in docs}
    for mention in mentions:
        if mention.get('adapter')!=VERSION:continue
        d=by_id[mention['source']];start,end=mention['line'],mention['sectionEnd']
        def add(field,value,a,b=None,scope='model'):
            c={'model':mention['id'],'field':field,'value':value,'scope':scope,
               'rule':VERSION,'evidence':[proof(d,a,a if b is None else b)]}
            c['id']=digest(encode(c))[:20];claims.append(c)
        add('officialDesignation',mention['designation'],start)
        add('family',mention['family'],start,scope='classification')
        # A publication date is NOT the date of adoption, production or service.
        add('period',{'event':'documented-in-publication','subject':mention['designation'],
                      'date':str(d['publicationYear'])},start,scope='document')
        if mention.get('origin')=='configuration-list-not-unique-model':
            issues.append({'model':mention['id'],'kind':'identity-ambiguity',
                           'detail':'Equipment-list material/configuration is not a unique model identity. No M65 equivalence inferred.'})
            t=mention['context'].lower()
            if 'cotton and nylon' in t:add('fabric','Coton et nylon',start,end,scope='configuration')
            if 'with integral hood' in t:add('hood','Capuche intégrée, rangement non établi',start,end,scope='configuration')
            continue
        if d.get('kind')=='primary_manual_network' and not re.search(r'\b(FM|TM|TC)[ -]',d['title'],re.I):
            issues.append({'model':mention['id'],'kind':'source-scope-warning',
                           'detail':'Institutional report, not a specification of the complete production model.'})
        for n in range(start,end+1):
            if n not in d['lines']:continue
            last=min(n+3,end)
            # Keep consecutive original lines for proof and join OCR word splits.
            original=' '.join(d['lines'][k]['text'] for k in range(n,last+1) if k in d['lines'])
            t=normalized(re.sub(r'[-¬]\s+(?=[a-z])','',original)).lower()
            # Do not carry an assertion about another garment mentioned for layering.
            subject=re.search(r'\b(?:the|this|mountain|field|combat|arctic)\s+(?:[a-z]+\s+){0,2}(?:jacket|shirt)\b',t)
            direct_pockets='there are two breast pockets and two lower pockets' in t and n-start<60
            if not subject and not direct_pockets:
                continue
            if re.search(r'not (?:windproof|waterproof|water.repell?ent)',t):
                continue
            if 'two breast pockets and two lower pockets' in t:
                add('pockets','Quatre poches avant',n,last)
            if 'four patch pockets' in t or 'four front pockets' in t:
                add('pockets','Quatre poches avant',n,last)
            if 'large pocket in the back' in t or 'back pocket' in t and 'slide fastener' in t:
                # A back pocket is not counted as a front pocket.
                add('construction','Poche dorsale fermée par glissière',n,last)
            if 'lining of this cotton jacket is made of wool' in t:
                add('lining','Doublure de laine',n,last)
                if 'cotton outer material' in t:
                    add('fabric','Coton (extérieur)',n,last)
            if 'wool lining' in t and re.search(r'(?:this|the) jacket',t):
                add('lining','Doublure de laine',n,last)
            if 'two adjustment buttons on the cuffs' in t:
                add('construction','Deux boutons de réglage aux poignets',n,last)
            if 'jacket' in t and 'warm when worn under the field jacket or parka' in t:
                add('function','Couche isolante portée sous une veste de terrain ou une parka',n,last)
            if 'hood' in t and re.search(r'(?:hood is|hood which|hood that) (?:an )?(?:integral|attached)',t):
                add('hood','Capuche intégrée, rangement non établi',n,last)
            # Read construction only; suggested repairs/test modifications and
            # garment-care directions never become universal historical features.
    return claims,issues
