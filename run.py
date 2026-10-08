"""Start the isolated local preparation workspace with Python 3.11+."""
from __future__ import annotations

import argparse
import contextlib
from pathlib import Path
import sys
import getpass
import sqlite3
import uuid

from assistant_app.server import create_server
from assistant_app.model import ModelClient, ModelError
from assistant_app import __version__
from assistant_app.auth import AuthService, AuthError
from assistant_app.store import Store


@contextlib.contextmanager
def instance_lock(data_dir: Path):
    """Use an OS-released lock so one data directory has one application."""
    data_dir.mkdir(parents=True, exist_ok=True)
    stream = (data_dir / '.instance.lock').open('a+b')
    acquired = False
    try:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        yield
    finally:
        if acquired:
            stream.seek(0)
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def main():
    parser = argparse.ArgumentParser(description='项目综合AI助理：本地准备工作区')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).parent / '.runtime')
    parser.add_argument('--model-env', type=Path, default=Path(__file__).parent / '.env', help='仅此明确文件的交融模型配置；不会搜索其他目录')
    parser.add_argument('--multi-user', action='store_true', help='本地独立账号与菜单反馈模式；升级前自动备份，旧私有记录不自动归新身份')
    parser.add_argument('--setup-admin', metavar='USERNAME', help='服务停止后，在本机终端交互建立首次管理员；不接收命令行密码')
    parser.add_argument('--backup', action='store_true', help='服务停止后，保存同一数据目录的一致SQLite备份，不迁移数据')
    options = parser.parse_args()
    if not 1 <= options.port <= 65535:
        parser.error('端口须在 1–65535 之间')
    if options.backup and options.setup_admin:
        parser.error('备份与账号引导须分别运行')
    try:
        with instance_lock(options.data_dir.resolve()):
            if options.backup:
                target = options.data_dir.resolve() / 'assistant.sqlite3'
                if not target.is_file():
                    raise OSError('本数据目录没有可备份的数据库')
                directory = target.parent / 'backups'
                directory.mkdir(exist_ok=True)
                with contextlib.closing(sqlite3.connect(target)) as source:
                    version = source.execute('PRAGMA user_version').fetchone()[0]
                    output = directory / f'manual-schema-v{version}-{uuid.uuid4()}.sqlite3'
                    with contextlib.closing(sqlite3.connect(output)) as destination:
                        source.backup(destination)
                        if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or destination.execute('PRAGMA foreign_key_check').fetchone():
                            raise OSError('备份检查未通过，原数据保持不变')
                print('一致备份已保存：' + str(output), flush=True)
                return 0
            if options.setup_admin:
                if not sys.stdin.isatty():
                    print('首次管理员引导须在本机交互终端运行，密码不接受管道或命令行。', file=sys.stderr)
                    return 1
                from assistant_app.menu import MENU_SCHEMA
                store = Store(options.data_dir.resolve())
                store.enable_identity(MENU_SCHEMA)
                password = getpass.getpass('管理员密码（至少12字符）：')
                confirmation = getpass.getpass('再次输入：')
                if password != confirmation:
                    print('两次密码不一致，未创建账号。', file=sys.stderr)
                    return 1
                AuthService(store).bootstrap(options.setup_admin, password)
                password = confirmation = None
                print('首次管理员已建立。启动 --multi-user 后本人登录；账号管理员不自动拥有业务职责。', flush=True)
                return 0
            model = ModelClient.from_env_file(options.model_env)
            server = create_server(host='127.0.0.1', port=options.port,
                                   data_dir=options.data_dir.resolve(),
                                   web_dir=Path(__file__).parent / 'web', model_client=model, multi_user=options.multi_user)
            print(f'项目综合AI助理 v{__version__} · 本地准备工作区\nhttp://127.0.0.1:{options.port}\n交融模型配置状态：{model.status()["reason"]}', flush=True)
            try:
                server.serve_forever(poll_interval=0.2)
            except KeyboardInterrupt:
                print('\n正在保存并停止服务。', flush=True)
            finally:
                server.server_close()
    except ModelError as error:
        print('启动失败：' + error.message, file=sys.stderr)
        return 1
    except AuthError as error:
        print('账号引导未完成：' + error.message, file=sys.stderr)
        return 1
    except OSError as error:
        print(f'启动失败：{error}。请确认端口空闲、数据目录可写且未被另一实例使用。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
