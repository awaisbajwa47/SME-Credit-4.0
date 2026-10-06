import os,json,hashlib
from pathlib import Path
import joblib
from data_gen import generate
from model import CreditModel, FEATURES
from schemas import ApplicationInput, DEFAULTS, RISK_FIELDS, FINANCE_FIELDS
from affordability import calculate
import database as db

synthetic=generate(1000)
train,test=synthetic.iloc[:700],synthetic.iloc[700:]
model=CreditModel().fit(train)
MODEL_VERSION='synthetic-logistic-v4-seed7'
MODEL_KIND='synthetic'
MODEL_REPORT={'auc':round(model.auc(test),3),'sample_size':300,'default_horizon':'Undefined for synthetic labels',
 'warning':'Practice model trained on synthetic outcomes. Not validated for real lending.'}
# Explicit local-only activation. Never accept serialized models through web uploads.
model_path=os.getenv('SME_MODEL_PATH')
if model_path:
    artifact=joblib.load(Path(model_path))
    if artifact.get('features')!=FEATURES or artifact.get('kind')!='real':
        raise RuntimeError('Model artifact is not compatible with this scoring pipeline')
    model=artifact['model']; MODEL_VERSION=artifact['version']; MODEL_KIND='real'; MODEL_REPORT=artifact['report']


def public_record(rec):
    return {k:(v.item() if hasattr(v,'item') else v) for k,v in rec.items() if k!='defaulted'}

def evidence_for(record,source='user_entered'):
    return {k:{'source':source if record.get(k) is not None else 'missing',
      'verification':'unverified','document_id':None} for k in ['monthly_revenue','loan_amount']+RISK_FIELDS+FINANCE_FIELDS}

def evaluate(record,evidence=None):
    valid=ApplicationInput.model_validate(record).model_dump()
    assumed=[k for k in RISK_FIELDS if valid[k] is None]
    effective={**valid,**{k:DEFAULTS[k] for k in assumed}}
    a=model.assess(effective)
    threshold=float(db.one("SELECT value FROM settings WHERE key='dscr_threshold'")['value'])
    affordability=calculate(valid,threshold)
    warnings=[f'{k} is missing; demo assumption {DEFAULTS[k]} used.' for k in assumed]
    if valid['existing_debt_ratio'] is not None and valid['existing_debt_ratio']>1:
        warnings.append('Existing debt payments exceed monthly revenue; confirm the ratio definition.')
    if affordability['status']=='unknown': warnings.append('Affordability unknown: financing or cash-flow fields missing.')
    elif affordability['status']=='fail': warnings.append('Cash flow does not meet the illustrative DSCR threshold.')
    if affordability.get('stress_pass') is False: warnings.append('A 20% collections reduction fails the affordability stress test.')
    ev=evidence or evidence_for(valid)
    for k in assumed: ev[k]={'source':'assumed_for_simulation','verification':'unverified','document_id':None}
    unverified=[k for k in ['monthly_revenue']+RISK_FIELDS+FINANCE_FIELDS if ev.get(k,{}).get('verification')!='verified']
    if unverified: warnings.append('Financial evidence has not been fully verified.')
    consistency=[]
    if valid['existing_monthly_repayments'] is not None and valid['existing_debt_ratio'] is not None:
        derived=valid['existing_monthly_repayments']/valid['monthly_revenue']
        if abs(derived-valid['existing_debt_ratio'])>.005:
            consistency.append('Existing debt ratio differs from existing monthly repayments / monthly revenue. Reconcile before approval.')
    warnings.extend(consistency)
    return {'record':valid,'effective_record':effective,'assessment':a,
       'path':model.path_to_approval(effective) if a['decision']!='approve' else None,
       'affordability':affordability,'evidence':ev,'assumed_fields':assumed,'warnings':warnings,
       'consistency_errors':consistency,'provisional':bool(assumed),'model_version':MODEL_VERSION,'model_kind':MODEL_KIND,
       'model_warning':MODEL_REPORT.get('warning'),'default_horizon':MODEL_REPORT.get('default_horizon'),
       'suggested_workflow':'needs_information' if assumed or affordability['status']=='unknown' else 'under_review'}


def save_assessment(aid,record,evidence):
    result=evaluate(record,evidence)
    sid=db.execute('INSERT INTO assessments(application_id,snapshot,result,model_version,created_at) VALUES (?,?,?,?,?)',
      (aid,json.dumps(record),json.dumps(result),MODEL_VERSION,db.now()))
    db.execute('UPDATE applications SET current_assessment_id=?,updated_at=? WHERE id=?',(sid,db.now(),aid))
    return sid,result


def create_application(record,owner,source='user_entered',demo=False):
    record=ApplicationInput.model_validate(record).model_dump()
    evidence=evidence_for(record,source)
    # Initial evaluation is performed before any insertion.
    result=evaluate(record,evidence)
    with db.connect() as c:
        bid=c.execute('INSERT INTO businesses(name,sector,city,owner_id) VALUES (?,?,?,?)',
           (record['name'],record['sector'],record['city'],owner)).lastrowid
        aid=c.execute('INSERT INTO applications(business_id,record,evidence,status,is_demo,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
           (bid,json.dumps(record),json.dumps(evidence),result['suggested_workflow'],int(demo),db.now(),db.now())).lastrowid
        sid=c.execute('INSERT INTO assessments(application_id,snapshot,result,model_version,created_at) VALUES (?,?,?,?,?)',
           (aid,json.dumps(record),json.dumps(result),MODEL_VERSION,db.now())).lastrowid
        c.execute('UPDATE applications SET current_assessment_id=? WHERE id=?',(sid,aid))
    if not demo: db.audit(owner,aid,'application_created',after={'assessment_id':sid,'source':source})
    return aid


def seed():
    if db.one("SELECT value FROM settings WHERE key='demo_seeded'"): return
    demo_model=CreditModel().fit(train)
    scored=[(r,demo_model.assess(r)) for r in test.to_dict('records')]
    prepared=[]
    for decision in ['approve','refer','decline']:
        for r,a in [x for x in scored if x[1]['decision']==decision][:8]:
            record=ApplicationInput.model_validate({k:public_record(r)[k] for k in ApplicationInput.model_fields if k in r}).model_dump()
            evidence=evidence_for(record,'synthetic_demo')
            prepared.append((record,evidence,evaluate(record,evidence)))
    # The entire seed is one transaction: interrupted starts cannot leave half a portfolio.
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT value FROM settings WHERE key='demo_seeded'").fetchone(): return
        for record,evidence,result in prepared:
            bid=c.execute('INSERT INTO businesses(name,sector,city,owner_id) VALUES (?,?,?,NULL)',(record['name'],record['sector'],record['city'])).lastrowid
            aid=c.execute('INSERT INTO applications(business_id,record,evidence,status,is_demo,created_at,updated_at) VALUES (?,?,?,?,1,?,?)',
              (bid,json.dumps(record),json.dumps(evidence),result['suggested_workflow'],db.now(),db.now())).lastrowid
            sid=c.execute('INSERT INTO assessments(application_id,snapshot,result,model_version,created_at) VALUES (?,?,?,?,?)',
              (aid,json.dumps(record),json.dumps(result),MODEL_VERSION,db.now())).lastrowid
            c.execute('UPDATE applications SET current_assessment_id=? WHERE id=?',(sid,aid))
        c.execute("INSERT INTO settings(key,value) VALUES ('demo_seeded','1')")
