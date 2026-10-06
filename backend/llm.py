"""Claude-powered steps: the credit committee and ledger extraction.

The committee falls back to a labelled template without a working API key.
Ledger extraction requires live AI. Configure ANTHROPIC_API_KEY and CLAUDE_MODEL.
"""
import base64
import json
import os
import re
import logging
import math
from datetime import date

try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")


def _client():
    key = os.getenv("ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=key) if (key and anthropic) else None


def _parse_json(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)


COMMITTEE_SYSTEM = """You simulate a small SME credit committee in Pakistan with three members:
- underwriter: argues the strongest honest case FOR lending, citing the supplied numbers.
- skeptic: argues the strongest honest case AGAINST, citing the supplied numbers.
- compliance: checks data-quality and policy red flags (for example a revenue gap between declared and bank-observed).
Use ONLY the facts supplied. Never invent figures. Keep each member to 2 or 3 sentences.
Then give the committee verdict. Return ONLY JSON with this shape:
{"underwriter": str, "skeptic": str, "compliance": str,
 "verdict": {"decision": "approve"|"refer"|"decline", "conditions": [str], "summary": str}}"""


def committee(payload: dict) -> dict:
    client = _client()
    if client:
        try:
            msg = client.messages.create(
                model=MODEL, max_tokens=900, system=COMMITTEE_SYSTEM,
                messages=[{"role": "user", "content": json.dumps(payload)}])
            out = _parse_json(msg.content[0].text)
            if not isinstance(out,dict) or any(not isinstance(out.get(k),str) for k in ('underwriter','skeptic','compliance')):
                raise ValueError('Malformed committee response')
            verdict=out.get('verdict',{})
            if verdict.get('decision') not in ('approve','refer','decline') or not isinstance(verdict.get('summary'),str) or not isinstance(verdict.get('conditions'),list) or any(not isinstance(c,str) for c in verdict['conditions']):
                raise ValueError('Malformed committee verdict')
            out["source"] = "claude"
            return out
        except Exception as e:  # fall through to the offline version
            logging.getLogger("sme-credit").exception("Live committee failed")
            err = "AI unavailable"
    else:
        err = "no ANTHROPIC_API_KEY set"
    a = payload["assessment"]
    helps = [f["label"] for f in a["factors"] if f["impact_pts"] < 0][:2]
    hurts = [f["label"] for f in a["factors"] if f["impact_pts"] > 0][:2]
    return {
        "underwriter": f"Strengths: {', '.join(helps) or 'none stand out'}.",
        "skeptic": f"Concerns: {', '.join(hurts) or 'none stand out'}. Estimated default risk is {a['pd'] * 100:.0f}%.",
        "compliance": "Offline mode: no automated policy review was run.",
        "verdict": {"decision": a["decision"], "conditions": [], "summary": "Score-based decision (offline fallback)."},
        "source": "offline" if err == "no ANTHROPIC_API_KEY set" else "offline_after_error",
    }


EXTRACT_PROMPT = """This is a photo or scan of a small business's ledger, sales register, or bank/wallet statement.
Extract what you can read. Return ONLY JSON:
{"monthly_sales": [{"month": "YYYY-MM or null", "sales": number in PKR}, oldest first, up to 12],
 "digital_share_estimate": number 0-1 or null,
 "top_customer_share": number 0-1 or null,
 "notes": "one sentence on legibility or anything unclear"}
Use null when a field is not visible. Never guess numbers you cannot read."""


def extract_from_image(data: bytes, media_type: str) -> dict:
    client = _client()
    if not client:
        raise RuntimeError("Set ANTHROPIC_API_KEY to enable ledger extraction.")
    msg = client.messages.create(
        model=MODEL, max_tokens=800,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": base64.b64encode(data).decode()}},
            {"type": "text", "text": EXTRACT_PROMPT}]}])
    raw = _parse_json(msg.content[0].text)
    return features_from_ledger(raw)


def features_from_ledger(raw):
    """Zero months are retained; six-month growth requires consecutive dated months."""
    items=[]
    for item in raw.get('monthly_sales') or []:
        month=item.get('month') if isinstance(item,dict) else None
        value=item.get('sales') if isinstance(item,dict) else item
        if value is None:
            continue
        value=float(value)
        if not math.isfinite(value) or value<0:
            raise ValueError('Invalid ledger sales')
        if month is not None:
            date.fromisoformat(month+'-01')
        items.append({'month':month,'sales':value})
    if all(x['month'] for x in items): items.sort(key=lambda x:x['month'])
    items=items[-12:]
    sales=[x['sales'] for x in items]
    out={'notes':raw.get('notes'),'months_read':len(items),'monthly_sales':items,
         'source':'claude','requires_confirmation':True}
    if len(sales)>=3 and sum(sales)>0:
        mean=sum(sales)/len(sales)
        variance=sum((x-mean)**2 for x in sales)/len(sales)
        out['monthly_revenue']=round(mean,2)
        out['revenue_volatility']=round(math.sqrt(variance)/mean,4)
    if len(items)>=6 and all(x['month'] for x in items[-6:]):
        last=items[-6:]
        indexes=[int(x['month'][:4])*12+int(x['month'][5:7]) for x in last]
        if all(b-a==1 for a,b in zip(indexes,indexes[1:])) and sum(x['sales'] for x in last[:3])>0:
            out['revenue_growth_6m']=round(sum(x['sales'] for x in last[3:])/sum(x['sales'] for x in last[:3])-1,4)
    for source,target in [('digital_share_estimate','digital_payment_share'),('top_customer_share','customer_concentration')]:
        if raw.get(source) is not None:
            value=float(raw[source])
            if not math.isfinite(value) or not 0<=value<=1: raise ValueError('Invalid ledger share')
            out[target]=value
    return out
