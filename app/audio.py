"""음성 파일 처리: 디코딩, 무음 분석, 무음 정리(단축·유지), 검증.

원칙
- 재생 속도는 절대 바꾸지 않는다. 무음 구간의 '가운데'만 잘라 내고 소리 구간 샘플은 그대로 둔다.
- 소리가 끝난 뒤(guard_after)와 시작 전(guard_before)에는 여유를 남겨 약한 어미·자음이 잘리지 않게 한다.
- 잘라낸 구간에 기준보다 큰 소리가 있었는지 사후 검증한다.
- 원본 파일은 절대 수정하지 않는다(처리본은 새 파일로 저장).
"""
from __future__ import annotations

import re
import wave
from pathlib import Path

import numpy as np

from .util import UserError, ffmpeg_exe, run_tool

HOP_S = 0.01
WIN_S = 0.03
FADE_S = 0.004

MODES = {
    "natural": {"label": "자연스럽게", "min_silence_ms": 300, "gap_ms": 420, "lead_ms": 150, "tail_ms": 350,
                "guard_before_ms": 60, "guard_after_ms": 130, "threshold_mode": "auto", "threshold_db": -55.0,
                "threshold_offset_db": 0.0},
    "fast": {"label": "빠르게", "min_silence_ms": 200, "gap_ms": 240, "lead_ms": 80, "tail_ms": 220,
             "guard_before_ms": 40, "guard_after_ms": 100, "threshold_mode": "auto", "threshold_db": -55.0,
             "threshold_offset_db": 0.0},
}
PARAM_LIMITS = {
    "min_silence_ms": (80, 3000), "gap_ms": (60, 3000), "lead_ms": (0, 2000), "tail_ms": (0, 3000),
    "guard_before_ms": (10, 500), "guard_after_ms": (30, 800), "threshold_db": (-90.0, -20.0),
    "threshold_offset_db": (-20.0, 20.0),
}


def resolve_params(mode: str, params: dict | None) -> dict:
    base = dict(MODES.get(mode if mode in MODES else "natural"))
    if mode == "custom":
        base = dict(MODES["natural"])
        base["label"] = "직접 설정"
    for k, v in (params or {}).items():
        if k in PARAM_LIMITS and v not in (None, ""):
            lo, hi = PARAM_LIMITS[k]
            try:
                base[k] = float(min(hi, max(lo, float(v))))
            except (TypeError, ValueError):
                pass
        elif k == "threshold_mode" and v in ("auto", "manual"):
            base[k] = v
    # 남길 간격은 보호 여유보다 작을 수 없다
    base["gap_ms"] = max(base["gap_ms"], base["guard_before_ms"] + base["guard_after_ms"] * 0.5)
    base["lead_ms"] = max(base["lead_ms"], base["guard_before_ms"])
    base["tail_ms"] = max(base["tail_ms"], base["guard_after_ms"])
    base["mode"] = mode
    return base


# ---------------------------------------------------------------- 입출력

def probe(path: Path) -> dict:
    r = run_tool([ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path)], timeout=60)
    err = r.stderr.decode("utf-8", "replace")
    m = re.search(r"Audio:\s*([^,\n]+),\s*(\d+)\s*Hz,\s*([^,\n]+)", err)
    if not m:
        raise UserError("음성 파일을 읽을 수 없습니다.", "타입캐스트에서 받은 WAV 또는 MP3 파일인지 확인하세요.")
    codec, sr, layout = re.sub(r"\s*\(.*", "", m.group(1)).strip(), int(m.group(2)), m.group(3).strip()
    if layout.startswith("mono") or layout.startswith("1 channel"):
        ch = 1
    elif layout.startswith("stereo") or layout.startswith("2 channels"):
        ch = 2
    else:
        ch = 1
    d = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else None
    return {"codec": codec, "sr": sr, "channels": ch, "duration": dur}


