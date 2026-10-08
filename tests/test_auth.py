"""Local synthetic account fixtures; no production credentials or identities."""

import hashlib
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from assistant_app.auth import AUTH_SCHEMA, ITERATIONS, AuthError, AuthService


PASSWORD = "isolated-fixture-password-01"


class FixtureStore:
    def __init__(self, folder):
        self.path = Path(folder) / "isolated-auth.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as connection:
            connection.executescript(AUTH_SCHEMA)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        with self.lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise


class AuthContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qa-auth-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.store = FixtureStore(self.temp.name)
        self.current = 1000
        self.auth = AuthService(self.store, clock=lambda: self.current, monotonic=lambda: self.current)
        self.admin = self.auth.bootstrap("FixtureAdmin", PASSWORD)

    def account(self, username="fixture_employee", caps=None):
        created = self.auth.create_account(self.admin, username, "隔离工程账号", ["employee"] if caps is None else caps)
        actor = self.auth.activate(created["activation_token"], PASSWORD)
        return actor, created

    def assert_auth_error(self, callback, status, code=None):
        with self.assertRaises(AuthError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertNotIn(PASSWORD, str(caught.exception))
        return caught.exception

    def test_bootstrap_is_permanent_and_admin_has_no_business_capabilities(self):
        self.assertTrue(self.admin["is_admin"])
        self.assertEqual(self.admin["capabilities"], [])
        self.assert_auth_error(lambda: self.auth.bootstrap("second_admin", PASSWORD), 409, "bootstrap_closed")
        self.auth.control_account(self.admin, self.admin["id"], self.admin["auth_epoch"], "disabled")
        self.assert_auth_error(lambda: self.auth.bootstrap("second_admin", PASSWORD), 409, "bootstrap_closed")
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM accounts WHERE bootstrap_admin=1").fetchone()[0], 1)
            self.assertIsNotNone(connection.execute("SELECT 1 FROM auth_meta WHERE key='bootstrap_admin'").fetchone())

    def test_activation_is_single_use_expiring_and_cannot_self_escalate(self):
        employee, created = self.account()
        self.assertFalse(employee["is_admin"])
        self.assertEqual(employee["capabilities"], ["employee"])
        self.assert_auth_error(lambda: self.auth.activate(created["activation_token"], PASSWORD), 401, "activation_invalid")
        self.assert_auth_error(lambda: self.auth.create_account({**employee, "is_admin": True}, "bad", "工程夹具", ["cook"]), 403)
        self.assert_auth_error(lambda: self.auth.create_account(self.admin, "bad", "工程夹具", ["admin"]), 422)
        self.assert_auth_error(lambda: self.auth.create_account(self.admin, "FIXTURE_EMPLOYEE", "重复账号", []), 409)
        pending = self.auth.create_account(self.admin, "expired", "过期工程账号", [])
        self.current += 86401
        self.assert_auth_error(lambda: self.auth.activate(pending["activation_token"], PASSWORD), 401)

    def test_password_hash_storage_safe_returns_and_no_plaintext_audit(self):
        employee, created = self.account()
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE id=?", (employee["id"],)).fetchone()
            self.assertEqual(row["kdf_iterations"], ITERATIONS)
            self.assertEqual(len(row["salt"]), 16)
            self.assertEqual(len(row["password_hash"]), 32)
            self.assertEqual(row["password_hash"], hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), row["salt"], 600000))
            self.assertIsNone(row["activation_digest"])
            audit = " ".join(item[0] for item in connection.execute("SELECT value FROM auth_meta"))
            self.assertNotIn(PASSWORD, audit)
            self.assertNotIn(created["activation_token"], audit)
            self.assertNotIn(row["password_hash"].hex(), audit)
        self.assertEqual(set(employee), {"id", "username", "display_name", "capabilities", "is_admin", "auth_epoch"})
        self.assertEqual(set(created), {"account", "activation_token"})
        for password in ("short", "x" * 257, None, 123):
            pending = self.auth.create_account(self.admin, "invalid_" + str(len(str(password))), "工程账号", [])
            self.assert_auth_error(lambda: self.auth.activate(pending["activation_token"], password), 422, "invalid_password")

    def test_two_accounts_sessions_rotate_csrf_logout_and_expiration(self):
        one, _ = self.account("employee_one")
        two, _ = self.account("employee_two")
        raw_anon, anonymous = self.auth.new_anonymous()
        self.assertFalse(anonymous["authenticated"])
        raw_one, session_one = self.auth.login(raw_anon, anonymous["csrf_token"], "employee_one", PASSWORD)
        raw_two_anon, second_anon = self.auth.new_anonymous()
        raw_two, session_two = self.auth.login(raw_two_anon, second_anon["csrf_token"], "employee_two", PASSWORD)
        self.assertNotEqual(raw_one, raw_two)
        self.assertNotEqual(session_one["csrf_token"], session_two["csrf_token"])
        self.assertEqual(self.auth.resolve(raw_one)["principal"]["id"], one["id"])
        self.assertEqual(self.auth.resolve(raw_two)["principal"]["id"], two["id"])
        self.assert_auth_error(lambda: self.auth.resolve(raw_anon), 401)
        self.assert_auth_error(lambda: self.auth.check_csrf(raw_one, session_two["csrf_token"]), 403)
        self.assert_auth_error(lambda: self.auth.check_csrf(raw_one, "非ASCII"), 403)
        self.assertNotIn(raw_one, json.dumps(session_one))
        self.assertNotIn(raw_one, self.auth._sessions)
        new_raw, new_session = self.auth.logout(raw_one, session_one["csrf_token"])
        self.assertFalse(new_session["authenticated"])
        self.assert_auth_error(lambda: self.auth.resolve(raw_one), 401)
        self.current += 1801
        self.assert_auth_error(lambda: self.auth.resolve(raw_two), 401)
        self.assert_auth_error(lambda: self.auth.resolve(new_raw), 401)
        restarted = AuthService(self.store)
        self.assert_auth_error(lambda: restarted.resolve(raw_two), 401)

    def test_disable_reset_and_capability_epoch_revoke_existing_sessions(self):
        employee, created = self.account()
        raw, anonymous = self.auth.new_anonymous()
        raw, session = self.auth.login(raw, anonymous["csrf_token"], employee["username"], PASSWORD)
        changed = self.auth.control_account(self.admin, employee["id"], employee["auth_epoch"], "capabilities", capabilities=["employee", "cook"])["account"]
        self.assert_auth_error(lambda: self.auth.resolve(raw), 401)
        self.assert_auth_error(lambda: self.auth.control_account(self.admin, employee["id"], employee["auth_epoch"], "disabled"), 409)
        updated = self.auth.authenticate(employee["username"], PASSWORD)
        self.assertEqual(updated["capabilities"], ["cook", "employee"])
        reset = self.auth.control_account(self.admin, updated["id"], updated["auth_epoch"], "reset")
        self.assertEqual(reset["account"]["state"], "pending")
        self.assert_auth_error(lambda: self.auth.authenticate(employee["username"], PASSWORD), 401)
        active = self.auth.activate(reset["activation_token"], PASSWORD + "new")
        self.auth.control_account(self.admin, active["id"], active["auth_epoch"], "disabled")
        self.assert_auth_error(lambda: self.auth.authenticate(active["username"], PASSWORD + "new"), 401)

    def test_uniform_missing_account_cost_and_rate_limit_recovers(self):
        employee, _ = self.account()
        real_hash = self.auth._hash
        with patch.object(self.auth, "_hash", wraps=real_hash) as hashing:
            wrong = self.assert_auth_error(lambda: self.auth.authenticate(employee["username"], PASSWORD + "wrong"), 401)
            missing = self.assert_auth_error(lambda: self.auth.authenticate("missing-account", PASSWORD), 401)
        self.assertEqual((wrong.code, wrong.message), (missing.code, missing.message))
        self.assertEqual(hashing.call_count, 2)
        for _ in range(3):
            self.assert_auth_error(lambda: self.auth.authenticate("missing-account", PASSWORD), 401)
        self.assert_auth_error(lambda: self.auth.authenticate(employee["username"], PASSWORD), 429)
        self.current += 61
        self.assertEqual(self.auth.authenticate(employee["username"], PASSWORD)["id"], employee["id"])

    def test_concurrent_activation_consumes_once_and_hash_slots_are_bounded(self):
        pending = self.auth.create_account(self.admin, "parallel", "并发工程账号", ["employee"])
        barrier = threading.Barrier(2)
        actual_hash = self.auth._hash
        def synced_hash(password, salt):
            barrier.wait(timeout=3)
            return actual_hash(password, salt)
        def activate():
            try:
                self.auth.activate(pending["activation_token"], PASSWORD)
                return "active"
            except AuthError as error:
                return error.code
        with patch.object(self.auth, "_hash", side_effect=synced_hash), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: activate(), range(2)))
        self.assertCountEqual(results, ["active", "activation_invalid"])
        # Occupy the two permitted workers: a third request must fail before hashing.
        self.assertTrue(self.auth._hash_slots.acquire(blocking=False))
        self.assertTrue(self.auth._hash_slots.acquire(blocking=False))
        try:
            self.assert_auth_error(lambda: self.auth.authenticate("parallel", PASSWORD), 429)
        finally:
            self.auth._hash_slots.release()
            self.auth._hash_slots.release()

    def test_absolute_session_expiry_is_not_extended_by_activity(self):
        employee, _ = self.account()
        auth = AuthService(self.store, clock=lambda: self.current, session_ttl=10, idle_ttl=8)
        raw, anon = auth.new_anonymous()
        raw, session = auth.login(raw, anon["csrf_token"], employee["username"], PASSWORD)
        self.current += 7
        self.assertTrue(auth.resolve(raw)["authenticated"])
        previous = raw
        raw, session = auth.rotate(raw, session["csrf_token"])
        self.assert_auth_error(lambda: auth.resolve(previous), 401)
        self.current += 4
        self.assert_auth_error(lambda: auth.resolve(raw), 401)

    def test_malformed_unicode_inputs_fail_safely_and_returned_principal_is_not_session_storage(self):
        employee, _ = self.account()
        raw, anon = self.auth.new_anonymous()
        raw, session = self.auth.login(raw, anon["csrf_token"], employee["username"], PASSWORD)
        session["principal"]["id"] = self.admin["id"]
        session["principal"]["capabilities"].append("cook")
        self.assertEqual(self.auth.resolve(raw)["principal"], employee)
        self.assert_auth_error(lambda: self.auth.authenticate(employee["username"], "x" * 12 + "\ud800"), 401)
        self.assert_auth_error(lambda: self.auth.activate("x" * 30 + "\ud800", PASSWORD), 401)
        self.assert_auth_error(lambda: self.auth.resolve("x" * 30 + "\ud800"), 401)
        self.assert_auth_error(lambda: self.auth.check_csrf(raw, "x" * 30 + "\ud800"), 403)


if __name__ == "__main__":
    unittest.main()
