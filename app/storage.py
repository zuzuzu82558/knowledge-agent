import json
import sqlite3


class Store:
    def __init__(self, directory):
        self.directory = directory
        self.db = sqlite3.connect(directory / 'state.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, name TEXT, deleted INTEGER DEFAULT 0, active TEXT);
        CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, document TEXT, sha TEXT, suffix TEXT, path TEXT,
            UNIQUE(document,sha,suffix));
        CREATE TABLE IF NOT EXISTS generations(id TEXT PRIMARY KEY, version TEXT, config TEXT, contract TEXT,
            parse_status TEXT, vector_status TEXT, graph_status TEXT DEFAULT 'not_in_m1',
            summary_status TEXT DEFAULT 'not_in_m1', parsed TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, generation TEXT, status TEXT, stage TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, generation TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS vocabulary(term TEXT PRIMARY KEY, idx INTEGER UNIQUE);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value INTEGER);
        INSERT OR IGNORE INTO metadata VALUES ('corpus_revision',0);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, body TEXT);
        CREATE TABLE IF NOT EXISTS processing_checkpoints(
            generation TEXT, operation TEXT, key TEXT, body TEXT,
            PRIMARY KEY(generation,operation,key));
        CREATE TABLE IF NOT EXISTS web_evidence(id TEXT PRIMARY KEY, body TEXT);
        ''')
        self._migrate_versions()
        self._migrate_generations()
        if 'cleanup_status' not in {r['name'] for r in self.db.execute('PRAGMA table_info(documents)')}:
            self.execute("ALTER TABLE documents ADD COLUMN cleanup_status TEXT DEFAULT 'none'")
        if 'target' not in {r['name'] for r in self.db.execute('PRAGMA table_info(documents)')}:
            # Latest generation the user explicitly asked to publish (upload, update, rebuild).
            # Only that generation may become active when its job completes.
            self.execute('ALTER TABLE documents ADD COLUMN target TEXT')
        self.execute("UPDATE jobs SET status='interrupted',error='process_restarted_retry_required' WHERE status IN ('queued','running')")
        self.execute("UPDATE generations SET vector_status='interrupted' WHERE vector_status='building'")

    def _migrate_generations(self):
        """Allow immutable rebuild attempts for the same content/configuration."""
        for index in self.db.execute('PRAGMA index_list(generations)').fetchall():
            name = index['name'].replace('"', '""')
            columns = [r['name'] for r in self.db.execute(f'PRAGMA index_info("{name}")')]
            if index['unique'] and columns == ['version', 'config']:
                backup_path = self.directory / 'state.pre-rebuild-v3.sqlite3'
                if not backup_path.exists():
                    with sqlite3.connect(backup_path) as backup:
                        self.db.backup(backup)
                with self.db:
                    self.db.execute('BEGIN IMMEDIATE')
                    self.db.execute('''CREATE TABLE generations_rebuild_v3(
                        id TEXT PRIMARY KEY, version TEXT, config TEXT, contract TEXT,
                        parse_status TEXT, vector_status TEXT, graph_status TEXT DEFAULT 'not_in_m1',
                        summary_status TEXT DEFAULT 'not_in_m1', parsed TEXT)''')
                    self.db.execute('INSERT INTO generations_rebuild_v3 SELECT * FROM generations')
                    self.db.execute('DROP TABLE generations')
                    self.db.execute('ALTER TABLE generations_rebuild_v3 RENAME TO generations')
                break
        self.execute('CREATE INDEX IF NOT EXISTS generations_version_config ON generations(version,config)')

    def _migrate_versions(self):
        unique_keys = []
        for index in self.db.execute('PRAGMA index_list(versions)').fetchall():
            if index['unique']:
                name = index['name'].replace('"', '""')
                unique_keys.append([r['name'] for r in self.db.execute(f'PRAGMA index_info("{name}")')])
        if ['document', 'sha'] in unique_keys:
            # Preserve every legacy ID, reference and path. Keep an online SQLite
            # backup before the transactional table replacement (including WAL).
            backup_path = self.directory / 'state.pre-format-v2.sqlite3'
            if not backup_path.exists():
                with sqlite3.connect(backup_path) as backup:
                    self.db.backup(backup)
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('''CREATE TABLE versions_format_v2(
                    id TEXT PRIMARY KEY, document TEXT, sha TEXT, suffix TEXT, path TEXT,
                    UNIQUE(document,sha,suffix))''')
                self.db.execute('INSERT INTO versions_format_v2 SELECT id,document,sha,suffix,path FROM versions')
                self.db.execute('DROP TABLE versions')
                self.db.execute('ALTER TABLE versions_format_v2 RENAME TO versions')
                self.db.execute('PRAGMA user_version=2')
        elif ['document', 'sha', 'suffix'] in unique_keys:
            self.execute('PRAGMA user_version=2')

    def execute(self, sql, args=()):
        cursor = self.db.execute(sql, args)
        self.db.commit()
        return cursor

    def all(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def revision(self):
        return self.one("SELECT value FROM metadata WHERE key='corpus_revision'")['value']

    def bump(self):
        self.execute("UPDATE metadata SET value=value+1 WHERE key='corpus_revision'")

    def vocabulary(self, terms=()):
        vocab = {r['term']: r['idx'] for r in self.all('SELECT * FROM vocabulary')}
        for term in sorted(set(terms) - vocab.keys()):
            idx = len(vocab)
            self.db.execute('INSERT INTO vocabulary VALUES (?,?)', (term, idx))
            vocab[term] = idx
        self.db.commit()
        return vocab

    def event(self, name, **fields):
        # Never include raw content, URLs, exceptions, or secrets in logs.
        record = json.dumps({'event': name, **fields}, ensure_ascii=False)
        print(record, flush=True)
        try:
            with (self.directory / 'events.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(record + '\n')
        except OSError:
            pass  # Observability failure must not break ingestion or answers.
