import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

# 앱을 불러오기 전에 데이터·설정 폴더를 임시 폴더로 돌린다(실제 사용자 데이터에 영향 없음)
_TMP = Path(tempfile.mkdtemp(prefix="jpss_test_"))
os.environ["JPSS_DATA_DIR"] = str(_TMP / "data")
os.environ["JPSS_CONFIG_DIR"] = str(_TMP / "cfg")
os.environ["JPSS_ENV_FILE"] = str(_TMP / "no.env")  # 실제 프로젝트 폴더의 .env(진짜 키)를 시험에서 읽지 않음
_models = os.environ.get("JPSS_TEST_MODELS")  # 이미 받은 whisper 모델 폴더 재사용(선택)

FIX = ROOT / "tests" / "fixtures"
MOCK_PORT = 8798
MOCK_KEY = "mock-key-1234567890"


def _free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


@pytest.fixture(scope="session")
def mock_relay():
    import uvicorn
    import mock_relay as mr

    cfg = uvicorn.Config(mr.app, host="127.0.0.1", port=MOCK_PORT, log_level="error")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(50):
        if not _free(MOCK_PORT):
            break
        time.sleep(0.1)
    yield mr
    server.should_exit = True


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app, headers={"x-jpss": "1"}) as c:
        yield c


def wait_job(client, job, timeout=120):
    t0 = time.time()
    while job["status"] == "running":
        assert time.time() - t0 < timeout, "작업 시간 초과"
        time.sleep(0.2)
        job = client.get(f"/api/jobs/{job['id']}").json()
    return job


@pytest.fixture
def configure_ai(client, mock_relay):
    def _cfg(model="mock-model", key=MOCK_KEY, auth="x-api-key", timeout=30, stream=False):
        s = client.get("/api/settings").json()["settings"]
        s["ai"].update({"base_url": f"http://127.0.0.1:{MOCK_PORT}", "model": model, "auth": auth,
                        "timeout_s": timeout, "stream": stream})
        client.put("/api/settings", json={"settings": s})
        client.put("/api/secrets", json={"ai_api_key": key})
    return _cfg
