"""로컬 서버 API 통합 검증: 저장·복원, AI 연결(모의 중개서버), 음성 원본 보존, 내보내기, 보안."""
import io
import json
import time
import zipfile
from pathlib import Path

import pytest

from conftest import FIX, MOCK_KEY, wait_job
from app import config, storage

needs_ja = pytest.mark.skipif(not (FIX / "ja_tts_sample.wav").exists(), reason="tests/make_fixtures.py 실행 필요")


def new_project(client, name="테스트"):
    return client.post("/api/projects", json={"name": name}).json()["project"]


def save(client, p, force=False):
    r = client.put(f"/api/projects/{p['id']}", json={"project": p, "base_rev": p["rev"], "force": force})
    assert r.status_code == 200, r.text
    return r.json()["project"]


# ---------------------------------------------------------------- 저장·복원

def test_save_restore_conflict_backup_duplicate(client):
    p = new_project(client, "저장 테스트")
    p["idea"]["topic"] = "日本語の題名 / 한국어 주제"
    p = save(client, p)
    got = client.get(f"/api/projects/{p['id']}").json()["project"]
    assert got["idea"]["topic"] == "日本語の題名 / 한국어 주제" and got["rev"] == p["rev"]
    # 다른 창에서 오래된 rev로 저장 → 409(덮어쓰기 방지)
    stale = dict(got, rev=got["rev"] - 1)
    r = client.put(f"/api/projects/{p['id']}", json={"project": stale, "base_rev": stale["rev"]})
    assert r.status_code == 409 and "먼저 저장" in r.json()["error"]
    # 강제 덮어쓰기 시 이전 내용 백업
    r = client.put(f"/api/projects/{p['id']}", json={"project": stale, "base_rev": stale["rev"], "force": True})
    assert r.status_code == 200
    names = [b["name"] for b in client.get(f"/api/projects/{p['id']}/backups").json()["backups"]]
    assert any("before-overwrite" in n for n in names)
    # 전체 백업 ZIP, 복제(기획·대본만)
    assert client.post(f"/api/projects/{p['id']}/backup").status_code == 200
    d = client.post(f"/api/projects/{p['id']}/duplicate", json={"mode": "plan"}).json()["project"]
    assert d["id"] != p["id"] and d["idea"]["topic"] == got["idea"]["topic"]
    # 손상 파일 처리
    assert any(x["id"] == p["id"] for x in client.get("/api/projects").json()["projects"])


def test_project_rejects_secret_in_content(client):
    client.put("/api/secrets", json={"ai_api_key": MOCK_KEY})
    p = new_project(client)
    p["idea"]["notes"] = f"키 메모 {MOCK_KEY}"
    r = client.put(f"/api/projects/{p['id']}", json={"project": p, "base_rev": p["rev"]})
    assert r.status_code == 400 and "API 키" in r.json()["error"]


# ---------------------------------------------------------------- AI 연결

def test_ai_not_configured_shows_need(client):
    s = client.get("/api/settings").json()["settings"]
    s["ai"]["base_url"] = ""
    client.put("/api/settings", json={"settings": s})
    p = new_project(client)
    r = client.post(f"/api/projects/{p['id']}/ai/plan", json={})
    assert r.status_code == 428 and "AI 연결 설정이 필요" in r.json()["error"]


def test_ai_success_and_usage(client, configure_ai, mock_relay):
    configure_ai()
    j = wait_job(client, client.post("/api/ai/test").json())
    assert j["status"] == "done" and j["result"]["reply"] == "OK"
    assert j["result"]["usage"]["input_tokens"] > 0 and j["result"]["cost"] is None  # 단가 미입력 → 비용 표시 안 함
    p = new_project(client)
    p["idea"]["topic"] = "テスト"
    p = save(client, p)
    j = wait_job(client, client.post(f"/api/projects/{p['id']}/ai/plan", json={}).json())
    assert j["status"] == "done" and len(j["result"]["items"]) == 2
    assert j["result"]["log"]["usage"]["output_tokens"] > 0
    # 키가 응답·로그에 없음
    assert MOCK_KEY not in json.dumps(j)
    assert all(MOCK_KEY not in json.dumps(x) for x in mock_relay.LOG)


def test_ai_streaming(client, configure_ai):
    configure_ai(stream=True)
    j = wait_job(client, client.post("/api/ai/test").json())
    assert j["status"] == "done" and j["result"]["reply"] == "OK" and j["result"]["usage"]["output_tokens"] >= 1
    configure_ai(stream=False)


