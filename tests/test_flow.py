"""Google Flow 클립 계획·프롬프트·생성 영상 연결·내보내기 검증."""
import json
from pathlib import Path

import pytest

from conftest import FIX, wait_job
from app import flow, storage

needs_ja = pytest.mark.skipif(not (FIX / "ja_tts_sample.wav").exists(), reason="tests/make_fixtures.py 실행 필요")


def test_fit_and_split():
    assert [flow.fit_duration(x) for x in (3.0, 4.0, 4.5, 6.0, 7.9, 9.99, 10.2)] == [4, 4, 6, 6, 8, 10, None]
    segs = flow._split_scene(0, 13.3, [4.2, 9.0])
    assert all(flow.fit_duration(b - a) for a, b in segs)
    assert segs[0][0] == 0 and segs[-1][1] == 13.3
    assert all(p in (0, 4.2, 9.0, 13.3) for s in segs for p in s)  # 문장 경계에서만 나눔
    long = flow._split_scene(0, 25, [])  # 경계 없는 긴 구간 → 10초 이하 균등 분할
    assert len(long) == 3 and all(b - a <= 10 for a, b in long)


def test_plan_covers_timeline_estimated():
    p = json.loads((Path(__file__).parent.parent / "examples" / "example_project.json").read_text(encoding="utf-8"))
    r = flow.plan_clips(p)
    clips = r["clips"]
    assert r["timing"] == "추정" and clips
    for a, b in zip(clips, clips[1:]):
        assert abs(a["end"] - b["start"]) < 1e-6  # 빈틈 없이 이어짐
    for c in clips:
        assert c["duration"] in (4, 6, 8, 10) and c["duration"] + 0.05 >= c["need_s"]
    # 다시 계산해도 같은 구간이면 프롬프트 유지
    clips[0]["prompt_en"] = "keep me"
    r2 = flow.plan_clips(p, clips)
    assert r2["clips"][0]["prompt_en"] == "keep me" and r2["clips"][0]["id"] == clips[0]["id"]


@needs_ja
def test_flow_full_flow(client, configure_ai, tmp_path):
    configure_ai()
    pr = client.post("/api/examples/import", json={}).json()["project"]
    pid = pr["id"]
    body = {"mode": "natural", "params": {}, "protect_ranges": [], "custom_ranges": []}
    pr["audio"]["processed"] = wait_job(client, client.post(f"/api/projects/{pid}/audio/apply", json=body).json())["result"]["processed"]
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    r = wait_job(client, client.post(f"/api/projects/{pid}/subtitles/align", json={}).json())["result"]
    pr["subtitles"].update(cues=r["cues"], based_on_audio_version=r["audio_version"], based_on_display_hash=r["display_hash"])
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    plan = client.post(f"/api/projects/{pid}/flow/plan").json()
    assert plan["timing"] == "실제"
    dur = pr["audio"]["processed"]["duration"]
    assert plan["clips"][0]["start"] == 0 and abs(plan["clips"][-1]["end"] - dur) < 0.01  # 최종 음성 전체를 덮음
    pr["flow"] = {"clips": plan["clips"], "based_on": plan["based_on"], "timing": plan["timing"], "style": None}
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    st = client.get(f"/api/projects/{pid}").json()["status"]["steps"]["sources"]
    assert st["state"] == "todo" and any("프롬프트가 없는 클립" in n for n in st["notes"])
    # AI 프롬프트(모의)
    j = wait_job(client, client.post(f"/api/projects/{pid}/ai/flow", json={}).json())
    assert j["status"] == "done" and len(j["result"]["clips"]) == len(plan["clips"]) and j["result"]["style"]["look_en"]
    one = wait_job(client, client.post(f"/api/projects/{pid}/ai/flow", json={"clip_ids": [plan["clips"][1]["id"]]}).json())
    assert [c["clip_id"] for c in one["result"]["clips"]] == [plan["clips"][1]["id"]]  # 지정한 클립만
    for out in j["result"]["clips"]:
        c = next(x for x in pr["flow"]["clips"] if x["id"] == out["clip_id"])
        c.update(prompt_en=out["prompt_en"], prompt_ko=out["prompt_ko"], prompt_for_duration=c["duration"])
    # 생성 영상(시험용 mp4)을 각 클립에 연결
    from app.util import ffmpeg_exe, run_tool
    for c in pr["flow"]["clips"]:
        mp4 = tmp_path / f"{c['id']}.mp4"
        run_tool([ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=s=360x640:d={c['duration']}",
                  "-pix_fmt", "yuv420p", str(mp4)])
        up = client.post(f"/api/projects/{pid}/sources/upload", files=[("files", (mp4.name, mp4.read_bytes(), "video/mp4"))],
                         data={"scene_id": c["scene_id"], "clip_id": c["id"]}).json()
        it = up["items"][0]
        assert it["provider"] == "flow" and it["ai_generated"] and abs(it["duration"] - c["duration"]) < 0.2
        pr["sources"]["items"].append(it)
        c["item_id"] = it["id"]
    pr["publish"].update(title="t", description="d")
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    st = client.get(f"/api/projects/{pid}").json()["status"]["steps"]["sources"]
    assert st["state"] == "done", st
    lf = client.post(f"/api/projects/{pid}/flow/lastframe", json={"item_id": pr["flow"]["clips"][0]["item_id"]}).json()
    assert storage.media_path(pid, lf["file"]).stat().st_size > 0
    ex = wait_job(client, client.post(f"/api/projects/{pid}/export", json={"zip": False}).json(), timeout=180)
    assert ex["status"] == "done", ex
    folder = Path(ex["result"]["folder"])
    names = sorted(p.name for p in (folder / "04_영상소스").iterdir())
    assert names[0].startswith("S01_C01_") and names[0].endswith("s_flow.mp4")
    assert (folder / "05b_Flow_클립_배치표.csv").exists() and (folder / "10_Flow_영상프롬프트.txt").exists()
    assert "[MOCK] clip" in (folder / "10_Flow_영상프롬프트.txt").read_text(encoding="utf-8")
    assert "합성된 콘텐츠" in (folder / "08_게시정보_제목_설명_크레딧.txt").read_text(encoding="utf-8")
    assert ex["result"]["verify"]["all_ok"]
    # 자막 시간이 바뀌면 클립 계획 '재생성 필요'
    pr["subtitles"]["cues"][0]["end"] += 0.5
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    st = client.get(f"/api/projects/{pid}").json()["status"]["steps"]["sources"]
    assert st["state"] == "stale" and "클립 계획" in st["notes"][0]
