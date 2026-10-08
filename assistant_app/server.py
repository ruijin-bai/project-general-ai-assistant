"""Loopback-only local preview HTTP service and one bounded durable executor."""

import json
import mimetypes
import re
import secrets
import sqlite3
import threading
from http.cookies import SimpleCookie
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qs, quote

from . import __version__
from .preparation import DOMAINS, prepare
from .model import ModelClient, ModelError
from .store import AppError, Store, dump, now, uid
from .auth import AuthService, AuthError
from .structured import StructuredService
from .journey import JourneyService
from .checklist import ChecklistService
from .sharing import SHARING_SCHEMA, ShareService
from .exports import docx_bytes, rows_csv_bytes
from .waiting import WaitingService
from .context import ContextService
from .readings import UtilitiesService
from .source_lifecycle import SourceLifecycleService
from .extraction import ExtractionService
from .tabular import TabularService
from .source_impact import SourceImpactService
from .reminders import ReminderPreferencesService
from .attachments import AttachmentService
from .material_records import MaterialRecordsService
from .material_nodes import MaterialNodesService
from .row_reports import RowReportsService
from .menu_model import MenuModelService
from .meal_summaries import MealSummaryService
from .expense_journey import ExpenseJourneyService
from .related_tasks import RelatedTasksService
from .private_context import PrivateContextService,title_snapshot,snapshot_current


