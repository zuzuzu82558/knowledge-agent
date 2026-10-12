"""Evidence-group metrics use stable source text, not brittle chunk identities."""
import math


def evidence_metrics(groups, candidates, context):
    def score(evidence):
        texts=[e['text'] for e in evidence]
        # groups are alternatives, each alternative requires ALL listed anchors.
        recalls=[sum(any(anchor in text for text in texts) for anchor in group)/len(group)
                 for group in groups if group]
        return max(recalls,default=None)
    candidate, final=score(candidates),score(context)
    # Exact anchors do not establish subject attribution or semantic support.
    return {'candidate_anchor_recall':candidate,'context_anchor_recall':final,
            'all_required_anchors_present':None if final is None else final==1,
            'semantic_support':'not_judged'}


def citation_metrics(result):
    allowed={e['id'] for e in result['evidence']}
    ids=[i for c in result['claims'] for i in c['citations']]
    return {'citation_scope_validity':sum(i in allowed for i in ids)/len(ids) if ids else None,
            'semantic_support':'not_judged'}


async def ragas_scores(settings, record, meter=None):
    names=['faithfulness','answer_relevancy','context_recall']
    if settings.app_mode=='offline': return {n:{'value':None,'status':'not_run_offline'} for n in names}
    if not record.get('response') or not record.get('reference'):
        return {n:{'value':None,'status':'not_applicable_refusal_or_missing_reference'} for n in names}
    try:
        from ragas import SingleTurnSample
        from ragas.metrics import Faithfulness, ResponseRelevancy, LLMContextRecall
        from .ragas_adapter import make_adapters
        llm,embedding,model=make_adapters(settings)
        model.budget.meter=meter
        metrics=[Faithfulness(llm=llm),ResponseRelevancy(llm=llm,embeddings=embedding),LLMContextRecall(llm=llm)]
        sample=SingleTurnSample(user_input=record['question'],response=record['response'],
            retrieved_contexts=record['contexts'],reference=record['reference'])
        values={}
        for name,metric in zip(names,metrics):
            try:
                value=float(await metric.single_turn_ascore(sample))
                values[name]={'value':value if math.isfinite(value) else None,'status':'scored' if math.isfinite(value) else 'invalid_score'}
            except Exception as exc:
                values[name]={'value':None,'status':'failed','error':type(exc).__name__}
        values['judge_usage']=model.usage
        values['judge_calls']=model.budget.events
        return values
    except Exception as exc:
        return {n:{'value':None,'status':'dependency_or_adapter_failed','error':type(exc).__name__} for n in names}
