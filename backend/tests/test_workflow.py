import os,sys,tempfile,json,io,importlib
from pathlib import Path
from datetime import date,timedelta
import pytest
os.environ['SME_DATA_DIR']=tempfile.mkdtemp(prefix='sme-v4-tests-')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import main
from schemas import ApplicationInput,DEFAULTS
from affordability import calculate
from llm import features_from_ledger

PASSWORD='Test-password-4.0!'
RECORD={'name':'Test Store','sector':'Retail','city':'Rawalpindi','monthly_revenue':350000,'loan_amount':1000000,
 **DEFAULTS,'existing_debt_ratio':10000/350000,'monthly_cash_collections':350000,'operating_costs':220000,'monthly_taxes':5000,
 'owner_withdrawals':15000,'existing_monthly_repayments':10000,'annual_rate':.2,'tenure_months':24,'has_credit_history':False}

@pytest.fixture(scope='module')
def admin():
    c=TestClient(main.app)
    token=(main.db.ROOT/'setup-token.txt').read_text()
    assert c.post('/api/setup',json={'username':'admin','password':PASSWORD,'setup_token':token}).status_code==200
    assert c.post('/api/login',json={'username':'admin','password':PASSWORD}).status_code==200
    return c

def create(c,**changes):
    r=c.post('/api/applicants',json={**RECORD,**changes});assert r.status_code==200,r.text;return r.json()


def test_access_and_bootstrap(admin):
    c=TestClient(main.app)
    assert c.get('/api/applicants').status_code==401
    assert c.post('/api/setup',json={'username':'another','password':PASSWORD,'setup_token':'wrong'}).status_code==409
    assert len(admin.get('/api/applicants').json())>=24
    assert admin.post('/api/login',headers={'Origin':'https://evil.invalid'},json={'username':'admin','password':PASSWORD}).status_code==403

@pytest.mark.parametrize('field,value',[('monthly_revenue',0),('loan_amount',-1),('digital_payment_share',5),('annual_rate',6),('tenure_months',0),('customer_concentration',-1),('revenue_growth_6m',-2)])
def test_rejects_invalid(admin,field,value):
    assert admin.post('/api/applicants',json={**RECORD,field:value}).status_code==422


def test_nan_and_infinity():
    for value in [float('inf'),float('nan')]:
        with pytest.raises(Exception):ApplicationInput.model_validate({**RECORD,'monthly_revenue':value})


def test_csv_partial_success_and_retrievable(admin):
    data='name,monthly_revenue,loan_amount,digital_payment_share\nValid CSV,350000,1000000,0.5\nBad CSV,0,1000000,0.5\nBad Share,350000,1000000,5\n'
    r=admin.post('/api/upload-csv',files={'file':('portfolio.csv',data,'text/csv')})
    assert r.status_code==200,r.text
    body=r.json();assert len(body['saved'])==1;assert {e['row'] for e in body['errors']}=={3,4}
    d=admin.get(f"/api/applicants/{body['saved'][0]['id']}").json()
    assert d['record']['name']=='Valid CSV' and d['provisional'] and d['status']=='needs_information'
    assert 'business_age_years' in d['assumed_fields']
    assert d['affordability']['status']=='unknown'


def test_csv_edge_cases(admin):
    for data in ['name,name,monthly_revenue,loan_amount\nA,A,1,1\n','name,loan_amount\nA,1\n','monthly_revenue,loan_amount\n']:
        assert admin.post('/api/upload-csv',files={'file':('x.csv',data)}).status_code==422
    assert admin.post('/api/upload-csv',files={'file':('x.csv','monthly_revenue,loan_amount\nInfinity,1\n')}).json()['errors']


def test_affordability():
    r=calculate({**RECORD,'annual_rate':0,'loan_amount':1200000,'tenure_months':12})
    assert r['instalment']==100000 and r['dscr']==1
    assert r['status']=='fail' and r['stress_dscr']<r['dscr']
    assert calculate({**RECORD,'operating_costs':None})['status']=='unknown'


