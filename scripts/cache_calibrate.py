import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.models import CloudModels
from app.cache import guard,cosine


async def main(args):
    pairs=json.loads(Path('eval/cache-pairs.json').read_text(encoding='utf-8'))
    s=Settings() if args.allow_cloud else Settings(_env_file=None)
    if args.allow_cloud and s.app_mode=='offline': raise ValueError('configure_local_or_cloud_for_real_embedding')
    model=CloudModels(s);started=time.monotonic();vectors=None
    if args.allow_cloud: vectors=await model.embed([q for p in pairs for q in (p['a'],p['b'])])
    rows=[]
    for i,p in enumerate(pairs):
        same=guard(p['a'])==guard(p['b'])
        similarity=cosine(vectors[2*i],vectors[2*i+1]) if vectors else None
        accepted=same and similarity>=s.semantic_threshold if similarity is not None else None
        rows.append({**p,'guard_allows':same,'similarity':similarity,'accepted':accepted})
    report={'mode':s.app_mode,'threshold':s.semantic_threshold,'samples':len(rows),'rows':rows,
        'actual_embedding':vectors is not None,'false_accepts':sum(r['accepted'] is True and not r['reusable'] for r in rows) if vectors else None,
        'coverage':sum(r['accepted'] is True for r in rows)/len(rows) if vectors else None,
        'guard_false_allows':sum(r['guard_allows'] and not r['reusable'] for r in rows),
        'elapsed_ms':round((time.monotonic()-started)*1000),'usage':model.usage,'net_cost_or_latency_benefit':'not_measured'}
    Path(args.report).parent.mkdir(parents=True,exist_ok=True)
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('rows','usage')},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--allow-cloud',action='store_true');p.add_argument('--report',default='artifacts/cache-guard-report.json')
    asyncio.run(main(p.parse_args()))
