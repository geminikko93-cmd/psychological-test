"""영상 일관성 관리: 화풍(고정)·반복 등장 요소(고정)·클립별 가변 설명을 분리해 Flow 프롬프트를 조립한다.

원칙
- 고정 블록(화풍·등장 요소 묘사)은 AI가 다시 쓰지 않는다. 저장된 원문을 이 모듈이 그대로 끼워 넣는다.
- AI·사용자가 쓰는 것은 클립별 가변 부분(행동·구도·카메라·시간 배분)뿐이다.
- "같은 사람", "이전 장면과 동일" 같은 말로 외형 설명을 대신하지 않는다(연결된 요소의 고정 묘사를 매번 넣는다).
- 검수는 사람이 눈으로 한다. 이 모듈은 '무엇이 바뀌어 다시 봐야 하는지'만 계산한다(동일성 자동 판정 없음).

프로젝트 데이터(모두 선택 필드 — 예전 프로젝트에 없어도 동작):
  visual.style    {preset_id, locked, version, style_en, palette[], lighting_en, material_en, line_en, motion_en,
                   avoid_en, accent{name,hex,note_ko}}
  visual.elements [{id, name, type, desc_ko, fixed_en, features{}, must_keep[], primary_ref_id, status, locked,
                    version, flow_name, flow_note, mascot}]
  visual.assets   [{id, kind(reference|start|pose), file, sha256, orig_name, element_id, clip_id, note}]
  visual.frames   [{id, kind(last_used|review_start|review_mid|review_end), clip_id, item_id, item_sig,
                    use_start, use_len, t, fps, file}]
  flow.clips[]    + element_ids, role, gen_mode, var_en, var_ko, changes[], start_frame{}, end_frame{},
                    ref_asset_ids, use_start, edit{}, reuse_of, still_asset_id, prompt_source, review{}
"""
from __future__ import annotations

import math
import re

from .presets import CLIP_ROLES, STYLE_PRESETS
from .util import stable_hash

ELEMENT_TYPES = {"person": "인물", "character": "캐릭터", "object": "사물", "place": "장소"}
FEATURE_KEYS = {
    "person": [("face", "얼굴"), ("hair", "머리"), ("body", "체형"), ("accessories", "안경·액세서리"),
               ("outfit", "의상(색상·소재·형태)")],
    "character": [("silhouette", "실루엣"), ("proportions", "비율"), ("face", "얼굴 구조"), ("pattern", "무늬"),
                  ("colors", "색상")],
    "object": [("shape", "형태"), ("color", "색상"), ("material", "재질"), ("parts", "부품"), ("unique", "고유 특징")],
    "place": [("layout", "공간 구조"), ("furniture", "가구·물건 배치"), ("openings", "창문·문 위치")],
}
GEN_MODES = {
    "text": "텍스트로 영상(Text to Video)",
    "ingredients": "재료/참조 이미지(Ingredients to Video)",
    "first_frame": "첫 프레임(Frames to Video: First)",
    "first_last": "첫·마지막 프레임(Frames to Video: First and last)",
    "still": "확정 이미지를 정지 화면으로 사용(Flow 생성 없음)",
    "reuse": "다른 클립 소재 재사용(Flow 생성 없음)",
}
FLOW_MODES = ("text", "ingredients", "first_frame", "first_last")
CHECK_ITEMS = [("face", "얼굴·캐릭터 형태"), ("outfit", "의상·액세서리"), ("object", "사물의 형태·색상·개수"),
               ("layout", "배경 배치"), ("continuity", "장면 연결"), ("choice_match", "선택 화면과 결과 화면의 소재 일치")]

COMMON_CONSTRAINTS = [
    "Vertical 9:16 composition; keep the main subjects in the center and upper two-thirds and leave the bottom area "
    "calm and uncluttered for captions added in editing.",
    "No on-screen text, no letters or numbers, no captions, no signs with writing, no logos, no watermarks.",
    "No dialogue, no speech, no lip movement, no narration; ambient sound only (narration is added separately).",
]
CHOICE_CONSTRAINT = ("The option objects keep exactly the same shape, color, position and count for the whole shot; "
                     "show them at similar size and equal visibility; no motion or lighting that highlights one option.")
SAME_AS_RE = re.compile(r"\b(same (person|man|woman|character|girl|boy|object|room|place)|same as (before|the previous|previous|earlier)"
                        r"|as (in|seen in) the previous (shot|scene|clip)|identical to the previous)\b", re.I)