def test_scenario_consistency_and_revision(admin):
    d=create(admin);aid=d['id'];original=d['assessment']['pd']
    assert admin.post('/api/whatif',json={'id':aid,'overrides':{'loan_amount':-1}}).status_code==422
    r=admin.post('/api/whatif',json={'id':aid,'base_assessment_id':d['assessment_id'],'overrides':{'loan_amount':3000000,'cash_reserve_months':2}})
    assert r.status_code==200,r.text;s=r.json()
    assert s['record']['loan_amount']==3000000 and s['effective_record']['cash_reserve_months']==2
    assert s['assessment']['pd']!=original and s['affordability']['instalment']>d['affordability']['instalment']
    assert admin.get(f'/api/applicants/{aid}').json()['record']['loan_amount']==1000000
    committee=admin.post('/api/committee',json={'id':aid,'scenario_id':s['scenario_id']}).json()
    assert committee['scenario_id']==s['scenario_id'] and committee['source'] in ['offline','offline_after_error']
    report=admin.get(f"/api/applicants/{aid}/report?scenario_id={s['scenario_id']}").json()
    assert report['active_scenario']['result']['assessment']==s['assessment']
    assert admin.post(f"/api/applicants/{aid}/scenarios/{s['scenario_id']}/apply",json={}).status_code==200
    assert admin.get(f'/api/applicants/{aid}').json()['record']['loan_amount']==3000000
    assert admin.post('/api/committee',json={'id':aid,'scenario_id':s['scenario_id']}).status_code==409
    assert len(admin.get(f'/api/applicants/{aid}/report').json()['assessment_history'])==2


def test_roles_and_ownership(admin):
    for name,role in [('applicant-a','applicant'),('applicant-b','applicant'),('officer','credit_officer')]:
        assert admin.post('/api/users',json={'username':name,'password':PASSWORD,'role':role}).status_code==200
    a,b,officer=[TestClient(main.app) for _ in range(3)]
    for c,name in [(a,'applicant-a'),(b,'applicant-b'),(officer,'officer')]:assert c.post('/api/login',json={'username':name,'password':PASSWORD}).status_code==200
    d=create(a);aid=d['id']
    assert len(a.get('/api/applicants').json())==1
    assert b.get(f'/api/applicants/{aid}').status_code==404
    assert a.get('/api/impact').status_code==403
    assert a.get('/api/training-export').status_code==403
    assert a.post('/api/users',json={'username':'evil','password':PASSWORD,'role':'administrator'}).status_code==403
    assert officer.post(f'/api/applicants/{aid}/decision',json={'assessment_id':d['assessment_id'],'decision':'approved','reason':'A complete review reason'}).status_code==403
    assert officer.post(f'/api/applicants/{aid}/decision',json={'assessment_id':d['assessment_id'],'decision':'needs_information','reason':'Please provide verified revenue evidence'}).status_code==200


def test_document_verification_decision_and_audit(admin):
    d=create(admin);aid=d['id']
    body={'assessment_id':d['assessment_id'],'decision':'approved','reason':'Verified financial and business records'}
    assert admin.post(f'/api/applicants/{aid}/decision',json=body).status_code==422
    assert admin.post(f'/api/applicants/{aid}/documents',data={'kind':'revenue'},files={'file':('bad.svg',b'<svg/>','image/svg+xml')}).status_code==415
    for kind in ['identity','business','bank_account','revenue']:
        r=admin.post(f'/api/applicants/{aid}/documents',data={'kind':kind},files={'file':('document.pdf',b'%PDF-1.4\nTest', 'application/pdf')})
        assert r.status_code==200,r.text;doc=r.json();assert 'path' not in doc
        assert TestClient(main.app).get(f"/api/documents/{doc['id']}").status_code==401
        fields=['monthly_revenue',*main.RISK_FIELDS,*main.FINANCE_FIELDS] if kind=='revenue' else []
        r=admin.post(f"/api/documents/{doc['id']}/verify",json={'status':'verified','note':'Reviewed originals and reconciled values','fields':fields})
        assert r.status_code==200,r.text;d=r.json()
    body['assessment_id']=d['assessment_id']
    r=admin.post(f'/api/applicants/{aid}/decision',json=body);assert r.status_code==200,r.text
    assert r.json()['status']=='approved'
    assert admin.put(f'/api/applicants/{aid}',json=RECORD).status_code==409
    assert admin.post(f'/api/applicants/{aid}/disburse',json={}).status_code==200
    date_str=date.today().isoformat()
    assert admin.post(f'/api/applicants/{aid}/repayments',json={'due_date':date_str,'paid_date':date_str,'amount_due':10000,'amount_paid':10000}).status_code==200
    assert len(admin.get(f'/api/applicants/{aid}/repayments').json())==1
    actions=[x['action'] for x in admin.get(f'/api/applicants/{aid}/audit').json()]
    assert 'human_decision' in actions and 'document_reviewed' in actions and 'repayment_recorded' in actions
    assert any(x['application_id']==aid for x in admin.get('/api/training-export').json()['applications'])


