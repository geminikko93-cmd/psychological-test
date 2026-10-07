"""채널 기본 설정(한 번 입력)과 주제 목록.

- 저장 위치: 데이터 폴더의 channel.json (프로젝트들과 같은 곳, 비밀값 없음)
- 처음 열 때 examples/channel_seed.json(보고서 기반, 과장 표현 제거)으로 채운다.
- 새 프로젝트를 만들면 채널 설정으로 기획 칸을 미리 채우고, 모든 AI 요청에 채널 설정을 함께 보낸다.
"""
from __future__ import annotations

import json
import threading

from . import config, storage
from .util import UserError, atomic_write_text, new_id, now_iso

SEED_FILE = config.STATIC_DIR.parent / "examples" / "channel_seed.json"
_lock = threading.Lock()


def _file():
    return config.DATA_DIR / "channel.json"


def _seed() -> dict:
    try:
        return json.loads(SEED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"profile": {}, "topics": []}


def load() -> dict:
    with _lock:
        f = _file()
        if not f.exists():
            data = _seed()
            data["created_at"] = now_iso()
            config.ensure_dirs()
            atomic_write_text(f, json.dumps(data, ensure_ascii=False, indent=1))
            return data
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            raise UserError("채널 설정 파일이 손상되었습니다.", f"{f} 를 확인하세요.") from e
        if int(data.get("seed_version") or 1) < 2:
            data = _upgrade_seed(f, data)
        return data


