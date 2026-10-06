"""단계 간 최신 상태(의존 관계)·내보내기 차단·데이터 마이그레이션 검증."""
import json

import pytest

from conftest import FIX, wait_job
from app import export, storage
from app.util import UserError

needs_ja = pytest.mark.skipif(not (FIX / "ja_tts_sample.wav").exists(), reason="tests/make_fixtures.py 실행 필요")


def _status(p):
    return storage.compute_status(p)["steps"]


def test_plan_change_marks_script_stale_and_ack():
    p = storage.empty_project("x")
    p["plan"] = {"approach_name_ko": "A", "format_ko": "질문형", "updated_at": "1"}
    p["script"]["scenes"] = [{"id": "s", "lines": [{"id": "l", "display": "あ", "tts": "あ", "ko": "아"}]}]
    p["script"]["based_on_plan_hash"] = storage.plan_hash(p)
    assert _status(p)["script"]["state"] == "done"
    p["plan"]["updated_at"] = "2"  # 시각만 바뀜 → 내용 동일
    assert _status(p)["script"]["state"] == "done"
    p["plan"]["format_ko"] = "완전히 다른 이야기형"
    assert _status(p)["script"]["state"] == "stale"
    p["script"]["based_on_plan_hash"] = storage.plan_hash(p)  # '현재 대본 유지(확인)'
    assert _status(p)["script"]["state"] == "done"


def _with_audio():
    p = storage.empty_project("x")
    p["script"]["scenes"] = [{"id": "s", "lines": [{"id": "l", "display": "こんにちは", "tts": "こんにちは", "ko": "안녕"}]}]
    p["audio"]["original"] = {"sha256": "abc", "script_tts_hash": storage.tts_hash(p), "file": "x"}
    p["audio"]["processed"] = {"source_sha": "abc", "version": 1, "file": "y"}
    return p


def test_korean_note_change_does_not_require_tts():
    p = _with_audio()
    p["script"]["scenes"][0]["lines"][0]["ko"] = "안녕하세요(수정)"
    p["script"]["scenes"][0]["lines"][0]["reading_note"] = "메모 수정"
    assert _status(p)["audio"]["state"] == "done"
    assert not storage.audio_mismatch(p)["mismatch"]


def test_tts_change_mismatch_and_ack():
    p = _with_audio()
    p["script"]["scenes"][0]["lines"][0]["tts"] = "こんばんは"
    assert storage.audio_mismatch(p)["mismatch"] and _status(p)["audio"]["state"] == "stale"
    assert _status(p)["export"]["state"] == "todo"
    p["audio"]["mismatch_ack"] = {"tts_hash": storage.tts_hash(p), "audio_sha": "abc", "reason": ""}
    assert not storage.audio_mismatch(p)["acknowledged"]  # 이유 없이는 예외 처리 안 됨
    p["audio"]["mismatch_ack"]["reason"] = "문장부호만 바꿈"
    assert storage.audio_mismatch(p)["acknowledged"] and _status(p)["audio"]["state"] == "done"
    p["script"]["scenes"][0]["lines"][0]["tts"] = "おはよう"  # 확인 후 또 바뀌면 다시 확인 필요
    assert not storage.audio_mismatch(p)["acknowledged"]


