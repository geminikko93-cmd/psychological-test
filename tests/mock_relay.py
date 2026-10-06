"""시험용 모의 중개서버 (Anthropic Messages 형식 흉내). 실제 AI가 아니며 고정 응답을 돌려준다.
프로그램의 연결·오류 처리·화면 흐름 검증에만 쓴다. 실행: python tests/mock_relay.py 8799

특수 모델 이름으로 오류 상황을 흉내 낸다:
  mock-429 → 사용 한도 오류, mock-401 → 인증 오류, mock-slow → 30초 지연, mock-openai → OpenAI 형식 응답,
  mock-truncated → stop_reason=max_tokens
"""
from __future__ import annotations

import asyncio
import json
import sys

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

KEY = "mock-key-1234567890"
app = FastAPI()
LOG: list[dict] = []

PLAN = {"candidates": [
    {"id": "A", "approach_name_ko": "[모의] 상황 몰입형", "format_ko": "모의 응답", "content_kind": "entertainment",
     "why_watch_ko": "모의", "first_scene_ko": "모의", "first_line_ja": "【モック】最初の一文です。", "first_line_ko": "[모의] 첫 문장",
     "structure_ko": ["장면 1", "장면 2"], "scene_count": 2, "choices": {"use": False, "count": None, "reason_ko": "모의"},
     "payoff_ko": "모의", "next_video_pull_ko": "모의", "visuals_ko": ["모의"], "source_difficulty_ko": "쉬움",
     "claims_ko": "오락", "risks_ko": "", "overlap_check_ko": "", "estimated_seconds": 20},
    {"id": "B", "approach_name_ko": "[모의] 관찰형", "format_ko": "모의 응답", "content_kind": "entertainment",
     "first_line_ja": "【モック】二つ目の案。", "first_line_ko": "[모의] 두 번째 안", "structure_ko": ["장면 1"], "scene_count": 1,
     "choices": {"use": True, "count": 2, "reason_ko": "모의"}},
], "comparison_ko": "[모의 응답] 실제 AI가 아닙니다."}

SCRIPT = {"content_kind": "entertainment", "title_candidates": [{"ja": "【モック】タイトル", "ko": "[모의] 제목"}],
          "scenes": [
              {"purpose_ko": "[모의] 훅", "visual_ko": "모의 화면", "search_keywords": ["mock", "モック"], "edit_intent_ko": "",
               "hold_ms": 0, "lines": [{"display_ja": "【モック】一行目です。", "tts_ja": "もっく、いちぎょうめです。", "ko": "[모의] 첫 줄",
                                        "reading_note_ko": "", "caution_ko": ""}]},
              {"purpose_ko": "[모의] 결말", "visual_ko": "모의 화면2", "search_keywords": ["mock2"], "hold_ms": 1200, "hold_reason_ko": "생각할 시간",
               "lines": [{"display_ja": "【モック】二行目。毎日見てね。", "tts_ja": "もっく、にぎょうめ。", "ko": "[모의] 둘째 줄"}]}],
          "claims_level_ko": "[모의]", "self_check_ko": ["[모의 응답]"]}

REVIEW = {"items": [{"line_id": None, "severity": "mid", "category_ko": "[모의]", "issue_ko": "[모의 응답] 검토 항목",
                     "suggestion_display_ja": "", "suggestion_tts_ja": "", "suggestion_ko": ""}],
          "consistency_ko": "[모의]", "overall_ko": "[모의 응답]"}
PUBLISH = {"titles": [{"ja": "【モック】投稿タイトル", "ko": "[모의] 게시 제목", "note_ko": ""}],
           "description_ja": "【モック】説明文です。娯楽としてお楽しみください。", "description_ko": "[모의] 설명",
           "hashtags": ["#モック"], "notes_ko": ""}


