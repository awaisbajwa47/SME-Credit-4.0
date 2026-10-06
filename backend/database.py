"""Local SQLite store. Secrets and documents live outside the web public directory."""
import os, sqlite3, json, hashlib, secrets
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(os.getenv('SME_DATA_DIR', str(Path(__file__).parent/'data')))
ROOT.mkdir(parents=True,exist_ok=True)
(ROOT/'documents').mkdir(exist_ok=True)
DB = ROOT/'credit.sqlite3'

def now(): return datetime.now(timezone.utc).isoformat()
def connect():
    c=sqlite3.connect(DB,timeout=30)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def execute(sql,args=()):
    with connect() as c:
        cur=c.execute(sql,args)
        return cur.lastrowid

def one(sql,args=()):
    with connect() as c:
        row=c.execute(sql,args).fetchone()
        return dict(row) if row else None

def all_rows(sql,args=()):
    with connect() as c: return [dict(r) for r in c.execute(sql,args).fetchall()]

def init():
    with connect() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),expires REAL);
        CREATE TABLE IF NOT EXISTS businesses(id INTEGER PRIMARY KEY,name TEXT,sector TEXT,city TEXT,owner_id INTEGER REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY,business_id INTEGER REFERENCES businesses(id),record TEXT NOT NULL,evidence TEXT NOT NULL,status TEXT NOT NULL,is_demo INTEGER DEFAULT 0,current_assessment_id INTEGER,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,disbursed_at TEXT);
        CREATE TABLE IF NOT EXISTS assessments(id INTEGER PRIMARY KEY,application_id INTEGER REFERENCES applications(id),snapshot TEXT NOT NULL,result TEXT NOT NULL,model_version TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS scenarios(id INTEGER PRIMARY KEY,application_id INTEGER REFERENCES applications(id),base_assessment_id INTEGER REFERENCES assessments(id),overrides TEXT NOT NULL,snapshot TEXT NOT NULL,result TEXT NOT NULL,created_by INTEGER REFERENCES users(id),created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY,application_id INTEGER REFERENCES applications(id),kind TEXT NOT NULL,original_name TEXT NOT NULL,path TEXT NOT NULL,mime TEXT NOT NULL,sha256 TEXT NOT NULL,status TEXT DEFAULT 'unverified',note TEXT DEFAULT '',created_at TEXT NOT NULL,reviewed_by INTEGER REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS extraction_drafts(id INTEGER PRIMARY KEY,owner_id INTEGER REFERENCES users(id),result TEXT NOT NULL,path TEXT NOT NULL,mime TEXT NOT NULL,original_name TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS committee_reviews(id INTEGER PRIMARY KEY,application_id INTEGER REFERENCES applications(id),assessment_id INTEGER REFERENCES assessments(id),scenario_id INTEGER REFERENCES scenarios(id),result TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS human_decisions(id INTEGER PRIMARY KEY,application_id INTEGER REFERENCES applications(id),assessment_id INTEGER REFERENCES assessments(id),reviewer_id INTEGER REFERENCES users(id),decision TEXT NOT NULL,reason TEXT NOT NULL,conditions TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY,actor_id INTEGER,application_id INTEGER,action TEXT NOT NULL,before_value TEXT,after_value TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS repayments(id INTEGER PRIMARY KEY,application_id INTEGER REFERENCES applications(id),due_date TEXT NOT NULL,paid_date TEXT,amount_due REAL NOT NULL,amount_paid REAL NOT NULL,created_at TEXT NOT NULL,UNIQUE(application_id,due_date));
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        c.execute("INSERT OR IGNORE INTO settings VALUES ('dscr_threshold','1.25')")
    token_path=ROOT/'setup-token.txt'
    if not one('SELECT id FROM users LIMIT 1') and not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(32))
        try: token_path.chmod(0o600)
        except OSError: pass


def audit(actor,aid,action,before=None,after=None):
    execute('INSERT INTO audit_events(actor_id,application_id,action,before_value,after_value,created_at) VALUES (?,?,?,?,?,?)',
      (actor,aid,action,json.dumps(before),json.dumps(after),now()))

def password_hash(password):
    salt=secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()
    return salt+':'+digest

def check_password(password,stored):
    salt,expected=stored.split(':')
    actual=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()
    return secrets.compare_digest(actual,expected)
