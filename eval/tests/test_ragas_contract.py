"""Explicit optional suite: synthetic judge responses validate API plumbing only."""
import asyncio
import json
import os
import socket
from types import SimpleNamespace

os.environ['RAGAS_DO_NOT_TRACK']='true'
from app.config import Settings
from app.evaluation import ragas_scores


def test_three_real_ragas_metric_classes_with_synthetic_judge(monkeypatch):
    import litellm
    def denied(*args,**kwargs): raise AssertionError('no external network in adapter test')
    monkeypatch.setattr(socket,'getaddrinfo',denied)
    calls=[]
    async def completion(model,messages,**kwargs):
        prompt=messages[-1]['content'];calls.append(('judge',model))
        if '"classifications"' in prompt:
            value={'classifications':[{'statement':'收入120亿元','reason':'synthetic fixture','attributed':1}]}
        elif '"verdict"' in prompt:
            value={'statements':[{'statement':'收入120亿元','reason':'synthetic fixture','verdict':1}]}
        elif '"noncommittal"' in prompt:
            value={'question':'收入多少','noncommittal':0}
        else: value={'statements':['收入120亿元']}
        return SimpleNamespace(model=model,usage=None,choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value,ensure_ascii=False)))])
    async def embedding(model,input,**kwargs):
        calls.append(('embedding',model))
        return SimpleNamespace(data=[{'index':i,'embedding':[1.,0.]} for i in range(len(input))],usage=None)
    monkeypatch.setattr(litellm,'acompletion',completion);monkeypatch.setattr(litellm,'aembedding',embedding)
    scores=asyncio.run(ragas_scores(Settings(_env_file=None,app_mode='local',model_api_key='test-only',embedding_dimension=2),
        {'question':'收入多少','response':'收入120亿元','contexts':['收入120亿元'],'reference':'收入120亿元'}))
    for name in ['faithfulness','answer_relevancy','context_recall']:
        assert scores[name]['status']=='scored',scores
        assert abs(scores[name]['value']-1)<1e-6
    assert any(kind=='embedding' for kind,_ in calls)
    assert all(model in {'openai/gpt-4o-mini','openai/text-embedding-3-small'} for _,model in calls)
