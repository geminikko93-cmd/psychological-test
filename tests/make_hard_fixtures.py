"""어려운 조건의 검증용 음성(검증용 합성, 타입캐스트 아님).

기존 ja_tts_sample 은 문장마다 따로 합성하고 '알려진 길이의 쉼'을 넣은 자료라 정렬이 쉽다.
여기서는 실제 낭독에 더 가까운 조건을 만든다.
  continuous : 전체 대본을 한 번에 이어 읽음(쉼은 합성기가 자연스럽게 넣음)
  fast       : 같은 대본을 25% 빠르게(쉼이 더 짧음)
  omitted    : 음성에서 5번째 문장을 빼고 읽음(대본은 그대로)
  added      : 대본에 없는 문장을 음성에 하나 더 넣음
대본에는 숫자·한자 읽기 차이(화면 '3つ' / 낭독 'みっつ')를 포함한다.
정답 시간은 edge-tts의 문장 경계(SentenceBoundary) 정보로 만든다(합성기 기준 정답).

실행: .venv\\Scripts\\python tests\\make_hard_fixtures.py   (인터넷 필요)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.util import ffmpeg_exe, run_tool  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "hard"
VOICE = "ja-JP-NanamiNeural"

LINES = [  # (화면 표시, TTS 낭독)
    ("3つのドアがあります。", "みっつのドアがあります。"),
    ("どれか1つだけ開けられます。", "どれかひとつだけ開けられます。"),
    ("左は赤、真ん中は青、右は白です。", "左は赤、真ん中は青、右は白です。"),
    ("赤を選んだ人は、2分で決める人。", "赤を選んだ人は、にふんで決める人。"),
    ("青を選んだ人は、10秒だけ迷う人。", "青を選んだ人は、じゅうびょうだけ迷う人。"),
    ("白を選んだ人は、最後まで迷う人です。", "白を選んだ人は、最後まで迷う人です。"),
    ("今日は4月1日です。", "今日はしがつついたちです。"),
    ("あなたはどれを開けましたか。", "あなたはどれを開けましたか。"),
    ("コメントで教えてください。", "コメントで教えてください。"),
]
EXTRA = "ちなみに、ドアの色に意味はありません。"
OMIT_INDEX = 4
ADD_AFTER = 5


async def synth(text: str, out_mp3: Path, rate: str = "+0%") -> list[dict]:
    import edge_tts

    comm = edge_tts.Communicate(text, VOICE, rate=rate, boundary="SentenceBoundary")
    bounds = []
    with open(out_mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "SentenceBoundary":
                bounds.append({"start": chunk["offset"] / 1e7, "end": (chunk["offset"] + chunk["duration"]) / 1e7,
                               "text": chunk["text"]})
    return bounds


def to_wav(mp3: Path, wav: Path) -> None:
    r = run_tool([ffmpeg_exe(), "-y", "-v", "error", "-i", str(mp3), "-ac", "1", "-ar", "24000", str(wav)])
    assert r.returncode == 0, r.stderr


def map_lines(spoken: list[str], bounds: list[dict]) -> list[list[float]]:
    """합성기의 문장 경계(。 단위)를 낭독 줄 단위로 묶는다."""
    out, bi = [], 0
    for line in spoken:
        n = max(1, line.count("。"))
        seg = bounds[bi:bi + n]
        bi += n
        out.append([round(seg[0]["start"], 3), round(seg[-1]["end"], 3)])
    return out


async def main() -> None:
    FIX.mkdir(parents=True, exist_ok=True)
    tts = [t for _, t in LINES]
    variants = {
        "continuous": (tts, "+0%"),
        "fast": (tts, "+25%"),
        "omitted": ([t for i, t in enumerate(tts) if i != OMIT_INDEX], "+0%"),
        "added": (tts[:ADD_AFTER + 1] + [EXTRA] + tts[ADD_AFTER + 1:], "+0%"),
    }
    meta = {"lines": LINES, "extra": EXTRA, "omit_index": OMIT_INDEX, "add_after": ADD_AFTER, "voice": VOICE, "variants": {}}
    for name, (spoken, rate) in variants.items():
        mp3 = FIX / f"{name}.mp3"
        bounds = await synth("".join(spoken), mp3, rate)
        to_wav(mp3, FIX / f"{name}.wav")
        meta["variants"][name] = {"spoken": spoken, "rate": rate, "truth": map_lines(spoken, bounds), "bounds": bounds}
        print(name, len(bounds), "sentences")
    (FIX / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