@needs_ja
def test_realign_with_old_audio_still_blocks_export(client):
    """TTS를 바꾼 뒤 기존 음성으로 자막만 다시 맞춰도, 음성-대본 불일치면 내보내기가 막힌다."""
    pr = client.post("/api/examples/import", json={}).json()["project"]
    pid = pr["id"]
    body = {"mode": "natural", "params": {}, "protect_ranges": [], "custom_ranges": []}
    pr["audio"]["processed"] = wait_job(client, client.post(f"/api/projects/{pid}/audio/apply", json=body).json())["result"]["processed"]
    pr["script"]["scenes"][0]["lines"][0]["tts"] += "ね"  # 낭독문 변경(음성은 그대로)
    pr["publish"].update(title="t", description="d")
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    r = wait_job(client, client.post(f"/api/projects/{pid}/subtitles/align", json={}).json())["result"]
    fr = r["proposal"]["fresh"]
    pr["subtitles"].update(cues=fr["cues"], line_texts=fr["line_texts"], based_on_audio_version=r["audio_version"],
                           based_on_display_hash=r["display_hash"], based_on_tts_hash=r["tts_hash"])
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    st = client.get(f"/api/projects/{pid}").json()["status"]["steps"]
    assert st["subtitles"]["state"] == "done" and st["audio"]["state"] == "stale"
    pc = client.get(f"/api/projects/{pid}/export/precheck").json()
    assert pc["blocking"] and any("TTS" in i["message"] for i in pc["items"])
    with pytest.raises(UserError):
        export.build_package(pid, allow_stale=True)
    # 이유를 기록하면 경고와 함께 내보낼 수 있다
    pr["audio"]["mismatch_ack"] = {"tts_hash": storage.tts_hash(pr), "audio_sha": pr["audio"]["original"]["sha256"],
                                   "reason": "어미 하나 추가, 음성은 재사용"}
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    pc = client.get(f"/api/projects/{pid}/export/precheck").json()
    assert not pc["blocking"] and any("사용자가 확인" in i["message"] for i in pc["items"])
    res = export.build_package(pid, make_zip=False, allow_stale=True)
    # 내보낸 뒤 내용이 바뀌면 내보내기 단계가 '최신 아님'
    pr["last_export"] = {"at": res["exported_at"], "basis": res["basis"], "folder": res["folder"]}
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    assert client.get(f"/api/projects/{pid}").json()["status"]["steps"]["export"]["state"] == "done"
    pr["publish"]["title"] = "바뀐 제목"
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    assert client.get(f"/api/projects/{pid}").json()["status"]["steps"]["export"]["state"] == "stale"


def test_migration_from_v1_keeps_content_and_backs_up():
    p = storage.create_project("구버전")
    pid = p["id"]
    old = json.loads(storage.project_file(pid).read_text(encoding="utf-8"))
    old["schema"] = 1
    old["plan"] = {"approach_name_ko": "A"}
    old["script"]["scenes"] = [{"id": "s", "lines": [{"id": "l0", "display": "あいう\nえお", "tts": "あいうえお", "ko": "-"}]}]
    old["subtitles"] = {"cues": [{"id": "c1", "line_id": "l0", "part": 0, "text": "あいう", "start": 0, "end": 1},
                                 {"id": "c2", "line_id": "l0", "part": 0.5, "text": "えお", "start": 1, "end": 2, "manual_text": True}],
                        "based_on_audio_version": 1}
    old.pop("last_export", None)
    storage.project_file(pid).write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    p2 = storage.load_project(pid)
    assert p2["schema"] == storage.SCHEMA and p2["rev"] == old["rev"]
    assert p2["script"]["scenes"] == old["script"]["scenes"] and p2["plan"] == old["plan"]
    assert [c["text"] for c in p2["subtitles"]["cues"]] == ["あいう", "えお"]
    assert p2["subtitles"]["cues"][0]["src"] == [{"line_id": "l0", "a": 0, "b": 3}]
    assert p2["subtitles"]["cues"][1]["manual_struct"]
    assert p2["script"]["based_on_plan_hash"] == storage.plan_hash(p2)
    backups = [b["name"] for b in storage.list_backups(pid)]
    assert any("before-migration" in n for n in backups)
    raw_backup = next((storage.project_dir(pid) / "backups").glob("*before-migration*")).read_text(encoding="utf-8")
    assert json.loads(raw_backup)["schema"] == 1  # 변환 전 원본 그대로
    # 다시 열어도 변환·백업이 반복되지 않음
    storage.load_project(pid)
    assert len([n for n in (b["name"] for b in storage.list_backups(pid)) if "before-migration" in n]) == 1
