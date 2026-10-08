"""Local independent accounts and revocable in-memory sessions; no real-name claim."""

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from collections import deque

CAPABILITIES = frozenset({"employee", "clerk", "cook", "executor"})
ITERATIONS = 600000
AUTH_SCHEMA = """
CREATE TABLE accounts(
 id TEXT PRIMARY KEY, username_key TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
 password_hash BLOB, salt BLOB, kdf_iterations INTEGER NOT NULL DEFAULT 600000 CHECK(kdf_iterations=600000),
 state TEXT NOT NULL CHECK(state IN ('pending','active','disabled')),
 capabilities TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0 CHECK(is_admin IN (0,1)),
 bootstrap_admin INTEGER NOT NULL DEFAULT 0 CHECK(bootstrap_admin IN (0,1)), auth_epoch INTEGER NOT NULL DEFAULT 1,
 activation_digest TEXT, expires_at REAL, created_at REAL NOT NULL, created_by TEXT NOT NULL);
CREATE UNIQUE INDEX accounts_bootstrap_unique ON accounts(bootstrap_admin) WHERE bootstrap_admin=1;
CREATE TABLE auth_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""


class AuthError(Exception):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _username(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value.strip()):
        raise AuthError("invalid_account", "用户名须为1至80位字母、数字、点、下划线或短横线。")
    return value.strip().casefold()


def _password(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 256:
        raise AuthError("invalid_password", "密码须为12至256字符，不能截断。")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise AuthError("invalid_password", "密码字符编码无效。") from None
    return value


def _caps(value):
    if not isinstance(value, list) or any(not isinstance(c, str) or c not in CAPABILITIES for c in value) or len(set(value)) != len(value):
        raise AuthError("invalid_capabilities", "职责仅允许employee、clerk、cook、executor，不能自选管理权限。")
    return sorted(value)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _token_valid(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{20,200}", value) is not None


def _principal(row):
    return {"id": row["id"], "username": row["username_key"], "display_name": row["display_name"],
            "capabilities": json.loads(row["capabilities"]), "is_admin": bool(row["is_admin"]), "auth_epoch": row["auth_epoch"]}


def _account(row):
    return {**_principal(row), "state": row["state"]}


class AuthService:
    def __init__(self, store, *, clock=time.time, monotonic=time.monotonic, max_hashes=2,
                 failure_limit=5, rate_window=60, activation_ttl=86400,
                 session_ttl=43200, idle_ttl=1800, anonymous_ttl=900):
        self.store, self.clock, self.monotonic = store, clock, monotonic
        self.failure_limit, self.rate_window = failure_limit, rate_window
        self.activation_ttl, self.session_ttl = activation_ttl, session_ttl
        self.idle_ttl, self.anonymous_ttl = idle_ttl, anonymous_ttl
        self._lock = threading.RLock()
        self._hash_slots = threading.BoundedSemaphore(max_hashes)
        self._sessions, self._failures = {}, {}
        self._dummy_salt, self._dummy_hash = secrets.token_bytes(16), secrets.token_bytes(32)

    def _hash(self, password, salt):
        if not self._hash_slots.acquire(blocking=False):
            raise AuthError("auth_rate_limited", "登录或激活暂时受限，请稍后重试。", 429)
        try:
            return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
        finally:
            self._hash_slots.release()

    def _rate(self, key, ip, failed=False):
        current = self.monotonic()
        with self._lock:
            # Bound expired buckets as well as concurrent hash work.
            for bucket in list(self._failures):
                queue = self._failures[bucket]
                while queue and queue[0] <= current - self.rate_window:
                    queue.popleft()
                if not queue:
                    del self._failures[bucket]
            keys = ("account:" + key, "ip:" + str(ip))
            if any(len(self._failures.get(bucket, ())) >= self.failure_limit for bucket in keys):
                raise AuthError("auth_rate_limited", "登录或激活暂时受限，请稍后重试。", 429)
            if failed:
                for bucket in keys:
                    self._failures.setdefault(bucket, deque()).append(current)

    def _audit(self, connection, actor, action, account_id, **details):
        record = {"actor_id": actor, "action": action, "account_id": account_id, "at": self.clock(), **details}
        connection.execute("INSERT INTO auth_meta VALUES(?,?)", ("audit:" + str(uuid.uuid4()), json.dumps(record, ensure_ascii=False)))

    def _admin(self, connection, actor):
        if not isinstance(actor, dict) or not isinstance(actor.get("id"), str):
            raise AuthError("auth_required", "请先登录。", 401)
        row = connection.execute("SELECT * FROM accounts WHERE id=?", (actor["id"],)).fetchone()
        if not row or row["state"] != "active" or row["auth_epoch"] != actor.get("auth_epoch"):
            raise AuthError("auth_required", "登录已失效，请重新登录。", 401)
        if not row["is_admin"]:
            raise AuthError("admin_required", "仅工作区账号管理员可执行此操作。", 403)
        return row

    def bootstrap(self, username, password):
        """Only the explicit, instance-locked local management CLI may call this method."""
        username, password = _username(username), _password(password)
        self._rate("bootstrap", "local")
        with self.store.connect() as connection:
            initialized = connection.execute("SELECT 1 FROM auth_meta WHERE key='bootstrap_admin'").fetchone()
        if initialized:
            raise AuthError("bootstrap_closed", "管理员首次引导已永久关闭。", 409)
        salt = secrets.token_bytes(16)
        hashed = self._hash(password, salt)
        with self.store.transaction() as connection:
            if connection.execute("SELECT 1 FROM auth_meta WHERE key='bootstrap_admin'").fetchone() or connection.execute("SELECT 1 FROM accounts WHERE bootstrap_admin=1").fetchone():
                raise AuthError("bootstrap_closed", "管理员首次引导已永久关闭。", 409)
            account_id = str(uuid.uuid4())
            try:
                connection.execute("INSERT INTO accounts(id,username_key,display_name,password_hash,salt,state,capabilities,is_admin,bootstrap_admin,created_at,created_by) VALUES(?,?,?,?,?,'active','[]',1,1,?,'local-cli')",
                                   (account_id, username, username, hashed, salt, self.clock()))
            except sqlite3.IntegrityError:
                raise AuthError("account_conflict", "用户名已存在。", 409) from None
            connection.execute("INSERT INTO auth_meta VALUES('bootstrap_admin',?)", (account_id,))
            self._audit(connection, "local-cli", "bootstrap", account_id)
            return _principal(connection.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone())

    def create_account(self, actor_admin, username, display_name, capabilities):
        username, capabilities = _username(username), _caps(capabilities)
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 200:
            raise AuthError("invalid_account", "显示名须为1至200字符。")
        token, account_id = secrets.token_urlsafe(32), str(uuid.uuid4())
        with self.store.transaction() as connection:
            actor = self._admin(connection, actor_admin)
            try:
                connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,activation_digest,expires_at,created_at,created_by) VALUES(?,?,?,'pending',?,?,?,?,?)",
                                   (account_id, username, display_name.strip(), json.dumps(capabilities), _digest(token), self.clock() + self.activation_ttl, self.clock(), actor["id"]))
            except sqlite3.IntegrityError:
                raise AuthError("account_conflict", "用户名已存在。", 409) from None
            self._audit(connection, actor["id"], "create_account", account_id, capabilities=capabilities)
            return {"account": _account(connection.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()), "activation_token": token}

    def activate(self, token, password, ip="local"):
        password = _password(password)
        digest = _digest(token) if _token_valid(token) else "invalid"
        self._rate("activate:" + digest, ip)
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE activation_digest=?", (digest,)).fetchone()
        if not row or row["state"] != "pending" or row["expires_at"] is None or row["expires_at"] <= self.clock():
            self._rate("activate:" + digest, ip, failed=True)
            raise AuthError("activation_invalid", "激活凭据无效、已使用或已过期。", 401)
        salt = secrets.token_bytes(16)
        hashed = self._hash(password, salt)
        with self.store.transaction() as connection:
            cursor = connection.execute("UPDATE accounts SET state='active',password_hash=?,salt=?,activation_digest=NULL,expires_at=NULL,auth_epoch=auth_epoch+1 WHERE id=? AND state='pending' AND activation_digest=? AND auth_epoch=? AND expires_at>?",
                                        (hashed, salt, row["id"], digest, row["auth_epoch"], self.clock()))
            if cursor.rowcount != 1:
                raise AuthError("activation_invalid", "激活凭据无效、已使用或已过期。", 401)
            self._audit(connection, row["id"], "activate", row["id"])
            return _principal(connection.execute("SELECT * FROM accounts WHERE id=?", (row["id"],)).fetchone())

    def control_account(self, actor_admin, account_id, expected_epoch, command, *, capabilities=None):
        if not isinstance(account_id, str) or type(expected_epoch) is not int:
            raise AuthError("invalid_account", "账号与授权版本无效。")
        if not isinstance(command, str) or command not in {"disabled", "reset", "capabilities"}:
            raise AuthError("invalid_control", "不支持此账号操作。")
        caps = _caps(capabilities) if command == "capabilities" else None
        token = secrets.token_urlsafe(32) if command == "reset" else None
        with self.store.transaction() as connection:
            actor = self._admin(connection, actor_admin)
            row = connection.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not row:
                raise AuthError("account_not_found", "账号不存在。", 404)
            if row["auth_epoch"] != expected_epoch:
                raise AuthError("account_conflict", "账号授权已变化，请重新读取。", 409)
            if command == "disabled":
                connection.execute("UPDATE accounts SET state='disabled',auth_epoch=auth_epoch+1,activation_digest=NULL,expires_at=NULL WHERE id=?", (account_id,))
            elif command == "reset":
                connection.execute("UPDATE accounts SET state='pending',password_hash=NULL,salt=NULL,auth_epoch=auth_epoch+1,activation_digest=?,expires_at=? WHERE id=?", (_digest(token), self.clock() + self.activation_ttl, account_id))
            else:
                connection.execute("UPDATE accounts SET capabilities=?,auth_epoch=auth_epoch+1 WHERE id=?", (json.dumps(caps), account_id))
            self._audit(connection, actor["id"], command, account_id, before_capabilities=json.loads(row["capabilities"]), after_capabilities=caps if caps is not None else json.loads(row["capabilities"]))
            result = {"account": _account(connection.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone())}
            if token is not None:
                result["activation_token"] = token
            return result

    def authenticate(self, username, password, ip="local"):
        try:
            key = _username(username)
        except AuthError:
            key = "invalid"
        self._rate(key, ip)
        valid_password = isinstance(password, str) and 12 <= len(password) <= 256
        if valid_password:
            try:
                password.encode("utf-8")
            except UnicodeError:
                valid_password = False
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE username_key=?", (key,)).fetchone()
        valid_row = row is not None and row["state"] == "active" and row["password_hash"] is not None and row["salt"] is not None
        salt = row["salt"] if valid_row else self._dummy_salt
        expected = row["password_hash"] if valid_row else self._dummy_hash
        actual = self._hash(password if valid_password else "invalid-password-fixture", salt)
        matches = hmac.compare_digest(actual, expected)
        if not valid_password or not valid_row or not matches:
            self._rate(key, ip, failed=True)
            raise AuthError("invalid_credentials", "账号或密码不正确。", 401)
        # Re-read after expensive hashing: concurrent disable/reset must win.
        with self.store.connect() as connection:
            current = connection.execute("SELECT * FROM accounts WHERE id=?", (row["id"],)).fetchone()
        if not current or current["state"] != "active" or current["auth_epoch"] != row["auth_epoch"]:
            raise AuthError("invalid_credentials", "账号或密码不正确。", 401)
        return _principal(current)

    def _new_session(self, principal=None):
        raw, current = secrets.token_urlsafe(32), self.clock()
        private_principal = {**principal, "capabilities": list(principal["capabilities"])} if principal else None
        record = {"principal": private_principal, "csrf_token": secrets.token_urlsafe(32), "created_at": current,
                  "last_seen": current, "expires_at": current + (self.session_ttl if principal else self.anonymous_ttl)}
        with self._lock:
            for digest, session in list(self._sessions.items()):
                if session["expires_at"] <= current or session["last_seen"] + self.idle_ttl <= current:
                    del self._sessions[digest]
            self._sessions[_digest(raw)] = record
        return raw, {"authenticated": principal is not None, "csrf_token": record["csrf_token"], **({"principal": principal} if principal else {})}

    def new_anonymous(self):
        return self._new_session()

    def resolve(self, raw_token):
        if not _token_valid(raw_token):
            raise AuthError("auth_required", "登录已失效，请重新登录。", 401)
        digest, current = _digest(raw_token), self.clock()
        with self._lock:
            record = self._sessions.get(digest)
            if not record or record["expires_at"] <= current or record["last_seen"] + self.idle_ttl <= current:
                self._sessions.pop(digest, None)
                raise AuthError("auth_required", "登录已失效，请重新登录。", 401)
            principal = record["principal"]
            if principal:
                with self.store.connect() as connection:
                    row = connection.execute("SELECT * FROM accounts WHERE id=?", (principal["id"],)).fetchone()
                if not row or row["state"] != "active" or row["auth_epoch"] != principal["auth_epoch"]:
                    del self._sessions[digest]
                    raise AuthError("auth_required", "登录已失效，请重新登录。", 401)
                principal = _principal(row)
            record["last_seen"] = current
            return {"authenticated": principal is not None, "csrf_token": record["csrf_token"], **({"principal": principal} if principal else {})}

    def check_csrf(self, raw_token, csrf_token):
        session = self.resolve(raw_token)
        if not _token_valid(csrf_token) or not hmac.compare_digest(session["csrf_token"], csrf_token):
            raise AuthError("csrf_invalid", "请求校验失败，请刷新后重试。", 403)
        return session

    def login(self, raw_token, csrf_token, username, password, ip="local"):
        self.check_csrf(raw_token, csrf_token)
        principal = self.authenticate(username, password, ip)
        with self._lock:
            self._sessions.pop(_digest(raw_token), None)
        return self._new_session(principal)

    def rotate(self, raw_token, csrf_token):
        """Replace token/CSRF without extending the original absolute lifetime."""
        session = self.check_csrf(raw_token, csrf_token)
        with self._lock:
            record = self._sessions.pop(_digest(raw_token), None)
            if record is None:
                raise AuthError("auth_required", "登录已失效，请重新登录。", 401)
            raw, result = self._new_session(session.get("principal"))
            replacement = self._sessions[_digest(raw)]
            replacement["created_at"], replacement["expires_at"] = record["created_at"], record["expires_at"]
            return raw, result

    def logout(self, raw_token, csrf_token):
        self.check_csrf(raw_token, csrf_token)
        with self._lock:
            self._sessions.pop(_digest(raw_token), None)
        return self.new_anonymous()
