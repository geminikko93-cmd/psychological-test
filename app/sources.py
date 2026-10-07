"""영상·이미지 소스.

- 사용자 파일 가져오기(필수 기능)
- Pexels / Pixabay 공식 API 검색·다운로드(각 사이트에서 무료 API 키를 받아 설정에 넣은 경우에만)
  * 검색 결과는 '후보'일 뿐이며, 파일을 실제로 내려받아 확인한 뒤에만 '확보됨'으로 기록한다.
  * 작가·원본 페이지·라이선스 안내를 함께 저장해 크레딧을 만든다.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

import httpx

from . import config, storage
from .util import UserError, ffmpeg_exe, new_id, now_iso, run_tool, safe_filename, sha256_file

ALLOWED_EXT = {"mp4", "mov", "m4v", "webm", "jpg", "jpeg", "png", "webp", "gif"}
VIDEO_EXT = {"mp4", "mov", "m4v", "webm"}
MAX_DOWNLOAD = 300 * 1024 * 1024

LICENSE_NOTES = {
    "pexels": "Pexels License: 무료 사용 가능·출처 표기 의무 없음(권장). 식별 가능한 인물·상표·예술작품은 별도 주의. 원본 페이지에서 조건을 직접 확인하세요.",
    "pixabay": "Pixabay Content License: 무료 사용 가능·출처 표기 의무 없음. 원본 그대로 재판매 금지, 인물·상표 주의. 원본 페이지에서 조건을 직접 확인하세요.",
}


def media_kind(ext: str) -> str:
    return "video" if ext.lower() in VIDEO_EXT else "image"


def verify_media(path: Path) -> tuple[bool, str]:
    """파일이 실제로 열리는 영상·이미지인지 확인."""
    if not path.exists() or path.stat().st_size == 0:
        return False, "파일이 없거나 비어 있습니다."
    r = run_tool([ffmpeg_exe(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-frames:v", "1",
                  "-f", "null", "-"], timeout=120)
    err = r.stderr.decode("utf-8", "replace").strip()
    if r.returncode != 0:
        return False, "영상·이미지로 열 수 없습니다: " + err[:200]
    return True, ""


FLOW_LICENSE = ("Google Flow로 직접 생성한 영상입니다. Flow·Google 생성형 AI 이용약관을 확인하고, "
                "사실적인 AI 영상이면 YouTube '변경되거나 합성된 콘텐츠' 공개, Instagram AI 라벨이 필요한지 게시할 때 확인하세요.")


def media_duration(path: Path) -> float | None:
    import re
    r = run_tool([ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path)], timeout=60)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr.decode("utf-8", "replace"))
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None


def add_local_file(pid: str, filename: str, data: bytes, scene_id: str | None, clip_id: str | None = None) -> dict:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXT:
        raise UserError(f"지원하지 않는 파일 형식입니다(.{ext}).", "MP4·MOV·WEBM 영상 또는 JPG·PNG·WEBP 이미지를 넣어 주세요.")
    item_id = new_id("src")
    name = f"{item_id}_{safe_filename(filename, 'source')}"
    dest = storage.media_path(pid, f"media/sources/{name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    ok, err = verify_media(dest)
    if not ok:
        dest.unlink(missing_ok=True)
        raise UserError("파일을 열 수 없어 추가하지 않았습니다.", err)
    kind = media_kind(ext)
    dur = media_duration(dest) if kind == "video" else None
    flow = bool(clip_id)
    return {"id": item_id, "scene_id": scene_id, "clip_id": clip_id, "provider": "flow" if flow else "local", "kind": kind,
            "file": f"media/sources/{name}", "orig_name": filename, "status": "acquired",
            "page_url": "", "download_url": "", "author": "", "author_url": "",
            "license_note": FLOW_LICENSE if flow else "직접 넣은 파일입니다. 사용 권리(직접 촬영·구매·라이선스)를 확인해 메모하세요.",
            "credit_text": "", "ai_generated": flow, "rights_checked": False, "memo": "", "added_at": now_iso(),
            "size": dest.stat().st_size, "duration": round(dur, 2) if dur else None, "sha256": sha256_file(dest)}


def _keys() -> dict:
    return config.load_secrets()


def providers_status() -> dict:
    k = _keys()
    return {"pexels": bool(k.get("pexels_key")), "pixabay": bool(k.get("pixabay_key"))}


async def search(provider: str, query: str, media: str = "video", page: int = 1) -> dict:
    query = (query or "").strip()[:100]
    if not query:
        raise UserError("검색어를 입력하세요.")
    keys = _keys()
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            if provider == "pexels":
                key = keys.get("pexels_key")
                if not key:
                    raise UserError("Pexels API 키가 설정되어 있지 않습니다.", "pexels.com/api 에서 무료 키를 받아 [설정]에 입력하세요.", 428)
                url = "https://api.pexels.com/videos/search" if media == "video" else "https://api.pexels.com/v1/search"
                r = await c.get(url, headers={"Authorization": key},
                                params={"query": query, "orientation": "portrait", "per_page": 15, "page": page})
                if r.status_code != 200:
                    raise _http_error("Pexels", r.status_code)
                return {"items": _pexels_items(r.json(), media)}
            if provider == "pixabay":
                key = keys.get("pixabay_key")
                if not key:
                    raise UserError("Pixabay API 키가 설정되어 있지 않습니다.", "pixabay.com/api/docs 에서 무료 키를 받아 [설정]에 입력하세요.", 428)
                url = "https://pixabay.com/api/videos/" if media == "video" else "https://pixabay.com/api/"
                params = {"key": key, "q": query, "per_page": 15, "page": page, "safesearch": "true"}
                if media != "video":
                    params["orientation"] = "vertical"
                r = await c.get(url, params=params)
                if r.status_code != 200:
                    raise _http_error("Pixabay", r.status_code)
                return {"items": _pixabay_items(r.json(), media)}
    except httpx.HTTPError as e:
        raise UserError("소스 사이트에 연결하지 못했습니다.", "인터넷 연결을 확인하세요.", 502) from e
    raise UserError("알 수 없는 소스 사이트입니다.")


def _http_error(name: str, code: int) -> UserError:
    if code in (401, 403):
        return UserError(f"{name} API 키가 올바르지 않습니다.", "설정에서 키를 다시 확인하세요.", 502)
    if code == 429:
        return UserError(f"{name} 검색 한도에 걸렸습니다.", "잠시 뒤 다시 시도하세요.", 502)
    return UserError(f"{name} 검색 중 오류가 발생했습니다(HTTP {code}).", "", 502)


def _pexels_items(j: dict, media: str) -> list[dict]:
    out = []
    if media == "video":
        for v in j.get("videos", []):
            files = [f for f in v.get("video_files", []) if f.get("link") and f.get("file_type") == "video/mp4"]
            # 세로 1080 근처 우선, 너무 큰 파일 회피
            files.sort(key=lambda f: (abs((f.get("height") or 0) - 1920), -(f.get("width") or 0)))
            if not files:
                continue
            f = files[0]
            user = v.get("user") or {}
            out.append({"provider": "pexels", "kind": "video", "remote_id": str(v.get("id")),
                        "thumb": v.get("image"), "page_url": v.get("url"), "download_url": f.get("link"),
                        "width": f.get("width"), "height": f.get("height"), "duration": v.get("duration"),
                        "author": user.get("name", ""), "author_url": user.get("url", ""), "ext": "mp4"})
    else:
        for p in j.get("photos", []):
            src = p.get("src") or {}
            out.append({"provider": "pexels", "kind": "image", "remote_id": str(p.get("id")),
                        "thumb": src.get("medium"), "page_url": p.get("url"),
                        "download_url": src.get("portrait") or src.get("large2x") or src.get("original"),
                        "width": p.get("width"), "height": p.get("height"), "author": p.get("photographer", ""),
                        "author_url": p.get("photographer_url", ""), "ext": "jpg"})
    return out


def _pixabay_items(j: dict, media: str) -> list[dict]:
    out = []
    for h in j.get("hits", []):
        if media == "video":
            vids = h.get("videos") or {}
            best = None
            for k in ("large", "medium", "small"):
                if (vids.get(k) or {}).get("url"):
                    best = vids[k]
                    break
            if not best:
                continue
            out.append({"provider": "pixabay", "kind": "video", "remote_id": str(h.get("id")),
                        "thumb": best.get("thumbnail") or (vids.get("tiny") or {}).get("thumbnail"),
                        "page_url": h.get("pageURL"), "download_url": best.get("url"),
                        "width": best.get("width"), "height": best.get("height"), "duration": h.get("duration"),
                        "author": h.get("user", ""), "author_url": f"https://pixabay.com/users/{h.get('user', '')}-{h.get('user_id', '')}/",
                        "ext": "mp4"})
        else:
            out.append({"provider": "pixabay", "kind": "image", "remote_id": str(h.get("id")),
                        "thumb": h.get("webformatURL"), "page_url": h.get("pageURL"),
                        "download_url": h.get("largeImageURL") or h.get("webformatURL"),
                        "width": h.get("imageWidth"), "height": h.get("imageHeight"), "author": h.get("user", ""),
                        "author_url": f"https://pixabay.com/users/{h.get('user', '')}-{h.get('user_id', '')}/",
                        "ext": (h.get("largeImageURL") or "x.jpg").rsplit(".", 1)[-1][:4].lower()})
    return out


ALLOWED_DOWNLOAD_HOSTS = ("pexels.com", "pixabay.com", "vimeo.com", "vimeocdn.com", "akamaized.net", "cdn.pixabay.com")


async def download(pid: str, cand: dict, scene_id: str | None, job=None) -> dict:
    provider = cand.get("provider")
    url = str(cand.get("download_url") or "")
    if provider not in ("pexels", "pixabay") or not url.startswith("https://"):
        raise UserError("내려받을 수 없는 후보입니다.")
    host = httpx.URL(url).host or ""
    if not any(host == h or host.endswith("." + h) for h in ALLOWED_DOWNLOAD_HOSTS):
        raise UserError("허용되지 않은 다운로드 주소입니다.")
    ext = str(cand.get("ext") or "mp4").lower()
    if ext not in ALLOWED_EXT:
        ext = "mp4" if cand.get("kind") == "video" else "jpg"
    item_id = new_id("src")
    name = f"{item_id}_{provider}_{safe_filename(str(cand.get('remote_id') or 'x'), 'x')}.{ext}"
    dest = storage.media_path(pid, f"media/sources/{name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=True) as c:
            async with c.stream("GET", url) as r:
                if r.status_code != 200:
                    raise UserError(f"파일을 내려받지 못했습니다(HTTP {r.status_code}).", "다른 후보를 선택하세요.", 502)
                clen = int(r.headers.get("content-length") or 0)
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 16):
                        total += len(chunk)
                        if total > MAX_DOWNLOAD:
                            raise UserError("파일이 너무 큽니다(300MB 초과).", "더 작은 해상도의 후보를 고르세요.")
                        f.write(chunk)
                        if job and clen:
                            job.report(total / clen, f"내려받는 중… {total // 1024 // 1024}MB")
    except httpx.HTTPError as e:
        dest.unlink(missing_ok=True)
        raise UserError("다운로드 중 연결이 끊겼습니다.", "다시 시도하세요.", 502) from e
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    ok, err = verify_media(dest)
    if not ok:
        dest.unlink(missing_ok=True)
        raise UserError("내려받은 파일을 열 수 없어 확보하지 않았습니다.", err)
    site = "Pexels" if provider == "pexels" else "Pixabay"
    author = cand.get("author") or ""
    return {"id": item_id, "scene_id": scene_id, "provider": provider, "kind": cand.get("kind") or media_kind(ext),
            "file": f"media/sources/{name}", "orig_name": name, "status": "acquired",
            "page_url": cand.get("page_url") or "", "download_url": url, "author": author,
            "author_url": cand.get("author_url") or "", "license_note": LICENSE_NOTES[provider],
            "credit_text": f"{site}: {author}" + (f" ({cand.get('page_url')})" if cand.get("page_url") else ""),
            "rights_checked": False, "memo": "", "added_at": now_iso(), "size": total,
            "mime": mimetypes.guess_type(name)[0] or ""}
