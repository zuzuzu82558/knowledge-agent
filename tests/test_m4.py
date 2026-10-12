import asyncio
import json

from app.config import Settings
from app.evaluation import evidence_metrics, citation_metrics, ragas_scores
from app.observability import Observer
from app.storage import Store


def test_evidence_groups_and_missing_scores(tmp_path):
    groups=[['供应商','交付延期'],['替代完整证据']]
    metrics=evidence_metrics(groups,[{'text':'供应商'},{'text':'交付延期'}],[{'text':'供应商'}])
    assert metrics=={'candidate_anchor_recall':1,'context_anchor_recall':.5,'all_required_anchors_present':False,'semantic_support':'not_judged'}
    assert evidence_metrics(groups,[],[{'text':'替代完整证据'}])['all_required_anchors_present']
    scores=asyncio.run(ragas_scores(Settings(_env_file=None),{}))
    assert all(s['value'] is None and s['status']=='not_run_offline' for s in scores.values())
    assert citation_metrics({'evidence':[{'id':'yes'}],'claims':[{'citations':['yes','no']}]})['citation_scope_validity']==.5


def test_offline_observation_does_not_export(tmp_path):
    st=Store(tmp_path);observer=Observer(Settings(_env_file=None,data_dir=tmp_path,langfuse_public_key='fake'),st)
    asyncio.run(observer.record('test',run_id='r'))
    assert observer.client is None
    assert json.loads((tmp_path/'events.jsonl').read_text())['run_id']=='r'
    observer.close();st.db.close()


def test_golden_dataset_contract():
    from pathlib import Path
    rows=[json.loads(l) for l in Path('eval/golden.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows)>=30 and len({r['id'] for r in rows})==len(rows)
    for r in rows:
        text=(Path('examples')/r['document']).read_text(encoding='utf-8')
        assert r['split'] in {'dev','holdout'}
        assert all(a in text for group in r['evidence_groups'] for a in group)
        assert r['human_review']=='pending'