def test_ai_bearer_auth(client, configure_ai):
    configure_ai(auth="bearer")
    assert wait_job(client, client.post("/api/ai/test").json())["status"] == "done"
    configure_ai()


@pytest.mark.parametrize("model,needle", [
    ("mock-401", "인증에 실패"), ("mock-429", "사용 한도"), ("mock-openai", "OpenAI 호환"), ("mock-truncated", "최대 출력"),
])
def test_ai_errors(client, configure_ai, mock_relay, model, needle):
    configure_ai(model=model)
    before = len(mock_relay.LOG)
    j = wait_job(client, client.post("/api/ai/test").json())
    assert j["status"] == "error" and needle in j["error"], j
    assert j["hint"]
    if model == "mock-429":
        assert "37초" in j["hint"]
    assert len(mock_relay.LOG) - before == 1  # 자동 재시도 없음
    configure_ai()


def test_ai_wrong_key(client, configure_ai):
    configure_ai(key="wrong-key-000000")
    j = wait_job(client, client.post("/api/ai/test").json())
    assert j["status"] == "error" and "인증" in j["error"] and "wrong-key-000000" not in json.dumps(j)
    configure_ai()


def test_ai_connection_refused(client, configure_ai):
    configure_ai()
    s = client.get("/api/settings").json()["settings"]
    s["ai"]["base_url"] = "http://127.0.0.1:9"
    client.put("/api/settings", json={"settings": s})
    j = wait_job(client, client.post("/api/ai/test").json())
    assert j["status"] == "error" and "연결하지 못했" in j["error"]
    configure_ai()


def test_ai_timeout_and_cancel(client, configure_ai):
    configure_ai(model="mock-slow", timeout=2)
    t0 = time.time()
    j = wait_job(client, client.post("/api/ai/test").json())
    assert j["status"] == "error" and "응답이 오지 않아" in j["error"] and time.time() - t0 < 10
    configure_ai(model="mock-slow", timeout=60)
    j = client.post("/api/ai/test").json()
    time.sleep(0.5)
    client.post(f"/api/jobs/{j['id']}/cancel")
    j = wait_job(client, j, timeout=10)
    assert j["status"] == "cancelled"
    configure_ai()


def test_partial_regen_rejects_out_of_scope(client, configure_ai):
    configure_ai()
    p = new_project(client)
    p["plan"] = {"approach_name_ko": "x", "updated_at": "1"}
    p = save(client, p)
    j = wait_job(client, client.post(f"/api/projects/{p['id']}/ai/script", json={}).json())
    p["script"]["scenes"] = j["result"]["scenes"]
    p = save(client, p)
    lid = p["script"]["scenes"][0]["lines"][0]["id"]
    j = wait_job(client, client.post(f"/api/projects/{p['id']}/ai/partial",
                                     json={"scope": {"line_ids": [lid]}, "preset": "hook"}).json())
    assert j["status"] == "done"
    assert [c["line_id"] for c in j["result"]["changes"]] == [lid]
    assert j["result"]["rejected_out_of_scope"]


# ---------------------------------------------------------------- 음성·자막·내보내기 전체 흐름