NON_ASCII_SCRIPT_RE = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
TEXT_REQUEST_RE = re.compile(r"\b(caption|subtitle|title card|on-screen text|text overlay|written|letters? saying|sign that says|typography)\b", re.I)

COLORS = ["black", "white", "gray", "grey", "beige", "ivory", "cream", "brown", "tan", "khaki", "navy", "blue",
          "teal", "turquoise", "green", "sage", "olive", "mint", "yellow", "mustard", "gold", "orange", "red", "crimson",
          "burgundy", "maroon", "pink", "coral", "purple", "lavender", "violet", "silver", "charcoal", "denim"]
GARMENTS = ["shirt", "blouse", "t-shirt", "tee", "sweater", "cardigan", "hoodie", "jacket", "coat", "blazer", "vest",
            "dress", "skirt", "pants", "trousers", "jeans", "shorts", "scarf", "hat", "cap", "beanie", "glasses",
            "apron", "uniform", "suit", "tie", "boots", "shoes", "sneakers", "gloves", "earrings", "necklace"]
_COLOR_RE = "|".join(COLORS)
_WORD = r"[a-z][a-z-]*"


# ---------------------------------------------------------------- 기본 접근

def vis(project: dict) -> dict:
    return project.get("visual") or {}


def elements(project: dict) -> list[dict]:
    return [e for e in vis(project).get("elements") or [] if isinstance(e, dict) and e.get("id")]


def element_map(project: dict) -> dict:
    return {e["id"]: e for e in elements(project)}


def assets(project: dict) -> dict:
    return {a["id"]: a for a in vis(project).get("assets") or [] if isinstance(a, dict) and a.get("id")}


def frames(project: dict) -> dict:
    return {f["id"]: f for f in vis(project).get("frames") or [] if isinstance(f, dict) and f.get("id")}


def clips(project: dict) -> list[dict]:
    return (project.get("flow") or {}).get("clips") or []


def items(project: dict) -> dict:
    return {i.get("id"): i for i in (project.get("sources") or {}).get("items", []) if i.get("status") == "acquired"}


def new_style_from_preset(preset_id: str) -> dict:
    pr = STYLE_PRESETS.get(preset_id) or STYLE_PRESETS["picturebook"]
    return {"preset_id": pr["id"], "locked": False, "version": 0, "label_ko": pr["label_ko"], "desc_ko": pr["desc_ko"],
            "style_en": pr["style_en"], "palette": [dict(c) for c in pr["palette"]], "lighting_en": pr["lighting_en"],
            "material_en": pr["material_en"], "line_en": pr["line_en"], "motion_en": pr["motion_en"],
            "motion_ko": pr["motion_ko"], "avoid_en": pr["avoid_en"], "accent": {"name": "", "hex": "", "note_ko": ""}}


# ---------------------------------------------------------------- 고정 블록(저장된 원문을 그대로 사용)

def style_block(project: dict) -> tuple[str, str]:
    """(스타일 블록, 피할 요소). 프로젝트 화풍이 없으면 예전 방식의 flow.style(공통 스타일)을 쓴다."""
    st = vis(project).get("style") or {}
    if st.get("style_en"):
        parts = [f"Visual style: {st['style_en'].strip()}"]
        pal = [c.get("name") for c in st.get("palette") or [] if c.get("name")]
        if pal:
            parts.append("Base palette: " + ", ".join(pal) + ".")
        acc = (st.get("accent") or {}).get("name")
        if acc:
            parts.append(f"Episode accent color (small touches only): {acc}.")
        for k, label in (("lighting_en", "Lighting"), ("material_en", "Materials"), ("line_en", "Linework")):
            if (st.get(k) or "").strip():
                parts.append(f"{label}: {st[k].strip()}.")
        return " ".join(p.replace("..", ".") for p in parts), (st.get("avoid_en") or "").strip()
    legacy = (project.get("flow") or {}).get("style") or {}
    return ((f"Visual style: {legacy['look_en'].strip()}" if (legacy.get("look_en") or "").strip() else ""),
            (legacy.get("avoid_en") or "").strip())


def element_block(el: dict, mode: str = "text", ref_no: int | None = None) -> str:
    name = (el.get("name") or el.get("id")).strip()
    kind = {"person": "person", "character": "character", "object": "object", "place": "place"}.get(el.get("type"), "element")
    fixed = (el.get("fixed_en") or "").strip().rstrip(".")
    keep = [k.strip() for k in el.get("must_keep") or [] if k.strip()]
    head = f"{name} ({kind})"
    if mode == "ingredients" and ref_no:
        head += f" — shown in reference image {ref_no}; match its appearance"
    s = f"{head}: {fixed}." if fixed else f"{head}."
    if keep:
        s += " Must stay unchanged: " + "; ".join(keep) + "."
    if el.get("mascot"):
        s += " Use the provided original mascot design exactly; do not redesign it or add new characters."
    return s


