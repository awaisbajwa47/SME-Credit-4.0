"""SME Credit 4.0: persistent application workflow with server-enforced access."""
import os,json,secrets,hashlib,time,io,csv,logging
from datetime import date
from pathlib import Path
from fastapi import FastAPI,UploadFile,File,HTTPException,Depends,Request,Response,Form
from fastapi.responses import FileResponse,JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError,BaseModel,Field
import database as db
from schemas import *
import llm

db.init()
from service import evaluate,create_application,save_assessment,seed,model,MODEL_VERSION,MODEL_KIND,MODEL_REPORT,test
seed()
app=FastAPI(title='SME Credit 4.0',version='4.0')
@app.exception_handler(RequestValidationError)
async def safe_validation_error(request,exc):
    # Do not serialize NaN, infinity, raw uploaded inputs or exception objects.
    return JSONResponse({'detail':[{'loc':list(e['loc']),'msg':e['msg'],'type':e['type']} for e in exc.errors()]},status_code=422)

logger=logging.getLogger('sme-credit')
# Login throttles apply per client and username; sessions are server side.
ATTEMPTS={}
ALLOWED_ORIGINS=set(os.getenv('SME_ALLOWED_ORIGINS','http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000').split(','))

@app.middleware('http')
async def security_headers(request,call_next):
    origin=request.headers.get('origin')
    if request.method not in ('GET','HEAD','OPTIONS') and origin and origin not in ALLOWED_ORIGINS:
        return JSONResponse({'detail':'Untrusted request origin'},status_code=403)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    return response


def user(request:Request):
    token=request.cookies.get('sme_session','')
    found=db.one('SELECT u.id,u.username,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?',
       (hashlib.sha256(token.encode()).hexdigest(),time.time()))
    if not found: raise HTTPException(401,'Sign in required')
    return found

def require(u,roles):
    if u['role'] not in roles: raise HTTPException(403,'Your role cannot perform this action')

def application(aid,u):
    r=db.one('SELECT a.*,b.owner_id FROM applications a JOIN businesses b ON b.id=a.business_id WHERE a.id=?',(aid,))
    if not r: raise HTTPException(404,'Unknown application')
    if u['role']=='applicant' and r['owner_id']!=u['id']: raise HTTPException(404,'Unknown application')
    return r

def clean_doc(d):
    return {k:v for k,v in d.items() if k!='path'}

def detailed(aid,u):
    row=application(aid,u)
    saved=db.one('SELECT * FROM assessments WHERE id=?',(row['current_assessment_id'],))
    result=json.loads(saved['result'])
    result.update({'id':aid,'assessment_id':saved['id'],'status':row['status'],'is_demo':bool(row['is_demo']),
      'created_at':row['created_at'],'documents':[clean_doc(d) for d in db.all_rows('SELECT * FROM documents WHERE application_id=?',(aid,))],
      'human_decisions':db.all_rows('SELECT h.*,u.username AS reviewer FROM human_decisions h JOIN users u ON u.id=h.reviewer_id WHERE application_id=? ORDER BY h.id DESC',(aid,)),
      'committee_reviews':[dict(r,result=json.loads(r['result'])) for r in db.all_rows('SELECT * FROM committee_reviews WHERE application_id=? ORDER BY id DESC',(aid,))],
      'disbursed_at':row['disbursed_at']})
    return result

@app.get('/api/health')
def health():
    return {'version':'4.0','setup_required':not bool(db.one('SELECT id FROM users LIMIT 1'))}

@app.post('/api/setup')
def setup(body:Setup):
    path=db.ROOT/'setup-token.txt'
    if not path.exists(): raise HTTPException(409,'Setup already completed')
    if not secrets.compare_digest(body.setup_token.strip(),path.read_text().strip()): raise HTTPException(403,'Invalid setup token')
    # BEGIN IMMEDIATE protects the first-admin bootstrap against concurrent requests.
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT id FROM users LIMIT 1').fetchone(): raise HTTPException(409,'Setup already completed')
        uid=c.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)',
           (body.username,db.password_hash(body.password),'administrator',db.now())).lastrowid
    path.unlink(missing_ok=True)
    db.audit(uid,None,'administrator_setup')
    return {'message':'Administrator created. Sign in.'}

