"""Phase 51: bounded paragraph extraction with replayable lexical warrants.

No model fact table, authored notices, guessed aliases or adoption dates.
Rules operate only inside headings already discovered by the Phase 50 adapter.
Legacy captures retain their adapters; military OCR assertions are re-extracted.
"""
import copy
import json
import re
from collections import defaultdict

import documentary_ocr as ocr

VERSION = 'phase51-bounded-1'

# field, normalized value, lexical warrant. Separate semantic fields prevent a
# back-pocket zipper or neck button becoming the jacket's front closure.
RULES = {
    'front-four': ('pockets', 'Quatre poches avant',
                   r'(?:There are|has) two breast pockets and two lower pockets'),
    'back-zip': ('construction', 'Poche dorsale fermée par glissière',
                 r'the slide fastener which holds the back pocket shut'),
    'back-zip-right': ('pockets', 'Poche dorsale ; glissière située du côté droit',
                       r'the slide fastener which holds the back pocket shut is on the right side'),
    'internal-braces': ('construction', 'Bretelles intérieures réglables par boucles',
                        r'interior suspenders\. These suspenders.{0,110}?Buckles are provided to adjust the suspenders'),
    'hood-pocket': ('hood', 'Capuche repliée dans une poche dont le col couvre l’ouverture',
                    r'If the hood is not needed, it fits neatly into a pocket.{0,130}?The jacket-collar covers the opening of the pocket'),
    'hood-adjustment': ('construction', 'Capuche réglée par boutons et cordon autour du visage',
                        r'The hood is adjusted.{0,110}?fastening the buttons and adjusting the drawstring around the face'),
    'loose-layering': ('construction', 'Coupe ample pour superposition des couches',
                       r'The field jacket should be large enough.{0,150}?still be loose.{0,70}?not constrict'),
    'loose-shoulders': ('construction', 'Coupe ample aux bras et aux épaules',
                       r'It should have a loose fit, especially around the arms and shoulders'),
    'loose-winter': ('construction', 'Coupe ample permettant le port sur chemise de flanelle et chandail',
                     r'it should be fitted loosely enough to cover a flannel shirt and sweater'),
    'loose-arctic': ('construction', 'Coupe ample permettant le port sur chemise de flanelle et chandail',
                     r'The jacket should be large enough to fit comfortably over a flannel shirt and sweater'),
    'neck-wrist-waist': ('construction', 'Réglages boutonnés au cou et aux poignets ; cordon de taille',
                         r'double sets of buttons at the wrists and neck, and by the waist drawstrings'),
    'cuff-two': ('construction', 'Deux boutons de réglage aux poignets',
                 r'There are two adjustment buttons on the cuffs'),
    'collar-three': ('collar', 'Col ouvert, fermé ou relevé avec patte boutonnée sous le menton',
                     r'The collar may be worn in three different ways:.{0,150}?worn open.{0,800}?It may be closed.{0,380}?collar turned up and the flap buttoned under the chin'),
    'cotton-shell': ('fabric', 'Coton (extérieur)',
                     r'lining of this cotton jacket is made of wool.{0,200}?cotton outer material'),
    'wool-lining': ('lining', 'Doublure de laine',
                    r'(?:lining of this cotton jacket is made of wool|shrink the wool lining)'),
    'pile-insulation': ('function', 'Couche isolante portée sous une veste de terrain ou une parka',
                        r'The pile jacket.{0,30}?warm when worn under the field jacket or parka'),
    'armor-use': ('function', 'Vêtement porté par les membres des forces blindées par temps froid',
                  r'winter combat jacket is worn by members of the armored force when the weather is cold'),
    'moderate-cold': ('function', 'Veste destinée aux températures modérément froides',
                      r'Arctic field jacket is useful in moderately cold temperatures'),
    'water-repellent': ('finish', 'Traitement déperlant documenté',
                        r'(?:The mountain jacket is treated to make it water-repellent|The field jacket.{0,50}?It has been treated to make it water-repellent)'),
}


def view(doc, start, end):
    """Parsing view and character-to-original-line map; never rewrites the capture.

    Only standalone page numbers and AGO print codes are removed. Captions stay,
    so a rule must explicitly tolerate them or fail rather than cross blindly.
    """
    text=''; offsets=[]
    for n in range(start,end+1):
        if n not in doc['lines']: continue
        t=doc['lines'][n]['text']
        if re.fullmatch(r'(?:\d{1,3}|AGO\s+\w+)',t,re.I):continue
        if not t:continue
        if text:
            if text[-1] in '-¬' and t[:1].islower():
                text=text[:-1];offsets.pop()
            else:
                text+=' ';offsets.append(n)
        text+=t;offsets.extend([n]*len(t))
    return text,offsets