def pick(prompt: str, body: dict) -> dict | str:
    if "Reply with exactly" in prompt:
        return "OK"
    if "指定範囲だけ" in prompt:
        # 현재 대본의 첫 line_id를 찾아 그 줄만 바꾼다
        import re
        m = re.search(r'"line_id": "([^"]+)"', prompt)
        lid = m.group(1) if m else "x"
        return {"changes": [{"op": "replace_line", "line_id": lid, "display_ja": "【モック】書き直した一行。",
                             "tts_ja": "もっく、かきなおした いちぎょう。", "ko": "[모의] 다시 쓴 줄"},
                            {"op": "replace_line", "line_id": "out_of_scope_id", "display_ja": "x", "tts_ja": "x", "ko": "x"}],
                "explanation_ko": "[모의 응답] 첫 줄만 바꿈"}
    if "Gemini Omni Flash 1.1 に入力するプロンプト" in prompt:
        import re
        ids = list(dict.fromkeys(re.findall(r'"clip_id": "([^"]+)"', prompt)))
        return {"style_bible": {"look_en": "[MOCK] soft warm 3D miniature style, vertical 9:16", "look_ko": "[모의] 따뜻한 3D 미니어처",
                                "recurring_ko": ["[모의] 방"], "palette_ko": "[모의] 따뜻한 색", "avoid_en": "on-screen text, logos"},
                "clips": [{"clip_id": i, "prompt_en": f"[MOCK] clip {i}: a door slowly opens, no on-screen text, no dialogue",
                           "prompt_ko": "[모의] 문이 천천히 열림", "beats_ko": "0~2초: [모의]", "mode": "text",
                           "mode_note_ko": "[모의]", "risk_ko": ""} for i in ids]}
    if "1案だけを作り直して" in prompt:
        return {"candidate": {"id": "C", "core_idea_ko": "[모의] 짧은 이야기형", "content_type": "短い物語・場面",
                              "viewer_action": "想像する", "ending_type": "どんでん返し", "approach_name_ko": "[모의] 미니 스토리",
                              "first_line_ja": "【モック】ある朝、ドアの前に箱がありました。", "structure_ko": ["장면 1: 상자 발견", "장면 2: 반전"],
                              "payoff_ko": "[모의] 반전", "ending_ko": "[모의] 질문을 남김", "differs_from_ko": "[모의] 선택지 없음"}}
    if "企画方向を3案" in prompt and body.get("model") == "mock-similar":
        base = {"content_type": "選択型の問いかけ", "viewer_action": "選ぶ", "ending_type": "結果公開",
                "structure_ko": ["장면 1: 질문", "장면 2: 선택지 3개", "장면 3: 결과"], "payoff_ko": "고른 동물에 따라 결과"}
        return {"candidates": [
            dict(base, id="A", approach_name_ko="[모의] 동물 선택", first_line_ja="最初に目に入った動物はどれですか？"),
            dict(base, id="B", approach_name_ko="[모의] 관찰 공감", content_type="観察・あるある共感", viewer_action="共感する",
                 ending_type="オチ・笑い", structure_ko=["장면 1: 흔한 상황", "장면 2: 공감 포인트", "장면 3: 웃음"],
                 first_line_ja="エレベーターで気まずい時、どこを見ますか。", payoff_ko="공감되는 웃음"),
            dict(base, id="C", approach_name_ko="[모의] 동물 선택2", first_line_ja="最初に目に入った動物はどれですか？（犬・猫・鳥）")],
            "comparison_ko": "[모의]"}
    if "企画方向を3案" in prompt:
        return PLAN
    if "台本を作って" in prompt:
        return SCRIPT
    if "問題点を挙げて" in prompt:
        return REVIEW
    if "投稿用タイトル" in prompt:
        return PUBLISH
    return {"error": "unknown"}


@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    hdr = {k.lower(): v for k, v in request.headers.items()}
    first = (body.get("messages") or [{}])[0].get("content", "")
    LOG.append({"headers": {k: ("***" if k in ("x-api-key", "authorization") else v) for k, v in hdr.items()}, "model": body.get("model"),
                "prompt_head": first[:3000] if isinstance(first, str) else ""})
    model = body.get("model", "")
    auth_ok = hdr.get("x-api-key") == KEY or hdr.get("authorization") == f"Bearer {KEY}" or hdr.get("x-relay-key") == KEY
    if model == "mock-401" or not auth_ok:
        return JSONResponse({"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}, 401)
    if model == "mock-429":
        return JSONResponse({"type": "error", "error": {"type": "rate_limit_error", "message": "rate limited"}}, 429,
                            headers={"retry-after": "37"})
    if model == "mock-slow":
        await asyncio.sleep(30)
    if model == "mock-openai":
        return {"id": "x", "choices": [{"message": {"role": "assistant", "content": "OK"}}]}
    prompt = body["messages"][0]["content"]
    out = pick(prompt, body)
    text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
    stop = "max_tokens" if model == "mock-truncated" else "end_turn"
    usage = {"input_tokens": max(1, len(prompt) // 3), "output_tokens": max(1, len(text) // 3)}
    if body.get("stream") and model in ("mock-cut", "mock-stall"):
        async def broken():
            start = {"type": "message_start", "message": {"id": "m", "model": model, "usage": {}}}
            delta = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": '{"candidates": ['}}
            yield "event: message_start\ndata: " + json.dumps(start) + "\n\n"
            yield "event: content_block_delta\ndata: " + json.dumps(delta) + "\n\n"
            if model == "mock-stall":
                await asyncio.sleep(15)
            # mock-cut: message_stop 없이 연결 종료
        return StreamingResponse(broken(), media_type="text/event-stream")
    if body.get("stream"):
        async def gen():
            yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': 'msg_mock', 'model': model, 'usage': {'input_tokens': usage['input_tokens'], 'output_tokens': 1}}})}\n\n"
            for i in range(0, len(text), 40):
                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text[i:i + 40]}}, ensure_ascii=False)}\n\n"
            yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop}, 'usage': {'output_tokens': usage['output_tokens']}})}\n\n"
            yield "event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return {"id": "msg_mock", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}], "stop_reason": stop, "usage": usage}


@app.get("/_log")
async def log():
    return LOG


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8799, log_level="warning")