@app.post('/api/login')
def login(body:Login,request:Request,response:Response):
    key=(request.client.host if request.client else 'local',body.username)
    recent=[t for t in ATTEMPTS.get(key,[]) if t>time.time()-900]
    if len(recent)>=10: raise HTTPException(429,'Too many attempts. Try again in 15 minutes.')
    ATTEMPTS[key]=recent+[time.time()]
    u=db.one('SELECT * FROM users WHERE username=?',(body.username,))
    if not u or not db.check_password(body.password,u['password_hash']): raise HTTPException(401,'Incorrect username or password')
    ATTEMPTS.pop(key,None)
    token=secrets.token_urlsafe(32)
    db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
    db.execute('INSERT INTO sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],time.time()+28800))
    response.set_cookie('sme_session',token,httponly=True,samesite='strict',secure=os.getenv('SME_SECURE_COOKIES')=='1',max_age=28800,path='/')
    db.audit(u['id'],None,'login')
    return {'id':u['id'],'username':u['username'],'role':u['role']}

@app.post('/api/logout')
def logout(request:Request,response:Response):
    token=request.cookies.get('sme_session','')
    db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie('sme_session',path='/')
    return {'ok':True}

@app.get('/api/me')
def me(u=Depends(user)): return u

@app.get('/api/users')
def users(u=Depends(user)):
    require(u,['administrator'])
    return db.all_rows('SELECT id,username,role,created_at FROM users')

@app.post('/api/users')
def add_user(body:UserCreate,u=Depends(user)):
    require(u,['administrator'])
    if body.role not in ['applicant','credit_officer','approver','administrator']: raise HTTPException(422,'Invalid role')
    if db.one('SELECT id FROM users WHERE username=?',(body.username,)): raise HTTPException(409,'Username already exists')
    uid=db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)',
       (body.username,db.password_hash(body.password),body.role,db.now()))
    db.audit(u['id'],None,'user_created',after={'id':uid,'username':body.username,'role':body.role})
    return {'id':uid}

@app.get('/api/applicants')
def applicants(u=Depends(user)):
    rows=db.all_rows('SELECT a.*,b.owner_id FROM applications a JOIN businesses b ON b.id=a.business_id ORDER BY a.id DESC')
    out=[]
    for r in rows:
        if u['role']=='applicant' and r['owner_id']!=u['id']: continue
        rec=json.loads(r['record']); a=json.loads(db.one('SELECT result FROM assessments WHERE id=?',(r['current_assessment_id'],))['result'])
        out.append({'id':r['id'],'name':rec['name'],'sector':rec['sector'],'city':rec['city'],
         'loan_amount':rec['loan_amount'],**a['assessment'],'thin_file':rec.get('has_credit_history') is False,
         'status':r['status'],'provisional':a['provisional'],'is_demo':bool(r['is_demo'])})
    return out

@app.get('/api/applicants/{aid}')
def applicant(aid:int,u=Depends(user)): return detailed(aid,u)

@app.post('/api/applicants')
def new_applicant(body:ApplicationInput,u=Depends(user)):
    aid=create_application(body.model_dump(),u['id'])
    return detailed(aid,u)

