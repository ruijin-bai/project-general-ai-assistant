"""Six-table SQLite store with revision, idempotency and stop fences."""

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextvars import ContextVar
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .preparation import DATE_FIELDS, DOMAINS, initial_facts, readiness
from .calculations import CalculationError, summarize_rows, validate_rows

OWNER = "local-preview"
_ACTOR = ContextVar('assistant_actor', default=None)
KINDS = {"user_text", "wechat_text", "oral_note", "excel_excerpt"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def uid():
    return str(uuid.uuid4())


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class AppError(Exception):
    def __init__(self, code, message, status=422, retryable=False, recovery="检查输入后重试"):
        super().__init__(message)
        self.status = status
        self.payload = dict(code=code, message=message, retryable=retryable, recovery=recovery)


def string(value, name, maximum=20000, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise AppError("invalid_input", f"{name}须为非空文字，最多{maximum}字")
    return value.strip()


SCHEMA = """
CREATE TABLE matters(id TEXT PRIMARY KEY, owner TEXT NOT NULL, goal_text TEXT NOT NULL,
 domain TEXT NOT NULL, version INTEGER NOT NULL,
 revision_no INTEGER NOT NULL, control_epoch INTEGER NOT NULL, assistant_status TEXT NOT NULL,
 preparation_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE revisions(matter_id TEXT NOT NULL REFERENCES matters(id), revision_no INTEGER NOT NULL,
 fields TEXT NOT NULL, recorded_at TEXT NOT NULL, confirmed_by TEXT, PRIMARY KEY(matter_id,revision_no));
CREATE TABLE sources(id TEXT PRIMARY KEY, matter_id TEXT NOT NULL REFERENCES matters(id),
 kind TEXT NOT NULL, text TEXT NOT NULL, reported_by TEXT, recorded_by TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE TABLE artifacts(id TEXT NOT NULL, version INTEGER NOT NULL, matter_id TEXT NOT NULL REFERENCES matters(id),
 type TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL,
 input_revision INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(id,version));
CREATE TABLE actions(id TEXT PRIMARY KEY, matter_id TEXT REFERENCES matters(id), kind TEXT NOT NULL,
 status TEXT NOT NULL, idempotency_key TEXT UNIQUE, fingerprint TEXT, response TEXT,
 input_revision INTEGER, control_epoch INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
 lease_until TEXT, error TEXT, created_at TEXT NOT NULL);
CREATE TABLE events(id TEXT PRIMARY KEY, matter_id TEXT NOT NULL REFERENCES matters(id),
 event_type TEXT NOT NULL, message TEXT NOT NULL, recorded_by TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE INDEX actions_pending ON actions(status,kind,created_at);
CREATE INDEX sources_matter ON sources(matter_id);
CREATE INDEX events_matter ON events(matter_id,recorded_at);
PRAGMA user_version=2;
"""


class Store:
    def __init__(self, data_dir):
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "assistant.sqlite3"
        self.lock = threading.RLock()
        self.requires_auth = False
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise RuntimeError("未识别数据库结构，保留原文件并停止启动")
                connection.executescript(SCHEMA)
            elif version == 1:
                # Back up the existing six-table database before lifting the domain
                # restriction. Rebuild only matters; identifiers and histories stay intact.
                backup_dir = data_dir / 'backups'
                backup_dir.mkdir(exist_ok=True)
                backup_path = backup_dir / ('schema-v1-' + uid() + '.sqlite3')
                with closing(sqlite3.connect(backup_path)) as backup:
                    connection.backup(backup)
                connection.execute('PRAGMA foreign_keys=OFF')
                connection.execute('BEGIN IMMEDIATE')
                try:
                    connection.execute('CREATE TABLE matters_new(id TEXT PRIMARY KEY, owner TEXT NOT NULL, goal_text TEXT NOT NULL, domain TEXT NOT NULL, version INTEGER NOT NULL, revision_no INTEGER NOT NULL, control_epoch INTEGER NOT NULL, assistant_status TEXT NOT NULL, preparation_status TEXT NOT NULL, updated_at TEXT NOT NULL)')
                    connection.execute('INSERT INTO matters_new SELECT * FROM matters')
                    connection.execute('DROP TABLE matters')
                    connection.execute('ALTER TABLE matters_new RENAME TO matters')
                    if connection.execute('PRAGMA foreign_key_check').fetchone():
                        raise RuntimeError('迁移引用检查失败，已回滚并保留备份')
                    connection.execute('PRAGMA user_version=2')
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    connection.execute('PRAGMA foreign_keys=ON')
            elif version not in {2, 3, 4, 5}:
                raise RuntimeError("数据库结构版本不兼容，停止启动")
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise RuntimeError("本地预览要求默认 rollback journal；请使用独立数据目录")
            self.requires_auth = connection.execute('PRAGMA user_version').fetchone()[0] >= 3

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
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

    @contextmanager
    def as_actor(self, principal):
        token = _ACTOR.set(principal)
        try:
            yield
        finally:
            _ACTOR.reset(token)

    def actor_id(self):
        principal = self.principal()
        if principal:
            return principal['id']
        if self.requires_auth:
            raise AppError('authentication_required', '请先登录自己的账号', 401)
        return OWNER

    def principal(self):
        principal = _ACTOR.get()
        if self.requires_auth:
            if not principal:
                raise AppError('authentication_required', '请先登录自己的账号', 401)
            with self.connect() as connection:
                row = connection.execute('SELECT state,auth_epoch,capabilities,is_admin FROM accounts WHERE id=?', (principal['id'],)).fetchone()
            if not row or row['state'] != 'active' or row['auth_epoch'] != principal['auth_epoch']:
                raise AppError('authentication_required', '登录或职责已失效，请重新登录', 401)
            principal = {**principal, 'capabilities': json.loads(row['capabilities']), 'is_admin': bool(row['is_admin'])}
        return principal

    def enable_identity(self, additional_schema=''):
        from .auth import AUTH_SCHEMA
        with self.lock, self.connect() as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version == 2:
                backup_dir = self.path.parent / 'backups'
                backup_dir.mkdir(exist_ok=True)
                with closing(sqlite3.connect(backup_dir / ('schema-v2-' + uid() + '.sqlite3'))) as backup:
                    connection.backup(backup)
                connection.execute('BEGIN IMMEDIATE')
                try:
                    for statement in (AUTH_SCHEMA + additional_schema).split(';'):
                        if statement.strip():
                            connection.execute(statement)
                    if connection.execute('SELECT 1 FROM matters WHERE owner=?', (OWNER,)).fetchone():
                        connection.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'disabled','[]',?,'schema-migration')", (OWNER, 'legacy-local-preview', '原单经办预览记录（未核对应身份）', datetime.now(timezone.utc).timestamp()))
                    for column in ['actor_id TEXT', 'auth_epoch INTEGER', 'access_snapshot TEXT']:
                        connection.execute('ALTER TABLE actions ADD COLUMN ' + column)
                    connection.execute("UPDATE actions SET status='cancelled',error='独立身份迁移后须本人核对，未自动继承操作身份',lease_until=NULL WHERE status IN ('queued','running')")
                    connection.execute("UPDATE matters SET assistant_status='waiting' WHERE assistant_status='processing'")
                    if connection.execute('PRAGMA foreign_key_check').fetchone():
                        raise RuntimeError('身份迁移引用检查失败')
                    connection.execute('PRAGMA user_version=3')
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        self.requires_auth = True

    def enable_services(self):
        from .services import SERVICE_SCHEMA
        with self.lock, self.connect() as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version in {4, 5}:
                return
            if version != 3:
                raise RuntimeError('服务增量须在独立身份schema3上升级')
            backup_dir = self.path.parent / 'backups'
            backup_dir.mkdir(exist_ok=True)
            with closing(sqlite3.connect(backup_dir / ('schema-v3-' + uid() + '.sqlite3'))) as backup:
                connection.backup(backup)
            connection.execute('BEGIN IMMEDIATE')
            try:
                for statement in SERVICE_SCHEMA.split(';'):
                    if statement.strip():
                        connection.execute(statement)
                if connection.execute('PRAGMA foreign_key_check').fetchone():
                    raise RuntimeError('服务增量引用检查失败')
                connection.execute('PRAGMA user_version=4')
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def enable_sharing(self, additional_schema):
        with self.lock, self.connect() as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version == 5:
                return
            if version != 4:
                raise RuntimeError('成果分享须在独立身份schema4上升级')
            backup_dir = self.path.parent / 'backups'
            backup_dir.mkdir(exist_ok=True)
            with closing(sqlite3.connect(backup_dir / ('schema-v4-' + uid() + '.sqlite3'))) as backup:
                connection.backup(backup)
            connection.execute('BEGIN IMMEDIATE')
            try:
                for statement in additional_schema.split(';'):
                    if statement.strip():
                        connection.execute(statement)
                if connection.execute('PRAGMA foreign_key_check').fetchone():
                    raise RuntimeError('成果分享引用检查失败')
                connection.execute('PRAGMA user_version=5')
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _queue(self, connection, matter, kind):
        action_id = uid()
        connection.execute('INSERT INTO actions(id,matter_id,kind,status,input_revision,control_epoch,created_at) VALUES(?,?,?,?,?,?,?)', (action_id, matter['id'], kind, 'queued', matter['revision_no'], matter['control_epoch'], now()))
        if self.requires_auth:
            principal = self.principal()
            connection.execute('UPDATE actions SET actor_id=?,auth_epoch=?,access_snapshot=? WHERE id=?', (principal['id'], principal['auth_epoch'], dump({'owner': principal['id']}), action_id))
        if kind=='prepare_model':
            from .private_context import title_snapshot
            context=title_snapshot(connection,self,matter)
            if context is not None:connection.execute('UPDATE actions SET response=? WHERE id=?',(dump({'related_context':context}),action_id))
        return action_id

    def legacy_records(self, data=None):
        principal = self.principal()
        if not self.requires_auth or not principal['is_admin']:
            raise AppError('permission_denied', '仅账号管理员可安排旧预览记录归属', 403)
        with self.transaction() as connection:
            count = connection.execute('SELECT COUNT(*) FROM matters WHERE owner=?', (OWNER,)).fetchone()[0]
            if data is None:
                return {'unassigned_count': count, 'scope': '仅旧预览记录数量，不读取私人正文；需明确指定活跃综合经办认领'}
            if type(data.get('expected_count')) is not int or data['expected_count'] != count:
                raise AppError('version_conflict', '旧记录数量已变化，请读回核对', 409)
            target = connection.execute('SELECT * FROM accounts WHERE id=?', (data.get('target_account_id'),)).fetchone()
            if not target or target['state'] != 'active' or 'clerk' not in json.loads(target['capabilities']):
                raise AppError('invalid_input', '须明确指定已激活的综合经办账号')
            rows = connection.execute('SELECT id FROM matters WHERE owner=?', (OWNER,)).fetchall()
            for row in rows:
                connection.execute('UPDATE matters SET owner=?,control_epoch=control_epoch+1,version=version+1,updated_at=? WHERE id=?', (target['id'], now(), row['id']))
                self._event(connection, row['id'], 'legacy_claimed', dump({'previous_owner': OWNER, 'current_owner': target['id'], 'scope': '只变当前归属，既往操作者、事实、人工稿及声明不追认'}))
            return {'claimed_count': len(rows), 'target_account_id': target['id']}

    def _matter(self, connection, matter_id):
        matter = connection.execute("SELECT * FROM matters WHERE id=? AND owner=?", (matter_id, self.actor_id())).fetchone()
        if not matter:
            raise AppError("not_found", "事项不存在", 404)
        return dict(matter)

    def _event(self, connection, matter_id, kind, message):
        connection.execute("INSERT INTO events VALUES(?,?,?,?,?,?)", (uid(), matter_id, kind, message, self.actor_id(), now()))

    def _bump(self, connection, matter_id, **changes):
        changes["updated_at"] = now()
        columns = ",".join(key + "=?" for key in changes)
        connection.execute(f"UPDATE matters SET version=version+1,{columns} WHERE id=?", (*changes.values(), matter_id))

    def _check_version(self, matter, data):
        version = data.get("expected_version")
        if type(version) is not int or version != matter["version"]:
            raise AppError("version_conflict", "事项已变化，请重新读取后保留你的修改再提交", 409, recovery="刷新事项并核对最新版本；不要直接覆盖")

    def _stored_facts(self, connection, matter):
        return json.loads(connection.execute('SELECT fields FROM revisions WHERE matter_id=? AND revision_no=?', (matter['id'], matter['revision_no'])).fetchone()[0])

    def _detail(self, connection, matter_id):
        matter = self._matter(connection, matter_id)
        stored_facts = self._stored_facts(connection, matter)
        facts = {key: stored_facts.get(key, {'value': None, 'status': 'unknown'}) for key in DOMAINS[matter['domain']]['fields']}
        _, waiting, steps = readiness(matter["domain"], facts)
        artifacts = [dict(row) for row in connection.execute("SELECT a.* FROM artifacts a WHERE matter_id=? AND version=(SELECT MAX(version) FROM artifacts WHERE id=a.id) ORDER BY created_at DESC,a.rowid DESC", (matter_id,))]
        for action in connection.execute("SELECT response FROM actions WHERE matter_id=? AND kind='translate_model' AND status='completed'", (matter_id,)):
            metadata = json.loads(action['response'])
            for artifact in artifacts:
                if artifact['id'] == metadata.get('artifact_id'):
                    basis = metadata['basis']
                    current_basis = connection.execute('SELECT MAX(version) FROM artifacts WHERE id=? AND matter_id=?', (basis['artifact_id'], matter_id)).fetchone()[0]
                    artifact['language_basis'] = {**basis, 'original_stale': current_basis != basis['artifact_version'] or basis['input_revision'] != matter['revision_no'], 'model': metadata['model']}
        for artifact in artifacts:
            artifact["stale"] = artifact["input_revision"] != matter["revision_no"]
        if matter['domain']=='expense':
            from .expense_journey import report_references, reference_current
            references=report_references(connection,matter_id)
            for artifact in artifacts:
                context=references.get(artifact['id'])
                if context and not reference_current(connection,matter['owner'],context):artifact['stale']=True
        result = {key: value for key, value in matter.items() if key != "owner"}
        spec = DOMAINS[matter['domain']]
        result.update(business_status="unknown", facts=facts, legacy_facts={key:value for key,value in stored_facts.items() if key not in spec['fields'] and not key.startswith('__')}, field_labels=spec['fields'], tracks={key: {"status": "unknown", "label": label} for key, label in spec['tracks'].items()}, waiting=waiting, next_steps=steps,
                      sources=[dict(row) for row in connection.execute("SELECT * FROM sources WHERE matter_id=? ORDER BY recorded_at", (matter_id,))],
                      artifacts=artifacts, activities=[dict(row) for row in connection.execute("SELECT * FROM events WHERE matter_id=? ORDER BY recorded_at", (matter_id,))],
                      actions=[dict(row) for row in connection.execute("SELECT id,status,kind,error FROM actions WHERE matter_id=? AND kind IN ('prepare','prepare_model','translate_model','menu_feedback_model') ORDER BY created_at", (matter_id,))])
        evidence = []
        for event in result['activities']:
            if event['event_type'] == 'business_evidence':
                record = json.loads(event['message'])
                record.update(id=event['id'], recorded_by=event['recorded_by'], recorded_at=event['recorded_at'], stale=record['input_revision'] != matter['revision_no'])
                evidence.append(record)
                event['message'] = '已保存人工转录记录：' + spec['tracks'].get(record['track'], record['track']) + ' / ' + record['reported_by'] + '；真实性与实际身份待核，未推进业务状态。'
            elif event['event_type'] == 'model_usage':
                usage = json.loads(event['message'])
                event['message'] = '交融模型实际调用用量：' + dump(usage['usage']) + '（无返回项保持未知；不代表业务结果）'
                if usage.get('discarded'):
                    event['message'] += '；控制、输入或原稿已变化，返回结果未发布。'
                if usage.get('rejected'):
                    event['message'] += '；本响应被合同校验拒绝，未保存候选或成果。'
        result['evidence'] = evidence
        source_states = stored_facts.get('__source_states', {}).get('items', {})
        for source in result['sources']:
            state = source_states.get(source['id'], {})
            source.update(source_state=state.get('state', 'usable'), replacement_source_id=state.get('replacement_source_id'), state_note=state.get('note', ''), preparation_usable=state.get('state', 'usable') == 'usable')
            if source['id'] in stored_facts.get('__source_files', {}):
                source['file_info'] = stored_facts['__source_files'][source['id']]
            if source['id'] in stored_facts.get('__attachment_text', {}):
                source['attachment_text'] = stored_facts['__attachment_text'][source['id']]
        from .related_tasks import origin_current
        result['private_title_context_enabled'] = stored_facts.get('__private_title_context',{}).get('enabled',False)
        result['domain_routing'] = stored_facts.get('__domain_routing', {})
        if result['domain_routing']:
            from .domain_routing import switchable
            result['category_change_allowed']=switchable(connection,matter,stored_facts)
        origin = stored_facts.get('__related_origin')
        origin_stale = not origin_current(connection,matter,stored_facts)
        if origin:
            result['related_origin'] = {**origin,'stale':origin_stale}
            if origin_stale:
                for artifact in artifacts:artifact['stale']=True
                result['waiting'].append({'who':'事项本人','reason':'关联原话或来源已换版，旧准备留作历史，请回原事项核对。'})
        result['model_disclosure_blocked'] = origin_stale or any(source['source_state'] == 'withheld' for source in result['sources'])
        result['conversation_questions'] = []
        if not result['model_disclosure_blocked']:
            row = connection.execute("SELECT response FROM actions WHERE matter_id=? AND kind='prepare_model' AND status='completed' ORDER BY created_at DESC,rowid DESC LIMIT 1", (matter_id,)).fetchone()
            response = json.loads(row['response']) if row and row['response'] else {}
            if response.get('input_revision') == matter['revision_no']:
                result['conversation_questions'] = response.get('questions', [])
        if result['model_disclosure_blocked']:
            result['waiting'].append({'who': '事项本人', 'reason': '本人已暂不使用原文，新准备和模型输入暂缓；原文及人工历史保留，可继续修正。'})
        # Use the same already-authorized connection; no cross-owner reminder view.
        from .waiting import WaitingService
        result['waiting_points'] = WaitingService(self)._view(connection, matter)['items']
        for point in result['waiting_points']:
            if point['active'] and point['state'] == 'waiting':
                result['waiting'].append({key: point[key] for key in ('id', 'who', 'reason', 'next_step', 'due_at', 'due_now')})
        result['workflow'] = stored_facts.get('__workflow', {'enabled': False, 'mode': 'template'})
        result['rows'] = stored_facts.get('__rows', [])
        result['calculation'] = None
        if '__rows' in stored_facts and matter['domain'] in {'expense', 'inventory'}:
            result['calculation'] = summarize_rows(matter['domain'], stored_facts['__rows'])
            result['calculation'].update(input_revision=stored_facts.get('__rows_revision'), stale=stored_facts.get('__rows_revision') != matter['revision_no'])
        return result

    def _continue(self, connection, matter_id):
        matter = self._matter(connection, matter_id)
        workflow = self._stored_facts(connection, matter).get('__workflow', {})
        if any(item.get('state') == 'withheld' for item in self._stored_facts(connection, matter).get('__source_states', {}).get('items', {}).values()):
            return
        if not workflow.get('enabled') or matter['assistant_status'] in {'paused', 'handoff'}:
            return
        if connection.execute("SELECT 1 FROM actions WHERE matter_id=? AND kind='prepare' AND input_revision=? AND control_epoch=? AND status IN ('queued','running','completed')", (matter_id, matter['revision_no'], matter['control_epoch'])).fetchone():
            return
        if connection.execute("SELECT COUNT(*) FROM actions WHERE matter_id=? AND kind='prepare'", (matter_id,)).fetchone()[0] >= 20:
            self._event(connection, matter_id, 'continuation_limit', '本事项有限自动整理已达20次，停止自动推进；已有内容保留，可人工准备。')
            return
        self._queue(connection, matter, 'prepare')
        self._bump(connection, matter_id, assistant_status='processing')
        self._event(connection, matter_id, 'auto_prepare_queued', '根据委托继续本地模板整理；等待期间不循环调用，不自动调用模型或外部渠道。')

    def detail(self, matter_id):
        with self.lock, self.connect() as connection:
            return self._detail(connection, matter_id)

    def search_sources(self, query):
        query = string(query, '原文关键词', 120)
        matches, limited = [], False
        with self.lock, self.connect() as connection:
            owner = self.actor_id()
            rows = connection.execute('SELECT s.*,m.goal_text,m.domain FROM sources s JOIN matters m ON m.id=s.matter_id WHERE m.owner=? ORDER BY s.recorded_at DESC', (owner,))
            for row in rows:
                for index, line in enumerate(row['text'].splitlines(), 1):
                    if query.casefold() not in line.casefold():
                        continue
                    if len(matches) >= 30:
                        limited = True
                        break
                    matches.append(dict(matter_id=row['matter_id'], source_id=row['id'], domain=row['domain'], goal_text=row['goal_text'][:180], kind=row['kind'], start_line=index, end_line=index, excerpt=line[:2000], excerpt_truncated=len(line)>2000, reported_by=row['reported_by'], recorded_by=row['recorded_by'], recorded_at=row['recorded_at']))
                if limited:
                    break
        return dict(items=matches, limited=limited, scope='仅检索本人事项已提供的原文；未读取附件、微信群、在线表或他人资料，不据搜索结果判断依据效力。')

    def worklist(self):
        with self.lock, self.connect() as connection:
            rows = connection.execute("SELECT id,goal_text,domain,version,assistant_status,preparation_status,updated_at FROM matters WHERE owner=? ORDER BY updated_at DESC", (self.actor_id(),)).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                detail = self._detail(connection, item["id"])
                item.update(waiting=detail["waiting"], next_steps=detail["next_steps"], reminders=[point for point in detail['waiting_points'] if point['active'] and point['state'] == 'waiting' and point['due_now']])
                items.append(item)
            return dict(items=items, as_of=now(), scope="本地独立账号的私有事项" if self.requires_auth else "本机单经办预览；无独立账号隔离")

    def mutate(self, operation, object_id, data, key):
        key = string(key, "Idempotency-Key", 200)
        actor_id = self.actor_id()
        if self.requires_auth and not set(self.principal()['capabilities']) & {'employee', 'clerk', 'cook'}:
            raise AppError('permission_denied', '当前账号未获私人准备工作区职责', 403)
        fingerprint = hashlib.sha256(dump([actor_id, operation, object_id, data]).encode()).hexdigest()
        with self.transaction() as connection:
            previous = connection.execute("SELECT * FROM actions WHERE idempotency_key=?", (key,)).fetchone()
            if previous:
                self._matter(connection, previous['matter_id'])
                compatible_fingerprints = {fingerprint}
                if actor_id == OWNER and not self.requires_auth:
                    compatible_fingerprints.add(hashlib.sha256(dump([operation, object_id, data]).encode()).hexdigest())
                if previous["fingerprint"] not in compatible_fingerprints:
                    raise AppError("idempotency_conflict", "此请求键已用于不同内容", 409, recovery="使用新的请求键，先核对原请求结果")
                if operation in {"goal","category"}:
                    return self._detail(connection, previous["matter_id"])
                return json.loads(previous["response"])
            matter_id = object_id
            if operation == "create":
                goal = string(data.get("goal_text"), "目标", 12000)
                domain = data.get("domain", "general")
                auto_domain = domain == 'auto'
                if auto_domain:domain = 'general'
                if not isinstance(domain, str) or domain not in DOMAINS:
                    raise AppError("invalid_input", "不支持此业务类型")
                matter_id = uid()
                connection.execute("INSERT INTO matters VALUES(?,?,?,?,?,?,?,?,?,?)", (matter_id, actor_id, goal, domain, 1, 1, 0, "idle", "draft", now()))
                auto_prepare = data.get('auto_prepare', False)
                if type(auto_prepare) is not bool:
                    raise AppError('invalid_input', '自动本地整理须为明确布尔值')
                initial_mode = data.get('initial_mode', 'template')
                if not isinstance(initial_mode, str) or initial_mode not in {'template', 'model'}:
                    raise AppError('invalid_input', '首次整理方式须为 template 或 model')
                if initial_mode == 'model' and auto_prepare:
                    raise AppError('invalid_input', '首次模型整理与持续本地模板整理须分别委托，不能隐式启用。')
                initial = initial_facts(domain)
                initial['__workflow'] = {'enabled': auto_prepare, 'mode': 'template'}
                if auto_domain:initial['__domain_routing'] = {'mode':'auto','state':'pending'}
                connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (matter_id, 1, dump(initial), now(), None))
                self._source(connection, matter_id, data.get("source_kind", "user_text"), goal, data.get("reported_by"))
                self._event(connection, matter_id, "created", "已保存目标。规则模板可用；模型须配置并显式选择，外部业务渠道未接入。")
                if initial_mode == 'model':
                    self._queue(connection, self._matter(connection, matter_id), 'prepare_model')
                    self._bump(connection, matter_id, assistant_status='processing')
                    self._event(connection, matter_id, 'prepare_queued', '本人在目标入口明确选择交融首次整理；仅发送本事项文字，后续不自动调用模型。')
                else:
                    self._continue(connection, matter_id)
            elif operation == "artifact":
                artifact = connection.execute("SELECT * FROM artifacts WHERE id=? ORDER BY version DESC LIMIT 1", (object_id,)).fetchone()
                if not artifact:
                    raise AppError("not_found", "成果不存在", 404)
                matter_id = artifact["matter_id"]
                self._matter(connection, matter_id)
                if type(data.get("base_version")) is not int or data["base_version"] != artifact["version"]:
                    raise AppError("version_conflict", "成果已有新版本，请读回后合并修改", 409, recovery="刷新成果，保留你的正文并核对差异")
                content = string(data.get("edited_content"), "成果正文", 50000)
                connection.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)", (object_id, artifact["version"]+1, matter_id, artifact["type"], artifact["title"], content, "human_saved", artifact["input_revision"], now()))
                self._bump(connection, matter_id)
                self._event(connection, matter_id, "artifact_saved", "人工修改已保存为成果新版本；不代表外部提交或接受。")
            else:
                matter = self._matter(connection, matter_id)
                self._check_version(matter, data)
                if operation == 'category':
                    from .domain_routing import correct
                    correct(connection,self,matter,data)
                elif operation == "manual_artifact":
                    if set(data) != {"expected_version", "input_revision", "edited_content"}:
                        raise AppError("invalid_input", "人工稿只接受当前基版、输入修订和本人正文。")
                    if type(data["input_revision"]) is not int or data["input_revision"] != matter["revision_no"]:
                        raise AppError("version_conflict", "输入修订已变化，请保留正文并读回当前事项。", 409)
                    content = string(data["edited_content"], "人工稿正文", 50000)
                    saved_artifact_id = uid()
                    connection.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)", (saved_artifact_id, 1, matter_id,
                        "manual_preparation", DOMAINS[matter["domain"]]["label"] + "人工稿", content, "human_saved", matter["revision_no"], now()))
                    self._bump(connection, matter_id)
                    self._event(connection, matter_id, "manual_artifact_created", "本人主动保存人工准备稿；未运行模型或自动整理，暂停/接手及真实业务状态保持。")
                elif operation == "goal":
                    if set(data) != {"expected_version", "goal_text", "recheck_fields"}:
                        raise AppError("invalid_input", "目标修正只接受基版、新目标和本人选择的待重核字段。")
                    goal = string(data["goal_text"], "修正目标", 12000)
                    fields = data["recheck_fields"]
                    if not isinstance(fields, list) or any(not isinstance(field, str) for field in fields) or len(fields) != len(set(fields)) or set(fields) - set(DOMAINS[matter["domain"]]["fields"]):
                        raise AppError("invalid_input", "待重核项须为本事项已有事实字段，不改变业务类型或实际操作状态。")
                    if goal == matter["goal_text"]:
                        raise AppError("unchanged_goal", "委托目标未变化，请直接核对事实或继续准备。")
                    facts = self._stored_facts(connection, matter)
                    for field in fields:
                        item = facts.get(field, {"value": None, "status": "unknown"})
                        facts[field] = {**item, "status": "candidate" if item.get("value") else "unknown", "recheck_reason": "goal_revised"}
                    previous_source = facts.get("__goal_source_id")
                    if not previous_source:
                        first = connection.execute("SELECT id,text FROM sources WHERE matter_id=? ORDER BY rowid LIMIT 1", (matter_id,)).fetchone()
                        if first and first["text"] == matter["goal_text"]:
                            previous_source = first["id"]
                    source_id = self._source(connection, matter_id, "user_text", goal, None)
                    revision, stamp = matter["revision_no"] + 1, now()
                    states = dict(facts.get("__source_states", {}).get("items", {}))
                    if previous_source and states.get(previous_source, {}).get("state", "usable") == "usable":
                        states[previous_source] = {"state": "superseded", "replacement_source_id": source_id, "note": "本人明确修正委托目标，原目标来源保留作历史。", "changed_by": actor_id, "changed_at": stamp}
                    # Withheld/explicitly replaced sources retain their existing controls.
                    facts["__source_states"] = {"schema_version": 1, "input_revision": revision, "items": states}
                    facts["__goal_source_id"] = source_id
                    connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (matter_id, revision, dump(facts), stamp, actor_id))
                    self._cancel(connection, matter_id, "本人修正委托目标，停止旧输入的准备动作")
                    status = matter["assistant_status"] if matter["assistant_status"] in {"paused", "handoff"} else "waiting"
                    self._bump(connection, matter_id, goal_text=goal, revision_no=revision, control_epoch=matter["control_epoch"] + 1, assistant_status=status, preparation_status="needs_review")
                    self._event(connection, matter_id, "goal_revised", dump({"before_goal": matter["goal_text"], "after_goal": goal, "recheck_fields": fields, "source_id": source_id, "previous_source_id": previous_source,
                                "input_revision": revision, "scope": "本人修正当前委托；仅所选事实待重核，人工稿与原文保留，不撤销或改变实际业务状态"}))
                    self._continue(connection, matter_id)
                elif operation == "facts":
                    changes = data.get("field_changes")
                    if not isinstance(changes, dict) or not changes or set(changes)-set(DOMAINS[matter['domain']]['fields']):
                        raise AppError("invalid_input", "仅允许修改本事项已列出的事实字段")
                    facts = self._stored_facts(connection, matter)
                    for field, value in changes.items():
                        if value is not None:
                            value = string(value, field, 2000)
                            if field in DATE_FIELDS:
                                try:
                                    if not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|T| )", value):
                                        raise ValueError()
                                    datetime.fromisoformat(value)
                                except ValueError:
                                    raise AppError("date_unconfirmed", "请提供明确年月日或 ISO 日期时间；不将相对日期确认为真")
                        facts[field] = {"value": value, "status": "confirmed" if value is not None else "unknown"}
                    departure = facts.get('departure_at', {}).get('value') if facts.get('departure_at', {}).get('status') == 'confirmed' else None
                    returning = facts.get('return_at', {}).get('value') if facts.get('return_at', {}).get('status') == 'confirmed' else None
                    if departure and returning:
                        # Mixed precision still proves reversal across local dates.
                        # Aware vs naive cannot establish a common time meaning.
                        start, end = datetime.fromisoformat(departure), datetime.fromisoformat(returning)
                        same_precision = (len(departure) == 10) == (len(returning) == 10)
                        same_zone_semantics = (start.tzinfo is None) == (end.tzinfo is None)
                        if same_zone_semantics and ((same_precision and end < start) or
                                                    (not same_precision and end.date() < start.date())):
                            raise AppError("time_conflict", "计划返营不能早于同口径的计划离营，请核对日期时间")
                    if matter['domain'] == 'leave':
                        start = facts.get('leave_start', {}).get('value') if facts.get('leave_start', {}).get('status') == 'confirmed' else None
                        end = facts.get('leave_end', {}).get('value') if facts.get('leave_end', {}).get('status') == 'confirmed' else None
                        if start and end and end[:10] < start[:10]:
                            raise AppError('time_conflict', '休假结束不能早于开始，请核对明确日期')
                    revision = matter["revision_no"] + 1
                    connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (matter_id, revision, dump(facts), now(), actor_id))
                    self._cancel(connection, matter_id, "事实变更，旧准备动作停止")
                    status = matter["assistant_status"] if matter["assistant_status"] in {"paused", "handoff"} else "waiting"
                    self._bump(connection, matter_id, revision_no=revision, preparation_status="needs_review", assistant_status=status)
                    self._event(connection, matter_id, "facts_confirmed", "已保存当前操作者确认的事实修订；旧成果保留并标记需重核。")
                    self._continue(connection, matter_id)
                elif operation == "message":
                    if set(data) != {'expected_version', 'text', 'mode'}:
                        raise AppError('invalid_input', '对话补充只接受当前基版、本人文字和明确整理方式。')
                    mode = data['mode']
                    if not isinstance(mode, str) or mode not in {'save', 'template', 'model'}:
                        raise AppError('invalid_input', '须明确选择只保存、本地整理或交融整理。')
                    if mode != 'save':
                        from .structured import StructuredService
                        StructuredService(self)._ensure_preparation_sources(connection, matter_id)
                        if matter['assistant_status'] in {'paused', 'handoff'}:
                            raise AppError('assistant_stopped', '已暂停或人工接手；可以只保存补充，恢复后再整理。', 409)
                    text = string(data['text'], '本人对话补充', 12000)
                    source_id = self._source(connection, matter_id, 'user_text', text, None)
                    revision = matter['revision_no'] + 1
                    connection.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (matter_id, revision, dump(self._stored_facts(connection, matter)), now(), actor_id))
                    self._cancel(connection, matter_id, '对话补充已保存，旧输入的整理停止')
                    status = matter['assistant_status'] if matter['assistant_status'] in {'paused', 'handoff'} else 'waiting'
                    self._bump(connection, matter_id, revision_no=revision, control_epoch=matter['control_epoch']+1, assistant_status=status, preparation_status='needs_review')
                    self._event(connection, matter_id, 'conversation_message', dump({'source_id': source_id, 'input_revision': revision, 'mode': mode, 'scope': '本人补充原话；不自动确认事实、替换目标或登记实际执行'}))
                    if mode != 'save':
                        self._queue(connection, self._matter(connection, matter_id), 'prepare_model' if mode == 'model' else 'prepare')
                        self._bump(connection, matter_id, assistant_status='processing')
                        self._event(connection, matter_id, 'prepare_queued', '本人在对话中明确发送并交融整理；使用当前获许可事项上下文，原人工稿保留。' if mode == 'model' else '本人在对话中明确发送并本地整理；原人工稿保留，不调用模型。')
                    # Save-only is explicit even when an older template workflow is enabled.
                elif operation == "source":
                    source_id = self._source(connection, matter_id, data.get("kind"), data.get("text"), data.get("reported_by"))
                    # Source changes advance the input revision and fence any old generation.
                    facts = self._stored_facts(connection, matter)
                    if 'file_info' in data:
                        info = data['file_info']
                        if not isinstance(info, dict) or set(info) != {'name', 'format', 'reported_bytes', 'text_edited'}:
                            raise AppError('invalid_file_info', '文件来源声明字段不符合当前文字导入范围。')
                        name = string(info['name'], '本人所选文件名', 200)
                        if any(char in name for char in ('/', '\\', '\x00', '\r', '\n')) or not isinstance(info['format'], str) or info['format'] not in {'txt', 'md', 'csv', 'tsv'}:
                            raise AppError('invalid_file_info', '仅保存纯文件名与TXT/MD/CSV/TSV文字格式，不读取其他路径。')
                        if type(info['reported_bytes']) is not int or not 0 <= info['reported_bytes'] <= 48003 or type(info['text_edited']) is not bool:
                            raise AppError('invalid_file_info', '文件字节数与文字更改状态须明确填写，不猜测读取范围。')
                        facts.setdefault('__source_files', {})[source_id] = {**info, 'name': name,
                            'saved_text_bytes': len(string(data.get('text'), '来源文字', 20000).encode('utf-8')),
                            'verification': 'user_file_selection_statement', 'scope': '本人选择文字文件的声明；仅保存当前文字，不保存或核真原附件。'}
                    revision = matter["revision_no"]+1
                    connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (matter_id, revision, dump(facts), now(), None))
                    self._cancel(connection, matter_id, "来源新增，旧准备动作停止")
                    status = matter["assistant_status"] if matter["assistant_status"] in {"paused", "handoff"} else "waiting"
                    self._bump(connection, matter_id, revision_no=revision, preparation_status="needs_review", assistant_status=status)
                    self._event(connection, matter_id, "source_added", "新增人工来源引用，未读取群聊或同步在线表。")
                    self._continue(connection, matter_id)
                elif operation == 'translate':
                    from .translation import LANGUAGES, MAX_SOURCE_CHARS
                    from .structured import StructuredService
                    if set(data) != {'expected_version', 'input_revision', 'artifact_id', 'artifact_version', 'target_language'}:
                        raise AppError('invalid_input', '语言稿仅接受当前基版、输入修订、明确原稿版本和目标语言。')
                    if type(data['input_revision']) is not int or data['input_revision'] != matter['revision_no']:
                        raise AppError('version_conflict', '输入修订已变化，请保留原稿并读回核对。', 409)
                    language = data['target_language']
                    if not isinstance(language, str) or language not in LANGUAGES:
                        raise AppError('invalid_input', '当前须明确选择中文或English，不按国籍推断语言。')
                    if not isinstance(data['artifact_id'], str) or type(data['artifact_version']) is not int:
                        raise AppError('invalid_input', '须选择已保存的明确原稿版本。')
                    artifact = connection.execute('SELECT * FROM artifacts WHERE id=? AND matter_id=? ORDER BY version DESC LIMIT 1', (data['artifact_id'], matter_id)).fetchone()
                    if not artifact:
                        raise AppError('not_found', '所选原稿不存在。', 404)
                    if artifact['version'] != data['artifact_version'] or artifact['input_revision'] != matter['revision_no']:
                        raise AppError('version_conflict', '原稿版本或事实依据已变化，须核对并保存当前稿后再准备语言稿。', 409)
                    if len(artifact['content']) > MAX_SOURCE_CHARS:
                        raise AppError('input_limit', '原稿超过本次4000字符范围，请另存所需段落；不自动截断。')
                    StructuredService(self)._ensure_preparation_sources(connection, matter_id)
                    if matter['assistant_status'] in {'paused', 'handoff', 'processing'}:
                        raise AppError('assistant_stopped', '请恢复后再登记语言稿；已有整理须先完成或暂停。', 409)
                    basis = {'artifact_id': artifact['id'], 'artifact_version': artifact['version'], 'input_revision': matter['revision_no'], 'target_language': language}
                    previous = connection.execute("SELECT response FROM actions WHERE matter_id=? AND kind='translate_model' AND status='completed'", (matter_id,)).fetchall()
                    if not any(json.loads(row['response']).get('basis') == basis for row in previous):
                        action_id = self._queue(connection, matter, 'translate_model')
                        connection.execute('UPDATE actions SET response=? WHERE id=?', (dump({'basis': basis}), action_id))
                        self._bump(connection, matter_id, assistant_status='processing')
                        self._event(connection, matter_id, 'translate_queued', '本人明确委托'+LANGUAGES[language]+'语言稿；仅向许可交融发送所选已保存正文，未保存编辑不发送。原稿版本 '+str(artifact['version'])+'。')
                elif operation == "prepare":
                    from .structured import StructuredService
                    StructuredService(self)._ensure_preparation_sources(connection, matter_id)
                    if data.get("kind") != "prepare":
                        raise AppError("contract_unconfirmed", "本轮仅支持准备，不允许批准、派车、扣餐或外部操作")
                    if matter["assistant_status"] in {"paused", "handoff"}:
                        raise AppError("assistant_stopped", "代办已暂停或人工接手，请先恢复", 409, recovery="恢复后再登记准备任务")
                    mode = data.get('mode', 'template')
                    if not isinstance(mode, str) or mode not in {'template', 'model'}:
                        raise AppError('invalid_input', '整理方式须为 template 或 model')
                    action_kind = 'prepare_model' if mode == 'model' else 'prepare'
                    existing = connection.execute("SELECT id FROM actions WHERE matter_id=? AND kind=? AND input_revision=? AND control_epoch=? AND status IN ('queued','running','completed')", (matter_id, action_kind, matter["revision_no"], matter["control_epoch"])).fetchone()
                    if not existing:
                        self._queue(connection, matter, action_kind)
                        self._bump(connection, matter_id, assistant_status="processing")
                        self._event(connection, matter_id, "prepare_queued", "已登记交融模型准备；只向许可路线发送本事项必要文字，不执行业务。" if mode == 'model' else "规则模板准备已登记；不会调用模型或执行正式业务。")
                elif operation == "control":
                    command = data.get("command")
                    if not isinstance(command, str) or command not in {"pause", "resume", "handoff"}:
                        raise AppError("invalid_input", "未知控制命令")
                    self._cancel(connection, matter_id, "用户控制停止旧动作")
                    self._bump(connection, matter_id, control_epoch=matter["control_epoch"]+1, assistant_status={"pause": "paused", "handoff": "handoff", "resume": "waiting"}[command])
                    self._event(connection, matter_id, command, {"pause": "未来代办已暂停，已保存事实与成果保留。", "handoff": "已停止未来代办并交由人工接手。", "resume": "已恢复；请按需重新登记准备，不重放外部动作。"}[command])
                    if command == 'resume':
                        self._continue(connection, matter_id)
                elif operation == 'workflow':
                    enabled = data.get('enabled')
                    if type(enabled) is not bool:
                        raise AppError('invalid_input', '持续本地整理须为明确布尔值')
                    facts = self._stored_facts(connection, matter)
                    facts['__workflow'] = {'enabled': enabled, 'mode': 'template'}
                    # Workflow settings do not revise business facts or invalidate a saved draft.
                    connection.execute('UPDATE revisions SET fields=? WHERE matter_id=? AND revision_no=?', (dump(facts), matter_id, matter['revision_no']))
                    self._bump(connection, matter_id)
                    self._event(connection, matter_id, 'workflow_changed', '已启用补充后持续本地整理。' if enabled else '已关闭后续自动本地整理；已登记动作可用暂停控制停止。')
                    self._continue(connection, matter_id)
                elif operation == "event":
                    kind = data.get("event_type")
                    if not isinstance(kind, str) or kind not in {"note", "copied"}:
                        raise AppError("contract_unconfirmed", "仅可登记备注或复制声明，不可登记正式批准/执行结果")
                    self._event(connection, matter_id, kind, string(data.get("message"), "声明", 6000))
                    self._bump(connection, matter_id)
                elif operation == 'evidence':
                    track = data.get('track')
                    record_type = data.get('record_type')
                    if not isinstance(track, str) or track not in DOMAINS[matter['domain']]['tracks']:
                        raise AppError('invalid_input', '记录必须属于本事项独立业务轨道')
                    if not isinstance(record_type, str) or record_type not in {'statement', 'receipt_reference', 'review_note'}:
                        raise AppError('invalid_input', '仅允许人工转录声明、凭据引用或核对备注')
                    source_id = string(data.get('source_id'), '来源ID', 100)
                    if not connection.execute('SELECT id FROM sources WHERE id=? AND matter_id=?', (source_id, matter_id)).fetchone():
                        raise AppError('invalid_input', '来源须属于本事项，不允许引用其他事项资料')
                    occurred = string(data.get('occurred_at'), '实际陈述或记录发生日期时间', 100)
                    try:
                        if not re.match(r'^\d{4}-\d{2}-\d{2}(?:$|T| )', occurred):
                            raise ValueError()
                        datetime.fromisoformat(occurred)
                    except ValueError:
                        raise AppError('date_unconfirmed', '请注明明确日期时间，不猜相对日期')
                    record = dict(track=track, record_type=record_type, source_id=source_id,
                                  source_position=string(data.get('source_position'), '原记录位置', 200),
                                  reported_by=string(data.get('reported_by'), '实际陈述人', 200),
                                  occurred_at=occurred, text=string(data.get('text'), '声明或凭据引用原话', 6000),
                                  input_revision=matter['revision_no'], verification='unverified_transcription')
                    self._event(connection, matter_id, 'business_evidence', dump(record))
                    self._bump(connection, matter_id)
                elif operation == 'rows':
                    try:
                        rows = validate_rows(matter['domain'], data.get('rows'), self._detail(connection, matter_id)['sources'])
                    except CalculationError as error:
                        raise AppError('invalid_rows', str(error)) from None
                    if rows:
                        from .structured import StructuredService
                        StructuredService(self)._ensure_preparation_sources(connection, matter_id, [row['source_id'] for row in rows])
                    facts = self._stored_facts(connection, matter)
                    revision = matter['revision_no'] + 1
                    facts.update(__rows=rows, __rows_revision=revision)
                    connection.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (matter_id, revision, dump(facts), now(), actor_id))
                    self._cancel(connection, matter_id, '已核明细变化，停止旧准备动作')
                    status = matter['assistant_status'] if matter['assistant_status'] in {'paused', 'handoff'} else 'waiting'
                    self._bump(connection, matter_id, revision_no=revision, preparation_status='needs_review', assistant_status=status)
                    self._event(connection, matter_id, 'rows_confirmed', '已保存本地人工核对明细并由程序分别合计；不代表报销批准、支付、收发或权威库更新。')
                    self._continue(connection, matter_id)
                else:
                    raise AppError("not_found", "接口不存在", 404)
            response = self._detail(connection, matter_id)
            if operation == "manual_artifact":
                response["saved_artifact_id"] = saved_artifact_id
            connection.execute("INSERT INTO actions(id,matter_id,kind,status,idempotency_key,fingerprint,response,created_at) VALUES(?,?,?,?,?,?,?,?)", (uid(), matter_id, "request:"+operation, "completed", key, fingerprint, dump(response), now()))
            return response

    def _source(self, connection, matter_id, kind, text, reported_by):
        if not isinstance(kind, str) or kind not in KINDS:
            raise AppError("invalid_input", "来源类型不支持")
        source_id = uid()
        connection.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?)", (source_id, matter_id, kind, string(text, "来源文字", 20000), string(reported_by, "陈述人", 200, optional=True), self.actor_id(), now()))
        return source_id

    def _cancel(self, connection, matter_id, message):
        connection.execute("UPDATE actions SET status='cancelled',error=?,lease_until=NULL WHERE matter_id=? AND kind IN ('prepare','prepare_model','translate_model','menu_feedback_model') AND status IN ('queued','running')", (message, matter_id))

    def download_rows(self, matter_id, input_revision):
        with self.lock, self.connect() as connection:
            matter = self._matter(connection, matter_id)
            if matter['domain'] not in {'expense', 'inventory'}:
                raise AppError('invalid_domain', '只有已人工核对的费用与物资明细可导出。')
            if type(input_revision) is not int or input_revision < 1:
                raise AppError('invalid_revision', '须指定已保存明细的明确修订。')
            revision = connection.execute('SELECT fields FROM revisions WHERE matter_id=? AND revision_no=?', (matter_id, input_revision)).fetchone()
            if not revision:
                raise AppError('not_found', '所选明细修订不存在。', 404)
            facts = json.loads(revision['fields'])
            if facts.get('__rows_revision') != input_revision:
                raise AppError('not_found', '该修订不是人工保存明细的修订，不导出当前草稿。', 404)
            return {'domain': matter['domain'], 'input_revision': input_revision, 'rows': facts['__rows'], 'source_files': facts.get('__source_files', {})}

    def download(self, artifact_id, version=None):
        if version is not None and (type(version) is not int or version < 1):
            raise AppError('invalid_input', '成果版本须为正整数')
        with self.lock, self.connect() as connection:
            if version is None:
                row = connection.execute("SELECT * FROM artifacts WHERE id=? ORDER BY version DESC LIMIT 1", (artifact_id,)).fetchone()
            else:
                row = connection.execute('SELECT * FROM artifacts WHERE id=? AND version=?', (artifact_id, version)).fetchone()
            if not row:
                raise AppError("not_found", "成果不存在", 404)
            self._matter(connection, row["matter_id"])
            return row["content"]
