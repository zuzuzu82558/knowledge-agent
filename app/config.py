import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    app_mode: Literal['offline', 'local', 'cloud'] = 'offline'
    data_dir: Path = Path('data')
    llm_model: str = 'openai/gpt-4o-mini'
    embedding_model: str = 'openai/text-embedding-3-small'
    embedding_revision: str = 'provider-default'
    embedding_dimension: int = Field(1536, ge=1, le=65536)
    model_api_key: SecretStr = SecretStr('')
    model_api_base: str | None = None
    llm_api_key: SecretStr = SecretStr('')
    llm_api_base: str | None = None
    # Provider-specific generation options; never forwarded to embeddings.
    llm_extra_body: dict = Field(default_factory=dict)
    embedding_api_key: SecretStr = SecretStr('')
    embedding_api_base: str | None = None
    fallback_model: str | None = None
    max_model_calls: int = Field(40, ge=1, le=500)
    background_timeout: int = Field(1800, ge=1, le=14400)
    background_max_model_calls: int = Field(5000, ge=1, le=50000)
    model_call_timeout: int = Field(60, ge=1, le=180)
    embedding_batch_size: int = Field(32, ge=1, le=128)
    embedding_input_profile: Literal['auto', 'symmetric', 'cohere', 'voyage', 'prefix'] = 'auto'
    embedding_query_prefix: str = ''
    embedding_document_prefix: str = ''
    chunk_strategy: Literal['legacy', 'boundary'] = 'legacy'
    chunk_size: int = Field(900, ge=100, le=8000)
    chunk_overlap: int = Field(0, ge=0, le=2000)
    retrieval_branch_limit: int = Field(20, ge=1, le=100)
    retrieval_candidate_pool: int = Field(24, ge=1, le=100)
    retrieval_top_k: int = Field(8, ge=1, le=50)
    rrf_k: int = Field(60, ge=1, le=200)
    evidence_budget_bytes: int | None = Field(None, ge=500, le=100000)
    model_request_budget_bytes: int = Field(64000, ge=4000, le=1000000)
    generation_max_tokens: int = Field(1200, ge=100, le=8000)
    search_provider: Literal['disabled', 'tavily'] = 'disabled'
    search_api_key: SecretStr = SecretStr('')
    search_max_results: int = Field(5, ge=1, le=10)
    search_timeout: int = Field(10, ge=1, le=30)
    search_max_calls: int = Field(3, ge=1, le=5)
    web_timeout: int = Field(10, ge=1, le=30)
    web_max_bytes: int = Field(1000000, ge=10000, le=5000000)
    web_max_text_chars: int = Field(30000, ge=1000, le=100000)
    web_concurrency: int = Field(3, ge=1, le=5)
    web_max_redirects: int = Field(3, ge=0, le=5)
    web_max_pages: int = Field(6, ge=1, le=10)
    agent_max_rounds: int = Field(2, ge=0, le=2)
    retry_attempts: int = Field(1, ge=0, le=2)
    graph_enabled: bool = False
    neo4j_uri: str | None = None
    neo4j_user: str = 'neo4j'
    neo4j_password: SecretStr = SecretStr('')
    neo4j_database: str = 'neo4j'
    cache_enabled: bool = True
    cache_ttl: int = Field(3600, ge=1, le=86400)
    cache_capacity: int = Field(100, ge=1, le=1000)
    semantic_threshold: float = Field(0.98, ge=0.9, le=1)
    redis_url: str | None = None
    redis_token: SecretStr = SecretStr('')
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr = SecretStr('')
    langfuse_base_url: str = 'https://cloud.langfuse.com'
    rerank_model: str | None = None
    qdrant_url: str | None = None
    qdrant_api_key: SecretStr = SecretStr('')
    collection_prefix: str = Field('knowledge_m1', pattern=r'^[a-zA-Z0-9_]{1,50}$')
    max_upload_mb: int = Field(20, ge=1, le=50)
    max_chunks: int = Field(2000, ge=1, le=10000)
    context_tokens: int = Field(3000, ge=500, le=12000)
    request_timeout: int = Field(60, ge=1, le=180)

    @field_validator('model_api_base','llm_api_base','embedding_api_base','qdrant_url','neo4j_uri',
        'redis_url','langfuse_public_key','fallback_model','rerank_model',mode='before')
    @classmethod
    def empty_optional(cls,value):
        return None if isinstance(value,str) and not value.strip() else value

    @model_validator(mode='after')
    def valid_cloud(self):
        protected = {'model', 'messages', 'input', 'max_tokens', 'max_completion_tokens',
                     'stream', 'response_format', 'api_key', 'api_base', 'timeout'}
        if protected.intersection(self.llm_extra_body):
            raise ValueError('LLM_EXTRA_BODY cannot override request identity, input, output or connection controls')
        if self.chunk_overlap >= self.chunk_size or (self.chunk_strategy == 'legacy' and self.chunk_overlap):
            raise ValueError('overlap requires boundary strategy and must be smaller than chunk_size')
        if self.retrieval_top_k > self.retrieval_candidate_pool:
            raise ValueError('retrieval_top_k must not exceed candidate_pool')
        if self.app_mode != 'offline' and not (self.model_api_key.get_secret_value() or
                (self.llm_api_key.get_secret_value() and self.embedding_api_key.get_secret_value())):
            raise ValueError('MODEL_API_KEY is required for local/cloud mode')
        if self.app_mode == 'cloud' and not (self.qdrant_url and self.qdrant_api_key.get_secret_value()):
            raise ValueError('QDRANT_URL and QDRANT_API_KEY are required for cloud mode')
        if self.app_mode != 'offline':
            if bool(self.neo4j_uri) != bool(self.neo4j_password.get_secret_value()):
                raise ValueError('NEO4J_URI and NEO4J_PASSWORD must be configured together')
            if bool(self.redis_url) != bool(self.redis_token.get_secret_value()):
                raise ValueError('REDIS_URL and REDIS_TOKEN must be configured together')
            if bool(self.langfuse_public_key) != bool(self.langfuse_secret_key.get_secret_value()):
                raise ValueError('LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be configured together')
        return self

    @property
    def generation_options_fingerprint(self):
        return hashlib.sha256(json.dumps(self.llm_extra_body, sort_keys=True).encode()).hexdigest()[:16]

    @property
    def evidence_bytes(self):
        # Backward compatible alias: CONTEXT_TOKENS always meant bytes.
        return self.evidence_budget_bytes if self.evidence_budget_bytes is not None else self.context_tokens

    @property
    def input_profile(self):
        if self.embedding_input_profile != 'auto':
            return self.embedding_input_profile
        provider = self.embedding_model.split('/')[0]
        if provider in {'cohere', 'voyage'}:
            return provider
        if provider in {'openai', 'azure', 'mistral'}:
            return 'symmetric'
        raise ValueError('explicit_embedding_input_profile_required_for_provider')

    @property
    def contract(self):
        return {'parser': 'm5-v3-html-dom-table-grid', 'chunker': 'section-900-v2-bounded-table',
                'sparse': 'jieba-0.42.1-bm25-k1=1.5-b=0-qdrant-idf-v1',
                'mode': self.app_mode, 'embedding': self.embedding_model,
                'revision': self.embedding_revision, 'dimension': self.embedding_dimension,
                'distance': 'Cosine', 'normalization': 'qdrant-cosine',
                'endpoint': self.embedding_api_base or self.model_api_base,
                'chunk_contract': {'strategy': self.chunk_strategy, 'size': self.chunk_size,
                                   'overlap': self.chunk_overlap, 'version': 'boundary-v1'},
                'embedding_input': {'profile': self.input_profile,
                                    'query_prefix': self.embedding_query_prefix,
                                    'document_prefix': self.embedding_document_prefix, 'version': 'purpose-v1'}}

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.contract, sort_keys=True).encode()).hexdigest()[:16]