@needs_ja
def test_full_flow_original_preserved_export(client, configure_ai):
    configure_ai()
    ex = client.post("/api/examples/import", json={}).json()
    p, status = ex["project"], ex["status"]
    assert status["steps"]["audio"]["state"] == "todo"  # 원본만 있고 처리 전
    pid = p["id"]
    orig_file = storage.media_path(pid, p["audio"]["original"]["file"])
    sha_before = p["audio"]["original"]["sha256"]
    body = {"mode": "natural", "params": {}, "protect_ranges": [], "custom_ranges": []}
    an = wait_job(client, client.post(f"/api/projects/{pid}/audio/analyze", json=body).json())["result"]
    # 장면 2의 의도적 멈춤(1500ms)이 자동으로 남는다
    assert an["auto_holds"] and an["auto_holds"][0][2] == 1500
    held = [r for r in an["regions"] if r.get("custom_label", "").startswith("장면 2")]
    # 예제 음성에서 장면 2 뒤의 실제 쉼은 약 0.4초 → 줄이지 않고 그대로 두며, 짧다는 안내를 붙인다
    assert held and not held[0]["remove"] and any("짧습니다" in f for f in held[0]["flags"])
    # 남길 길이보다 긴 쉼이면 정확히 그 길이로 남긴다
    long_gap = max((r for r in an["regions"] if r["kind"] == "gap"), key=lambda r: r["length"])
    an2 = wait_job(client, client.post(f"/api/projects/{pid}/audio/analyze", json=dict(body, custom_ranges=[[long_gap["start"], long_gap["end"], 1500]])).json())["result"]
    r2 = next(r for r in an2["regions"] if r["start"] == long_gap["start"])
    assert abs(r2["new_length"] - 1.5) < 0.02
    j = wait_job(client, client.post(f"/api/projects/{pid}/audio/apply", json=body).json())
    proc = j["result"]["processed"]
    assert proc["verify"]["ok"] and proc["duration"] < p["audio"]["original"]["duration"]
    from app.util import sha256_file
    assert sha256_file(orig_file) == sha_before  # 원본 보존
    p["audio"]["processed"] = proc
    p = save(client, p)
    j = wait_job(client, client.post(f"/api/projects/{pid}/subtitles/align", json={}).json())
    r = j["result"]
    p["subtitles"].update(cues=r["cues"], based_on_audio_version=r["audio_version"],
                          based_on_display_hash=r["display_hash"], method=r["method"])
    p = save(client, p)
    st = client.get(f"/api/projects/{pid}").json()["status"]
    assert st["steps"]["subtitles"]["state"] == "done"
    # 음성을 '빠르게'로 다시 → 자막 '재생성 필요'
    body2 = dict(body, mode="fast")
    proc2 = wait_job(client, client.post(f"/api/projects/{pid}/audio/apply", json=body2).json())["result"]["processed"]
    assert proc2["version"] == 2
    p["audio"]["processed_history"] = [p["audio"]["processed"]]
    p["audio"]["processed"] = proc2
    # 자막 하나를 수동 수정
    p["subtitles"]["cues"][6]["text"] = "手修正テキスト"
    p["subtitles"]["cues"][6]["manual_text"] = True
    p = save(client, p)
    st = client.get(f"/api/projects/{pid}").json()["status"]
    assert st["steps"]["subtitles"]["state"] == "stale"
    # 다시 맞추기: 수동 수정이 diff에 보고되고, 유지 버전이 제공된다
    r2 = wait_job(client, client.post(f"/api/projects/{pid}/subtitles/align", json={}).json())["result"]
    assert r2["diff"]["manual"] and r2["diff"]["merged_keep_manual_text"][6]["text"] == "手修正テキスト"
    assert r2["cues"][-1]["end"] <= proc2["duration"] + 0.001
    assert r2["cues"][-1]["end"] < p["subtitles"]["cues"][-1]["end"] - 0.5  # 새 음성(더 짧음)에 맞춰 이동
    p["subtitles"].update(cues=r2["diff"]["merged_keep_manual_text"], based_on_audio_version=2,
                          based_on_display_hash=r2["display_hash"])
    # 대본 변경 → 음성 stale
    p2 = json.loads(json.dumps(p))
    p2["script"]["scenes"][0]["lines"][0]["tts"] += "ね"
    st2 = storage.compute_status(p2)
    assert st2["steps"]["audio"]["state"] == "stale"
    p = save(client, p)
    # 소스: 이미지 파일 + 위험한 파일명
    png = _png_bytes()
    files = [("files", ('..\\..\\evil"; calc.exe & -i x.png', png, "image/png"))]
    up = client.post(f"/api/projects/{pid}/sources/upload", files=files, data={"scene_id": p["script"]["scenes"][0]["id"]}).json()
    assert up["items"] and not up["errors"]
    it = up["items"][0]
    assert it["file"].startswith("media/sources/src_") and ".." not in it["file"] and "\\" not in it["file"]
    assert storage.media_path(pid, it["file"]).exists()
    bad = client.post(f"/api/projects/{pid}/sources/upload", files=[("files", ("fake.png", b"not an image", "image/png"))]).json()
    assert bad["errors"] and not bad["items"]  # 열리지 않는 파일은 '확보' 처리 안 함
    p["sources"]["items"] = [it]
    p["publish"].update(title="タイトル", description="説明")
    p = save(client, p)
    # 내보내기
    ex = wait_job(client, client.post(f"/api/projects/{pid}/export", json={"zip": True}).json(), timeout=180)
    assert ex["status"] == "done", ex
    res = ex["result"]
    assert res["verify"]["all_ok"] and not res["verify"]["secret_found"]
    folder = Path(res["folder"])
    for name in ("01_FINAL_최종음성_무음정리.wav", "02_FINAL_자막_ja.srt", "03_원본음성_타입캐스트_ORIGINAL.wav",
                 "05_장면표_시간_소스.csv", "06_대본_일본어_한국어.txt", "07_타입캐스트_낭독문.txt",
                 "08_게시정보_제목_설명_크레딧.txt", "00_먼저_읽기_CapCut_작업순서.txt"):
        assert (folder / name).exists(), name
    srt = (folder / "02_FINAL_자막_ja.srt").read_bytes().decode("utf-8")
    assert "手修正テキスト" in srt
    tts = (folder / "07_타입캐스트_낭독문.txt").read_text(encoding="utf-8")
    assert "장면" not in tts and not any("가" <= c <= "힣" for c in tts)  # 한국어·지시 없음
    # API 키가 어디에도 없음(프로젝트 폴더 전체 + ZIP)
    for f in storage.project_dir(pid).rglob("*"):
        if f.is_file():
            assert MOCK_KEY.encode() not in f.read_bytes(), f
    with zipfile.ZipFile(res["zip"]) as z:
        assert z.testzip() is None
        for n in z.namelist():
            assert MOCK_KEY.encode() not in z.read(n)
    # ZIP으로 다시 열기
    imp = client.post("/api/projects/import", files={"file": ("p.zip", Path(res["zip"]).read_bytes(), "application/zip")}).json()
    q = imp["project"]
    assert q["id"] != pid and q["subtitles"]["cues"] and storage.media_path(q["id"], q["audio"]["processed"]["file"]).exists()


