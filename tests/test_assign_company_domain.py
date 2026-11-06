import json
import sqlite3
from pathlib import Path

import pytest

from scripts.assign_company_domain import assign_domain
from scripts.domain_review import timestamp


@pytest.fixture
def data(tmp_path):
    db=tmp_path/'staging.sqlite';cache=tmp_path/'cache.sqlite'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE staging_leads (id INTEGER PRIMARY KEY,company_name TEXT,raw_email TEXT,neverbounce_status TEXT)')
        c.executemany('INSERT INTO staging_leads VALUES(?,?,?,?)',[(1,'Access Energy','known@example.ma','UNKNOWN'),(2,'ACCESS ENERGY',None,'PENDING'),(3,'Other',None,'PENDING')])
    evidence={'domain':'accessenergy.ma','status':'FOUND','protocol':'WHOIS','source':'whois.registre.ma:43','registrant_names':['Access Energy'],'observed_at':timestamp(),'evidence_sha256':'a'*64}
    with sqlite3.connect(cache) as c:
        c.execute('CREATE TABLE domain_review_cache (key TEXT PRIMARY KEY,observed_at TEXT,payload TEXT)')
        c.execute('INSERT INTO domain_review_cache VALUES(?,?,?)',('accessenergy.ma',evidence['observed_at'],json.dumps(evidence)))
    return db,cache


def run(data,**kw):
    return assign_domain(*data,'accessenergy.ma','ACCESS ENERGY',**kw)


def test_preview_apply_backup_and_idempotence(data):
    db,cache=data;before=db.read_bytes();cache_before=cache.read_bytes()
    assert run(data,dry_run=True)['would_update']==[1,2]
    assert db.read_bytes()==before and cache.read_bytes()==cache_before
    assert not Path(str(db)+'.enrichment.lock').exists()
    r=run(data);assert r['updated']==[1,2]
    import gzip
    restored = db.parent/'restored.sqlite'
    restored.write_bytes(gzip.decompress(Path(r['backup']).read_bytes()))
    with sqlite3.connect(db) as c,sqlite3.connect(restored) as old:
        assert c.execute('SELECT id,company_name,raw_email,neverbounce_status FROM staging_leads').fetchall()==old.execute('SELECT * FROM staging_leads').fetchall()
        assert c.execute('SELECT company_domain FROM staging_leads ORDER BY id').fetchall()==[('accessenergy.ma',),('accessenergy.ma',),(None,)]
        assert c.execute('SELECT count(*) FROM company_domain_assignments').fetchone()[0]==2
    after=db.read_bytes();assert run(data)['updated']==[];assert db.read_bytes()==after


@pytest.mark.parametrize('mode',['stale','no_holder','wrong_name','wrong_domain','failed'])
def test_uncertain_review_never_writes(data,mode):
    db,cache=data
    with sqlite3.connect(cache) as c:
        r=json.loads(c.execute('SELECT payload FROM domain_review_cache').fetchone()[0])
        if mode=='stale':r['observed_at']='2000-01-01T00:00:00+00:00'
        if mode=='no_holder':r['registrant_names']=[]
        if mode=='wrong_name':r['registrant_names']=['Other']
        if mode=='wrong_domain':r['domain']='other.ma'
        if mode=='failed':r['status']='UNKNOWN'
        c.execute('UPDATE domain_review_cache SET observed_at=?,payload=?',(r['observed_at'],json.dumps(r)))
    before=db.read_bytes()
    with pytest.raises(ValueError):run(data)
    assert db.read_bytes()==before


def test_conflict_aborts_entire_assignment(data):
    db,_=data
    with sqlite3.connect(db) as c:
        c.execute('ALTER TABLE staging_leads ADD COLUMN company_domain TEXT')
        c.execute("UPDATE staging_leads SET company_domain='different.ma' WHERE id=2")
    before=db.read_bytes()
    with pytest.raises(ValueError,match='Conflicting'):run(data)
    assert db.read_bytes()==before


def test_unmatched_company_does_not_migrate(data):
    db,cache=data
    with sqlite3.connect(db) as c:c.execute("UPDATE staging_leads SET company_name='Other'")
    before=db.read_bytes();assert run(data)['updated']==[];assert db.read_bytes()==before


def test_missing_database_never_created(data,tmp_path):
    p=tmp_path/'absent.sqlite'
    with pytest.raises(sqlite3.OperationalError):assign_domain(p,data[1],'accessenergy.ma','ACCESS ENERGY')
    assert not p.exists()