def normalized_designation(text):
    text=ocr.normalized(text).upper()
    return re.sub(r'\bM[I1L]\s*(9\d{2})\b',r'M1\1',text)


def validate_warrant(claim):
    """Independent lexical check: value, field and evidence must fit the rule.

    Does not pretend lexical agreement proves OCR accuracy or historical truth.
    """
    rid=claim.get('warrant')
    if rid in RULES:
        field,value,pattern=RULES[rid]
        ev=claim['evidence'][0]
        # Recreate the same display normalization from original numbered lines.
        text=ev.get('parsingExcerpt')
        if claim['field']!=field or claim['value']!=value or text is None or not re.search(pattern,text,re.I):
            raise ValueError('Lexical warrant/value mismatch')
    elif rid=='official-heading':
        if claim['field']!='officialDesignation' or normalized_designation(claim['value']) not in normalized_designation(claim['evidence'][0]['excerpt']):
            raise ValueError('Designation not covered by evidence')
    else:
        raise ValueError('Unknown lexical warrant')


def improve(docs,mentions,claims,issues):
    import pipeline as p
    mentions=copy.deepcopy(mentions)
    by_id={d['id']:d for d in docs}
    # Remove Phase 50 OCR facts, including the partial-heading proof. Keep source
    # warnings and ambiguity; these are never cleared by finding more features.
    claims=[c for c in claims if c['rule']!=ocr.VERSION]

    def add(m,field,value,d,a,b,scope='model',warrant=None,identity=True):
        ev=p.proof(d,a,b)
        ev['role']='assertion'
        if warrant in RULES:
            ev['parsingExcerpt']=view(d,a,b)[0]
        evidence=[ev]
        if identity and a!=m['line'] and d['id']==m['source']:
            ident=p.proof(d,m['line'],m.get('identityEnd',m['line']))
            ident['role']='identity';evidence.append(ident)
        c={'model':m['id'],'field':field,'value':value,'scope':scope,
           'rule':VERSION,'evidence':evidence}
        if warrant:
            c['warrant']=warrant;validate_warrant(c)
        claims.append(c)

    for m in mentions:
        if m.get('adapter')!=ocr.VERSION:continue
        d=by_id[m['source']];a,b=m['line'],m['sectionEnd']
        config=m.get('origin')=='configuration-list-not-unique-model'
        m['identityEnd']=b if config else a
        scope='configuration' if config else 'model'
        correction_line=None
        if not config and m['designation'] not in p.window(d,a,a):
            token=re.search(r'M19\d\d',m['designation'])
            if token:
                pattern=r'jacket,?\s*field,?\s*'+re.escape(token[0])+r'\b'
                correction_line=next((n for n in range(a,b+1) if n in d['lines'] and re.search(pattern,d['lines'][n]['text'],re.I)),None)
                if correction_line is None:
                    issues.append({'model':m['id'],'kind':'identity-ambiguity',
                                   'detail':'OCR model correction lacks a literal same-section jacket nomenclature.'})
                    continue
        # Corrected OCR name needs an additional literal occurrence, retained.
        add(m,'officialDesignation',m['designation'],d,a,m['identityEnd'],scope,'official-heading')
        if correction_line is not None:
            ev=p.proof(d,correction_line,correction_line);ev['role']='OCR-corroboration';claims[-1]['evidence'].append(ev)
        add(m,'family',m['family'],d,a,a,'classification')
        add(m,'period',{'event':'documented-in-publication','subject':m['designation'],
                        'date':str(d['publicationYear'])},d,a,m['identityEnd'],'document')
        if config:
            t=p.window(d,a,b)
            if re.search('cotton and nylon',t,re.I):add(m,'fabric','Coton et nylon',d,a,b,scope)
            color=re.search(r'OG\s+\d{3}',t)
            if color:add(m,'color',color[0],d,a,b,scope)
            if 'with integral hood' in t.lower():add(m,'hood','Capuche intégrée, rangement non établi',d,a,b,scope)
            # No transfer from a separate generic coat paragraph: the manual
            # gives no explicit model/variant bridge between list and narrative.
            continue
        text,offsets=view(d,a,b)
        # Each rule belongs to the named section, not a moving four-line window.
        for rid,(field,value,pattern) in RULES.items():
            for match in re.finditer(pattern,text,re.I):
                prefix=re.split(r'[.!?;]',text[max(0,match.start()-80):match.start()])[-1]
                if re.search(r'\b(?:no|not|never|without|rather than|proposed|experimental)\b',prefix,re.I):
                    continue
                first,last=offsets[match.start()],offsets[match.end()-1]
                add(m,field,value,d,first,last,warrant=rid)
        # A stock/contract number in running prose is not automatically the
        # reference for every size or production variant. Retain as an observation.
        for tag,field,pattern in [
            ('stock','stock',r'\b(?:FSN\s*[:#]?\s*\d{4}[- ]\d{3}[- ]\d{4}|NSN\s*[:#]?\s*\d{4}[- ]\d{2}[- ]\d{3}[- ]\d{4})\b'),
            ('contract','contract',r'\b(?:Contract|DA|DSA|DLA)\s*(?:No\.?\s*)?[:#-]?\s*[A-Z0-9]+(?:-[A-Z0-9]+){2,}\b')]:
            for match in re.finditer(pattern,text,re.I):
                first,last=offsets[match.start()],offsets[match.end()-1]
                add(m,field,match[0],d,first,last,'document-entry-not-universal')
        for match in re.finditer(r'\b(?:label reads|marking reads)\s*[:]?\s*"([^"\n]{2,150})"',text,re.I):
            first,last=offsets[match.start()],offsets[match.end()-1]
            add(m,'markings',match[1],d,first,last,'document-entry-not-universal')

    # Exact primary nomenclature only. A caption adds identity corroboration,
    # never material/construction, and cannot create an unheaded new model.
    originals=list(mentions)
    seen={(m['id'],m['source'],m['line']) for m in mentions}
    for m in originals:
        if m.get('adapter')!=ocr.VERSION or m.get('origin')!='automatic-primary-heading':continue
        if m.get('year'):continue  # numbered model matching remains in Phase 49
        pattern=r'^\s*'+re.escape(m['designation'])+r'[\s.*]*$'
        for d in docs:
            if d['independence']==m['independence'] or d['kind']=='media_manifest':continue
            for n,line in d['lines'].items():
                match=re.search(pattern,line['text'],re.I)
                # Institutional PDF captions may be linearized into one line
                # containing several nomenclatures. Accept an exact final label
                # only, never text following that label or a variant qualifier.
                if not match and d['kind']=='institutional_survey' and len(re.findall(r'\b(?:Coat|Jacket|Shirt),',line['text']))>=2 and not re.search(r'\b(?:modified|experimental|prototype|women|pattern)\b',line['text'],re.I):
                    match=re.search(r'(?<![\w,])'+re.escape(m['designation'])+r'[\s.*]*$',line['text'],re.I)
                if not match or (m['id'],d['id'],n) in seen:continue
                # No near-match, omitted adjectives or variant suffix stripping.
                seen.add((m['id'],d['id'],n))
                linked={**m,'source':d['id'],'independence':d['independence'],'line':n,
                        'identityEnd':n,'sectionEnd':n,'context':line['text'],
                        'origin':'exact-nomenclature-corroboration','adapter':VERSION}
                mentions.append(linked)
                add(linked,'aliases',match[0],d,n,n,'identity-corroboration',identity=False)

    # Explicit detachable-hood component headings can link to a numbered jacket
    # only when the heading itself names that model. No integral-hood inference.
    ids={m['id'] for m in mentions}
    for d in docs:
        if d.get('adapter')!=ocr.VERSION:continue
        for n,line in d['lines'].items():
            match=re.search(r'^\d+\. HOOD, JACKET, FIELD, M[Il1]?\s*(9\d\d)\..*detachable hood',line['text'],re.I)
            if not match:continue
            key=p.model_id(int('1'+match[1]))
            if key not in ids:continue
            dummy={'id':key,'line':n,'source':d['id']}
            add(dummy,'hood','Capuche séparée',d,n,n,warrant=None)

    # Collapse duplicate claims across scans and partial/full same-work copies.
    merged={}
    for c in claims:
        sig=(c['model'],c['field'],c['scope'],json.dumps(c['value'],sort_keys=True))
        if sig not in merged:merged[sig]=c
        else:
            target=merged[sig]
            for ev in c['evidence']:
                if ev not in target['evidence']:target['evidence'].append(ev)
    claims=list(merged.values())
    for c in claims:c['id']=p.digest(p.encode({k:v for k,v in c.items() if k!='id'}))[:20]
    return mentions,claims,issues
