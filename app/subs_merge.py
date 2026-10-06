"""자막 ↔ 대본 원문 대응과 '수동 수정 유지' 재정렬.

각 자막(cue)은 src = [{"line_id", "a", "b"}] 로 '어느 대본 줄(화면 표시 문구, 줄바꿈 제외)의
몇 번째 글자부터 몇 번째 글자 앞까지'를 덮는지 기록한다. 한 자막이 여러 줄을 덮을 수도 있다(합치기).

다시 맞추기(재정렬) 시 '수동 수정 유지'는 다음 순서로 만든다.
1) 사용자가 문구를 고쳤거나(manual_text) 나누기·합치기로 모양을 바꾼(manual_struct) 자막은
   원문 위치(src)를 그대로 차지하고, 새 음성의 문장 시간 안에서 위치 비율로 시간을 다시 계산한다.
2) 사용자가 삭제한 부분(removed)은 계속 비워 둔다(원하면 다시 넣기 선택).
3) 남은 원문 글자는 새 자동 결과로 채운다.
4) 모든 줄이 빠짐없이·겹침 없이 덮였는지 검사한다. 통과하지 못하면 적용하지 않는다.
대본 문구 자체가 바뀐 줄의 수동 자막은 자동으로 맞추지 않고 사용자에게 선택을 받는다.
"""
from __future__ import annotations

from .jp_text import balance_two_lines
from .util import new_id


def flat(text: str) -> str:
    return (text or "").replace("\r", "").replace("\n", "")


def script_lines(project: dict) -> list[dict]:
    """자막 대상 줄(정렬과 같은 기준)."""
    return [ln for sc in (project.get("script") or {}).get("scenes", []) for ln in sc.get("lines", [])
            if (ln.get("tts") or "").strip() and (ln.get("display") or "").strip()]


def line_texts(project: dict) -> dict[str, str]:
    return {ln["id"]: flat(ln.get("display", "")) for ln in script_lines(project)}


# ---------------------------------------------------------------- 기존 자막에 원문 위치 붙이기(이전 버전 데이터)

def ensure_src(cues: list[dict], texts: dict[str, str]) -> bool:
    """src가 없는 자막에 원문 위치를 추정해 붙인다. 바뀐 것이 있으면 True."""
    changed = False
    groups: dict[str, list[dict]] = {}
    for c in sorted(cues, key=lambda c: c.get("start", 0)):
        if "src" not in c:
            groups.setdefault(c.get("line_id"), []).append(c)
    for lid, cs in groups.items():
        changed = True
        line = texts.get(lid)
        if line is None:
            for c in cs:
                c["src"], c["src_approx"] = [], True
            continue
        others = [c for c in cues if c.get("line_id") == lid and "src" in c and c not in cs]
        if others:  # 일부만 src가 있는 드문 경우: 나머지는 위치 미상으로 둔다
            for c in cs:
                c["src"], c["src_approx"] = [], True
            continue
        joined = "".join(flat(c.get("text", "")) for c in cs)
        n = len(line)
        if joined == line:
            pos = 0
            for c in cs:
                L = len(flat(c.get("text", "")))
                c["src"], c["src_approx"] = [{"line_id": lid, "a": pos, "b": pos + L}], False
                pos += L
        else:  # 문구를 고친 자막이 섞여 있음 → 글자 수 비율로 나눔(위치는 추정)
            lens = [max(1, len(flat(c.get("text", "")))) for c in cs]
            tot, acc = sum(lens), 0
            for i, c in enumerate(cs):
                a = round(n * acc / tot)
                acc += lens[i]
                b = n if i == len(cs) - 1 else round(n * acc / tot)
                c["src"], c["src_approx"] = [{"line_id": lid, "a": a, "b": b}], True
        for c in cs:
            if c.get("part") not in (None, 0) and float(c.get("part") or 0) % 1:
                c["manual_struct"] = True  # 예전 '나누기'로 생긴 자막(part 0.5 등)
    return changed


# ---------------------------------------------------------------- 표시 시간 확정(정렬·병합 공용)

def finalize(raw: list[dict], duration: float, fill_under: float) -> list[dict]:
    """raw: s,e(말소리 시작·끝)와 나머지 필드. 말 시작 직전부터 표시하고 짧은 쉼은 다음 자막까지 이어 표시."""
    raw = sorted(raw, key=lambda r: (r["s"], r["e"]))
    cues: list[dict] = []
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
        c = {k: v for k, v in r.items() if k not in ("s", "e")}
        c.setdefault("id", new_id("c"))
        c.update(start=round(s, 3), end=round(e, 3), speech_start=round(r["s"], 3), speech_end=round(r["e"], 3))
        cues.append(c)
    return cues


# ---------------------------------------------------------------- 구간 계산

