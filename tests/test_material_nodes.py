"""Source-bound private date nodes, stale guards and durable human edits."""
from pathlib import Path
import tempfile
import unittest
from assistant_app.store import AppError, Store, uid
from assistant_app.journey import JourneyService, SEMANTICS
from assistant_app.material_nodes import MaterialNodesService
from assistant_app.waiting import WaitingService
from assistant_app.source_lifecycle import SourceLifecycleService


class MaterialNodesSmoke(unittest.TestCase):
    def test_nodes_preserve_edits_stale_basis_and_private_reopen(self):
        for protected in (False, True):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory(prefix='qa-material-nodes-',dir=Path(__file__).resolve().parent) as folder:
                store=Store(folder);actors=[None,None]
                if protected:
                    store.enable_identity()
                    with store.transaction() as c:
                        for i in range(2):
                            aid=uid();c.execute("INSERT INTO accounts(id,username_key,display_name,state,capabilities,created_at,created_by) VALUES(?,?,?,'active','[\"employee\"]',0,'engineering')",(aid,'fixture'+str(i),'工程本人'));actors[i]={'id':aid,'auth_epoch':1}
                nodes=MaterialNodesService(store);journey=JourneyService(store);waiting=WaitingService(store)
                with store.as_actor(actors[0]):
                    m=store.mutate('create',None,{'domain':'leave','goal_text':'工程日期待办，非真实材料或办理','auto_prepare':False},uid());mid=m['id']
                    m=store.mutate('source',mid,{'expected_version':m['version'],'kind':'user_text','text':'工程假设护照材料日期2026-11-30，非真实证件。\n工程假设另一材料原文，非真实材料。'},uid());source=m['sources'][-1]['id']
                    m=store.mutate('manual_artifact',mid,{'expected_version':m['version'],'input_revision':m['revision_no'],'edited_content':'本人旧稿不得覆盖；未办理'},uid());old=m['saved_artifact_id'];tracks=m['tracks']
                    def bind_data(material_id,mode='create',**extra):
                        fresh=store.detail(mid);candidate=next(i for i in nodes.get_candidates(mid)['items'] if i['material_id']==material_id);return {'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'material_id':material_id,'mode':mode,'basis_token':candidate['basis_token'],**({'who':'本人','due_at':'2000-01-01T09:00:00+01:00','note':'工程本人备注'} if mode=='create' else {}),**extra}
                    def save_material(item):
                        fresh=store.detail(mid);return journey.save_materials(mid,{'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'upserts':[item],'removed_ids':[]},uid())
                    material={'id':uid(),'kind':'passport','availability':'source_provided','provider_class':'current_actor_statement','valid_until':None,'check':'pending','source_id':source,'source_position':'第1–1行','label':'工程假设'}
                    save_material(material)
                    with self.assertRaises(AppError) as missing:nodes.bind(mid,bind_data(material['id']),uid())
                    self.assertEqual(missing.exception.status,409)
                    material={**material,'valid_until':'2026-11-30','check':'local_source_checked'};save_material(material)
                    for due in ('2026-10-08','2026-10-08T09:00:00'):
                        with self.assertRaises(AppError):nodes.bind(mid,bind_data(material['id'],due_at=due),uid())
                    before=store.detail(mid);paused=store.mutate('control',mid,{'expected_version':before['version'],'command':'pause'},uid())
                    key=uid();data=bind_data(material['id']);result=nodes.bind(mid,data,key);self.assertEqual(nodes.bind(mid,data,key),result);pid=result['point_id']
                    self.assertEqual(store.detail(mid)['assistant_status'],'paused');point=waiting.get_waiting(mid)['items'][0];self.assertTrue(point['due_now']);self.assertFalse(point['basis_stale'])
                    clean={k:point[k] for k in ('id','who','reason','next_step','due_at','state','note')};clean.update(who='本人修改对象',reason='本人保存原因不覆盖',note='旧人工说明')
                    waiting.save_waiting(mid,{'expected_version':store.detail(mid)['version'],'upserts':[clean],'removed_ids':[]},uid())
                    duplicate=nodes.bind(mid,bind_data(material['id'],note='不覆盖原说明'),uid());self.assertFalse(duplicate['created']);self.assertEqual(waiting.get_waiting(mid)['items'][0]['note'],'旧人工说明')
                    with self.assertRaises(AppError) as oldbase:nodes.bind(mid,data,uid())
                    self.assertEqual(oldbase.exception.status,409)
                    # Unrelated material fields are not a false change to this selected node.
                    save_material({**material,'id':uid(),'kind':'visa','source_position':'第2–2行','valid_until':'2026-12-31'})
                    self.assertFalse(waiting.get_waiting(mid)['items'][0]['basis_stale'])
                    fresh=store.detail(mid);journey.save_plan(mid,{'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'plan_semantics':dict(SEMANTICS),'field_changes':{'flight_info':{'value':'工程假设航班已变化','status':'confirmed'}}},uid())
                    self.assertTrue(waiting.get_waiting(mid)['items'][0]['basis_stale']);self.assertTrue(waiting.get_waiting(mid)['items'][0]['due_now'])
                    nodes.bind(mid,bind_data(material['id'],'rebind'),uid());self.assertEqual(waiting.get_waiting(mid)['items'][0]['note'],'旧人工说明')
                    self.assertFalse(waiting.get_waiting(mid)['items'][0]['basis_stale'])
                    # Linked travel can change without bumping this leave version.
                    target=store.mutate('create',None,{'domain':'travel','goal_text':'工程关联行程，非实际出行','auto_prepare':False},uid())
                    fresh=store.detail(mid);journey.set_travel_link(mid,{'expected_version':fresh['version'],'travel_matter_id':target['id'],'travel_revision':target['revision_no']},uid())
                    outdated=bind_data(material['id'],'rebind')
                    journey.save_plan(target['id'],{'expected_version':target['version'],'input_revision':target['revision_no'],'plan_semantics':dict(SEMANTICS),'field_changes':{'destination':{'value':'工程假设目的地变更','status':'confirmed'}}},uid())
                    with self.assertRaises(AppError) as linked:nodes.bind(mid,outdated,uid())
                    self.assertEqual(linked.exception.payload['code'],'material_basis_changed')
                    nodes.bind(mid,bind_data(material['id'],'rebind'),uid())
                    with self.assertRaises(AppError):waiting.get_followup(mid,pid) # paused
                    fresh=store.detail(mid);store.mutate('control',mid,{'expected_version':fresh['version'],'command':'resume'},uid());self.assertIn('本人保存原因不覆盖',waiting.get_followup(mid,pid)['content'])
                    material={**material,'valid_until':'2027-01-15'};save_material(material)
                    with self.assertRaises(AppError) as stale:waiting.get_followup(mid,pid)
                    self.assertEqual(stale.exception.payload['code'],'waiting_basis_changed')
                    view=nodes.get_candidates(mid);candidate=next(i for i in view['items'] if i['kind']=='passport');self.assertEqual(candidate['bound_date'],'2026-11-30');self.assertEqual(candidate['date'],'2027-01-15')
                    nodes.bind(mid,bind_data(material['id'],'rebind'),uid());self.assertIn('本人保存原因不覆盖',waiting.get_followup(mid,pid)['content'])
                    reopened=Store(folder)
                    with reopened.as_actor(actors[0]):
                        saved=WaitingService(reopened).get_waiting(mid)['items'][0];self.assertFalse(saved['basis_stale']);self.assertEqual(saved['note'],'旧人工说明');self.assertEqual(saved['due_at'],clean['due_at'])
                    fresh=store.detail(mid);SourceLifecycleService(store).set_source_state(mid,source,{'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'state':'withheld','replacement_source_id':None,'note':'本人暂不用'},uid())
                    self.assertTrue(waiting.get_waiting(mid)['items'][0]['basis_stale']);self.assertFalse(nodes.get_candidates(mid)['items'][0]['eligible'])
                    with self.assertRaises(AppError):nodes.bind(mid,bind_data(material['id'],'rebind'),uid())
                    fresh=store.detail(mid);SourceLifecycleService(store).set_source_state(mid,source,{'expected_version':fresh['version'],'input_revision':fresh['revision_no'],'state':'usable','replacement_source_id':None,'note':'本人恢复可用'},uid())
                    waiting.save_waiting(mid,{'expected_version':store.detail(mid)['version'],'upserts':[],'removed_ids':[pid]},uid());self.assertFalse(nodes.bind(mid,bind_data(material['id']),uid())['created'])
                    with self.assertRaises(AppError):nodes.bind(mid,bind_data(material['id'],'rebind'),uid())
                    self.assertFalse(waiting.get_waiting(mid)['items'][0]['active']);self.assertEqual(store.download(old,1),'本人旧稿不得覆盖；未办理');self.assertEqual(store.detail(mid)['tracks'],tracks)
                if protected:
                    with store.as_actor(actors[1]),self.assertRaises(AppError) as private:nodes.get_candidates(mid)
                    self.assertEqual(private.exception.status,404)
                    with store.transaction() as c:c.execute("UPDATE accounts SET capabilities='[\"executor\"]' WHERE id=?",(actors[0]['id'],))
                    with store.as_actor(actors[0]),self.assertRaises(AppError) as revoked:nodes.get_candidates(mid)
                    self.assertEqual(revoked.exception.status,403)
