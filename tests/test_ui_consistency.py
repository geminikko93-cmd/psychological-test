"""화면 시험(실제 Chrome, 헤드리스): '작은 방' 예제로 화풍 확정 → 기준 이미지 → 요소 확정 → 클립 계획·연결 →
가변 설명 입력 시 최종 프롬프트 자동 조립·충돌 표시 → 직접 수정·되돌리기 → 영상 넣기 → 끝 프레임 → 검수."""
import time

from app import storage
from app.util import ffmpeg_exe, run_tool


def _wait(pg, cond_js, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pg.evaluate(cond_js):
            return True
        time.sleep(0.2)
    raise AssertionError("화면 조건 시간 초과: " + cond_js)


def _saved(pg):
    _wait(pg, "() => !document.querySelector('.savestate.dirty') && !/저장 중/.test(document.querySelector('.savestate')?.textContent || '')")


def test_consistency_ui_flow(ui_server, browser, client, tmp_path):
    pid = client.post("/api/examples/small-room", json={}).json()["project"]["id"]
    png = tmp_path / "room.png"
    run_tool([ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=beige:s=90x160", "-frames:v", "1", str(png)])
    mp4 = tmp_path / "intro.mp4"
    run_tool([ffmpeg_exe(), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=180x320:d=4:r=24", "-pix_fmt", "yuv420p", str(mp4)])
    pg = browser.new_page(viewport={"width": 1300, "height": 1000})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: d.dismiss())

    # 5단계: 화풍 확정, 기준 이미지 올리기, 요소 확정
    pg.goto(f"{ui_server}/#/p/{pid}/visual")
    pg.wait_for_selector("text=이 화풍으로 확정(잠금)")
    pg.click("text=이 화풍으로 확정(잠금)")
    pg.wait_for_selector("text=확정됨 v1")
    room = pg.locator(".element").first
    room.locator("input[type=file]").set_input_files(str(png))
    room.locator("button", has_text="이미지 올리기").click()
    pg.wait_for_selector(".element .cand-img.picked")
    pg.locator(".element").first.locator("button", has_text="확정(잠금)").click()
    _wait(pg, "() => document.querySelector('.element.locked') !== null")
    _saved(pg)
    p = storage.load_project(pid)
    assert p["visual"]["style"]["locked"] and p["visual"]["elements"][0]["locked"] and p["visual"]["elements"][0]["primary_ref_id"]
    assert p["visual"]["assets"][0]["file"].startswith("media/visual/reference/")

    # 6단계: 클립 계획 → 카드에 연결·생성 방식·Flow 설정 표시
    pg.goto(f"{ui_server}/#/p/{pid}/sources")
    pg.click("text=클립 계획 만들기")
    pg.wait_for_selector("#page .clip")
    assert pg.locator("#page .clip").count() == 6
    first = pg.locator("#page .clip").first
    _wait(pg, "() => document.querySelector('.clip .flow-settings') !== null")
    assert "Gemini Omni Flash 1.1" in first.locator(".flow-settings").inner_text()
    # 가변 설명 입력 → 최종 프롬프트 자동 조립(고정 묘사 원문 포함)
    var = first.locator("textarea").first
    var.fill("0-4s: slow push-in; curtains sway slightly. A person in a black jacket.")
    _wait(pg, "() => (document.querySelector('.clip .final textarea')?.value || '').includes('slow push-in')")
    final = first.locator(".final textarea").input_value()
    room_fixed = p["visual"]["elements"][0]["fixed_en"]
    assert room_fixed in final and "No on-screen text" in final and p["visual"]["style"]["style_en"] in final
    # 직접 수정 → 고정 블록 자동 반영 안 됨 → 자동 조립으로 되돌리기
    first.locator("button", has_text="직접 수정").click()
    first = pg.locator("#page .clip").first
    first.locator(".final textarea").fill("MY MANUAL PROMPT")
    _saved(pg)
    assert storage.load_project(pid)["flow"]["clips"][0]["prompt_source"] == "manual"
    first.locator("button", has_text="자동 조립으로 되돌리기").click()
    first = pg.locator("#page .clip").first
    _wait(pg, "() => (document.querySelector('.clip .final textarea')?.value || '').includes('slow push-in')")
    _saved(pg)
    c0 = storage.load_project(pid)["flow"]["clips"][0]
    assert c0["prompt_source"] == "composed" and c0["prompt_prev"]["en"] == "MY MANUAL PROMPT"
    # 영상 넣기 → 실제 사용 끝 프레임 추출
    first.locator(".clip-file input[type=file]").set_input_files(str(mp4))
    first.locator(".clip-file button", has_text="넣기").click()
    pg.wait_for_selector("#page .clip.has-file")
    pg.locator("#page .clip").first.locator("button", has_text="실제 사용 끝 프레임 추출").click()
    pg.wait_for_selector("text=다음 클립(S2-C1) 시작 프레임으로 연결")
    _saved(pg)
    p = storage.load_project(pid)
    fr = p["visual"]["frames"][-1]
    c0 = p["flow"]["clips"][0]
    assert fr["kind"] == "last_used" and fr["t"] < c0["need_s"]

    # 7단계: 검수(사람 확인) — 프레임 추출 후 통과 표시
    pg.goto(f"{ui_server}/#/p/{pid}/review")
    pg.wait_for_selector("text=자동으로 판정하지 않습니다")
    card = pg.locator(".card.review").first
    card.locator("button", has_text="시작·중간·끝 프레임 추출").click()
    _wait(pg, "() => document.querySelectorAll('.card.review')[0]?.querySelectorAll('.thumb img').length >= 3")
    card = pg.locator(".card.review").first
    card.locator("button", has_text="통과").click()
    _wait(pg, "() => /검수 통과/.test(document.querySelector('.card.review')?.textContent || '')")
    _saved(pg)
    p = storage.load_project(pid)
    assert p["flow"]["clips"][0]["review"]["state"] == "pass" and p["flow"]["clips"][0]["review"]["basis"]["video"]
    # 기준이 바뀌면 재검토: 사용 시작을 바꾸면 '재검토 필요'
    pg.goto(f"{ui_server}/#/p/{pid}/sources")
    pg.wait_for_selector("#page .clip.has-file")
    num = pg.locator("#page .clip").first.locator(".clip-file input.num")
    num.fill("0.5")
    _wait(pg, "() => /재검토 필요/.test(document.querySelector('.clip .live')?.textContent || '')")
    assert not errs, errs