# ---------------------------------------------------------------- 참조 자료·소재 해석

def clip_refs(project: dict, clip: dict) -> list[dict]:
    """이 클립의 생성 요청에서 '재료'로 선택할 기준 이미지. 명시 지정 > 연결 요소의 확정 기준 이미지."""
    em, am = element_map(project), assets(project)
    out, seen = [], set()
    for aid in clip.get("ref_asset_ids") or []:
        a = am.get(aid)
        if a and aid not in seen:
            el = em.get(a.get("element_id"))
            out.append({"asset_id": aid, "file": a["file"], "element_id": a.get("element_id"),
                        "element_name": el.get("name") if el else "", "explicit": True})
            seen.add(aid)
    for eid in clip.get("element_ids") or []:
        el = em.get(eid)
        if not el:
            continue
        aid = el.get("primary_ref_id")
        if aid and aid in am and aid not in seen and not any(r["element_id"] == eid for r in out):
            out.append({"asset_id": aid, "file": am[aid]["file"], "element_id": eid, "element_name": el.get("name", ""),
                        "explicit": False})
            seen.add(aid)
    for i, r in enumerate(out, 1):
        r["no"] = i
        el = em.get(r["element_id"])
        r["keep_en"] = "; ".join(el.get("must_keep") or []) if el else ""
    return out


def frame_ref(project: dict, ref: dict | None) -> dict | None:
    """start_frame/end_frame 지정 해석 → {source, file, label, frame?}"""
    if not ref or ref.get("source") in (None, "", "none"):
        return None
    if ref.get("source") == "asset":
        a = assets(project).get(ref.get("asset_id"))
        return {"source": "asset", "file": a["file"], "label": a.get("orig_name") or a["file"], "asset": a} if a else \
            {"source": "missing", "file": None, "label": "(지정한 이미지가 없음)"}
    if ref.get("source") == "frame":
        f = frames(project).get(ref.get("frame_id"))
        return {"source": "frame", "file": f["file"], "label": f"클립 {f.get('clip_label', '')} 실제 사용 끝 프레임 ({f.get('t')}초)",
                "frame": f} if f else {"source": "missing", "file": None, "label": "(지정한 프레임이 없음)"}
    return None


def resolve_media(project: dict, clip: dict, _depth: int = 0) -> dict | None:
    """클립이 실제로 쓰는 소재(영상 파일·정지 이미지). 재사용이면 원본 클립을 따라간다(순환 방지)."""
    if _depth > 8:
        return None
    if clip.get("gen_mode") == "reuse" and clip.get("reuse_of"):
        src = next((c for c in clips(project) if c.get("id") == clip["reuse_of"]), None)
        r = resolve_media(project, src, _depth + 1) if src and src is not clip else None
        return dict(r, via=clip["reuse_of"]) if r else None
    if clip.get("gen_mode") == "still" and clip.get("still_asset_id"):
        a = assets(project).get(clip["still_asset_id"])
        if a:
            return {"kind": "asset", "id": a["id"], "file": a["file"], "media": "image", "duration": None,
                    "sig": a.get("sha256") or a["file"]}
        return None
    it = items(project).get(clip.get("item_id"))
    if it:
        return {"kind": "item", "id": it["id"], "file": it["file"], "media": it.get("kind", "video"),
                "duration": it.get("duration"), "sig": item_sig(it)}
    return None


def item_sig(it: dict) -> str:
    return it.get("sha256") or f"{it.get('file')}:{it.get('size')}"


# ---------------------------------------------------------------- 충돌·주의 검사(규칙 기반)

def _color_noun_pairs(text: str) -> list[tuple[str, str]]:
    t = text.lower()
    out = []
    for m in re.finditer(rf"\b({_COLOR_RE})\b((?:\s+{_WORD}){{1,3}})", t):
        for w in m.group(2).split():
            out.append((m.group(1), w))
    return out


def _nouns(text: str) -> set[str]:
    return set(re.findall(_WORD, (text or "").lower()))


def element_text(el: dict) -> str:
    feats = " ".join(str(v) for v in (el.get("features") or {}).values() if v)
    return " ".join([el.get("fixed_en") or "", feats, " ".join(el.get("must_keep") or [])])


