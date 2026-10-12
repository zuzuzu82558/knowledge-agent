"""Evidence-bearing facts; identities are scoped to an index generation."""
import hashlib
import json
from collections import deque

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Entity(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern='^(company|product|supplier|metric|risk|other)$')
    aliases: list[str] = Field(default_factory=list, max_length=10)


class Fact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    subject: Entity
    relation: str = Field(pattern='^(produces|supplies|depends_on|affects|has_metric|reports)$')
    object: Entity
    quote: str = Field(min_length=1, max_length=900)
    value: str | None = Field(None, max_length=120)
    unit: str | None = Field(None, max_length=80)
    period: str | None = Field(None, max_length=80)
    scope: str | None = Field(None, max_length=120)
    assertion: str = Field(pattern='^(historical|forecast|opinion)$')

    @model_validator(mode='after')
    def metric_context(self):
        if self.relation == 'has_metric' and not all([self.value, self.unit, self.period, self.scope]):
            raise ValueError('metric_context_required')
        return self


def validate_facts(raw, chunk, origin='model'):
    if not isinstance(raw, list) or len(raw) > 30:
        raise ValueError('facts_contract')
    result = []
    for item in raw:
        fact = Fact.model_validate(item).model_dump()
        if fact['quote'] not in chunk['text']:
            raise ValueError('fact_quote_not_in_source')
        # A quote is a provenance check, not a semantic truth oracle.
        fact.update(generation=chunk['generation'], chunk=chunk['id'], origin=origin,
                    contract='facts-v1', extraction_model=None)
        fact['id'] = hashlib.sha256(json.dumps(fact, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        result.append(fact)
    return result


class Graph:
    def __init__(self, settings, store):
        self.s, self.st = settings, store
        store.execute('CREATE TABLE IF NOT EXISTS facts(id TEXT PRIMARY KEY,generation TEXT,body TEXT)')
        self.driver = None

    def remote(self):
        binding=json.dumps([self.s.neo4j_uri,self.s.neo4j_database,self.s.collection_prefix])
        previous=self.st.one("SELECT value FROM metadata WHERE key='graph_binding'")
        if previous and previous['value']!=binding:
            raise ValueError('graph_endpoint_changed_use_separate_data_dir')
        self.st.execute("INSERT OR IGNORE INTO metadata VALUES ('graph_binding',?)",(binding,))
        if self.driver is None:
            from neo4j import GraphDatabase
            self.driver = GraphDatabase.driver(self.s.neo4j_uri,
                auth=(self.s.neo4j_user, self.s.neo4j_password.get_secret_value()),
                connection_timeout=5, max_transaction_retry_time=0)
        return self.driver

    def replace(self, generation, facts):
        if self.s.neo4j_uri and self.s.app_mode != 'offline':
            from neo4j import unit_of_work
            # Remote facts retain all source fields and immutable generation IDs.
            with self.remote().session(database=self.s.neo4j_database) as session:
                @unit_of_work(timeout=10)
                def write(tx):
                    tx.run('MATCH (n) WHERE n.namespace=$ns AND n.generation=$g AND (n:KAFact OR n:KAEntity) DETACH DELETE n',
                           ns=self.s.collection_prefix, g=generation).consume()
                    tx.run('UNWIND $rows AS row CREATE (f:KAFact) '
                        'SET f.id=row.id,f.namespace=row.namespace,f.generation=row.generation,f.body=row.body '
                        'MERGE (a:KAEntity {namespace:row.namespace,generation:row.generation,name:row.subject,kind:row.subject_kind}) '
                        'MERGE (b:KAEntity {namespace:row.namespace,generation:row.generation,name:row.object,kind:row.object_kind}) '
                        'MERGE (a)-[:SUBJECT]->(f) MERGE (f)-[:OBJECT]->(b)',
                           rows=[{'id': f['id'], 'namespace':self.s.collection_prefix,
                                  'subject':f['subject']['name'],'subject_kind':f['subject']['kind'],
                                  'object':f['object']['name'],'object_kind':f['object']['kind'],
                                  'generation':generation, 'body':json.dumps(f, ensure_ascii=False)} for f in facts]).consume()
                session.execute_write(write)
        with self.st.db:
            self.st.db.execute('DELETE FROM facts WHERE generation=?', (generation,))
            self.st.db.executemany('INSERT INTO facts VALUES (?,?,?)',
                [(f['id'], generation, json.dumps(f, ensure_ascii=False)) for f in facts])
        self.st.bump()

    def rows(self, generations):
        if self.s.neo4j_uri and self.s.app_mode != 'offline':
            from neo4j import Query
            with self.remote().session(database=self.s.neo4j_database) as session:
                rows = session.run(Query('MATCH (f:KAFact) WHERE f.namespace=$ns AND f.generation IN $gs '
                    'RETURN f.body AS body LIMIT 1001', timeout=5), ns=self.s.collection_prefix, gs=generations)
                values = [json.loads(r['body']) for r in rows]
        else:
            values = [json.loads(r['body']) for r in self.st.all('SELECT body FROM facts WHERE generation IN ('+
                      ','.join('?' for _ in generations)+') LIMIT 1001', generations)] if generations else []
        if len(values) > 1000:
            raise ValueError('graph_scope_exceeds_1000_facts')
        return values

    def search(self, question, generations):
        facts = self.rows(generations)
        def key(e, g):
            return (g, e['kind'], e['name'].casefold())
        adjacency, seeds = {}, set()
        for fact in facts:
            if fact['generation'] not in generations:
                raise ValueError('remote_graph_scope_violation')
            a, b = key(fact['subject'], fact['generation']), key(fact['object'], fact['generation'])
            for entity, node in [(fact['subject'], a), (fact['object'], b)]:
                if any(n.casefold() in question.casefold() for n in [entity['name'], *entity['aliases']] if n):
                    seeds.add(node)
            adjacency.setdefault(a, []).append((b, fact))
            adjacency.setdefault(b, []).append((a, fact))
        queue = deque((s, []) for s in sorted(seeds)[:5])
        paths, seen = [], set()
        while queue and len(paths) < 20:
            node, path = queue.popleft()
            if len(path) >= 2:
                continue
            for nxt, fact in sorted(adjacency.get(node, []), key=lambda x:x[1]['id'])[:10]:
                if fact['id'] in {f['id'] for f in path}:
                    continue
                step = path + [fact]
                identity = tuple(f['id'] for f in step)
                if identity not in seen:
                    seen.add(identity); paths.append(step)
                    if len(paths) >= 20:
                        break
                    queue.append((nxt, step))
        scores = {}
        for path in paths:
            for fact in path:
                scores[fact['chunk']] = max(scores.get(fact['chunk'], 0), 1/len(path))
        hits = [{'id':c, 'score':v, 'rank':i+1} for i,(c,v) in
                enumerate(sorted(scores.items(), key=lambda x:(-x[1],x[0])))]
        return hits, paths

    def delete(self, generations):
        if self.s.neo4j_uri and self.s.app_mode != 'offline':
            from neo4j import Query
            with self.remote().session(database=self.s.neo4j_database) as session:
                session.run(Query('MATCH (n) WHERE n.namespace=$ns AND n.generation IN $gs AND (n:KAFact OR n:KAEntity) DETACH DELETE n', timeout=10),
                            ns=self.s.collection_prefix, gs=generations).consume()
        for generation in generations:
            self.st.execute('DELETE FROM facts WHERE generation=?', (generation,))

    def close(self):
        if self.driver:
            self.driver.close()