@app.put('/api/applicants/{aid}')
def revise(aid:int,body:ApplicationInput,u=Depends(user)):
    row=application(aid,u)
    if row['status'] in ['approved','disbursed']: raise HTTPException(409,'Approved applications cannot be edited; create a new application')
    rec=body.model_dump(); old=json.loads(row['record']); evidence=json.loads(row['evidence'])
    for k in ['monthly_revenue','loan_amount']+RISK_FIELDS+FINANCE_FIELDS:
        if old.get(k)!=rec.get(k): evidence[k]={'source':'user_entered' if rec.get(k) is not None else 'missing','verification':'unverified','document_id':None}
    result=evaluate(rec,evidence)
    db.execute('UPDATE applications SET record=?,evidence=?,status=? WHERE id=?',(json.dumps(rec),json.dumps(evidence),result['suggested_workflow'],aid))
    db.execute('UPDATE businesses SET name=?,sector=?,city=? WHERE id=?',(rec['name'],rec['sector'],rec['city'],row['business_id']))
    sid,result=save_assessment(aid,rec,evidence)
    db.audit(u['id'],aid,'application_revised',before=old,after={'record':rec,'assessment_id':sid})
    return detailed(aid,u)

@app.post('/api/score-record')
def score_record(body:ApplicationInput,u=Depends(user)): return evaluate(body.model_dump())

@app.post('/api/upload-csv')
async def upload_csv(file:UploadFile=File(...),u=Depends(user)):
    data=await file.read(5*1024*1024+1)
    if len(data)>5*1024*1024: raise HTTPException(413,'CSV limit is 5 MB')
    try:
        reader=csv.DictReader(io.StringIO(data.decode('utf-8-sig')),strict=True)
        headers=reader.fieldnames or []
        if len(headers)!=len(set(headers)): raise HTTPException(422,'Duplicate CSV column names')
        missing=[k for k in ['monthly_revenue','loan_amount'] if k not in headers]
        if missing: raise HTTPException(422,'Missing columns: '+', '.join(missing))
        unknown=[k for k in headers if k not in ApplicationInput.model_fields]
        if unknown: raise HTTPException(422,'Unknown columns: '+', '.join(unknown))
        rows=list(reader)
    except (UnicodeError,csv.Error): raise HTTPException(422,'Use a valid UTF-8 comma-separated CSV')
    if not rows: raise HTTPException(422,'CSV has no business rows')
    if len(rows)>1000: raise HTTPException(413,'Maximum 1,000 rows per import')
    saved,errors=[],[]
    for number,row in enumerate(rows,start=2):
        if None in row or any(v is None for v in row.values()):
            errors.append({'row':number,'field':'csv','message':'Row has a different column count from the header','original':row}); continue
        payload={k:v.strip() for k,v in row.items() if v.strip()}
        try:
            rec=ApplicationInput.model_validate(payload).model_dump()
            aid=create_application(rec,u['id'],'csv_import')
            d=detailed(aid,u)
            saved.append({'row':number,'id':aid,'name':rec['name'],**d['assessment'],
               'status':d['status'],'provisional':d['provisional'],'top_factor':d['assessment']['factors'][0]['label']})
        except ValidationError as e:
            for err in e.errors(): errors.append({'row':number,'field':'.'.join(str(k) for k in err['loc']),
                'message':err['msg'],'original':row})
    db.audit(u['id'],None,'csv_import',after={'saved':len(saved),'errors':len(errors)})
    return {'saved':saved,'errors':errors}

@app.post('/api/whatif')
def whatif(body:ScenarioInput,u=Depends(user)):
    row=application(body.id,u)
    if body.base_assessment_id is not None and body.base_assessment_id!=row['current_assessment_id']:
        raise HTTPException(409,'Assessment changed. Reload the application.')
    unknown=set(body.overrides)-set(NUMERIC_FIELDS)
    if unknown: raise HTTPException(422,'Unsupported scenario fields: '+', '.join(sorted(unknown)))
    rec={**json.loads(row['record']),**body.overrides}
    try: rec=ApplicationInput.model_validate(rec).model_dump()
    except ValidationError as e: raise HTTPException(422,[{'loc':list(x['loc']),'msg':x['msg']} for x in e.errors()])
    evidence=json.loads(row['evidence'])
    for k in body.overrides: evidence[k]={'source':'assumed_for_simulation','verification':'unverified','document_id':None}
    result=evaluate(rec,evidence)
    sid=db.execute('INSERT INTO scenarios(application_id,base_assessment_id,overrides,snapshot,result,created_by,created_at) VALUES (?,?,?,?,?,?,?)',
       (body.id,row['current_assessment_id'],json.dumps(body.overrides),json.dumps(rec),json.dumps(result),u['id'],db.now()))
    db.audit(u['id'],body.id,'scenario_created',after={'scenario_id':sid,'overrides':body.overrides})
    return {**result,'id':body.id,'scenario_id':sid,'base_assessment_id':row['current_assessment_id'],'overrides':body.overrides}