def find_conflicts(project: dict, clip: dict) -> list[dict]:
    """고정 설정과 가변 설명의 충돌. 의도적 변경(clip.changes)에 적힌 단어는 제외."""
    var = " ".join([clip.get("var_en") or ""])
    if not var.strip():
        return []
    change_words = set()
    for ch in clip.get("changes") or []:
        change_words |= _nouns(ch.get("en", "")) | _nouns(ch.get("ko", ""))
    em = element_map(project)
    linked = [em[e] for e in clip.get("element_ids") or [] if e in em]
    out = []
    var_pairs = _color_noun_pairs(var)
    for el in linked:
        etext = element_text(el)
        epairs = _color_noun_pairs(etext)
        enouns = _nouns(etext)
        ecolors = {c for c, _ in epairs} | {c for c in COLORS if re.search(rf"\b{c}\b", etext.lower())}
        for color, noun in var_pairs:
            if noun in change_words or color in change_words:
                continue
            fixed_colors = {c for c, n in epairs if n == noun}
            if fixed_colors and color not in fixed_colors:
                out.append({"element_id": el["id"], "level": "conflict",
                            "message_ko": f"'{el.get('name')}'의 고정 설정은 {'/'.join(sorted(fixed_colors))} {noun}인데, "
                                          f"가변 설명에 {color} {noun}이(가) 있습니다.",
                            "hint_ko": "의도한 변경이면 '의도적 변경'에 기록하고, 아니면 가변 설명을 고치세요."})
        if el.get("type") in ("person", "character"):
            for g in GARMENTS:
                if re.search(rf"\b{re.escape(g)}s?\b", var.lower()) and g not in enouns and g not in change_words:
                    col = next((c for c, n in var_pairs if n == g), "")
                    out.append({"element_id": el["id"], "level": "conflict",
                                "message_ko": f"'{el.get('name')}'의 고정 의상에 없는 '{(col + ' ' + g).strip()}'이(가) 가변 설명에 있습니다.",
                                "hint_ko": "고정 의상을 바꾸려면 요소를 수정·재확정하거나, 이 클립만의 의도적 변경으로 기록하세요."})
    # 같은 충돌 중복 제거
    seen, uniq = set(), []
    for c in out:
        if c["message_ko"] not in seen:
            seen.add(c["message_ko"])
            uniq.append(c)
    return uniq


def find_warnings(project: dict, clip: dict) -> list[str]:
    var = clip.get("var_en") or ""
    w = []
    em = element_map(project)
    if SAME_AS_RE.search(var):
        w.append("가변 설명에 'same person/as before' 같은 표현이 있습니다. 외형은 연결된 요소의 고정 묘사로 넣으니, "
                 "그 인물·사물을 이 클립에 '등장 요소'로 연결했는지 확인하세요.")
    if NON_ASCII_SCRIPT_RE.search(var):
        w.append("가변 설명에 일본어·한국어 글자가 있습니다. 영상 모델에 글자를 그리게 할 수 있으니 영어로만 쓰세요(질문·선택지·결과 문자는 CapCut 자막).")
    if TEXT_REQUEST_RE.search(var):
        w.append("가변 설명이 화면 속 글자·자막을 요청하는 것처럼 보입니다. 글자는 편집에서 넣으세요.")
    for eid in clip.get("element_ids") or []:
        if eid not in em:
            w.append(f"연결된 등장 요소({eid})가 목록에 없습니다(삭제됨). 연결을 정리하세요.")
    linked = set(clip.get("element_ids") or [])
    for el in em.values():
        nm = (el.get("name") or "").strip()
        if el["id"] not in linked and len(nm) >= 3 and re.search(rf"\b{re.escape(nm.lower())}\b", var.lower()):
            w.append(f"가변 설명에 '{nm}'이(가) 나오지만 이 클립의 등장 요소로 연결되지 않았습니다(고정 묘사가 들어가지 않음).")
    for eid in linked:
        el = em.get(eid)
        if el and not el.get("locked"):
            w.append(f"'{el.get('name')}'이(가) 아직 확정(잠금)되지 않았습니다. 묘사가 바뀌면 이 프롬프트도 바뀝니다.")
    mode = clip.get("gen_mode") or "text"
    if mode == "ingredients" and not clip_refs(project, clip):
        w.append("재료(참조 이미지) 방식인데 사용할 기준 이미지가 없습니다. 등장 요소의 기준 이미지를 확정하세요.")
    if mode in ("first_frame", "first_last"):
        sf = frame_ref(project, clip.get("start_frame"))
        if not sf or sf["source"] == "missing":
            w.append("첫 프레임 방식인데 시작 프레임이 지정되지 않았습니다.")
    if mode == "first_last":
        ef = frame_ref(project, clip.get("end_frame"))
        if not ef or ef["source"] == "missing":
            w.append("첫·마지막 프레임 방식인데 마지막 프레임 이미지가 지정되지 않았습니다.")
    if mode == "still" and not clip.get("still_asset_id"):
        w.append("정지 화면 방식인데 사용할 확정 이미지가 지정되지 않았습니다.")
    if mode == "reuse" and not clip.get("reuse_of"):
        w.append("재사용 방식인데 재사용할 클립이 지정되지 않았습니다.")
    return w


