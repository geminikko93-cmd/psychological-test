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

SCHEMA = 1
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
        return json.loads(f.read_text(encoding="utf-8"))
    except ValueError as e:
        raise UserError("프로젝트 파일이 손상되었습니다.",
                        "프로젝트 폴더의 backups 안에 있는 최근 백업으로 복원하세요.") from e


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


STEP_LABELS = {"plan": "기획", "script": "대본", "audio": "음성", "subtitles": "자막",
               "sources": "영상소스", "export": "내보내기"}


def compute_status(p: dict) -> dict:
    steps: dict[str, dict] = {}
    plan = p.get("plan")
    steps["plan"] = {"state": "done" if plan else "todo",
                     "notes": [] if plan else ["기획 방향을 선택하세요."]}
    lines = all_lines(p)
    script_ok = bool(lines) and all((ln.get("tts") or "").strip() and (ln.get("display") or "").strip() for ln in lines)
    s_notes = []
    if not lines:
        s_notes.append("대본이 아직 없습니다.")
    elif not script_ok:
        s_notes.append("화면 표시 또는 TTS 일본어가 비어 있는 줄이 있습니다.")
    steps["script"] = {"state": "done" if script_ok else "todo", "notes": s_notes}

    audio = p.get("audio") or {}
    orig = audio.get("original")
    a_state, a_notes = "todo", []
    if orig:
        a_state = "done"
        if orig.get("script_tts_hash") and orig.get("script_tts_hash") != tts_hash(p):
            a_state = "stale"
            a_notes.append("음성을 넣은 뒤 TTS 낭독문이 바뀌었습니다. 타입캐스트에서 다시 생성하거나, 바뀐 부분만 다시 녹음하세요.")
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

    def scene_ok(sc):
        if sc.get("no_source_needed"):
            return True
        if sc.get("id") in clip_scenes:
            return all(c.get("item_id") in acquired_items for c in clips if c.get("scene_id") == sc.get("id"))
        return sc.get("id") in acquired

    missing = [i + 1 for i, sc in enumerate(scenes) if not scene_ok(sc)]
    from .flow import timing_hash
    flow_stale = bool(clips) and fl.get("based_on") != timing_hash(p)
    src_state = "todo" if (not scenes or missing) else ("stale" if (sub_state == "stale" or flow_stale) else "done")
    src_notes = [f"영상을 확보하지 않은 장면: {', '.join(map(str, missing))}"] if missing else []
    if clips:
        no_prompt = sum(1 for c in clips if not c.get("prompt_en"))
        no_file = sum(1 for c in clips if c.get("item_id") not in acquired_items)
        if no_prompt:
            src_notes.append(f"Flow 프롬프트가 없는 클립 {no_prompt}개")
        if no_file:
            src_notes.append(f"생성한 영상을 아직 넣지 않은 클립 {no_file}개")
    if flow_stale:
        src_notes.insert(0, "자막(또는 대본) 시간이 바뀌어 클립 계획을 다시 계산해야 합니다.")
    if sub_state == "stale" and scenes:
        src_notes.append("자막이 최신이 아니라 장면 시간도 다시 계산해야 합니다.")
    steps["sources"] = {"state": src_state, "notes": src_notes}

    pub = p.get("publish") or {}
    pub_ok = bool(pub.get("title")) and bool(pub.get("description"))
    steps["export"] = {"state": "done" if (pub_ok and p.get("last_export")) else "todo",
                       "notes": [] if pub_ok else ["게시 제목과 설명문을 작성하세요."]}

    order = [("plan", "기획 방향을 정하세요"), ("script", "대본을 완성하세요"),
             ("audio", "음성을 넣고 무음 정리를 하세요"), ("subtitles", "자막 시간을 맞추세요"),
             ("sources", "장면별 영상소스를 확보하세요"), ("export", "게시 정보 작성 후 내보내세요")]
    nxt = "모든 단계가 완료되었습니다. CapCut에서 편집을 시작하세요."
    for key, label in order:
        st = steps[key]["state"]
        if st != "done":
            nxt = (steps[key]["notes"][0] if st == "stale" and steps[key]["notes"] else label)
            nxt = f"[{STEP_LABELS[key]}] {nxt}"
            break
    return {"steps": steps, "next": nxt, "tts_hash": tts_hash(p), "display_hash": display_hash(p)}
