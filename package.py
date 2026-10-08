"""Build an application-only local package; do not copy runtime or credentials."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

from assistant_app import __version__


def main():
    root = Path(__file__).resolve().parent
    sources = [root / name for name in ('run.py', 'start.ps1', 'package.py', '.env.example', 'docs/轻量启动.md')]
    for directory, extensions in (('assistant_app', {'.py'}), ('web', {'.js', '.mjs', '.html', '.css'})):
        sources.extend(path for path in (root / directory).iterdir() if path.is_file() and path.suffix in extensions)
    output_dir = root / 'dist'
    output_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = output_dir / f'assistant-v{__version__}-{stamp}-{uuid.uuid4().hex[:8]}.zip'
    manifest = {'version': __version__, 'created_at': stamp, 'runtime': 'Python 3.11+, standard library only', 'files': []}
    with ZipFile(output, 'x', ZIP_DEFLATED) as archive:
        for source in sorted(sources):
            if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(root):
                raise ValueError('包内只允许本项目固定清单的真实源文件：' + source.name)
            content = source.read_bytes()
            name = source.relative_to(root).as_posix()
            if name == '.env.example':
                expected = {'ASSISTANT_MODEL_ENABLED': 'false', 'ASSISTANT_MODEL_BASE_URL': 'https://c4ai.ccccltd.cn/api/compatible/v1',
                            'ASSISTANT_MODEL_API_KEY': '', 'ASSISTANT_MODEL_NAME': 'jiaorong-instruct'}
                actual = {}
                for line in content.decode('utf-8-sig').splitlines():
                    if not line.strip() or line.lstrip().startswith('#'):
                        continue
                    key, separator, value = line.partition('=')
                    if not separator or key.strip() in actual:
                        raise ValueError('模型示例格式已变化，未打包；请保留真实配置在本地。')
                    actual[key.strip()] = value.strip()
                if actual != expected:
                    raise ValueError('模型示例须保持禁用且无凭据，未打包；真实配置留在本地。')
            archive.writestr(name, content)
            manifest['files'].append({'path': name, 'sha256': hashlib.sha256(content).hexdigest()})
        readme = (f'# 项目综合AI助理 v{__version__}\n\n'
                  '在本目录运行 `python -X utf8 run.py`，打开 http://127.0.0.1:8765。需要Python 3.11或更新版本，无第三方应用依赖。\n\n'
                  '独立账号、停止续办与备份步骤见 [轻量启动](docs/轻量启动.md)。\n\n'
                  '这是本地竞赛开发版本，完整PRD仍持续推进；无内置业务数据或账号，模型默认禁用，未接实际业务渠道。软件记录不代表批准、执行、供餐、签发或成效。\n').encode('utf-8')
        archive.writestr('README.md', readme)
        manifest['files'].append({'path': 'README.md', 'sha256': hashlib.sha256(readme).hexdigest(), 'generated': True})
        archive.writestr('PACKAGE.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('应用包CRC检查未通过，源文件保持不变')
    print('本地应用包：' + str(output))
    print('文件：' + str(len(manifest['files'])) + '；含禁用空配置示例，不含数据库、账号、工程夹具或真实模型配置。')
    print('SHA256：' + hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