# ---------------------------------------------------------------- 프롬프트 조립

def compose(project: dict, clip: dict) -> dict:
    """최종 영어 프롬프트 = 등장 요소 고정 묘사 + 장소·공통 스타일 + 클립별 가변 설명 + 의도적 변경 + 공통 제약."""
    mode = clip.get("gen_mode") or clip.get("mode") or "text"
    if mode not in GEN_MODES:
        mode = "text"
    em = element_map(project)
    linked = [em[e] for e in clip.get("element_ids") or [] if e in em]
    refs = clip_refs(project, clip) if mode == "ingredients" else []
    ref_no = {r["element_id"]: r["no"] for r in refs if r.get("element_id")}
    subjects = [e for e in linked if e.get("type") != "place"]
    places = [e for e in linked if e.get("type") == "place"]
    style, avoid = style_block(project)
    sections: list[tuple[str, str]] = []

    if mode in ("first_frame", "first_last"):
        sections.append(("start", "Start exactly from the provided start frame image and keep everything in it consistent"
                                  + (" ; end on the provided last frame image." if mode == "first_last" else ".")))
    if refs:
        sections.append(("refs", "Reference images: " + " ".join(
            f"Image {r['no']} = {r['element_name'] or 'reference'} (use only for its appearance"
            + (f"; keep {r['keep_en']}" if r["keep_en"] else "") + ")." for r in refs)))
    if subjects:
        sections.append(("elements", " ".join(element_block(e, mode, ref_no.get(e["id"])) for e in subjects)))
    if places:
        sections.append(("place", " ".join("Setting — " + element_block(e, mode, ref_no.get(e["id"])) for e in places)))
    if style:
        sections.append(("style", style))
    var = (clip.get("var_en") or "").strip()
    dur = clip.get("duration")
    if var:
        sections.append(("shot", f"Shot ({dur}s): {var}" if dur else f"Shot: {var}"))
    changes = [c for c in clip.get("changes") or [] if (c.get("en") or "").strip()]
    if changes:
        sections.append(("changes", "Intentional change for this shot only: " + "; ".join(c["en"].strip() for c in changes) + "."))
    cons = list(COMMON_CONSTRAINTS)
    if clip.get("role") == "choice":
        cons.append(CHOICE_CONSTRAINT)
    if avoid:
        cons.append(f"Avoid: {avoid}.")
    sections.append(("constraints", " ".join(cons)))
    text = "\n\n".join(s for _, s in sections if s)
    return {"prompt_en": text if var else "", "sections": [{"key": k, "text": s} for k, s in sections],
            "mode": mode, "refs": refs, "needs_flow": mode in FLOW_MODES}


def flow_settings_ko(project: dict, clip: dict, model_label: str, aspect: str) -> list[str]:
    """Flow 화면에서 사용자가 직접 고를 설정(프로그램은 Flow에 아무것도 자동 등록·선택하지 않음)."""
    mode = clip.get("gen_mode") or "text"
    if mode == "still":
        a = assets(project).get(clip.get("still_asset_id"))
        return [f"Flow 생성 없음: 확정 이미지 '{(a or {}).get('orig_name', '(미지정)')}'를 CapCut에서 정지 화면으로 사용"]
    if mode == "reuse":
        return [f"Flow 생성 없음: 클립 {clip_label(project, clip.get('reuse_of'))}의 소재를 다시 사용(크롭·확대는 CapCut 편집 지시 참고)"]
    out = [f"모델: {model_label}", f"화면 비율: 세로 {aspect}", f"길이: {clip.get('duration')}초", f"방식: {GEN_MODES[mode]}"]
    if mode == "ingredients":
        refs = clip_refs(project, clip)
        out.append("참조 이미지: " + (", ".join(f"{r['no']}) {r['element_name'] or r['file']}" for r in refs) or "(없음)")
                   + " — Flow 프로젝트에 올려 두는 것만으로는 적용되지 않습니다. 생성 요청 입력란에서 재료로 직접 선택하세요.")
    if mode in ("first_frame", "first_last"):
        sf = frame_ref(project, clip.get("start_frame"))
        out.append("시작 프레임: " + (sf["label"] if sf else "(미지정)") + " — 생성 요청의 첫 프레임 칸에 직접 넣으세요.")
    if mode == "first_last":
        ef = frame_ref(project, clip.get("end_frame"))
        out.append("마지막 프레임: " + (ef["label"] if ef else "(미지정)"))
    names = [e for e in (element_map(project).get(i) for i in clip.get("element_ids") or []) if e and e.get("flow_name")]
    if names:
        out.append("Flow Characters에 직접 등록해 둔 이름(사용자 기록): " + ", ".join(f"{e['flow_name']}" for e in names)
                   + " — 프로그램이 Flow에 등록·연결하지 않습니다.")
    return out


