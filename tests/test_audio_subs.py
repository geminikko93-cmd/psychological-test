"""음성 무음 정리·자막 정렬·SRT 검증 (실제 음성 파일 사용, 모의 아님)."""
import json
import os

import numpy as np
import pytest

from conftest import FIX
from app import align
from app import audio as A
from app.jp_text import lint_text, split_for_cues, wrap_lines
from app.srt import build_srt, check_cues, parse_srt

needs_ja = pytest.mark.skipif(not (FIX / "ja_tts_sample.wav").exists(), reason="tests/make_fixtures.py 실행 필요")


def load(name):
    info = A.probe(FIX / name)
    return A.decode(FIX / name, info["sr"], info["channels"]), info["sr"]


@pytest.mark.parametrize("mode", ["natural", "fast"])
def test_weak_endings_not_cut_synthetic(mode):
    """정답이 알려진 합성 신호: 약한 어미(-46~-56dB 마찰음 꼬리)를 포함한 모든 소리 구간이 처리본에 그대로 남는다."""
    data, sr = load("synthetic_speech.wav")
    truth = json.loads((FIX / "synthetic_truth.json").read_text())["speech"]
    params = A.resolve_params(mode, {})
    an = A.analyze(data, sr, params)
    out, seg = A.apply_plan(data, sr, an["regions"])
    v = A.verify_cut(data, sr, an["regions"], an["threshold_db"], out, seg)
    assert v["ok"] and v["speech_samples_identical"]
    assert an["removed_s"] > 3.0  # 긴 무음은 실제로 줄었다
    for s, e in truth:
        a, b = round(s * sr), round(e * sr)
        ns = round(A.map_time(seg, s) * sr)
        assert np.array_equal(data[a:b], out[ns:ns + (b - a)]), f"소리 구간 {s}-{e}가 변형됨"
    # 문장 안 짧은 쉼(0.25초)은 줄이지 않는다
    short = [r for r in an["regions"] if abs(r["length"] - 0.25) < 0.05]
    assert all(not r["remove"] for r in short)


@needs_ja
@pytest.mark.parametrize("mode", ["natural", "fast"])
def test_japanese_voice_silence_and_alignment(mode):
    data, sr = load("ja_tts_sample.wav")
    truth = json.loads((FIX / "ja_tts_truth.json").read_text(encoding="utf-8"))
    params = A.resolve_params(mode, {})
    an = A.analyze(data, sr, params)
    out, seg = A.apply_plan(data, sr, an["regions"])
    v = A.verify_cut(data, sr, an["regions"], an["threshold_db"], out, seg)
    assert v["ok"], v
    assert v["min_distance_to_speech_ms"] >= params["guard_before_ms"] - 15
    # 문장 시작·끝(첫 음·어미) 부분이 처리본에 그대로 있음. 문장 안의 긴 쉼(。 뒤)은 줄어들 수 있으므로 양 끝 0.3초만 비교
    for s, e in truth["lines"]:
        for t0, t1 in ((s, s + 0.3), (e - 0.3, e)):
            a, b = round(t0 * sr), round(t1 * sr)
            ns = round(A.map_time(seg, t0) * sr)
            assert np.array_equal(data[a:b], out[ns:ns + (b - a)]), (t0, t1)
    # 남은 문장 사이 간격은 설정값 근처(0이 아님 = 호흡 유지)
    gaps = []
    for (s0, e0), (s1, e1) in zip(truth["lines"], truth["lines"][1:]):
        gaps.append(A.map_time(seg, s1) - A.map_time(seg, e0))
    assert min(gaps) > 0.15, gaps  # 정답 경계에 앞뒤 여백(0.08초)이 포함되어 있어 실제 남은 쉼은 이보다 김
    # 자막 정렬: 처리본 기준 문장 경계 오차 0.1초 이내
    lines = [{"id": f"l{i}", "display": d, "tts": t} for i, (d, t, _) in enumerate(truth["text"])]
    proj = {"script": {"scenes": [{"id": "s", "lines": lines}]}}
    res = align.align_project(proj, out, sr, an["threshold_db"])
    for lt, (s, e) in zip(res["line_times"], truth["lines"]):
        assert abs(lt["start"] - A.map_time(seg, s)) < 0.1
        assert abs(lt["end"] - A.map_time(seg, e)) < 0.1
    issues = check_cues(res["cues"], len(out) / sr, {})
    assert not [i for i in issues if i["level"] == "error"]


@needs_ja
def test_subtitles_follow_audio_change():
    """무음 설정을 바꾸면 처리본 길이가 달라지고, 다시 정렬한 자막이 새 음성에 맞는다."""
    data, sr = load("ja_tts_sample.wav")
    truth = json.loads((FIX / "ja_tts_truth.json").read_text(encoding="utf-8"))
    lines = [{"id": f"l{i}", "display": d, "tts": t} for i, (d, t, _) in enumerate(truth["text"])]
    proj = {"script": {"scenes": [{"id": "s", "lines": lines}]}}
    ends = {}
    for mode in ("natural", "fast"):
        an = A.analyze(data, sr, A.resolve_params(mode, {}))
        out, seg = A.apply_plan(data, sr, an["regions"])
        res = align.align_project(proj, out, sr, an["threshold_db"])
        last = res["line_times"][-1]
        assert abs(last["start"] - A.map_time(seg, truth["lines"][-1][0])) < 0.1
        ends[mode] = res["cues"][-1]["end"]
    assert ends["fast"] < ends["natural"] - 0.5


