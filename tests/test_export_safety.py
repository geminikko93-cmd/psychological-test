"""내보내기 안전성: 이전 결과·사용자 파일 보존, 검증 실패·취소를 성공으로 처리하지 않음, 스냅샷 고정."""
import threading
from pathlib import Path

import pytest

from conftest import FIX, wait_job
from app import export, storage
from app.util import UserError

needs_ja = pytest.mark.skipif(not (FIX / "ja_tts_sample.wav").exists(), reason="tests/make_fixtures.py 실행 필요")


@pytest.fixture
def ready_project(client):
    """음성·자막까지 끝난 예제 프로젝트(내보내기 가능한 상태)."""
    pr = client.post("/api/examples/import", json={}).json()["project"]
    pid = pr["id"]
    body = {"mode": "natural", "params": {}, "protect_ranges": [], "custom_ranges": []}
    pr["audio"]["processed"] = wait_job(client, client.post(f"/api/projects/{pid}/audio/apply", json=body).json())["result"]["processed"]
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    r = wait_job(client, client.post(f"/api/projects/{pid}/subtitles/align", json={}).json())["result"]
    fr = r["proposal"]["fresh"]
    pr["subtitles"].update(cues=fr["cues"], line_texts=fr["line_texts"], removed=[], based_on_audio_version=r["audio_version"],
                           based_on_display_hash=r["display_hash"], based_on_tts_hash=r["tts_hash"])
    pr["publish"].update(title="t", description="d")
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    return pid


class FakeJob:
    """취소 시험용: n번째 확인 지점에서 취소, 또는 그 지점에서 콜백 실행."""

    def __init__(self, cancel_at=None, on_step=None):
        self.n, self.cancel_at, self.on_step = 0, cancel_at, on_step

    def check(self):
        self.n += 1
        if self.on_step:
            self.on_step(self.n)
        if self.cancel_at is not None and self.n >= self.cancel_at:
            raise UserError("사용자가 작업을 취소했습니다.", status=499)

    def report(self, *a, **k):
        pass

    def cancelled(self):
        return self.cancel_at is not None and self.n >= self.cancel_at


def _exports(pid):
    return sorted(p.name for p in (storage.project_dir(pid) / "exports").iterdir())


@needs_ja
def test_reexport_same_minute_keeps_previous_and_user_files(ready_project):
    pid = ready_project
    r1 = export.build_package(pid, make_zip=True)
    user_file = Path(r1["folder"]) / "내가_추가한_메모.txt"
    user_file.write_text("사용자 파일", encoding="utf-8")
    r2 = export.build_package(pid, make_zip=True)  # 같은 분 안에 다시 내보내기
    assert r1["folder"] != r2["folder"] and r1["zip"] != r2["zip"]
    assert user_file.exists() and Path(r1["zip"]).exists()
    assert r2["version"] == r1["version"] + 1
    assert not [n for n in _exports(pid) if n.startswith(".tmp")]


@needs_ja
def test_concurrent_exports_get_distinct_folders(ready_project):
    pid = ready_project
    results, errors = [], []

    def run():
        try:
            results.append(export.build_package(pid, make_zip=False))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=run) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors
    folders = {r["folder"] for r in results}
    assert len(folders) == 3 and all(r["verify"]["all_ok"] for r in results)


@needs_ja
def test_corrupt_media_fails_and_is_not_success(ready_project, client):
    pid = ready_project
    p = storage.load_project(pid)
    # 확보된 소스 파일을 손상된 내용으로 바꿔 둔다(사전 점검은 파일 존재만 확인)
    item_path = storage.media_path(pid, "media/sources/src_broken.mp4")
    item_path.write_bytes(b"not a video at all")
    p["sources"]["items"].append({"id": "src_broken", "scene_id": p["script"]["scenes"][0]["id"], "provider": "local",
                                  "kind": "video", "file": "media/sources/src_broken.mp4", "orig_name": "broken.mp4",
                                  "status": "acquired", "rights_checked": True, "credit_text": ""})
    storage.save_project(pid, p, p["rev"])
    before = _exports(pid)
    with pytest.raises(UserError) as ei:
        export.build_package(pid, make_zip=True)
    assert "검증" in ei.value.message
    assert _exports(pid) == before  # 실패한 결과물이 남지 않음(임시 폴더도 정리)
    # API로도: 작업 상태가 error이며 결과(성공 정보)가 없다
    j = wait_job(client, client.post(f"/api/projects/{pid}/export", json={"zip": True}).json(), timeout=120)
    assert j["status"] == "error" and j["result"] is None


@needs_ja
def test_cancel_removes_only_this_export_temp(ready_project):
    pid = ready_project
    r1 = export.build_package(pid, make_zip=False)
    before = _exports(pid)
    with pytest.raises(UserError) as ei:
        export.build_package(pid, make_zip=True, job=FakeJob(cancel_at=4))
    assert ei.value.status == 499
    assert _exports(pid) == before and Path(r1["folder"]).exists()


@needs_ja
def test_snapshot_fixed_during_export(ready_project):
    """내보내는 도중 프로젝트를 고쳐도 이번 결과물은 시작 시점 내용으로 일관된다."""
    pid = ready_project
    p0 = storage.load_project(pid)
    first_line = p0["script"]["scenes"][0]["lines"][0]["display"].replace("\n", "")

    def edit(n):
        if n == 2:
            q = storage.load_project(pid)
            q["script"]["scenes"][0]["lines"][0]["display"] = "途中で変更した文"
            q["script"]["scenes"][0]["lines"][0]["tts"] = "とちゅうでへんこうしたぶん"
            storage.save_project(pid, q, q["rev"])

    r = export.build_package(pid, make_zip=False, job=FakeJob(on_step=edit))
    folder = Path(r["folder"])
    script = (folder / "06_대본_일본어_한국어.txt").read_text(encoding="utf-8")
    tts = (folder / "07_타입캐스트_낭독문.txt").read_text(encoding="utf-8")
    assert first_line in script and "途中で変更した文" not in script
    assert "とちゅう" not in tts
