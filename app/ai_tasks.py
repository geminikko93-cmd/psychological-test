"""AI 작업: 요청 → JSON 해석 → 검증·정규화. 결과는 프로젝트에 바로 쓰지 않고
화면에서 사용자가 확인·적용한다(중요 편집 내용 덮어쓰기 방지)."""
from __future__ import annotations

import json
import re

from . import ai_client, channel, plan_check, prompts, storage
from .jp_text import reading_hint
from .util import UserError, new_id, now_iso


def extract_json(text: str) -> dict:
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    else:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            t = t[a:b + 1]
    try:
        obj = json.loads(t)
    except ValueError as e:
        raise UserError("AI 응답을 해석하지 못했습니다(JSON 형식이 아님).",
                        "다시 시도하세요. 반복되면 설정의 최대 출력 토큰을 늘려 보세요.", 422) from e
    if not isinstance(obj, dict):
        raise UserError("AI 응답 형식이 예상과 다릅니다.", "다시 시도하세요.", 422)
    return obj


def _s(v, limit: int = 4000) -> str:
    return str(v if v is not None else "").strip()[:limit]


async def _call(user: str, job) -> dict:
    """모든 AI 요청에 채널 기본 설정을 함께 보낸다(제작자가 정한 방침)."""
    prof = channel.profile_block()
    if prof:
        user = ("## チャンネル設定(制作者が決めた方針。これに従う。韓国語で書かれている)\n"
                + json.dumps(prof, ensure_ascii=False, indent=1) + "\n\n" + user)
    return await ai_client.call(prompts.SYSTEM, user, job=job)


def _usage_entry(task: str, res: dict) -> dict:
    return {"at": now_iso(), "task": task, "model": res.get("model"), "usage": res.get("usage"),
            "latency_s": res.get("latency_s"), "cost": res.get("cost"), "stop_reason": res.get("stop_reason")}


def history_for(project_id: str | None, limit: int = 12) -> list[dict]:
    """다른 프로젝트들의 요약(중복 회피용). 최근 순."""
    out = []
    for meta in storage.list_projects():
        if meta.get("id") == project_id or meta.get("broken"):
            continue
        try:
            p = storage.load_project(meta["id"])
        except UserError:
            continue
        plan = p.get("plan") or {}
        lines = [ln.get("display", "") for sc in (p.get("script") or {}).get("scenes", []) for ln in sc.get("lines", [])]
        if not plan and not lines:
            continue
        out.append({"name": p.get("name"), "topic": (p.get("idea") or {}).get("topic", ""),
                    "approach": plan.get("approach_name_ko", ""), "format": plan.get("format_ko", "")[:200],
                    "payoff": plan.get("payoff_ko", "")[:200], "title": (p.get("publish") or {}).get("title", ""),
                    "script_excerpt": " / ".join(lines)[:500]})
        if len(out) >= limit:
            break
    return out


def script_view(project: dict) -> dict:
    """AI에게 보낼 대본 표현(id 포함)."""
    scenes = []
    for sc in (project.get("script") or {}).get("scenes", []):
        scenes.append({"scene_id": sc["id"], "purpose_ko": sc.get("purpose", ""), "visual_ko": sc.get("visual", ""),
                       "hold_ms": sc.get("hold_ms", 0),
                       "lines": [{"line_id": ln["id"], "display_ja": ln.get("display", ""), "tts_ja": ln.get("tts", ""),
                                  "ko": ln.get("ko", "")} for ln in sc.get("lines", [])]})
    return {"scenes": scenes}


def plan_input(project: dict) -> dict:
    plan = dict(project.get("plan") or {})
    plan.pop("source", None)
    return plan


# ---------------------------------------------------------------- 정규화

