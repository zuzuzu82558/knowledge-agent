from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_default_twenty_mb_upload_boundary(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, app_mode='offline')
    assert settings.max_upload_mb == 20
    with TestClient(create_app(settings)) as client:
        # Isolate transport acceptance from parser limits and indexing costs.
        upload = AsyncMock(return_value={'job_id': 'boundary-fixture'})
        client.app.state.service.upload = upload
        assert client.get('/api/health').json()['max_upload_mb'] == 20
        exact = b'x' * (20 * 1024 * 1024)
        assert client.post('/api/documents', files={'file': ('limit.txt', exact)}).status_code == 202
        assert len(upload.call_args.args[2]) == len(exact)
        upload.reset_mock()
        response = client.post('/api/documents', files={'file': ('large.txt', exact + b'x')})
        assert response.status_code == 413
        assert response.json()['detail'] == 'upload_too_large'
        upload.assert_not_called()
        response = client.post('/api/documents', files={'file': ('larger.txt', exact + b'x' * 70000)})
        assert response.status_code == 413
        assert response.json()['detail'] == 'request_too_large'
        upload.assert_not_called()
