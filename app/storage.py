"""프로젝트 저장소: 생성·저장(리비전 충돌 방지)·자동 백업·복제·상태 계산."""
from __future__ import annotations

import json
import shutil
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from . import config
from .util import (UserError, atomic_write_text, check_project_id, inside, new_project_id,
                   now_iso, stable_hash)

SCHEMA = 2
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
BACKUP_INTERVAL_S = 180
BACKUP_KEEP = 30


def _lock(pid: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(pid, threading.Lock())


def project_dir(pid: str) -> Path:
    check_project_id(pid)
    return inside(config.PROJECTS_DIR, config.PROJECTS_DIR / pid)


def project_file(pid: str) -> Path:
    return project_dir(pid) / "project.json"


def media_path(pid: str, rel: str) -> Path:
    """프로젝트 폴더 기준 상대경로를 안전하게 절대경로로."""
    if not rel or not isinstance(rel, str):
        raise UserError("파일 경로가 비어 있습니다.")
    base = project_dir(pid)
    return inside(base, base / rel)


def empty_project(name: str) -> dict:
    t = now_iso()
    return {
        "schema": SCHEMA,
        "id": "",
        "name": name or "새 프로젝트",
        "created_at": t,
        "updated_at": t,
        "rev": 0,
        "idea": {"topic": "", "audience": "", "notes": "", "content_kind": "undecided",
                 "target_seconds": 35, "avoid": "", "platforms": ["YouTube Shorts", "Instagram Reels"]},
        "plan_candidates": {"items": [], "comparison_ko": "", "generated_at": None, "feedback": ""},
        "plan": None,
        "script": {"scenes": [], "title_candidates": [], "claims_level_ko": "", "self_check_ko": [],
                   "generated_at": None, "updated_at": None, "history": [], "review": None},
        "publish": {"title": "", "description": "", "hashtags": "", "credits_extra": "",
                    "ai_draft": None, "entertainment_notice": True},
        "typecast": {"voice": "", "credit_text": "", "settings_memo": "", "notes": ""},
        "audio": {"original": None, "originals_history": [],
                  "silence": {"mode": "natural", "params": {}, "protect_ranges": [], "custom_ranges": []},
                  "processed": None, "processed_history": []},
        "subtitles": {"cues": [], "based_on_audio_version": None, "based_on_display_hash": None,
                      "method": None, "generated_at": None, "previous": []},
        "sources": {"items": []},
        "ai_log": [],
    }


def list_projects() -> list[dict]:
    config.ensure_dirs()
    out = []
    for d in sorted(config.PROJECTS_DIR.iterdir(), reverse=True):
        f = d / "project.json"
        if not d.is_dir() or not f.exists():
            continue
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out.append({"id": d.name, "name": "(읽을 수 없는 프로젝트)", "broken": True})
            continue
        st = compute_status(p)
        out.append({"id": p.get("id"), "name": p.get("name"), "updated_at": p.get("updated_at"),
                    "created_at": p.get("created_at"), "topic": (p.get("idea") or {}).get("topic", ""),
                    "next": st["next"], "steps": {k: v["state"] for k, v in st["steps"].items()}})
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return out


def create_project(name: str, data: dict | None = None) -> dict:
    config.ensure_dirs()
    pid = new_project_id()
    d = config.PROJECTS_DIR / pid
    (d / "media" / "audio").mkdir(parents=True)
    (d / "media" / "sources").mkdir(parents=True)
    (d / "backups").mkdir()
    (d / "exports").mkdir()
    p = data if data is not None else empty_project(name)
    p["id"] = pid
    p["rev"] = 1
    p["updated_at"] = now_iso()
    atomic_write_text(d / "project.json", json.dumps(p, ensure_ascii=False, indent=1))
    return p


def load_project(pid: str) -> dict:
    f = project_file(pid)
    if not f.exists():
        raise UserError("프로젝트를 찾을 수 없습니다.", status=404)
    try:
        p = json.loads(f.read_text(encoding="utf-8"))
    except ValueError as e:
        raise UserError("프로젝트 파일이 손상되었습니다.",
                        "프로젝트 폴더의 backups 안에 있는 최근 백업으로 복원하세요.") from e
    if int(p.get("schema") or 1) < SCHEMA:
        with _lock(pid):
            raw = f.read_text(encoding="utf-8")
            p = json.loads(raw)
            if int(p.get("schema") or 1) < SCHEMA:
                # 변환 전 원본을 그대로 백업(내용 손실 없이 되돌릴 수 있게)
                bdir = _backup_dir(pid)
                atomic_write_text(bdir / f"project_{datetime.now().strftime('%Y%m%d_%H%M%S')}_before-migration-v{SCHEMA}.json", raw)
                p = migrate(p)
                atomic_write_text(f, json.dumps(p, ensure_ascii=False, indent=1))
    return p


def migrate(p: dict) -> dict:
    """이전 버전 프로젝트를 현재 구조로 변환. 기존 내용은 바꾸지 않고 빠진 기준 정보만 채운다."""
    from .subs_merge import ensure_src, line_texts

    p = json.loads(json.dumps(p))
    script = p.setdefault("script", {})
    # 대본이 어떤 기획을 바탕으로 했는지 기록이 없으면, 지금 기획을 기준으로 본다(이후 기획 변경부터 감지)
    if script.get("scenes") and p.get("plan") and not script.get("based_on_plan_hash"):
        script["based_on_plan_hash"] = plan_hash(p)
    # 예전 프로젝트에 Flow 클립이 있으면 제작 방식을 Flow로 기록(기존 동작 유지)
    if (p.get("flow") or {}).get("clips") and not (p.get("production") or {}).get("source_mode"):
        p["production"] = {"source_mode": "flow"}
    subs = p.setdefault("subtitles", {})
    subs.setdefault("removed", [])
    cues = subs.get("cues") or []
    if cues:
        texts = line_texts(p)
        consistent = subs.get("based_on_display_hash") in (None, display_hash(p))
        ensure_src(cues, texts)
        if consistent and not subs.get("line_texts"):
            subs["line_texts"] = texts
    p["schema"] = SCHEMA
    p.setdefault("migrations", []).append({"to": SCHEMA, "at": now_iso()})
    return p


def _assert_no_secrets(text: str) -> None:
    for s in config.all_secret_values():
        if s and s in text:
            raise UserError("프로젝트에 API 키와 같은 문자열이 포함되어 저장을 막았습니다.",
                            "메모 칸 등에 키를 붙여 넣지 않았는지 확인하세요.")


def save_project(pid: str, project: dict, base_rev: int | None, force: bool = False) -> dict:
    """base_rev가 서버 rev와 다르면 409(다른 창/작업이 먼저 저장). force면 덮어쓴다(백업 후)."""
    with _lock(pid):
        cur = load_project(pid)
        if not force and base_rev is not None and int(base_rev) != int(cur.get("rev", 0)):
            raise UserError("다른 창이나 작업에서 이 프로젝트가 먼저 저장되었습니다.",
                            "최신 내용을 다시 불러오거나, 현재 화면 내용으로 덮어쓰기를 선택하세요.", status=409)
        if force:
            _write_backup(pid, cur, tag="before-overwrite")
        project = dict(project)
        project["id"] = pid
        project["schema"] = SCHEMA
        project["rev"] = int(cur.get("rev", 0)) + 1
        project["updated_at"] = now_iso()
        project["created_at"] = cur.get("created_at", project.get("created_at"))
        text = json.dumps(project, ensure_ascii=False, indent=1)
        _assert_no_secrets(text)
        atomic_write_text(project_file(pid), text)
        _maybe_periodic_backup(pid, cur)
        return project


def _backup_dir(pid: str) -> Path:
    d = project_dir(pid) / "backups"
    d.mkdir(exist_ok=True)
    return d


def _write_backup(pid: str, project: dict, tag: str = "auto") -> Path:
    d = _backup_dir(pid)
    name = f"project_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{tag}.json"
    f = d / name
    atomic_write_text(f, json.dumps(project, ensure_ascii=False, indent=1))
    backups = sorted(d.glob("project_*_auto.json"))
    for old in backups[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)
    return f


def _maybe_periodic_backup(pid: str, previous: dict) -> None:
    d = _backup_dir(pid)
    autos = sorted(d.glob("project_*_auto.json"))
    if autos and time.time() - autos[-1].stat().st_mtime < BACKUP_INTERVAL_S:
        return
    _write_backup(pid, previous, tag="auto")


def list_backups(pid: str) -> list[dict]:
    d = _backup_dir(pid)
    out = []
    for f in sorted(d.iterdir(), reverse=True):
        if f.suffix in (".json", ".zip"):
            out.append({"name": f.name, "size": f.stat().st_size,
                        "time": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")})
    return out


def restore_backup(pid: str, name: str) -> dict:
    d = _backup_dir(pid)
    f = inside(d, d / name)
    if f.suffix != ".json" or not f.exists():
        raise UserError("복원할 수 있는 백업이 아닙니다.")
    data = json.loads(f.read_text(encoding="utf-8"))
    cur = load_project(pid)
    _write_backup(pid, cur, tag="before-restore")
    return save_project(pid, data, None, force=True)


def full_backup_zip(pid: str) -> Path:
    """프로젝트 폴더 전체(내보내기·백업 제외)를 ZIP으로 백업."""
    base = project_dir(pid)
    out = _backup_dir(pid) / f"full_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in base.rglob("*"):
            rel = f.relative_to(base)
            if not f.is_file() or rel.parts[0] in ("backups", "exports"):
                continue
            z.write(f, rel.as_posix())
    return out


def duplicate_project(pid: str, mode: str = "all") -> dict:
    """mode=all: 전체 복제 / mode=plan: 기획·대본만 복제(음성·자막·소스 제외)."""
    src = load_project(pid)
    copy = json.loads(json.dumps(src))
    copy["name"] = f"{src.get('name', '')} (복제)"
    copy["created_at"] = now_iso()
    copy["ai_log"] = []
    if mode == "plan":
        blank = empty_project("")
        for k in ("audio", "subtitles", "sources", "typecast"):
            copy[k] = blank[k]
    new = create_project(copy["name"], copy)
    if mode == "plan" and (project_dir(pid) / "media" / "visual").exists():  # 화풍·등장 요소의 기준 이미지는 함께
        shutil.copytree(project_dir(pid) / "media" / "visual", project_dir(new["id"]) / "media" / "visual", dirs_exist_ok=True)
    if mode == "all":
        sdir, ddir = project_dir(pid) / "media", project_dir(new["id"]) / "media"
        shutil.copytree(sdir, ddir, dirs_exist_ok=True)
    return new


def delete_project_to_trash(pid: str) -> None:
    d = project_dir(pid)
    trash = config.DATA_DIR / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    shutil.move(str(d), str(trash / pid))


# ---------------------------------------------------------------- 상태 계산

def all_lines(project: dict) -> list[dict]:
    return [ln for sc in (project.get("script") or {}).get("scenes", []) for ln in sc.get("lines", [])]


def tts_text(project: dict, scene_ids: list[str] | None = None) -> str:
    """타입캐스트 낭독문: TTS 일본어만, 장면 번호·지시·번역 없음."""
    parts = []
    for sc in (project.get("script") or {}).get("scenes", []):
        if scene_ids and sc.get("id") not in scene_ids:
            continue
        for ln in sc.get("lines", []):
            t = (ln.get("tts") or "").strip()
            if t:
                parts.append(t)
    return "\n".join(parts)


def tts_hash(project: dict) -> str:
    return stable_hash([ln.get("tts", "").strip() for ln in all_lines(project) if ln.get("tts", "").strip()])


def display_hash(project: dict) -> str:
    return stable_hash([[ln.get("id"), ln.get("display", ""), ln.get("tts", "")] for ln in all_lines(project)])


STEP_LABELS = {"plan": "기획", "script": "대본", "audio": "음성", "subtitles": "자막", "visual": "화풍·등장요소",
               "sources": "영상소스", "review": "검수", "export": "내보내기"}

# ---------------------------------------------------------------- 단계별 기반 버전(무엇이 바뀌면 어떤 단계가 최신이 아닌지)
#  기획 내용        → 대본(based_on_plan_hash)
#  TTS 낭독 문구    → 원본 음성(script_tts_hash)  ※ 한국어 의미·메모·화면 표시만 바꾸면 음성은 그대로 유효
#  원본 음성        → 처리 음성(source_sha)
#  처리 음성·대본 문구 → 자막(based_on_audio_version, based_on_display_hash)
#  자막 시간        → Flow 클립 계획(based_on)
#  위 모든 결과물   → 내보내기(last_export.basis)

PLAN_META_KEYS = ("updated_at", "source", "selected_at")
# 내보내기 결과물(배치표·일관성 자료)에 들어가는 클립 필드
VISUAL_CLIP_KEYS = ("element_ids", "gen_mode", "ref_asset_ids", "start_frame", "end_frame", "use_start", "edit", "reuse_of",
                    "still_asset_id", "review", "changes", "role")


def plan_hash(project: dict) -> str | None:
    plan = project.get("plan")
    if not plan:
        return None
    return stable_hash({k: v for k, v in plan.items() if k not in PLAN_META_KEYS})


def audio_mismatch(p: dict) -> dict:
    """원본 음성이 현재 TTS 낭독문과 다른지(음성을 넣은 뒤 낭독문이 바뀜), 사용자가 예외로 확인했는지."""
    orig = (p.get("audio") or {}).get("original") or {}
    cur = tts_hash(p)
    mismatch = bool(orig) and bool(orig.get("script_tts_hash")) and orig.get("script_tts_hash") != cur
    ack = (p.get("audio") or {}).get("mismatch_ack") or {}
    acknowledged = mismatch and ack.get("tts_hash") == cur and ack.get("audio_sha") == orig.get("sha256") and bool(ack.get("reason"))
    return {"mismatch": mismatch, "acknowledged": acknowledged, "ack": ack if acknowledged else {}}


def export_basis(p: dict) -> str:
    """내보내기 결과물에 영향을 주는 내용의 지문. 내보낸 뒤 이것이 바뀌면 '다시 내보내기 필요'."""
    audio = p.get("audio") or {}
    subs = p.get("subtitles") or {}
    pub = p.get("publish") or {}
    return stable_hash({
        "script": [[ln.get("id"), ln.get("display"), ln.get("tts"), ln.get("ko")] for ln in all_lines(p)],
        "orig": (audio.get("original") or {}).get("sha256"),
        "proc": (audio.get("processed") or {}).get("sha256") or (audio.get("processed") or {}).get("file"),
        "cues": [[c.get("text"), c.get("start"), c.get("end")] for c in subs.get("cues") or []],
        "sources": [[i.get("id"), i.get("file"), i.get("scene_id"), i.get("credit_text")] for i in (p.get("sources") or {}).get("items", [])
                    if i.get("status") == "acquired"],
        "clips": [[c.get("id"), c.get("item_id"), c.get("start"), c.get("end"), c.get("duration"), c.get("prompt_en")]
                  + ([c.get(k) for k in VISUAL_CLIP_KEYS] if any(c.get(k) for k in VISUAL_CLIP_KEYS) else [])
                  for c in (p.get("flow") or {}).get("clips") or []],
        **({"visual": p["visual"]} if p.get("visual") else {}),
        "publish": [pub.get("title"), pub.get("description"), pub.get("hashtags"), pub.get("credits_extra")],
        "typecast": (p.get("typecast") or {}).get("credit_text"),
    })


def compute_status(p: dict) -> dict:
    steps: dict[str, dict] = {}
    plan = p.get("plan")
    steps["plan"] = {"state": "done" if plan else "todo",
                     "notes": [] if plan else ["기획 방향을 선택하세요."]}
    lines = all_lines(p)
    script = p.get("script") or {}
    script_ok = bool(lines) and all((ln.get("tts") or "").strip() and (ln.get("display") or "").strip() for ln in lines)
    s_notes = []
    if not lines:
        s_notes.append("대본이 아직 없습니다.")
    elif not script_ok:
        s_notes.append("화면 표시 또는 TTS 일본어가 비어 있는 줄이 있습니다.")
    s_state = "done" if script_ok else "todo"
    if lines and plan and script.get("based_on_plan_hash") and script.get("based_on_plan_hash") != plan_hash(p):
        s_state = "stale" if script_ok else s_state
        s_notes.insert(0, "대본을 만든 뒤 기획 내용이 바뀌었습니다. 필요한 부분을 다시 만들거나, 지금 대본을 유지(확인)하세요.")
    steps["script"] = {"state": s_state, "notes": s_notes}

    audio = p.get("audio") or {}
    orig = audio.get("original")
    a_state, a_notes = "todo", []
    mm = audio_mismatch(p)
    if orig:
        a_state = "done"
        if mm["mismatch"] and not mm["acknowledged"]:
            a_state = "stale"
            a_notes.append("음성을 넣은 뒤 TTS 낭독문이 바뀌었습니다. 타입캐스트에서 다시 생성하거나, 이 음성을 그대로 쓸 이유를 기록하세요.")
        elif mm["acknowledged"]:
            a_notes.append(f"대본과 다른 음성을 사용자가 확인하고 사용 중: {mm['ack'].get('reason', '')}")
        proc = audio.get("processed")
        if not proc:
            a_notes.append("무음 정리를 아직 적용하지 않았습니다(원본을 그대로 쓰려면 '원본 그대로 사용'을 누르세요).")
            if a_state == "done":
                a_state = "todo"
        elif proc.get("source_sha") != orig.get("sha256"):
            a_state = "stale"
            a_notes.append("원본 음성이 바뀌어 무음 정리를 다시 적용해야 합니다.")
    else:
        a_notes.append("타입캐스트에서 만든 음성 파일을 넣으세요.")
    steps["audio"] = {"state": a_state, "notes": a_notes}

    subs = p.get("subtitles") or {}
    proc = audio.get("processed")
    sub_state, sub_notes = "todo", []
    if subs.get("cues"):
        sub_state = "done"
        if not proc or subs.get("based_on_audio_version") != proc.get("version"):
            sub_state = "stale"
            sub_notes.append("최종 음성이 바뀌었습니다. 자막 시간을 다시 맞추세요.")
        if subs.get("based_on_display_hash") and subs.get("based_on_display_hash") != display_hash(p):
            sub_state = "stale"
            sub_notes.append("대본(화면 표시/낭독)이 자막 생성 후 바뀌었습니다.")
    else:
        sub_notes.append("최종 음성 기준으로 자막을 생성하세요.")
    steps["subtitles"] = {"state": sub_state, "notes": sub_notes}

    scenes = (p.get("script") or {}).get("scenes", [])
    items = (p.get("sources") or {}).get("items", [])
    acquired_items = {it.get("id"): it for it in items if it.get("status") == "acquired"}
    acquired = {it.get("scene_id") for it in acquired_items.values()}
    fl = p.get("flow") or {}
    clips = fl.get("clips") or []
    clip_scenes = {c.get("scene_id") for c in clips}

    from .flow import scene_mode
    from .visual import resolve_media

    def _has_media(c):  # 업로드한 영상, 확정 이미지(정지 화면), 다른 클립 소재 재사용 모두 인정
        return resolve_media(p, c) is not None

    def scene_ok(sc):
        mode = scene_mode(p, sc)
        if sc.get("no_source_needed") or mode == "text":
            return True
        if mode == "flow":
            mine = [c for c in clips if c.get("scene_id") == sc.get("id")]
            return bool(mine) and all(_has_media(c) for c in mine)
        return sc.get("id") in acquired

    missing = [i + 1 for i, sc in enumerate(scenes) if not scene_ok(sc)]
    from .flow import timing_hash
    flow_stale = bool(clips) and fl.get("based_on") != timing_hash(p)
    src_state = "todo" if (not scenes or missing) else ("stale" if (sub_state == "stale" or flow_stale) else "done")
    src_notes = [f"영상을 확보하지 않은 장면: {', '.join(map(str, missing))}"] if missing else []
    if clips:
        no_prompt = sum(1 for c in clips if not c.get("prompt_en"))
        no_file = sum(1 for c in clips if not _has_media(c))
        if no_prompt:
            src_notes.append(f"Flow 프롬프트가 없는 클립 {no_prompt}개")
        if no_file:
            src_notes.append(f"생성한 영상을 아직 넣지 않은 클립 {no_file}개")
    if flow_stale:
        src_notes.insert(0, "자막(또는 대본) 시간이 바뀌어 클립 계획을 다시 계산해야 합니다.")
    if sub_state == "stale" and scenes:
        src_notes.append("자막이 최신이 아니라 장면 시간도 다시 계산해야 합니다.")
    steps["sources"] = {"state": src_state, "notes": src_notes}

    steps["visual"], steps["review"] = _visual_steps(p, clips)

    pub = p.get("publish") or {}
    pub_ok = bool(pub.get("title")) and bool(pub.get("description"))
    last = p.get("last_export") or {}
    e_notes = [] if pub_ok else ["게시 제목과 설명문을 작성하세요."]
    if mm["mismatch"] and not mm["acknowledged"]:
        e_state = "todo"
        e_notes.insert(0, "음성이 현재 대본(TTS)과 달라 내보낼 수 없습니다.")
    elif last and last.get("basis") == export_basis(p) and pub_ok:
        e_state = "done"
    elif last:
        e_state = "stale"
        e_notes.insert(0, "마지막으로 내보낸 뒤 바뀐 내용이 있습니다. 다시 내보내세요."
                       if last.get("basis") else "이전 버전에서 내보낸 기록입니다. 최신 내용으로 다시 내보내세요.")
    else:
        e_state = "todo"
    steps["export"] = {"state": e_state, "notes": e_notes}

    order = [("plan", "기획 방향을 정하세요"), ("script", "대본을 완성하세요"),
             ("audio", "음성을 넣고 무음 정리를 하세요"), ("subtitles", "자막 시간을 맞추세요"),
             ("visual", "화풍을 확정하고 반복 등장 요소를 정하세요"),
             ("sources", "장면별 영상소스를 확보하세요"), ("review", "영상을 기준 이미지와 비교해 검수하세요"),
             ("export", "게시 정보 작성 후 내보내세요")]
    nxt = "모든 단계가 완료되었습니다. CapCut에서 편집을 시작하세요."
    for key, label in order:
        st = steps[key]["state"]
        if st not in ("done", "optional"):
            nxt = (steps[key]["notes"][0] if st == "stale" and steps[key]["notes"] else label)
            nxt = f"[{STEP_LABELS[key]}] {nxt}"
            break
    return {"steps": steps, "next": nxt, "tts_hash": tts_hash(p), "display_hash": display_hash(p),
            "plan_hash": plan_hash(p), "audio_mismatch": mm["mismatch"], "audio_mismatch_ack": mm["acknowledged"]}


def _visual_steps(p: dict, clips: list[dict]) -> tuple[dict, dict]:
    """화풍·등장 요소 단계와 검수 단계. Flow를 쓰지 않는 프로젝트(예전 프로젝트 포함)는 '선택'으로 두어 막지 않는다."""
    from . import visual as V

    vis = p.get("visual") or {}
    uses = bool(vis.get("elements")) or bool(vis.get("style")) or any(c.get("review") for c in clips)
    if not uses:  # 예전 프로젝트·Flow 미사용: 막지 않고 안내만
        note = ("화풍을 확정하고 반복 등장 요소를 정하면 클립 사이 일관성을 관리하기 쉬워집니다(선택)." if clips
                else "Flow 클립을 쓰지 않으면 건너뛰어도 됩니다.")
        opt = {"state": "optional", "notes": [note]}
        return opt, dict(opt)
    notes = []
    st = vis.get("style") or {}
    if not st.get("locked"):
        notes.append("프로젝트 화풍을 확정(잠금)하세요." if st.get("style_en") else "화풍 프리셋을 고르고 확정하세요.")
    em = V.element_map(p)
    used = {e for c in clips for e in c.get("element_ids") or []}
    unconfirmed = [em[e].get("name") for e in used if e in em and not em[e].get("locked")]
    if unconfirmed:
        notes.append("클립에 연결된 미확정 등장 요소: " + ", ".join(unconfirmed))
    missing = [e for e in used if e not in em]
    no_ref = [em[e].get("name") for e in used if e in em and not em[e].get("primary_ref_id")]
    if no_ref:
        notes.append("기준 이미지가 확정되지 않은 요소(텍스트 묘사만 사용): " + ", ".join(no_ref))
    v_state = "stale" if missing else ("todo" if not st.get("locked") or unconfirmed else "done")
    if missing:
        notes.insert(0, f"삭제된 등장 요소를 가리키는 클립 연결이 {len(missing)}개 있습니다.")
    r_notes, r_state = [], "done"
    if not clips:
        return {"state": v_state, "notes": notes}, {"state": "optional", "notes": ["클립 계획 후 검수합니다."]}
    states = [V.review_state(p, c) for c in clips]
    n = lambda k: sum(1 for x in states if x["state"] == k)  # noqa: E731
    if n("recheck"):
        r_state = "stale"
        r_notes.append(f"재검토가 필요한 클립 {n('recheck')}개(기준 자료·영상·사용 구간 등이 바뀜)")
    if n("todo") or n("fix") or n("no_media"):
        r_state = "todo" if r_state == "done" else r_state
        if n("todo"):
            r_notes.append(f"검수 전 클립 {n('todo')}개")
        if n("fix"):
            r_notes.append(f"'수정 필요' 표시 클립 {n('fix')}개")
        if n("no_media"):
            r_notes.append(f"영상 미등록 클립 {n('no_media')}개")
    return {"state": v_state, "notes": notes}, {"state": r_state, "notes": r_notes}