def norm_candidate(c: dict, i: int) -> dict:
    ch = c.get("choices") if isinstance(c.get("choices"), dict) else {}
    return {
        "id": _s(c.get("id")) or "ABCDEFG"[i % 7],
        "approach_name_ko": _s(c.get("approach_name_ko")), "format_ko": _s(c.get("format_ko")),
        "content_kind": c.get("content_kind") if c.get("content_kind") in ("entertainment", "factual") else "entertainment",
        "why_watch_ko": _s(c.get("why_watch_ko")), "first_scene_ko": _s(c.get("first_scene_ko")),
        "first_line_ja": _s(c.get("first_line_ja")), "first_line_ko": _s(c.get("first_line_ko")),
        "structure_ko": [_s(x) for x in (c.get("structure_ko") or []) if _s(x)][:20],
        "scene_count": int(c.get("scene_count") or 0) if str(c.get("scene_count") or "0").isdigit() else 0,
        "choices": {"use": bool(ch.get("use")), "count": ch.get("count"), "reason_ko": _s(ch.get("reason_ko"))},
        "payoff_ko": _s(c.get("payoff_ko")), "next_video_pull_ko": _s(c.get("next_video_pull_ko")),
        "visuals_ko": [_s(x) for x in (c.get("visuals_ko") or []) if _s(x)][:15],
        "source_difficulty_ko": _s(c.get("source_difficulty_ko")), "claims_ko": _s(c.get("claims_ko")),
        "risks_ko": _s(c.get("risks_ko")), "overlap_check_ko": _s(c.get("overlap_check_ko")),
        "estimated_seconds": c.get("estimated_seconds"),
        "core_idea_ko": _s(c.get("core_idea_ko"), 300), "content_type": _s(c.get("content_type"), 60),
        "viewer_action": _s(c.get("viewer_action"), 60), "ending_type": _s(c.get("ending_type"), 60),
        "ending_ko": _s(c.get("ending_ko")), "production_load_ko": _s(c.get("production_load_ko")),
        "differs_from_ko": _s(c.get("differs_from_ko")),
    }


def norm_line(d: dict, keep_id: str | None = None) -> dict:
    disp = _s(d.get("display_ja") or d.get("display"), 400)
    tts = _s(d.get("tts_ja") or d.get("tts"), 400) or disp
    note = _s(d.get("reading_note_ko") or d.get("reading_note"), 600)
    return {"id": keep_id or new_id("l"), "display": disp, "tts": tts, "ko": _s(d.get("ko"), 600),
            "reading_note": note, "caution": _s(d.get("caution_ko") or d.get("caution"), 600),
            "reading_hint": reading_hint(disp)}


def norm_scene(d: dict, keep_id: str | None = None) -> dict:
    lines = [norm_line(x) for x in (d.get("lines") or []) if isinstance(x, dict)]
    lines = [ln for ln in lines if ln["display"] or ln["tts"]]
    kw = d.get("search_keywords") or []
    if isinstance(kw, str):
        kw = [k.strip() for k in kw.split(",")]
    hold = d.get("hold_ms") or 0
    try:
        hold = max(0, min(10000, int(hold)))
    except (TypeError, ValueError):
        hold = 0
    return {"id": keep_id or new_id("s"), "purpose": _s(d.get("purpose_ko")), "visual": _s(d.get("visual_ko")),
            "search_keywords": [_s(k, 80) for k in kw if _s(k)][:8], "edit_intent": _s(d.get("edit_intent_ko")),
            "hold_ms": hold, "hold_reason": _s(d.get("hold_reason_ko")), "fact_check": _s(d.get("fact_check_ko")),
            "fact_note": "", "no_source_needed": False, "lines": lines}


# ---------------------------------------------------------------- 작업

def production_of(project: dict) -> dict:
    """프로젝트의 영상 제작 방식(기획·대본·소스 프롬프트에 반영)."""
    prod = project.get("production") or {}
    modes = {sc.get("id"): sc.get("source_mode") for sc in (project.get("script") or {}).get("scenes", []) if sc.get("source_mode")}
    out = {"source_mode": prod.get("source_mode") or "mixed", "label_ko": SOURCE_MODE_LABELS.get(prod.get("source_mode") or "mixed")}
    vis = project.get("visual") or {}
    if (vis.get("style") or {}).get("label_ko"):
        out["visual_style_ko"] = vis["style"]["label_ko"] + " — " + (vis["style"].get("desc_ko") or "")
    if (vis.get("structure") or {}).get("use"):
        st = vis["structure"]
        out["structure_template"] = {"note": "推奨構成(制作者が選択・変更可。実際の長さは音声で決まる。合わなければ変えてよい)",
                                     "choices": st.get("choices"), "segments": st.get("segments")}
    if modes:
        out["scene_overrides"] = {k: SOURCE_MODE_LABELS.get(v, v) for k, v in modes.items()}
    return out


