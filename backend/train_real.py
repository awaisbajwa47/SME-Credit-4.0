"""Offline training from reviewed decision-time snapshots and complete repayment records.
No uploads are deserialized. Activation requires a trusted local artifact path.
"""
import argparse, json, hashlib, calendar
from pathlib import Path
from datetime import date
import numpy as np
import pandas as pd
import joblib
from sklearn.metrics import roc_auc_score,brier_score_loss,log_loss
from sklearn.calibration import calibration_curve
from schemas import ApplicationInput,RISK_FIELDS,NUMERIC_FIELDS
from model import CreditModel,FEATURES,to_features


def horizon_end(value):
    # Calendar 12-month anniversary, including leap-day handling.
    return value.replace(year=value.year+1,day=min(value.day,calendar.monthrange(value.year+1,value.month)[1]))


def prepare(apps,payments,as_of):
    required={'application_id','business_id','disbursed_at','snapshot_at','monthly_revenue','loan_amount',*RISK_FIELDS}
    if not required.issubset(apps.columns): raise ValueError('Missing application columns: '+', '.join(sorted(required-set(apps.columns))))
    if apps['application_id'].duplicated().any(): raise ValueError('Duplicate application IDs')
    if not {'application_id','due_date','paid_date','amount_due','amount_paid'}.issubset(payments.columns): raise ValueError('Missing payment columns')
    if payments.duplicated(['application_id','due_date']).any(): raise ValueError('Duplicate payment due dates for an application')
    if set(payments['application_id'])-set(apps['application_id']): raise ValueError('Repayments reference an unknown application')
    rows=[];excluded=[]
    for _,row in apps.iterrows():
        aid=int(row['application_id']);start=date.fromisoformat(str(row['disbursed_at'])[:10]);end=horizon_end(start)
        snapshot=date.fromisoformat(str(row['snapshot_at'])[:10])
        if snapshot>start: raise ValueError(f'Application {aid}: snapshot is after disbursement (leakage)')
        if row.get('is_demo',False) in [True,1,'true','True']: raise ValueError('Synthetic demo records are not allowed')
        if start>as_of: raise ValueError('Disbursement is in the future')
        # Use only complete, fully observed horizons for both classes.
        if end>as_of:
            excluded.append({'application_id':aid,'reason':'12-month outcome horizon incomplete'});continue
        payload={k:row[k] for k in ApplicationInput.model_fields if k in row and pd.notna(row[k])}
        record=ApplicationInput.model_validate(payload).model_dump()
        if any(record[k] is None for k in RISK_FIELDS): raise ValueError(f'Application {aid}: incomplete scoring snapshot')
        rp=payments[payments.application_id==row['application_id']]
        if rp.empty: raise ValueError(f'Application {aid}: no repayment schedule/outcomes')
        target=False
        for _,p in rp.iterrows():
            due=date.fromisoformat(str(p['due_date'])[:10])
            if due<start: raise ValueError(f'Application {aid}: repayment due before disbursement')
            amount_due=float(p['amount_due']);amount_paid=float(p['amount_paid'])
            if not np.isfinite(amount_due) or not np.isfinite(amount_paid) or amount_due<=0 or amount_paid<0:
                raise ValueError('Invalid payment amounts')
            paid=date.fromisoformat(str(p['paid_date'])[:10]) if pd.notna(p['paid_date']) and str(p['paid_date']).strip() else None
            if paid and paid>as_of: raise ValueError('Settlement date is after observation cutoff')
            if paid and amount_paid<amount_due: raise ValueError('Paid date must be a full settlement date')
            if not paid and amount_paid>0: raise ValueError('Partial-payment allocation unsupported; supply full settlement records')
            if due>end: continue
            observed=min(paid,end) if paid else end
            if (observed-due).days>=90: target=True
        rows.append({**record,'application_id':aid,'business_id':row['business_id'],
                     'disbursed_at':start.isoformat(),'defaulted':int(target)})
    if not rows: raise ValueError('No complete mature outcomes')
    return pd.DataFrame(rows),excluded


