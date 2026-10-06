"""채널 기본 설정·주제 목록 검증."""
import json
import re

from conftest import wait_job
from app import channel
from app.jp_text import lint_text

BANNED = r"3秒|本当の性格|当たる|絶対|毎日|明日も|また明日"


def test_seed_is_clean():
    seed = json.loads(channel.SEED_FILE.read_text(encoding="utf-8"))
    assert len(seed["topics"]) == 30 and len({t["id"] for t in seed["topics"]}) == 30
    for t in seed["topics"]:
        text = " ".join(str(t[k]) for k in ("title_ja", "title_ko", "angle_ko", "first_line_ja", "example_choices"))
        assert not re.search(BANNED, text), t["title_ja"]
        assert not [i for i in lint_text(t["title_ja"] + t["first_line_ja"]) if i["category"] in ("약속", "과장", "근거")]  # 必ず(가방에 꼭) 같은 일상 표현은 제외
        assert "성격 유형을 보여준다" not in t["angle_ko"]
    prof = {k: v for k, v in seed["profile"].items() if k != "rules"}
    assert not re.search(r"毎日|明日も|3秒", json.dumps(prof, ensure_ascii=False))
    assert "주 3회" in seed["profile"]["upload_plan"]


def test_channel_flow(client, configure_ai, mock_relay):
    data = client.get("/api/channel").json()
    assert len(data["topics"]) == 30 and data["profile"]["audience"]
    # 빈 새 프로젝트도 채널 설정으로 미리 채워짐
    blank = client.post("/api/projects", json={"name": "빈"}).json()["project"]
    assert blank["idea"]["audience"] == data["profile"]["audience"] and blank["idea"]["content_kind"] == "entertainment"
    # 주제로 시작
    t = data["topics"][16]  # 카레 토핑
    p = client.post(f"/api/channel/topics/{t['id']}/start").json()["project"]
    assert "カレー" in p["idea"]["topic"] and "참고용" in p["idea"]["notes"] and p["idea"]["topic_id"] == t["id"]
    assert p["idea"]["avoid"] == t["caution_ko"]
    d2 = client.get("/api/channel").json()
    t2 = next(x for x in d2["topics"] if x["id"] == t["id"])
    assert t2["status"] == "used" and p["id"] in t2["live_project_ids"]
    # 프로필 수정 저장 → AI 요청에 채널 설정이 함께 들어감
    d2["profile"]["tone"] = "テスト用トーン規則"
    assert client.put("/api/channel", json={"channel": d2}).status_code == 200
    configure_ai()
    before = len(mock_relay.LOG)
    j = wait_job(client, client.post(f"/api/projects/{p['id']}/ai/plan", json={}).json())
    assert j["status"] == "done"
    head = mock_relay.LOG[before]["prompt_head"]
    assert head.startswith("## チャンネル設定") and "テスト用トーン規則" in head
    # 주제 추가·다시 불러오기
    r = client.post("/api/channel/topics", json={"text": "好きなおにぎりの具は？|좋아하는 주먹밥 속은?\n\n"}).json()
    assert r["added"] == 1 and r["channel"]["topics"][-1]["title_ko"] == "좋아하는 주먹밥 속은?"
    d3 = r["channel"]
    d3["topics"] = [x for x in d3["topics"] if x["id"] != "t01"]
    client.put("/api/channel", json={"channel": d3})
    assert client.post("/api/channel/reseed").json()["added"] == 1
