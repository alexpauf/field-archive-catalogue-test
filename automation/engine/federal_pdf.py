"""Full federal reference texts; no model registry and no image interpretation."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

VERSION = 'federal-pdf-v1'

def rendered(pages):
    rows = []
    for page in pages:
        for line in page['text'].splitlines():
            rows.append(f"L{len(rows)}@P{page['page']}: {line}")
    return '\n'.join(rows)

def validate_capture(doc, folder):
    relative = doc.get('rawRelativePath', '')
    if not re.fullmatch(r'http/[a-f0-9]{64}', relative):
        raise ValueError('Unsafe federal PDF path')
    raw = folder.parent / relative
    data = raw.read_bytes()
    if len(data) > 32_000_000 or hashlib.sha256(data).hexdigest() != doc['rawHTTPBodySHA256']:
        raise ValueError('Federal PDF integrity failure')
    extractor = Path(os.environ['FIELDARCHIVE_PDF_TEXT'])
    if not extractor.is_absolute() or not extractor.is_file():
        raise ValueError('Trusted PDF text extractor required')
    pages = json.loads(subprocess.check_output([str(extractor), str(raw)], timeout=60))
    if rendered(pages) != doc['toolResponse']['content'][0]['text']:
        raise ValueError('Federal PDF parsing view differs from original')
    if doc.get('rights') != 'US-federal-text-only' or not doc.get('rightsEvidence'):
        raise ValueError('Federal text rights basis absent')

def fold(text):
    return ' '.join(re.sub(r'-\s*\n\s*(?=[a-z])', '', text).split()).casefold()

def aliases(mention, doc, window):
    """Learn the defined short name from the first sentence of its own heading.

    Never drop qualifiers by a global synonym table; an alias needs a literal
    anaphoric definition under that garment's actual primary heading.
    """
    title = mention.get('designation', '')
    values = {fold(title): {'value': title, 'kind': 'literal-heading'}}
    end = min(mention['line'] + 5, mention.get('sectionEnd', mention['line']))
    first = window(doc, mention['line'], end)
    found = re.search(r'\bThe\s+((?:[A-Za-z-]+\s+){1,3}jacket)\b', first)
    if found:
        words = re.findall(r'[a-z]+', fold(found[1]))
        title_words = set(re.findall(r'[a-z]+', fold(title)))
        if set(words) <= title_words and len(words) >= 2:
            values[fold(found[1])] = {'value': found[1], 'kind': 'defined-in-primary-heading',
                                      'definitionStart': mention['line'], 'definitionEnd': end}
    return values

def corroborate(docs, mentions, window):
    by_id = {d['id']: d for d in docs}
    result = []
    seen = {(m['id'], m['source']) for m in mentions}
    for m in list(mentions):
        if m.get('origin') != 'automatic-primary-heading' or m.get('year'):
            continue
        names = aliases(m, by_id[m['source']], window)
        for d in docs:
            if d.get('adapter') != VERSION or d['independence'] == m['independence'] or (m['id'], d['id']) in seen:
                continue
            for n, line in d['lines'].items():
                # A maximum of six lines, on one PDF page. Original evidence is retained.
                end = min(n + 5, max(d['lines']))
                if any(d['lines'][k]['page'] != line['page'] for k in range(n, end + 1) if k in d['lines']):
                    continue
                text = window(d, n, end)
                joined = fold(text)
                selected = next((alias for alias in names if re.search(r'(?<![a-z])' + re.escape(alias) + r'(?![a-z])', joined)), None)
                kind = 'federal-literal-alias'
                if selected is None:
                    # Explicit shared-noun enumeration, not a bag of nearby adjectives.
                    enumerated = re.search(r'\bthe ([a-z, -]{1,100}?) jackets were declared limited standard\b', joined)
                    if enumerated:
                        items = [v.strip() for v in re.split(r',|\band\b', enumerated[1]) if v.strip()]
                        expanded = {item + ' jacket' for item in items}
                        selected = next((alias for alias in names if alias in expanded), None)
                        kind = 'federal-shared-noun-enumeration'
                if selected:
                    result.append({**m, 'source': d['id'], 'independence': d['independence'],
                                   'line': n, 'sectionEnd': end, 'context': text,
                                   'origin': 'federal-identity-corroboration', 'adapter': VERSION,
                                   'matchedAlias': selected, 'identityWarrant': kind,
                                   'aliasDefinition': names[selected]})
                    seen.add((m['id'], d['id']))
                    break
    return result

def identity_claims(docs, mentions, proof, digest, encode):
    by_id = {d['id']: d for d in docs}
    result = []
    for m in mentions:
        if m.get('origin') != 'federal-identity-corroboration':
            continue
        ev = proof(by_id[m['source']], m['line'], m['sectionEnd'])
        ev['role'] = 'identity-corroboration-only'
        c = {'model': m['id'], 'field': 'aliases', 'value': m['matchedAlias'],
             'scope': 'identity-corroboration', 'rule': VERSION, 'evidence': [ev]}
        c['id'] = digest(encode(c))[:20]
        result.append(c)
    return result
