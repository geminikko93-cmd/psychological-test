"""자막 시간 맞추기 — 최종(무음 정리된) 음성 기준.

방법 1) 무음 경계 정렬(기본, 추가 설치 없음)
  - 실제 음성에서 소리 덩어리(섬)와 그 사이 쉼을 찾는다.
  - 대본 각 문장의 예상 길이(모라 수)와 실제 쉼 위치를 동적 계획법으로 맞춰
    문장 경계를 '실제 쉼'에 놓는다. 글자 수 비례로 시간을 나누는 방식이 아니다.
  - 쉼이 없어 경계를 찾지 못하면 소리 덩어리 내부의 가장 작은 지점에서 나누고 '낮은 신뢰도'로 표시한다.
  - 한 문장을 자막 여러 장으로 나눌 때는 문장 내부 쉼에 맞추고, 쉼이 없으면 '추정'으로 표시한다.

방법 2) 음성 인식 보조 정렬(선택, faster-whisper 설치 필요, 모두 로컬 처리)
  - 음성 인식 결과의 글자별 시간을 대본 읽기(히라가나)와 맞대어 문장 시작·끝을 찾는다.
  - 인식된 문장으로 자막을 바꾸지 않는다. 자막 문구는 항상 사용자가 확정한 화면용 일본어다.
"""
from __future__ import annotations

import difflib
import math

import numpy as np

from . import audio as A
from . import config
from .jp_text import mora_count, split_for_cues, to_hira
from .util import UserError, new_id


# ---------------------------------------------------------------- 소리 덩어리

def get_islands(data: np.ndarray, sr: int, thr: float) -> tuple[list[list[float]], np.ndarray]:
    x = A.mono_float(data)
    db = A.frame_db(x, sr)
    isl = [list(t) for t in A.speech_islands(db, thr, merge_gap_s=0.06)]
    return isl, db


def _split_island(isl: list[float], db: np.ndarray) -> tuple[list[float], list[float]] | None:
    s, e = isl
    i0, i1 = int(s / A.HOP_S), int(e / A.HOP_S)
    n = i1 - i0
    if n < 12:
        return None
    lo, hi = i0 + n // 4, i1 - n // 4
    k = lo + int(np.argmin(db[lo:hi]))
    t = k * A.HOP_S
    return [s, t], [t, e]


# ---------------------------------------------------------------- 문장 경계 DP

def _dp(islands: list[list[float]], weights: list[float], virtual: set[int]) -> list[tuple[int, int]]:
    m, n = len(islands), len(weights)
    gaps = [islands[k + 1][0] - islands[k][1] for k in range(m - 1)]
    span = islands[-1][1] - islands[0][0]
    largest = sorted(gaps, reverse=True)[: n - 1]
    rate = max(0.03, (span - sum(largest)) / max(1e-6, sum(weights)))

    def gap_cost(k: int) -> float:  # 경계를 gap k(섬 k와 k+1 사이)에 둘 때
        g = gaps[k]
        c = 1.2 * (1 - min(g, 0.45) / 0.45) ** 2
        if k in virtual:
            c += 1.5
        return c

    INF = float("inf")
    dp = [[INF] * m for _ in range(n)]
    back = [[-1] * m for _ in range(n)]
    for b in range(m):
        dur = islands[b][1] - islands[0][0]
        dp[0][b] = math.log(max(dur, 0.05) / (weights[0] * rate)) ** 2
    for i in range(1, n):
        exp_i = weights[i] * rate
        for b in range(i, m - (n - 1 - i)):
            best, arg = INF, -1
            for a in range(i, b + 1):
                prev = dp[i - 1][a - 1]
                if prev == INF:
                    continue
                dur = islands[b][1] - islands[a][0]
                c = prev + math.log(max(dur, 0.05) / exp_i) ** 2 + gap_cost(a - 1)
                if c < best:
                    best, arg = c, a
            dp[i][b], back[i][b] = best, arg
    # 마지막 줄은 마지막 섬에서 끝나야 한다
    spans = []
    b = m - 1
    for i in range(n - 1, -1, -1):
        a = back[i][b] if i > 0 else 0
        spans.append((a, b))
        b = a - 1
    spans.reverse()
    return spans


