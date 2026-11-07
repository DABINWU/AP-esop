"""Local administrator accounts and persistent opaque browser sessions.

No API keys, passwords or raw session tokens are stored in browser JS storage.
Each service has a separate auth database and cookie name.
"""
import argparse
import getpass
import hashlib
import hmac
import os
import secrets
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request, Response
from pydantic import Field
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from .reports import Input


class Login(Input):
    username: str = Field(pattern=r'^[A-Za-z0-9_.-]{3,64}$')
    password: str = Field(min_length=1, max_length=128)


class PasswordChange(Input):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class AuthBodyLimit:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or not scope['path'].startswith('/api/v1/auth/'):
            return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]))
        try:large=int(headers.get(b'content-length',b'0'))>8192
        except ValueError:large=True
        if large:
            return await JSONResponse({'error':{'code':'too_large','message':'登录请求过大'}},status_code=413)(scope,receive,send)
        total=0
        async def limited():
            nonlocal total
            message=await receive();total+=len(message.get('body',b''))
            if total>8192:raise HTTPException(413,'登录请求过大')
            return message
        return await self.app(scope,limited,send)


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1, dklen=32)
    return 'scrypt$'+salt.hex()+'$'+digest.hex()


def verify_password(password, stored):
    kind, salt, expected = stored.split('$')
    if kind != 'scrypt': raise RuntimeError('Unsupported password hash')
    actual = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
    return hmac.compare_digest(actual, bytes.fromhex(expected))


def restrict_file(path):
    if os.name == 'nt':
        # Owner remains able to retrieve the one-time credential even without elevation.
        result = subprocess.run(['whoami','/user','/fo','csv','/nh'],capture_output=True,check=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        import csv
        sid = next(csv.reader(result.stdout.decode(errors='replace').splitlines()))[1]
        subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', '*'+sid+':F',
                        '*S-1-5-18:F', '*S-1-5-32-544:F'], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else: path.chmod(0o600)