SOURCE_MODE_LABELS = {"flow": "Google Flow로 AI 영상 생성", "stock": "스톡 영상·사진", "upload": "직접 촬영·보유 파일",
                      "image": "이미지·일러스트(정지 화면 + 움직임 효과)", "text": "텍스트·단색 화면", "mixed": "장면마다 다르게(혼합)"}


async def run_plan(project: dict, feedback: str, job) -> dict:
    hist = history_for(project.get("id"))
    prev = (project.get("plan_candidates") or {}).get("items") or []
    user = prompts.plan_prompt(project.get("idea") or {}, hist, feedback, prev if feedback or prev else None,
                               production_of(project))
    res = await _call(user, job)
    obj = extract_json(res["text"])
    cands = [norm_candidate(c, i) for i, c in enumerate(obj.get("candidates") or []) if isinstance(c, dict)]
    if not cands:
        raise UserError("AI가 기획 후보를 돌려주지 않았습니다.", "다시 시도하세요.", 422)
    ids = set()
    for i, c in enumerate(cands):  # id 중복 방지
        if c["id"] in ids:
            c["id"] = "ABCDEFG"[i % 7]
        ids.add(c["id"])
    return {"items": cands, "comparison_ko": _s(obj.get("comparison_ko")), "generated_at": now_iso(),
            "feedback": feedback, "history_used": len(hist), "similarity": plan_check.compare_candidates(cands),
            "log": _usage_entry("기획 제안", res)}


async def run_plan_one(project: dict, target_id: str, instruction: str, job) -> dict:
    """비슷하다고 표시된 후보 1개만 다시 만든다(AI 호출 1회)."""
    pc = project.get("plan_candidates") or {}
    cands = pc.get("items") or []
    target = next((c for c in cands if c.get("id") == target_id), None)
    if not target:
        raise UserError("다시 만들 후보를 찾지 못했습니다.")
    others = [{k: c.get(k) for k in ("id", "core_idea_ko", "content_type", "viewer_action", "ending_type", "first_line_ja",
                                     "structure_ko", "payoff_ko")} for c in cands if c.get("id") != target_id]
    reason = "; ".join((pc.get("similarity") or {}).get("flagged", {}).get(target_id, [])) or "제작자가 이 후보만 다시 만들기를 요청함"
    user = prompts.plan_one_prompt(project.get("idea") or {}, history_for(project.get("id"), limit=6), others, target_id,
                                   reason, instruction, production_of(project))
    res = await _call(user, job)
    obj = extract_json(res["text"])
    c = obj.get("candidate") if isinstance(obj.get("candidate"), dict) else None
    if not c:
        raise UserError("AI가 새 후보를 돌려주지 않았습니다.", "다시 시도하세요.", 422)
    new = norm_candidate(c, 0)
    new["id"] = target_id
    new["regenerated_at"] = now_iso()
    merged = [new if x.get("id") == target_id else x for x in cands]
    return {"candidate": new, "similarity": plan_check.compare_candidates(merged), "log": _usage_entry("기획 후보 1개 다시", res)}


async def run_script(project: dict, notes: str, job) -> dict:
    if not project.get("plan"):
        raise UserError("먼저 기획 방향을 선택하세요.")
    hist = history_for(project.get("id"))
    user = prompts.script_prompt(project.get("idea") or {}, plan_input(project), hist, notes, production_of(project))
    res = await _call(user, job)
    obj = extract_json(res["text"])
    scenes = [norm_scene(s) for s in (obj.get("scenes") or []) if isinstance(s, dict)]
    scenes = [s for s in scenes if s["lines"]]
    if not scenes:
        raise UserError("AI가 대본 장면을 돌려주지 않았습니다.", "다시 시도하세요.", 422)
    return {"scenes": scenes,
            "title_candidates": [{"ja": _s(t.get("ja")), "ko": _s(t.get("ko"))} for t in (obj.get("title_candidates") or [])
                                 if isinstance(t, dict)][:6],
            "content_kind": obj.get("content_kind"),
            "claims_level_ko": _s(obj.get("claims_level_ko")),
            "self_check_ko": [_s(x) for x in (obj.get("self_check_ko") or [])][:12],
            "generated_at": now_iso(), "plan_hash": storage.plan_hash(project), "log": _usage_entry("대본 생성", res)}