@app.post('/api/applicants/{aid}/scenarios/{sid}/apply')
def apply_scenario(aid:int,sid:int,u=Depends(user)):
    row=application(aid,u)
    scenario=db.one('SELECT * FROM scenarios WHERE id=? AND application_id=?',(sid,aid))
    if not scenario: raise HTTPException(404,'Unknown scenario')
    if scenario['base_assessment_id']!=row['current_assessment_id']: raise HTTPException(409,'Scenario is stale; recalculate it')
    if row['status'] in ['approved','disbursed']: raise HTTPException(409,'Approved applications cannot be revised')
    rec=ApplicationInput.model_validate(json.loads(scenario['snapshot']))
    return revise(aid,rec,u)

class CommitteeInput(BaseModel):
    id:int
    scenario_id:int|None=None

@app.post('/api/committee')
def committee(body:CommitteeInput,u=Depends(user)):
    row=application(body.id,u)
    scenario=None
    if body.scenario_id:
        scenario=db.one('SELECT * FROM scenarios WHERE id=? AND application_id=?',(body.scenario_id,body.id))
        if not scenario: raise HTTPException(404,'Unknown scenario')
        if scenario['base_assessment_id']!=row['current_assessment_id']: raise HTTPException(409,'Scenario is stale')
        d=json.loads(scenario['result'])
    else: d=detailed(body.id,u)
    rec=d['effective_record']
    payload={'business':{k:rec[k] for k in ['name','sector','city']},
      'request':{k:rec[k] for k in ['loan_amount','monthly_revenue']},'has_formal_credit_history':rec.get('has_credit_history'),
      'declared_vs_observed_revenue_gap':rec['revenue_gap'],'assessment':d['assessment'],'path_to_approval':d['path'],
      'affordability':d['affordability'],'warnings':d['warnings'],'is_scenario':bool(scenario)}
    result=llm.committee(payload)
    result.update({'created_at':db.now(),'model':llm.MODEL if result['source']=='claude' else None,
       'scenario_id':body.scenario_id,'assessment_id':row['current_assessment_id']})
    rid=db.execute('INSERT INTO committee_reviews(application_id,assessment_id,scenario_id,result,created_at) VALUES (?,?,?,?,?)',
       (body.id,row['current_assessment_id'],body.scenario_id,json.dumps(result),db.now()))
    db.audit(u['id'],body.id,'committee_review',after={'review_id':rid,'source':result['source'],'scenario_id':body.scenario_id})
    return result

