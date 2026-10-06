"""로컬 웹 서버(127.0.0.1 전용). 화면은 static/, API는 /api/."""
from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import ai_client, ai_tasks, align, channel, config, export, flow, jobs, sources, storage
from . import audio as A
from .jp_text import lint_project, mora_count, overlap_report, reading_hint, wrap_lines
from .srt import build_srt, check_cues
from .util import UserError, now_iso, safe_filename, sha256_file

config.ensure_dirs()
app = FastAPI(title="JP Shorts Studio", docs_url=None, redoc_url=None, openapi_url=None)
PORT = int(os.environ.get("JPSS_PORT", "8765"))
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", "127.0.0.1", "localhost", "testserver"}
MAX_AUDIO = 200 * 1024 * 1024
MAX_SOURCE = 500 * 1024 * 1024


@app.middleware("http")
async def guard(request: Request, call_next):
    # DNS 리바인딩 방지: 로컬 호스트 이름만 허용
    host = request.headers.get("host", "")
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"error": "허용되지 않은 접근입니다."}, status_code=403)
    # 다른 웹사이트가 이 로컬 서버에 요청을 보내지 못하도록, 변경 요청에는 전용 헤더를 요구
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-jpss") != "1":
        return JSONResponse({"error": "허용되지 않은 요청입니다."}, status_code=403)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if resp.headers.get("content-type", "").startswith("text/html"):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: https:; media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'")
    return resp


@app.exception_handler(UserError)
async def user_error(_: Request, e: UserError):
    return JSONResponse({"error": e.message, "hint": e.hint}, status_code=e.status if 400 <= e.status < 600 else 400)


# ---------------------------------------------------------------- 화면

@app.get("/")
async def index():
    return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/api/health")
async def health():
    return {"ok": True, "data_dir": str(config.DATA_DIR)}


# ---------------------------------------------------------------- 설정

@app.get("/api/settings")
async def get_settings():
    return {"settings": config.load_settings(), "secrets": config.secret_status(),
            "data_dir": str(config.DATA_DIR), "config_dir": str(config.CONFIG_DIR)}


@app.put("/api/settings")
async def put_settings(body: dict):
    s = config.save_settings(body.get("settings") or {})
    return {"settings": s, "secrets": config.secret_status()}


@app.put("/api/secrets")
async def put_secrets(body: dict):
    upd = {}
    for k in ("ai_api_key", "pexels_key", "pixabay_key"):
        if k in body and isinstance(body[k], str):
            upd[k] = body[k].strip()
    if "ai_extra_headers" in body:
        h = body["ai_extra_headers"]
        if isinstance(h, dict):
            clean = {}
            for k, v in h.items():
                k = str(k).strip()
                if not k or any(c in k for c in "\r\n:") or any(c in str(v) for c in "\r\n"):
                    raise UserError("추가 헤더 이름·값에 줄바꿈이나 ':'를 넣을 수 없습니다.")
                clean[k] = str(v)
            upd["ai_extra_headers"] = clean
    config.save_secrets(upd)
    return {"secrets": config.secret_status()}


@app.post("/api/ai/test")
async def ai_test():
    ai_client.check_ready()
    job = jobs.start_async_job("ai", "AI 연결 테스트", lambda j: ai_client.test_connection())
    return job.public()


# ---------------------------------------------------------------- 작업

@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    return jobs.get(job_id).public()


@app.post("/api/jobs/{job_id}/cancel")
async def job_cancel(job_id: str):
    return jobs.cancel(job_id).public()


# ---------------------------------------------------------------- 프로젝트

def _with_status(p: dict) -> dict:
    return {"project": p, "status": storage.compute_status(p)}


@app.get("/api/projects")
async def projects():
    return {"projects": storage.list_projects()}


@app.post("/api/projects")
async def new_project(body: dict):
    base = storage.empty_project(str(body.get("name") or "새 프로젝트")[:80])
    base["idea"].update(channel.idea_from_profile())  # 채널 기본 설정으로 미리 채움
    return _with_status(storage.create_project(base["name"], base))


# ---------------------------------------------------------------- 채널·주제

@app.get("/api/channel")
async def get_channel():
    data = channel.load()
    # 주제와 연결된 프로젝트가 지워졌으면 표시만 정리
    alive = {m["id"] for m in storage.list_projects()}
    for t in data["topics"]:
        t["live_project_ids"] = [x for x in t.get("project_ids", []) if x in alive]
    return data


