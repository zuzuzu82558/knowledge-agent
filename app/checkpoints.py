"""Validated successful batches survive retries; generation publication stays atomic."""
import json


class Checkpoints:
    def __init__(self, store, generation, operation):
        self.store, self.generation, self.operation = store, generation, operation

    def get(self, key):
        row = self.store.one('SELECT body FROM processing_checkpoints WHERE generation=? AND operation=? AND key=?',
                             (self.generation,self.operation,key))
        return json.loads(row['body']) if row else None

    def put(self, key, value):
        self.store.execute('INSERT OR REPLACE INTO processing_checkpoints VALUES (?,?,?,?)',
                           (self.generation,self.operation,key,json.dumps(value,ensure_ascii=False)))