@app.post('/api/applicants/{aid}/decision')
def decision(aid:int,body:DecisionInput,u=Depends(user)):
    require(u,['approver','administrator','credit_officer'])
    row=application(aid,u)
    allowed=['approved','approved_with_conditions','needs_information','declined']
    if body.decision not in allowed: raise HTTPException(422,'Invalid human decision')
    if body.decision in ['approved','approved_with_conditions']: require(u,['approver','administrator'])
    if row['status']=='disbursed': raise HTTPException(409,'Disbursed application cannot be re-decided')
    if body.assessment_id!=row['current_assessment_id']: raise HTTPException(409,'Assessment changed. Review the latest assessment.')
    d=detailed(aid,u)
    if body.decision=='approved':
        verified={doc['kind'] for doc in d['documents'] if doc['status']=='verified'}
        if not {'identity','business','bank_account','revenue'}.issubset(verified): raise HTTPException(422,'Verify identity, business, bank ownership and revenue documents before final approval')
        if d['assumed_fields'] or d['affordability']['status']!='pass': raise HTTPException(422,'Final approval requires complete inputs and a passing affordability check')
        if any(d['evidence'].get(k,{}).get('verification')!='verified' for k in ['monthly_revenue']+RISK_FIELDS+FINANCE_FIELDS):
            raise HTTPException(422,'Verify all scoring and affordability evidence before final approval')
        if d.get('consistency_errors'): raise HTTPException(422,'Reconcile inconsistent debt ratio and repayment inputs before final approval')
    if body.decision=='approved_with_conditions' and not body.conditions: raise HTTPException(422,'Add at least one condition')
    hid=db.execute('INSERT INTO human_decisions(application_id,assessment_id,reviewer_id,decision,reason,conditions,created_at) VALUES (?,?,?,?,?,?,?)',
       (aid,body.assessment_id,u['id'],body.decision,body.reason,json.dumps(body.conditions),db.now()))
    db.execute('UPDATE applications SET status=?,updated_at=? WHERE id=?',(body.decision,db.now(),aid))
    db.audit(u['id'],aid,'human_decision',before={'status':row['status']},after={'decision_id':hid,**body.model_dump()})
    return detailed(aid,u)

@app.post('/api/applicants/{aid}/disburse')
def disburse(aid:int,u=Depends(user)):
    require(u,['approver','administrator'])
    row=application(aid,u)
    if row['status']!='approved': raise HTTPException(422,'Only fully approved applications can be marked disbursed')
    db.execute('UPDATE applications SET status=?,disbursed_at=?,updated_at=? WHERE id=?',('disbursed',date.today().isoformat(),db.now(),aid))
    db.audit(u['id'],aid,'disbursement_recorded',after={'note':'Workflow record only; no funds transferred'})
    return detailed(aid,u)

MAX_DOCUMENT=10*1024*1024
KINDS=['identity','business','bank_account','revenue','utility','ledger','other']
async def document_bytes(file):
    data=await file.read(MAX_DOCUMENT+1)
    if len(data)>MAX_DOCUMENT: raise HTTPException(413,'Document limit is 10 MB')
    mime=None
    if data.startswith(b'%PDF-'): mime='application/pdf'
    elif data.startswith(b'\xff\xd8\xff'): mime='image/jpeg'
    elif data.startswith(b'\x89PNG\r\n\x1a\n'): mime='image/png'
    elif data[:4]==b'RIFF' and data[8:12]==b'WEBP': mime='image/webp'
    if not mime: raise HTTPException(415,'Use PDF, JPEG, PNG or WebP with valid file content')
    return data,mime

@app.post('/api/applicants/{aid}/documents')
async def upload_document(aid:int,file:UploadFile=File(...),kind:str=Form(...),u=Depends(user)):
    application(aid,u)
    if kind not in KINDS: raise HTTPException(422,'Invalid document kind')
    data,mime=await document_bytes(file)
    path=db.ROOT/'documents'/secrets.token_hex(24)
    path.write_bytes(data)
    did=db.execute('INSERT INTO documents(application_id,kind,original_name,path,mime,sha256,created_at) VALUES (?,?,?,?,?,?,?)',
       (aid,kind,Path(file.filename or 'document').name,str(path),mime,hashlib.sha256(data).hexdigest(),db.now()))
    db.audit(u['id'],aid,'document_uploaded',after={'document_id':did,'kind':kind})
    return clean_doc(db.one('SELECT * FROM documents WHERE id=?',(did,)))

@app.get('/api/documents/{did}')
def download_document(did:int,u=Depends(user)):
    doc=db.one('SELECT * FROM documents WHERE id=?',(did,))
    if not doc: raise HTTPException(404,'Unknown document')
    application(doc['application_id'],u)
    db.audit(u['id'],doc['application_id'],'document_downloaded',after={'document_id':did})
    return FileResponse(doc['path'],media_type=doc['mime'],filename=doc['original_name'],content_disposition_type='attachment')

