"""기획 품질: 후보 비교(규칙 기반)·후보 1개만 다시 만들기(AI 1회)·채널 기본값 개선 마이그레이션."""
import json

from conftest import wait_job
from app import channel, config, plan_check, prompts


def test_compare_flags_same_question_different_animals():
    a = {"id": "A", "content_type": "選択型の問いかけ", "viewer_action": "選ぶ", "ending_type": "結果公開",
         "first_line_ja": "最初に目に入った動物はどれですか？", "structure_ko": ["장면 1: 질문", "장면 2: 선택지", "장면 3: 결과"]}
    c = dict(a, id="C", first_line_ja="最初に目に入った動物はどれですか？（犬・猫・鳥）")
    b = {"id": "B", "content_type": "短い物語・場面", "viewer_action": "想像する", "ending_type": "どんでん返し",
         "first_line_ja": "ある朝、ドアの前に箱がありました。", "structure_ko": ["장면 1: 상자", "장면 2: 반전"]}
    r = plan_check.compare_candidates([a, b, c])
    assert "C" in r["flagged"] and "B" not in r["flagged"]
    assert "품질 점수가 아닙니다" in r["note"]


def test_prompt_requires_experience_differences():
    t = prompts.plan_prompt({"topic": "x"}, [], "", None, {"source_mode": "flow"})
    assert "viewer_action" in t and "ending_type" in t and "同じ案" in t and "断定しない" in t
    assert "推定" in t and "Typecast" in t


def test_similar_flag_and_regenerate_one(client, configure_ai, mock_relay):
    configure_ai(model="mock-similar")
    p = client.post("/api/projects", json={"name": "기획 비교"}).json()["project"]
    j = wait_job(client, client.post(f"/api/projects/{p['id']}/ai/plan", json={}).json())
    assert j["status"] == "done"
    sim = j["result"]["similarity"]
    assert list(sim["flagged"]) == ["C"]
    p["plan_candidates"] = {k: v for k, v in j["result"].items() if k != "log"}
    p = client.put(f"/api/projects/{p['id']}", json={"project": p, "base_rev": p["rev"]}).json()["project"]
    before = len(mock_relay.LOG)
    j2 = wait_job(client, client.post(f"/api/projects/{p['id']}/ai/plan_one", json={"candidate_id": "C"}).json())
    assert j2["status"] == "done" and len(mock_relay.LOG) - before == 1  # 추가 요청은 정확히 1회
    assert j2["result"]["candidate"]["id"] == "C" and j2["result"]["candidate"]["content_type"] == "短い物語・場面"
    assert not j2["result"]["similarity"]["flagged"]
    assert j2["result"]["log"]["usage"]["output_tokens"] > 0
    # 요청에 '비슷함' 이유와 다른 후보 요약이 들어간다
    assert "A안과 비슷함" in mock_relay.LOG[-1]["prompt_head"] or "1案だけ" in mock_relay.LOG[-1]["prompt_head"]
    configure_ai()


def test_channel_seed_upgrade_keeps_user_edits(tmp_path, monkeypatch):
    v1 = json.loads(channel.SEED_FILE.with_name("channel_seed_v1.json").read_text(encoding="utf-8"))
    v1.pop("seed_version", None)
    v1["profile"]["audience"] = "내가 직접 고친 시청자"
    v1["topics"][0]["angle_ko"] = "내가 고친 관점"
    v1["topics"].append({"id": "t_mine", "no": 31, "title_ja": "自作", "title_ko": "직접", "angle_ko": "직접 관점", "status": "unused"})
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "channel.json").write_text(json.dumps(v1, ensure_ascii=False), encoding="utf-8")
    data = channel.load()
    new = json.loads(channel.SEED_FILE.read_text(encoding="utf-8"))
    assert data["seed_version"] == 2
    assert data["profile"]["audience"] == "내가 직접 고친 시청자"           # 사용자 수정 유지
    assert data["profile"]["format"] == new["profile"]["format"]          # 고치지 않은 기본값만 개선
    assert data["topics"][0]["angle_ko"] == "내가 고친 관점"
    assert data["topics"][1]["angle_ko"] == new["topics"][1]["angle_ko"]
    assert any(t["id"] == "t_mine" for t in data["topics"])
    assert list(tmp_path.glob("channel_backup_*.json"))