def decode(path: Path, sr: int, channels: int, cancel_check=None) -> np.ndarray:
    """int16 배열 (samples, channels)로 디코딩. 경로는 인자 리스트로만 전달."""
    r = run_tool([ffmpeg_exe(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
                  "-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(sr), "-ac", str(channels), "-"],
                 timeout=600, cancel_check=cancel_check)
    if r.returncode != 0 or not r.stdout:
        raise UserError("음성 파일을 해독하지 못했습니다.", "파일이 손상되지 않았는지, 지원 형식(WAV/MP3/M4A)인지 확인하세요.")
    a = np.frombuffer(r.stdout, dtype="<i2")
    a = a[: len(a) // channels * channels].reshape(-1, channels)
    return a


def write_wav(path: Path, data: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.ascontiguousarray(data.astype("<i2"))
    tmp = path.with_name(path.name + ".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(data.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())
    tmp.replace(path)


def verify_playable(path: Path) -> tuple[bool, str]:
    """ffmpeg로 끝까지 디코딩해 재생 가능 여부 확인."""
    r = run_tool([ffmpeg_exe(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-f", "null", "-"],
                 timeout=300)
    err = r.stderr.decode("utf-8", "replace").strip()
    return (r.returncode == 0 and not err), err[:300]


# ---------------------------------------------------------------- 분석

def mono_float(data: np.ndarray) -> np.ndarray:
    return data.astype(np.float32).mean(axis=1) / 32768.0


def frame_db(x: np.ndarray, sr: int) -> np.ndarray:
    hop = max(1, int(sr * HOP_S))
    win = max(hop, int(sr * WIN_S))
    n = max(1, int(np.ceil(len(x) / hop)))
    pad = np.concatenate([np.zeros(win // 2, np.float32), x, np.zeros(win, np.float32)])
    sq = np.concatenate([[0.0], np.cumsum(pad.astype(np.float64) ** 2)])
    starts = np.arange(n) * hop
    energy = (sq[starts + win] - sq[starts]) / win
    return 10 * np.log10(np.maximum(energy, 1e-10))


def levels(db: np.ndarray) -> dict:
    floor = float(np.percentile(db, 10))
    speech = float(np.percentile(db, 95))
    return {"floor_db": round(max(floor, -100.0), 1), "speech_db": round(speech, 1)}


def threshold_for(db: np.ndarray, params: dict) -> float:
    if params.get("threshold_mode") == "manual":
        return float(params["threshold_db"])
    lv = levels(db)
    thr = max(lv["floor_db"] + 10.0, lv["speech_db"] - 42.0)
    thr = min(-30.0, max(-72.0, thr)) + float(params.get("threshold_offset_db") or 0.0)
    return round(thr, 1)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """True 구간들의 [시작, 끝) 프레임 목록."""
    if not len(mask):
        return []
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    starts = np.where(d == 1)[0]
    ends = np.where(d == -1)[0]
    return list(zip(starts.tolist(), ends.tolist()))


def speech_islands(db: np.ndarray, thr: float, merge_gap_s: float = 0.06) -> list[tuple[float, float]]:
    isl = [(s * HOP_S, e * HOP_S) for s, e in _runs(db >= thr)]
    out: list[list[float]] = []
    for s, e in isl:
        if out and s - out[-1][1] < merge_gap_s:
            out[-1][1] = e
        else:
            out.append([s, e])
    return [(round(s, 3), round(e, 3)) for s, e in out]


def _overlaps(a0, a1, b0, b1) -> bool:
    return a0 < b1 and b0 < a1


def analyze(data: np.ndarray, sr: int, params: dict, protect_ranges: list | None = None,
            custom_ranges: list | None = None) -> dict:
    """무음 구간을 찾고 각 구간을 어떻게 처리할지(제거·단축·유지) 계획한다. 파일은 바꾸지 않는다."""
    x = mono_float(data)
    total = len(x) / sr
    db = frame_db(x, sr)
    thr = threshold_for(db, params)
    lv = levels(db)
    min_sil = params["min_silence_ms"] / 1000
    g_before = params["guard_before_ms"] / 1000
    g_after = params["guard_after_ms"] / 1000
    gap_target = params["gap_ms"] / 1000
    protect_ranges = protect_ranges or []
    custom_ranges = custom_ranges or []

    quiet_runs = [(s * HOP_S, min(total, e * HOP_S)) for s, e in _runs(db < thr)]
    regions = []
    islands = speech_islands(db, thr)
    if not islands:
        raise UserError("음성에서 소리를 찾지 못했습니다.", "무음 판단 기준(dB)을 낮추거나 파일을 확인하세요.")
    first_speech, last_speech = islands[0][0], islands[-1][1]
    for s, e in quiet_runs:
        length = e - s
        kind = "lead" if s <= 0.0001 else ("tail" if e >= total - 0.011 else "gap")
        if kind == "gap" and length < min_sil:
            continue
        if kind != "gap" and length < 0.02:
            continue
        r = {"start": round(s, 3), "end": round(e, 3), "length": round(length, 3), "kind": kind,
             "action": "untouched", "remove": None, "new_length": round(length, 3), "flags": [],
             "protected": False, "custom_keep_ms": None}
        # 품질이 불확실한 구간 표시
        i0, i1 = int(s / HOP_S), int(e / HOP_S)
        inner = db[i0 + 6:i1 - 6]  # 경계 부근(창 겹침) 제외
        if len(inner) and float(inner.max()) > thr - 4:
            r["flags"].append("무음 구간 안에 기준에 가까운 작은 소리가 있습니다(숨소리·잔향 가능).")
        if kind != "lead":
            pre = db[max(0, i0 - 8):i0]
            if len(pre) and float(np.mean(pre)) < thr + 8:
                r["flags"].append("바로 앞 소리가 약하게 끝납니다. 어미가 잘리지 않았는지 들어 보세요.")
        if kind != "tail":
            post = db[i1:i1 + 5]
            if len(post) and float(np.mean(post)) < thr + 8:
                r["flags"].append("바로 뒤 소리가 약하게 시작합니다. 첫 음이 잘리지 않았는지 들어 보세요.")
        # 보호 구간
        if any(_overlaps(s, e, float(a), float(b)) for a, b, *_ in protect_ranges):
            r["protected"] = True
            r["action"] = "keep"
            regions.append(r)
            continue
        target = gap_target
        for cr in custom_ranges:
            a, b, keep_ms = float(cr[0]), float(cr[1]), cr[2]
            if s <= (a + b) / 2 <= e or _overlaps(s, e, a, b) and (min(e, b) - max(s, a)) > 0.5 * length:
                target = max(g_before + 0.03, float(keep_ms) / 1000)
                r["custom_keep_ms"] = int(keep_ms)
                r["custom_label"] = str(cr[3]) if len(cr) > 3 else "직접 지정"
                if float(keep_ms) / 1000 > length + 0.05:
                    r["flags"].append(f"남기려는 길이({int(keep_ms)}ms)보다 실제 쉼({int(length * 1000)}ms)이 짧습니다. "
                                      "무음 정리는 쉼을 늘리지 않으므로, CapCut에서 멈춤을 늘리거나 타입캐스트에서 쉼을 넣으세요.")
                break
        if kind == "lead":
            keep = max(params["lead_ms"] / 1000, g_before)
            if length - keep > 0.02:
                r["remove"] = [0.0, round(e - keep, 4)]
        elif kind == "tail":
            keep = max(params["tail_ms"] / 1000, g_after)
            if length - keep > 0.02:
                r["remove"] = [round(s + keep, 4), round(total, 4)]
        else:
            if length > target + 0.03:
                keep_after = max(g_after, target * 0.55)
                keep_before = max(g_before, target - keep_after)
                a, b = s + keep_after, e - keep_before
                if b - a > 0.02:
                    r["remove"] = [round(a, 4), round(b, 4)]
        if r["remove"]:
            removed = r["remove"][1] - r["remove"][0]
            r["action"] = "trim" if kind in ("lead", "tail") else "shorten"
            r["new_length"] = round(length - removed, 3)
        regions.append(r)
    for i, r in enumerate(regions):
        r["id"] = i
    removed_total = sum(r["remove"][1] - r["remove"][0] for r in regions if r["remove"])
    return {"threshold_db": thr, "levels": lv, "duration": round(total, 3), "regions": regions,
            "removed_s": round(removed_total, 3), "result_duration": round(total - removed_total, 3),
            "speech_span": [first_speech, last_speech], "islands": len(islands),
            "bgm_suspect": lv["floor_db"] > -50,
            "params": params}


def apply_plan(data: np.ndarray, sr: int, regions: list[dict]) -> tuple[np.ndarray, list[list[float]]]:
    """계획대로 무음 일부를 잘라낸다. 반환: (처리된 샘플, 시간 대응표 [[원본시작, 원본끝, 새시작], ...])."""
    removes = sorted((r["remove"] for r in regions if r.get("remove")), key=lambda t: t[0])
    n = len(data)
    keep_iv: list[tuple[int, int]] = []
    pos = 0
    for a, b in removes:
        ia, ib = int(round(a * sr)), int(round(b * sr))
        ia, ib = max(pos, ia), min(n, ib)
        if ib <= ia:
            continue
        if ia > pos:
            keep_iv.append((pos, ia))
        pos = ib
    if pos < n:
        keep_iv.append((pos, n))
    fade = max(1, int(FADE_S * sr))
    pieces, segments, cur = [], [], 0
    for idx, (a, b) in enumerate(keep_iv):
        seg = data[a:b].astype(np.float32)
        # 잘린 경계(무음 안쪽)에만 아주 짧은 페이드를 걸어 '틱' 소리를 막는다
        if idx > 0 and len(seg) > fade:
            seg[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)[:, None]
        if idx < len(keep_iv) - 1 and len(seg) > fade:
            seg[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
        pieces.append(seg)
        segments.append([round(a / sr, 5), round(b / sr, 5), round(cur / sr, 5)])
        cur += b - a
    out = np.concatenate(pieces) if pieces else data[:0].astype(np.float32)
    return np.clip(np.round(out), -32768, 32767).astype("<i2"), segments


def verify_cut(data: np.ndarray, sr: int, regions: list[dict], thr: float,
               processed: np.ndarray, segments: list[list[float]]) -> dict:
    """음량 기준 점검(발음이 절대 잘리지 않았다는 보장은 아님):
    1) 잘라낸 구간 안의 최대 레벨이 무음 기준보다 낮은가
    2) 잘라낸 경계와 가장 가까운 소리 사이에 보호 여유가 있었는가
    3) 남긴 소리 구간의 샘플이 원본과 같은가(페이드 구간 제외)
    """
    x = mono_float(data)
    db = frame_db(x, sr)
    speech = db >= thr
    worst_db = -120.0
    min_dist = None
    for r in regions:
        if not r.get("remove"):
            continue
        a, b = r["remove"]
        seg = x[int(a * sr):int(b * sr)]
        if len(seg):
            # 10ms 창 RMS 최대
            w = max(1, int(sr * 0.01))
            m = len(seg) // w * w
            if m:
                rms = np.sqrt((seg[:m].reshape(-1, w) ** 2).mean(axis=1))
                worst_db = max(worst_db, float(20 * np.log10(max(rms.max(), 1e-6))))
        ia, ib = int(a / HOP_S), int(b / HOP_S)
        left = np.where(speech[:max(0, ia)])[0]
        right = np.where(speech[ib:])[0]
        d_left = (ia - left[-1]) * HOP_S if len(left) else None
        d_right = right[0] * HOP_S if len(right) else None
        for d in (d_left, d_right):
            if d is not None:
                min_dist = d if min_dist is None else min(min_dist, d)
    fade = max(1, int(FADE_S * sr))
    identical = True
    for os_, oe, ns in segments:
        a, b = int(round(os_ * sr)) + fade, int(round(oe * sr)) - fade
        na = int(round(ns * sr)) + fade
        if b > a:
            if not np.array_equal(data[a:b], processed[na:na + (b - a)]):
                identical = False
                break
    ok = worst_db < thr and identical
    return {"ok": bool(ok), "removed_max_db": round(worst_db, 1), "threshold_db": thr,
            "min_distance_to_speech_ms": None if min_dist is None else int(round(min_dist * 1000)),
            "speech_samples_identical": identical,
            "summary": (f"잘라낸 구간은 모두 무음 기준({thr} dB)보다 조용했고, 남긴 구간 샘플은 원본과 같습니다. "
                        "아주 약한 소리(속삭임·숨·약한 어미)는 기준만으로 판단하지 못할 수 있으니 '확인 권장' 구간은 직접 들어 보세요."
                        if ok else
                        "잘라낸 구간에 무음 기준보다 큰 소리가 있었거나, 남긴 구간이 원본과 다릅니다. 해당 구간을 들어 확인하세요.")}


def map_time(segments: list[list[float]], t: float) -> float:
    """원본 시간 → 처리본 시간."""
    for os_, oe, ns in segments:
        if t < os_:
            return ns
        if t <= oe:
            return ns + (t - os_)
    if segments:
        os_, oe, ns = segments[-1]
        return ns + (oe - os_)
    return t


def peaks(data: np.ndarray, buckets: int = 1600) -> list[list[float]]:
    x = mono_float(data)
    if not len(x):
        return []
    buckets = max(10, min(buckets, len(x)))
    m = len(x) // buckets * buckets
    v = x[:m].reshape(buckets, -1)
    return np.round(np.stack([v.min(axis=1), v.max(axis=1)], axis=1), 3).tolist()