@app.post('/api/documents/{did}/verify')
def verify_document(did:int,body:VerificationInput,u=Depends(user)):
    require(u,['credit_officer','approver','administrator'])
    doc=db.one('SELECT * FROM documents WHERE id=?',(did,))
    if not doc: raise HTTPException(404,'Unknown document')
    row=application(doc['application_id'],u)
    if row['status'] in ['approved','disbursed']: raise HTTPException(409,'Evidence is locked after final approval; request a new application')
    if body.status not in ['verified','rejected','unverified']: raise HTTPException(422,'Invalid verification status')
    if set(body.fields)-set(['monthly_revenue','loan_amount']+RISK_FIELDS+FINANCE_FIELDS): raise HTTPException(422,'Invalid evidence field')
    rec=json.loads(row['record']); evidence=json.loads(row['evidence'])
    if any(rec.get(k) is None for k in body.fields): raise HTTPException(422,'Cannot verify a missing field')
    # Invalidate all previous links when a document is rejected or verification is withdrawn.
    if body.status!='verified':
        for k,v in evidence.items():
            if v.get('document_id')==did: v['verification']='unverified'
    for k in body.fields:
        evidence[k]={'source':'human_verified_document' if body.status=='verified' else 'document',
         'verification':body.status,'document_id':did}
    db.execute('UPDATE documents SET status=?,note=?,reviewed_by=? WHERE id=?',(body.status,body.note,u['id'],did))
    db.execute('UPDATE applications SET evidence=?,status=? WHERE id=?',(json.dumps(evidence),'under_review',row['id']))
    save_assessment(row['id'],rec,evidence)
    db.audit(u['id'],row['id'],'document_reviewed',before={'status':doc['status']},after={'document_id':did,**body.model_dump()})
    return detailed(row['id'],u)

@app.post('/api/extract')
async def extract(file:UploadFile=File(...),u=Depends(user)):
    data,mime=await document_bytes(file)
    if mime=='application/pdf': raise HTTPException(415,'Ledger extraction currently needs a JPEG, PNG or WebP image')
    try: result=llm.extract_from_image(data,mime)
    except Exception:
        logger.exception('Ledger extraction failed')
        raise HTTPException(503,'Ledger extraction unavailable. Configure ANTHROPIC_API_KEY and CLAUDE_MODEL, or enter figures manually.')
    path=db.ROOT/'documents'/secrets.token_hex(24)
    path.write_bytes(data)
    draft_id=db.execute('INSERT INTO extraction_drafts(owner_id,result,path,mime,original_name,created_at) VALUES (?,?,?,?,?,?)',
      (u['id'],json.dumps(result),str(path),mime,Path(file.filename or 'ledger').name,db.now()))
    db.audit(u['id'],None,'ledger_extracted',after={'months_read':result.get('months_read'),'extraction_id':draft_id})
    return {**result,'extraction_id':draft_id}

@app.get('/api/applicants/{aid}/audit')
def audit(aid:int,u=Depends(user)):
    require(u,['credit_officer','approver','administrator']); application(aid,u)
    return db.all_rows('SELECT e.*,u.username AS actor FROM audit_events e LEFT JOIN users u ON e.actor_id=u.id WHERE application_id=? ORDER BY e.id DESC',(aid,))

@app.get('/api/applicants/{aid}/report')
def report(aid:int,scenario_id:int|None=None,u=Depends(user)):
    base=detailed(aid,u)
    scenario=None
    if scenario_id:
        s=db.one('SELECT * FROM scenarios WHERE id=? AND application_id=?',(scenario_id,aid))
        if not s: raise HTTPException(404,'Unknown scenario')
        scenario={**s,'overrides':json.loads(s['overrides']),'snapshot':json.loads(s['snapshot']),'result':json.loads(s['result'])}
    history=[dict(r,snapshot=json.loads(r['snapshot']),result=json.loads(r['result'])) for r in db.all_rows('SELECT * FROM assessments WHERE application_id=? ORDER BY id DESC',(aid,))]
    return {'version':'4.0','generated_at':db.now(),'application':base,'active_scenario':scenario,'assessment_history':history,
      'notice':'Recommendations and simulated scenarios are not bank approvals; disbursement status records no actual transfer.'}

