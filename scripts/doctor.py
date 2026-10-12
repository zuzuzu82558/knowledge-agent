"""Credential-presence and local dependency checks; never calls cloud services."""
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Settings


def main():
    s=Settings()
    result={'python':sys.version.split()[0],'mode':s.app_mode,'config':s.fingerprint,
        'data_directory':str(s.data_dir.resolve()),'credentials':{
        'generation':bool(s.llm_api_key.get_secret_value() or s.model_api_key.get_secret_value()),
        'embedding':bool(s.embedding_api_key.get_secret_value() or s.model_api_key.get_secret_value()),
        'search':bool(s.search_provider!='disabled' and s.search_api_key.get_secret_value()),
        'qdrant':bool(s.qdrant_url and s.qdrant_api_key.get_secret_value()),
        'neo4j':bool(s.neo4j_uri and s.neo4j_password.get_secret_value()),
        'redis':bool(s.redis_url and s.redis_token.get_secret_value()),
        'langfuse':bool(s.langfuse_public_key and s.langfuse_secret_key.get_secret_value())},
        'dependencies':{n:bool(importlib.util.find_spec(n)) for n in ['fastapi','litellm','qdrant_client','docx','pptx','openpyxl','xlrd','bs4','neo4j','langfuse','ragas']},
        'budgets':{'interactive_calls':s.max_model_calls,'interactive_seconds':s.request_timeout,
                   'background_calls':s.background_max_model_calls,'background_seconds':s.background_timeout,
                   'evidence_bytes':s.evidence_bytes,'model_request_bytes':s.model_request_budget_bytes},
        'embedding_input_profile':s.input_profile,
        'cloud_connections':'not_tested','fee_calls':0}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