def test_conditional_evidence_can_be_completed(admin):
    d=create(admin);aid=d['id']
    body={'assessment_id':d['assessment_id'],'decision':'approved_with_conditions','reason':'Conditional approval pending source documents','conditions':['Verify revenue']}
    assert admin.post(f'/api/applicants/{aid}/decision',json=body).status_code==200
    doc=admin.post(f'/api/applicants/{aid}/documents',data={'kind':'revenue'},files={'file':('doc.pdf',b'%PDF-1.4\nTest')}).json()
    r=admin.post(f"/api/documents/{doc['id']}/verify",json={'status':'verified','note':'Verified submitted revenue evidence','fields':['monthly_revenue']})
    assert r.status_code==200 and r.json()['status']=='under_review'


def test_ledger_zero_months_and_dates():
    raw={'monthly_sales':[{'month':f'2025-{i:02d}','sales':v} for i,v in enumerate([0,100,200,300,400,500],1)],'digital_share_estimate':.3}
    d=features_from_ledger(raw)
    assert d['months_read']==6 and d['monthly_revenue']==250
    assert d['revenue_growth_6m']==3
    raw['monthly_sales'][3]['month']='2025-08'
    assert 'revenue_growth_6m' not in features_from_ledger(raw)
    raw={'monthly_sales':[0,100,200,300,400,500]}
    assert 'revenue_growth_6m' not in features_from_ledger(raw)


def test_policy_and_restart_persistence(admin):
    d=create(admin);sid=d['assessment_id']
    assert admin.post('/api/settings',json={'dscr_threshold':1.4}).status_code==200
    now=admin.get(f"/api/applicants/{d['id']}").json();assert now['assessment_id']!=sid
    assert now['affordability']['threshold']==1.4
    n=len(admin.get('/api/applicants').json())
    importlib.reload(main)
    assert len(admin.get('/api/applicants').json())==n
    assert not main.db.ROOT.joinpath('setup-token.txt').exists()


def test_nonfinite_json_and_audit_permissions(admin):
    for value in ['NaN','Infinity']:
        body=json.dumps(RECORD).replace('350000',value,1)
        r=admin.post('/api/applicants',content=body,headers={'Content-Type':'application/json'})
        assert r.status_code==422
    assert admin.post('/api/applicants',json={**RECORD,'monthly_revenue':True}).status_code==422
    assert admin.get('/api/audit').status_code==200
    assert TestClient(main.app).get('/api/audit').status_code==401


def test_ledger_confirmation_provenance(admin,monkeypatch):
    extracted={'monthly_revenue':350000,'digital_payment_share':.5,'months_read':6,'source':'claude','requires_confirmation':True}
    monkeypatch.setattr(main.llm,'extract_from_image',lambda data,mime:extracted)
    r=admin.post('/api/extract',files={'file':('ledger.png',b'\x89PNG\r\n\x1a\nTest','image/png')})
    assert r.status_code==200,r.text;draft=r.json()
    d=create(admin,digital_payment_share=.4)
    r=admin.post(f"/api/applicants/{d['id']}/ledger-draft/{draft['extraction_id']}",json={})
    assert r.status_code==200,r.text
    evidence=r.json()['evidence']
    assert evidence['monthly_revenue']['source']=='extracted_from_document'
    assert evidence['digital_payment_share']['source']=='user_corrected_extraction'
    assert evidence['monthly_revenue']['verification']=='unverified'
    assert admin.post(f"/api/applicants/{d['id']}/ledger-draft/{draft['extraction_id']}",json={}).status_code==404


def test_conditional_revision_reopens_review(admin):
    d=create(admin,utility_on_time_rate=None);aid=d['id']
    assert admin.post(f'/api/applicants/{aid}/decision',json={'assessment_id':d['assessment_id'],'decision':'approved_with_conditions','reason':'Add missing utility evidence before final approval','conditions':['Complete utility evidence']}).status_code==200
    r=admin.put(f'/api/applicants/{aid}',json=RECORD)
    assert r.status_code==200 and r.json()['status']=='under_review'
    assert r.json()['assessment_id']!=d['assessment_id']