@app.get('/api/settings')
def settings(u=Depends(user)):
    return {'dscr_threshold':float(db.one("SELECT value FROM settings WHERE key='dscr_threshold'")['value']),
      'model_version':MODEL_VERSION,'model_kind':MODEL_KIND,'model_report':MODEL_REPORT,
      'default_target':'90+ days past due within 12 months of disbursement (real-data tool only)',
      'formulas':{'existing_debt_ratio':'existing monthly debt payments / monthly revenue',
       'cash_reserve_months':'cash available / average monthly operating costs',
       'revenue_gap':'abs(declared monthly revenue - observed monthly revenue) / declared monthly revenue',
       'revenue_growth_6m':'sum(last 3 complete monthly sales) / sum(previous 3 complete monthly sales) - 1'}}

class PolicyInput(BaseModel):
    dscr_threshold:float=Field(ge=1,le=5,allow_inf_nan=False)

@app.post('/api/settings')
def set_policy(body:PolicyInput,u=Depends(user)):
    require(u,['administrator'])
    before=settings(u)
    db.execute("UPDATE settings SET value=? WHERE key='dscr_threshold'",(str(body.dscr_threshold),))
    # Refresh current assessments and invalidate prior scenario bases under the new policy.
    for row in db.all_rows("SELECT * FROM applications WHERE status NOT IN ('approved','approved_with_conditions','disbursed')"):
        save_assessment(row['id'],json.loads(row['record']),json.loads(row['evidence']))
    db.audit(u['id'],None,'policy_updated',before=before,after=body.model_dump())
    return settings(u)

