"""앤트로픽 API 중개서버 클라이언트 (Anthropic Messages 규격 전용).

사용자 확인 결과 중개서버는 Anthropic Messages 규격이다. 따라서 이 규격만 구현한다.
- 서버 주소·경로·모델·인증 방식·추가 헤더는 모두 사용자 설정값을 쓴다(하드코딩 없음).
- 중개서버가 경로·인증을 공식 서버와 다르게 쓸 수 있어 SDK 대신 httpx로 직접 호출한다.
- 자동 재시도 없음. 실패하면 이유와 해결 방법을 돌려주고 사용자가 다시 누른다.
- API 키는 요청 헤더에만 쓰이고 로그·응답·프로젝트에 남지 않는다.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from . import config
from .util import UserError


def _redact(text: str) -> str:
    for s in config.all_secret_values():
        text = text.replace(s, "****")
    return text


def _settings() -> tuple[dict, dict]:
    s = config.load_settings()["ai"]
    sec = config.load_secrets()
    return s, sec


def check_ready() -> None:
    s, sec = _settings()
    missing = []
    if not (s.get("base_url") or "").strip():
        missing.append("서버 기본 주소")
    if not (s.get("model") or "").strip():
        missing.append("모델 식별자")
    key, _, _ = config.ai_key()
    if s.get("auth") != "none" and not key:
        missing.append("API 키(.env의 ANTHROPIC_AUTH_TOKEN 또는 설정 화면)")
    if s.get("auth") == "custom" and not (s.get("custom_header_name") or "").strip():
        missing.append("인증 헤더 이름")
    if missing:
        raise UserError("AI 연결 설정이 필요합니다: " + ", ".join(missing),
                        "왼쪽 아래 [설정] → AI 연결에서 값을 입력하고 [연결 테스트]를 눌러 주세요.", status=428)


def _build(system: str, messages: list[dict], max_tokens: int | None, stream: bool) -> tuple[str, dict, dict, float]:
    s, sec = _settings()
    base = s["base_url"].strip().rstrip("/")
    if not base.lower().startswith(("http://", "https://")):
        raise UserError("서버 기본 주소는 http:// 또는 https:// 로 시작해야 합니다.", status=428)
    path = (s.get("path") or "/v1/messages").strip()
    if not path.startswith("/"):
        path = "/" + path
    url = base + path
    headers = {"content-type": "application/json", "accept": "application/json"}
    if s.get("send_version_header", True) and s.get("anthropic_version"):
        headers["anthropic-version"] = str(s["anthropic_version"]).strip()
    key, _, env_auth = config.ai_key()
    auth = env_auth or s.get("auth", "x-api-key")
    if auth == "x-api-key":
        headers["x-api-key"] = key
    elif auth == "bearer":
        headers["authorization"] = f"Bearer {key}"
    elif auth == "custom":
        headers[s["custom_header_name"].strip()] = key
    extra = sec.get("ai_extra_headers") or {}
    if isinstance(extra, dict):
        for k, v in extra.items():
            if k and v is not None:
                headers[str(k)] = str(v)
    body: dict[str, Any] = {
        "model": s["model"].strip(),
        "max_tokens": int(max_tokens or s.get("max_tokens") or 8000),
        "messages": messages,
    }
    if system:
        body["system"] = system
    raw_extra = (s.get("extra_body_json") or "").strip()
    if raw_extra:
        try:
            extra_body = json.loads(raw_extra)
        except ValueError as e:
            raise UserError("설정의 '추가 요청 본문(JSON)'이 올바른 JSON이 아닙니다.", status=428) from e
        if not isinstance(extra_body, dict):
            raise UserError("'추가 요청 본문(JSON)'은 { } 객체여야 합니다.", status=428)
        for k, v in extra_body.items():
            if k not in ("messages", "model", "system"):
                body[k] = v
    if stream:
        body["stream"] = True
    timeout = float(s.get("timeout_s") or 180)
    return url, headers, body, timeout


def _error_from_response(status: int, text: str, headers: httpx.Headers) -> UserError:
    etype, emsg = "", ""
    try:
        j = json.loads(text)
        err = j.get("error") if isinstance(j, dict) else None
        if isinstance(err, dict):
            etype, emsg = str(err.get("type", "")), str(err.get("message", ""))
        elif isinstance(j, dict) and "message" in j:
            emsg = str(j.get("message"))
    except ValueError:
        emsg = text[:200]
    detail = _redact(f" (서버 메시지: {emsg[:300]})" if emsg else "")
    retry_after = headers.get("retry-after")
    if status == 401 or etype == "authentication_error":
        return UserError("인증에 실패했습니다." + detail, "API 키를 확인하세요(.env의 ANTHROPIC_AUTH_TOKEN 값, 앞뒤 공백·따옴표 주의). 화면에서 키를 넣었다면 인증 방식(x-api-key / Bearer)도 확인하세요.", 502)
    if status == 403 or etype == "permission_error":
        return UserError("이 키로는 요청 권한이 없습니다." + detail, "중개서버에서 이 모델·기능 사용이 허용되는지 확인하세요.", 502)
    if status == 404 or etype == "not_found_error":
        return UserError("요청한 주소나 모델을 찾지 못했습니다." + detail, "서버 기본 주소, 요청 경로(/v1/messages 등), 모델 식별자를 확인하세요.", 502)
    if status == 402 or etype == "billing_error":
        return UserError("결제·크레딧 문제로 요청이 거절되었습니다." + detail, "중개서버 또는 계정의 잔액·결제 상태를 확인하세요.", 502)
    if status == 413 or etype == "request_too_large":
        return UserError("요청이 너무 큽니다." + detail, "참고할 이전 프로젝트 수를 줄이거나 메모를 짧게 하세요.", 502)
    if status == 429 or etype == "rate_limit_error":
        wait = f" 약 {retry_after}초 뒤" if retry_after else " 잠시 뒤"
        return UserError("사용 한도(요청 속도 또는 사용량)에 걸렸습니다." + detail,
                         f"{wait}에 다시 시도하세요. 자동 재시도는 하지 않습니다. 계속되면 중개서버의 한도를 확인하세요.", 502)
    if status == 529 or etype == "overloaded_error":
        return UserError("AI 서버가 일시적으로 과부하 상태입니다." + detail, "몇 분 뒤 다시 시도하세요.", 502)
    if status in (408, 504):
        return UserError("서버 응답 시간이 초과되었습니다." + detail, "설정의 시간 제한을 늘리거나 잠시 뒤 다시 시도하세요.", 502)
    if status == 400 or etype == "invalid_request_error":
        return UserError("서버가 요청 형식을 거절했습니다." + detail,
                         "모델 식별자, 최대 출력 길이, '추가 요청 본문(JSON)' 설정을 확인하세요. 중개서버가 Anthropic Messages 규격을 그대로 받는지도 확인하세요.", 502)
    if status >= 500:
        return UserError(f"AI 서버 오류가 발생했습니다(HTTP {status})." + detail, "잠시 뒤 다시 시도하세요.", 502)
    return UserError(f"예상하지 못한 응답입니다(HTTP {status})." + detail, "", 502)


def _parse_message(j: Any) -> dict:
    if not isinstance(j, dict):
        raise UserError("서버 응답이 Anthropic Messages 형식이 아닙니다.", "요청 경로와 중개서버 규격을 확인하세요.", 502)
    if "choices" in j and "content" not in j:
        raise UserError("서버가 OpenAI 호환 형식으로 응답했습니다. 이 프로그램은 Anthropic Messages 형식만 지원합니다.",
                        "중개서버의 Anthropic Messages 경로(예: /v1/messages)를 설정하세요.", 502)
    content = j.get("content")
    if not isinstance(content, list):
        raise UserError("서버 응답에 content 블록이 없습니다(Anthropic Messages 형식이 아님).",
                        "요청 경로와 중개서버 규격을 확인하세요.", 502)
    text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return {"text": text, "stop_reason": j.get("stop_reason"), "stop_details": j.get("stop_details"),
            "usage": j.get("usage") or {}, "model": j.get("model"), "id": j.get("id")}


STALL_S = 60  # 글자가 오기 시작한 뒤 이 시간 동안 아무것도 안 오면 '멈춤'으로 보고 중단


async def _stream_request(client: httpx.AsyncClient, url: str, headers: dict, body: dict, job=None,
                          deadline: float | None = None, state: dict | None = None) -> dict:
    """SSE 스트리밍 수신. 중개서버가 도중에 끊거나 멈추면 기다리지 않고 바로 알린다."""
    state = state if state is not None else {}
    text_parts: list[str] = []
    usage: dict = {}
    stop_reason = None
    stop_details = None
    model = None
    msg_id = None
    got_stop = False
    async with client.stream("POST", url, headers=headers, json=body) as resp:
        if resp.status_code != 200:
            raw = (await resp.aread()).decode("utf-8", "replace")
            raise _error_from_response(resp.status_code, raw, resp.headers)
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" not in ctype:
            raw = (await resp.aread()).decode("utf-8", "replace")
            try:
                return _parse_message(json.loads(raw))
            except ValueError as e:
                raise UserError("스트리밍 응답을 해석하지 못했습니다.", "설정에서 '스트리밍 사용'을 끄고 다시 시도하세요.", 502) from e
        lines = resp.aiter_lines().__aiter__()
        while True:
            if text_parts:
                wait = STALL_S
            else:  # 첫 글자 전: 모델이 생각하는 동안은 전체 시간 제한까지 기다린다
                wait = max(1.0, (deadline - time.time()) if deadline else 300.0)
            try:
                line = await asyncio.wait_for(lines.__anext__(), timeout=wait)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError as e:
                n = sum(len(x) for x in text_parts)
                if text_parts:
                    raise UserError(f"응답을 받는 도중 {STALL_S}초 동안 멈춰서 중단했습니다(받은 글자 {n}자).",
                                    "중개서버가 긴 응답을 중간에 멈춘 것으로 보입니다. [다시 시도]하거나, 설정에서 더 빠른 모델(예: claude-sonnet-5)을 써 보세요.", 504) from e
                raise UserError("정해진 시간 안에 첫 응답이 오지 않아 중단했습니다.",
                                "잠시 뒤 다시 시도하거나, 설정에서 시간 제한을 늘리거나 더 빠른 모델을 쓰세요.", 504) from e
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            t = ev.get("type")
            if t == "message_start":
                m = ev.get("message") or {}
                usage.update(m.get("usage") or {})
                model, msg_id = m.get("model"), m.get("id")
            elif t == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta":
                    text_parts.append(d.get("text", ""))
                    state["receiving"] = True
                    if job is not None:
                        job.report(message=f"응답 받는 중… {sum(len(x) for x in text_parts)}자")
            elif t == "message_delta":
                d = ev.get("delta") or {}
                stop_reason = d.get("stop_reason", stop_reason)
                stop_details = d.get("stop_details", stop_details)
                usage.update(ev.get("usage") or {})
            elif t == "message_stop":
                got_stop = True
            elif t == "error":
                err = ev.get("error") or {}
                raise _error_from_response(529 if err.get("type") == "overloaded_error" else 500,
                                           json.dumps(ev), httpx.Headers())
    if not got_stop and not stop_reason:
        n = sum(len(x) for x in text_parts)
        raise UserError(f"응답을 받는 도중 중개서버 연결이 끊겼습니다(받은 글자 {n}자, 응답 미완성).",
                        "중개서버가 긴 응답을 중간에 끊은 것으로 보입니다. [다시 시도]하거나, 설정에서 더 빠른 모델(예: claude-sonnet-5)을 써 보세요.", 502)
    return {"text": "".join(text_parts), "stop_reason": stop_reason, "stop_details": stop_details,
            "usage": usage, "model": model, "id": msg_id}


async def _wait_ticker(job, state: dict, t0: float) -> None:
    """첫 글자가 오기 전, 기다린 시간을 화면에 보여준다(멈춘 것이 아님을 알 수 있게)."""
    while True:
        await asyncio.sleep(1)
        if state.get("receiving") or job is None:
            return
        sec = int(time.time() - t0)
        job.report(message=f"응답을 기다리는 중… {sec}초 — 모델이 먼저 생각한 뒤 답을 쓰기 때문에 첫 글자까지 1~2분 걸릴 수 있습니다.")


async def call(system: str, user: str, max_tokens: int | None = None, job=None) -> dict:
    """한 번의 Messages 요청. 반환: text, usage(서버가 준 값 그대로), stop_reason, latency_s, cost."""
    check_ready()
    s, _ = _settings()
    stream = bool(s.get("stream"))
    url, headers, body, timeout = _build(system, [{"role": "user", "content": user}], max_tokens, stream)
    t0 = time.time()
    state: dict = {}
    ticker = asyncio.create_task(_wait_ticker(job, state, t0)) if job is not None else None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
            if job is not None:
                job.report(message="AI 서버에 요청을 보냈습니다. 응답을 기다리는 중…")
            if stream:
                res = await _stream_request(client, url, headers, body, job, deadline=t0 + timeout, state=state)
            else:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code != 200:
                    raise _error_from_response(resp.status_code, resp.text, resp.headers)
                try:
                    j = resp.json()
                except ValueError as e:
                    raise UserError("서버 응답이 JSON이 아닙니다(Anthropic Messages 형식이 아님).",
                                    "서버 기본 주소와 요청 경로를 확인하세요. 웹페이지 주소를 넣은 것은 아닌지 확인하세요.", 502) from e
                res = _parse_message(j)
    except httpx.TimeoutException as e:
        raise UserError(f"{int(timeout)}초 안에 응답이 오지 않아 중단했습니다.",
                        "설정에서 시간 제한을 늘리거나, 스트리밍을 켜거나, 잠시 뒤 다시 시도하세요.", 504) from e
    except httpx.ConnectError as e:
        raise UserError("AI 서버에 연결하지 못했습니다.", "서버 기본 주소, 인터넷 연결, 중개서버 실행 상태를 확인하세요.", 502) from e
    except httpx.RemoteProtocolError as e:
        raise UserError("응답을 받는 도중 중개서버 연결이 끊겼습니다.",
                        "중개서버가 긴 응답을 중간에 끊은 것으로 보입니다. [다시 시도]하거나 더 빠른 모델을 써 보세요.", 502) from e
    except httpx.HTTPError as e:
        raise UserError("AI 서버와 통신 중 오류가 발생했습니다: " + _redact(type(e).__name__),
                        "인터넷 연결과 서버 주소를 확인하세요.", 502) from e
    finally:
        if ticker:
            ticker.cancel()
    res["latency_s"] = round(time.time() - t0, 2)
    if res.get("stop_reason") == "refusal":
        raise UserError("AI가 이 요청을 거절했습니다.", "주제나 표현을 바꿔 다시 시도하세요.", 422)
    if res.get("stop_reason") == "max_tokens":
        raise UserError("AI 응답이 최대 출력 길이에서 잘렸습니다.",
                        "설정의 '최대 출력 토큰'을 늘리거나, 일부만 다시 생성하세요.", 422)
    res["cost"] = estimate_cost(res.get("usage") or {})
    return res


def estimate_cost(usage: dict) -> dict | None:
    """사용자가 단가를 입력한 경우에만 추정 비용을 계산한다(서버가 준 토큰 수 기준)."""
    s = config.load_settings()["ai"]
    pin, pout = s.get("price_input_per_mtok"), s.get("price_output_per_mtok")
    if pin in (None, "") or pout in (None, ""):
        return None
    try:
        pin, pout = float(pin), float(pout)
    except (TypeError, ValueError):
        return None
    tin = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    tout = int(usage.get("output_tokens") or 0)
    return {"estimate": round(tin / 1e6 * pin + tout / 1e6 * pout, 6), "currency": s.get("price_currency") or "USD",
            "note": "입력한 단가 기준 추정치(캐시 할인 등은 반영하지 않음)"}


async def test_connection() -> dict:
    """짧은 요청으로 연결·인증·형식을 확인."""
    res = await call("", "Reply with exactly: OK", max_tokens=64)
    return {"ok": True, "reply": res["text"][:100], "model": res.get("model"), "usage": res.get("usage"),
            "stop_reason": res.get("stop_reason"), "latency_s": res.get("latency_s"), "cost": res.get("cost"),
            "format": "Anthropic Messages"}