def align_by_pauses(islands: list[list[float]], db: np.ndarray, weights: list[float]) -> list[dict]:
    """문장별 [시작, 끝, 신뢰도] 계산."""
    if not islands:
        raise UserError("음성에서 소리를 찾지 못했습니다.", "음성 단계에서 무음 기준을 확인하세요.")
    islands = [list(x) for x in islands]
    virtual: set[int] = set()
    n = len(weights)
    # 섬이 문장 수보다 적으면 가장 긴 섬을 내부 최소 에너지 지점에서 나눈다
    guard = 0
    while len(islands) < n and guard < 500:
        guard += 1
        idx = max(range(len(islands)), key=lambda k: islands[k][1] - islands[k][0])
        sp = _split_island(islands[idx], db)
        if not sp:
            break
        islands[idx:idx + 1] = [sp[0], sp[1]]
        virtual = {v + 1 if v >= idx else v for v in virtual}
        virtual.add(idx)
    if len(islands) < n:
        raise UserError("음성 길이에 비해 대본 문장이 너무 많습니다.",
                        "대본과 음성이 같은 내용인지 확인하세요. 음성을 다시 넣었다면 대본도 맞춰야 합니다.")
    spans = _dp(islands, weights, virtual)
    total_w = sum(weights)
    span_all = islands[-1][1] - islands[0][0]
    out = []
    for i, (a, b) in enumerate(spans):
        start, end = islands[a][0], islands[b][1]
        left_gap = (islands[a][0] - islands[a - 1][1]) if a > 0 else 1.0
        right_gap = (islands[b + 1][0] - islands[b][1]) if b < len(islands) - 1 else 1.0
        is_virtual = (a - 1 in virtual) or (b in virtual)
        exp = weights[i] / total_w * span_all
        ratio = abs(math.log(max(end - start, 0.05) / max(exp, 0.05)))
        g = min(left_gap, right_gap)
        if is_virtual:
            conf = "low"
        elif g >= 0.18 and ratio < 0.45:
            conf = "high"
        elif g >= 0.08 and ratio < 0.8:
            conf = "mid"
        else:
            conf = "low"
        inner = [[islands[k][1], islands[k + 1][0]] for k in range(a, b)]
        out.append({"start": round(start, 3), "end": round(end, 3), "conf": conf, "inner_gaps": inner,
                    "virtual": is_virtual})
    return out


# ---------------------------------------------------------------- 음성 인식 보조

def whisper_status() -> dict:
    try:
        import faster_whisper  # noqa: F401
        installed = True
    except Exception:
        installed = False
    size = config.load_settings()["whisper"]["model_size"]
    model_dir = config.MODELS_DIR
    downloaded = any(p.is_dir() and size in p.name for p in model_dir.glob("models--*")) if model_dir.exists() else False
    return {"installed": installed, "model_size": size, "model_downloaded": downloaded,
            "model_dir": str(model_dir),
            "sizes": {"base": "약 150MB", "small": "약 480MB", "medium": "약 1.5GB"}}


def whisper_char_times(wav_path, job=None, initial_prompt: str = "") -> list[tuple[str, float, float]]:
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        raise UserError("음성 인식 보조 정렬 모듈(faster-whisper)이 설치되어 있지 않습니다.",
                        "install_whisper.bat 을 실행하거나 '무음 경계 정렬'을 사용하세요.") from e
    size = config.load_settings()["whisper"]["model_size"]
    if job:
        job.report(0.05, f"음성 인식 모델({size}) 불러오는 중… 처음 한 번은 모델을 내려받습니다.")
    try:
        model = WhisperModel(size, device="cpu", compute_type="int8", download_root=str(config.MODELS_DIR))
    except Exception as e:
        raise UserError("음성 인식 모델을 불러오지 못했습니다.",
                        "처음 사용할 때는 인터넷으로 모델을 내려받아야 합니다(Hugging Face). 연결을 확인하세요.") from e
    if job:
        job.check()
        job.report(0.15, "음성 인식 중(내 PC에서 처리, 음성은 외부로 보내지 않음)…")
    # PyAV 버전 차이를 피하려고 우리 ffmpeg로 16kHz 모노로 직접 디코딩해 넘긴다
    pcm = A.decode(wav_path, 16000, 1, cancel_check=job.cancelled if job else None)
    samples = pcm[:, 0].astype(np.float32) / 32768.0
    segments, info = model.transcribe(samples, language="ja", word_timestamps=True, beam_size=5,
                                      vad_filter=False, initial_prompt=initial_prompt[:200] or None,
                                      condition_on_previous_text=False)
    chars: list[tuple[str, float, float]] = []
    dur = info.duration or 1
    for seg in segments:
        if job:
            job.check()
            job.report(0.15 + 0.75 * min(1, seg.end / dur), f"음성 인식 중… {seg.end:.1f}/{dur:.1f}초")
        for w in seg.words or []:
            hira = "".join(c for c in to_hira(w.word) if _is_kana(c))
            if not hira:
                continue
            step = (w.end - w.start) / len(hira)
            for j, c in enumerate(hira):
                chars.append((c, w.start + j * step, w.start + (j + 1) * step))
    return chars