def _merge_iv(iv: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in sorted(iv):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _subtract(full: tuple[int, int], covered: list[tuple[int, int]]) -> list[tuple[int, int]]:
    a0, b0 = full
    out, pos = [], a0
    for a, b in _merge_iv(covered):
        if b <= pos or a >= b0:
            continue
        if a > pos:
            out.append((pos, min(a, b0)))
        pos = max(pos, b)
    if pos < b0:
        out.append((pos, b0))
    return out


def coverage_report(cues: list[dict], removed: list[dict], texts: dict[str, str]) -> dict:
    """모든 줄이 자막(+사용자가 삭제한 부분)으로 빠짐없이·겹침 없이 덮였는지 검사."""
    problems = []
    per: dict[str, list[tuple[int, int, str]]] = {lid: [] for lid in texts}
    for c in cues:
        for r in c.get("src") or []:
            if r["line_id"] in per:
                per[r["line_id"]].append((r["a"], r["b"], c.get("text", "")))
            else:
                problems.append({"type": "orphan", "line_id": r["line_id"], "text": c.get("text", ""),
                                 "message": "대본에 없는 줄을 가리키는 자막이 있습니다."})
    for r in removed or []:
        if r["line_id"] in per:
            per[r["line_id"]].append((r["a"], r["b"], "(삭제한 부분)"))
    for lid, line in texts.items():
        segs = sorted(per[lid])
        pos = 0
        for a, b, t in segs:
            if a > pos:
                problems.append({"type": "missing", "line_id": lid, "a": pos, "b": a, "text": line[pos:a],
                                 "message": f"빠진 부분: 「{line[pos:a]}」"})
            elif a < pos:
                problems.append({"type": "duplicate", "line_id": lid, "a": a, "b": min(pos, b), "text": line[a:min(pos, b)],
                                 "message": f"두 번 들어간 부분: 「{line[a:min(pos, b)]}」"})
            pos = max(pos, b)
        if pos < len(line):
            problems.append({"type": "missing", "line_id": lid, "a": pos, "b": len(line), "text": line[pos:],
                             "message": f"빠진 부분: 「{line[pos:]}」"})
    return {"ok": not problems, "problems": problems}


# ---------------------------------------------------------------- 수동 수정 유지 재정렬

def is_user_shaped(c: dict) -> bool:
    return bool(c.get("manual_text") or c.get("manual_struct"))


def merge(old_cues: list[dict], old_texts: dict[str, str] | None, removed: list[dict] | None,
          auto_cues: list[dict], line_times: list[dict], project: dict, duration: float, settings: dict,
          choices: dict | None = None, restore_removed: bool = False) -> dict:
    choices = choices or {}
    removed = list(removed or [])
    texts = line_texts(project)
    old_cues = [dict(c) for c in old_cues]
    # 이전 버전 데이터: 원문 위치·줄 문구 기록이 없으면 추정
    ensure_src(old_cues, {**texts, **(old_texts or {})})
    old_texts = dict(old_texts or {})
    for lid, line in texts.items():
        if lid not in old_texts:
            auto_old = [c for c in old_cues if c.get("line_id") == lid and not is_user_shaped(c)]
            joined = "".join(flat(c.get("text", "")) for c in sorted(auto_old, key=lambda c: c.get("start", 0)))
            all_auto = auto_old and len(auto_old) == len([c for c in old_cues if c.get("line_id") == lid])
            old_texts[lid] = joined if all_auto and joined else line
    changed_lines = [{"line_id": lid, "old": old_texts.get(lid, ""), "new": texts[lid]}
                     for lid in texts if old_texts.get(lid, texts[lid]) != texts[lid]]
    changed_ids = {x["line_id"] for x in changed_lines}
    lt = {x["line_id"]: (x["start"], x["end"]) for x in line_times}
    conf_by_line = {x["line_id"]: x.get("conf", "mid") for x in line_times}

    kept, uncertain = [], []
    for c in old_cues:
        if not is_user_shaped(c):
            continue
        src = c.get("src") or []
        lids = [r["line_id"] for r in src]
        reason = None
        if not src:
            reason = "원문 위치를 알 수 없는 자막입니다."
        elif any(l not in texts for l in lids):
            reason = "자막이 가리키는 대본 줄이 지워졌습니다."
        elif any(l in changed_ids for l in lids):
            reason = "대본(화면 표시) 문구가 바뀐 줄입니다."
        if reason:
            choice = choices.get(c["id"], "new")
            uncertain.append({"cue_id": c["id"], "text": c.get("text", ""), "reason": reason, "choice": choice,
                              "lines": [{"line_id": l, "old": old_texts.get(l, ""), "new": texts.get(l, "")} for l in lids]})
            if choice != "keep" or not all(l in texts for l in lids) or not src:
                continue
            # 사용자가 '유지'를 고름: 바뀐 줄은 새 문구 길이에 비율로 맞춤
            src = [dict(r, a=round(r["a"] * len(texts[r["line_id"]]) / max(1, len(old_texts.get(r["line_id"], "")) or 1)),
                        b=round(r["b"] * len(texts[r["line_id"]]) / max(1, len(old_texts.get(r["line_id"], "")) or 1)))
                   if r["line_id"] in changed_ids else r for r in src]
            src = [dict(r, b=min(r["b"], len(texts[r["line_id"]]))) for r in src]
        kept.append(dict(c, src=src))

    # 수동 자막끼리 겹치면(예전 데이터 등) 자동 처리하지 않고 선택을 받는다
    claimed: dict[str, list[tuple[int, int, str]]] = {}
    for c in kept:
        for r in c["src"]:
            for a, b, other in claimed.get(r["line_id"], []):
                if r["a"] < b and a < r["b"] and other != c["id"]:
                    uncertain.append({"cue_id": c["id"], "text": c.get("text", ""), "choice": "new",
                                      "reason": "다른 수동 자막과 원문 위치가 겹칩니다.", "lines": []})
            claimed.setdefault(r["line_id"], []).append((r["a"], r["b"], c["id"]))
    overlap_ids = {u["cue_id"] for u in uncertain if u["reason"].startswith("다른 수동")}
    kept = [c for c in kept if c["id"] not in overlap_ids]

    keep_removed = [r for r in removed if not restore_removed and r["line_id"] in texts and r["line_id"] not in changed_ids]
    dropped_removed = [r for r in removed if r not in keep_removed]

    def t_at(lid: str, pos: int) -> float:
        s, e = lt.get(lid, (0.0, 0.0))
        n = max(1, len(texts[lid]))
        return s + (e - s) * pos / n

    raw = []
    for c in kept:
        first, last = c["src"][0], c["src"][-1]
        raw.append({**{k: v for k, v in c.items() if k not in ("start", "end", "speech_start", "speech_end")},
                    "s": t_at(first["line_id"], first["a"]), "e": t_at(last["line_id"], last["b"]),
                    "line_id": first["line_id"], "conf": min((conf_by_line.get(r["line_id"], "mid") for r in c["src"]),
                                                             key=["low", "mid", "high"].index),
                    "note": "수동 수정 유지(새 음성의 문장 시간 안에서 위치 비율로 시간을 다시 계산)", "manual_time": False})
    max_chars = int(settings.get("max_line_chars", 14))
    regenerated = 0
    for lid, line in texts.items():
        covered = [(r["a"], r["b"]) for c in kept for r in c["src"] if r["line_id"] == lid]
        covered += [(r["a"], r["b"]) for r in keep_removed if r["line_id"] == lid]
        holes = _subtract((0, len(line)), covered)
        if not holes:
            continue
        for ac in [c for c in auto_cues if c.get("line_id") == lid]:
            for r in ac.get("src") or []:
                for a, b in holes:
                    a2, b2 = max(a, r["a"]), min(b, r["b"])
                    if b2 <= a2:
                        continue
                    whole = (a2, b2) == (r["a"], r["b"])
                    regenerated += 1
                    raw.append({"line_id": lid, "part": ac.get("part", 0), "text": ac["text"] if whole else balance_two_lines(line[a2:b2], max_chars),
                                "s": ac["speech_start"] if whole else t_at(lid, a2), "e": ac["speech_end"] if whole else t_at(lid, b2),
                                "conf": ac.get("conf", "mid") if whole else "low", "method": ac.get("method"),
                                "note": ac.get("note", "") if whole else "수동 자막 사이 남은 부분을 새로 만든 자막입니다(시간은 위치 비율 추정).",
                                "manual_text": False, "manual_time": False, "src": [{"line_id": lid, "a": a2, "b": b2}]})
    cues = finalize(raw, duration, float(settings.get("fill_gaps_under_s", 0.6)))
    cov = coverage_report(cues, keep_removed, texts)
    manual_time_reset = sum(1 for c in old_cues if c.get("manual_time") and not is_user_shaped(c))
    return {"cues": cues, "line_texts": texts, "removed": keep_removed, "coverage": cov,
            "report": {"time_only": not changed_lines, "changed_lines": changed_lines,
                       "kept": len(kept), "regenerated": regenerated, "uncertain": uncertain,
                       "removed_kept": len(keep_removed), "removed_dropped": len(dropped_removed),
                       "manual_time_reset": manual_time_reset}}


def fresh(auto_cues: list[dict], project: dict) -> dict:
    """'새 결과로 모두 바꾸기': 수동 수정·삭제 기록 없이 새 자동 결과."""
    texts = line_texts(project)
    return {"cues": auto_cues, "line_texts": texts, "removed": [], "coverage": coverage_report(auto_cues, [], texts)}
