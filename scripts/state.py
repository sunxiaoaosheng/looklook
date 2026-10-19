"""事务状态接口。标准输入输出为 JSON，使用短事务；工作租约与状态更新分离。"""
import hashlib,json,os,sqlite3,sys,time
from pathlib import Path
from settings import state_root
class Store:
    def __init__(self,root=None):
        root=Path(root or state_root());
        if root.is_symlink() or (root/'state.sqlite3').is_symlink():raise ValueError('状态路径不能是符号链接')
        root.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(root,0o700)
        self.db=sqlite3.connect(str(root/'state.sqlite3'),timeout=20,isolation_level=None)
        os.chmod(root/'state.sqlite3',0o600)
        self.db.execute('PRAGMA busy_timeout=20000')
        self.db.execute('CREATE TABLE IF NOT EXISTS records (namespace TEXT,key TEXT,value TEXT,updated REAL,PRIMARY KEY(namespace,key))')
        self.db.execute('CREATE TABLE IF NOT EXISTS leases (key TEXT PRIMARY KEY,owner TEXT,expires REAL)')
    def get(self,ns,key):
        row=self.db.execute('SELECT value FROM records WHERE namespace=? AND key=?',(ns,key)).fetchone()
        return json.loads(row[0]) if row else None
    def put(self,ns,key,value):
        self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',(ns,key,json.dumps(value,ensure_ascii=False),time.time()));return value
    def all(self,ns):return {k:json.loads(v) for k,v in self.db.execute('SELECT key,value FROM records WHERE namespace=?',(ns,))}
    def lease(self,key,owner,seconds=75):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT owner,expires FROM leases WHERE key=?',(key,)).fetchone()
            if row and row[0]!=owner and row[1]>time.time():self.db.execute('ROLLBACK');return False
            self.db.execute('INSERT OR REPLACE INTO leases VALUES (?,?,?)',(key,owner,time.time()+seconds));self.db.execute('COMMIT');return True
        except BaseException:self.db.execute('ROLLBACK');raise
    def release(self,key,owner):self.db.execute('DELETE FROM leases WHERE key=? AND owner=?',(key,owner))
    def prune(self):
        for ns,key,value,updated in self.db.execute('SELECT * FROM records').fetchall():
            v=json.loads(value);ttl=86400 if v.get('status') in ('sent','completed') else 604800 if v.get('status') in ('failed','delivery_failed','cancelled') else None
            if ttl and time.time()-updated>ttl:self.db.execute('DELETE FROM records WHERE namespace=? AND key=?',(ns,key))
    def close(self):self.db.close()
def fingerprint(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file():return None
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return {'size':p.stat().st_size,'sha256':h.hexdigest()}
def main():
    r=json.load(sys.stdin);s=Store(r.get('root'))
    try:
        op=r['op'];ns=r.get('namespace','jobs');key=r.get('key','')
        if op=='get':out=s.get(ns,key)
        elif op=='put':out=s.put(ns,key,r['value'])
        elif op=='all':s.prune();out=s.all(ns)
        elif op=='lease':out=s.lease(key,r['owner'],r.get('seconds',75))
        elif op=='release':s.release(key,r['owner']);out=True
        else:raise ValueError('无效状态操作')
        print(json.dumps({'ok':True,'value':out},ensure_ascii=False))
    finally:s.close()
if __name__=='__main__':main()