def chronological_split(frame):
    # A business appearing on both sides is excluded from later testing.
    ordered=frame.sort_values(['disbursed_at','application_id']).reset_index(drop=True)
    if len(ordered)<30: raise ValueError('At least 30 mature applications required for this training tool; substantially more is needed for reliable validation')
    split=int(len(ordered)*.7)
    cutoff=ordered.iloc[split]['disbursed_at']
    train=ordered[ordered.disbursed_at<cutoff].copy()
    test=ordered[ordered.disbursed_at>=cutoff].copy()
    before=len(test);test=test[~test.business_id.isin(set(train.business_id))]
    if len(train)<15 or len(test)<10 or train.defaulted.nunique()<2 or test.defaulted.nunique()<2:
        raise ValueError('Need sufficient earlier/later records and both outcome classes in each split, with no business overlap')
    return train,test,cutoff,before-len(test)


def metrics(m,df):
    probs=np.array([m.predict_pd(r) for r in df.to_dict('records')])
    y=df.defaulted.to_numpy(dtype=int)
    return {'sample_size':len(df),'defaults':int(y.sum()),'auc':float(roc_auc_score(y,probs)) if len(set(y))>1 else None,
      'brier_loss':float(brier_score_loss(y,probs)),'log_loss':float(log_loss(y,probs,labels=[0,1])),
      'mean_predicted_pd':float(probs.mean()),'observed_default_rate':float(y.mean()),
      'approval_rate_at_15_percent':float((probs<=.15).mean())}


def train_real(applications,repayments,output,as_of,confirm=False):
    if not confirm: raise ValueError('Confirm that all scheduled instalments, including unpaid ones, and full settlement dates are included using --confirm-complete-outcomes')
    apps=pd.read_csv(applications);pay=pd.read_csv(repayments)
    frame,excluded=prepare(apps,pay,as_of)
    train,test,cutoff,overlap=chronological_split(frame)
    m=CreditModel().fit(train)
    probs=np.array([m.predict_pd(r) for r in test.to_dict('records')]);truth=test.defaulted.to_numpy(dtype=int)
    observed,predicted=calibration_curve(truth,probs,n_bins=5,strategy='quantile')
    sha=hashlib.sha256(Path(applications).read_bytes()+Path(repayments).read_bytes()).hexdigest()
    report={**metrics(m,test),'training_sample_size':len(train),'temporal_cutoff':cutoff,
      'business_overlap_test_records_excluded':overlap,'immature_records_excluded':excluded,
      'calibration_bins':[{'mean_predicted_pd':float(p),'observed_default_rate':float(y)} for p,y in zip(predicted,observed)],
      'sector_metrics':{str(s):metrics(m,group) for s,group in test.groupby('sector')},
      'default_horizon':'90+ days past due within 12 calendar months of disbursement',
      'as_of':as_of.isoformat(),'input_sha256':sha,'outcome_completeness_confirmed_by_operator':True,
      'warning':'Real-data research model. Requires independent validation; selection bias, data quality, calibration and fairness need review. No automatic certification.'}
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    version='real-logistic-v4-'+sha[:12]
    joblib.dump({'model':m,'features':FEATURES,'kind':'real','version':version,'report':report},output/'credit-model.joblib')
    (output/'validation-report.json').write_text(json.dumps(report,indent=2))
    frame.to_csv(output/'labelled-outcomes.csv',index=False)
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--applications',required=True);parser.add_argument('--repayments',required=True)
    parser.add_argument('--output',default='artifacts/real-model');parser.add_argument('--as-of',required=True)
    parser.add_argument('--confirm-complete-outcomes',action='store_true')
    args=parser.parse_args()
    report=train_real(args.applications,args.repayments,args.output,date.fromisoformat(args.as_of),args.confirm_complete_outcomes)
    print(json.dumps(report,indent=2))
