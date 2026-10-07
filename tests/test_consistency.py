"""영상 일관성 관리 검증: 고정/가변 분리, 잠금 유지, 클립 계획 재계산, 수동 편집·되돌리기,
실제 사용 구간 끝 프레임, 기준 자료·원본 변경 시 재검토, 소재 재사용 내보내기, 예전 프로젝트 호환, 채널 추천값 비적용."""
import copy
import csv
import io
import json
import zipfile
from pathlib import Path

import pytest

from conftest import FIX, ROOT, wait_job
from app import channel, flow, storage
from app import visual as V
from app.util import ffmpeg_exe, run_tool

needs_ja = pytest.mark.skipif(not (FIX / "ja_tts_sample.wav").exists(), reason="tests/make_fixtures.py 실행 필요")
EX = ROOT / "examples"


def small_room() -> dict:
    return json.loads((EX / "example_small_room.json").read_text(encoding="utf-8"))


def planned(p: dict) -> dict:
    p = copy.deepcopy(p)
    r = flow.plan_clips(p)
    p["flow"] = {"clips": r["clips"], "based_on": r["based_on"], "timing": r["timing"]}
    return p


def lock_all(p: dict) -> dict:
    p["visual"]["style"].update(locked=True, version=1)
    for e in p["visual"]["elements"]:
        e.update(locked=True, status="confirmed", version=1)
    return p


def clip(p, scene_no, idx=1):
    return next(c for c in p["flow"]["clips"] if c["scene_no"] == scene_no and c["index_in_scene"] == idx)