@app.put("/api/channel")
async def put_channel(body: dict):
    data = body.get("channel") or {}
    for t in data.get("topics") or []:
        t.pop("live_project_ids", None)
    return channel.save(data)


@app.post("/api/channel/reseed")
async def channel_reseed():
    return channel.reseed()


@app.post("/api/channel/topics")
async def channel_add_topics(body: dict):
    return channel.add_topics(str(body.get("text") or "").splitlines()[:500])


@app.post("/api/channel/topics/{tid}/start")
async def channel_start(tid: str):
    return _with_status(channel.start_project(tid))


@app.get("/api/projects/{pid}")
async def get_project(pid: str):
    return _with_status(storage.load_project(pid))


@app.put("/api/projects/{pid}")
async def put_project(pid: str, body: dict):
    p = body.get("project")
    if not isinstance(p, dict):
        raise UserError("저장할 내용이 없습니다.")
    saved = storage.save_project(pid, p, body.get("base_rev"), bool(body.get("force")))
    return _with_status(saved)


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    storage.delete_project_to_trash(pid)
    return {"ok": True, "message": f"휴지통 폴더로 옮겼습니다: {config.DATA_DIR / 'trash'}"}


@app.post("/api/projects/{pid}/duplicate")
async def duplicate(pid: str, body: dict):
    return _with_status(storage.duplicate_project(pid, body.get("mode", "all")))


@app.post("/api/projects/{pid}/backup")
async def backup(pid: str):
    f = storage.full_backup_zip(pid)
    return {"file": f.name, "folder": str(f.parent)}


@app.get("/api/projects/{pid}/backups")
async def backups(pid: str):
    return {"backups": storage.list_backups(pid)}


@app.post("/api/projects/{pid}/restore")
async def restore(pid: str, body: dict):
    return _with_status(storage.restore_backup(pid, str(body.get("name", ""))))


@app.post("/api/projects/import")
async def import_project(file: UploadFile = File(...)):
    data = await file.read()
    return _with_status(export.import_package(data))


@app.get("/api/projects/{pid}/checks")
async def checks(pid: str):
    p = storage.load_project(pid)
    others = []
    for meta in storage.list_projects():
        if meta.get("id") != pid and not meta.get("broken"):
            try:
                others.append(storage.load_project(meta["id"]))
            except UserError:
                pass
    return {"lint": lint_project(p), "overlap": overlap_report(p, others)}


