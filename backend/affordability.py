"""Fixed-rate reducing-balance illustration, not a universal bank policy."""
import math
from schemas import FINANCE_FIELDS

def calculate(record, threshold=1.25):
    missing = [key for key in FINANCE_FIELDS if record.get(key) is None]
    if missing:
        return {'status':'unknown','missing':missing,'threshold':threshold,
                'method':'Fixed monthly payments on a reducing balance'}
    p, r, n = record['loan_amount'], record['annual_rate']/12, record['tenure_months']
    payment = p/n if r == 0 else p*r/(-math.expm1(-n*math.log1p(r)))
    collections = record['monthly_cash_collections']
    available = collections-record['operating_costs']-record['monthly_taxes']-record['owner_withdrawals']
    repayments = record['existing_monthly_repayments']+payment
    dscr = available/repayments
    stress = (available-.2*collections)/repayments
    return {'status':'pass' if dscr>=threshold else 'fail','threshold':threshold,
      'instalment':round(payment,2),'total_monthly_repayments':round(repayments,2),
      'available_cash':round(available,2),'cash_profit_before_withdrawals':round(available+record['owner_withdrawals'],2),
      'monthly_surplus':round(available-repayments,2),'dscr':round(dscr,4),'stress_dscr':round(stress,4),
      'stress_pass':stress>=threshold,'stress_sales_drop':.2,'annual_rate':record['annual_rate'],
      'tenure_months':n,'total_repaid':round(payment*n,2),
      'method':'Fixed monthly payments on a reducing balance; excludes fees, insurance and variable rates'}