def _is_kana(c: str) -> bool:
    return "ぁ" <= c <= "ゟ" or c == "ー"


def align_by_asr(asr_chars, lines_tts: list[str]) -> list[dict | None]:
    script = []
    for i, t in enumerate(lines_tts):
        for c in to_hira(t):
            if _is_kana(c):
                script.append((c, i))
    a = [c for c, _ in script]
    b = [c for c, _, _ in asr_chars]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    times: dict[int, list[tuple[float, float]]] = {}
    counts = [0] * len(lines_tts)
    for _, li in script:
        counts[li] += 1
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            li = script[blk.a + k][1]
            _, s, e = asr_chars[blk.b + k]
            times.setdefault(li, []).append((s, e))
    out: list[dict | None] = []
    for i in range(len(lines_tts)):
        tl = times.get(i, [])
        cov = len(tl) / max(1, counts[i])
        if cov < 0.3 or not tl:
            out.append(None)
            continue
        out.append({"start": min(s for s, _ in tl), "end": max(e for _, e in tl), "coverage": round(cov, 2)})
    return out


def _snap(t: float, edges: list[float], limit: float) -> tuple[float, bool]:
    if not edges:
        return t, False
    best = min(edges, key=lambda x: abs(x - t))
    return (best, True) if abs(best - t) <= limit else (t, False)


def _island_bounds(islands, t0: float, t1: float, limit: float = 0.5) -> tuple[float, float]:
    """인식 결과의 시작·끝을 실제 소리 덩어리 경계로 맞춘다(인식 단어 끝은 보통 조금 이르다)."""
    s = t0
    for a, b in islands:
        if b > t0:
            if abs(a - t0) <= limit:
                s = a
            break
    e = t1
    for a, b in reversed(islands):
        if a < t1 - 0.02:
            if abs(b - t1) <= limit:
                e = b
            break
    return s, max(e, s + 0.2)


# ---------------------------------------------------------------- 자막 카드 만들기

def build_cues(lines: list[dict], line_times: list[dict], duration: float, sub_settings: dict,
               method_label: str) -> list[dict]:
    max_chars = int(sub_settings.get("max_line_chars", 14))
    max_lines = int(sub_settings.get("max_lines", 2))
    fill_under = float(sub_settings.get("fill_gaps_under_s", 0.6))
    raw = []
    for ln, lt in zip(lines, line_times):
        parts = split_for_cues(ln.get("display", ""), max_chars, max_lines)
        if not parts:
            continue
        start, end = lt["start"], lt["end"]
        if len(parts) == 1:
            raw.append({"line_id": ln["id"], "part": 0, "text": parts[0], "s": start, "e": end,
                        "conf": lt["conf"], "note": lt.get("note", "")})
            continue
        w = [mora_count(p.replace("\n", "")) for p in parts]
        tot = sum(w)
        inner_edges = [(g[0] + g[1]) / 2 for g in lt.get("inner_gaps", [])]
        cuts, acc = [], 0.0
        for k in range(len(parts) - 1):
            acc += w[k]
            guess = start + (end - start) * acc / tot
            t, snapped = _snap(guess, inner_edges, 0.35)
            if cuts and t <= cuts[-1][0] + 0.2:
                t, snapped = guess, False
            cuts.append((t, snapped))
        bounds = [(start, True)] + cuts + [(end, True)]
        for k, p in enumerate(parts):
            s, e = bounds[k][0], bounds[k + 1][0]
            snapped = bounds[k][1] and bounds[k + 1][1]
            conf = lt["conf"] if snapped else "low"
            note = "" if snapped else "문장 안에서 나눈 위치는 쉼이 없어 길이 비율로 추정했습니다. 들어 보고 조정하세요."
            raw.append({"line_id": ln["id"], "part": k, "text": p, "s": s, "e": e, "conf": conf, "note": note})
    # 화면 표시 시간: 말 시작 직전부터, 짧은 쉼은 다음 자막까지 이어 표시
    cues = []
    for i, r in enumerate(raw):
        s = max(0.0, r["s"] - 0.05)
        if cues:
            s = max(s, cues[-1]["end"])
        nxt = raw[i + 1]["s"] - 0.05 if i + 1 < len(raw) else None
        if nxt is not None and nxt - r["e"] <= fill_under:
            e = nxt
        else:
            e = r["e"] + 0.25
            if nxt is not None:
                e = min(e, nxt)
        e = min(e, duration)
        if e - s < 0.2:
            e = min(duration, s + 0.2)
        cues.append({"id": new_id("c"), "line_id": r["line_id"], "part": r["part"], "text": r["text"],
                     "start": round(s, 3), "end": round(e, 3), "speech_start": round(r["s"], 3),
                     "speech_end": round(r["e"], 3), "conf": r["conf"], "method": method_label,
                     "note": r["note"], "manual_text": False, "manual_time": False})
    return cues