@app.get("/api/projects/{pid}/media")
async def media(pid: str, path: str):
    f = storage.media_path(pid, path)
    if not f.exists() or not f.is_file():
        raise UserError("파일을 찾을 수 없습니다.", status=404)
    return FileResponse(f, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- AI 작업

@app.post("/api/projects/{pid}/ai/{task}")
async def ai_task(pid: str, task: str, body: dict):
    ai_client.check_ready()
    p = storage.load_project(pid)
    labels = {"plan": "기획 방향 제안", "script": "대본 생성", "partial": "부분 재생성", "review": "대본 검토",
              "publish": "게시 정보 생성", "flow": "Flow 영상 프롬프트"}
    if task not in labels:
        raise UserError("알 수 없는 AI 작업입니다.", status=404)

    async def run(job):
        if task == "plan":
            return await ai_tasks.run_plan(p, str(body.get("feedback") or ""), job)
        if task == "script":
            return await ai_tasks.run_script(p, str(body.get("notes") or ""), job)
        if task == "partial":
            return await ai_tasks.run_partial(p, body.get("scope") or {}, str(body.get("preset") or ""),
                                              str(body.get("instruction") or ""), job)
        if task == "review":
            return await ai_tasks.run_review(p, job)
        if task == "flow":
            ids = body.get("clip_ids") if isinstance(body.get("clip_ids"), list) else None
            return await ai_tasks.run_flow_prompts(p, ids, str(body.get("instruction") or ""), job)
        return await ai_tasks.run_publish(p, job)

    return jobs.start_async_job("ai", labels[task], run).public()


# ---------------------------------------------------------------- 음성

_audio_cache: "OrderedDict[str, tuple]" = OrderedDict()
_audio_lock = threading.Lock()


def load_audio(pid: str, meta: dict, job=None):
    key = f"{pid}:{meta['file']}:{meta.get('sha256') or meta.get('created_at')}"
    with _audio_lock:
        if key in _audio_cache:
            _audio_cache.move_to_end(key)
            return _audio_cache[key]
    f = storage.media_path(pid, meta["file"])
    if not f.exists():
        raise UserError("음성 파일이 프로젝트 폴더에 없습니다.", "음성을 다시 넣어 주세요.")
    data = A.decode(f, int(meta["sr"]), int(meta["channels"]), cancel_check=job.cancelled if job else None)
    with _audio_lock:
        _audio_cache[key] = (data, int(meta["sr"]))
        while len(_audio_cache) > 4:
            _audio_cache.popitem(last=False)
    return data, int(meta["sr"])


def import_audio_bytes(pid: str, name: str, data: bytes) -> dict:
    p = storage.load_project(pid)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ("wav", "mp3", "m4a", "aac", "ogg", "flac"):
        raise UserError(f"지원하지 않는 음성 형식입니다(.{ext}).", "타입캐스트에서 WAV 또는 MP3로 내려받아 넣어 주세요.")
    if len(data) > MAX_AUDIO:
        raise UserError("음성 파일이 너무 큽니다(200MB 초과).")
    n = len(list((storage.project_dir(pid) / "media" / "audio").glob("original_*"))) + 1
    rel = f"media/audio/original_{n:03d}.{ext}"
    dest = storage.media_path(pid, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    try:
        info = A.probe(dest)
        arr = A.decode(dest, info["sr"], info["channels"])
    except UserError:
        dest.unlink(missing_ok=True)
        raise
    dur = len(arr) / info["sr"]
    if dur < 0.3:
        dest.unlink(missing_ok=True)
        raise UserError("음성이 너무 짧습니다(0.3초 미만).")
    db = A.frame_db(A.mono_float(arr), info["sr"])
    lv = A.levels(db)
    warnings = []
    if lv["floor_db"] > -50:
        warnings.append("조용한 부분의 소음이 큽니다(배경음악·잡음 가능). 무음 판단이 부정확할 수 있으니 BGM 없는 타입캐스트 음성을 권장합니다.")
    if float(abs(arr.astype("int32")).max()) >= 32760:
        warnings.append("소리가 최대치에 닿는 부분(클리핑)이 있습니다.")
    return {"file": rel, "orig_name": safe_filename(name, "audio"), "sha256": sha256_file(dest),
            "duration": round(dur, 3), "sr": info["sr"], "channels": info["channels"], "codec": info["codec"],
            "imported_at": now_iso(), "script_tts_hash": storage.tts_hash(p), "levels": lv, "warnings": warnings,
            "size": len(data)}


@app.post("/api/projects/{pid}/audio/upload")
def audio_upload(pid: str, file: UploadFile = File(...)):
    data = file.file.read(MAX_AUDIO + 1)
    return {"original": import_audio_bytes(pid, file.filename or "audio", data)}


@app.post("/api/examples/import")
def import_example():
    ex = config.STATIC_DIR.parent / "examples"
    data = json.loads((ex / "example_project.json").read_text(encoding="utf-8"))
    base = storage.empty_project(data.get("name", "예제"))
    for k, v in data.items():
        base[k] = v
    p = storage.create_project(base["name"], base)
    audio_file = ex / "example_voice.wav"
    if audio_file.exists():
        meta = import_audio_bytes(p["id"], audio_file.name, audio_file.read_bytes())
        meta["warnings"] = list(meta["warnings"]) + ["예제 음성은 검증용 합성 음성입니다(타입캐스트 음성 아님, 게시용 아님)."]
        p["audio"]["original"] = meta
        p = storage.save_project(p["id"], p, p["rev"])
    return _with_status(p)


def hold_ranges(project: dict, data, sr: int, params: dict) -> list[list]:
    """대본 장면의 '의도적 멈춤(hold_ms)'을 실제 음성의 쉼 위치에 대응시켜 남길 길이로 지정한다.
    원본 음성에서 문장 경계를 먼저 찾고, 해당 장면 마지막 문장 뒤의 쉼을 hold_ms 길이로 남긴다."""
    lines, holds = [], {}
    for si, sc in enumerate((project.get("script") or {}).get("scenes", [])):
        ls = [ln for ln in sc.get("lines", []) if (ln.get("tts") or "").strip()]
        lines.extend(ls)
        if ls and int(sc.get("hold_ms") or 0) > 0:
            holds[len(lines) - 1] = (int(sc["hold_ms"]), si + 1)
    if not holds:
        return []
    try:
        thr = A.threshold_for(A.frame_db(A.mono_float(data), sr), params)
        islands, db = align.get_islands(data, sr, thr)
        times = align.align_by_pauses(islands, db, [float(mora_count(ln["tts"])) for ln in lines])
    except UserError:
        return []
    out = []
    for i, (ms, scene_no) in holds.items():
        if i + 1 < len(times):
            out.append([times[i]["end"], times[i + 1]["start"], ms, f"장면 {scene_no} 의도적 멈춤"])
    return out


def _silence_args(body: dict):
    mode = body.get("mode") if body.get("mode") in ("natural", "fast", "custom") else "natural"
    params = A.resolve_params(mode, body.get("params") or {})
    protect = [r for r in (body.get("protect_ranges") or []) if isinstance(r, list) and len(r) >= 2]
    custom = [r for r in (body.get("custom_ranges") or []) if isinstance(r, list) and len(r) >= 3]
    return params, protect, custom


@app.post("/api/projects/{pid}/audio/analyze")
async def audio_analyze(pid: str, body: dict):
    p = storage.load_project(pid)
    orig = (p.get("audio") or {}).get("original")
    if not orig:
        raise UserError("먼저 음성 파일을 넣으세요.")
    params, protect, custom = _silence_args(body)
    auto_holds = body.get("auto_holds", True) is not False

    def run(job):
        job.report(0.1, "음성 불러오는 중…")
        data, sr = load_audio(pid, orig, job)
        job.check()
        job.report(0.5, "무음 구간 분석 중…")
        holds = hold_ranges(p, data, sr, params) if auto_holds else []
        res = A.analyze(data, sr, params, protect, custom + holds)
        res["auto_holds"] = holds
        res["peaks"] = A.peaks(data)
        return res

    return jobs.start_thread_job("audio", "무음 분석", run).public()


@app.post("/api/projects/{pid}/audio/apply")
async def audio_apply(pid: str, body: dict):
    p = storage.load_project(pid)
    orig = (p.get("audio") or {}).get("original")
    if not orig:
        raise UserError("먼저 음성 파일을 넣으세요.")
    params, protect, custom = _silence_args(body)
    use_original = bool(body.get("use_original"))
    auto_holds = body.get("auto_holds", True) is not False

    def run(job):
        job.report(0.1, "음성 불러오는 중…")
        data, sr = load_audio(pid, orig, job)
        job.check()
        if use_original:
            res = {"regions": [], "threshold_db": A.threshold_for(A.frame_db(A.mono_float(data), sr), params),
                   "removed_s": 0.0, "duration": round(len(data) / sr, 3)}
        else:
            job.report(0.3, "무음 구간 분석 중…")
            custom_all = custom + (hold_ranges(p, data, sr, params) if auto_holds else [])
            res = A.analyze(data, sr, params, protect, custom_all)
        job.check()
        job.report(0.6, "무음 정리 적용 중…")
        out, segments = A.apply_plan(data, sr, res["regions"])
        job.check()
        job.report(0.8, "발음 잘림 검증 중…")
        ver = A.verify_cut(data, sr, res["regions"], res["threshold_db"], out, segments)
        prev = [x for x in (storage.project_dir(pid) / "media" / "audio").glob("final_v*.wav")]
        version = max([int(x.stem.split("_v")[-1]) for x in prev if x.stem.split("_v")[-1].isdigit()] + [0]) + 1
        rel = f"media/audio/final_v{version:03d}.wav"
        dest = storage.media_path(pid, rel)
        A.write_wav(dest, out, sr)
        ok, err = A.verify_playable(dest)
        if not ok:
            raise UserError("처리된 음성 파일을 확인하지 못했습니다: " + err)
        return {"processed": {
            "file": rel, "version": version, "duration": round(len(out) / sr, 3), "sr": sr,
            "channels": int(out.shape[1]), "source_sha": orig["sha256"], "source_file": orig["file"],
            "created_at": now_iso(), "mode": "original" if use_original else params["mode"], "params": params,
            "protect_ranges": protect, "custom_ranges": custom, "threshold_db": res["threshold_db"],
            "original_duration": res["duration"], "removed_s": res["removed_s"],
            "regions_changed": sum(1 for r in res["regions"] if r.get("remove")),
            "uncertain": sum(1 for r in res["regions"] if r.get("flags")),
            "segments": segments, "verify": ver, "sha256": sha256_file(dest)}}

    return jobs.start_thread_job("audio", "무음 정리 적용", run).public()


@app.get("/api/projects/{pid}/audio/peaks")
def audio_peaks(pid: str, which: str = "original"):
    p = storage.load_project(pid)
    meta = (p.get("audio") or {}).get("processed" if which == "processed" else "original")
    if not meta:
        raise UserError("음성이 없습니다.", status=404)
    data, sr = load_audio(pid, meta)
    return {"peaks": A.peaks(data), "duration": len(data) / sr}


# ---------------------------------------------------------------- 자막

def cue_key(c: dict) -> str:
    return f"{c.get('line_id')}#{c.get('part', 0)}"


def diff_cues(old: list[dict], new: list[dict]) -> dict:
    old_by = {cue_key(c): c for c in old}
    changes, manual = [], []
    for c in new:
        o = old_by.get(cue_key(c))
        if not o:
            changes.append({"key": cue_key(c), "type": "added", "new_text": c["text"], "new_start": c["start"], "new_end": c["end"]})
            continue
        if o.get("manual_text") or o.get("manual_time"):
            manual.append({"key": cue_key(c), "old_text": o.get("text"), "new_text": c["text"],
                           "old_start": o.get("start"), "old_end": o.get("end"),
                           "new_start": c["start"], "new_end": c["end"],
                           "manual_text": bool(o.get("manual_text")), "manual_time": bool(o.get("manual_time"))})
        if o.get("text") != c["text"] or abs(o.get("start", 0) - c["start"]) > 0.02 or abs(o.get("end", 0) - c["end"]) > 0.02:
            changes.append({"key": cue_key(c), "type": "changed", "old_text": o.get("text"), "new_text": c["text"],
                            "old_start": o.get("start"), "new_start": c["start"], "old_end": o.get("end"), "new_end": c["end"]})
    new_keys = {cue_key(c) for c in new}
    for k, o in old_by.items():
        if k not in new_keys:
            changes.append({"key": k, "type": "removed", "old_text": o.get("text")})
    merged = []
    for c in new:
        o = old_by.get(cue_key(c))
        if o and o.get("manual_text"):
            merged.append(dict(c, text=o["text"], manual_text=True))
        else:
            merged.append(c)
    return {"changes": changes, "manual": manual, "merged_keep_manual_text": merged}


@app.post("/api/projects/{pid}/subtitles/align")
async def subtitles_align(pid: str, body: dict):
    p = storage.load_project(pid)
    proc = (p.get("audio") or {}).get("processed")
    if not proc:
        raise UserError("최종 음성이 없습니다.", "음성 단계에서 무음 정리를 적용하거나 '원본 그대로 사용'을 누르세요.")
    use_asr = bool(body.get("use_asr"))

    def run(job):
        data, sr = load_audio(pid, proc, job)
        job.check()
        res = align.align_project(p, data, sr, float(proc.get("threshold_db") or -55),
                                  wav_path=storage.media_path(pid, proc["file"]), use_asr=use_asr, job=job)
        res["diff"] = diff_cues((p.get("subtitles") or {}).get("cues") or [], res["cues"])
        res["audio_version"] = proc.get("version")
        res["display_hash"] = storage.display_hash(p)
        res["issues"] = check_cues(res["cues"], proc.get("duration"), config.load_settings()["subtitle"])
        return res

    return jobs.start_thread_job("align", "자막 시간 맞추기", run).public()


@app.post("/api/projects/{pid}/subtitles/check")
async def subtitles_check(pid: str, body: dict):
    p = storage.load_project(pid)
    proc = (p.get("audio") or {}).get("processed") or {}
    cues = body.get("cues") if isinstance(body.get("cues"), list) else (p.get("subtitles") or {}).get("cues") or []
    return {"issues": check_cues(cues, proc.get("duration"), config.load_settings()["subtitle"])}


@app.get("/api/projects/{pid}/subtitles/srt")
async def subtitles_srt(pid: str):
    p = storage.load_project(pid)
    return PlainTextResponse(build_srt((p.get("subtitles") or {}).get("cues") or []), media_type="text/plain; charset=utf-8")


@app.get("/api/whisper/status")
async def whisper_status():
    return align.whisper_status()


@app.post("/api/jp/tools")
async def jp_tools(body: dict):
    text = str(body.get("text") or "")[:2000]
    max_chars = int(config.load_settings()["subtitle"].get("max_line_chars", 14))
    return {"reading": reading_hint(text), "wrapped": "\n".join(wrap_lines(text, max_chars))}


# ---------------------------------------------------------------- 영상소스

@app.get("/api/sources/status")
async def sources_status():
    return sources.providers_status()


@app.post("/api/projects/{pid}/sources/upload")
def sources_upload(pid: str, files: list[UploadFile] = File(...), scene_id: str = Form(""), clip_id: str = Form("")):
    storage.load_project(pid)
    items, errors = [], []
    for f in files:
        data = f.file.read(MAX_SOURCE + 1)
        if len(data) > MAX_SOURCE:
            errors.append({"name": f.filename, "error": "파일이 너무 큽니다(500MB 초과)."})
            continue
        try:
            items.append(sources.add_local_file(pid, f.filename or "file", data, scene_id or None, clip_id or None))
        except UserError as e:
            errors.append({"name": f.filename, "error": e.message, "hint": e.hint})
    return {"items": items, "errors": errors}


@app.post("/api/sources/search")
async def sources_search(body: dict):
    return await sources.search(str(body.get("provider")), str(body.get("query") or ""),
                                "video" if body.get("media") != "image" else "image", int(body.get("page") or 1))


@app.post("/api/projects/{pid}/sources/download")
async def sources_download(pid: str, body: dict):
    storage.load_project(pid)
    cand = body.get("candidate") or {}
    scene_id = body.get("scene_id")
    return jobs.start_async_job("download", "소스 내려받기",
                                lambda job: sources.download(pid, cand, scene_id, job)).public()


# ---------------------------------------------------------------- Google Flow 클립

@app.post("/api/projects/{pid}/flow/plan")
async def flow_plan(pid: str):
    p = storage.load_project(pid)
    if not storage.all_lines(p):
        raise UserError("대본이 있어야 클립을 계획할 수 있습니다.")
    return flow.plan_clips(p, (p.get("flow") or {}).get("clips") or [])


@app.post("/api/projects/{pid}/flow/lastframe")
def flow_lastframe(pid: str, body: dict):
    p = storage.load_project(pid)
    item = next((i for i in (p.get("sources") or {}).get("items", []) if i.get("id") == body.get("item_id")), None)
    if not item or item.get("kind") != "video":
        raise UserError("영상 파일을 찾을 수 없습니다.")
    src = storage.media_path(pid, item["file"])
    rel = f"media/flow/frames/{item['id']}_last.png"
    dest = storage.media_path(pid, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    from .util import ffmpeg_exe, run_tool
    r = run_tool([ffmpeg_exe(), "-y", "-hide_banner", "-nostdin", "-v", "error", "-sseof", "-0.2", "-i", str(src),
                  "-frames:v", "1", "-update", "1", str(dest)], timeout=120)
    if r.returncode != 0 or not dest.exists():
        raise UserError("마지막 프레임을 뽑지 못했습니다.", r.stderr.decode("utf-8", "replace")[:200])
    return {"file": rel}


# ---------------------------------------------------------------- 내보내기

@app.get("/api/projects/{pid}/export/precheck")
async def export_precheck(pid: str):
    return export.precheck(pid)


@app.post("/api/projects/{pid}/export")
async def do_export(pid: str, body: dict):
    def run(job):
        job.report(0.1, "파일을 확인하고 묶는 중…")
        return export.build_package(pid, bool(body.get("zip", True)), bool(body.get("allow_stale")))

    return jobs.start_thread_job("export", "내보내기", run).public()


@app.post("/api/projects/{pid}/open-folder")
async def open_folder(pid: str, body: dict):
    base = storage.project_dir(pid)
    target = body.get("path")
    if target:
        t = Path(str(target))
        from .util import inside

        folder = inside(base, t if t.is_absolute() else base / t)
    else:
        folder = base / "exports"
    if folder.is_file():
        folder = folder.parent
    if not folder.exists():
        raise UserError("폴더가 없습니다.", status=404)
    if os.name == "nt":
        os.startfile(str(folder))  # noqa: S606 - 프로젝트 폴더 내부 경로만 허용
    return {"ok": True, "folder": str(folder)}