@app.get('/api/impact')
def impact(u=Depends(user)):
    require(u,['credit_officer','approver','administrator'])
    # Historical synthetic benchmark remains explicitly separate from the active model.
    from service import train
    from model import CreditModel
    benchmark=CreditModel().fit(train)
    records=test.to_dict('records'); trad=[]; ai=[]; extra=0
    for r in records:
        t=r['has_credit_history'] and r['existing_debt_ratio']<.35 and r['loan_amount']/(12*r['monthly_revenue'])<.6
        a=benchmark.assess(r)['decision']=='approve'
        if t: trad.append(r)
        if a: ai.append(r)
        if a and not t: extra+=r['loan_amount']
    rate=lambda rows:round(100*sum(r['defaulted'] for r in rows)/len(rows),1) if rows else None
    return {'sample_size':300,'model_auc':round(benchmark.auc(test),3),'ai_approved':len(ai),'traditional_approved':len(trad),
     'thin_file_approved_by_ai':sum(not r['has_credit_history'] for r in ai),'ai_default_rate':rate(ai),'traditional_default_rate':rate(trad),
     'extra_loan_volume_pkr':extra,'estimated_jobs_supported':int(extra//1000000),
     'assumptions':'Synthetic benchmark only. Assumes one job per Rs. 1 million and minutes vs 14 days; neither is measured.'}

@app.post('/api/applicants/{aid}/repayments')
def repayment(aid:int,body:RepaymentInput,u=Depends(user)):
    require(u,['credit_officer','approver','administrator']); row=application(aid,u)
    if not row['disbursed_at']: raise HTTPException(422,'Record disbursement first')
    try:
        due=date.fromisoformat(body.due_date); paid=date.fromisoformat(body.paid_date) if body.paid_date else None
    except ValueError: raise HTTPException(422,'Dates must use YYYY-MM-DD')
    if due<date.fromisoformat(row['disbursed_at']): raise HTTPException(422,'Due date cannot precede disbursement')
    if paid and paid>date.today(): raise HTTPException(422,'Paid date cannot be in the future')
    if body.amount_paid>0 and not paid: raise HTTPException(422,'Supply a paid date for recorded payments')
    if paid and body.amount_paid<body.amount_due: raise HTTPException(422,'Paid date must indicate full settlement; for partial outstanding payments leave paid date blank and amount_paid=0 until settled')
    db.execute('INSERT INTO repayments(application_id,due_date,paid_date,amount_due,amount_paid,created_at) VALUES (?,?,?,?,?,?) ON CONFLICT(application_id,due_date) DO UPDATE SET paid_date=excluded.paid_date,amount_due=excluded.amount_due,amount_paid=excluded.amount_paid',
      (aid,body.due_date,body.paid_date,body.amount_due,body.amount_paid,db.now()))
    db.audit(u['id'],aid,'repayment_recorded',after=body.model_dump())
    return {'ok':True}

@app.get('/api/applicants/{aid}/repayments')
def repayments(aid:int,u=Depends(user)):
    application(aid,u)
    return db.all_rows('SELECT * FROM repayments WHERE application_id=? ORDER BY due_date',(aid,))

@app.get('/api/training-export')
def training_export(u=Depends(user)):
    require(u,['administrator'])
    records=[]
    for row in db.all_rows('SELECT * FROM applications WHERE disbursed_at IS NOT NULL AND is_demo=0'):
        # Last decision's reviewed snapshot, rather than later updates.
        decision=db.one("SELECT * FROM human_decisions WHERE application_id=? AND decision='approved' ORDER BY id DESC LIMIT 1",(row['id'],))
        if not decision: continue
        a=db.one('SELECT * FROM assessments WHERE id=?',(decision['assessment_id'],))
        records.append({'business_id':row['business_id'],'application_id':row['id'],'disbursed_at':row['disbursed_at'],
          'snapshot_at':a['created_at'],**json.loads(a['snapshot'])})
    payments=db.all_rows('SELECT r.* FROM repayments r JOIN applications a ON r.application_id=a.id WHERE a.is_demo=0')
    return {'applications':records,'repayments':payments,'as_of':date.today().isoformat(),
      'notice':'Export does not certify repayment coverage. Training tool requires an explicit data-completeness confirmation.'}

@app.post('/api/applicants/{aid}/ledger-draft/{draft_id}')
def confirm_ledger(aid:int,draft_id:int,u=Depends(user)):
    row=application(aid,u)
    draft=db.one('SELECT * FROM extraction_drafts WHERE id=? AND owner_id=?',(draft_id,u['id']))
    if not draft: raise HTTPException(404,'Unknown extraction draft')
    if row['status'] in ['approved','disbursed']: raise HTTPException(409,'Application is locked')
    extracted=json.loads(draft['result']);record=json.loads(row['record']);evidence=json.loads(row['evidence'])
    path=Path(draft['path'])
    did=db.execute('INSERT INTO documents(application_id,kind,original_name,path,mime,sha256,created_at) VALUES (?,?,?,?,?,?,?)',
      (aid,'ledger',draft['original_name'],str(path),draft['mime'],hashlib.sha256(path.read_bytes()).hexdigest(),db.now()))
    for k in ['monthly_revenue']+RISK_FIELDS:
        if k in extracted and record.get(k) is not None:
            evidence[k]={'source':'extracted_from_document' if record[k]==extracted[k] else 'user_corrected_extraction',
              'verification':'unverified','document_id':did}
    db.execute('UPDATE applications SET evidence=? WHERE id=?',(json.dumps(evidence),aid))
    save_assessment(aid,record,evidence)
    db.execute('DELETE FROM extraction_drafts WHERE id=?',(draft_id,))
    db.audit(u['id'],aid,'ledger_confirmed',after={'document_id':did,'extraction_id':draft_id})
    return detailed(aid,u)

@app.get('/api/audit')
def workspace_audit(u=Depends(user)):
    require(u,['administrator'])
    return db.all_rows('SELECT e.*,u.username AS actor FROM audit_events e LEFT JOIN users u ON e.actor_id=u.id ORDER BY e.id DESC LIMIT 500')