def guidance_ko(project: dict, clip: dict) -> list[str]:
    """권장 생성 방식 안내(규칙 기반)."""
    out = []
    cl = clips(project)
    idx = next((i for i, c in enumerate(cl) if c.get("id") == clip.get("id")), -1)
    prev = cl[idx - 1] if idx > 0 else None
    em = element_map(project)
    linked = [em[e] for e in clip.get("element_ids") or [] if e in em]
    role = clip.get("role")
    if role in ("choice", "think", "ending"):
        out.append("선택지·마무리 화면은 모양 유지가 중요합니다. 확정 이미지(정지 화면)나 이미 확정한 영상을 재사용하는 것을 권장합니다.")
    if role == "result":
        out.append("결과 화면은 선택 화면과 같은 소재를 재사용(크롭·확대 편집 지시)하거나, 같은 기준 이미지로 시작하세요.")
    if any(e.get("type") == "person" for e in linked):
        out.append("같은 인물의 다른 장면: 그 인물의 같은 기준 이미지를 재료로 다시 쓰세요.")
    if any(e.get("type") == "object" for e in linked):
        out.append("같은 사물의 다른 장면: 그 사물의 같은 기준 이미지를 재료로 다시 쓰세요.")
    if prev and clip.get("continues_previous"):
        out.append("직전 동작을 이어 가는 클립입니다: 이전 클립의 '실제 사용 구간 끝 프레임'을 시작 프레임으로 쓰세요(파일 끝 프레임이 아님).")
    elif prev:
        out.append("이전 클립과 동작이 이어지지 않으면 이전 마지막 프레임 대신 원래 기준 이미지에서 새로 시작하세요.")
    return out


def clip_label(project: dict, clip_id: str | None) -> str:
    c = next((x for x in clips(project) if x.get("id") == clip_id), None)
    return f"S{c.get('scene_no')}-C{c.get('index_in_scene')}" if c else "(없음)"


# ---------------------------------------------------------------- 검수 상태(무엇이 바뀌었는지)

def element_fp(project: dict, el: dict) -> str:
    a = assets(project).get(el.get("primary_ref_id")) or {}
    return stable_hash([el.get("type"), el.get("fixed_en"), el.get("must_keep"), el.get("features"),
                        a.get("sha256") or a.get("file")])


def style_fp(project: dict) -> str:
    st = vis(project).get("style") or {}
    legacy = (project.get("flow") or {}).get("style") or {}
    return stable_hash([style_block(project), st.get("accent"), legacy.get("look_en") if not st.get("style_en") else None])


def frame_basis(project: dict, clip: dict) -> dict | None:
    """이 클립의 '실제 사용 구간'을 결정하는 값(영상 파일·사용 시작·사용 길이)."""
    m = resolve_media(project, clip)
    if not m:
        return None
    return {"item_id": m["id"], "item_sig": m["sig"], "use_start": round(float(clip.get("use_start") or 0), 3),
            "use_len": round(float(clip.get("need_s") or 0), 3)}


def frame_stale(project: dict, fr: dict) -> str | None:
    """추출한 프레임이 원본 클립의 현재 영상·사용 구간과 다르면 이유를 돌려준다."""
    src = next((c for c in clips(project) if c.get("id") == fr.get("clip_id")), None)
    if not src:
        return "프레임을 뽑은 원본 클립이 클립 계획에서 사라졌습니다."
    b = frame_basis(project, src)
    if not b:
        return "원본 클립에 연결된 영상이 없습니다."
    if b["item_sig"] != fr.get("item_sig"):
        return "원본 클립의 영상 파일이 바뀌었습니다."
    if abs(b["use_start"] - float(fr.get("use_start") or 0)) > 0.001 or abs(b["use_len"] - float(fr.get("use_len") or 0)) > 0.001:
        return "원본 클립의 사용 구간(시작·길이)이 바뀌었습니다."
    return None


