"""SRT 생성·검사."""
from __future__ import annotations

import re


def fmt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_time(s: str) -> float:
    m = re.match(r"^(\d+):(\d{2}):(\d{2})[,.](\d{3})$", s.strip())
    if not m:
        raise ValueError(f"시간 형식 오류: {s}")
    h, mi, se, ms = map(int, m.groups())
    return h * 3600 + mi * 60 + se + ms / 1000


def build_srt(cues: list[dict]) -> str:
    out = []
    for i, c in enumerate(sorted(cues, key=lambda c: c["start"]), 1):
        text = "\n".join(x for x in (c.get("text") or "").replace("\r", "").split("\n") if x.strip())
        out.append(f"{i}\n{fmt_time(c['start'])} --> {fmt_time(c['end'])}\n{text}\n")
    return "\r\n".join(block.replace("\n", "\r\n") for block in out)


def parse_srt(text: str) -> list[dict]:
    text = text.lstrip("﻿").replace("\r\n", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        if len(lines) < 3:
            raise ValueError(f"블록 형식 오류: {block[:40]}")
        a, b = lines[1].split(" --> ")
        cues.append({"index": int(lines[0]), "start": parse_time(a), "end": parse_time(b), "text": "\n".join(lines[2:])})
    return cues


def check_cues(cues: list[dict], audio_duration: float | None, settings: dict) -> list[dict]:
    """자막 점검: 중첩, 음성 길이 초과, 시작≥종료, 너무 짧음/빠름, 줄 수·길이 초과."""
    max_chars = int(settings.get("max_line_chars", 14))
    max_lines = int(settings.get("max_lines", 2))
    min_s = float(settings.get("min_cue_s", 0.8))
    max_cps = float(settings.get("max_cps", 9.0))
    issues = []
    cs = sorted(cues, key=lambda c: c["start"])
    for i, c in enumerate(cs):
        cid = c.get("id")
        s, e = float(c["start"]), float(c["end"])
        text = c.get("text") or ""
        flat = text.replace("\n", "")
        if not flat.strip():
            issues.append({"cue_id": cid, "level": "error", "message": "자막 문구가 비어 있습니다."})
        if e <= s:
            issues.append({"cue_id": cid, "level": "error", "message": "종료 시간이 시작 시간보다 빠르거나 같습니다."})
        if audio_duration is not None and e > audio_duration + 0.01:
            issues.append({"cue_id": cid, "level": "error",
                           "message": f"종료 시간({e:.2f}초)이 최종 음성 길이({audio_duration:.2f}초)를 넘습니다."})
        if i > 0 and s < float(cs[i - 1]["end"]) - 0.001:
            issues.append({"cue_id": cid, "level": "error", "message": "앞 자막과 시간이 겹칩니다."})
        dur = e - s
        if 0 < dur < min_s:
            issues.append({"cue_id": cid, "level": "warn", "message": f"표시 시간이 {dur:.2f}초로 너무 짧습니다."})
        if dur > 0 and len(flat) / dur > max_cps:
            issues.append({"cue_id": cid, "level": "warn",
                           "message": f"초당 {len(flat) / dur:.1f}자로 읽기에 빠릅니다(기준 {max_cps:g}자)."})
        rows = text.split("\n")
        if len(rows) > max_lines:
            issues.append({"cue_id": cid, "level": "warn", "message": f"{len(rows)}줄입니다(기준 {max_lines}줄)."})
        if any(len(r) > max_chars + 2 for r in rows):
            issues.append({"cue_id": cid, "level": "warn", "message": f"한 줄이 {max(len(r) for r in rows)}자로 깁니다(기준 {max_chars}자)."})
        if dur > 7:
            issues.append({"cue_id": cid, "level": "warn", "message": f"한 자막이 {dur:.1f}초 동안 표시됩니다. 나누는 것을 고려하세요."})
        if c.get("conf") == "low" and not c.get("manual_time"):
            issues.append({"cue_id": cid, "level": "check", "message": "시간 신뢰도가 낮습니다. 재생해서 확인하세요."})
    return issues
