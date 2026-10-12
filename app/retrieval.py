import math
import re
from collections import Counter

import jieba
from qdrant_client import QdrantClient, models as m


def tokenize(text):
    return [t.lower() for t in jieba.cut(text, HMM=False) if re.search(r'[\w\u4e00-\u9fff]', t)]


def sparse(text, vocabulary, query=False):
    counts = Counter(tokenize(text))
    terms = sorted((vocabulary[t], n) for t, n in counts.items() if t in vocabulary)
    return m.SparseVector(indices=[i for i, _ in terms],
                          values=[1.0 if query else n * 2.5 / (n + 1.5) for _, n in terms])


def rrf(branches, limit=8, k=60):
    results = {}
    for name, points in branches.items():
        for rank, point in enumerate(points, 1):
            key = str(point.id)
            item = results.setdefault(key, {'id': key, 'rrf': 0.0, 'branches': {}})
            item['rrf'] += 1 / (k + rank)
            item['branches'][name] = {'rank': rank, 'score': point.score}
    return sorted(results.values(), key=lambda x: (-x['rrf'], x['id']))[:limit]


class Index:
    def __init__(self, settings):
        self.settings = settings
        self.name = f'{settings.collection_prefix}_{settings.fingerprint}'
        if settings.app_mode == 'cloud':
            self.client = QdrantClient(url=settings.qdrant_url,
                api_key=settings.qdrant_api_key.get_secret_value(), timeout=15)
        else:
            self.client = QdrantClient(path=str(settings.data_dir / 'qdrant'))
        if not self.client.collection_exists(self.name):
            self.client.create_collection(self.name,
                vectors_config={} if settings.app_mode == 'offline' else {
                    'dense': m.VectorParams(size=settings.embedding_dimension, distance=m.Distance.COSINE)},
                sparse_vectors_config={'sparse': m.SparseVectorParams(modifier=m.Modifier.IDF)})
            if settings.app_mode == 'cloud':
                self.client.create_payload_index(self.name, 'generation', m.PayloadSchemaType.KEYWORD, wait=True)

    def upsert(self, chunks, vectors, vocab, generation):
        for start in range(0, len(chunks), 64):
            points = []
            for i in range(start, min(start + 64, len(chunks))):
                vector = {'sparse': sparse(chunks[i]['text'], vocab)}
                if vectors is not None:
                    vector['dense'] = vectors[i]
                points.append(m.PointStruct(id=chunks[i]['id'], vector=vector,
                                             payload={'generation': generation}))
            self.client.upsert(self.name, points, wait=True)

    @staticmethod
    def scope(generations):
        return m.Filter(must=[m.FieldCondition(key='generation', match=m.MatchAny(any=generations))])

    def search(self, question, vector, vocab, generations, strategy='hybrid'):
        if not generations:
            return {}, []
        branches = {}
        for name, query in [('sparse', sparse(question, vocab, True)), ('dense', vector)]:
            if strategy in {'sparse', 'dense'} and name != strategy:
                continue
            if query is None or (name == 'sparse' and not query.indices):
                continue
            points = self.client.query_points(self.name, query=query, using=name,
                query_filter=self.scope(generations), limit=self.settings.retrieval_branch_limit, with_payload=False).points
            branches[name] = [p for p in points if name != 'sparse' or p.score > 0]
        return {k: [{'id': str(p.id), 'score': p.score, 'rank': i+1} for i, p in enumerate(v)]
                for k, v in branches.items()}, rrf(branches, self.settings.retrieval_candidate_pool, self.settings.rrf_k)

    def delete(self, generations):
        if generations:
            self.client.delete(self.name, points_selector=m.FilterSelector(filter=self.scope(generations)), wait=True)

    def close(self):
        self.client.close()