async def run_partial(project: dict, scope: dict, preset: str, instruction: str, job) -> dict:
    sv = script_view(project)
    if not sv["scenes"]:
        raise UserError("다시 만들 대본이 없습니다.")
    text = prompts.PARTIAL_PRESETS.get(preset, "")
    if instruction.strip():
        text = (text + "\n추가 지시: " + instruction.strip()).strip()
    if not text:
        raise UserError("무엇을 고칠지 선택하거나 입력하세요.")
    line_ids = {ln["line_id"] for s in sv["scenes"] for ln in s["lines"]}
    scene_ids = {s["scene_id"] for s in sv["scenes"]}
    hist = history_for(project.get("id")) if preset == "overlap" else history_for(project.get("id"), limit=5)
    user = prompts.partial_prompt(project.get("idea") or {}, plan_input(project), sv, scope, text, hist)
    res = await _call(user, job)
    obj = extract_json(res["text"])
    allowed_lines = set(scope.get("line_ids") or [])
    allowed_scenes = set(scope.get("scene_ids") or [])
    is_all = scope.get("all") is True
    # 장면 안의 줄은 장면 범위에 포함
    for s in sv["scenes"]:
        if s["scene_id"] in allowed_scenes:
            allowed_lines |= {ln["line_id"] for ln in s["lines"]}
    changes, rejected = [], []
    for ch in obj.get("changes") or []:
        if not isinstance(ch, dict):
            continue
        op = ch.get("op")
        if op == "replace_line" and ch.get("line_id") in line_ids:
            if is_all or ch["line_id"] in allowed_lines:
                changes.append({"op": op, "line_id": ch["line_id"], "line": norm_line(ch, keep_id=ch["line_id"])})
            else:
                rejected.append(ch.get("line_id"))
        elif op == "replace_scene" and ch.get("scene_id") in scene_ids and isinstance(ch.get("scene"), dict):
            if is_all or ch["scene_id"] in allowed_scenes:
                changes.append({"op": op, "scene_id": ch["scene_id"], "scene": norm_scene(ch["scene"], keep_id=ch["scene_id"])})
            else:
                rejected.append(ch.get("scene_id"))
        elif op == "insert_scene_after" and is_all and isinstance(ch.get("scene"), dict):
            after = ch.get("after_scene_id")
            changes.append({"op": op, "after_scene_id": after if after in scene_ids else None,
                            "scene": norm_scene(ch["scene"])})
        elif op == "delete_scene" and is_all and ch.get("scene_id") in scene_ids:
            changes.append({"op": op, "scene_id": ch["scene_id"]})
        else:
            rejected.append(str(ch.get("line_id") or ch.get("scene_id") or op))
    if not changes:
        raise UserError("AI가 지정한 범위 안에서 바꾼 내용이 없습니다.", "범위나 지시를 바꿔 다시 시도하세요.", 422)
    return {"changes": changes, "explanation_ko": _s(obj.get("explanation_ko")), "rejected_out_of_scope": rejected,
            "log": _usage_entry("부분 재생성", res)}


async def run_review(project: dict, job) -> dict:
    sv = script_view(project)
    if not sv["scenes"]:
        raise UserError("검토할 대본이 없습니다.")
    pub = {k: (project.get("publish") or {}).get(k, "") for k in ("title", "description")}
    res = await _call(prompts.review_prompt(project.get("idea") or {}, sv, pub), job)
    obj = extract_json(res["text"])
    items = []
    for it in obj.get("items") or []:
        if not isinstance(it, dict):
            continue
        items.append({"line_id": it.get("line_id"), "severity": it.get("severity") if it.get("severity") in ("high", "mid", "low") else "mid",
                      "category_ko": _s(it.get("category_ko")), "issue_ko": _s(it.get("issue_ko")),
                      "suggestion_display_ja": _s(it.get("suggestion_display_ja")),
                      "suggestion_tts_ja": _s(it.get("suggestion_tts_ja")), "suggestion_ko": _s(it.get("suggestion_ko"))})
    return {"items": items, "consistency_ko": _s(obj.get("consistency_ko")), "overall_ko": _s(obj.get("overall_ko")),
            "reviewed_at": now_iso(), "label": "AI 검토(원어민 검수 아님)", "log": _usage_entry("대본 검토", res)}


