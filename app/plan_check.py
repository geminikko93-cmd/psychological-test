"""기획 후보끼리 '실질적으로 다른가'를 규칙으로 비교한다(AI 호출 없음, 품질 점수가 아님).

비교 기준
- 범주: 콘텐츠 종류 · 시청자 행동 · 결말 유형이 같은가
- 글: 첫 문장, 전개(장면 구성), 결말 문구의 글자 3-gram 유사도
같은 질문에 선택지·결과 말만 바꾼 후보(첫 문장·전개가 거의 같음)를 '비슷함'으로 표시한다.
"""
from __future__ import annotations

import re
import unicodedata

from .jp_text import similarity


def _norm(v: str) -> str:
    return re.sub(r"[\s・/、,()（）]", "", unicodedata.normalize("NFKC", str(v or "")).lower())


def _structure(c: dict) -> str:
    s = c.get("structure_ko") or []
    if isinstance(s, str):
        s = s.split("\n")
    # '장면 1:' 같은 번호 머리말은 비교에서 뺀다
    return " ".join(re.sub(r"^\s*장면\s*\d+\s*[:：.]\s*", "", x) for x in s)


def compare_pair(a: dict, b: dict) -> dict:
    same = []
    for key, label in (("content_type", "콘텐츠 종류"), ("viewer_action", "시청자 행동"), ("ending_type", "결말 유형")):
        if a.get(key) and _norm(a.get(key)) == _norm(b.get(key)):
            same.append(label)
    sim_first = similarity(a.get("first_line_ja", ""), b.get("first_line_ja", ""))
    sim_struct = similarity(_structure(a), _structure(b))
    sim_end = similarity(a.get("payoff_ko", "") + a.get("ending_ko", ""), b.get("payoff_ko", "") + b.get("ending_ko", ""))
    reasons = []
    if same:
        reasons.append("같은 " + "·".join(same))
    if sim_first >= 0.5:
        reasons.append(f"첫 문장이 거의 같음(유사도 {sim_first:.2f})")
    if sim_struct >= 0.45:
        reasons.append(f"전개가 비슷함(유사도 {sim_struct:.2f})")
    if sim_end >= 0.5:
        reasons.append(f"결말이 비슷함(유사도 {sim_end:.2f})")
    similar = (len(same) >= 2 and (sim_struct >= 0.3 or sim_first >= 0.4)) or len(same) == 3 \
        or sim_first >= 0.6 or (sim_struct >= 0.55 and sim_end >= 0.4)
    return {"a": a.get("id"), "b": b.get("id"), "similar": bool(similar), "reasons": reasons,
            "scores": {"first_line": round(sim_first, 2), "structure": round(sim_struct, 2), "ending": round(sim_end, 2)},
            "same_categories": same}


def compare_candidates(cands: list[dict]) -> dict:
    pairs = [compare_pair(cands[i], cands[j]) for i in range(len(cands)) for j in range(i + 1, len(cands))]
    flagged: dict[str, list[str]] = {}
    for p in pairs:
        if p["similar"]:
            # 뒤쪽 후보를 다시 만들 대상으로 표시(앞 후보는 유지)
            flagged.setdefault(p["b"], []).append(f"{p['a']}안과 비슷함: " + (", ".join(p["reasons"]) or "전반적으로 비슷함"))
    return {"pairs": pairs, "flagged": flagged,
            "note": "규칙 기반 자동 비교입니다(콘텐츠 종류·시청자 행동·결말 유형 일치와 문장 유사도). 품질 점수가 아닙니다."}