def _upgrade_seed(f, data: dict) -> dict:
    """기본값 개선(v2): 사용자가 고치지 않은 기본 문구만 새 기본값으로 바꾼다. 고친 항목·추가 주제·상태는 그대로.
    바꾸기 전 원본은 channel_backup_*.json 으로 보관."""
    old_file = SEED_FILE.with_name("channel_seed_v1.json")
    try:
        old = json.loads(old_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    new = _seed()
    atomic_write_text(f.with_name(f"channel_backup_{now_iso()[:19].replace(':', '').replace('-', '')}.json"),
                      json.dumps(data, ensure_ascii=False, indent=1))
    prof, oldp, newp = data.setdefault("profile", {}), old.get("profile", {}), new.get("profile", {})
    updated = []
    for k in ("genre", "value", "format", "rules"):
        if prof.get(k) == oldp.get(k) and k in newp:
            prof[k] = newp[k]
            updated.append(k)
    old_angles = {t["id"]: t.get("angle_ko") for t in old.get("topics", [])}
    new_angles = {t["id"]: t.get("angle_ko") for t in new.get("topics", [])}
    for t in data.get("topics", []):
        if t.get("id") in old_angles and t.get("angle_ko") == old_angles[t["id"]] and t["id"] in new_angles:
            t["angle_ko"] = new_angles[t["id"]]
    data["seed_version"] = 2
    data.setdefault("migrations", []).append({"to": "seed_v2", "at": now_iso(), "updated_profile_keys": updated})
    atomic_write_text(f, json.dumps(data, ensure_ascii=False, indent=1))
    return data


def save(data: dict) -> dict:
    if not isinstance(data.get("profile"), dict) or not isinstance(data.get("topics"), list):
        raise UserError("채널 설정 형식이 올바르지 않습니다.")
    text = json.dumps(data, ensure_ascii=False, indent=1)
    for s in config.all_secret_values():
        if s in text:
            raise UserError("채널 설정에 API 키와 같은 문자열이 있어 저장을 막았습니다.")
    data["updated_at"] = now_iso()
    with _lock:
        atomic_write_text(_file(), json.dumps(data, ensure_ascii=False, indent=1))
    return data


def reseed() -> dict:
    """보고서 기본 주제 중 목록에 없는 것만 다시 추가(기존 주제·상태는 그대로)."""
    data = load()
    have = {t.get("id") for t in data["topics"]}
    added = [t for t in _seed()["topics"] if t["id"] not in have]
    data["topics"].extend(added)
    data["topics"].sort(key=lambda t: (t.get("no") or 999, t.get("title_ja", "")))
    save(data)
    return {"added": len(added), "channel": data}


def add_topics(lines: list[str]) -> dict:
    data = load()
    nos = [t.get("no") or 0 for t in data["topics"]]
    n = max(nos + [0])
    added = 0
    for line in lines:
        line = line.strip().lstrip("-•*0123456789.) ").strip()
        if not line:
            continue
        n += 1
        ja, _, ko = line.partition("|")
        data["topics"].append({"id": new_id("t"), "no": n, "title_ja": ja.strip()[:200], "title_ko": ko.strip()[:200],
                               "angle_ko": "", "first_line_ja": "", "example_choices": "", "caution_ko": "",
                               "series_ko": "", "source": "직접 추가", "status": "unused", "project_ids": [], "memo": ""})
        added += 1
    save(data)
    return {"added": added, "channel": data}


def _backup(tag: str) -> None:
    f = _file()
    if f.exists():
        atomic_write_text(f.with_name(f"channel_backup_{now_iso()[:19].replace(':', '').replace('-', '')}_{tag}.json"),
                          f.read_text(encoding="utf-8"))


def apply_preset(keys: list[str], add_rules: bool = False) -> dict:
    """사용자가 고른 추천 항목만 채널 설정에 적용(자동 적용 없음). 적용 전 channel_backup_*.json 보관."""
    from .presets import CHANNEL_PRESET

    fields = CHANNEL_PRESET["fields"]
    keys = [k for k in keys if k in fields]
    if not keys and not add_rules:
        raise UserError("적용할 항목을 고르세요.")
    data = load()
    _backup("before-preset")
    prof = data.setdefault("profile", {})
    for k in keys:
        prof[k] = fields[k]
    added = 0
    if add_rules:
        rules = prof.setdefault("rules", [])
        for r in CHANNEL_PRESET["rules_add"]:
            if r not in rules:
                rules.append(r)
                added += 1
    data.setdefault("applied_presets", []).append({"id": CHANNEL_PRESET["id"], "keys": keys, "rules_added": added,
                                                   "at": now_iso()})
    save(data)
    return {"applied": keys, "rules_added": added, "channel": data}


def add_ideas() -> dict:
    """추천 기획 후보를 주제 목록에 '추가'만 한다(이미 있으면 그대로 — 사용자가 고친 내용 보존)."""
    from .presets import PLAN_IDEAS

    data = load()
    have = {t.get("id") for t in data["topics"]}
    n = max([t.get("no") or 0 for t in data["topics"]] + [0])
    added = 0
    for idea in PLAN_IDEAS:
        if idea["id"] in have:
            continue
        n += 1
        data["topics"].append({"id": idea["id"], "no": n, "title_ja": idea["title_ja"], "title_ko": idea["title_ko"],
                               "angle_ko": idea["angle_ko"], "first_line_ja": idea["first_line_ja"],
                               "example_choices": idea["example_choices"], "caution_ko": idea["caution_ko"],
                               "series_ko": idea["series_ko"], "style_hint": idea.get("style_hint", ""),
                               "source": "추천 기획 후보(실험용)", "status": "unused", "project_ids": [], "memo": ""})
        added += 1
    save(data)
    return {"added": added, "channel": data}


def visual_defaults(project: dict, topic: dict | None = None) -> None:
    """새 프로젝트에만: 채널에서 고른 기본 화풍(또는 주제의 추천 화풍)을 '확정 전 초안'으로 넣는다."""
    from . import visual

    prof = load().get("profile") or {}
    pid = prof.get("default_style_preset") or (topic or {}).get("style_hint")  # 사용자가 고른 채널 기본값이 우선
    if pid and not (project.get("visual") or {}).get("style"):
        project.setdefault("visual", {})["style"] = visual.new_style_from_preset(pid)


def profile_block() -> dict:
    """AI 요청에 붙일 채널 설정(빈 값 제외)."""
    p = load().get("profile") or {}
    keys = ("channel_name", "genre", "audience", "value", "format", "tone", "visual_style", "rules",
            "description_notice_ja", "upload_plan", "target_seconds", "length_range")
    return {k: p[k] for k in keys if p.get(k)}


def idea_from_profile(topic: dict | None = None) -> dict:
    p = load().get("profile") or {}
    idea = {"topic": "", "audience": p.get("audience", ""), "notes": "", "content_kind": "entertainment"
            if "오락" in (p.get("genre") or "") else "undecided",
            "target_seconds": p.get("target_seconds") or 35, "avoid": "", "platforms": p.get("platforms") or
            ["YouTube Shorts", "Instagram Reels"]}
    if topic:
        idea["topic"] = f"{topic.get('title_ja', '')}（{topic.get('title_ko', '')}）" + \
            (f" — {topic['angle_ko']}" if topic.get("angle_ko") else "")
        notes = []
        if topic.get("first_line_ja"):
            notes.append(f"첫 문장 후보(참고): {topic['first_line_ja']}")
        if topic.get("example_choices"):
            notes.append(f"보고서의 예시 선택지(참고용, 그대로 쓰지 않아도 됨): {topic['example_choices']}")
        if topic.get("series_ko"):
            notes.append(f"후속편 확장: {topic['series_ko']}")
        if topic.get("memo"):
            notes.append(f"메모: {topic['memo']}")
        idea["notes"] = "\n".join(notes)
        idea["avoid"] = topic.get("caution_ko", "")
        idea["topic_id"] = topic.get("id")
    return idea


def start_project(topic_id: str) -> dict:
    data = load()
    topic = next((t for t in data["topics"] if t.get("id") == topic_id), None)
    if not topic:
        raise UserError("주제를 찾을 수 없습니다.", status=404)
    base = storage.empty_project(f"{topic.get('no', '')}. {topic.get('title_ko') or topic.get('title_ja')}")
    base["idea"] = idea_from_profile(topic)
    visual_defaults(base, topic)
    p = storage.create_project(base["name"], base)
    topic["status"] = "used"
    topic.setdefault("project_ids", []).append(p["id"])
    save(data)
    return p