def review_basis(project: dict, clip: dict) -> dict:
    em = element_map(project)
    m = resolve_media(project, clip)
    sf = frame_ref(project, clip.get("start_frame"))
    return {
        "refs": stable_hash([[e, element_fp(project, em[e])] for e in clip.get("element_ids") or [] if e in em]
                            + [[a, (assets(project).get(a) or {}).get("sha256")] for a in clip.get("ref_asset_ids") or []]),
        "style": style_fp(project),
        "prompt": stable_hash((clip.get("prompt_en") or "").strip()),
        "video": m["sig"] if m else None,
        "range": [round(float(clip.get("use_start") or 0), 3), round(float(clip.get("need_s") or 0), 3)],
        "start_frame": stable_hash([clip.get("start_frame"), (sf or {}).get("file"),
                                    (sf.get("asset") or {}).get("sha256") if sf else None]),
    }


BASIS_REASONS = {"refs": "등장 요소의 고정 묘사·기준 이미지가 바뀜", "style": "화풍·공통 스타일이 바뀜",
                 "prompt": "프롬프트가 바뀜", "video": "영상(소재) 파일이 바뀜", "range": "실제 사용 구간이 바뀜",
                 "start_frame": "시작 프레임 지정·파일이 바뀜"}


def review_state(project: dict, clip: dict) -> dict:
    """registered(영상 등록)와 review(일관성 검수)를 따로 계산. 자동 동일성 판정은 하지 않는다."""
    m = resolve_media(project, clip)
    rv = clip.get("review") or {}
    reasons = []
    sf = frame_ref(project, clip.get("start_frame"))
    if sf and sf.get("frame"):
        why = frame_stale(project, sf["frame"])
        if why:
            reasons.append("시작 프레임 재추출 필요: " + why)
    if sf and sf["source"] == "missing":
        reasons.append("지정한 시작 프레임 자료가 없습니다.")
    if not m:
        return {"registered": False, "state": "no_media", "label_ko": "영상 미등록", "reasons": reasons}
    if not rv.get("state"):
        return {"registered": True, "state": "todo", "label_ko": "검수 전", "reasons": reasons}
    old = rv.get("basis") or {}
    cur = review_basis(project, clip)
    changed = [BASIS_REASONS[k] for k in BASIS_REASONS if old.get(k) != cur.get(k)]
    reasons = changed + reasons
    if reasons:  # 지문 변경 또는 시작 프레임의 원본(이전 클립 영상·사용 구간) 변경
        return {"registered": True, "state": "recheck", "label_ko": "재검토 필요", "reasons": reasons,
                "previous": rv.get("state")}
    if rv["state"] == "pass":
        return {"registered": True, "state": "pass", "label_ko": "검수 통과(사람 확인)", "reasons": reasons}
    return {"registered": True, "state": "fix", "label_ko": "수정 필요", "reasons": reasons}


# ---------------------------------------------------------------- 실제 사용 구간 프레임 시각

def used_frame_times(use_start: float, use_len: float, src_duration: float | None, fps: float | None) -> dict:
    """편집에서 원본의 [use_start, use_start+use_len) 구간만 쓴다. 그 구간 안의 시작·중간·끝 프레임 번호와 시각.
    프레임 k는 k/fps에 시작해 1/fps 동안 보인다. 끝 프레임 = 구간 끝보다 먼저 시작하는 마지막 프레임.
    원본 길이보다 긴 구간은 원본 끝에서 자르고, 구간 밖 프레임은 고르지 않는다."""
    fps = float(fps) if fps and fps > 0 else 30.0
    a = max(0.0, float(use_start or 0))
    end = a + max(0.0, float(use_len or 0))
    if src_duration:
        end = min(end, float(src_duration))
        a = min(a, max(0.0, float(src_duration) - 1.0 / fps))
    last_frame = max(0, int(math.floor(src_duration * fps + 1e-6)) - 1) if src_duration else None
    k0 = int(math.ceil(a * fps - 1e-6))
    k_end = int(math.ceil(end * fps - 1e-6)) - 1
    if last_frame is not None:
        k_end = min(k_end, last_frame)
        k0 = min(k0, last_frame)
    k_end = max(k_end, k0)
    k_mid = (k0 + k_end) // 2
    f = lambda k: {"k": k, "t": round(k / fps, 4), "seek": round(max(0.0, (k - 0.5) / fps), 4)}  # noqa: E731
    return {"fps": fps, "use_start": a, "use_end": round(end, 4), "start": f(k0), "mid": f(k_mid), "end": f(k_end)}