def align_project(project: dict, data: np.ndarray, sr: int, thr: float, wav_path=None, use_asr: bool = False,
                  job=None) -> dict:
    lines = [ln for sc in (project.get("script") or {}).get("scenes", []) for ln in sc.get("lines", [])
             if (ln.get("tts") or "").strip() and (ln.get("display") or "").strip()]
    if not lines:
        raise UserError("자막을 만들 대본이 없습니다.", "대본 단계에서 화면 표시·TTS 일본어를 채우세요.")
    duration = len(data) / sr
    if job:
        job.report(0.05, "음성의 소리 구간과 쉼을 분석하는 중…")
    islands, db = get_islands(data, sr, thr)
    weights = [float(mora_count(ln["tts"])) for ln in lines]
    pause = align_by_pauses(islands, db, weights)
    method = "무음 경계 정렬"
    if use_asr and wav_path is not None:
        chars = whisper_char_times(wav_path, job, initial_prompt="".join(ln["tts"] for ln in lines))
        asr = align_by_asr(chars, [ln["tts"] for ln in lines])
        merged = []
        for p, a in zip(pause, asr):
            if a is None:
                merged.append(dict(p, conf="low" if p["conf"] == "low" else "mid",
                                   note="음성 인식으로 이 문장을 찾지 못해 무음 경계 결과를 썼습니다."))
                continue
            s, e = _island_bounds(islands, a["start"], a["end"])
            conf = "high" if a["coverage"] >= 0.7 else ("mid" if a["coverage"] >= 0.45 else "low")
            inner = [g for g in p.get("inner_gaps", []) if s < g[0] < e]
            inner += [[islands[k][1], islands[k + 1][0]] for k in range(len(islands) - 1)
                      if s < islands[k][1] < e and [islands[k][1], islands[k + 1][0]] not in inner]
            merged.append({"start": round(s, 3), "end": round(max(e, s + 0.2), 3), "conf": conf, "inner_gaps": inner,
                           "note": f"인식 일치율 {int(a['coverage'] * 100)}%"})
        # 순서가 뒤집히거나 겹치면 무음 경계 결과로 되돌린다
        for i in range(1, len(merged)):
            if merged[i]["start"] < merged[i - 1]["end"] - 0.05:
                merged[i] = dict(pause[i], conf="low", note="음성 인식 결과가 앞 문장과 겹쳐 무음 경계 결과를 썼습니다.")
        pause = merged
        method = "음성 인식 보조 정렬"
    if job:
        job.report(0.95, "자막 카드를 만드는 중…")
    cues = build_cues(lines, pause, duration, config.load_settings()["subtitle"], method)
    return {"cues": cues, "method": method, "duration": round(duration, 3),
            "line_times": [{"line_id": ln["id"], **{k: v for k, v in t.items() if k != "inner_gaps"}}
                           for ln, t in zip(lines, pause)]}
