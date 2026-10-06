"""자막 수동 수정(나누기·합치기·삭제·문구 수정) 후 다시 맞추기 — 누락·중복 없음 검증.

화면의 나누기·합치기·삭제 코드는 static/js/subsrc.js 를 Node로 그대로 실행해 시험한다.
"""
import copy
import json
import shutil
import subprocess

import pytest

from conftest import FIX, ROOT
from app import align
from app import audio as A
from app import subs_merge
from app.subs_merge import flat

needs_ja = pytest.mark.skipif(not (FIX / "ja_tts_sample.wav").exists(), reason="tests/make_fixtures.py 실행 필요")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js 필요(화면 코드 실행)")
SETTINGS = {"max_line_chars": 14, "max_lines": 2, "fill_gaps_under_s": 0.6}


def js(cues, ops, removed=None):
    r = subprocess.run(["node", str(ROOT / "tests" / "js_ops.mjs")],
                       input=json.dumps({"cues": cues, "ops": ops, "removed": removed or []}),
                       capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def setup():
    truth = json.loads((FIX / "ja_tts_truth.json").read_text(encoding="utf-8"))
    lines = [{"id": f"l{i}", "display": d, "tts": t} for i, (d, t, _) in enumerate(truth["text"])]
    proj = {"script": {"scenes": [{"id": "s", "lines": lines}]}}
    info = A.probe(FIX / "ja_tts_sample.wav")
    data = A.decode(FIX / "ja_tts_sample.wav", info["sr"], 1)
    out = {}
    for mode in ("natural", "fast"):
        an = A.analyze(data, info["sr"], A.resolve_params(mode, {}))
        pd, _ = A.apply_plan(data, info["sr"], an["regions"])
        out[mode] = align.align_project(proj, pd, info["sr"], an["threshold_db"])
        out[mode + "_audio"] = (pd, info["sr"], an["threshold_db"])
    return proj, out


def all_text(cues):
    return "".join(flat(c["text"]) for c in sorted(cues, key=lambda c: c["start"]))


def realign(proj, old, removed, new, choices=None, restore=False, old_texts="same"):
    texts = subs_merge.line_texts(proj) if old_texts == "same" else old_texts
    return subs_merge.merge(old, texts, removed, new["cues"], new["line_times"], proj, new["duration"], SETTINGS,
                            choices, restore)


@needs_ja
def test_auto_cues_cover_script_exactly(setup):
    proj, out = setup
    cov = subs_merge.coverage_report(out["natural"]["cues"], [], subs_merge.line_texts(proj))
    assert cov["ok"], cov


@needs_ja
@needs_node
def test_split_then_realign_keeps_both_halves(setup):
    proj, out = setup
    cues = copy.deepcopy(out["natural"]["cues"])
    target = cues[0]["text"]  # 'ドアを開けると、知らない\n部屋がありました。'
    pos = target.index("\n")
    r = js(cues, [{"op": "split", "index": 0, "pos": pos}])
    assert [c["text"] for c in r["cues"][:2]] == [target[:pos], target[pos + 1:]]
    m = realign(proj, r["cues"], r["removed"], out["fast"])
    assert m["coverage"]["ok"], m["coverage"]
    texts = [c["text"] for c in m["cues"]]
    assert target[:pos] in texts and target[pos + 1:] in texts  # 뒷부분이 사라지지 않음
    assert all_text(m["cues"]) == "".join(subs_merge.line_texts(proj).values())
    assert m["report"]["time_only"] and m["report"]["kept"] == 2


@needs_ja
@needs_node
def test_merge_lines_then_realign_no_duplicate(setup):
    proj, out = setup
    cues = copy.deepcopy(out["natural"]["cues"])
    r = js(cues, [{"op": "merge", "index": 0}])  # 서로 다른 대본 줄(l0+l1)을 합침
    assert {s["line_id"] for s in r["cues"][0]["src"]} == {"l0", "l1"}
    m = realign(proj, r["cues"], r["removed"], out["fast"])
    assert m["coverage"]["ok"], m["coverage"]
    line1 = flat(proj["script"]["scenes"][0]["lines"][1]["display"])
    assert sum(flat(c["text"]).count(line1) for c in m["cues"]) == 1  # 뒷줄이 중복되지 않음
    assert all_text(m["cues"]) == "".join(subs_merge.line_texts(proj).values())


@needs_ja
@needs_node
def test_delete_then_realign(setup):
    proj, out = setup
    cues = copy.deepcopy(out["natural"]["cues"])
    gone = cues[6]["text"]
    r = js(cues, [{"op": "delete", "index": 6}])
    m = realign(proj, r["cues"], r["removed"], out["fast"])
    assert m["coverage"]["ok"] and gone not in [c["text"] for c in m["cues"]]
    assert m["report"]["removed_kept"] == 1
    m2 = realign(proj, r["cues"], r["removed"], out["fast"], restore=True)  # 다시 넣기 선택
    assert m2["coverage"]["ok"] and flat(gone) in all_text(m2["cues"])


@needs_ja
@needs_node
def test_edit_split_merge_combo(setup):
    proj, out = setup
    cues = copy.deepcopy(out["natural"]["cues"])
    r = js(cues, [{"op": "edit", "index": 3, "text": "窓を選んだ人は、\n景色を先に見る人です。"},
                  {"op": "split", "index": 3, "pos": 8},
                  {"op": "merge", "index": 5}])
    m = realign(proj, r["cues"], r["removed"], out["fast"])
    assert m["coverage"]["ok"], m["coverage"]["problems"]
    assert "景色を先に見る人です。" in [c["text"] for c in m["cues"]]


@needs_ja
@needs_node
def test_script_change_then_realign_asks(setup):
    proj, out = setup
    cues = copy.deepcopy(out["natural"]["cues"])
    old_texts = subs_merge.line_texts(proj)
    r = js(cues, [{"op": "edit", "index": 2, "text": "窓、時計、本棚。\n直感で（手修正）"}])
    proj2 = copy.deepcopy(proj)
    proj2["script"]["scenes"][0]["lines"][2]["display"] = "窓と時計と本棚。\n直感で選んでください。"
    pd, sr, thr = out["fast_audio"]
    new = align.align_project(proj2, pd, sr, thr)  # 프로그램처럼 '현재' 대본으로 다시 정렬
    m = realign(proj2, r["cues"], r["removed"], new, old_texts=old_texts)
    rep = m["report"]
    assert not rep["time_only"] and rep["changed_lines"][0]["line_id"] == "l2"
    assert rep["uncertain"] and rep["uncertain"][0]["choice"] == "new"
    assert m["coverage"]["ok"] and "窓と時計と本棚。" in all_text(m["cues"])  # 기본: 새 대본 기준
    cid = rep["uncertain"][0]["cue_id"]
    m2 = realign(proj2, r["cues"], r["removed"], new, choices={cid: "keep"}, old_texts=old_texts)
    assert m2["coverage"]["ok"] and "窓、時計、本棚。\n直感で（手修正）" in [c["text"] for c in m2["cues"]]


@needs_ja
def test_legacy_split_cues_migrated(setup):
    """이전 버전(part 0 / 0.5, src 없음) 자막도 다시 맞출 때 앞뒤 조각이 모두 남는다."""
    proj, out = setup
    cues = copy.deepcopy(out["natural"]["cues"])
    for c in cues:
        c.pop("src")
    t = cues[0]["text"]
    pos = t.index("\n")
    a = dict(cues[0], text=t[:pos], end=1.5, manual_text=True, manual_time=True)
    b = dict(cues[0], id="c_legacy", text=t[pos + 1:], start=1.5, part=0.5, manual_text=True, manual_time=True)
    m = realign(proj, [a, b] + cues[1:], [], out["fast"], old_texts=None)
    assert m["coverage"]["ok"], m["coverage"]
    texts = [c["text"] for c in m["cues"]]
    assert t[:pos] in texts and t[pos + 1:] in texts


@needs_ja
@needs_node
def test_merge_endpoint_wiring(client, setup):
    from app import storage
    proj, out = setup
    p = storage.create_project("자막 병합 API")
    p["script"] = proj["script"]
    cues = copy.deepcopy(out["natural"]["cues"])
    r = js(cues, [{"op": "split", "index": 0, "pos": cues[0]["text"].index("\n")}])
    p["subtitles"].update(cues=r["cues"], line_texts=subs_merge.line_texts(proj), removed=r["removed"])
    storage.save_project(p["id"], p, p["rev"])
    res = client.post(f"/api/projects/{p['id']}/subtitles/merge",
                      json={"auto_cues": out["fast"]["cues"], "line_times": out["fast"]["line_times"],
                            "duration": out["fast"]["duration"]}).json()
    assert res["keep_manual"]["coverage"]["ok"] and res["fresh"]["coverage"]["ok"]
    assert res["keep_manual"]["report"]["kept"] == 2
