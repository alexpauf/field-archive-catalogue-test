"""Explorer gate: independent identity evidence; scope warnings remain explicit."""
import copy
import re

def admit_partial(notice, evidence):
    key = notice['id']
    mentions = [m for m in evidence['mentions'] if m['id'] == key]
    claims = [c for c in evidence['claims'] if c['model'] == key]
    issues = [i for i in evidence['issues'] if i['model'] == key]
    reasons = []
    if len({m['independence'] for m in mentions}) < 2:
        reasons.append('Moins de deux ouvrages indépendants établissant l’identité')
    identities = [c for c in claims if c['field'] == 'officialDesignation' and c['scope'] == 'model']
    if not identities:
        reasons.append('Désignation principale non établie à la portée du modèle')
    if any(i['kind'] != 'source-scope-warning' for i in issues):
        reasons.append('Ambiguïté ou contradiction documentaire non résolue')
    refs = {s['id'] for s in notice['sources']}
    if len(refs) < 2 or not claims or not notice['sections']:
        reasons.append('Preuves ou références insuffisantes')
    if any(not f.get('sources') or not set(f['sources']) <= refs for s in notice['sections'] for f in s['facts']):
        reasons.append('Affirmation non sourcée')
    if reasons:
        return None, reasons
    model = copy.deepcopy(notice)
    model['intro'] = 'Notice historique partielle. Chaque information renvoie à sa source. Les éléments non établis restent signalés comme inconnus.'
    date=re.search(r'documented-in-publication\s*:\s*(19\d{2})',model.get('period',''))
    if date:
        model['period']=model['indexPeriod']='Attestée dans un document de '+date[1]
    if not model['illustration'].get('filename'):
        model['illustration']['title'] = 'Aucune photographie aux droits et à l’identité vérifiés disponible'
    model['criteria'] = []
    model['publication'] = {'identitySources': sorted(refs), 'summarySources': sorted(refs),
                            'identifierEligible': False, 'unknowns': [
                                'Notice partielle : période de service et caractéristiques non documentées inconnues.',
                                'Aucun critère de cette notice n’est admis pour Identifier.',
                                *['Portée de la source : '+i['detail'] for i in issues if i['kind']=='source-scope-warning']]}
    return model, []
