"""공용 유틸: 안전한 경로, ID, 해시, 외부 프로그램 실행."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import time
import unicodedata
from datetime import datetime
from pathlib import Path


class UserError(Exception):
    """사용자에게 그대로 보여줄 한국어 오류. hint는 해결 방법."""

    def __init__(self, message: str, hint: str = "", status: int = 400):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.status = status


PROJECT_ID_RE = re.compile(r"^p_[0-9]{8}_[0-9]{6}_[a-f0-9]{6}$")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def new_project_id() -> str:
    return "p_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)


def check_project_id(pid: str) -> str:
    if not isinstance(pid, str) or not PROJECT_ID_RE.match(pid):
        raise UserError("잘못된 프로젝트 ID입니다.", status=404)
    return pid


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name: str, default: str = "file", max_len: int = 60) -> str:
    """사용자 파일명을 저장용 이름으로 정리. 경로 구분자·제어문자·예약어 제거.
    결과는 항상 영숫자/일본어/한글 등으로 시작해 명령행 옵션으로 해석될 수 없다."""
    name = unicodedata.normalize("NFC", str(name or ""))
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name)
    name = name.strip(" .-_")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = stem[:max_len].strip(" .-_") or default
    if stem.upper() in _WIN_RESERVED:
        stem = "_" + stem
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:8].lower()
    return f"{stem}.{ext}" if ext else stem


def inside(base: Path, target: Path) -> Path:
    """target이 base 내부인지 확인하고 절대경로를 돌려준다(경로 탈출 방지)."""
    base_r = base.resolve()
    t = target.resolve()
    if t != base_r and base_r not in t.parents:
        raise UserError("허용되지 않은 파일 경로입니다.", status=403)
    return t


def ffmpeg_exe() -> str:
    env = os.environ.get("JPSS_FFMPEG")
    if env and Path(env).exists():
        return env
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # pragma: no cover
        raise UserError("ffmpeg를 찾을 수 없습니다.", "setup.bat을 다시 실행해 필요한 패키지를 설치하세요.") from e


_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def run_tool(args: list[str], timeout: float = 600, input_bytes: bytes | None = None,
             cancel_check=None) -> subprocess.CompletedProcess:
    """외부 프로그램 실행. 항상 인자 리스트로 실행하고 shell을 쓰지 않는다
    (파일명·사용자 입력이 명령어로 해석되지 않음). cancel_check()가 True면 중단."""
    assert isinstance(args, list) and all(isinstance(a, str) for a in args)
    proc = subprocess.Popen(
        args,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        creationflags=_CREATE_NO_WINDOW,
    )
    if cancel_check is None:
        try:
            out, err = proc.communicate(input=input_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise UserError("처리 시간이 너무 오래 걸려 중단했습니다.", "파일 길이를 확인하고 다시 시도하세요.")
        return subprocess.CompletedProcess(args, proc.returncode, out, err)
    # 취소 가능한 실행: 짧게 나눠 기다린다
    import threading

    result: dict = {}

    def _comm():
        result["out"], result["err"] = proc.communicate(input=input_bytes)

    t = threading.Thread(target=_comm, daemon=True)
    t.start()
    start = time.time()
    while t.is_alive():
        t.join(0.2)
        if cancel_check():
            proc.kill()
            t.join(2)
            raise UserError("사용자가 작업을 취소했습니다.", status=499)
        if time.time() - start > timeout:
            proc.kill()
            t.join(2)
            raise UserError("처리 시간이 너무 오래 걸려 중단했습니다.")
    return subprocess.CompletedProcess(args, proc.returncode, result.get("out", b""), result.get("err", b""))


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