# ---------------------------------------------------------------- AI 등장 요소 제안 병합(확정 요소 보호)

def _key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def merge_element_proposals(existing: list[dict], proposals: list[dict]) -> dict:
    """AI 제안을 '새 초안 추가'와 '미확정 요소 갱신 제안'으로 나눈다. 확정·잠긴 요소는 절대 바꾸지 않는다."""
    by_name = {_key(e.get("name")): e for e in existing}
    by_id = {e.get("id"): e for e in existing}
    add, update, skipped = [], [], []
    for p in proposals:
        cur = by_id.get(p.get("id")) or by_name.get(_key(p.get("name")))
        if cur is None:
            add.append(p)
        elif cur.get("locked") or cur.get("status") == "confirmed":
            skipped.append({"id": cur["id"], "name": cur.get("name"), "reason_ko": "확정·잠긴 요소라 AI 제안으로 바꾸지 않음"})
        else:
            update.append({"id": cur["id"], "proposal": p})
    return {"add": add, "update": update, "skipped_locked": skipped}


def role_label(role: str | None) -> str:
    return CLIP_ROLES.get(role or "", "")


# ---------------------------------------------------------------- AI 프롬프트 결과 적용(가변 부분만)

LEGACY_MODE = {"text": "text", "ingredients": "ingredients", "first_frame": "first_frame", "first_last": "first_frame",
               "still": "text", "reuse": "text"}


def apply_ai_prompts(project: dict, result: dict) -> dict:
    """AI 결과를 클립에 적용한다. 바꾸는 것은 가변 부분(var_en·의미·진행·권장 방식)뿐이고,
    화풍·등장 요소(고정 블록)는 건드리지 않는다. 사용자가 직접 정한 등장 요소·역할·생성 방식은 유지하고 AI 제안만 따로 둔다.
    직전 프롬프트는 prompt_prev에 보관(되돌리기)."""
    import json as _json

    p = _json.loads(_json.dumps(project))
    fl = p.setdefault("flow", {})
    cl = {c["id"]: c for c in fl.get("clips") or []}
    el_ids = set(element_map(p))
    applied = []
    for out in result.get("clips") or []:
        c = cl.get(out.get("clip_id"))
        if not c:
            continue
        if c.get("prompt_en") or c.get("var_en"):
            c["prompt_prev"] = {"en": c.get("prompt_en", ""), "ko": c.get("prompt_ko", ""), "var_en": c.get("var_en", ""),
                                "var_ko": c.get("var_ko", ""), "beats_ko": c.get("beats_ko", ""),
                                "source": c.get("prompt_source") or ("legacy" if c.get("prompt_en") else "composed")}
        c.update(var_en=out.get("var_en", ""), var_ko=out.get("var_ko", ""), prompt_ko=out.get("var_ko", ""),
                 beats_ko=out.get("beats_ko", ""), mode_note_ko=out.get("mode_note_ko", ""), risk_ko=out.get("risk_ko", ""),
                 prompt_source="composed", prompt_for_duration=c.get("duration"))
        ai_els = [e for e in out.get("element_ids") or [] if e in el_ids]
        if c.get("elements_manual"):
            c["ai_element_ids"] = ai_els
        else:
            c["element_ids"] = ai_els
        if out.get("role") and not c.get("role_manual"):
            c["role"] = out["role"]
        if out.get("gen_mode") in GEN_MODES:
            if c.get("gen_mode_manual"):
                c["ai_gen_mode"] = out["gen_mode"]
            else:
                c["gen_mode"] = out["gen_mode"]
        c["mode"] = LEGACY_MODE.get(c.get("gen_mode") or "text", "text")
        if not c.get("continues_manual"):
            c["continues_previous"] = bool(out.get("continues_previous"))
        if (out.get("intended_change_en") or "").strip():
            # AI가 제안한 의도적 변경은 '제안'으로만 둔다(사용자가 확인해야 프롬프트에 들어감)
            c["ai_change_suggestion"] = {"en": out["intended_change_en"], "ko": out.get("intended_change_ko", "")}
        applied.append(c["id"])
    if result.get("style") and not (vis(p).get("style") or {}).get("style_en"):
        if not (fl.get("style") or {}).get("look_en") or result.get("full"):
            fl["style"] = result["style"]
    for cid in applied:
        cl[cid]["prompt_en"] = compose(p, cl[cid])["prompt_en"]
    return {"clips": fl.get("clips") or [], "style": fl.get("style"), "applied": applied}
