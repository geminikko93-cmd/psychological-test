"""Google Flow(Gemini Omni Flash 1.1) 영상 클립 계획.

Flow에는 공식 자동화 연동을 쓰지 않는다. 프로그램은 클립 길이·배치·프롬프트를 준비하고,
사용자가 Flow 사이트에서 직접 생성한 MP4를 다시 넣는다(타입캐스트와 같은 방식).

Omni Flash 1.1 클립 길이: 4·6·8·10초 (Google Flow 도움말 기준).
장면의 실제 내레이션 길이(최종 자막 기준, 없으면 모라 수로 추정)를 문장 경계에서 나눠
각 구간을 덮는 가장 짧은 허용 길이를 고른다. 남는 꼬리는 CapCut에서 잘라 쓴다.
"""
from __future__ import annotations

from .jp_text import mora_count
from .util import new_id, stable_hash

# Flow 모델 정보는 여기 한 곳에서만 정의한다(화면·프롬프트·내보내기가 모두 이 값을 쓴다).
MODEL_LABEL = "Gemini Omni Flash 1.1"
PRODUCT_LABEL = f"Google Flow ({MODEL_LABEL})"
ALLOWED = (4, 6, 8, 10)
ASPECT = "9:16"
SOURCE_MODES = ("flow", "stock", "upload", "image", "text", "mixed")


def config_info() -> dict:
    return {"model_label": MODEL_LABEL, "product_label": PRODUCT_LABEL, "durations": list(ALLOWED), "aspect": ASPECT}


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
        for k, (a, b) in enumerate(pieces):
            covered = [l for l in line_ids if spans[l][0] < b - 0.05 and spans[l][1] > a + 0.05]
            key = stable_hash([sc["id"], covered, k])
            old = by_key.get(key)
            dur = fit_duration(b - a) or 10
            clip = {
                "id": old["id"] if old else new_id("clip"), "key": key, "scene_id": sc["id"], "scene_no": si + 1,
                "index_in_scene": k + 1, "start": round(a, 3), "end": round(b, 3), "need_s": round(b - a, 2),
                "duration": old["duration"] if old and old.get("duration_manual") else dur,
                "duration_manual": bool(old and old.get("duration_manual")),
                "line_ids": covered, "timing": "실제" if real else "추정",
                "prompt_en": old.get("prompt_en", "") if old else "", "prompt_ko": old.get("prompt_ko", "") if old else "",
                "beats_ko": old.get("beats_ko", "") if old else "", "mode": old.get("mode", "text") if old else "text",
                "mode_note_ko": old.get("mode_note_ko", "") if old else "", "notes": old.get("notes", "") if old else "",
                "prompt_for_duration": old.get("prompt_for_duration") if old else None,
                "item_id": old.get("item_id") if old else None,
            }
            clips.append(clip)
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