async def run_publish(project: dict, job) -> dict:
    sv = script_view(project)
    if not sv["scenes"]:
        raise UserError("대본이 있어야 게시 정보를 만들 수 있습니다.")
    platforms = (project.get("idea") or {}).get("platforms") or ["YouTube Shorts", "Instagram Reels"]
    res = await _call(prompts.publish_prompt(project.get("idea") or {}, plan_input(project), sv, platforms), job)
    obj = extract_json(res["text"])
    return {"titles": [{"ja": _s(t.get("ja")), "ko": _s(t.get("ko")), "note_ko": _s(t.get("note_ko"))}
                       for t in (obj.get("titles") or []) if isinstance(t, dict)][:6],
            "description_ja": _s(obj.get("description_ja")), "description_ko": _s(obj.get("description_ko")),
            "hashtags": [_s(h, 60) for h in (obj.get("hashtags") or [])][:8], "notes_ko": _s(obj.get("notes_ko")),
            "generated_at": now_iso(), "log": _usage_entry("게시 정보", res)}


def fixed_context(project: dict) -> dict:
    """AI에게 '참조용'으로만 보내는 고정 정보(AI 출력은 이 값을 바꾸지 못한다)."""
    from . import visual as V

    st = (project.get("visual") or {}).get("style") or {}
    style_text, _ = V.style_block(project) if st.get("style_en") else ("", "")
    return {"style_locked_text": style_text,
            "elements": [{"id": e["id"], "name": e.get("name"), "type": e.get("type"), "desc_ko": e.get("desc_ko", ""),
                          "fixed_en_readonly": e.get("fixed_en", ""), "confirmed": bool(e.get("locked"))}
                         for e in V.elements(project)],
            "structure": (project.get("visual") or {}).get("structure") or {}}


async def run_flow_prompts(project: dict, clip_ids: list[str] | None, instruction: str, job) -> dict:
    from . import visual as V
    from .presets import CLIP_ROLES

    fl = project.get("flow") or {}
    clips = fl.get("clips") or []
    if not clips:
        raise UserError("먼저 클립 계획을 만드세요.", "영상 단계에서 [클립 계획 만들기]를 누르세요.")
    lines = {ln["id"]: ln for sc in (project.get("script") or {}).get("scenes", []) for ln in sc.get("lines", [])}
    scenes = [{"scene_id": sc["id"], "purpose_ko": sc.get("purpose", ""), "visual_ko": sc.get("visual", ""),
               "edit_intent_ko": sc.get("edit_intent", ""), "role": sc.get("role", ""),
               "lines": [{"ja": ln.get("display", ""), "ko": ln.get("ko", "")} for ln in sc.get("lines", [])]}
              for sc in (project.get("script") or {}).get("scenes", [])]
    clip_view = [{"clip_id": c["id"], "scene_id": c["scene_id"], "duration_s": c["duration"],
                  "covers_narration_s": [c["start"], c["end"]], "role": c.get("role", ""),
                  "element_ids_now": c.get("element_ids") or [], "gen_mode_now": c.get("gen_mode", ""),
                  "narration_ja": [lines[l].get("display", "") for l in c["line_ids"] if l in lines],
                  "narration_ko": [lines[l].get("ko", "") for l in c["line_ids"] if l in lines],
                  "current_var_en": c.get("var_en", "")} for c in clips]
    valid = {c["id"] for c in clips}
    only = [i for i in (clip_ids or []) if i in valid] or None
    fixed = fixed_context(project)
    user = prompts.flow_prompt(project.get("idea") or {}, plan_input(project), scenes, clip_view,
                               fl.get("style"), instruction, only, fixed)
    res = await _call(user, job)
    obj = extract_json(res["text"])
    style = None
    if not fixed["style_locked_text"]:  # 화풍이 확정된 프로젝트에서는 AI 스타일 제안을 받지 않는다
        sb = obj.get("style_bible") if isinstance(obj.get("style_bible"), dict) else {}
        style = {"look_en": _s(sb.get("look_en")), "look_ko": _s(sb.get("look_ko")),
                 "recurring_ko": [_s(x) for x in (sb.get("recurring_ko") or [])][:10], "palette_ko": _s(sb.get("palette_ko")),
                 "avoid_en": _s(sb.get("avoid_en"))}
    el_ids = {e["id"] for e in V.elements(project)}
    out = []
    targets = set(only) if only else valid
    for c in obj.get("clips") or []:
        if not isinstance(c, dict) or c.get("clip_id") not in targets:
            continue
        mode = c.get("gen_mode") or c.get("mode")
        mode = mode if mode in V.GEN_MODES else "text"
        var_en = _s(c.get("var_en") or c.get("prompt_en"), 3000)
        out.append({"clip_id": c["clip_id"], "var_en": var_en, "var_ko": _s(c.get("var_ko") or c.get("prompt_ko"), 3000),
                    "beats_ko": _s(c.get("beats_ko"), 1000), "gen_mode": mode,
                    "element_ids": [e for e in (c.get("element_ids") or []) if e in el_ids],  # 정의된 요소만
                    "role": c.get("role") if c.get("role") in CLIP_ROLES else "",
                    "continues_previous": c.get("continues_previous") is True,
                    "intended_change_en": _s(c.get("intended_change_en"), 500),
                    "intended_change_ko": _s(c.get("intended_change_ko"), 500),
                    "mode_note_ko": _s(c.get("mode_note_ko")), "risk_ko": _s(c.get("risk_ko"))})
    if not out:
        raise UserError("AI가 클립 프롬프트를 돌려주지 않았습니다.", "다시 시도하세요.", 422)
    return {"style": style, "clips": out, "generated_at": now_iso(), "log": _usage_entry("Flow 프롬프트", res)}


