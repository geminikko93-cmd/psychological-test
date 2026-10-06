"""검증용 음성 픽스처 생성.

1) synthetic_*.wav : 정답(소리 구간)이 정확히 알려진 합성 신호. 약한 어미(작은 마찰음 꼬리)를 일부러 넣어
   무음 정리가 이를 자르지 않는지 확인한다.
2) ja_tts_sample.wav/mp3 : 일본어 문장을 edge-tts(검증용, 타입캐스트 아님)로 문장별 합성 후
   알려진 길이의 무음으로 이어 붙인 것. 문장 경계 정답이 있어 자막 정렬 정확도를 잴 수 있다.
   ※ 타입캐스트 음성이 아니므로 실제 타입캐스트 음성에서의 결과와 다를 수 있다.

실행: .venv\\Scripts\\python tests\\make_fixtures.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import audio as A  # noqa: E402
from app.util import ffmpeg_exe, run_tool  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
SR = 24000

SAMPLE_LINES = [
    ("ドアを開けると、知らない部屋がありました。", "ドアを開けると、知らない部屋がありました。", "문을 열자, 모르는 방이 있었습니다."),
    ("最初に目に入ったのは、どれですか。", "最初に目に入ったのは、どれですか。", "가장 먼저 눈에 들어온 것은 어느 것인가요?"),
    ("窓、時計、本棚。\n直感で選んでください。", "窓、時計、本棚。直感で選んでください。", "창문, 시계, 책장. 직감으로 골라 주세요."),
    ("窓を選んだ人は、出口より先に景色を見る人です。", "窓を選んだ人は、出口より先に景色を見る人です。", "창문을 고른 사람은 출구보다 먼저 풍경을 보는 사람입니다."),
    ("時計を選んだ人は、今日の予定がまだ頭に残っている人。", "時計を選んだ人は、きょうの予定がまだ頭に残っている人。", "시계를 고른 사람은 오늘 일정이 아직 머릿속에 남아 있는 사람."),
    ("本棚を選んだ人は、答えより手がかりが好きな人です。", "本棚を選んだ人は、答えより手がかりが好きな人です。", "책장을 고른 사람은 답보다 단서를 좋아하는 사람입니다."),
    ("これは遊びのテストです。", "これは遊びのテストです。", "이것은 놀이용 테스트입니다."),
    ("あなたはどれでしたか。\nコメントで教えてください。", "あなたはどれでしたか。コメントで教えてください。", "당신은 어느 것이었나요? 댓글로 알려 주세요."),
]
# 문장 앞뒤 무음(초): 앞 0.9, 문장 사이 다양, 끝 1.4 — 타입캐스트에서 생기는 긴 대기 시간을 흉내
PAUSES = [0.9, 0.55, 1.6, 0.35, 2.4, 0.8, 1.1, 0.7, 1.4]


def make_synthetic() -> None:
    rng = np.random.default_rng(7)
    parts, truth, t = [], [], 0.0

    def sil(d):
        nonlocal t
        parts.append(np.zeros(int(d * SR)))
        t += d

    def syllables(n, weak_tail=False):
        nonlocal t
        start = t
        for _ in range(n):
            d = 0.12
            tt = np.arange(int(d * SR)) / SR
            f0 = 180 + rng.uniform(-20, 20)
            env = np.sin(np.pi * tt / d) ** 0.6
            v = 0.3 * env * (np.sin(2 * np.pi * f0 * tt) + 0.4 * np.sin(2 * np.pi * 2 * f0 * tt))
            parts.append(v)
            t += d
        if weak_tail:  # 무성화된 'す' 같은 약한 마찰음 꼬리(-46dBFS 정도)
            d = 0.09
            noise = rng.normal(0, 1, int(d * SR)) * 0.005 * np.linspace(1, 0.3, int(d * SR))
            parts.append(noise)
            t += d
        truth.append([round(start, 4), round(t, 4)])

    sil(0.8)
    syllables(8, weak_tail=True)
    sil(1.5)
    syllables(6)
    sil(0.25)  # 문장 안 짧은 쉼(유지돼야 함)
    syllables(5, weak_tail=True)
    sil(2.2)
    syllables(9)
    sil(1.3)
    x = np.concatenate(parts)
    x = x + rng.normal(0, 0.0003, len(x))  # 아주 작은 바닥 잡음(-70dB 정도)
    data = (np.clip(x, -1, 1) * 32767).astype("<i2")[:, None]
    A.write_wav(FIX / "synthetic_speech.wav", data, SR)
    (FIX / "synthetic_truth.json").write_text(json.dumps({"speech": truth, "sr": SR}, indent=1), encoding="utf-8")
    print("synthetic ok", len(x) / SR)


async def make_ja() -> None:
    import edge_tts

    tmp = FIX / "tmp"
    tmp.mkdir(exist_ok=True)
    chunks = []
    for i, (_, tts, _) in enumerate(SAMPLE_LINES):
        mp3 = tmp / f"l{i}.mp3"
        if not mp3.exists():
            await edge_tts.Communicate(tts, "ja-JP-NanamiNeural").save(str(mp3))
        r = run_tool([ffmpeg_exe(), "-v", "error", "-i", str(mp3), "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"])
        a = np.frombuffer(r.stdout, dtype="<i2").astype(np.float32)
        # 각 파일 앞뒤 자체 무음을 걷어 내고 정해진 무음만 넣는다(정답 경계를 알기 위함)
        db = A.frame_db(a / 32768, SR)
        idx = np.where(db > -50)[0]
        s, e = idx[0] * A.HOP_S, (idx[-1] + 1) * A.HOP_S
        chunks.append(a[int(max(0, s - 0.02) * SR): int(min(len(a) / SR, e + 0.06) * SR)])
    parts, truth, t = [], [], 0.0
    for i, c in enumerate(chunks):
        parts.append(np.zeros(int(PAUSES[i] * SR)))
        t += PAUSES[i]
        truth.append([round(t, 3), round(t + len(c) / SR, 3)])
        parts.append(c)
        t += len(c) / SR
    parts.append(np.zeros(int(PAUSES[-1] * SR)))
    x = np.concatenate(parts).astype(np.int16)[:, None]
    A.write_wav(FIX / "ja_tts_sample.wav", x, SR)
    run_tool([ffmpeg_exe(), "-y", "-v", "error", "-i", str(FIX / "ja_tts_sample.wav"), "-b:a", "128k",
              str(FIX / "ja_tts_sample.mp3")])
    (FIX / "ja_tts_truth.json").write_text(json.dumps({"lines": truth, "pauses": PAUSES, "sr": SR,
                                                       "text": SAMPLE_LINES}, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    print("ja ok", t)


if __name__ == "__main__":
    FIX.mkdir(parents=True, exist_ok=True)
    make_synthetic()
    try:
        asyncio.run(make_ja())
    except Exception as e:  # 네트워크 없으면 건너뜀
        print("일본어 음성 픽스처 생성 실패(네트워크 필요):", e)
