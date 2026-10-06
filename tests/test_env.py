""".env 파일로 중개서버 키·주소·모델을 넣는 방식 검증(모의 중개서버 사용)."""
import json

from conftest import MOCK_KEY, MOCK_PORT, wait_job
from app import config, storage


def test_env_parsing(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# 주석\nANTHROPIC_AUTH_TOKEN="abc 123"\nexport ANTHROPIC_BASE_URL=https://x.example  # 메모\n'
                 "ANTHROPIC_MODEL='claude-sonnet-5'\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", f)
    e = config.env_ai()
    assert e == {"key": "abc 123", "key_name": "ANTHROPIC_AUTH_TOKEN", "auth": "bearer",
                 "base_url": "https://x.example", "model": "claude-sonnet-5"}
    f.write_text("ANTHROPIC_AUTH_TOKEN=YOUR_API_KEY\nANTHROPIC_API_KEY=k2k2k2k2\n", encoding="utf-8")
    assert config.env_ai()["key"] == "k2k2k2k2" and config.env_ai()["auth"] == "x-api-key"  # 자리표시자는 무시


def test_env_key_used_for_relay(client, mock_relay, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"ANTHROPIC_AUTH_TOKEN={MOCK_KEY}\nANTHROPIC_BASE_URL=http://127.0.0.1:{MOCK_PORT}\nANTHROPIC_MODEL=mock-model\n",
                   encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env)
    client.put("/api/secrets", json={"ai_api_key": ""})  # 화면 저장 키 없음
    s = client.get("/api/settings").json()["settings"]
    s["ai"].update(base_url="", model="", auth="x-api-key")  # 화면 값을 비우면 .env 값 사용
    client.put("/api/settings", json={"settings": s})
    r = client.get("/api/settings").json()
    assert r["settings"]["ai"]["base_url"] == f"http://127.0.0.1:{MOCK_PORT}" and r["settings"]["ai"]["model"] == "mock-model"
    assert r["secrets"]["ai_api_key"] and r["secrets"]["ai_key_source"].startswith(".env") and r["secrets"]["ai_env_auth"] == "bearer"
    assert MOCK_KEY not in json.dumps(r)
    before = len(mock_relay.LOG)
    j = wait_job(client, client.post("/api/ai/test").json())
    assert j["status"] == "done", j
    hdr = mock_relay.LOG[before]["headers"]
    assert hdr.get("authorization") == "***" and "x-api-key" not in hdr  # Bearer로 전송
    # .env 키는 프로젝트에 저장 거부 대상
    p = client.post("/api/projects", json={"name": "env"}).json()["project"]
    p["idea"]["notes"] = MOCK_KEY
    assert client.put(f"/api/projects/{p['id']}", json={"project": p, "base_rev": p["rev"]}).status_code == 400
    assert config.SETTINGS_FILE.read_text(encoding="utf-8").find(MOCK_KEY) < 0