class Accounts:
    def __init__(self, root, scope, hours=12, secure=False):
        self.root = Path(root)
        self.path = self.root/'web-auth.sqlite3'
        self.bootstrap = self.root/'initial-login.txt'
        self.cookie = 'esop_'+scope+'_session'
        self.hours, self.secure = hours, secure
        if not 1 <= hours <= 168: raise ValueError('AUTH_SESSION_HOURS must be 1..168')

    @contextmanager
    def transaction(self):
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            c.execute('PRAGMA synchronous=FULL'); c.execute('PRAGMA busy_timeout=15000')
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback(); raise
        finally: c.close()

    def initialize(self):
        self.root.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
                CREATE TABLE IF NOT EXISTS web_users(username TEXT PRIMARY KEY,password_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS web_sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS web_failures(client TEXT PRIMARY KEY,count INTEGER NOT NULL,reset_at REAL NOT NULL);
            ''')
        with self.transaction() as c:
            if not c.execute('SELECT 1 FROM web_users').fetchone():
                password = secrets.token_urlsafe(18)
                temp = self.root/'initial-login.tmp'
                fd = os.open(temp, os.O_CREAT|os.O_WRONLY|os.O_TRUNC, 0o600)
                try:
                    restrict_file(temp)
                    with os.fdopen(fd,'w',encoding='utf-8') as stream:
                        fd = None
                        stream.write('username=admin\npassword='+password+'\n')
                        stream.flush();os.fsync(stream.fileno())
                    os.replace(temp,self.bootstrap)
                finally:
                    if fd is not None:os.close(fd)
                c.execute('INSERT INTO web_users VALUES(?,?)',('admin',password_hash(password)))

    def csrf(self, token):
        return hmac.new(token.encode(),b'esop-browser-csrf-v1',hashlib.sha256).hexdigest()

    def origin(self, request):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            raise HTTPException(403,'请从当前系统页面登录或操作')

    def require(self, request, csrf=True):
        token = request.cookies.get(self.cookie,'')
        if not token or len(token)>128:raise HTTPException(401,'请登录账号')
        hashed = hashlib.sha256(token.encode()).hexdigest()
        with self.transaction() as c:
            row=c.execute('SELECT * FROM web_sessions WHERE token_hash=? AND expires_at>?',(hashed,time.time())).fetchone()
        if row is None:raise HTTPException(401,'登录已过期，请重新登录')
        if csrf and request.method not in ('GET','HEAD','OPTIONS'):
            self.origin(request)
            if not hmac.compare_digest(request.headers.get('x-csrf-token','').encode(),self.csrf(token).encode()):
                raise HTTPException(403,'页面验证已失效，请刷新后重试')
        return dict(row)

    def response(self, row, token):
        return dict(username=row['username'],csrf_token=self.csrf(token),
                    expires_at=datetime.fromtimestamp(row['expires_at'],timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'))

    def issue(self,c,username):
        token=secrets.token_urlsafe(32)
        row=dict(username=username,expires_at=time.time()+self.hours*3600)
        c.execute('DELETE FROM web_sessions WHERE expires_at<=?',(time.time(),))
        c.execute('INSERT INTO web_sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),username,row['expires_at']))
        return row,token

    def set_cookie(self, response, request, token):
        response.set_cookie(self.cookie,token,max_age=self.hours*3600,httponly=True,samesite='strict',
                            secure=self.secure or request.url.scheme=='https',path='/api/v1')

    def reset(self,username,password):
        Login(username=username,password=password)
        PasswordChange(current_password=password,new_password=password)
        with self.transaction() as c:
            c.execute('DELETE FROM web_users')
            c.execute('INSERT INTO web_users VALUES(?,?)',(username,password_hash(password)))
            c.execute('DELETE FROM web_sessions');c.execute('DELETE FROM web_failures')
        self.bootstrap.unlink(missing_ok=True)

    def mount(self, app):
        app.add_middleware(AuthBodyLimit)
        @app.post('/api/v1/auth/login')
        def login(body: Login,request: Request,response: Response):
            self.origin(request)
            if request.headers.get('x-esop-login')!='1':raise HTTPException(403,'请使用系统登录页面')
            client=request.client.host if request.client else 'local'
            failed=False
            with self.transaction() as c:
                c.execute('DELETE FROM web_failures WHERE reset_at<=?',(time.time(),))
                row=c.execute('SELECT * FROM web_failures WHERE client=?',(client,)).fetchone()
                if row and row['count']>=10:raise HTTPException(429,'登录失败过多，请5分钟后再试')
                account=c.execute('SELECT * FROM web_users WHERE username=?',(body.username,)).fetchone()
                dummy=account or c.execute('SELECT * FROM web_users LIMIT 1').fetchone()
                valid=verify_password(body.password,dummy['password_hash'])
                if not account or not valid:
                    c.execute('INSERT INTO web_failures VALUES(?,1,?) ON CONFLICT(client) DO UPDATE SET count=count+1', (client,time.time()+300))
                    failed=True
                else:
                    c.execute('DELETE FROM web_failures WHERE client=?',(client,))
                    old=request.cookies.get(self.cookie,'')
                    if old:c.execute('DELETE FROM web_sessions WHERE token_hash=?',(hashlib.sha256(old.encode()).hexdigest(),))
                    row,token=self.issue(c,body.username)
            if failed:raise HTTPException(401,'账号或密码错误')
            self.set_cookie(response,request,token)
            return self.response(row,token)

        @app.get('/api/v1/auth/session')
        def session(request: Request):return self.response(self.require(request),request.cookies[self.cookie])

        @app.post('/api/v1/auth/logout')
        def logout(request: Request,response: Response):
            self.origin(request)
            try: self.require(request)
            except HTTPException as exc:
                if exc.status_code!=401:raise
            token=request.cookies.get(self.cookie,'')
            with self.transaction() as c:
                c.execute('DELETE FROM web_sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
            response.delete_cookie(self.cookie,path='/api/v1',httponly=True,samesite='strict')
            return {'logged_out':True}

        @app.post('/api/v1/auth/password')
        def change(body: PasswordChange,request: Request,response: Response):
            session=self.require(request)
            with self.transaction() as c:
                user=c.execute('SELECT * FROM web_users WHERE username=?',(session['username'],)).fetchone()
                if not user or not verify_password(body.current_password,user['password_hash']):
                    raise HTTPException(401,'当前密码错误')
                c.execute('UPDATE web_users SET password_hash=? WHERE username=?',(password_hash(body.new_password),session['username']))
                c.execute('DELETE FROM web_sessions')
                row,token=self.issue(c,session['username'])
            self.bootstrap.unlink(missing_ok=True)
            self.set_cookie(response,request,token)
            return self.response(row,token)


def main():
    parser=argparse.ArgumentParser(description='Local administrator password recovery; never takes a password on the command line')
    parser.add_argument('--data-dir',required=True);parser.add_argument('--username',default='admin')
    args=parser.parse_args()
    password=getpass.getpass('New password (10..128 characters): ')
    if password!=getpass.getpass('Confirm password: '):parser.exit(2,'Passwords do not match\n')
    accounts=Accounts(args.data_dir,'local');accounts.initialize();accounts.reset(args.username,password)
    print('Administrator updated; previous sessions revoked.')


if __name__=='__main__':main()