def make_video(path: Path, segs: list[tuple[str, float]], fps: int = 30) -> None:
    """색이 바뀌는 시험 영상(예: 빨강 5초 → 초록 0.4초 → 파랑 2.6초)."""
    args = [ffmpeg_exe(), "-y", "-v", "error"]
    for color, d in segs:
        args += ["-f", "lavfi", "-i", f"color=c={color}:s=64x64:r={fps}:d={d}"]
    args += ["-filter_complex", "".join(f"[{i}:v]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0",
             "-pix_fmt", "yuv420p", "-c:v", "libx264", "-g", "1", str(path)]
    r = run_tool(args)
    assert r.returncode == 0, r.stderr


def make_png(path: Path, color: str) -> None:
    r = run_tool([ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=90x160", "-frames:v", "1", str(path)])
    assert r.returncode == 0, r.stderr


def pixel(path: Path) -> tuple[int, int, int]:
    r = run_tool([ffmpeg_exe(), "-v", "error", "-i", str(path), "-vf", "crop=1:1:32:32", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    return tuple(r.stdout[:3])


def dominant(rgb) -> str:
    return "rgb"[max(range(3), key=lambda i: rgb[i])]


# ---------------------------------------------------------------- 고정/가변 분리·충돌

def test_compose_inserts_fixed_blocks_verbatim():
    p = lock_all(planned(small_room()))
    c = clip(p, 3)  # 결과 A(창문), 재료 방식
    c["var_en"] = "0-3s: slow push-in toward the window; 3-6s: rain streaks on the glass."
    out = V.compose(p, c)["prompt_en"]
    win = next(e for e in p["visual"]["elements"] if e["id"] == "el_ex_window")
    room = next(e for e in p["visual"]["elements"] if e["id"] == "el_ex_room")
    assert win["fixed_en"] in out and room["fixed_en"] in out          # 저장된 원문 그대로
    assert p["visual"]["style"]["style_en"] in out                    # 화풍 블록 원문
    assert "dusty coral" in out                                       # 회차 강조색
    assert c["var_en"] in out
    assert "No on-screen text" in out and "No dialogue" in out and "9:16" in out
    # 실제로 연결된 요소만: 시계·책장 묘사는 들어가지 않음
    clock = next(e for e in p["visual"]["elements"] if e["id"] == "el_ex_clock")
    assert clock["fixed_en"] not in out
    # 순서: 고정 요소 → 장소 → 스타일 → 가변 → 제약
    assert out.index(win["fixed_en"]) < out.index(room["fixed_en"]) < out.index("Visual style") < out.index(c["var_en"]) < out.index("No on-screen text")
    # 선택 화면 전용 제약
    ch = clip(p, 2)
    ch["gen_mode"], ch["var_en"] = "text", "static wide shot"
    assert V.CHOICE_CONSTRAINT in V.compose(p, ch)["prompt_en"]
    assert V.CHOICE_CONSTRAINT not in out
    # 생성 방식별 조정: 첫 프레임 방식은 시작 프레임 안내, 재료 방식은 이미지 역할
    c["gen_mode"] = "first_frame"
    assert "provided start frame" in V.compose(p, c)["prompt_en"]


def test_conflicts_and_intentional_change():
    p = planned(small_room())
    p["visual"]["elements"].append({"id": "el_p", "name": "Mika", "type": "person", "locked": True,
                                    "fixed_en": "a woman with short black hair wearing a beige linen shirt and round glasses",
                                    "must_keep": ["beige linen shirt"]})
    c = clip(p, 1)
    c["element_ids"] = c["element_ids"] + ["el_p"]
    c["var_en"] = "0-3s: she puts on a black jacket and looks at the window"
    cf = V.find_conflicts(p, c)
    assert any("jacket" in x["message_ko"] for x in cf)
    c["var_en"] = "0-3s: she smooths her navy shirt"
    assert any("navy shirt" in x["message_ko"] or "beige" in x["message_ko"] for x in V.find_conflicts(p, c))
    # 의도적 변경으로 기록하면 충돌 아님 + 프롬프트에 별도 문장으로 들어감
    c["var_en"] = "0-3s: she puts on a black jacket"
    c["changes"] = [{"kind": "outfit", "en": "she wears a black jacket over the shirt in this shot only", "ko": "이 장면만 검은 재킷"}]
    assert V.find_conflicts(p, c) == []
    assert "Intentional change for this shot only" in V.compose(p, c)["prompt_en"]
    # '같은 사람' 표현·일본어 글자 경고
    c["var_en"] = "same person as before, a sign that says 窓"
    w = " ".join(V.find_warnings(p, c))
    assert "same person" in w and "일본어" in w


# ---------------------------------------------------------------- 실제 사용 구간 끝 프레임

def test_used_frame_times_math():
    t = V.used_frame_times(0, 5.3, 8.0, 30)
    assert t["end"]["k"] == 158 and t["end"]["t"] < 5.3 and t["end"]["t"] > 5.2   # 5.3초 직전, 7.8초 아님
    assert t["start"]["k"] == 0
    b = V.used_frame_times(1.0, 4.0, 8.0, 30)  # 경계: 끝이 정확히 5.0초 → 5.0초 프레임은 구간 밖
    assert b["start"]["k"] == 30 and b["end"]["k"] == 149
    over = V.used_frame_times(2.0, 9.0, 8.0, 30)  # 원본보다 긴 구간 → 원본 마지막 프레임까지만
    assert over["end"]["k"] == 239
    odd = V.used_frame_times(0, 5.3, 8.0, 24)
    assert odd["end"]["t"] < 5.3 and (odd["end"]["k"] + 1) / 24 >= 5.3


def _flow_project(client, tmp_path, name="끝 프레임 시험"):
    p = small_room()
    base = storage.empty_project(name)
    base.update(p)
    pr = storage.create_project(name, base)
    pr = planned(pr)
    return storage.save_project(pr["id"], pr, pr["rev"])


def _upload_video(client, pid, c, path):
    up = client.post(f"/api/projects/{pid}/sources/upload", files=[("files", (path.name, path.read_bytes(), "video/mp4"))],
                     data={"scene_id": c["scene_id"], "clip_id": c["id"]}).json()
    return up["items"][0]


def test_lastframe_uses_actual_used_range(client, tmp_path):
    pr = _flow_project(client, tmp_path)
    pid = pr["id"]
    c1 = clip(pr, 1)
    vid = tmp_path / "rgb.mp4"
    make_video(vid, [("red", 5.0), ("green", 0.4), ("blue", 2.6)])  # 8초
    it = _upload_video(client, pid, c1, vid)
    pr["sources"]["items"].append(it)
    c1.update(item_id=it["id"], need_s=5.3, use_start=0)
    pr = storage.save_project(pid, pr, pr["rev"])
    fr = client.post(f"/api/projects/{pid}/flow/frames", json={"clip_id": c1["id"], "kinds": ["last"]}).json()["frames"][0]
    assert 5.2 < fr["t"] < 5.3 and fr["use_len"] == 5.3 and fr["clip_id"] == c1["id"] and fr["item_sig"] == it["sha256"]
    assert dominant(pixel(storage.media_path(pid, fr["file"]))) == "g"   # 사용 구간 끝(초록). 예전 방식이면 파랑
    legacy = client.post(f"/api/projects/{pid}/flow/lastframe", json={"item_id": it["id"]}).json()  # 예전 호출 방식도 같은 결과
    assert dominant(pixel(storage.media_path(pid, legacy["file"]))) == "g"
    # 사용 시작점이 있는 경우: 1초부터 4초 사용 → 끝 5.0초 직전(빨강)
    c1 = clip(pr, 1)
    c1.update(use_start=1.0, need_s=4.0)
    pr = storage.save_project(pid, pr, pr["rev"])
    frs = client.post(f"/api/projects/{pid}/flow/frames", json={"clip_id": c1["id"], "kinds": ["start", "mid", "end"]}).json()["frames"]
    assert [f["kind"] for f in frs] == ["review_start", "review_mid", "review_end"]
    assert frs[0]["t"] == 1.0 and 4.9 < frs[2]["t"] < 5.0
    assert all(dominant(pixel(storage.media_path(pid, f["file"]))) == "r" for f in frs)
    # 사용 구간이 원본보다 길면 원본 마지막 프레임(구간 밖 프레임 선택 안 함)
    c1.update(use_start=3.0, need_s=9.0)
    pr = storage.save_project(pid, pr, pr["rev"])
    f2 = client.post(f"/api/projects/{pid}/flow/frames", json={"clip_id": c1["id"], "kinds": ["last"]}).json()["frames"][0]
    assert f2["clipped"] and 7.9 < f2["t"] < 8.0
    assert dominant(pixel(storage.media_path(pid, f2["file"]))) == "b"


def test_start_frame_and_review_go_stale_when_source_changes(client, tmp_path):
    pr = _flow_project(client, tmp_path, "재검토 시험")
    pid = pr["id"]
    lock_all(pr)
    c1, c3 = clip(pr, 1), clip(pr, 3)
    v1 = tmp_path / "a.mp4"
    make_video(v1, [("red", 2.0), ("green", 2.0), ("blue", 4.0)])
    it = _upload_video(client, pid, c1, v1)
    pr["sources"]["items"].append(it)
    c1.update(item_id=it["id"], var_en="0-3s: slow push-in", prompt_source="composed")
    c1["prompt_en"] = V.compose(pr, c1)["prompt_en"]
    pr = storage.save_project(pid, pr, pr["rev"])
    fr = client.post(f"/api/projects/{pid}/flow/frames", json={"clip_id": c1["id"], "kinds": ["last"]}).json()["frames"][0]
    pr["visual"]["frames"].append(fr)
    c3 = clip(pr, 3)
    it3 = _upload_video(client, pid, c3, v1)
    pr["sources"]["items"].append(it3)
    c3.update(item_id=it3["id"], gen_mode="first_frame", start_frame={"source": "frame", "frame_id": fr["id"]}, continues_previous=True)
    # 두 클립 모두 검수 통과(사람 확인) — 그 시점의 지문을 저장
    for c in (clip(pr, 1), c3):
        c["review"] = {"state": "pass", "checks": {"face": "na"}, "memo": "ok", "basis": V.review_basis(pr, c)}
    assert V.review_state(pr, clip(pr, 1))["state"] == "pass" and V.review_state(pr, c3)["state"] == "pass"
    frame_file = storage.media_path(pid, fr["file"])
    # ① 원본 클립의 사용 구간이 바뀜 → 원본 클립·후속 클립 모두 재검토
    clip(pr, 1)["use_start"] = 0.5
    r1, r3 = V.review_state(pr, clip(pr, 1)), V.review_state(pr, c3)
    assert r1["state"] == "recheck" and "실제 사용 구간이 바뀜" in r1["reasons"]
    assert r3["state"] == "recheck" and any("시작 프레임 재추출" in x for x in r3["reasons"])
    assert frame_file.exists()  # 예전 추출 파일은 지우지 않음
    clip(pr, 1)["use_start"] = 0
    assert V.review_state(pr, c3)["state"] == "pass"
    # ② 원본 영상 교체 → 후속 클립 재검토
    v2 = tmp_path / "b.mp4"
    make_video(v2, [("blue", 8.0)])
    it_new = _upload_video(client, pid, clip(pr, 1), v2)
    pr["sources"]["items"].append(it_new)
    clip(pr, 1)["item_id"] = it_new["id"]
    assert any("영상 파일이 바뀌었습니다" in x for x in V.review_state(pr, c3)["reasons"])
    assert "영상(소재) 파일이 바뀜" in V.review_state(pr, clip(pr, 1))["reasons"]
    # ③ 기준 자료(요소 묘사) 변경 → 그 요소를 쓰는 클립 재검토, 수동 메모는 보존
    clip(pr, 1)["item_id"] = it["id"]
    pr["visual"]["elements"][1]["fixed_en"] += " with a brass handle"
    r = V.review_state(pr, c3)
    assert r["state"] == "recheck" and "등장 요소의 고정 묘사·기준 이미지가 바뀜" in r["reasons"] and c3["review"]["memo"] == "ok"
    # ④ 화풍 강조색 변경 → 재검토
    pr["visual"]["elements"][1]["fixed_en"] = pr["visual"]["elements"][1]["fixed_en"].replace(" with a brass handle", "")
    pr["visual"]["style"]["accent"]["name"] = "mustard"
    assert "화풍·공통 스타일이 바뀜" in V.review_state(pr, c3)["reasons"]
    # 상태 계산: 검수 단계 '재생성 필요'
    st = storage.compute_status(pr)["steps"]["review"]
    assert st["state"] == "stale"


# ---------------------------------------------------------------- AI 재생성·잠금

def test_ai_regeneration_keeps_locks_and_manual_links(client, configure_ai):
    configure_ai()
    p = lock_all(planned(small_room()))
    base = storage.empty_project("잠금 시험")
    base.update(p)
    pr = storage.create_project("잠금 시험", base)
    pid = pr["id"]
    c2 = clip(pr, 2)
    c2.update(element_ids=["el_ex_clock"], elements_manual=True, gen_mode="still", gen_mode_manual=True)
    pr = storage.save_project(pid, pr, pr["rev"])
    visual_before = copy.deepcopy(pr["visual"])
    # 전체 재생성(모의 AI는 화풍을 바꾸려 하고, 없는 요소 id를 섞어 보냄)
    j = wait_job(client, client.post(f"/api/projects/{pid}/ai/flow", json={}).json())
    assert j["status"] == "done", j
    res = j["result"]
    assert res["style"] is None  # 확정 화풍이 있으면 AI 스타일 제안을 받지 않음
    assert all("el_does_not_exist" not in c["element_ids"] for c in res["clips"])
    out = client.post("/api/flow/apply-ai", json={"project": pr, "result": res}).json()
    pr["flow"]["clips"] = out["clips"]
    assert pr["visual"] == visual_before  # 고정 앵커·잠금 그대로
    for c in pr["flow"]["clips"]:
        assert c["prompt_source"] == "composed" and c["var_en"].startswith("[MOCK]")
        for eid in c["element_ids"]:
            el = next(e for e in pr["visual"]["elements"] if e["id"] == eid)
            assert el["fixed_en"] in c["prompt_en"]
        assert pr["visual"]["style"]["style_en"] in c["prompt_en"]
    c2 = clip(pr, 2)
    assert c2["element_ids"] == ["el_ex_clock"] and c2["gen_mode"] == "still"  # 사용자가 직접 정한 연결 유지
    assert "ai_element_ids" in c2 and c2.get("ai_gen_mode")
    pr = storage.save_project(pid, pr, pr["rev"])
    # 부분 재생성: 한 클립만 바뀌고 나머지 프롬프트·잠금 유지
    before = {c["id"]: c["prompt_en"] for c in pr["flow"]["clips"]}
    target = clip(pr, 4)["id"]
    j = wait_job(client, client.post(f"/api/projects/{pid}/ai/flow", json={"clip_ids": [target]}).json())
    out = client.post("/api/flow/apply-ai", json={"project": pr, "result": j["result"], "clip_ids": [target]}).json()
    assert out["applied"] == [target]
    for c in out["clips"]:
        if c["id"] != target:
            assert c["prompt_en"] == before[c["id"]]
    tc = next(c for c in out["clips"] if c["id"] == target)
    assert tc["prompt_prev"]["en"] == before[target]  # 되돌리기용 보관
    pr["flow"]["clips"] = out["clips"]
    assert pr["visual"] == visual_before


def test_ai_element_extraction_never_overwrites_confirmed(client, configure_ai):
    configure_ai()
    p = lock_all(planned(small_room()))
    base = storage.empty_project("요소 추출 시험")
    base.update(p)
    pr = storage.create_project("요소 추출 시험", base)
    j = wait_job(client, client.post(f"/api/projects/{pr['id']}/ai/elements", json={}).json())
    assert j["status"] == "done", j
    r = j["result"]
    assert [x["name"] for x in r["add"]] == ["Teacup"]
    assert any(x["name"] == "Window" for x in r["skipped_locked"]) and not r["update"]
    # 미확정 요소는 '갱신 제안'으로만(자동 적용 아님)
    m = V.merge_element_proposals([{"id": "e1", "name": "Window", "locked": False}], [{"name": "window", "fixed_en": "x"}])
    assert m["update"][0]["id"] == "e1" and not m["add"]


# ---------------------------------------------------------------- 클립 계획 재계산

def test_replan_keeps_links_and_inherits_on_changed_range():
    p = planned(small_room())
    c3 = clip(p, 3)
    c3.update(element_ids=["el_ex_window"], elements_manual=True, ref_asset_ids=["va_x"], gen_mode="first_frame",
              start_frame={"source": "asset", "asset_id": "va_x"}, var_en="0-3s: push-in", prompt_source="composed",
              edit={"crop": "window"}, review={"state": "pass", "basis": {}})
    # 같은 구간으로 다시 계산 → 모든 연결 유지
    r = flow.plan_clips(p, p["flow"]["clips"])
    k3 = next(c for c in r["clips"] if c["id"] == c3["id"])
    for key in ("element_ids", "ref_asset_ids", "gen_mode", "start_frame", "var_en", "edit", "review", "elements_manual"):
        assert k3[key] == c3[key], key
    # 장면 3에 문장이 하나 늘어 구간이 바뀜 → 새 클립이지만 연결은 이어받음(프롬프트는 다시)
    sc = next(s for s in p["script"]["scenes"] if s["id"] == "s_sr3")
    sc["lines"].append({"id": "l_new", "display": "雨の音も、少し聞こえます。", "tts": "あめのおとも、すこしきこえます。", "ko": "빗소리도 조금 들립니다."})
    r2 = flow.plan_clips(p, p["flow"]["clips"])
    n3 = [c for c in r2["clips"] if c["scene_id"] == "s_sr3"]
    assert all(c["id"] != c3["id"] for c in n3)
    first = n3[0]
    assert first["element_ids"] == ["el_ex_window"] and first["gen_mode"] == "first_frame"
    assert first["ref_asset_ids"] == ["va_x"] and first["inherited_from"] == c3["id"]
    assert not first.get("var_en") and not first.get("review")  # 프롬프트·검수는 새로
    # 다른 장면 클립(구간 동일)은 그대로
    assert next(c for c in r2["clips"] if c["scene_id"] == "s_sr4")["id"] == clip(p, 4)["id"]


# ---------------------------------------------------------------- 수동 편집·예전 방식 호환

def test_manual_and_legacy_prompts_are_not_overwritten():
    p = lock_all(planned(small_room()))
    c = clip(p, 1)
    c.update(var_en="0-3s: push-in", prompt_source="composed")
    c["prompt_en"] = V.compose(p, c)["prompt_en"]
    ins = flow.inspect(p)[c["id"]]
    assert ins["prompt_source"] == "composed" and not ins["out_of_date"]
    # 고정 묘사가 바뀌면 자동 조립 프롬프트는 '갱신 필요'
    p["visual"]["elements"][0]["fixed_en"] += " A small rug on the floor."
    ins = flow.inspect(p)[c["id"]]
    assert ins["out_of_date"] and "small rug" in ins["composed_en"]
    # 직접 수정한 프롬프트는 자동으로 바뀌지 않음
    c.update(prompt_source="manual", prompt_en="my hand-written prompt")
    ins = flow.inspect(p)[c["id"]]
    assert not ins["out_of_date"] and c["prompt_en"] == "my hand-written prompt"
    # 예전 방식(가변 분리 전) 프롬프트도 그대로
    legacy = clip(p, 3)
    legacy.pop("prompt_source", None)
    legacy.update(prompt_en="old full prompt from previous version", var_en="")
    ins = flow.inspect(p)[legacy["id"]]
    assert ins["prompt_source"] == "legacy" and not ins["out_of_date"]


def test_old_project_without_new_fields_opens(client):
    p = json.loads((EX / "example_project.json").read_text(encoding="utf-8"))
    p.pop("visual", None)
    r = flow.plan_clips(p)
    p["flow"] = {"clips": r["clips"], "based_on": r["based_on"]}
    for c in p["flow"]["clips"]:
        c["prompt_en"] = "legacy prompt"
    st = storage.compute_status(p)
    assert st["steps"]["visual"]["state"] == "optional" and st["steps"]["review"]["state"] == "optional"
    ins = flow.inspect(p)
    assert all(v["prompt_source"] == "legacy" and not v["out_of_date"] for v in ins.values())
    # 프롬프트에 예전 공통 스타일이 쓰임
    p["flow"]["style"] = {"look_en": "LEGACY STYLE"}
    c = p["flow"]["clips"][0]
    c["var_en"] = "x"
    assert "LEGACY STYLE" in V.compose(p, c)["prompt_en"]
    # API로 저장·열기
    base = storage.empty_project("예전 프로젝트")
    base.update(p)
    pr = storage.create_project("예전 프로젝트", base)
    got = client.get(f"/api/projects/{pr['id']}").json()
    assert got["project"]["flow"]["clips"][0]["prompt_en"] == "legacy prompt"


# ---------------------------------------------------------------- 채널 추천값

def test_channel_preset_applies_only_selected(client):
    data = client.get("/api/channel").json()
    aud = data["profile"]["audience"]
    assert "10~40대" in aud  # 기존 시청층 설정은 그대로
    client.get("/api/presets")
    assert client.get("/api/channel").json()["profile"]["audience"] == aud  # 추천값을 보기만 해서는 안 바뀜
    r = client.post("/api/channel/apply-preset", json={"keys": ["tone"], "rules": True}).json()
    assert r["applied"] == ["tone"] and r["rules_added"] >= 1
    after = client.get("/api/channel").json()["profile"]
    assert after["audience"] == aud and after["tone"].startswith("짧고 자연스러운")
    assert list(channel._file().parent.glob("channel_backup_*before-preset.json"))
    a1 = client.post("/api/channel/add-ideas").json()
    assert a1["added"] == 5
    t = next(x for x in a1["channel"]["topics"] if x["id"] == "r01")
    t["memo"] = "내 메모"
    client.put("/api/channel", json={"channel": a1["channel"]})
    a2 = client.post("/api/channel/add-ideas").json()
    assert a2["added"] == 0 and next(x for x in a2["channel"]["topics"] if x["id"] == "r01")["memo"] == "내 메모"
    # 기존 프로젝트는 채널 기본 화풍을 바꿔도 그대로, 새 프로젝트만 초안으로 받음
    old = client.post("/api/projects", json={"name": "화풍 전"}).json()["project"]
    ch = client.get("/api/channel").json()
    ch["profile"]["default_style_preset"] = "miniature"
    client.put("/api/channel", json={"channel": ch})
    assert "visual" not in client.get(f"/api/projects/{old['id']}").json()["project"]
    new = client.post("/api/projects", json={"name": "화풍 후"}).json()["project"]
    assert new["visual"]["style"]["preset_id"] == "miniature" and new["visual"]["style"]["locked"] is False


# ---------------------------------------------------------------- 소재 재사용 + 내보내기 + 다시 열기

@needs_ja
def test_small_room_reuse_export_and_reimport(client, tmp_path):
    pr = client.post("/api/examples/small-room", json={}).json()["project"]
    pid = pr["id"]
    # 음성(검증용 합성 음성)·무음 정리·자막 — 내보내기에 필요
    wav = FIX / "ja_tts_sample.wav"
    up = client.post(f"/api/projects/{pid}/audio/upload", files=[("file", (wav.name, wav.read_bytes(), "audio/wav"))]).json()
    pr["audio"]["original"] = up["original"]
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    pr["audio"]["processed"] = wait_job(client, client.post(f"/api/projects/{pid}/audio/apply",
                                        json={"mode": "natural", "params": {}, "protect_ranges": [], "custom_ranges": []}).json())["result"]["processed"]
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    al = wait_job(client, client.post(f"/api/projects/{pid}/subtitles/align", json={}).json())["result"]
    pr["subtitles"].update(cues=al["cues"], based_on_audio_version=al["audio_version"], based_on_display_hash=al["display_hash"],
                           line_texts=al["proposal"]["fresh"]["line_texts"])
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    plan = client.post(f"/api/projects/{pid}/flow/plan").json()
    pr["flow"] = {"clips": plan["clips"], "based_on": plan["based_on"], "timing": plan["timing"]}
    # 기준 이미지·확정 방 이미지 업로드
    imgs = {}
    for name, color in (("room", "beige"), ("window", "green"), ("clock", "white"), ("shelf", "brown")):
        f = tmp_path / f"{name}.png"
        make_png(f, color)
        imgs[name] = f
    els = {e["name"]: e for e in pr["visual"]["elements"]}
    for key, ename in (("room", "Small Room"), ("window", "Window"), ("clock", "Wall Clock"), ("shelf", "Bookshelf")):
        a = client.post(f"/api/projects/{pid}/visual/upload", files=[("files", (imgs[key].name, imgs[key].read_bytes(), "image/png"))],
                        data={"kind": "reference", "element_id": els[ename]["id"]}).json()["assets"][0]
        assert a["sha256"] and a["file"].startswith("media/visual/reference/")
        pr["visual"]["assets"].append(a)
        els[ename]["primary_ref_id"] = a["id"]
    lock_all(pr)
    room_asset = els["Small Room"]["primary_ref_id"]
    cl = pr["flow"]["clips"]
    by_scene = {c["scene_id"]: c for c in cl}
    choice = by_scene["s_sr2"]
    choice["still_asset_id"] = room_asset  # 선택 화면 = 확정 방 이미지(정지)
    reusers = [by_scene[s] for s in ("s_sr4", "s_sr5", "s_sr6")]
    assert all(c["gen_mode"] == "reuse" and c["reuse_of"] == choice["id"] for c in reusers)
    assert by_scene["s_sr4"]["edit"]["crop"].startswith("시계")
    # 생성 영상 2개(도입·결과 A)
    for sid in ("s_sr1", "s_sr3"):
        c = by_scene[sid]
        mp4 = tmp_path / f"{sid}.mp4"
        run_tool([ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=s=360x640:d={c['duration']}", "-pix_fmt", "yuv420p", str(mp4)])
        it = _upload_video(client, pid, c, mp4)
        pr["sources"]["items"].append(it)
        c["item_id"] = it["id"]
        c.update(var_en="0-3s: slow push-in; curtains sway slightly", prompt_source="composed")
    pr["flow"]["clips"] = cl
    for c in cl:
        if c.get("var_en"):
            c["prompt_en"] = V.compose(pr, c)["prompt_en"]
    by_scene["s_sr1"]["start_frame"] = {"source": "asset", "asset_id": room_asset}
    by_scene["s_sr1"]["prompt_en"] = V.compose(pr, by_scene["s_sr1"])["prompt_en"]
    for c in cl:
        c["review"] = {"state": "pass", "checks": {"object": "ok", "choice_match": "ok"}, "memo": "확인", "basis": V.review_basis(pr, c)}
    pr["publish"].update(title="t")
    pr = client.put(f"/api/projects/{pid}", json={"project": pr, "base_rev": pr["rev"]}).json()["project"]
    st = client.get(f"/api/projects/{pid}").json()["status"]["steps"]
    assert st["sources"]["state"] == "done", st["sources"]   # 정지·재사용 클립도 소재 있음으로 인정
    assert st["visual"]["state"] == "done" and st["review"]["state"] == "done", (st["visual"], st["review"])
    ex = wait_job(client, client.post(f"/api/projects/{pid}/export", json={"zip": True, "allow_stale": True}).json(), timeout=240)
    assert ex["status"] == "done", ex
    folder = Path(ex["result"]["folder"])
    srcs = sorted(x.name for x in (folder / "04_영상소스").iterdir())
    assert srcs.count("S02_C01_still.png") == 1 and len([s for s in srcs if s.endswith("_flow.mp4")]) == 2
    rows = list(csv.DictReader(io.StringIO((folder / "05b_Flow_클립_배치표.csv").read_text(encoding="utf-8-sig"))))
    reuse_rows = [r for r in rows if r["재사용 원본"]]
    assert len(reuse_rows) == 3 and all(r["파일"] == "S02_C01_still.png" for r in reuse_rows)
    assert all(r["검수 상태"].startswith("검수 통과") for r in rows)
    r1 = next(r for r in rows if r["클립"] == "S01-C01")
    assert r1["시작 프레임"].startswith("11_일관성_자료/") and "참조" not in r1["재검토 사유"]
    assert "Gemini Omni Flash 1.1" in r1["Flow 설정"]
    cdir = folder / "11_일관성_자료"
    edit = (cdir / "CapCut_편집지시.txt").read_text(encoding="utf-8")
    assert "시계가 화면 중앙에 오도록 크롭" in edit and "적용하지 않았습니다" in edit
    assert len(list((cdir / "기준이미지").iterdir())) == 4
    erows = list(csv.DictReader(io.StringIO((cdir / "등장요소.csv").read_text(encoding="utf-8-sig"))))
    assert {r["이름"] for r in erows} == {"Small Room", "Window", "Wall Clock", "Bookshelf"} and all(r["확정·잠금"] == "확정" for r in erows)
    assert "Small Room" in (cdir / "화풍_공통설정.txt").read_text(encoding="utf-8") or "picture-book" in (cdir / "화풍_공통설정.txt").read_text(encoding="utf-8")
    prompts_txt = (folder / "10_Flow_영상프롬프트.txt").read_text(encoding="utf-8")
    assert els["Window"]["fixed_en"] in prompts_txt and "생성 요청 입력란에서 재료로 직접 선택" in prompts_txt
    pub = (folder / "08_게시정보_제목_설명_크레딧.txt").read_text(encoding="utf-8")
    assert "娯楽として楽しむ内容です" in pub
    assert ex["result"]["verify"]["all_ok"]
    # 다른 폴더(새 프로젝트)로 다시 열어도 자료 연결 유지
    zp = Path(ex["result"]["zip"])
    with zipfile.ZipFile(zp) as z:
        assert any("11_일관성_자료/등장요소.csv" in n for n in z.namelist())
    new = client.post("/api/projects/import", files={"file": (zp.name, zp.read_bytes(), "application/zip")}).json()["project"]
    assert new["id"] != pid
    for a in new["visual"]["assets"]:
        assert storage.media_path(new["id"], a["file"]).exists()
    ins = flow.inspect(new)
    assert all(v["review"]["state"] == "pass" for v in ins.values())  # 지문이 경로가 아닌 내용 기준이라 그대로 통과
    assert all(v["media"] for v in ins.values())
