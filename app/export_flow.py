"""내보내기의 Flow·일관성 자료 부분: 클립 배치표, 최종 프롬프트, 화풍·등장 요소·기준 이미지·시작 프레임,
CapCut 편집 지시(재사용·크롭·확대 — 프로그램이 적용하지 않음), 검수표, 설명란 고지."""
from __future__ import annotations

import csv
import io
import shutil
from pathlib import Path

from . import storage
from . import visual as V
from .srt import fmt_time
from .util import UserError, safe_filename

BOM = chr(0xFEFF)
DEFAULT_NOTICE = "※この動画の心理テストは、娯楽として楽しむ内容です。"


def notice_text(p: dict) -> str:
    """설명란 고지(오락 콘텐츠). 프로젝트 지정 > 채널 설정 > 기본 문구. 사실 콘텐츠·고지 끔이면 없음."""
    pub = p.get("publish") or {}
    if pub.get("entertainment_notice") is False or (p.get("idea") or {}).get("content_kind") == "factual":
        return ""
    if (pub.get("notice_ja") or "").strip():
        return pub["notice_ja"].strip()
    try:
        from . import channel
        t = ((channel.load().get("profile") or {}).get("description_notice_ja") or "").strip()
    except UserError:
        t = ""
    return t or DEFAULT_NOTICE


def description_with_notice(p: dict) -> str:
    desc = ((p.get("publish") or {}).get("description") or "").rstrip()
    n = notice_text(p)
    if n and n not in desc:
        desc = (desc + "\n\n" + n).strip()
    return desc


def csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["-"])
    w.writeheader()
    w.writerows(rows)
    return (BOM + buf.getvalue()).encode("utf-8")  # 엑셀 한글·일본어 깨짐 방지


def label(c: dict) -> str:
    return f"S{c['scene_no']:02d}-C{c['index_in_scene']:02d}"


def copy_still_assets(pid: str, p: dict, clips: list[dict], src_dir: Path, src_names: dict, rec, check) -> None:
    """정지 화면으로 쓰는 확정 이미지도 04_영상소스에 한 번만 복사."""
    am = V.assets(p)
    for c in clips:
        aid = c.get("still_asset_id")
        if c.get("gen_mode") == "still" and aid in am and aid not in src_names:
            a = am[aid]
            f = storage.media_path(pid, a["file"])
            if f.exists():
                name = f"S{c['scene_no']:02d}_C{c['index_in_scene']:02d}_still.{a['file'].rsplit('.', 1)[-1]}"
                shutil.copy2(f, src_dir / name)
                check()
                src_names[aid] = name
                rec(src_dir / name, "정지 화면으로 쓰는 확정 이미지")


