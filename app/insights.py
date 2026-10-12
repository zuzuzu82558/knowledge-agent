"""Visit every source chunk; persist reusable, cited section summaries."""
import hashlib
import json

from .context import select_context


async def insights(service, generations, model):
    st = service.store
    rows = st.all('SELECT body FROM chunks WHERE generation IN ('+','.join('?' for _ in generations)+') ORDER BY rowid', generations)
    chunks = [json.loads(r['body']) for r in rows]
    groups = {}
    for c in chunks:
        groups.setdefault((c['generation'], tuple(c['headings'])), []).append(c)
    sections, all_evidence = [], {}
    for (generation, headings), source in groups.items():
        claims, failed, covered = [], [], []
        # Each source chunk is visited even when it would never be in top-k.
        for c in source:
            key = hashlib.sha256(json.dumps([c['id'], service.s.llm_model,
                service.s.llm_api_base or service.s.model_api_base, service.s.fallback_model,
                service.s.evidence_bytes, service.s.model_request_budget_bytes,
                service.s.generation_options_fingerprint, 'summary-v2']).encode()).hexdigest()
            saved = st.one('SELECT body FROM summaries WHERE id=?', (key,))
            if saved:
                item = json.loads(saved['body'])
            else:
                evidence, context = select_context([c], '核心观点 原文明示风险 关键数据 时间 单位 口径 预测', service.s.evidence_bytes)
                if not evidence:
                    failed.append({'chunk':c['id'], 'reason':'budget_exceeded'}); continue
                if service.s.app_mode == 'offline':
                    item = {'status':'offline_extracts', 'claims':[], 'evidence':evidence}
                else:
                    try:
                        item = await model.answer('提取核心观点、原文明示风险、关键数据；保留对象、期间、单位、币种、口径、预测属性。不要把风险推断称为原文明示。', evidence)
                        item['evidence'] = evidence
                        if item['status'] != 'answered':
                            failed.append({'chunk':c['id'], 'reason':item['status']}); continue
                        if not any(e.get('status')=='success' and e.get('model')==service.s.fallback_model
                                   for e in model.budget.events):
                            st.execute('INSERT OR REPLACE INTO summaries VALUES (?,?,?)', (key,generation,json.dumps(item,ensure_ascii=False)))
                    except Exception as exc:
                        failed.append({'chunk':c['id'], 'reason':type(exc).__name__}); continue
            covered.append(c['id']); claims.extend(item['claims'])
            if any('excerpt' in e for e in item['evidence']):
                failed.append({'chunk':c['id'],'reason':'excerpt_only_partial_chunk_coverage'})
            for e in item['evidence']:
                all_evidence[e['id']] = e
        sections.append({'generation':generation, 'headings':list(headings), 'claims':claims,
                         'covered_chunks':covered, 'failed':failed, 'total_chunks':len(source),
                         'status':'partial' if failed else ('offline_extracts' if service.s.app_mode=='offline' else 'ready')})
    complete = sum(not s['failed'] for s in sections)
    union=list({json.dumps(c,sort_keys=True,ensure_ascii=False):c for s in sections for c in s['claims']}.values())
    aggregate={'core_viewpoints':[],'risk_points':[],'key_data':[],'status':'not_run_offline' if service.s.app_mode=='offline' else 'not_run_no_claims'}
    if union and service.s.app_mode!='offline':
        projection=[{'id':str(i),'text':c['text'],'citations':c['citations']} for i,c in enumerate(union)]
        size=len(json.dumps({'summaries':projection},ensure_ascii=False).encode())
        if size>service.s.evidence_bytes:
            aggregate['status']='budget_exceeded_section_union_retained'
        else:
            try:
                value=await model.structured('仅根据章节摘要聚合，不添加事实。输出 core_viewpoints/risk_points/key_data 三个数组。'
                    '每项含 text 和 sources（输入摘要id数组）；risk_points 还含 basis（explicit 或 inference）；'
                    'key_data 还含 subject,period,unit,scope,assertion（historical/forecast/opinion），不确定字段用unknown。'
                    '保持冲突事实并存，不跨口径求和。', {'summaries':projection})
                mapped={}
                for category in ('core_viewpoints','risk_points','key_data'):
                    entries=value.get(category)
                    if not isinstance(entries,list): raise ValueError('aggregate_contract')
                    mapped[category]=[]
                    for entry in entries:
                        sources=entry.get('sources')
                        if not isinstance(entry.get('text'),str) or not entry['text'].strip() or not isinstance(sources,list) or not sources:
                            raise ValueError('aggregate_contract')
                        allowed={p['id']:p for p in projection}
                        if any(i not in allowed for i in sources): raise ValueError('aggregate_citation')
                        if category=='risk_points' and entry.get('basis') not in ('explicit','inference'): raise ValueError('risk_basis')
                        if category=='key_data' and (any(not isinstance(entry.get(k),str) or not entry[k] for k in ('subject','period','unit','scope','assertion'))
                                or entry['assertion'] not in ('historical','forecast','opinion')): raise ValueError('numeric_context')
                        mapped[category].append({k:v for k,v in entry.items() if k!='sources'} | {
                            'citations':list(dict.fromkeys(i for source in sources for i in allowed[source]['citations']))})
                aggregate={**mapped,'status':'ready'}
            except Exception as exc:
                aggregate['status']='failed_section_union_retained';aggregate['error']=type(exc).__name__
    for gen in generations:
        relevant = [s for s in sections if s['generation']==gen]
        st.execute('UPDATE generations SET summary_status=? WHERE id=?',
                   ('offline_extracts' if service.s.app_mode=='offline' else 'partial' if any(s['failed'] for s in relevant) or aggregate['status']!='ready' else 'ready',gen))
    return {'sections':sections, 'coverage':{'total_sections':len(sections), 'complete_sections':complete,
            'total_chunks':len(chunks), 'visited_chunks':sum(len(s['covered_chunks']) for s in sections)},
            'claims':union,'insights':aggregate, 'coverage_basis':'parsed_chunks_not_original_document_completeness',
            'evidence':list(all_evidence.values()), 'status':'offline_extracts' if service.s.app_mode=='offline' else 'partial_summary' if complete<len(sections) or (union and aggregate['status']!='ready') else 'summarized',
            'aggregation':'cited_section_union_with_validated_optional_global_insights'}