def test_protect_and_custom_ranges():
    data, sr = load("synthetic_speech.wav")
    p = A.resolve_params("fast", {})
    base = A.analyze(data, sr, p)
    gap = next(r for r in base["regions"] if r["kind"] == "gap" and r["remove"])
    prot = A.analyze(data, sr, p, protect_ranges=[[gap["start"], gap["end"], "x"]])
    r2 = next(r for r in prot["regions"] if r["start"] == gap["start"])
    assert r2["protected"] and r2["remove"] is None
    cust = A.analyze(data, sr, p, custom_ranges=[[gap["start"], gap["end"], 1000]])
    r3 = next(r for r in cust["regions"] if r["start"] == gap["start"])
    assert abs(r3["new_length"] - 1.0) < 0.02


def test_no_speed_change_only_silence_removed():
    data, sr = load("synthetic_speech.wav")
    an = A.analyze(data, sr, A.resolve_params("natural", {}))
    out, seg = A.apply_plan(data, sr, an["regions"])
    kept = sum(oe - os_ for os_, oe, _ in seg)
    assert abs(kept - len(out) / sr) < 0.001  # 처리본 길이 = 남긴 원본 구간 합(속도 변화 없음)


@needs_ja
def test_mp3_input_decodes():
    data, sr = load("ja_tts_sample.mp3")
    an = A.analyze(data, sr, A.resolve_params("natural", {}))
    assert an["removed_s"] > 3


def test_srt_utf8_and_errors():
    cues = [{"id": "a", "start": 0.0, "end": 1.5, "text": "最初に目に入ったのは、\nどれですか。"},
            {"id": "b", "start": 1.4, "end": 2.0, "text": "窓、時計、本棚。"},
            {"id": "c", "start": 2.0, "end": 9.0, "text": "長い"}]
    text = build_srt(cues)
    raw = text.encode("utf-8")
    assert raw.decode("utf-8") == text and "\r\n" in text
    parsed = parse_srt(text)
    assert parsed[0]["text"] == "最初に目に入ったのは、\nどれですか。"
    issues = check_cues(cues, 5.0, {"max_line_chars": 14})
    msgs = " ".join(i["message"] for i in issues)
    assert "겹칩니다" in msgs and "넘습니다" in msgs and "짧습니다" in msgs


def test_japanese_line_breaking():
    lines = wrap_lines("窓を選んだ人は、出口より先に景色を見る人です。", 14)
    assert all(len(x) <= 15 for x in lines)
    assert not any(x[0] in "、。" for x in lines)
    parts = split_for_cues("時計を選んだ人は、今日の予定がまだ頭に残っている人。でも大丈夫です。気にしないでください。", 14, 2)
    assert len(parts) >= 2 and all(len(p.replace("\n", "")) <= 30 for p in parts)


def test_lint_detects_promises_and_exaggeration():
    cats = {i["category"] for i in lint_text("3秒で本当の性格が分かる！明日もお届けします。心理学的に証明")}
    assert {"약속", "과장", "근거"} <= cats


@pytest.mark.skipif(not os.environ.get("JPSS_TEST_MODELS"), reason="JPSS_TEST_MODELS(내려받은 whisper 모델 폴더) 지정 시에만")
@needs_ja
def test_whisper_assisted_alignment(tmp_path):
    from app import config
    config.MODELS_DIR = __import__("pathlib").Path(os.environ["JPSS_TEST_MODELS"])
    data, sr = load("ja_tts_sample.wav")
    truth = json.loads((FIX / "ja_tts_truth.json").read_text(encoding="utf-8"))
    an = A.analyze(data, sr, A.resolve_params("fast", {}))
    out, seg = A.apply_plan(data, sr, an["regions"])
    wav = tmp_path / "p.wav"
    A.write_wav(wav, out, sr)
    lines = [{"id": f"l{i}", "display": d, "tts": t} for i, (d, t, _) in enumerate(truth["text"])]
    res = align.align_project({"script": {"scenes": [{"id": "s", "lines": lines}]}}, out, sr, an["threshold_db"],
                              wav_path=wav, use_asr=True)
    assert res["method"] == "음성 인식 보조 정렬"
    for lt, (s, e), ln in zip(res["line_times"], truth["lines"], lines):
        assert abs(lt["start"] - A.map_time(seg, s)) < 0.12
        assert abs(lt["end"] - A.map_time(seg, e)) < 0.12
    # 자막 문구는 대본 그대로(인식 결과로 바뀌지 않음)
    assert [c["text"].replace("\n", "") for c in res["cues"]][0] == lines[0]["display"].replace("\n", "")
