"""자동 저장 재현 시험(실제 Chrome + 같은 프로세스 서버).

A) 첫 저장 요청이 진행 중일 때 추가로 고친 내용이 실제 파일에 남아야 한다.
B) 저장 충돌(409) 후 '서버 내용 다시 불러오기'가 멈추지 않아야 한다.
"""
import json
import time

import pytest

from app import storage
from app.util import UserError


def _new_project(name):
    return storage.create_project(name)


def _open(browser, base, pid):
    pg = browser.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(f"{base}/#/p/{pid}/plan")
    pg.wait_for_selector("text=아이디어", timeout=10000)
    return pg, errs


def test_edit_during_inflight_save_is_saved(ui_server, browser, monkeypatch):
    p = _new_project("저장 지연 시험")
    real = storage.save_project
    calls = {"n": 0}

    def slow_save(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(2.0)  # 첫 저장 요청을 느리게
        return real(*a, **k)

    monkeypatch.setattr(storage, "save_project", slow_save)
    pg, errs = _open(browser, ui_server, p["id"])
    topic = pg.locator("textarea").nth(0)
    topic.fill("첫 번째 수정")
    pg.wait_for_timeout(1200)          # 디바운스 후 첫 저장 요청이 진행 중
    topic.fill("첫 번째 수정 + 두 번째 수정")  # 진행 중에 추가 수정
    # 저장 완료 표시가 나올 때까지 기다림
    pg.wait_for_function("() => document.querySelector('.savestate')?.textContent.startsWith('저장됨')", timeout=15000)
    saved = storage.load_project(p["id"])
    assert saved["idea"]["topic"] == "첫 번째 수정 + 두 번째 수정", saved["idea"]["topic"]
    assert calls["n"] >= 2
    assert not errs, errs
    pg.close()


def test_conflict_reload_does_not_hang(ui_server, browser, monkeypatch):
    p = _new_project("충돌 시험")
    real = storage.save_project
    calls = {"n": 0}

    def conflict_once(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise UserError("다른 창이나 작업에서 이 프로젝트가 먼저 저장되었습니다.", "", status=409)
        return real(*a, **k)

    monkeypatch.setattr(storage, "save_project", conflict_once)
    pg, errs = _open(browser, ui_server, p["id"])
    pg.locator("textarea").nth(0).fill("충돌 날 수정")
    pg.wait_for_selector("text=저장 충돌", timeout=10000)
    t0 = time.time()
    pg.click("text=서버 내용 다시 불러오기")
    pg.wait_for_selector(".modal-back", state="detached", timeout=5000)
    pg.wait_for_selector("text=다시 불러왔습니다", timeout=5000)
    assert time.time() - t0 < 5
    # 다시 불러온 뒤에도 편집·저장이 정상 동작
    pg.locator("textarea").nth(0).fill("다시 불러온 뒤 수정")
    pg.wait_for_function("() => document.querySelector('.savestate')?.textContent.startsWith('저장됨')", timeout=10000)
    pg.wait_for_timeout(300)
    assert storage.load_project(p["id"])["idea"]["topic"] == "다시 불러온 뒤 수정"
    assert not errs, errs
    pg.close()


def test_navigation_blocked_when_save_fails(ui_server, browser, monkeypatch):
    p = _new_project("저장 실패 이동 시험")

    def fail(*a, **k):
        raise UserError("디스크 오류(시험)", "", status=500)

    monkeypatch.setattr(storage, "save_project", fail)
    pg, errs = _open(browser, ui_server, p["id"])
    pg.locator("textarea").nth(0).fill("저장 안 될 수정")
    pg.wait_for_timeout(1500)
    pg.click("#sidebar button:has-text('프로젝트 목록')")
    pg.wait_for_selector("text=저장되지 않은 변경", timeout=5000)
    pg.click("text=머무르기")
    pg.wait_for_timeout(500)
    assert "/plan" in pg.url and pg.locator("textarea").nth(0).input_value() == "저장 안 될 수정"
    pg.close()