def write_flow_files(pid: str, p: dict, out: Path, clips: list[dict], src_names: dict, rec, check) -> None:
    from .flow import ASPECT, MODEL_LABEL, PRODUCT_LABEL

    items_by_id = {s["id"]: s for s in (p.get("sources") or {}).get("items", [])}
    am = V.assets(p)
    em = V.element_map(p)
    by_id = {c["id"]: c for c in clips}
    cdir = out / "11_일관성_자료"
    refdir, framedir = cdir / "기준이미지", cdir / "시작프레임"
    exported: dict[str, str] = {}

    def export_img(rel_file: str | None, folder: Path, name: str) -> str:
        if not rel_file:
            return ""
        if rel_file in exported:
            return exported[rel_file]
        f = storage.media_path(pid, rel_file)
        if not f.exists():
            return "(파일 없음)"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{name}.{rel_file.rsplit('.', 1)[-1]}"
        shutil.copy2(f, dest)
        check()
        exported[rel_file] = dest.relative_to(out).as_posix()
        rec(dest, "일관성 자료 이미지")
        return exported[rel_file]

    el_ref_name: dict[str, str] = {}
    erows = []
    for i, e in enumerate(V.elements(p), 1):
        a = am.get(e.get("primary_ref_id"))
        fname = export_img(a["file"], refdir, f"E{i:02d}_{safe_filename(e.get('name') or 'element', 'element', 24)}_기준") if a else ""
        el_ref_name[e["id"]] = fname
        erows.append({"번호": f"E{i:02d}", "ID": e["id"], "이름": e.get("name", ""), "유형": V.ELEMENT_TYPES.get(e.get("type"), ""),
                      "한국어 설명": e.get("desc_ko", ""), "고정 영어 묘사": e.get("fixed_en", ""),
                      "변경 금지 특징": "; ".join(e.get("must_keep") or []),
                      "유형별 특징": "; ".join(f"{k}: {v}" for k, v in (e.get("features") or {}).items() if v),
                      "기준 이미지": fname or "(없음 — 텍스트 묘사만)", "확정·잠금": "확정" if e.get("locked") else "미확정",
                      "버전": e.get("version", 0), "Flow Characters 이름(사용자 기록)": e.get("flow_name", ""),
                      "Flow 메모": e.get("flow_note", ""),
                      "사용 클립": ", ".join(label(c) for c in clips if e["id"] in (c.get("element_ids") or []))})

    crow, prompt_lines, edit_lines, review_rows = [], [], [], []
    for c in clips:
        m = V.resolve_media(p, c)
        it = items_by_id.get(m["id"]) if m and m["kind"] == "item" else None
        is_img = (m or {}).get("media") == "image"
        use_start = float(c.get("use_start") or 0)
        use_end = use_start + float(c["need_s"])
        sf = V.frame_ref(p, c.get("start_frame"))
        sf_name = export_img(sf["file"], framedir, f"{label(c)}_시작프레임") if sf and sf.get("file") else ""
        ef = V.frame_ref(p, c.get("end_frame"))
        ef_name = export_img(ef["file"], framedir, f"{label(c)}_마지막프레임") if ef and ef.get("file") else ""
        refs = V.clip_refs(p, c)
        ref_names = [el_ref_name.get(r["element_id"]) or export_img(r["file"], refdir, f"{label(c)}_참조{r['no']}") for r in refs]
        rs = V.review_state(p, c)
        rv = c.get("review") or {}
        mode = c.get("gen_mode") or c.get("mode") or "text"
        src_file = src_names.get(m["id"]) if m else None
        reuse = by_id.get(c.get("reuse_of")) if mode == "reuse" else None
        link = ""
        if sf and sf.get("frame"):
            src_c = by_id.get(sf["frame"].get("clip_id"))
            link = f"{label(src_c) if src_c else '?'}의 실제 사용 구간 끝 프레임({sf['frame'].get('t')}초)에서 시작"
        elif sf and sf.get("file"):
            link = "확정 이미지에서 새로 시작"
        edit = c.get("edit") or {}
        names = ", ".join(em[e].get("name", e) for e in c.get("element_ids") or [] if e in em)
        settings = V.flow_settings_ko(p, c, MODEL_LABEL, ASPECT)
        crow.append({
            "클립": label(c), "장면": c["scene_no"], "역할": V.role_label(c.get("role")), "배치 시작": fmt_time(c["start"]),
            "배치 끝": fmt_time(c["end"]), "필요 길이(초)": c["need_s"], "Flow 생성 길이(초)": c["duration"],
            "생성 방식": V.GEN_MODES.get(mode, mode),
            "사용할 구간(파일 기준)": f"정지 이미지 {c['need_s']}초" if is_img else f"{use_start:g}초 ~ {round(use_end, 2):g}초",
            "잘라낼 꼬리(초, 실제 파일 기준)": ("(정지 이미지)" if is_img else
                                         round(max(0, it["duration"] - use_end), 2) if it and it.get("duration") else "(파일 길이 미확인)"),
            "부족한 길이(초)": (round(max(0, use_end - it["duration"]), 2) if it and it.get("duration") else ""),
            "파일": src_file or "(아직 없음)", "실제 파일 길이(초)": (it or {}).get("duration") or "",
            "재사용 원본": label(reuse) if reuse else "", "등장 요소": names,
            "참조 이미지(생성 요청에서 선택)": " / ".join(n for n in ref_names if n), "시작 프레임": sf_name, "마지막 프레임": ef_name,
            "클립 연결": link, "크롭": edit.get("crop", ""), "확대": edit.get("zoom", ""), "편집 의도": edit.get("intent", ""),
            "Flow 모드": c.get("mode", ""), "Flow 설정": " | ".join(settings), "검수 상태": rs["label_ko"],
            "재검토 사유": " / ".join(rs.get("reasons") or []),
            "프롬프트(영어)": c.get("prompt_en", ""), "프롬프트 의미": c.get("var_ko") or c.get("prompt_ko", ""),
            "진행(초 단위)": c.get("beats_ko", ""),
        })
        prompt_lines += [f"## {label(c)} — {V.role_label(c.get('role')) or '클립'} · Flow 길이 {c['duration']}초 "
                         f"(배치 {fmt_time(c['start'])} → {fmt_time(c['end'])})",
                         "Flow에서 직접 고를 설정:", *[f"  - {x}" for x in settings],
                         f"등장 요소: {names or '(없음)'}",
                         f"참조 이미지: {' / '.join(n for n in ref_names if n) or '(없음)'}",
                         f"시작 프레임: {sf_name or '(없음)'}" + (f" — {link}" if link else ""),
                         "", "[최종 프롬프트 — 그대로 붙여 넣기]",
                         c.get("prompt_en", "") or "(프롬프트 없음 — Flow 생성이 없는 클립이면 정상)", "",
                         "의미: " + (c.get("var_ko") or c.get("prompt_ko") or ""), "진행: " + (c.get("beats_ko") or "")]
        if c.get("changes"):
            prompt_lines.append("의도적 변경: " + "; ".join((x.get("ko") or x.get("en", "")) for x in c["changes"]))
        prompt_lines += [f"⚠ 충돌: {x['message_ko']}" for x in V.find_conflicts(p, c)] + [""]

        el = [f"{label(c)} ({fmt_time(c['start'])} → {fmt_time(c['end'])}, {V.role_label(c.get('role')) or '클립'})"]
        if reuse:
            el.append(f"  · 소재: {label(reuse)}의 소재({src_file or '파일 미등록'})를 다시 사용")
        elif mode == "still":
            el.append(f"  · 소재: 확정 이미지 {src_file or '(미지정)'}를 {c['need_s']}초 동안 정지 화면으로")
        elif src_file:
            el.append(f"  · 소재: {src_file}의 {use_start:g}초부터 {c['need_s']}초만 사용(그 뒤는 잘라냄)")
        for k, lab in (("crop", "크롭"), ("zoom", "확대"), ("intent", "편집 의도")):
            if (edit.get(k) or "").strip():
                el.append(f"  · {lab}: {edit[k].strip()}")
        if edit.get("hold_still"):
            el.append("  · 선택하는 동안 화면을 정지(프리즈 프레임)로 유지")
        if c.get("role") == "choice":
            el.append("  · 질문·A/B/C 선택지는 자막(텍스트)으로 넣기. 선택지 크기·밝기를 같게, 특정 선택지만 강조하지 않기")
        edit_lines += el + [""]
        checks = rv.get("checks") or {}
        review_rows.append({"클립": label(c), "영상 등록": "등록" if m else "미등록", "검수 상태": rs["label_ko"],
                            "재검토 사유": " / ".join(rs.get("reasons") or []),
                            **{lab: {"ok": "통과", "ng": "수정 필요", "na": "해당 없음"}.get(checks.get(k), "") for k, lab in V.CHECK_ITEMS},
                            "메모": rv.get("memo", ""), "검수 시각": rv.get("reviewed_at", "")})

    f_clip = out / "05b_Flow_클립_배치표.csv"
    f_clip.write_bytes(csv_bytes(crow))
    rec(f_clip, "Flow 클립별 배치 시간·실제 사용 구간·생성 방식·참조 자료·연결·편집 지시·검수 상태")

    st = (p.get("flow") or {}).get("style") or {}
    vst = (p.get("visual") or {}).get("style") or {}
    style_text, avoid = V.style_block(p)
    fl_lines = [f"# {PRODUCT_LABEL} 영상 프롬프트", "",
                "각 클립의 [최종 프롬프트] = 고정 블록(등장 요소·장소·화풍, 저장된 원문) + 클립별 가변 설명 + 공통 제약.",
                "텍스트나 참조 이미지를 써도 완벽한 동일성은 보장되지 않습니다. 생성 결과는 11_일관성_자료/검수표.csv로 직접 확인하세요.", "",
                "## 공통 스타일", style_text or st.get("look_en", ""), vst.get("desc_ko") or st.get("look_ko", ""), "",
                "피할 것: " + (avoid or st.get("avoid_en", "")), ""] + prompt_lines
    f_fp = out / "10_Flow_영상프롬프트.txt"
    f_fp.write_text("\n".join(fl_lines), encoding="utf-8")
    rec(f_fp, "Google Flow에 넣은 영상 프롬프트·설정·참조 자료 기록")

    cdir.mkdir(exist_ok=True)
    if erows:
        f = cdir / "등장요소.csv"
        f.write_bytes(csv_bytes(erows))
        rec(f, "반복 등장 요소 목록(고정 묘사·기준 이미지·잠금 상태·사용 클립)")
    acc = vst.get("accent") or {}
    sl = ["# 확정 화풍과 공통 설정", "",
          f"프리셋: {vst.get('label_ko') or '(프로젝트 화풍 없음 — 예전 방식 공통 스타일)'}",
          f"확정(잠금): {'예' if vst.get('locked') else '아니오'} / 버전 {vst.get('version', 0)}", "",
          "## 한국어 설명", vst.get("desc_ko") or st.get("look_ko", ""), "",
          "## 고정 영어 스타일 블록(모든 프롬프트에 그대로 들어감)", style_text, "",
          "## 기본 색상", ", ".join(f"{x.get('name')} {x.get('hex', '')}" for x in vst.get("palette") or []) or "-", "",
          "## 이번 회차 강조색", f"{acc.get('name') or '-'} {acc.get('hex') or ''} {acc.get('note_ko') or ''}".strip(), "",
          "## 권장 움직임", vst.get("motion_ko") or "-", "", "## 피할 요소", avoid or "-", "",
          "## 공통 제약(모든 프롬프트)", *[f"- {x}" for x in V.COMMON_CONSTRAINTS], f"- (선택지 화면) {V.CHOICE_CONSTRAINT}"]
    f = cdir / "화풍_공통설정.txt"
    f.write_text("\n".join(sl), encoding="utf-8")
    rec(f, "확정 화풍·기본 색상·강조색·공통 제약")
    f = cdir / "CapCut_편집지시.txt"
    f.write_text("\n".join(["# CapCut 편집 지시(소재 재사용·크롭·확대·정지 화면)", "",
                            "이 프로그램은 크롭·확대·정지 효과를 영상 파일에 적용하지 않았습니다. 아래 지시대로 CapCut에서 직접 적용하세요.",
                            "같은 소재를 여러 클립에서 쓰면 선택 화면과 결과 화면의 물건 모양이 같게 유지됩니다.", ""] + edit_lines),
                 encoding="utf-8")
    rec(f, "클립별 CapCut 편집 지시(프로그램이 적용하지 않음)")
    f = cdir / "검수표.csv"
    f.write_bytes(csv_bytes(review_rows))
    rec(f, "클립별 일관성 검수 상태(사람이 눈으로 확인한 결과, 자동 판정 아님)")


