"""Explicit engineering-only browser fixture, never the default runtime or real people.

Creates a new tests/ui-check-v03 directory once. No reset, production seed or model call.
The public test password below is ONLY for these clearly named disposable accounts.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from assistant_app.store import Store
from assistant_app.auth import AuthService
from assistant_app.menu import MENU_SCHEMA

if __name__ == '__main__':
    directory = ROOT / 'tests' / 'ui-check-v03'
    if directory.exists():
        raise SystemExit('工程夹具目录已存在，保留并停止初始化。')
    store = Store(directory)
    store.enable_identity(MENU_SCHEMA)
    auth = AuthService(store)
    password = 'EngineeringOnly-2026!'
    admin = auth.bootstrap('qa-admin', password)
    for username, name, caps in [('qa-cook', '工程夹具厨师', ['cook']), ('qa-employee', '工程夹具员工', ['employee']), ('qa-clerk', '工程夹具经办', ['clerk']), ('qa-executor', '工程夹具执行者', ['executor'])]:
        pending = auth.create_account(admin, username, name, caps)
        auth.activate(pending['activation_token'], password)
    print('独立身份工程夹具已建立，无真实人员或业务数据。')
