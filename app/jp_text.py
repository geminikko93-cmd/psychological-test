"""일본어 텍스트 도구: 읽기(히라가나) 추정, 모라 수, 문절 기반 줄바꿈, 표현 점검, 중복 검사.

- 읽기 추정은 pykakasi 사전 기반이라 틀릴 수 있다(참고용으로만 표시).
- 줄바꿈은 BudouX(구글의 일본어 문절 분할 모델, 로컬 실행)로 문절 경계를 찾는다.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_SMALL = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")
_NO_LINE_START = set("、。，．・：；？！ー」』）】〉》〕…‥ゃゅょっぁぃぅぇぉャュョッァィゥェォ!?)")
_NO_LINE_END = set("「『（【〈《〔(")


@lru_cache(maxsize=1)
def _kks():
    try:
        import pykakasi

        return pykakasi.kakasi()
    except Exception:  # pragma: no cover
        return None


@lru_cache(maxsize=1)
def _budoux():
    try:
        import budoux

        return budoux.load_default_japanese_parser()
    except Exception:  # pragma: no cover
        return None


def to_hira(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    k = _kks()
    if k is None:
        return text
    return "".join(item["hira"] for item in k.convert(text))


def reading_hint(text: str) -> str:
    """한자가 포함된 단어의 읽기 추정(참고용). '最初(さいしょ)' 형식."""
    k = _kks()
    if k is None:
        return ""
    out = []
    for item in k.convert(text or ""):
        orig, hira = item["orig"], item["hira"]
        if re.search(r"[一-鿿]", orig) and hira and hira != orig:
            out.append(f"{orig}({hira})")
    return "、".join(dict.fromkeys(out))


def mora_count(text: str) -> int:
    """낭독 길이 추정용 모라 수. 문장부호는 짧은 쉼으로 가중."""
    hira = to_hira(text)
    n = 0.0
    for ch in hira:
        if "ぁ" <= ch <= "ゟ" or "ァ" <= ch <= "ヿ":
            if ch in _SMALL:
                continue
            n += 1
        elif ch == "ー":
            n += 1
        elif ch in "、，,":
            n += 1.5
        elif ch in "。！？!?":
            n += 1
        elif ch.isascii() and ch.isalnum():
            n += 0.8
        elif re.match(r"[一-鿿]", ch):
            n += 2  # 변환 실패한 한자
    return max(1, int(round(n)))


def phrases(text: str) -> list[str]:
    p = _budoux()
    text = text or ""
    if p is None:
        return re.findall(r".+?(?:[、。！？]|(?<=[はがをにでともへ])|$)", text) or [text]
    return p.parse(text)


def _visual_len(s: str) -> float:
    return sum(0.5 if unicodedata.east_asian_width(c) in ("Na", "H") else 1 for c in s)


def wrap_lines(text: str, max_chars: int = 14) -> list[str]:
    """문절 경계에서 줄바꿈. 줄 머리 금칙문자(、。」 등)가 줄 앞에 오지 않게 한다.
    사용자가 이미 넣은 줄바꿈은 그대로 존중한다."""
    out: list[str] = []
    for para in (text or "").split("\n"):
        para = para.strip()
        if not para:
            continue
        cur = ""
        for ph in phrases(para):
            if cur and _visual_len(cur + ph) > max_chars:
                out.append(cur)
                cur = ph
            else:
                cur += ph
        if cur:
            out.append(cur)
    # 금칙 처리: 줄 머리 금칙 문자는 앞줄로 붙인다
    for i in range(1, len(out)):
        while out[i] and out[i][0] in _NO_LINE_START:
            out[i - 1] += out[i][0]
            out[i] = out[i][1:]
        while out[i - 1] and out[i - 1][-1] in _NO_LINE_END:
            out[i] = out[i - 1][-1] + out[i]
            out[i - 1] = out[i - 1][:-1]
    return [x for x in out if x]


def balance_two_lines(text: str, max_chars: int = 14) -> str:
    """2줄 이내로 들어가면 두 줄 길이를 비슷하게 맞춘다(문절 경계 기준)."""
    flat = text.replace("\n", "")
    if _visual_len(flat) <= max_chars:
        return flat
    ph = phrases(flat)
    best, best_score = None, None
    for i in range(1, len(ph)):
        a, b = "".join(ph[:i]), "".join(ph[i:])
        if b and b[0] in _NO_LINE_START:
            continue
        if _visual_len(a) > max_chars or _visual_len(b) > max_chars:
            continue
        score = abs(_visual_len(a) - _visual_len(b))
        if best_score is None or score < best_score:
            best, best_score = a + "\n" + b, score
    return best or "\n".join(wrap_lines(flat, max_chars))


def split_for_cues(text: str, max_chars: int = 14, max_lines: int = 2) -> list[str]:
    """한 줄 대본을 자막 카드 여러 장으로 나눈다(문절·문장부호 경계)."""
    flat = (text or "").replace("\n", "")
    if _visual_len(flat) <= max_chars * max_lines:
        return [balance_two_lines(flat, max_chars)]
    # 1차: 문장 부호(。！？、) 경계로 나눔
    chunks, cur = [], ""
    limit = max_chars * max_lines
    for ph in phrases(flat):
        if cur and _visual_len(cur + ph) > limit:
            chunks.append(cur)
            cur = ph
        else:
            cur += ph
            if ph and ph[-1] in "。！？" and _visual_len(cur) >= limit * 0.5:
                chunks.append(cur)
                cur = ""
    if cur:
        if chunks and _visual_len(cur) < 4 and _visual_len(chunks[-1] + cur) <= limit + 2:
            chunks[-1] += cur
        else:
            chunks.append(cur)
    return [balance_two_lines(c, max_chars) for c in chunks]


# ---------------------------------------------------------------- 표현 점검

LINT_RULES = [
    # (정규식, 분류, 한국어 안내)
    (r"毎日|明日も|また明日|明日の|毎朝|毎晩|次は明日", "약속",
     "주 3회 운영과 맞지 않을 수 있는 업로드 약속 표현입니다(매일·내일 또 등). 실제 일정과 맞는지 확인하세요."),
    (r"[0-9０-９一二三]秒で", "과장", "'○초 만에 알 수 있다' 류의 과장 표현입니다. 기본값으로 쓰지 않는 것을 권장합니다."),
    (r"本当の(性格|自分|あなた)|本性|隠された性格", "과장", "'진짜 성격' 류 표현은 창작 문항을 실제 진단처럼 보이게 합니다."),
    (r"絶対|必ず|100[%％]|間違いなく|確実に", "단정", "단정 표현입니다. 오락 콘텐츠라면 완화하는 것이 좋습니다."),
    (r"当たる|的中|当たりすぎ", "단정", "'잘 맞는다'는 주장입니다. 근거 없이 쓰면 과장으로 보일 수 있습니다."),
    (r"科学的|心理学的|心理学では|研究で|研究によると|論文|統計|データによると|専門家|証明され", "근거",
     "과학·연구·통계를 언급합니다. 실제 출처가 없다면 빼야 합니다(AI가 만든 근거일 수 있음)."),
    (r"[0-9０-９]+(\.[0-9]+)?\s*[%％]|[0-9０-９]+人に[0-9０-９]+人", "근거", "수치·비율이 있습니다. 실제 출처를 확인해 메모하세요."),
    (r"診断", "표현", "'진단' 표현은 실제 진단처럼 보일 수 있습니다. 오락용임이 분명한지 확인하세요."),
]


def lint_text(text: str) -> list[dict]:
    out = []
    for pat, cat, msg in LINT_RULES:
        for m in re.finditer(pat, text or ""):
            out.append({"category": cat, "match": m.group(0), "message": msg})
    return out


def lint_project(p: dict) -> list[dict]:
    """대본·제목·설명 전체 표현 점검. 각 항목: where, line_id, category, match, message."""
    issues: list[dict] = []
    kind = (p.get("idea") or {}).get("content_kind", "undecided")
    seen = set()

    def add(where, line_id, items):
        for it in items:
            key = (where, line_id, it["category"], it["match"])
            if key in seen:
                continue
            seen.add(key)
            if kind == "factual" and it["category"] == "근거":
                it = dict(it, message="사실 콘텐츠입니다. 이 수치·근거의 실제 출처를 장면의 '근거 메모'에 적어 두세요.")
            issues.append(dict(it, where=where, line_id=line_id))

    for si, sc in enumerate((p.get("script") or {}).get("scenes", [])):
        for ln in sc.get("lines", []):
            add(f"장면 {si + 1}", ln.get("id"), lint_text(ln.get("display", "")) + lint_text(ln.get("tts", "")))
            disp = (ln.get("display") or "").replace("\n", "")
            if len(disp) > 28:
                issues.append({"where": f"장면 {si + 1}", "line_id": ln.get("id"), "category": "길이",
                               "match": disp[:12] + "…", "message": f"화면 문장이 {len(disp)}자로 깁니다. 자막 2장 이상으로 나뉩니다."})
            tts = ln.get("tts") or ""
            if re.search(r"[A-Za-zＡ-Ｚａ-ｚ0-9０-９]", tts):
                issues.append({"where": f"장면 {si + 1}", "line_id": ln.get("id"), "category": "읽기",
                               "match": re.search(r"[A-Za-zＡ-Ｚａ-ｚ0-9０-９]+", tts).group(0),
                               "message": "TTS 문장에 숫자·영문이 있습니다. 타입캐스트가 의도대로 읽는지 확인하거나 가나로 바꾸세요."})
            if re.search(r"(?:\[|\]|【|】|S\d+|#\d|장면|씬)", tts):
                issues.append({"where": f"장면 {si + 1}", "line_id": ln.get("id"), "category": "낭독문",
                               "match": "", "message": "TTS 문장에 장면 번호나 편집 지시로 보이는 문자가 섞여 있습니다."})
        if kind == "factual":
            txt = "".join(ln.get("display", "") for ln in sc.get("lines", []))
            if re.search(r"[0-9０-９]|研究|調査|によると", txt) and not (sc.get("fact_note") or "").strip():
                issues.append({"where": f"장면 {si + 1}", "line_id": None, "category": "근거",
                               "match": "", "message": "사실 콘텐츠의 수치·주장이 있는데 근거 메모(출처 URL)가 비어 있습니다."})
    pub = p.get("publish") or {}
    add("게시 제목", None, lint_text(pub.get("title", "")))
    add("설명문", None, lint_text(pub.get("description", "")))
    # 제목·설명과 대본의 주장 수준 차이
    script_all = "".join(ln.get("display", "") for sc in (p.get("script") or {}).get("scenes", []) for ln in sc.get("lines", []))
    for where, text in (("게시 제목", pub.get("title", "")), ("설명문", pub.get("description", ""))):
        for it in lint_text(text):
            if it["category"] in ("과장", "단정", "근거") and it["match"] not in script_all:
                issues.append({"where": where, "line_id": None, "category": "일관성", "match": it["match"],
                               "message": f"'{it['match']}'는 대본에 없는 주장입니다. 제목·설명이 영상보다 강한 주장을 하지 않게 맞추세요."})
    return issues


# ---------------------------------------------------------------- 중복 검사

def _grams(text: str, n: int = 3) -> set[str]:
    t = re.sub(r"[\s、。！？!?「」『』（）()…・ー]", "", unicodedata.normalize("NFKC", text or ""))
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def similarity(a: str, b: str) -> float:
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def overlap_report(current: dict, others: list[dict], threshold: float = 0.45) -> list[dict]:
    """이전 프로젝트와 표현·구성이 겹치는 줄을 찾는다."""
    out = []
    cur_lines = [(ln.get("id"), ln.get("display", "")) for sc in (current.get("script") or {}).get("scenes", [])
                 for ln in sc.get("lines", [])]
    for o in others:
        o_lines = [ln.get("display", "") for sc in (o.get("script") or {}).get("scenes", []) for ln in sc.get("lines", [])]
        for lid, text in cur_lines:
            if len(text) < 8:
                continue
            best = max(((similarity(text, t), t) for t in o_lines), default=(0, ""))
            if best[0] >= threshold:
                out.append({"line_id": lid, "text": text, "other_project": o.get("name"), "other_text": best[1],
                            "score": round(best[0], 2)})
        whole_a = "".join(t for _, t in cur_lines)
        whole_b = "".join(o_lines)
        sim = similarity(whole_a, whole_b)
        if sim >= 0.3 and whole_a and whole_b:
            out.append({"line_id": None, "text": "(대본 전체)", "other_project": o.get("name"), "other_text": "",
                        "score": round(sim, 2)})
    return out