def precheck_flow(p: dict, clips: list[dict], add) -> None:
    by_id = {s.get("id"): s for s in (p.get("sources") or {}).get("items", [])}
    vis = p.get("visual") or {}
    if vis.get("elements") and not (vis.get("style") or {}).get("locked"):
        add("warn", "프로젝트 화풍이 확정(잠금)되지 않았습니다.", "화풍·등장요소 단계에서 확정하세요.")
    for c in clips:
        lab = f"장면 {c.get('scene_no')} 클립 {c.get('index_in_scene')}"
        m = V.resolve_media(p, c)
        rs = V.review_state(p, c)
        if not m:
            add("warn", f"{lab}: 생성한 영상(또는 정지 이미지·재사용 소재)을 넣지 않았습니다.")
            continue
        it = by_id.get(m["id"]) if m["kind"] == "item" else None
        end = float(c.get("use_start") or 0) + float(c.get("need_s") or 0)
        if it and it.get("duration") and it["duration"] + 0.05 < end:
            add("warn", f"{lab}: 영상 길이({it['duration']}초)가 사용 구간 끝({round(end, 2)}초)보다 짧습니다.",
                "더 긴 길이로 다시 생성하거나 사용 시작을 앞당기거나, CapCut에서 속도·정지 화면으로 채우세요.")
        if rs["state"] == "recheck":
            add("warn", f"{lab}: 재검토 필요 — " + ", ".join(rs["reasons"]), "검수 단계에서 다시 확인하세요(파일은 그대로 보존됨).")
        elif rs["state"] in ("todo", "fix"):
            add("warn", f"{lab}: 일관성 검수 " + ("전" if rs["state"] == "todo" else "— '수정 필요' 표시"))
        for cf in V.find_conflicts(p, c):
            add("warn", f"{lab}: 고정 설정과 충돌 — {cf['message_ko']}")
        if c.get("prompt_source") == "composed" and c.get("var_en") and V.compose(p, c)["prompt_en"] != (c.get("prompt_en") or ""):
            add("warn", f"{lab}: 저장된 최종 프롬프트가 현재 고정 블록과 다릅니다.", "영상 단계를 열면 자동으로 다시 조립됩니다.")