def _png_bytes():
    from app.util import ffmpeg_exe, run_tool
    r = run_tool([ffmpeg_exe(), "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1",
                  "-f", "image2pipe", "-vcodec", "png", "-"])
    return r.stdout


# ---------------------------------------------------------------- 보안

def test_security_guards(client):
    from fastapi.testclient import TestClient
    from app.main import app
    p = new_project(client)
    bare = TestClient(app)
    assert bare.post("/api/projects", json={}).status_code == 403  # 전용 헤더 없음 → 거부
    assert bare.get("/api/health", headers={"host": "evil.example"}).status_code == 403  # DNS 리바인딩 차단
    r = client.get(f"/api/projects/{p['id']}/media", params={"path": "../../../cfg/secrets.dat"})
    assert r.status_code == 403
    assert client.get("/api/projects/..%2F..%2Fx").status_code in (403, 404)
    s = client.get("/api/settings").json()
    assert "ai_api_key" not in json.dumps(s["settings"]) and MOCK_KEY not in json.dumps(s)
    r = client.put("/api/secrets", json={"ai_extra_headers": {"X-A\r\nInjected": "v"}})
    assert r.status_code == 400
    # 위험한 음성 파일명: 명령어로 실행되지 않고 안전한 이름으로 저장
    if (FIX / "synthetic_speech.wav").exists():
        data = (FIX / "synthetic_speech.wav").read_bytes()
        r = client.post(f"/api/projects/{p['id']}/audio/upload", files={"file": ("x & calc.exe; rm -rf  .wav", data, "audio/wav")})
        assert r.status_code == 200
        o = r.json()["original"]
        assert o["file"] == "media/audio/original_001.wav"


def test_secrets_encrypted_on_disk(client):
    client.put("/api/secrets", json={"ai_api_key": MOCK_KEY})
    raw = config.SECRETS_FILE.read_bytes()
    assert MOCK_KEY.encode() not in raw
    import sys
    if sys.platform == "win32":
        assert raw.startswith(b"DPAPI1:")
    assert MOCK_KEY.encode() not in config.SETTINGS_FILE.read_bytes()


def test_cost_only_when_price_given(client, configure_ai):
    configure_ai()
    s = client.get("/api/settings").json()["settings"]
    s["ai"].update(price_input_per_mtok=4, price_output_per_mtok=20)
    client.put("/api/settings", json={"settings": s})
    j = wait_job(client, client.post("/api/ai/test").json())
    c = j["result"]["cost"]
    u = j["result"]["usage"]
    assert c and abs(c["estimate"] - (u["input_tokens"] * 4 + u["output_tokens"] * 20) / 1e6) < 1e-9 and "추정" in c["note"]
    s["ai"].update(price_input_per_mtok=None, price_output_per_mtok=None)
    client.put("/api/settings", json={"settings": s})