async def run_elements(project: dict, instruction: str, job) -> dict:
    """대본에서 반복 등장 요소 초안 추출. 확정 요소는 바꾸지 않고, 결과는 화면에서 사용자가 골라 적용한다."""
    from . import visual as V

    sv = script_view(project)
    if not sv["scenes"]:
        raise UserError("대본이 있어야 등장 요소를 추출할 수 있습니다.")
    existing = [{"id": e["id"], "name": e.get("name"), "type": e.get("type"), "confirmed": bool(e.get("locked")),
                 "fixed_en": e.get("fixed_en", "")} for e in V.elements(project)]
    style_text, _ = V.style_block(project)
    res = await _call(prompts.elements_prompt(project.get("idea") or {}, plan_input(project), sv, existing, style_text,
                                              instruction), job)
    obj = extract_json(res["text"])
    scene_ids = {s["scene_id"] for s in sv["scenes"]}
    props = []
    for d in obj.get("elements") or []:
        if not isinstance(d, dict) or not _s(d.get("name")):
            continue
        typ = d.get("type") if d.get("type") in V.ELEMENT_TYPES else "object"
        keys = [k for k, _ in V.FEATURE_KEYS[typ]]
        feats = d.get("features") if isinstance(d.get("features"), dict) else {}
        props.append({"id": new_id("el"), "name": _s(d.get("name"), 60), "type": typ, "desc_ko": _s(d.get("desc_ko"), 600),
                      "fixed_en": _s(d.get("fixed_en"), 1200), "features": {k: _s(feats.get(k), 300) for k in keys},
                      "must_keep": [_s(x, 120) for x in (d.get("must_keep_en") or []) if _s(x)][:10],
                      "scene_ids": [x for x in (d.get("appears_in_scene_ids") or []) if x in scene_ids],
                      "reason_ko": _s(d.get("reason_ko"), 400), "source": "ai"})
    if not props:
        raise UserError("AI가 등장 요소를 돌려주지 않았습니다.", "다시 시도하세요.", 422)
    merged = V.merge_element_proposals(V.elements(project), props)
    return {**merged, "notes_ko": _s(obj.get("notes_ko")), "generated_at": now_iso(), "log": _usage_entry("등장 요소 추출", res)}
