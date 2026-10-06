"""CapCut 편집용 묶음 내보내기(폴더 + ZIP). 비공식 CapCut 프로젝트 파일은 만들지 않는다."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import shutil
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from . import audio as A
from . import config, storage
from .jp_text import lint_project
from .sources import verify_media
from .srt import build_srt, check_cues, fmt_time
from .util import UserError, atomic_write_text, safe_filename

BOM = chr(0xFEFF)  # 엑셀·일부 편집기용 UTF-8 BOM
KIND_LABEL = {"entertainment": "창작 오락 콘텐츠", "factual": "사실 기반 콘텐츠", "undecided": "미정"}


def scene_timings(p: dict) -> list[dict]:
    cues = (p.get("subtitles") or {}).get("cues") or []
    by_line: dict[str, list[dict]] = {}
    for c in cues:
        by_line.setdefault(c.get("line_id"), []).append(c)
    scenes = (p.get("script") or {}).get("scenes", [])
    out = []
    for sc in scenes:
        cs = [c for ln in sc.get("lines", []) for c in by_line.get(ln["id"], [])]
        out.append({"scene_id": sc["id"], "start": min((c["start"] for c in cs), default=None),
                    "speech_end": max((c["end"] for c in cs), default=None)})
    dur = ((p.get("audio") or {}).get("processed") or {}).get("duration")
    for i, s in enumerate(out):
        nxt = next((o["start"] for o in out[i + 1:] if o["start"] is not None), None)
        s["end"] = nxt if nxt is not None else (dur if dur else s["speech_end"])
    return out


def credits_text(p: dict) -> str:
    lines = []
    tc = p.get("typecast") or {}
    if (tc.get("credit_text") or "").strip():
        lines.append(tc["credit_text"].strip())
    seen = set()
    for it in (p.get("sources") or {}).get("items", []):
        if it.get("status") != "acquired":
            continue
        t = (it.get("credit_text") or "").strip()
        if t and t not in seen:
            seen.add(t)
            lines.append(t)
    extra = ((p.get("publish") or {}).get("credits_extra") or "").strip()
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def precheck(pid: str, p: dict | None = None) -> dict:
    p = p if p is not None else storage.load_project(pid)
    st = storage.compute_status(p)
    sub_settings = config.load_settings()["subtitle"]
    items: list[dict] = []

    def add(level, msg, hint=""):
        items.append({"level": level, "message": msg, "hint": hint})

    audio = p.get("audio") or {}
    orig, proc = audio.get("original"), audio.get("processed")
    if not orig:
        add("error", "원본 음성이 없습니다.", "음성 단계에서 타입캐스트 음성 파일을 넣으세요.")
    else:
        f = storage.media_path(pid, orig["file"])
        if not f.exists():
            add("error", "원본 음성 파일이 프로젝트 폴더에서 사라졌습니다.", "음성을 다시 넣으세요.")
    if not proc:
        add("error", "최종 음성(무음 정리본)이 없습니다.", "음성 단계에서 무음 정리를 적용하거나 '원본 그대로 사용'을 누르세요.")
    else:
        f = storage.media_path(pid, proc["file"])
        if not f.exists():
            add("error", "최종 음성 파일이 사라졌습니다.", "음성 단계에서 다시 적용하세요.")
        else:
            ok, err = A.verify_playable(f)
            if not ok:
                add("error", "최종 음성 파일을 재생할 수 없습니다: " + err)
    mm = storage.audio_mismatch(p)
    if mm["mismatch"] and not mm["acknowledged"]:
        add("error", "음성이 현재 대본(TTS 낭독문)과 다릅니다. 이대로면 자막·음성이 맞지 않는 영상이 됩니다.",
            "타입캐스트에서 바뀐 낭독문으로 다시 만들어 넣거나, 음성 단계에서 '이 음성 그대로 쓰기(이유 기록)'를 선택하세요.")
    elif mm["mismatch"]:
        add("warn", f"대본과 다른 음성을 사용자가 확인하고 사용 중: {mm['ack'].get('reason', '')}")
    if st["steps"]["audio"]["state"] == "stale" and not mm["mismatch"]:
        for n in st["steps"]["audio"]["notes"]:
            add("stale", n, "음성 단계에서 무음 정리를 다시 적용하세요.")
    cues = (p.get("subtitles") or {}).get("cues") or []
    if not cues:
        add("error", "자막이 없습니다.", "자막 단계에서 자막을 생성하세요.")
    else:
        if st["steps"]["subtitles"]["state"] == "stale":
            for n in st["steps"]["subtitles"]["notes"]:
                add("stale", "자막이 최신이 아닙니다: " + n, "자막 단계에서 다시 맞추세요.")
        for iss in check_cues(cues, proc.get("duration") if proc else None, sub_settings):
            if iss["level"] == "error":
                add("error", "자막 오류: " + iss["message"], "자막 단계에서 해당 자막을 고치세요.")
        warn_n = sum(1 for i in check_cues(cues, proc.get("duration") if proc else None, sub_settings) if i["level"] != "error")
        if warn_n:
            add("warn", f"자막 확인 권장 항목 {warn_n}개(너무 짧음·빠름·신뢰도 낮음 등)")
    scenes = (p.get("script") or {}).get("scenes", [])
    srcs = (p.get("sources") or {}).get("items", [])
    from .flow import scene_mode
    for i, sc in enumerate(scenes, 1):
        mine = [s for s in srcs if s.get("scene_id") == sc["id"] and s.get("status") == "acquired"]
        if not mine and not sc.get("no_source_needed") and scene_mode(p, sc) != "text":
            add("warn", f"장면 {i}에 확보된 영상·이미지가 없습니다.", "영상소스 단계에서 파일을 넣거나 '소스 불필요'로 표시하세요.")
    for s in srcs:
        if s.get("status") != "acquired":
            continue
        f = storage.media_path(pid, s["file"])
        if not f.exists():
            add("error", f"소스 파일이 사라졌습니다: {s.get('orig_name')}", "파일을 다시 넣으세요.")
        if s.get("provider") != "local" and not (s.get("credit_text") or "").strip():
            add("warn", f"외부 소스의 크레딧이 비어 있습니다: {s.get('orig_name')}")
        if not s.get("rights_checked"):
            add("warn", f"이용 조건 확인 표시가 없습니다: {s.get('orig_name')}", "원본 페이지에서 조건을 확인하고 체크하세요.")
    fl = p.get("flow") or {}
    clips = fl.get("clips") or []
    if clips:
        from .flow import timing_hash
        if fl.get("based_on") != timing_hash(p):
            add("stale", "Flow 클립 계획이 최신 자막 시간과 다릅니다.", "영상 단계에서 [클립 계획 다시 계산]을 누르세요.")
        by_id = {s.get("id"): s for s in srcs}
        for c in clips:
            it = by_id.get(c.get("item_id"))
            label = f"장면 {c.get('scene_no')} 클립 {c.get('index_in_scene')}"
            if not it:
                add("warn", f"{label}: 생성한 영상을 넣지 않았습니다.")
            elif it.get("duration") and it["duration"] + 0.05 < c.get("need_s", 0):
                add("warn", f"{label}: 영상 길이({it['duration']}초)가 배치 구간({c['need_s']}초)보다 짧습니다.",
                    "더 긴 길이로 다시 생성하거나 CapCut에서 속도·정지 화면으로 채우세요.")
    pub = p.get("publish") or {}
    if not pub.get("title"):
        add("warn", "게시 제목이 비어 있습니다.")
    if not pub.get("description"):
        add("warn", "게시 설명문이 비어 있습니다.")
    if not ((p.get("typecast") or {}).get("credit_text") or "").strip():
        add("warn", "타입캐스트 크레딧 기록이 비어 있습니다.", "사용한 요금제의 표기 조건을 확인해 음성 단계에 기록하세요.")
    lint = [x for x in lint_project(p) if x["category"] in ("과장", "근거", "약속", "일관성")]
    if lint:
        add("warn", f"표현 점검 항목 {len(lint)}개(과장·근거·약속·일관성)", "대본 단계의 표현 점검을 확인하세요.")
    blocking = any(i["level"] == "error" for i in items)
    return {"items": items, "blocking": blocking, "has_stale": any(i["level"] == "stale" for i in items)}


def _slug(name: str) -> str:
    s = safe_filename(name or "project", "project", 30).replace(" ", "_")
    return re.sub(r"\.+", "_", s)


_name_lock = threading.Lock()
_reserved: set[str] = set()


def _next_version(exports: Path) -> int:
    n = 0
    for f in list(exports.iterdir()) + [exports / r for r in _reserved]:
        m = re.search(r"_v(\d+)_", f.name)
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


def build_package(pid: str, make_zip: bool = True, allow_stale: bool = False, job=None) -> dict:
    """내보내기. 시작 시점의 프로젝트를 스냅샷으로 고정하고, 임시 폴더에 만든 뒤
    검증을 통과해야만 고유한 이름(시각+버전)의 최종 폴더로 확정한다.
    기존 내보내기 폴더·사용자가 넣은 파일은 절대 지우지 않는다. 실패·취소 시 이번 임시 파일만 정리."""
    p = storage.load_project(pid)  # 스냅샷: 이후 프로젝트가 바뀌어도 이번 결과물에는 섞이지 않음
    pc = precheck(pid, p)
    if pc["blocking"]:
        raise UserError("내보내기 전에 고쳐야 할 오류가 있습니다.", " / ".join(i["message"] for i in pc["items"] if i["level"] == "error"))
    if pc["has_stale"] and not allow_stale:
        raise UserError("최신이 아닌 항목이 있습니다.", "다시 맞추거나 다시 계산한 뒤 내보내세요. 그대로 내보내려면 확인 후 '최신 아님 무시하고 내보내기'를 선택하세요.", 409)

    def check():
        if job is not None:
            job.check()

    exports = storage.project_dir(pid) / "exports"
    exports.mkdir(exist_ok=True)
    tmp = exports / f".tmp_{secrets.token_hex(6)}"
    tmp.mkdir()
    tmp_zip = exports / f".tmp_{secrets.token_hex(6)}.zip"
    reserved = None
    try:
        files = _write_package(pid, p, tmp, check)
        check()
        if job is not None:
            job.report(0.7, "내보낸 파일 검증 중(재생 가능·자막 형식·비밀값)…")
        verify = verify_package(tmp)
        if verify["secret_found"]:
            raise UserError("내보낸 파일에서 API 키와 같은 문자열이 발견되어 내보내기를 확정하지 않았습니다.",
                            "메모·설명문 칸에 키를 붙여 넣지 않았는지 확인하세요.")
        if not verify["all_ok"]:
            bad = [f"{r['file']}: {r['note'][:120]}" for r in verify["files"] if not r["ok"]]
            raise UserError("내보낸 파일 검증에 실패해 결과를 확정하지 않았습니다.",
                            "문제 파일: " + " / ".join(bad[:5]) + " — 해당 파일을 다시 넣거나 빼고 다시 내보내세요.", 422)
        check()
        with _name_lock:
            version = _next_version(exports)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"{stamp}_v{version:02d}_{_slug(p.get('name'))}"
            while (exports / name).exists() or (exports / f"{name}.zip").exists() or name in _reserved:
                version += 1
                name = f"{stamp}_v{version:02d}_{_slug(p.get('name'))}"
            _reserved.add(name)
            reserved = name
        zip_final = None
        if make_zip:
            if job is not None:
                job.report(0.85, "ZIP 만드는 중…")
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(tmp.rglob("*")):
                    check()
                    if f.is_file():
                        z.write(f, (Path(name) / f.relative_to(tmp)).as_posix())
            with zipfile.ZipFile(tmp_zip) as z:
                bad = z.testzip()
            if bad:
                raise UserError(f"ZIP 파일 검증에 실패했습니다: {bad}")
        check()
        final = exports / name
        os.replace(tmp, final)
        if make_zip:
            zip_final = exports / f"{name}.zip"
            os.replace(tmp_zip, zip_final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)  # 이번 작업의 임시 폴더만 정리
        if tmp_zip.exists():
            tmp_zip.unlink(missing_ok=True)
        raise
    finally:
        if reserved:
            with _name_lock:
                _reserved.discard(reserved)
    return {"folder": str(final), "zip": str(zip_final) if zip_final else None, "files": files, "verify": verify,
            "precheck": pc, "version": version, "exported_at": datetime.now().isoformat(timespec="seconds"),
            "basis": storage.export_basis(p), "project_rev": p.get("rev")}


def _write_package(pid: str, p: dict, out: Path, check) -> list[dict]:
    files: list[dict] = []

    def rec(path: Path, desc: str):
        files.append({"file": path.relative_to(out).as_posix(), "desc": desc})

    audio = p["audio"]
    proc = audio["processed"]
    f_final = out / "01_FINAL_최종음성_무음정리.wav"
    shutil.copy2(storage.media_path(pid, proc["file"]), f_final)
    check()
    rec(f_final, "CapCut에 넣을 최종 음성(무음 정리 완료)")
    srt_text = build_srt(p["subtitles"]["cues"])
    f_srt = out / "02_FINAL_자막_ja.srt"
    bom = BOM if config.load_settings()["subtitle"].get("srt_bom") else ""
    f_srt.write_bytes((bom + srt_text).encode("utf-8"))
    rec(f_srt, "최종 음성에 맞춘 일본어 자막(UTF-8)")
    orig = audio["original"]
    ext = orig["file"].rsplit(".", 1)[-1]
    f_orig = out / f"03_원본음성_타입캐스트_ORIGINAL.{ext}"
    shutil.copy2(storage.media_path(pid, orig["file"]), f_orig)
    check()
    rec(f_orig, "타입캐스트 원본 음성(편집에 쓰지 않음, 보관용)")

    src_dir = out / "04_영상소스"
    src_dir.mkdir()
    scenes = p["script"]["scenes"]
    timings = {t["scene_id"]: t for t in scene_timings(p)}
    rows = []
    src_names: dict[str, str] = {}
    clips = (p.get("flow") or {}).get("clips") or []
    clip_by_item = {c.get("item_id"): c for c in clips if c.get("item_id")}
    for si, sc in enumerate(scenes, 1):
        mine = [s for s in p["sources"]["items"] if s.get("scene_id") == sc["id"] and s.get("status") == "acquired"]
        mine.sort(key=lambda s: (s["id"] not in clip_by_item, clip_by_item.get(s["id"], {}).get("index_in_scene", 0)))
        k_other = 0
        for s in mine:
            fext = s["file"].rsplit(".", 1)[-1]
            c = clip_by_item.get(s["id"])
            if c:
                name = f"S{si:02d}_C{c['index_in_scene']:02d}_{c['duration']}s_flow.{fext}"
            else:
                k_other += 1
                base = safe_filename(Path(s.get("orig_name") or "source").stem, "source", 30)
                name = f"S{si:02d}_{k_other:02d}_{base}.{fext}"
            shutil.copy2(storage.media_path(pid, s["file"]), src_dir / name)
            check()
            src_names[s["id"]] = name
        t = timings.get(sc["id"], {})
        rows.append({
            "장면": si,
            "시작": fmt_time(t["start"]) if t.get("start") is not None else "",
            "종료": fmt_time(t["end"]) if t.get("end") is not None else "",
            "길이(초)": round(t["end"] - t["start"], 2) if t.get("start") is not None and t.get("end") is not None else "",
            "일본어(화면)": " / ".join(ln["display"].replace("\n", "") for ln in sc["lines"]),
            "한국어 의미": " / ".join(ln.get("ko", "") for ln in sc["lines"]),
            "권장 화면(AI 추천)": sc.get("visual", ""),
            "확보한 파일": " / ".join(src_names[s["id"]] for s in mine) or ("(소스 불필요)" if sc.get("no_source_needed") else "(없음)"),
            "출처·크레딧": " / ".join((s.get("credit_text") or s.get("page_url") or "직접 넣은 파일") for s in mine),
            "이용조건 메모": " / ".join((s.get("license_note") or "") + (" [확인함]" if s.get("rights_checked") else " [미확인]") for s in mine),
            "검색 키워드": ", ".join(sc.get("search_keywords", [])),
            "편집 의도": sc.get("edit_intent", ""),
            "의도적 멈춤(ms)": sc.get("hold_ms") or "",
        })
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["장면"])
    w.writeheader()
    w.writerows(rows)
    f_csv = out / "05_장면표_시간_소스.csv"
    f_csv.write_bytes((BOM + buf.getvalue()).encode("utf-8"))  # 엑셀 한글·일본어 깨짐 방지
    rec(f_csv, "장면별 시간·소스 매칭표(엑셀로 열기)")
    for name in src_names.values():
        rec(src_dir / name, "장면 소스")
    if clips:
        items_by_id = {s["id"]: s for s in p["sources"]["items"]}
        crow = []
        for c in clips:
            it = items_by_id.get(c.get("item_id"))
            crow.append({"장면": c["scene_no"], "클립": c["index_in_scene"], "배치 시작": fmt_time(c["start"]),
                         "배치 끝": fmt_time(c["end"]), "필요 길이(초)": c["need_s"], "Flow 생성 길이(초)": c["duration"],
                         "사용할 구간(파일 기준)": f"0초 ~ {c['need_s']}초",
                         "잘라낼 꼬리(초, 실제 파일 기준)": (round(max(0, it["duration"] - c["need_s"]), 2)
                                                     if it and it.get("duration") else "(파일 길이 미확인)"),
                         "부족한 길이(초)": (round(max(0, c["need_s"] - it["duration"]), 2) if it and it.get("duration") else ""),
                         "파일": src_names.get(c.get("item_id"), "(아직 없음)"),
                         "실제 파일 길이(초)": (it or {}).get("duration") or "", "Flow 모드": c.get("mode", ""),
                         "프롬프트(영어)": c.get("prompt_en", ""), "프롬프트 의미": c.get("prompt_ko", "")})
        b2 = io.StringIO()
        w2 = csv.DictWriter(b2, fieldnames=list(crow[0].keys()))
        w2.writeheader()
        w2.writerows(crow)
        f_clip = out / "05b_Flow_클립_배치표.csv"
        f_clip.write_bytes((BOM + b2.getvalue()).encode("utf-8"))  # 엑셀 한글·일본어 깨짐 방지
        rec(f_clip, "Flow 클립별 배치 시간·생성 길이·파일")
        st = (p.get("flow") or {}).get("style") or {}
        from .flow import PRODUCT_LABEL
        fl_lines = [f"# {PRODUCT_LABEL} 영상 프롬프트", "",
                    "## 공통 스타일", st.get("look_en", ""), st.get("look_ko", ""), "", "피할 것: " + st.get("avoid_en", ""), ""]
        for c in clips:
            fl_lines += [f"## 장면 {c['scene_no']} · 클립 {c['index_in_scene']} — Flow 길이 {c['duration']}초 "
                         f"(배치 {fmt_time(c['start'])} → {fmt_time(c['end'])}, 모드: {c.get('mode', 'text')})",
                         c.get("prompt_en", "") or "(프롬프트 없음)", "", "의미: " + (c.get("prompt_ko") or ""),
                         "진행: " + (c.get("beats_ko") or ""), ""]
        f_fp = out / "10_Flow_영상프롬프트.txt"
        f_fp.write_text("\n".join(fl_lines), encoding="utf-8")
        rec(f_fp, "Google Flow에 넣은 영상 프롬프트 기록")

    kind = KIND_LABEL.get((p.get("idea") or {}).get("content_kind", "undecided"), "미정")
    sl = [f"# {p.get('name')} — 대본 (일본어 / 한국어 의미)", f"콘텐츠 구분: {kind}", ""]
    for si, sc in enumerate(scenes, 1):
        t = timings.get(sc["id"], {})
        tt = f" [{fmt_time(t['start'])} → {fmt_time(t['end'])}]" if t.get("start") is not None else ""
        sl.append(f"## 장면 {si}{tt}  {sc.get('purpose', '')}")
        for ln in sc["lines"]:
            sl.append(f"화면: {ln['display']}")
            if ln.get("tts") and ln["tts"] != ln["display"]:
                sl.append(f"낭독: {ln['tts']}")
            sl.append(f"의미: {ln.get('ko', '')}")
            if ln.get("reading_note"):
                sl.append(f"읽기 메모: {ln['reading_note']}")
            if ln.get("caution"):
                sl.append(f"주의: {ln['caution']}")
            sl.append("")
        if sc.get("hold_ms"):
            sl.append(f"(의도적 멈춤 {sc['hold_ms']}ms: {sc.get('hold_reason', '')})")
        if sc.get("fact_note"):
            sl.append(f"근거 메모: {sc['fact_note']}")
        sl.append("")
    sl.append("※ 일본어 표현은 AI 검토와 사용자 확인을 거친 것이며, 원어민 검수를 받은 것이 아닙니다.")
    f_script = out / "06_대본_일본어_한국어.txt"
    f_script.write_text("\n".join(sl), encoding="utf-8")
    rec(f_script, "일본어 대본과 한국어 의미")
    f_tts = out / "07_타입캐스트_낭독문.txt"
    f_tts.write_text(storage.tts_text(p), encoding="utf-8")
    rec(f_tts, "타입캐스트에 붙여 넣는 낭독문(일본어만)")

    pub = p.get("publish") or {}
    cred = credits_text(p)
    desc_full = (pub.get("description") or "").rstrip()
    if cred:
        desc_full += "\n\n" + cred
    pl = ["# 게시 정보", "", "## 제목", pub.get("title", ""), "", "## 설명문(크레딧 포함, 그대로 붙여 넣기)", desc_full, "",
          "## 해시태그", pub.get("hashtags", ""), "", "## 크레딧(실제 기록 기준)", cred or "(기록 없음)", "",
          "## 게시 전 확인", "- Instagram Reels: 사실적인 AI 음성 내레이션이 있으면 AI 라벨 표시가 필요한지 게시 화면에서 확인하세요.",
          *(["- AI 생성 영상(Google Flow)을 사용했습니다. 사실적으로 보이는 장면이면 YouTube 업로드 시 '변경되거나 합성된 콘텐츠'를 '예'로, "
             "Instagram은 AI 라벨을 켜야 하는지 확인하세요."] if any(i.get("ai_generated") for i in p["sources"]["items"]) else []),
          "- 제목·설명문이 영상보다 강한 주장을 하지 않는지 확인하세요."]
    f_pub = out / "08_게시정보_제목_설명_크레딧.txt"
    f_pub.write_text("\n".join(pl), encoding="utf-8")
    rec(f_pub, "게시 제목·설명문·크레딧")

    dur = proc.get("duration") or 0
    guide = [
        f"CapCut 작업 순서 — {p.get('name')}",
        "",
        f"최종 음성 길이: {dur:.2f}초 / 자막 {len(p['subtitles']['cues'])}개 / 장면 {len(scenes)}개",
        "",
        "1. CapCut에서 새 프로젝트를 만들고 화면 비율을 9:16으로 설정합니다.",
        "2. '01_FINAL_최종음성_무음정리.wav'를 오디오 트랙 0초 위치에 놓습니다. 음성 속도는 바꾸지 마세요(자막 시간이 어긋납니다).",
        "   ※ '03_원본음성…'은 보관용입니다. 편집에는 01번만 씁니다.",
        "3. 자막: 텍스트(캡션) 메뉴에서 '로컬 자막/자막 파일 가져오기'로 '02_FINAL_자막_ja.srt'를 불러옵니다.",
        "   (메뉴 이름은 CapCut 버전에 따라 다를 수 있습니다.) 불러온 뒤 글꼴·크기·위치를 정합니다.",
        "   하단은 Shorts/Reels UI에 가려지므로 화면 중앙~위쪽 2/3 안에 두는 것을 권장합니다.",
        "4. '05_장면표_시간_소스.csv'의 시작·종료 시간에 맞춰 '04_영상소스'의 파일을 배치합니다.",
        *(["   Flow 클립(S01_C01_8s_flow.mp4 등)은 '05b_Flow_클립_배치표.csv'의 '배치 시작'에 놓고, '배치 끝' 이후 꼬리는 잘라냅니다.",
           "   Flow 클립에 들어 있는 소리는 내레이션과 겹치므로 음소거하거나 아주 작게 줄이세요."] if clips else []),
        "   사진은 확대/이동(켄번즈) 효과로 움직임을 주면 좋습니다.",
        "5. 배경음악을 넣는다면 음성보다 충분히 작게(대략 -20dB 전후) 맞추고, 음원의 이용 조건을 확인합니다.",
        "6. 처음 3초에 훅 문장이 화면에 보이는지, 자막과 음성이 맞는지 처음부터 끝까지 한 번 재생해 확인합니다.",
        "7. 1080x1920으로 내보낸 뒤 '08_게시정보…'의 제목·설명문으로 게시합니다.",
        "",
        "장면별 배치",
    ]
    for r in rows:
        guide.append(f"  장면 {r['장면']}: {r['시작']} → {r['종료']}  파일: {r['확보한 파일']}  | {r['편집 의도']}")
    f_guide = out / "00_먼저_읽기_CapCut_작업순서.txt"
    f_guide.write_text("\n".join(guide), encoding="utf-8")
    rec(f_guide, "CapCut 작업 순서 안내")

    # 다시 열 수 있는 프로젝트 데이터(설정·키 제외)
    pdir = out / "09_프로젝트데이터(다시열기용)"
    pdir.mkdir()
    clean = json.loads(json.dumps(p))
    clean.pop("ai_log", None)
    (pdir / "project.json").write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    check()
    shutil.copytree(storage.project_dir(pid) / "media", pdir / "media")
    check()
    rec(pdir / "project.json", "프로그램에서 [프로젝트 가져오기]로 다시 열 수 있는 데이터")

    readme = ["이 폴더의 파일 목록", "", f"프로젝트 리비전: {p.get('rev')} (내보내기 시작 시점 스냅샷)", ""] + \
        [f"- {f['file']}: {f['desc']}" for f in files]
    atomic_write_text(out / "파일목록.txt", "\n".join(readme))
    return files


def verify_package(out: Path) -> dict:
    """내보낸 파일 존재·재생 가능·자막 형식·비밀값 포함 여부 검사."""
    from .srt import parse_srt

    results = []
    secrets = config.all_secret_values()
    secret_found = False
    for f in sorted(out.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(out).as_posix()
        ok, note = True, ""
        ext = f.suffix.lower().lstrip(".")
        if ext in ("wav", "mp3", "m4a", "aac", "ogg", "flac"):
            ok, note = A.verify_playable(f)
        elif ext in ("mp4", "mov", "webm", "m4v", "jpg", "jpeg", "png", "webp", "gif"):
            ok, note = verify_media(f)
        elif ext == "srt":
            try:
                raw = f.read_bytes()
                text = raw.decode("utf-8")
                parse_srt(text)
            except (UnicodeDecodeError, ValueError) as e:
                ok, note = False, f"SRT 형식/인코딩 오류: {e}"
        if secrets and ext in ("txt", "json", "srt", "csv", "md"):
            data = f.read_text(encoding="utf-8", errors="replace")
            if any(s in data for s in secrets):
                secret_found = True
                ok, note = False, "비밀값 포함"
        results.append({"file": rel, "ok": ok, "note": note, "size": f.stat().st_size})
    return {"files": results, "all_ok": all(r["ok"] for r in results), "secret_found": secret_found}


def import_package(zip_bytes: bytes) -> dict:
    """내보낸 ZIP(또는 프로젝트 백업 ZIP)에서 프로젝트를 새로 만든다."""
    try:
        z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise UserError("ZIP 파일이 아닙니다.") from e
    names = z.namelist()
    pj = [n for n in names if n.endswith("project.json")]
    if not pj:
        raise UserError("ZIP 안에서 project.json을 찾지 못했습니다.", "이 프로그램에서 내보낸 ZIP인지 확인하세요.")
    root = pj[0][: -len("project.json")]
    data = json.loads(z.read(pj[0]).decode("utf-8"))
    if not isinstance(data, dict) or "script" not in data:
        raise UserError("프로젝트 데이터 형식이 올바르지 않습니다.")
    data["name"] = (data.get("name") or "가져온 프로젝트") + " (가져옴)"
    new = storage.create_project(data["name"], data)
    for n in names:
        if not n.startswith(root + "media/") or n.endswith("/"):
            continue
        rel = n[len(root):]
        dest = storage.media_path(new["id"], rel)  # 경로 탈출 방지
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(z.read(n))
    return storage.load_project(new["id"])
