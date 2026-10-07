"""Google Flow(Gemini Omni Flash 1.1) 영상 클립 계획.

Flow에는 공식 자동화 연동을 쓰지 않는다. 프로그램은 클립 길이·배치·프롬프트를 준비하고,
사용자가 Flow 사이트에서 직접 생성한 MP4를 다시 넣는다(타입캐스트와 같은 방식).

Omni Flash 1.1 클립 길이: 4·6·8·10초 (Google Flow 도움말 기준).
장면의 실제 내레이션 길이(최종 자막 기준, 없으면 모라 수로 추정)를 문장 경계에서 나눠
각 구간을 덮는 가장 짧은 허용 길이를 고른다. 남는 꼬리는 CapCut에서 잘라 쓴다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .jp_text import mora_count
from .util import new_id, stable_hash

# Flow 모델 정보는 여기 한 곳에서만 정의한다(화면·프롬프트·내보내기가 모두 이 값을 쓴다).
MODEL_LABEL = "Gemini Omni Flash 1.1"
PRODUCT_LABEL = f"Google Flow ({MODEL_LABEL})"
ALLOWED = (4, 6, 8, 10)
ASPECT = "9:16"
SOURCE_MODES = ("flow", "stock", "upload", "image", "text", "mixed")
# 공식 도움말(Flow models & supported features)에서 확인한 이 모델의 생성 방식. 모두 4·6·8·10초, 가로·세로 지원.
SUPPORTED_MODES = ("text", "first_frame", "first_last", "ingredients")
DOC_URL = "https://support.google.com/flow/answer/16352836"
# 클립 계획이 매번 다시 계산하는 값(나머지 필드 — 프롬프트·등장 요소·참조·검수·편집 지시 — 는 유지)
COMPUTED = ("id", "key", "scene_id", "scene_no", "index_in_scene", "start", "end", "need_s", "duration", "duration_manual",
            "line_ids", "timing")
# 구간이 바뀐 클립이 이전 클립에서 이어받는 연결 정보
LINK_FIELDS = ("element_ids", "elements_manual", "role", "gen_mode", "ref_asset_ids", "start_frame", "end_frame", "changes",
               "edit", "still_asset_id", "reuse_of", "continues_previous")


def config_info() -> dict:
    return {"model_label": MODEL_LABEL, "product_label": PRODUCT_LABEL, "durations": list(ALLOWED), "aspect": ASPECT,
            "supported_modes": list(SUPPORTED_MODES), "doc_url": DOC_URL,
            "doc_note_ko": "Flow 도움말(2026-10-07 확인): Gemini Omni Flash 1.1은 텍스트·첫 프레임·첫+마지막 프레임·재료(참조 이미지)로 "
                           "영상 생성, 모두 4·6·8·10초. 참조 이미지 최대 개수는 도움말에서 확인하지 못했습니다."}


def scene_mode(project: dict, scene: dict) -> str:
    """장면의 영상 제작 방식. 장면 지정 > 프로젝트 기본값. 예전 프로젝트(설정 없음)는 Flow."""
    if scene.get("source_mode") in SOURCE_MODES and scene.get("source_mode") != "mixed":
        return scene["source_mode"]
    prod = (project.get("production") or {}).get("source_mode")
    if prod in SOURCE_MODES and prod != "mixed":
        return prod
    if prod == "mixed":
        return "unset"
    return "flow"
MORA_PER_SEC = 7.0  # 음성 없을 때 추정용(TTS 일반 속도). 실제 음성이 들어오면 실제 시간 사용
CLIP_PENALTY = 1.6  # 클립 1개 추가 비용(초 단위 낭비와 비교) — 너무 잘게 쪼개지 않게


def fit_duration(length: float) -> int | None:
    for d in ALLOWED:
        if length <= d + 0.05:
            return d
    return None


def _line_spans(project: dict) -> tuple[dict, bool]:
    """line_id → (start, end). 최종 자막이 있으면 실제 시간, 없으면 추정."""
    cues = (project.get("subtitles") or {}).get("cues") or []
    spans: dict[str, list[float]] = {}
    for c in cues:
        s = spans.setdefault(c["line_id"], [c["start"], c["end"]])
        s[0], s[1] = min(s[0], c["start"]), max(s[1], c["end"])
    if spans:
        return {k: tuple(v) for k, v in spans.items()}, True
    t = 0.0
    for sc in (project.get("script") or {}).get("scenes", []):
        for ln in sc.get("lines", []):
            d = mora_count(ln.get("tts") or ln.get("display") or "") / MORA_PER_SEC
            spans[ln["id"]] = (t, t + d)
            t += d + 0.35
        t += (sc.get("hold_ms") or 0) / 1000
    return {k: tuple(v) for k, v in spans.items()}, False


def _split_scene(start: float, end: float, bounds: list[float]) -> list[tuple[float, float]]:
    """[start,end]를 내부 경계(bounds) 중 일부에서 잘라 각 조각이 10초 이하가 되게,
    (낭비 시간 + 클립 수 벌점)이 최소가 되도록 나눈다. 경계로 안 되면 균등 분할."""
    pts = [start] + [b for b in bounds if start < b < end] + [end]
    n = len(pts)
    INF = float("inf")
    best = [INF] * n
    prev = [-1] * n
    best[0] = 0.0
    for j in range(1, n):
        for i in range(j):
            L = pts[j] - pts[i]
            d = fit_duration(L)
            if d is None or best[i] == INF:
                continue
            c = best[i] + (d - L) + CLIP_PENALTY
            if c < best[j]:
                best[j], prev[j] = c, i
    if best[-1] == INF:  # 한 문장이 10초를 넘는 등 → 그 구간을 균등하게
        segs: list[tuple[float, float]] = []
        for a, b in zip(pts, pts[1:]):
            L = b - a
            k = 1
            while L / k > 10:
                k += 1
            segs += [(a + L * q / k, a + L * (q + 1) / k) for q in range(k)]
        return segs
    out = []
    j = n - 1
    while j > 0:
        out.append((pts[prev[j]], pts[j]))
        j = prev[j]
    return list(reversed(out))


def plan_clips(project: dict, existing: list[dict] | None = None) -> dict:
    """장면별 클립 계획. 기존 클립과 같은 구간(장면+문장)이면 프롬프트·파일 연결을 유지한다."""
    spans, real = _line_spans(project)
    existing = existing or []
    by_key = {c.get("key"): c for c in existing}
    scenes = (project.get("script") or {}).get("scenes", [])
    # 장면 경계: 다음 장면 첫 문장 시작까지(마지막은 음성 끝)
    dur_total = ((project.get("audio") or {}).get("processed") or {}).get("duration") if real else None
    scene_ranges = []
    for sc in scenes:
        ls = [spans[ln["id"]] for ln in sc.get("lines", []) if ln["id"] in spans]
        scene_ranges.append((min(s for s, _ in ls), max(e for _, e in ls)) if ls else None)
    clips = []
    for si, sc in enumerate(scenes):
        rng = scene_ranges[si]
        if not rng or scene_mode(project, sc) != "flow":
            continue  # Flow를 쓰지 않는 장면에는 클립을 만들지 않는다
        s0 = rng[0] if si > 0 else 0.0
        nxt = next((r[0] for r in scene_ranges[si + 1:] if r), None)
        e0 = nxt if nxt is not None else (dur_total or rng[1] + 0.3)
        line_ids = [ln["id"] for ln in sc.get("lines", []) if ln["id"] in spans]
        bounds = [spans[l][0] for l in line_ids[1:]]
        pieces = _split_scene(s0, e0, bounds)
        olds_in_scene = [c for c in existing if c.get("scene_id") == sc["id"]]
        for k, (a, b) in enumerate(pieces):
            covered = [l for l in line_ids if spans[l][0] < b - 0.05 and spans[l][1] > a + 0.05]
            key = stable_hash([sc["id"], covered, k])
            old = by_key.get(key)
            dur = fit_duration(b - a) or 10
            if old:
                clip = {k2: v for k2, v in old.items() if k2 not in COMPUTED}  # 프롬프트·연결·검수·편집 지시 모두 유지
            else:
                clip = {"prompt_en": "", "prompt_ko": "", "beats_ko": "", "mode": "text", "mode_note_ko": "", "notes": "",
                        "prompt_for_duration": None, "item_id": None}
                # 구간이 바뀐 클립: 같은 장면에서 문장이 가장 많이 겹치는 이전 클립의 '연결'만 이어받는다(프롬프트는 다시 만들어야 함)
                best = max(olds_in_scene, key=lambda c: len(set(c.get("line_ids") or []) & set(covered)), default=None)
                if best is not None and set(best.get("line_ids") or []) & set(covered):
                    for f in LINK_FIELDS:
                        if f in best:
                            clip[f] = json.loads(json.dumps(best[f]))
                    clip["inherited_from"] = best.get("id")
                else:  # 처음 계획: 장면에 적어 둔 기본 연결(예제·사용자 지정)을 클립에 넣는다
                    for f, sf in (("element_ids", "element_ids"), ("role", "role"), ("gen_mode", "gen_mode_hint"),
                                  ("edit", "edit_hint"), ("reuse_scene_id", "reuse_scene_id")):
                        if sc.get(sf):
                            clip[f] = json.loads(json.dumps(sc[sf]))
            clip.update({
                "id": old["id"] if old else new_id("clip"), "key": key, "scene_id": sc["id"], "scene_no": si + 1,
                "index_in_scene": k + 1, "start": round(a, 3), "end": round(b, 3), "need_s": round(b - a, 2),
                "duration": old["duration"] if old and old.get("duration_manual") else dur,
                "duration_manual": bool(old and old.get("duration_manual")),
                "line_ids": covered, "timing": "실제" if real else "추정",
            })
            clips.append(clip)
    for c in clips:  # '다른 장면 소재 재사용' 기본값 → 그 장면 첫 클립을 재사용 원본으로
        if c.get("gen_mode") == "reuse" and not c.get("reuse_of") and c.get("reuse_scene_id"):
            src = next((x for x in clips if x["scene_id"] == c["reuse_scene_id"] and x is not c), None)
            if src:
                c["reuse_of"] = src["id"]
    kept = {c["id"] for c in clips}
    dropped = [c for c in existing if c.get("id") not in kept]
    return {"clips": clips, "timing": "실제" if real else "추정", "based_on": timing_hash(project),
            "dropped": [{"id": c["id"], "scene_no": c.get("scene_no"), "had_prompt": bool(c.get("prompt_en")),
                         "item_id": c.get("item_id")} for c in dropped]}


def timing_hash(project: dict) -> str:
    cues = (project.get("subtitles") or {}).get("cues") or []
    if cues:
        return stable_hash([[c["line_id"], round(c["start"], 2), round(c["end"], 2)] for c in cues])
    return stable_hash([[ln["id"], ln.get("tts", "")] for sc in (project.get("script") or {}).get("scenes", [])
                        for ln in sc.get("lines", [])])


# ---------------------------------------------------------------- 영상 정보·실제 사용 구간 프레임 추출

def probe_video(path: Path) -> dict:
    """ffmpeg 출력에서 길이·프레임률·크기를 읽는다(ffprobe 없이)."""
    from .util import ffmpeg_exe, run_tool

    r = run_tool([ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path)], timeout=60)
    err = r.stderr.decode("utf-8", "replace")
    out: dict = {"duration": None, "fps": None, "width": None, "height": None}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        out["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    vline = next((ln for ln in err.splitlines() if "Video:" in ln), "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*fps", vline) or re.search(r"(\d+(?:\.\d+)?)\s*tbr", vline)
    if m:
        out["fps"] = float(m.group(1))
    m = re.search(r",\s*(\d{2,5})x(\d{2,5})", vline)
    if m:
        out["width"], out["height"] = int(m.group(1)), int(m.group(2))
    return out


def extract_frame_at(src: Path, seek: float, dest: Path) -> None:
    """seek 이후 첫 프레임을 PNG로(입력 앞 -ss = 정확한 위치까지 디코딩)."""
    from .util import UserError, ffmpeg_exe, run_tool

    dest.parent.mkdir(parents=True, exist_ok=True)
    r = run_tool([ffmpeg_exe(), "-y", "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{seek:.4f}", "-i", str(src),
                  "-frames:v", "1", "-update", "1", str(dest)], timeout=120)
    if r.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise UserError("프레임을 뽑지 못했습니다.", r.stderr.decode("utf-8", "replace")[:200])


# ---------------------------------------------------------------- 클립 점검(화면·내보내기 공용)

def inspect(project: dict) -> dict:
    """클립마다 조립한 최종 프롬프트·충돌·주의·참조 자료·Flow 설정·검수 상태를 계산한다(저장하지 않음)."""
    from . import visual as V

    out = {}
    for c in V.clips(project):
        comp = V.compose(project, c)
        src = c.get("prompt_source") or ("legacy" if c.get("prompt_en") else "composed")
        sf, ef = V.frame_ref(project, c.get("start_frame")), V.frame_ref(project, c.get("end_frame"))
        out[c["id"]] = {
            "composed_en": comp["prompt_en"], "sections": comp["sections"], "mode": comp["mode"],
            "needs_flow": comp["needs_flow"], "prompt_source": src,
            "out_of_date": src == "composed" and bool(comp["prompt_en"]) and comp["prompt_en"] != (c.get("prompt_en") or ""),
            "conflicts": V.find_conflicts(project, c), "warnings": V.find_warnings(project, c),
            "refs": V.clip_refs(project, c),
            "start_frame": {k: v for k, v in (sf or {}).items() if k in ("source", "file", "label")} or None,
            "end_frame": {k: v for k, v in (ef or {}).items() if k in ("source", "file", "label")} or None,
            "flow_settings": V.flow_settings_ko(project, c, MODEL_LABEL, ASPECT),
            "guidance": V.guidance_ko(project, c),
            "review": V.review_state(project, c), "basis": V.review_basis(project, c),
            "frame_basis": V.frame_basis(project, c),
            "media": V.resolve_media(project, c),
            "label": f"S{c.get('scene_no')}-C{c.get('index_in_scene')}",
        }
    return out
