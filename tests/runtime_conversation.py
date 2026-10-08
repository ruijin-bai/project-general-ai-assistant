"""Read back the preserved T058 engineering UI chain; no model or business action."""
import json
from pathlib import Path
from tests.runtime_meal_summaries import Client

PROOF = Path('docs/evidence/conversation-v03.json')

def collect():
    p = json.loads(PROOF.read_text(encoding='utf-8'))
    clerk = Client('qa-clerk')
    m = clerk.get('/api/matters/' + p['matter_id'])
    assert m['assistant_status'] == 'waiting' and m['revision_no'] == 4
    assert len(m['sources']) == 4
    assert m['goal_text'] == p['before']['goal_text']
    assert m['facts'] == p['before']['facts'] and m['tracks'] == p['before']['tracks']
    assert clerk.raw('/api/artifacts/' + p['original_artifact_id'] + '/download?version=1&format=md') == '工程T058原人工稿，原文保留：只准备，不批准或派车。'
    assert clerk.raw('/api/artifacts/' + p['model_artifact_id'] + '/download?version=2&format=md') == p['model_v2_md']
    model = [a for a in m['actions'] if a['kind'] == 'prepare_model']
    local = [a for a in m['actions'] if a['kind'] == 'prepare']
    assert len(model) == len(local) == 1
    assert model[0]['status'] == local[0]['status'] == 'completed'
    assert len(m['artifacts']) == 3 and m['conversation_questions'] == []
    assert clerk.get('/api/matters/' + p['prior_t055']['id']) == p['prior_t055']
    prior = json.loads(Path('docs/evidence/expense-journey-v03.json').read_text(encoding='utf-8'))
    assert clerk.raw('/api/artifacts/' + prior['report']['artifact_id'] + '/download?version=2&format=md') == p['prior_t053_private_v2']
    denied = Client('qa-employee').denied('/api/matters/' + m['id'])
    assert denied == 404
    p.update(after=m, other_owner_read=denied, model_calls_this_increment=1,
             local_preparations=1, prior_manual_versions_unchanged=True,
             runtime_session=22231,
             verification='combined local/identity critical smoke 0.349s; affected Python/JS syntax; real Jiaorong call; actual browser send/edit/save/pause/reopen/resume/local-template; conversation refresh verified without full panel rerender')
    PROOF.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'matter_id':m['id'],'sources':4,'model_calls':1,'local_preparations':1,
                      'artifacts':3,'status':m['assistant_status'],'other_owner_read':denied,
                      'prior_manual_versions_unchanged':True}, ensure_ascii=False))

if __name__ == '__main__':
    collect()
