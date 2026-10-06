"""AI 작업: 요청 → JSON 해석 → 검증·정규화. 결과는 프로젝트에 바로 쓰지 않고
화면에서 사용자가 확인·적용한다(중요 편집 내용 덮어쓰기 방지)."""
from __future__ import annotations

import json
import re

from . import ai_client, channel, prompts, storage
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

async def run_plan(project: dict, feedback: str, job) -> dict:
    hist = history_for(project.get("id"))
    prev = (project.get("plan_candidates") or {}).get("items") or []
    user = prompts.plan_prompt(project.get("idea") or {}, hist, feedback, prev if feedback or prev else None)
    res = await _call(user, job)
    obj = extract_json(res["text"])
    cands = [norm_candidate(c, i) for i, c in enumerate(obj.get("candidates") or []) if isinstance(c, dict)]
    if not cands:
        raise UserError("AI가 기획 후보를 돌려주지 않았습니다.", "다시 시도하세요.", 422)
    return {"items": cands, "comparison_ko": _s(obj.get("comparison_ko")), "generated_at": now_iso(),
            "feedback": feedback, "history_used": len(hist), "log": _usage_entry("기획 제안", res)}


async def run_script(project: dict, notes: str, job) -> dict:
    if not project.get("plan"):
        raise UserError("먼저 기획 방향을 선택하세요.")
    hist = history_for(project.get("id"))
    user = prompts.script_prompt(project.get("idea") or {}, plan_input(project), hist, notes)
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
            "generated_at": now_iso(), "log": _usage_entry("대본 생성", res)}


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


async def run_flow_prompts(project: dict, clip_ids: list[str] | None, instruction: str, job) -> dict:
    fl = project.get("flow") or {}
    clips = fl.get("clips") or []
    if not clips:
        raise UserError("먼저 클립 계획을 만드세요.", "영상 단계에서 [클립 계획 만들기]를 누르세요.")
    lines = {ln["id"]: ln for sc in (project.get("script") or {}).get("scenes", []) for ln in sc.get("lines", [])}
    scenes = [{"scene_id": sc["id"], "purpose_ko": sc.get("purpose", ""), "visual_ko": sc.get("visual", ""),
               "edit_intent_ko": sc.get("edit_intent", ""),
               "lines": [{"ja": ln.get("display", ""), "ko": ln.get("ko", "")} for ln in sc.get("lines", [])]}
              for sc in (project.get("script") or {}).get("scenes", [])]
    clip_view = [{"clip_id": c["id"], "scene_id": c["scene_id"], "duration_s": c["duration"],
                  "covers_narration_s": [c["start"], c["end"]],
                  "narration_ja": [lines[l].get("display", "") for l in c["line_ids"] if l in lines],
                  "narration_ko": [lines[l].get("ko", "") for l in c["line_ids"] if l in lines],
                  "current_prompt_en": c.get("prompt_en", "")} for c in clips]
    valid = {c["id"] for c in clips}
    only = [i for i in (clip_ids or []) if i in valid] or None
    user = prompts.flow_prompt(project.get("idea") or {}, plan_input(project), scenes, clip_view,
                               fl.get("style"), instruction, only)
    res = await _call(user, job)
    obj = extract_json(res["text"])
    sb = obj.get("style_bible") if isinstance(obj.get("style_bible"), dict) else {}
    style = {"look_en": _s(sb.get("look_en")), "look_ko": _s(sb.get("look_ko")),
             "recurring_ko": [_s(x) for x in (sb.get("recurring_ko") or [])][:10], "palette_ko": _s(sb.get("palette_ko")),
             "avoid_en": _s(sb.get("avoid_en"))}
    out = []
    targets = set(only) if only else valid
    for c in obj.get("clips") or []:
        if not isinstance(c, dict) or c.get("clip_id") not in targets:
            continue
        mode = c.get("mode") if c.get("mode") in ("text", "first_frame", "ingredients") else "text"
        out.append({"clip_id": c["clip_id"], "prompt_en": _s(c.get("prompt_en"), 3000), "prompt_ko": _s(c.get("prompt_ko"), 3000),
                    "beats_ko": _s(c.get("beats_ko"), 1000), "mode": mode, "mode_note_ko": _s(c.get("mode_note_ko")),
                    "risk_ko": _s(c.get("risk_ko"))})
    if not out:
        raise UserError("AI가 클립 프롬프트를 돌려주지 않았습니다.", "다시 시도하세요.", 422)
    return {"style": style, "clips": out, "generated_at": now_iso(), "log": _usage_entry("Flow 프롬프트", res)}