class Runner:
    def __init__(self, store, model):
        self.store = store
        self.model = model
        self.stopped = threading.Event()
        with store.transaction() as connection:
            # A model request may have reached the provider before interruption.
            # Never replay it implicitly on startup.
            interrupted = connection.execute("SELECT matter_id FROM actions WHERE kind IN ('prepare_model','translate_model','menu_feedback_model') AND status='running'").fetchall()
            connection.execute("UPDATE actions SET status='failed',error='模型调用被中断，未自动重试；请人工核对后重新准备',lease_until=NULL WHERE kind IN ('prepare_model','translate_model','menu_feedback_model') AND status='running'")
            for row in interrupted:
                store._bump(connection, row['matter_id'], assistant_status='waiting')
                connection.execute('INSERT INTO events VALUES(?,?,?,?,?,?)', (uid(), row['matter_id'], 'model_interrupted', '模型调用被中断；不自动重放，输入和人工成果保留。', 'system-recovery', now()))
            # There are no external actions: only deterministic local preparation.
            connection.execute("UPDATE actions SET status='queued',lease_until=NULL WHERE kind='prepare' AND status='running' AND attempts<3")
            exhausted = connection.execute("SELECT matter_id FROM actions WHERE kind='prepare' AND status IN ('queued','running') AND attempts>=3").fetchall()
            connection.execute("UPDATE actions SET status='failed',error='恢复次数已达上限，请人工接手',lease_until=NULL WHERE kind='prepare' AND status IN ('queued','running') AND attempts>=3")
            for row in exhausted:
                store._bump(connection, row["matter_id"], assistant_status="waiting")
        self.thread = threading.Thread(target=self.run, name="preparation-runner", daemon=True)
        self.thread.start()

    def run(self):
        while not self.stopped.is_set():
            try:
                if not self.step():
                    self.stopped.wait(0.2)
            except sqlite3.Error:
                # Do not spin or claim persistence succeeded; pending work remains durable.
                self.stopped.wait(1)

    def step(self):
        if not self.store.requires_auth:
            return self._step()
        with self.store.connect() as connection:
            action = connection.execute("SELECT * FROM actions WHERE kind IN ('prepare','prepare_model','translate_model','menu_feedback_model') AND status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not action:
                # Recover deterministic leases without borrowing another actor.
                with self.store.transaction() as write:
                    write.execute("UPDATE actions SET status='queued',lease_until=NULL WHERE kind='prepare' AND status='running' AND attempts<3 AND lease_until<=?", (now(),))
                return False
            account = connection.execute('SELECT * FROM accounts WHERE id=?', (action['actor_id'],)).fetchone()
        if not account or account['state'] != 'active' or account['auth_epoch'] != action['auth_epoch']:
            self._reject_actor(action)
            return True
        principal = dict(id=account['id'], auth_epoch=account['auth_epoch'], capabilities=json.loads(account['capabilities']), is_admin=bool(account['is_admin']))
        try:
            with self.store.as_actor(principal):
                return self._step()
        except AppError:
            self._reject_actor(action)
            return True

    def _reject_actor(self, action):
        with self.store.transaction() as connection:
            connection.execute("UPDATE actions SET status='cancelled',error='操作身份或权限已失效，未发布结果',lease_until=NULL WHERE id=? AND status IN ('queued','running')", (action['id'],))
            self.store._bump(connection, action['matter_id'], assistant_status='waiting')

    def _step(self):
        store = self.store
        with store.transaction() as connection:
            # A transient storage error may leave a claimed step running. Recover only
            # after its bounded lease; deterministic local preparation has no external side effects.
            expired = connection.execute("SELECT id,matter_id,attempts FROM actions WHERE kind='prepare' AND status='running' AND lease_until<=?", (now(),)).fetchall()
            for item in expired:
                if item["attempts"] >= 3:
                    connection.execute("UPDATE actions SET status='failed',error='恢复次数已达上限，请人工接手',lease_until=NULL WHERE id=?", (item["id"],))
                    store._bump(connection, item["matter_id"], assistant_status="waiting")
                    connection.execute('INSERT INTO events VALUES(?,?,?,?,?,?)', (uid(), item['matter_id'], 'prepare_failed', '有限恢复次数已达上限，请人工接手。', 'system-recovery', now()))
                else:
                    connection.execute("UPDATE actions SET status='queued',lease_until=NULL WHERE id=?", (item["id"],))
            row = connection.execute("SELECT * FROM actions WHERE kind IN ('prepare','prepare_model','translate_model','menu_feedback_model') AND status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return False
            action = dict(row)
            detail = store._detail(connection, action["matter_id"])
            if detail["control_epoch"] != action["control_epoch"] or detail["revision_no"] != action["input_revision"] or detail["assistant_status"] != "processing":
                connection.execute("UPDATE actions SET status='cancelled',error='输入或控制状态已改变' WHERE id=?", (action["id"],))
                return True
            if detail.get('related_origin') and detail.get('model_disclosure_blocked'):
                connection.execute("UPDATE actions SET status='cancelled',error='关联原话已变化，未准备或发送',lease_until=NULL WHERE id=?",(action['id'],))
                store._bump(connection,action['matter_id'],assistant_status='waiting')
                return True
            title_context = json.loads(action['response']).get('related_context') if action['kind']=='prepare_model' and action['response'] else None
            if not snapshot_current(connection,store,store._matter(connection,action['matter_id']),title_context):
                connection.execute("UPDATE actions SET status='cancelled',error='标题引用许可或已有事项已变化，未发送',lease_until=NULL WHERE id=?",(action['id'],))
                store._bump(connection,action['matter_id'],assistant_status='waiting')
                return True
            if title_context is not None:detail['_related_context']=title_context
            translation_basis = None
            menu_basis = None
            if action['kind'] == 'menu_feedback_model':
                menu_basis = json.loads(action['response'])['basis']
                try:
                    _, model_input = MenuModelService(store)._basis_current(connection, menu_basis)
                except AppError:
                    connection.execute("UPDATE actions SET status='cancelled',error='菜单或共享意见已变化，未发送旧原话',lease_until=NULL WHERE id=?", (action['id'],))
                    store._bump(connection, action['matter_id'], assistant_status='waiting')
                    return True
                detail = {'_menu_feedback_input': model_input}
            if action['kind'] == 'translate_model':
                basis = json.loads(action['response'])['basis']
                artifact = connection.execute('SELECT * FROM artifacts WHERE id=? AND matter_id=? ORDER BY version DESC LIMIT 1', (basis['artifact_id'], action['matter_id'])).fetchone()
                if not artifact or artifact['version'] != basis['artifact_version'] or detail.get('model_disclosure_blocked'):
                    connection.execute("UPDATE actions SET status='cancelled',error='原稿版本或来源引用范围已变化' WHERE id=?", (action['id'],))
                    store._bump(connection, action['matter_id'], assistant_status='waiting')
                    return True
                translation_basis = {**basis, 'content': artifact['content']}
                detail = {**detail, '_translation_basis': translation_basis}
            lease = (datetime.now(timezone.utc)+timedelta(seconds=90)).isoformat()
            connection.execute("UPDATE actions SET status='running',attempts=attempts+1,lease_until=? WHERE id=?", (lease, action["id"]))
        try:
            model_result = None
            if action['kind'] in {'prepare_model', 'translate_model', 'menu_feedback_model'}:
                model_result = self.model.prepare(detail)
                content = model_result['content']
                preparation_state = 'needs_review'
            else:
                content, preparation_state = prepare(detail)  # No transaction during work.
        except Exception as error:
            message = error.message if isinstance(error, ModelError) else '准备失败，请人工接手'
            with store.transaction() as connection:
                current = connection.execute("SELECT status FROM actions WHERE id=?", (action["id"],)).fetchone()
                if current and (current['status'] == 'running' or translation_basis or menu_basis) and isinstance(error, ModelError) and error.usage is not None:
                    store._event(connection, action['matter_id'], 'model_usage', dump({'model': self.model.model, 'usage': error.usage, 'action_id': action['id'], 'replayed': False, 'rejected': True, 'discarded': current['status'] != 'running'}))
                if current and current["status"] == "running":
                    connection.execute("UPDATE actions SET status='failed',error=?,lease_until=NULL WHERE id=?", (message, action["id"]))
                    store._bump(connection, action["matter_id"], assistant_status="waiting")
                    store._event(connection, action["matter_id"], "prepare_failed", message + '；输入与已保存成果保留。')
            return True
        with store.transaction() as connection:
            current = connection.execute("SELECT status FROM actions WHERE id=?", (action["id"],)).fetchone()
            if menu_basis and model_result:
                matter = dict(connection.execute('SELECT * FROM matters WHERE id=?', (action['matter_id'],)).fetchone())
                invalid = current['status'] != 'running' or self.stopped.is_set() or matter['control_epoch'] != action['control_epoch'] or matter['revision_no'] != action['input_revision'] or matter['assistant_status'] != 'processing'
                try:
                    MenuModelService(store)._basis_current(connection, menu_basis)
                except AppError:
                    invalid = True
                store._event(connection, matter['id'], 'model_usage', dump({'model': model_result['model'], 'usage': model_result['usage'], 'action_id': action['id'], 'replayed': False, 'discarded': invalid}))
                if invalid:
                    if current['status'] == 'running':
                        connection.execute("UPDATE actions SET status='cancelled',error='意见共享、依据或控制已变化，候选未发布；不自动重试',lease_until=NULL WHERE id=?", (action['id'],))
                        if matter['assistant_status'] == 'processing':
                            store._bump(connection, matter['id'], assistant_status='waiting')
                    return True
                connection.execute("UPDATE actions SET status='completed',response=?,lease_until=NULL WHERE id=?", (dump({'basis': menu_basis, 'content': model_result['content'], 'review': model_result['review'], 'model': model_result['model']}), action['id']))
                store._bump(connection, matter['id'], assistant_status='waiting')
                store._event(connection, matter['id'], 'menu_review_completed', '所选自愿意见归纳及回应候选已保存；语义与安全待本人核对，不自动采用、回应或修改菜单。')
                return True
            matter = store._matter(connection, action["matter_id"])
            if translation_basis and model_result:
                basis = json.loads(action['response'])['basis']
                latest = connection.execute('SELECT MAX(version) FROM artifacts WHERE id=? AND matter_id=?', (basis['artifact_id'], matter['id'])).fetchone()[0]
                invalid = (current['status'] != 'running' or self.stopped.is_set() or latest != basis['artifact_version'] or matter['control_epoch'] != action['control_epoch'] or matter['revision_no'] != action['input_revision'] or matter['assistant_status'] != 'processing')
                store._event(connection, matter['id'], 'model_usage', dump({'model': model_result['model'], 'usage': model_result['usage'], 'action_id': action['id'], 'replayed': False, 'discarded': invalid}))
                if invalid:
                    if current['status'] == 'running':
                        connection.execute("UPDATE actions SET status='cancelled',error='原稿、事实或控制已变化，语言稿未发布；未自动重试',lease_until=NULL WHERE id=?", (action['id'],))
                        if matter['assistant_status'] == 'processing':
                            store._bump(connection, matter['id'], assistant_status='waiting')
                    return True
                from .translation import LANGUAGES
                artifact_id = uid()
                connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)', (artifact_id, 1, matter['id'], 'language_draft', LANGUAGES[basis['target_language']]+'语言准备稿', model_result['content'], 'model_draft', action['input_revision'], now()))
                connection.execute("UPDATE actions SET status='completed',response=?,lease_until=NULL WHERE id=?", (dump({'basis': basis, 'artifact_id': artifact_id, 'model': model_result['model']}), action['id']))
                store._bump(connection, matter['id'], assistant_status='waiting', preparation_status='needs_review')
                store._event(connection, matter['id'], 'translate_completed', '语言稿已另存，数字／日期／币种原值校验通过；翻译含义、职责与立场待本人核对。原稿、事实和真实业务状态保留，未外发。')
                return True
            from .related_tasks import origin_current
            if not origin_current(connection,matter,store._stored_facts(connection,matter)) or not snapshot_current(connection,store,matter,title_context):
                if model_result:
                    store._event(connection,matter['id'],'model_usage',dump({'model':model_result['model'],'usage':model_result['usage'],'action_id':action['id'],'replayed':False,'discarded':True}))
                if current['status']=='running':
                    connection.execute("UPDATE actions SET status='cancelled',error='关联原话已变化，结果未发布；不自动重试',lease_until=NULL WHERE id=?",(action['id'],))
                    store._bump(connection,matter['id'],assistant_status='waiting')
                return True
            if current["status"] != "running":
                if model_result:
                    store._event(connection, matter['id'], 'model_usage', dump({'model': model_result['model'], 'usage': model_result['usage'], 'action_id': action['id'], 'replayed': False, 'discarded': True}))
                return True
            if self.stopped.is_set():
                connection.execute("UPDATE actions SET status=?,error=?,lease_until=NULL WHERE id=?", ('failed' if model_result else 'queued', '模型服务已停止，未自动重试' if model_result else None, action['id']))
                return True
            if matter["control_epoch"] != action["control_epoch"] or matter["revision_no"] != action["input_revision"] or matter["assistant_status"] != "processing":
                connection.execute("UPDATE actions SET status='cancelled',error='旧输入已失效，未发布结果',lease_until=NULL WHERE id=?", (action["id"],))
                return True
            revision = action['input_revision']
            if model_result:
                facts = {**detail['facts'], **store._stored_facts(connection, matter)}
                changed = False
                for field, value in model_result['candidates'].items():
                    if field in facts and facts[field]['status'] != 'confirmed':
                        facts[field] = {'value': value, 'status': 'candidate', 'model': model_result['model'], 'action_id': action['id']}
                        changed = True
                from .domain_routing import apply_initial
                changed=apply_initial(connection,store,matter,facts,model_result.get('preparation_domain'),action['id']) or changed
                if changed:
                    revision += 1
                    connection.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (matter['id'], revision, dump(facts), now(), None))
                content += '\n\n待核问题：\n' + '\n'.join('- ' + question for question in model_result['questions']) if model_result['questions'] else ''
                store._event(connection, matter['id'], 'model_usage', dump({'model': model_result['model'], 'usage': model_result['usage'], 'action_id': action['id'], 'replayed': False}))
            connection.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)", (uid(), 1, matter["id"], "preparation", "交融模型准备稿" if model_result else DOMAINS[matter['domain']]['label'] + '准备稿', content, "model_draft" if model_result else "template_draft", revision, now()))
            connection.execute("UPDATE actions SET status='completed',lease_until=NULL WHERE id=?", (action["id"],))
            collection = {'meeting': 'meeting_items', 'document': 'document_sections', 'expense': 'row_candidates', 'inventory': 'row_candidates', 'hr': 'check_candidates', 'leave': 'check_candidates', 'travel': 'journey_candidates'}.get(matter['domain'])
            if model_result:
                response = {'input_revision': revision, 'model': model_result['model'], 'questions': model_result.get('questions', []), 'related_tasks': [{**item, 'id': 'rc-' + action['id'].replace('-', '') + '-' + str(index)} for index, item in enumerate(model_result.get('related_tasks', []), 1)]}
                response['related_context']=title_context
                response['related_matches']=[{**item,'id':'rm-'+action['id'].replace('-','')+'-'+str(index)} for index,item in enumerate(model_result.get('related_matches',[]),1)]
                if collection and collection in model_result:
                    response[collection] = [{**item, 'id': 'mc-' + action['id'].replace('-', '') + '-' + str(index)} for index, item in enumerate(model_result[collection], 1)]
                connection.execute('UPDATE actions SET response=? WHERE id=?', (dump(response), action['id']))
            store._bump(connection, matter["id"], revision_no=revision, assistant_status="waiting", preparation_status=preparation_state)
            store._event(connection, matter["id"], "prepare_completed", "交融模型候选与准备稿已保存；待人工核对，真实业务状态未改变。" if model_result else "规则模板准备稿已保存；真实业务状态未改变。")
        return True

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=6)


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_close(self):
        if getattr(self, "runner", None):
            self.runner.close()
        super().server_close()

    close = server_close


