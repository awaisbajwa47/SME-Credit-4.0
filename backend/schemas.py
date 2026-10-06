"""One validation contract for forms, CSVs, extraction drafts and scenarios."""
import math
from pydantic import BaseModel, Field, ConfigDict, field_validator

RISK_FIELDS = ['business_age_years','revenue_growth_6m','revenue_volatility','cash_reserve_months',
 'existing_debt_ratio','digital_payment_share','utility_on_time_rate','customer_concentration','revenue_gap']
FINANCE_FIELDS = ['monthly_cash_collections','operating_costs','monthly_taxes','owner_withdrawals',
 'existing_monthly_repayments','annual_rate','tenure_months']
DEFAULTS = dict(business_age_years=3,revenue_growth_6m=0,revenue_volatility=.3,cash_reserve_months=1,
 existing_debt_ratio=.2,digital_payment_share=.3,utility_on_time_rate=.9,customer_concentration=.3,revenue_gap=.05)
NUMERIC_FIELDS = ['monthly_revenue','loan_amount'] + RISK_FIELDS + FINANCE_FIELDS

class ApplicationInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(default='Unnamed business', min_length=1, max_length=160)
    sector: str = Field(default='Other', min_length=1, max_length=80)
    city: str = Field(default='Not supplied', min_length=1, max_length=80)
    purpose: str = Field(default='', max_length=500)
    monthly_revenue: float = Field(ge=1, le=1e12)
    loan_amount: float = Field(ge=1, le=1e12)
    business_age_years: float | None = Field(default=None, ge=0, le=200)
    revenue_growth_6m: float | None = Field(default=None, ge=-1, le=100)
    revenue_volatility: float | None = Field(default=None, ge=0, le=100)
    cash_reserve_months: float | None = Field(default=None, ge=0, le=1000)
    existing_debt_ratio: float | None = Field(default=None, ge=0, le=100)
    digital_payment_share: float | None = Field(default=None, ge=0, le=1)
    utility_on_time_rate: float | None = Field(default=None, ge=0, le=1)
    customer_concentration: float | None = Field(default=None, ge=0, le=1)
    # abs(declared monthly revenue - observed monthly revenue) / declared monthly revenue
    revenue_gap: float | None = Field(default=None, ge=0, le=100)
    monthly_cash_collections: float | None = Field(default=None, ge=0, le=1e12)
    operating_costs: float | None = Field(default=None, ge=0, le=1e12)
    monthly_taxes: float | None = Field(default=None, ge=0, le=1e12)
    owner_withdrawals: float | None = Field(default=None, ge=0, le=1e12)
    existing_monthly_repayments: float | None = Field(default=None, ge=0, le=1e12)
    annual_rate: float | None = Field(default=None, ge=0, le=5)
    tenure_months: int | None = Field(default=None, ge=1, le=360)
    has_credit_history: bool | None = None

    @field_validator(*NUMERIC_FIELDS, mode='before')
    @classmethod
    def no_boolean_numbers(cls,value):
        if isinstance(value,bool):
            raise ValueError('A numeric value is required, not a boolean')
        return value

    @field_validator('name','sector','city')
    @classmethod
    def meaningful_text(cls, value):
        if not value.strip():
            raise ValueError('Must not be blank')
        return value.strip()

class Login(BaseModel):
    username: str = Field(min_length=3,max_length=80)
    password: str = Field(min_length=10,max_length=200)

class Setup(Login):
    setup_token: str

class UserCreate(Login):
    role: str

class ScenarioInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: int
    overrides: dict = Field(default_factory=dict)
    base_assessment_id: int | None = None

class DecisionInput(BaseModel):
    assessment_id: int
    decision: str
    reason: str = Field(min_length=10,max_length=3000)
    conditions: list[str] = Field(default_factory=list,max_length=30)

class VerificationInput(BaseModel):
    status: str
    note: str = Field(min_length=5,max_length=2000)
    fields: list[str] = Field(default_factory=list)

class RepaymentInput(BaseModel):
    due_date: str
    paid_date: str | None = None
    amount_due: float = Field(gt=0,allow_inf_nan=False)
    amount_paid: float = Field(default=0,ge=0,allow_inf_nan=False)
