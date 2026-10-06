import sys
from pathlib import Path
from datetime import date
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_gen import generate
from train_real import prepare,train_real,chronological_split


def real_fixture(n=100):
    # Synthetic fixtures only test the training tool; they are never shipped as real data.
    f=generate(n).drop(columns=['defaulted'])
    f['application_id']=range(1,n+1);f['business_id']=range(1,n+1)
    dates=pd.date_range('2020-01-01',periods=n,freq='14D')
    f['disbursed_at']=[str(x.date()) for x in dates];f['snapshot_at']=f.disbursed_at
    pay=[]
    for i,d in enumerate(dates,1):
        due=(d+pd.Timedelta(days=30)).date()
        paid=(d+pd.Timedelta(days=35 if i%3 else 140)).date()
        pay.append({'application_id':i,'due_date':str(due),'paid_date':str(paid),'amount_due':1000,'amount_paid':1000})
    return f,pd.DataFrame(pay)


def test_outcome_target_and_maturity():
    f,p=real_fixture(4)
    frame,excluded=prepare(f,p,date(2023,1,1))
    assert frame.defaulted.tolist()==[0,0,1,0]
    f.loc[3,'disbursed_at']='2025-12-01';f.loc[3,'snapshot_at']='2025-12-01'
    frame,excluded=prepare(f,p,date(2026,2,1));assert len(frame)==3 and len(excluded)==1


def test_leakage_and_incomplete_inputs():
    f,p=real_fixture(4);f.loc[0,'snapshot_at']='2024-01-01'
    with pytest.raises(ValueError,match='leakage'):prepare(f,p,date(2026,1,1))
    f,p=real_fixture(4);f.loc[0,'cash_reserve_months']=None
    with pytest.raises(ValueError,match='incomplete scoring'):prepare(f,p,date(2026,1,1))


def test_training_artifact_and_temporal_split(tmp_path):
    f,p=real_fixture();f.to_csv(tmp_path/'apps.csv',index=False);p.to_csv(tmp_path/'payments.csv',index=False)
    with pytest.raises(ValueError,match='Confirm'):train_real(tmp_path/'apps.csv',tmp_path/'payments.csv',tmp_path/'output',date(2026,1,1))
    r=train_real(tmp_path/'apps.csv',tmp_path/'payments.csv',tmp_path/'output',date(2026,1,1),True)
    assert 0<=r['auc']<=1 and r['calibration_bins'] and r['sector_metrics']
    assert (tmp_path/'output'/'credit-model.joblib').exists()
    frame,_=prepare(f,p,date(2026,1,1));tr,te,_,_=chronological_split(frame)
    assert tr.disbursed_at.max()<te.disbursed_at.min()
    assert not set(tr.business_id)&set(te.business_id)