class Handler(BaseHTTPRequestHandler):
    server_version = "PreparationPreview/0.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, fmt, *args):
        pass  # Never log user-provided URLs, facts or source content.

    def _guards(self, modifying=False):
        host = self.headers.get("Host", "")
        try:
            parsed = urlsplit("//"+host)
            valid = parsed.hostname in ({'127.0.0.1'} if self.server.auth else {"localhost", "127.0.0.1"}) and parsed.port == self.server.server_port and not parsed.username and not parsed.path and not parsed.query and not parsed.fragment
        except ValueError:
            valid = False
        if not valid:
            raise AppError("local_only", "仅允许本机预览访问", 403)
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed = urlsplit(origin)
                valid = parsed.scheme == "http" and parsed.hostname == urlsplit('//'+host).hostname and parsed.port == self.server.server_port and not parsed.username and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
            except ValueError:
                valid = False
            if not valid:
                raise AppError("origin_rejected", "来源未获允许", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise AppError("origin_rejected", "不允许跨站访问", 403)
        if modifying:
            token = self.headers.get("X-CSRF-Token", "")
            if self.server.auth:
                self.server.auth.check_csrf(self._cookie(), token)
                return
            if not secrets.compare_digest(token.encode(), self.server.csrf_token.encode()):
                raise AppError("csrf_rejected", "预览会话校验失败", 403, recovery="重新获取本机预览会话再提交")

    def _cookie(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            return cookie['assistant_session'].value if 'assistant_session' in cookie else ''
        except Exception:
            return ''

    def _actor(self):
        if not self.server.auth:
            return None
        session = self.server.auth.resolve(self._cookie())
        if not session['authenticated']:
            raise AppError('authentication_required', '请登录自己的账号', 401)
        return session['principal']

    def _set_cookie(self, raw):
        self._response_cookie = 'assistant_session=' + raw + '; HttpOnly; SameSite=Strict; Path=/'

    def _send(self, status, content, mime="application/json; charset=utf-8", extra=None):
        if mime.startswith("application/json"):
            content = json.dumps(content, ensure_ascii=False).encode()
        elif isinstance(content, str):
            content = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        if getattr(self, '_response_cookie', None):
            self.send_header('Set-Cookie', self._response_cookie)
            self._response_cookie = None
        self.end_headers()
        self.wfile.write(content)

    def _error(self, error):
        if isinstance(error, AuthError):
            error = AppError(error.code, error.message, error.status)
        self._send(error.status, error.payload)

    def _body(self, maximum=131072):
        if self.headers.get("Transfer-Encoding"):
            raise AppError("invalid_input", "不支持分块请求体", 400)
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise AppError("invalid_input", "无效请求长度", 400)
        if not 0 < size <= maximum:
            raise AppError("invalid_input", "请求体为空或超过当前接口限制（附件3MiB，其他128KiB）", 413)
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise AppError("invalid_input", "须使用 application/json", 415)
        try:
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeDecodeError):
            raise AppError("invalid_input", "无效JSON对象", 400)

    def do_GET(self):
        try:
            path = unquote(urlsplit(self.path).path)
            public = path in {'/api/session', '/api/health'} or not path.startswith('/api/')
            principal = None if public else self._actor()
            with self.server.store.as_actor(principal):
                self._get()
        except (AppError, AuthError) as error:
            self._error(error)

    def _get(self):
        try:
            self._guards()
            path = unquote(urlsplit(self.path).path)
            parts = path.strip("/").split("/")
            if path == "/api/session":
                state = self.server.model.status()
                result = dict(csrf_token=self.server.csrf_token, mode="local_preview", model_configured=state['configured'], model_status=state, app_version=__version__, sqlite_version=sqlite3.sqlite_version)
                if self.server.auth:
                    try:
                        session = self.server.auth.resolve(self._cookie())
                    except AuthError:
                        raw, session = self.server.auth.new_anonymous()
                        self._set_cookie(raw)
                    result = {**session, 'mode': 'isolated_multiuser', 'app_version': __version__}
                    if session['authenticated']:
                        result.update(model_status=state, model_configured=state['configured'])
                    else:
                        with self.server.store.connect() as connection:
                            result['bootstrap_required'] = not bool(connection.execute("SELECT 1 FROM auth_meta WHERE key='bootstrap_admin'").fetchone())
            elif path == '/api/catalog':
                result = {'domains': DOMAINS}
            elif path == "/api/health":
                result = dict(healthy=True, mode='isolated_multiuser' if self.server.auth else "local_preview")
            elif path == '/api/admin/accounts' and self.server.auth:
                principal = self._actor()
                if not principal['is_admin']:
                    raise AppError('permission_denied', '仅账号管理员可查看账号', 403)
                with self.server.store.connect() as connection:
                    result = {'items': [dict(id=row['id'], username=row['username_key'], display_name=row['display_name'], capabilities=json.loads(row['capabilities']), is_admin=bool(row['is_admin']), auth_epoch=row['auth_epoch'], state=row['state']) for row in connection.execute('SELECT * FROM accounts ORDER BY created_at')]}
            elif path == '/api/admin/legacy' and self.server.auth:
                result = self.server.store.legacy_records()
            elif path == '/api/menu-publications' and self.server.menu:
                result = self.server.menu.list_publications()
            elif len(parts) == 4 and parts[:2] == ['api', 'menu-publications'] and parts[3] == 'model-review' and self.server.menu_model:
                result = self.server.menu_model.read(parts[2])
            elif len(parts) == 4 and parts[:2] == ['api', 'menu-publications'] and parts[3] == 'feedback' and self.server.menu:
                result = self.server.menu.list_feedback(parts[2])
            elif len(parts) == 3 and parts[:2] == ['api', 'menu-feedback'] and self.server.menu:
                result = self.server.menu.get_own_feedback(parts[2])
            elif path == '/api/service-accounts' and self.server.services:
                try:
                    query = parse_qs(urlsplit(self.path).query, max_num_fields=5)
                except ValueError:
                    raise AppError('invalid_input', '查询参数过多') from None
                result = self.server.services.eligible_accounts(query.get('capability', [None])[0])
            elif path == '/api/services' and self.server.services:
                result = self.server.services.list_services()
            elif len(parts) == 3 and parts[:2] == ['api', 'services'] and self.server.services:
                result = self.server.services.get_service(parts[2])
            elif len(parts) == 4 and parts[:2] == ['api', 'services'] and parts[3] == 'draft' and self.server.services:
                try:
                    query = parse_qs(urlsplit(self.path).query, max_num_fields=1)
                except ValueError:
                    raise AppError('invalid_input', '仅允许指定一个历史稿版本。') from None
                if set(query) - {'version'}:
                    raise AppError('invalid_input', '仅允许指定历史稿版本。')
                version = query.get('version', [None])[0]
                if version is not None and (not re.fullmatch(r'[1-9][0-9]{0,6}', version) or int(version) > 1000000):
                    raise AppError('invalid_input', '历史稿版本须为有效正整数。')
                result = self.server.services.get_draft(parts[2], int(version) if version is not None else None)
            elif path == '/api/share-recipients' and self.server.sharing:
                result = {'items': self.server.sharing.eligible_recipients(), 'scope': 'read_preparation'}
            elif path == '/api/shares' and self.server.sharing:
                result = self.server.sharing.list_received()
            elif len(parts) == 3 and parts[:2] == ['api', 'shares'] and self.server.sharing:
                result = self.server.sharing.get_share(parts[2])
            elif len(parts) == 4 and parts[:2] == ['api', 'matters'] and parts[3] == 'shares' and self.server.sharing:
                result = self.server.sharing.list_owned(parts[2])
            elif len(parts) == 4 and parts[:2] == ['api', 'shares'] and parts[3] == 'download' and self.server.sharing:
                try:
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=2)
                except ValueError:
                    raise AppError('invalid_input', '分享下载参数过多') from None
                if set(query) - {'version', 'format'} or any(len(value) != 1 for value in query.values()):
                    raise AppError('invalid_input', '分享下载只接受一个明确版本和txt/md格式')
                version_text = query.get('version', [''])[0]
                file_format = query.get('format', ['txt'])[0]
                if not version_text.isascii() or not version_text.isdecimal() or len(version_text) > 9 or int(version_text) < 1 or file_format not in {'txt', 'md'}:
                    raise AppError('invalid_input', '请指定分享版本正整数和txt/md格式')
                return self._send(200, self.server.sharing.download_share(parts[2], int(version_text)), ('text/markdown' if file_format == 'md' else 'text/plain') + '; charset=utf-8', {'Content-Disposition': f'attachment; filename="shared-preparation-v{version_text}.{file_format}"'})
            elif len(parts) == 4 and parts[:2] == ['api', 'matters'] and parts[3] == 'attachments':
                result = self.server.attachments.get_attachments(parts[2])
            elif len(parts) == 6 and parts[:2] == ['api', 'matters'] and parts[3] == 'attachments' and parts[5] == 'text':
                try:
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=1)
                except ValueError:
                    raise AppError('invalid_input', '正文读取只接受一个明确工作表名称。')
                if set(query) - {'sheet_name'} or any(len(value) != 1 for value in query.values()):
                    raise AppError('invalid_input', '正文读取只接受一个明确工作表名称。')
                result = self.server.attachments.preview_text(parts[2], parts[4], query.get('sheet_name', [None])[0])
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['meeting', 'candidates']:
                result = self.server.structured.get_meeting_candidates(parts[2])
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['document', 'candidates']:
                result = self.server.structured.get_document_candidates(parts[2])
            elif len(parts) == 6 and parts[:2] == ['api', 'matters'] and parts[3] == 'attachments' and parts[5] == 'download':
                original = self.server.attachments.download(parts[2], parts[4])
                disposition = "attachment; filename=original-attachment; filename*=UTF-8''" + quote(original['filename'], safe='')
                return self._send(200, original['content'], 'application/octet-stream', {'Content-Disposition': disposition})
            elif path == "/api/worklist":
                result = self.server.store.worklist()
            elif path == '/api/context':
                result = self.server.context.get_context()
            elif path == '/api/reminder-preferences':
                result = self.server.reminder_preferences.get_preferences()
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['leave', 'material-nodes']:
                result = self.server.material_nodes.get_candidates(parts[2])
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['leave', 'material-records']:
                result = self.server.material_records.get_records(parts[2])
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['related-tasks','context']:
                result = self.server.private_context.preview(parts[2])
            elif len(parts) == 4 and parts[:2] == ['api', 'matters'] and parts[3] == 'related-tasks':
                result = self.server.related_tasks.read(parts[2])
            elif len(parts) == 4 and parts[:2] == ['api', 'matters'] and parts[3] == 'model-input':
                try:
                    with self.server.store.connect() as c:
                        matter=self.server.store._matter(c,parts[2]);detail=self.server.store._detail(c,parts[2]);context=title_snapshot(c,self.server.store,matter)
                        if context is not None:detail['_related_context']=context
                        result = self.server.model.preview_input(detail)
                except ModelError as error:
                    raise AppError(error.code, error.message, 409 if error.code == 'source_withheld' else 422) from None
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['facts', 'candidates']:
                result = self.server.extraction.get_candidates(parts[2])
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['travel', 'model-candidates']:
                result = self.server.journey.get_model_candidates(parts[2])
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['rows', 'model-candidates']:
                result = self.server.structured.get_row_candidates(parts[2])
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['rows', 'reports']:
                result = self.server.row_reports.get_inputs(parts[2])
            elif len(parts) == 4 and parts[:2] == ['api', 'matters'] and parts[3] == 'meal-summaries':
                result = self.server.meal_summaries.inputs(parts[2])
            elif len(parts) == 4 and parts[:2] == ['api', 'matters'] and parts[3] == 'expense-journey':
                result = self.server.expense_journey.inputs(parts[2])
            elif len(parts) == 7 and parts[:2] == ['api', 'matters'] and parts[3:5] == ['rows', 'reports'] and parts[6] == 'csv':
                snapshot = self.server.row_reports.csv_snapshot(parts[2], parts[5])
                return self._send(200, rows_csv_bytes(snapshot), 'text/csv; charset=utf-8', {'Content-Disposition': 'attachment; filename="selected-report-rows.csv"'})
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['rows', 'candidates']:
                try:
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=1)
                except ValueError:
                    raise AppError('invalid_input', '表格候选只接受一个来源ID') from None
                if set(query) != {'source_id'} or len(query['source_id']) != 1:
                    raise AppError('invalid_input', '请指定本事项一个实际来源ID')
                result = self.server.tabular.get_candidates(parts[2], query['source_id'][0])
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['rows', 'download']:
                try:
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=1)
                except ValueError:
                    raise AppError('invalid_input', '明细下载只接受一个固定修订') from None
                value = query.get('input_revision', [''])[0]
                if set(query) != {'input_revision'} or len(query['input_revision']) != 1 or not value.isascii() or not value.isdecimal() or len(value) > 9 or int(value) < 1:
                    raise AppError('invalid_input', '请指定已保存明细修订的正整数')
                snapshot = self.server.store.download_rows(parts[2], int(value))
                return self._send(200, rows_csv_bytes(snapshot), 'text/csv; charset=utf-8', {'Content-Disposition': f'attachment; filename="saved-rows-r{value}.csv"'})
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['sources', 'state']:
                result = self.server.source_lifecycle.get_sources(parts[2])
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['sources', 'impact']:
                result = self.server.source_impact.get_impact(parts[2])
            elif path == '/api/sources/search':
                try:
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=1)
                except ValueError:
                    raise AppError('invalid_input', '原文检索只接受一个关键词') from None
                if set(query) != {'q'} or len(query['q']) != 1:
                    raise AppError('invalid_input', '请提供一个明确原文关键词')
                result = self.server.store.search_sources(query['q'][0])
            elif len(parts) == 4 and parts[:2] == ['api', 'matters'] and parts[3] == 'journey':
                result = self.server.journey.get_journey(parts[2])
            elif len(parts) == 4 and parts[:2] == ['api', 'matters'] and parts[3] == 'checks':
                result = self.server.checklist.get_checks(parts[2])
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['checks', 'model-candidates']:
                result = self.server.checklist.get_model_candidates(parts[2])
            elif len(parts) == 4 and parts[:2] == ['api', 'matters'] and parts[3] == 'waiting':
                result = self.server.waiting.get_waiting(parts[2])
            elif len(parts) == 6 and parts[:2] == ['api', 'matters'] and parts[3] == 'waiting' and parts[5] == 'followup':
                result = self.server.waiting.get_followup(parts[2], parts[4])
            elif len(parts) == 4 and parts[:2] == ['api', 'matters'] and parts[3] == 'readings':
                result = self.server.readings.get_readings(parts[2])
            elif len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[4] == 'structure' and parts[3] in {'meeting', 'document'}:
                if parts[3] == 'meeting':
                    try:
                        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=3)
                    except ValueError:
                        raise AppError('invalid_input', '期限查询参数过多') from None
                    if set(query) - {'as_of_date', 'as_of_datetime', 'display_timezone'} or any(len(values) != 1 for values in query.values()):
                        raise AppError('invalid_input', '期限查询须为明确的日期、时间或显示时区')
                    result = self.server.structured.get_meeting_structure(parts[2], **{key: values[0] for key, values in query.items()})
                else:
                    result = self.server.structured.get_document_structure(parts[2])
            elif len(parts) == 3 and parts[:2] == ["api", "matters"]:
                result = self.server.store.detail(parts[2])
            elif len(parts) == 4 and parts[:2] == ["api", "artifacts"] and parts[3] == "download":
                try:
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=5)
                except ValueError:
                    raise AppError('invalid_input', '下载参数过多') from None
                if set(query) - {'version', 'format'} or any(len(value) != 1 for value in query.values()):
                    raise AppError('invalid_input', '下载只接受单个版本和txt/md/docx格式')
                version_text = query.get('version', [None])[0]
                if version_text is not None and (not version_text.isascii() or not version_text.isdecimal() or len(version_text) > 9 or int(version_text) < 1):
                    raise AppError('invalid_input', '成果版本须为明确正整数')
                version = int(version_text) if version_text is not None else None
                file_format = query.get('format', ['txt'])[0]
                if file_format not in {'txt', 'md', 'docx'}:
                    raise AppError('invalid_input', '本次下载支持真实txt/md/docx准备文件')
                filename = 'preparation' + (f'-v{version}' if version else '') + '.' + file_format
                if file_format == 'docx':
                    if version is None:
                        raise AppError('invalid_input', 'Word下载须指定已保存成果的明确版本')
                    content = self.server.store.download(parts[2], version)
                    return self._send(200, docx_bytes(content), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', {'Content-Disposition': 'attachment; filename="' + filename + '"'})
                return self._send(200, self.server.store.download(parts[2], version), ('text/markdown' if file_format == 'md' else 'text/plain') + '; charset=utf-8', {'Content-Disposition': 'attachment; filename="' + filename + '"'})
            elif path.startswith("/api/"):
                raise AppError("not_found", "接口不存在", 404)
            else:
                if "\\" in path or "\x00" in path:
                    raise AppError("not_found", "文件不存在", 404)
                root = self.server.web_dir.resolve()
                target = (root / (path.lstrip("/") or "index.html")).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    raise AppError("not_found", "文件不存在", 404)
                mime = 'application/javascript' if target.suffix in {'.js', '.mjs'} else (mimetypes.guess_type(target.name)[0] or "application/octet-stream")
                if mime.startswith("text/") or mime == "application/javascript":
                    mime += "; charset=utf-8"
                return self._send(200, target.read_bytes(), mime)
            self._send(200, result)
        except (AppError, AuthError) as error:
            self._error(error)
        except (sqlite3.Error, OSError):
            self._error(AppError("storage_unavailable", "读取暂不可用", 503, True, "稍后重试；保留已有输入"))

    def do_POST(self):
        self._mutation()

    def do_PATCH(self):
        self._mutation()

    def _mutation(self):
        try:
            self._guards(True)
            path = unquote(urlsplit(self.path).path)
            principal = None if path.startswith('/api/auth/') else self._actor()
            with self.server.store.as_actor(principal):
                self._write()
        except (AppError, AuthError) as error:
            self._error(error)

    def _write(self):
        try:
            self._guards(True)
            parts = unquote(urlsplit(self.path).path).strip("/").split("/")
            attachment_upload = self.command == 'POST' and len(parts) == 4 and parts[:2] == ['api', 'matters'] and parts[3] == 'attachments'
            data = self._body(3 * 1024 * 1024 if attachment_upload else 131072)
            if attachment_upload:
                return self._send(201, self.server.attachments.upload(parts[2], data, self.headers.get('Idempotency-Key')))
            if self.command == 'POST' and len(parts) == 6 and parts[:2] == ['api', 'matters'] and parts[3] == 'attachments' and parts[5] == 'text':
                return self._send(201, self.server.attachments.save_text(parts[2], parts[4], data, self.headers.get('Idempotency-Key')))
            status = 200
            if self.command == 'POST' and parts == ['api', 'context']:
                return self._send(200, self.server.context.save_context(data, self.headers.get('Idempotency-Key')))
            if self.command == 'POST' and parts == ['api', 'reminder-preferences']:
                return self._send(200, self.server.reminder_preferences.save_preferences(data, self.headers.get('Idempotency-Key')))
            if self.server.auth and self.command == 'POST':
                auth = self.server.auth
                csrf = self.headers.get('X-CSRF-Token', '')
                if parts == ['api', 'auth', 'login']:
                    raw, session = auth.login(self._cookie(), csrf, data.get('username'), data.get('password'), self.client_address[0])
                    self._set_cookie(raw)
                    return self._send(200, {**session, 'mode': 'isolated_multiuser', 'model_status': self.server.model.status(), 'model_configured': self.server.model.status()['configured']})
                if parts == ['api', 'auth', 'logout']:
                    raw, session = auth.logout(self._cookie(), csrf)
                    self._set_cookie(raw)
                    return self._send(200, {**session, 'mode': 'isolated_multiuser'})
                if parts == ['api', 'auth', 'activate']:
                    principal = auth.activate(data.get('token'), data.get('password'), self.client_address[0])
                    raw, _ = auth.rotate(self._cookie(), csrf)
                    self._set_cookie(raw)
                    return self._send(200, {'activated': True, 'username': principal['username']})
                if parts == ['api', 'admin', 'accounts']:
                    return self._send(201, auth.create_account(self._actor(), data.get('username'), data.get('display_name'), data.get('capabilities')))
                if len(parts) == 5 and parts[:3] == ['api', 'admin', 'accounts'] and parts[4] == 'control':
                    return self._send(200, auth.control_account(self._actor(), parts[3], data.get('expected_epoch'), data.get('command'), capabilities=data.get('capabilities')))
                if parts == ['api', 'admin', 'legacy', 'claim']:
                    return self._send(200, self.server.store.legacy_records(data))
                key = self.headers.get('Idempotency-Key')
                if len(parts) == 4 and parts[:2] == ['api', 'matters'] and parts[3] == 'shares':
                    return self._send(201, self.server.sharing.create_share(parts[2], data, key))
                if len(parts) == 4 and parts[:2] == ['api', 'shares'] and parts[3] == 'withdraw':
                    return self._send(200, self.server.sharing.withdraw_share(parts[2], data, key))
                if len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['service', 'submit']:
                    return self._send(201, self.server.services.submit(parts[2], data, key))
                if len(parts) == 4 and parts[:2] == ['api', 'services']:
                    method = {'assign': 'assign', 'respond': 'respond', 'result': 'result', 'revise': 'revise', 'withdraw': 'withdraw', 'statement': 'statement', 'close': 'close', 'draft': 'save_draft'}.get(parts[3])
                    if method:
                        return self._send(200, getattr(self.server.services, method)(parts[2], data, key))
                if len(parts) == 5 and parts[:2] == ['api', 'matters'] and parts[3:] == ['menu', 'publish']:
                    return self._send(201, self.server.menu.publish(parts[2], data, key))
                if len(parts) == 4 and parts[:2] == ['api', 'menu-publications']:
                    if parts[3] == 'model-review':
                        return self._send(201, self.server.menu_model.start(parts[2], data, key))
                    if parts[3] == 'feedback':
                        return self._send(201, self.server.menu.feedback(parts[2], data, key))
                    if parts[3] == 'withdraw':
                        return self._send(200, self.server.menu.withdraw(parts[2], data, key))
                    if parts[3] == 'revise':
                        return self._send(201, self.server.menu.revise(parts[2], data, key))
                if len(parts) == 6 and parts[:2] == ['api', 'menu-publications'] and parts[3] == 'feedback' and parts[5] == 'respond':
                    return self._send(201, self.server.menu.respond_feedback(parts[2], parts[4], data, key))
                if len(parts) == 4 and parts[:2] == ['api', 'menu-feedback']:
                    if parts[3] == 'revise':
                        return self._send(200, self.server.menu.update_feedback(parts[2], data, key))
                    if parts[3] == 'withdraw':
                        return self._send(200, self.server.menu.withdraw_feedback(parts[2], data, key))
            if self.command == 'POST' and parts[:2] == ['api', 'matters']:
                key = self.headers.get('Idempotency-Key')
                if len(parts) == 6 and parts[3] == 'waiting' and parts[5] == 'followup':
                    return self._send(201, self.server.waiting.save_followup(parts[2], parts[4], data, key))
                if len(parts) == 5 and parts[3:] == ['related-tasks', 'context']:
                    return self._send(200,self.server.private_context.save_scope(parts[2],data,key))
                if len(parts) == 5 and parts[3:] == ['related-tasks', 'match']:
                    return self._send(200,self.server.private_context.save_match(parts[2],data,key))
                if len(parts) == 5 and parts[3:] == ['related-tasks', 'withdraw-match']:
                    return self._send(200,self.server.private_context.withdraw(parts[2],data,key))
                if len(parts) == 5 and parts[3:] == ['related-tasks', 'unlink']:
                    return self._send(200, self.server.related_tasks.unlink(parts[2], data, key))
                if len(parts) == 5 and parts[3:] == ['related-tasks', 'create']:
                    return self._send(200, self.server.related_tasks.create(parts[2], data, key))
                if len(parts) == 6 and parts[3:] == ['facts', 'candidates', 'apply']:
                    return self._send(200, self.server.extraction.apply_candidates(parts[2], data, key))
                if len(parts) == 4 and parts[3] == 'checks':
                    return self._send(200, self.server.checklist.save_checks(parts[2], data, key))
                if len(parts) == 4 and parts[3] == 'waiting':
                    return self._send(200, self.server.waiting.save_waiting(parts[2], data, key))
                if len(parts) == 4 and parts[3] == 'readings':
                    return self._send(200, self.server.readings.save_readings(parts[2], data, key))
                if len(parts) == 6 and parts[3] == 'sources' and parts[5] == 'state':
                    return self._send(200, self.server.source_lifecycle.set_source_state(parts[2], parts[4], data, key))
                if len(parts) == 5 and parts[3:] == ['readings', 'render']:
                    return self._send(201, self.server.readings.render_readings(parts[2], data, key))
                if len(parts) == 5 and parts[3:] == ['checks', 'render']:
                    return self._send(201, self.server.checklist.render_checks(parts[2], data, key))
                if len(parts) == 5 and parts[3:] == ['leave', 'material-nodes']:
                    return self._send(200, self.server.material_nodes.bind(parts[2], data, key))
                if len(parts) == 6 and parts[3:] == ['rows', 'reports', 'render']:
                    return self._send(201, self.server.row_reports.render_report(parts[2], data, key))
                if len(parts) == 5 and parts[3:] == ['meal-summaries', 'preview']:
                    return self._send(200, self.server.meal_summaries.preview(parts[2], data))
                if len(parts) == 5 and parts[3:] == ['expense-journey', 'preview']:
                    return self._send(200, self.server.expense_journey.preview(parts[2], data))
                if len(parts) == 5 and parts[3:] == ['expense-journey', 'save']:
                    return self._send(200, self.server.expense_journey.save(parts[2], data, key))
                if len(parts) == 5 and parts[3:] == ['expense-journey', 'unlink']:
                    return self._send(200, self.server.expense_journey.unlink(parts[2], data, key))
                if len(parts) == 5 and parts[3:] == ['meal-summaries', 'render']:
                    return self._send(201, self.server.meal_summaries.render_report(parts[2], data, key))
                if len(parts) == 6 and parts[3:] == ['leave', 'material-dates', 'render']:
                    return self._send(201, self.server.material_nodes.render_dates(parts[2], data, key))
                if len(parts) in {5, 6} and parts[3:5] == ['leave', 'material-records']:
                    method = 'record' if len(parts) == 5 else 'render' if parts[5] == 'render' else None
                    if method is None: raise AppError('not_found', '材料记录接口不存在。', 404)
                    return self._send(200, getattr(self.server.material_records, method)(parts[2], data, self.headers.get('Idempotency-Key')))
                if len(parts) == 5 and parts[3] in {'journey', 'leave'}:
                    method = {('journey', 'plan'): 'save_plan', ('journey', 'meal-requests'): 'save_meals', ('journey', 'movement'): 'record_movement', ('journey', 'render'): 'render', ('leave', 'materials'): 'save_materials', ('leave', 'travel-link'): 'set_travel_link'}.get(tuple(parts[3:]))
                    if method:
                        return self._send(201 if parts[4] == 'render' else 200, getattr(self.server.journey, method)(parts[2], data, key))
                if len(parts) == 5 and parts[4] == 'structure' and parts[3] in {'meeting', 'document'}:
                    method = self.server.structured.save_meeting_structure if parts[3] == 'meeting' else self.server.structured.save_document_structure
                    return self._send(200, method(parts[2], data, key))
                if len(parts) == 7 and parts[3:5] == ['meeting', 'actions'] and parts[6] == 'progress':
                    return self._send(200, self.server.structured.meeting_progress(parts[2], parts[5], data, key))
                if len(parts) == 5 and parts[3:] == ['structure', 'render']:
                    domain = self.server.store.detail(parts[2])['domain']
                    if domain not in {'meeting', 'document'}:
                        raise AppError('invalid_input', '结构成果仅适用于会议或公文')
                    method = self.server.structured.meeting_render if domain == 'meeting' else self.server.structured.document_render
                    return self._send(201, method(parts[2], data, key))
            if self.command == "POST" and parts == ["api", "matters"]:
                if data.get('initial_mode') == 'model' and not self.server.model.status()['configured']:
                    raise AppError('model_unconfigured', '真实交融模型尚未配置就绪，请填写本地许可配置，或使用模板/人工路径。', 409)
                operation, object_id, status = "create", None, 201
            elif self.command == "PATCH" and len(parts) == 3 and parts[:2] == ["api", "artifacts"]:
                operation, object_id = "artifact", parts[2]
            elif self.command == "POST" and len(parts) in {4, 5} and parts[:2] == ["api", "matters"]:
                suffix = "/".join(parts[3:])
                operation = {"goal": "goal", "category": "category", "messages": "message", "facts/confirm": "facts", "sources": "source", "artifacts": "manual_artifact", "actions": "prepare", "language-drafts": "translate", "control": "control", "events": "event", 'evidence': 'evidence', 'rows/confirm': 'rows', 'workflow': 'workflow'}.get(suffix)
                if not operation:
                    raise AppError("not_found", "接口不存在", 404)
                object_id = parts[2]
                status = 202 if operation in {"prepare", "translate"} else 200
            else:
                raise AppError("not_found", "接口不存在", 404)
            if (operation == 'translate' or operation in {'prepare', 'message'} and data.get('mode') == 'model') and not self.server.model.status()['configured']:
                raise AppError('model_unconfigured', '交融模型未启用或配置无效，可选规则模板或人工准备', 409)
            result = self.server.store.mutate(operation, object_id, data, self.headers.get("Idempotency-Key"))
            self._send(status, result)
        except (AppError, AuthError) as error:
            self._error(error)
        except (sqlite3.Error, OSError):
            self._error(AppError("save_unavailable", "保存未确认，请保留输入", 503, True, "用原请求键重试或先读回核对结果"))


def create_server(host="127.0.0.1", port=8765, data_dir=Path(".runtime"), web_dir=Path("web"), model_client=None, multi_user=False):
    if host != "127.0.0.1":
        raise ValueError("本轮仅允许127.0.0.1本地隔离预览")
    store = Store(data_dir)
    with store.connect() as connection:
        multi_user = multi_user or connection.execute('PRAGMA user_version').fetchone()[0] >= 3
    if multi_user:
        from .menu import MENU_SCHEMA, MenuService
        from .services import ServiceService
        store.enable_identity(MENU_SCHEMA)
        store.enable_services()
        store.enable_sharing(SHARING_SCHEMA)
    server = PreviewServer((host, port), Handler)
    server.store = store
    server.auth = AuthService(store) if multi_user else None
    server.menu = MenuService(store) if multi_user else None
    server.services = ServiceService(store) if multi_user else None
    server.sharing = ShareService(store) if multi_user else None
    server.structured = StructuredService(store)
    server.journey = JourneyService(store)
    server.related_tasks = RelatedTasksService(store)
    server.private_context = PrivateContextService(store)
    server.checklist = ChecklistService(store)
    server.waiting = WaitingService(store)
    server.context = ContextService(store)
    server.readings = UtilitiesService(store)
    server.source_lifecycle = SourceLifecycleService(store)
    server.extraction = ExtractionService(store)
    server.tabular = TabularService(store)
    server.source_impact = SourceImpactService(store)
    server.reminder_preferences = ReminderPreferencesService(store)
    server.attachments = AttachmentService(store)
    server.material_records = MaterialRecordsService(store)
    server.material_nodes = MaterialNodesService(store)
    server.row_reports = RowReportsService(store)
    server.meal_summaries = MealSummaryService(store)
    server.expense_journey = ExpenseJourneyService(store)
    server.web_dir = Path(web_dir)
    server.csrf_token = secrets.token_urlsafe(32)
    server.model = model_client or ModelClient()
    server.menu_model = MenuModelService(store, server.model) if multi_user else None
    try:
        server.runner = Runner(store, server.model)
    except Exception:
        server.server_close()
        raise
    return server
