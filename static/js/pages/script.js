import { api } from "../api.js";
import { state, changed, flush } from "../state.js";
import { h, clear, section, field, bindInput, select, confirmBox, modal, toast, empty, fmtTime, copyText, showError } from "../ui.js";
import { runAi } from "../aiflow.js";
import { go, rerender } from "../nav.js";

const PRESETS = [
  ["hook", "첫 문장만 다시 만들기"],
  ["payoff", "결과가 뻔함 → 구체적으로"],
  ["natural", "일본어를 더 자연스럽고 짧게"],
  ["faster", "전개를 빠르게"],
  ["sources", "영상소스 구하기 쉬운 장면으로"],
  ["overlap", "이전 영상과 겹치는 부분 바꾸기"],
  ["", "직접 지시만 사용"],
];

const rid = (p) => `${p}_${Math.random().toString(16).slice(2, 10)}`;
const newLine = () => ({ id: rid("l"), display: "", tts: "", ko: "", reading_note: "", caution: "", reading_hint: "" });
const newScene = () => ({ id: rid("s"), purpose: "", visual: "", search_keywords: [], edit_intent: "", hold_ms: 0, hold_reason: "", fact_check: "", fact_note: "", no_source_needed: false, lines: [newLine()] });

function snapshot(reason) {
  const s = state.project.script;
  if (!s.scenes?.length) return;
  s.history = [...(s.history || []), { at: new Date().toISOString(), reason, scenes: JSON.parse(JSON.stringify(s.scenes)) }].slice(-15);
}

const selected = { lines: new Set(), scenes: new Set() };

export async function renderScript(page) {
  const p = state.project;
  const s = p.script;
  page.append(h("div", { class: "page-head" }, h("h1", {}, "2. 대본"),
    h("div", { class: "muted" }, "화면 표시용 일본어와 TTS 낭독용 일본어를 따로 고칠 수 있습니다. 한자·숫자·고유명사는 낭독 칸에서 가나로 바꾸면 타입캐스트가 의도대로 읽습니다.")));

  if (!p.plan) {
    page.append(section(null, empty("먼저 기획 방향을 선택하세요."), h("button", { onclick: () => go("plan") }, "← 기획으로")));
  }

  // 기획 변경 감지
  if (s.scenes?.length && p.plan?.updated_at && s.based_on_plan_at && p.plan.updated_at > s.based_on_plan_at) {
    page.append(h("div", { class: "banner stale" }, "대본을 만든 뒤 기획이 수정되었습니다. 필요한 부분만 다시 생성하거나, 현재 대본을 유지하세요.",
      h("button", { class: "small", onclick: () => { s.based_on_plan_at = p.plan.updated_at; changed(true); rerender(); } }, "현재 대본 유지(확인)")));
  }

  const notes = { text: "" };
  page.append(section("AI 대본 생성",
    field("추가 지시(선택)", bindInput(notes, "text", () => {}, { multiline: true, rows: 2, placeholder: "예: 결과는 2개로 / 마지막에 댓글 질문 없이 끝내기 / 존댓말 통일" })),
    h("div", { class: "row" },
      h("button", { class: "primary", disabled: !p.plan, onclick: async () => {
        if (s.scenes?.length && !(await confirmBox("대본 새로 만들기", "현재 대본 전체를 새 대본으로 바꿉니다.\n현재 대본은 '대본 기록'에 보관되어 되돌릴 수 있습니다.", "새로 만들기"))) return;
        const r = await runAi("script", { notes: notes.text });
        if (!r) return;
        snapshot("AI 새 대본 생성 전");
        Object.assign(s, { scenes: r.scenes, title_candidates: r.title_candidates, claims_level_ko: r.claims_level_ko,
          self_check_ko: r.self_check_ko, generated_at: r.generated_at, based_on_plan_at: p.plan?.updated_at });
        if (r.content_kind && ["entertainment", "factual"].includes(r.content_kind) && p.idea.content_kind === "undecided") p.idea.content_kind = r.content_kind;
        changed(true); rerender();
      } }, s.scenes?.length ? "대본 전체 새로 만들기" : "AI 대본 생성"),
      h("button", { onclick: () => { snapshot("직접 장면 추가 전"); s.scenes = [...(s.scenes || []), newScene()]; changed(true); rerender(); } }, "+ 장면 직접 추가"),
      s.generated_at ? h("span", { class: "muted small" }, `AI 생성 ${fmtTime(s.generated_at)}`) : null),
    s.claims_level_ko ? h("div", { class: "note" }, h("b", {}, "주장 수준: "), s.claims_level_ko) : null,
    s.self_check_ko?.length ? h("details", {}, h("summary", {}, "AI 자체 점검(참고)"), h("ul", {}, s.self_check_ko.map((x) => h("li", {}, x)))) : null,
    s.title_candidates?.length ? h("details", {}, h("summary", {}, `제목 후보 ${s.title_candidates.length}개`),
      h("ul", {}, s.title_candidates.map((t) => h("li", {}, h("span", { class: "jp" }, t.ja), " — ", t.ko,
        h("button", { class: "small", onclick: () => { p.publish.title = t.ja; changed(); toast("게시 제목으로 넣었습니다(게시·내보내기 단계에서 수정 가능).", "ok"); } }, "게시 제목으로"))))) : null));

  if (!s.scenes?.length) {
    page.append(section("대본", empty("아직 대본이 없습니다.")));
    return;
  }

  // 장면 편집
  const scenesBox = h("div", {});
  s.scenes.forEach((sc, si) => scenesBox.append(sceneCard(sc, si)));
  page.append(scenesBox);

  page.append(partialPanel());
  page.append(reviewPanel());
  const checksBox = h("div", {});
  page.append(section("표현 점검 · 이전 영상과 겹침", h("div", { class: "muted small" }, "규칙 기반 자동 점검입니다(과장·단정·근거·업로드 약속 표현·숫자 읽기·이전 프로젝트와 유사한 문장)."),
    h("button", { onclick: () => loadChecks(checksBox) }, "다시 점검"), checksBox));
  loadChecks(checksBox);
  page.append(historyPanel());
  page.append(h("div", { class: "row end" }, h("button", { class: "primary", onclick: () => go("audio") }, "음성 단계로 →")));
}

function sceneCard(sc, si) {
  const p = state.project;
  const s = p.script;
  const save = () => changed();
  const move = (d) => {
    const j = si + d;
    if (j < 0 || j >= s.scenes.length) return;
    [s.scenes[si], s.scenes[j]] = [s.scenes[j], s.scenes[si]];
    changed(true); rerender();
  };
  const kw = { text: (sc.search_keywords || []).join(", ") };
  const chk = h("input", { type: "checkbox", checked: selected.scenes.has(sc.id), title: "부분 재생성 범위에 포함" });
  chk.addEventListener("change", () => { chk.checked ? selected.scenes.add(sc.id) : selected.scenes.delete(sc.id); });
  const card = h("section", { class: "card scene" },
    h("div", { class: "scene-head" },
      h("label", { class: "chk" }, chk), h("h3", {}, `장면 ${si + 1}`),
      bindInput(sc, "purpose", save, { class: "grow", placeholder: "이 장면의 역할" }),
      h("button", { class: "small", title: "이 장면 낭독문 복사", onclick: () => copyText(sc.lines.map((l) => l.tts).filter(Boolean).join("\n"), `장면 ${si + 1} 낭독문`) }, "낭독문 복사"),
      h("button", { class: "small", onclick: () => move(-1), disabled: si === 0 }, "↑"),
      h("button", { class: "small", onclick: () => move(1), disabled: si === s.scenes.length - 1 }, "↓"),
      h("button", { class: "small danger", onclick: async () => {
        if (!(await confirmBox("장면 삭제", `장면 ${si + 1}을 삭제합니다. (대본 기록에 보관됨)`, "삭제", true))) return;
        snapshot(`장면 ${si + 1} 삭제 전`);
        s.scenes.splice(si, 1); changed(true); rerender();
      } }, "삭제")),
    h("div", { class: "lines" }, sc.lines.map((ln, li) => lineRow(sc, ln, li))),
    h("button", { class: "small", onclick: () => { sc.lines.push(newLine()); changed(true); rerender(); } }, "+ 문장 추가"),
    h("details", { class: "scene-meta", open: !sc.visual },
      h("summary", {}, "장면 설명 · 화면 · 소스 · 편집 의도"),
      h("div", { class: "grid2" },
        field("권장 화면 내용", bindInput(sc, "visual", save, { multiline: true, rows: 2 })),
        field("편집 의도", bindInput(sc, "edit_intent", save, { multiline: true, rows: 2 }))),
      h("div", { class: "grid3" },
        field("검색 키워드(쉼표 구분)", bindInput(kw, "text", () => { sc.search_keywords = kw.text.split(",").map((x) => x.trim()).filter(Boolean); save(); })),
        field("의도적 멈춤(ms)", bindInput(sc, "hold_ms", save, { type: "number" }), "시청자가 고르거나 생각할 시간. 음성 단계에서 이 구간을 유지하도록 지정하세요."),
        field("멈춤 이유", bindInput(sc, "hold_reason", save))),
      sc.fact_check ? h("div", { class: "note warn" }, h("b", {}, "근거 확인 필요(AI 메모): "), sc.fact_check) : null,
      field("근거 메모(출처 URL 등, 직접 확인한 것만)", bindInput(sc, "fact_note", save, { multiline: true, rows: 2 }))));
  return card;
}

function lineRow(sc, ln, li) {
  const save = () => changed();
  const chk = h("input", { type: "checkbox", checked: selected.lines.has(ln.id), title: "부분 재생성 범위에 포함" });
  chk.addEventListener("change", () => { chk.checked ? selected.lines.add(ln.id) : selected.lines.delete(ln.id); });
  const hint = h("div", { class: "muted small" }, ln.reading_hint ? `읽기 추정(사전 기반, 틀릴 수 있음): ${ln.reading_hint}` : "");
  const disp = bindInput(ln, "display", save, { multiline: true, rows: 2, class: "full jp" });
  const tts = bindInput(ln, "tts", save, { multiline: true, rows: 2, class: "full jp" });
  disp.addEventListener("change", async () => {
    try { const r = await api.post("/api/jp/tools", { text: ln.display }); ln.reading_hint = r.reading; hint.textContent = r.reading ? `읽기 추정(사전 기반, 틀릴 수 있음): ${r.reading}` : ""; save(); } catch { /* 무시 */ }
  });
  return h("div", { class: "line" },
    h("div", { class: "line-num" }, h("label", { class: "chk" }, chk), `${li + 1}`),
    h("div", { class: "line-body" },
      h("div", { class: "grid2" },
        field("화면 표시(자막)", disp, "Enter로 줄바꿈하면 자막 줄바꿈 위치로 존중됩니다."),
        field("TTS 낭독(타입캐스트용)", tts)),
      h("div", { class: "row tight" },
        h("button", { class: "small", onclick: () => { ln.tts = ln.display.replace(/\n/g, ""); tts.value = ln.tts; save(); } }, "화면 → 낭독 복사"),
        hint),
      h("div", { class: "grid3" },
        field("한국어 의미", bindInput(ln, "ko", save, { multiline: true, rows: 2 })),
        field("발음·읽기 메모", bindInput(ln, "reading_note", save, { multiline: true, rows: 2 })),
        field("표현 주의", bindInput(ln, "caution", save, { multiline: true, rows: 2 })))),
    h("div", { class: "line-actions" },
      h("button", { class: "small", onclick: () => { sc.lines.splice(li + 1, 0, newLine()); changed(true); rerender(); } }, "+"),
      h("button", { class: "small danger", onclick: async () => {
        if (!(await confirmBox("문장 삭제", "이 문장을 삭제합니다.", "삭제", true))) return;
        snapshot("문장 삭제 전"); sc.lines.splice(li, 1); changed(true); rerender();
      } }, "−")));
}

function partialPanel() {
  const p = state.project;
  const opt = { preset: "hook", scope: "auto", text: "" };
  const scopeSel = select([["auto", "자동(선택 항목, 없으면 프리셋 기본 범위)"], ["selected", "체크한 문장·장면만"], ["first", "첫 장면"], ["last", "마지막 장면"], ["all", "대본 전체(장면 추가·삭제 허용)"]], "auto", (v) => (opt.scope = v));
  return section("필요한 부분만 다시 만들기",
    h("div", { class: "muted small" }, "장면 왼쪽 체크박스나 문장 번호 옆 체크박스로 범위를 고를 수 있습니다. 결과는 바로 바뀌지 않고, 비교 화면에서 확인한 뒤 적용합니다."),
    h("div", { class: "grid3" },
      field("무엇을 고칠까요", select(PRESETS, "hook", (v) => (opt.preset = v))),
      field("범위", scopeSel),
      field("추가 지시", bindInput(opt, "text", () => {}, { placeholder: "예: 결과를 생활 속 장면으로" }))),
    h("button", { class: "primary", onclick: async () => {
      const sc = p.script.scenes;
      let scope;
      const selL = [...selected.lines], selS = [...selected.scenes];
      if (opt.scope === "all") scope = { all: true };
      else if (opt.scope === "first") scope = { scene_ids: [sc[0].id] };
      else if (opt.scope === "last") scope = { scene_ids: [sc[sc.length - 1].id] };
      else if (opt.scope === "selected" || selL.length || selS.length) {
        if (!selL.length && !selS.length) { toast("체크한 문장이나 장면이 없습니다.", "info"); return; }
        scope = { line_ids: selL, scene_ids: selS };
      } else if (opt.preset === "hook") scope = { line_ids: [sc[0].lines[0].id] };
      else if (opt.preset === "payoff") scope = { scene_ids: sc.slice(-Math.min(2, sc.length)).map((x) => x.id) };
      else scope = { all: true };
      if (!opt.preset && !opt.text.trim()) { toast("무엇을 고칠지 선택하거나 지시를 적어 주세요.", "info"); return; }
      const r = await runAi("partial", { scope, preset: opt.preset, instruction: opt.text });
      if (r) await previewChanges(r);
    } }, "다시 만들기 (미리보기)"));
}

function findLine(id) {
  for (const sc of state.project.script.scenes) for (const ln of sc.lines) if (ln.id === id) return ln;
  return null;
}

async function previewChanges(r) {
  const s = state.project.script;
  const body = h("div", {});
  if (r.explanation_ko) body.append(h("div", { class: "note" }, r.explanation_ko));
  const keep = r.changes.map(() => ({ on: true }));
  const sceneIdx = (id) => s.scenes.findIndex((x) => x.id === id) + 1;
  const linesView = (lines) => h("div", {}, lines.map((l) => h("div", { class: "diffline" }, h("div", { class: "jp" }, l.display), l.tts !== l.display ? h("div", { class: "muted small jp" }, "낭독: " + l.tts) : null, h("div", { class: "ko" }, l.ko))));
  r.changes.forEach((ch, i) => {
    const cb = h("input", { type: "checkbox", checked: true });
    cb.addEventListener("change", () => (keep[i].on = cb.checked));
    let title, before, after;
    if (ch.op === "replace_line") {
      const old = findLine(ch.line_id);
      title = "문장 바꾸기"; before = old ? linesView([old]) : "-"; after = linesView([ch.line]);
    } else if (ch.op === "replace_scene") {
      const old = s.scenes.find((x) => x.id === ch.scene_id);
      title = `장면 ${sceneIdx(ch.scene_id)} 바꾸기`; before = old ? linesView(old.lines) : "-"; after = linesView(ch.scene.lines);
    } else if (ch.op === "insert_scene_after") {
      title = ch.after_scene_id ? `장면 ${sceneIdx(ch.after_scene_id)} 뒤에 새 장면` : "맨 앞에 새 장면"; before = "(없음)"; after = linesView(ch.scene.lines);
    } else {
      const old = s.scenes.find((x) => x.id === ch.scene_id);
      title = `장면 ${sceneIdx(ch.scene_id)} 삭제`; before = old ? linesView(old.lines) : "-"; after = "(삭제)";
    }
    body.append(h("div", { class: "diff" }, h("label", { class: "chk" }, cb, h("b", {}, title)), h("div", { class: "grid2" }, h("div", { class: "before" }, h("small", {}, "현재"), before), h("div", { class: "after" }, h("small", {}, "제안"), after))));
  });
  if (r.rejected_out_of_scope?.length) body.append(h("div", { class: "muted small" }, `범위 밖 변경 ${r.rejected_out_of_scope.length}건은 적용 대상에서 제외했습니다.`));
  const ok = await modal("AI 제안 비교", body, [{ label: "적용 안 함", value: false }, { label: "체크한 변경 적용", value: true, primary: true }], { wide: true });
  if (!ok) return;
  snapshot("부분 재생성 적용 전");
  r.changes.forEach((ch, i) => {
    if (!keep[i].on) return;
    if (ch.op === "replace_line") {
      for (const sc of s.scenes) { const k = sc.lines.findIndex((l) => l.id === ch.line_id); if (k >= 0) sc.lines[k] = ch.line; }
    } else if (ch.op === "replace_scene") {
      const k = s.scenes.findIndex((x) => x.id === ch.scene_id);
      if (k >= 0) { const old = s.scenes[k]; s.scenes[k] = { ...ch.scene, fact_note: old.fact_note || "", no_source_needed: old.no_source_needed || false }; }
    } else if (ch.op === "insert_scene_after") {
      const k = ch.after_scene_id ? s.scenes.findIndex((x) => x.id === ch.after_scene_id) + 1 : 0;
      s.scenes.splice(k, 0, ch.scene);
    } else if (ch.op === "delete_scene") {
      s.scenes = s.scenes.filter((x) => x.id !== ch.scene_id);
    }
  });
  selected.lines.clear(); selected.scenes.clear();
  changed(true); rerender();
  toast("적용했습니다. 이전 대본은 '대본 기록'에 있습니다.", "ok");
}

function reviewPanel() {
  const p = state.project;
  const rv = p.script.review;
  const box = section("AI 일본어·표현 검토",
    h("div", { class: "note warn" }, "AI 검토는 원어민 검수가 아닙니다. 한국어 설명과 제안을 보고 직접 판단하세요."),
    h("button", { onclick: async () => {
      const r = await runAi("review", {});
      if (r) { delete r.log; p.script.review = r; changed(true); rerender(); }
    } }, rv ? "다시 검토" : "AI 검토 받기"));
  if (!rv) return box;
  box.append(h("div", { class: "muted small" }, `${rv.label} · ${fmtTime(rv.reviewed_at)}`));
  if (rv.overall_ko) box.append(h("div", { class: "note" }, h("b", {}, "전체: "), rv.overall_ko));
  if (rv.consistency_ko) box.append(h("div", { class: "note" }, h("b", {}, "제목·대본·설명 일관성: "), rv.consistency_ko));
  for (const it of rv.items || []) {
    const ln = it.line_id ? findLine(it.line_id) : null;
    box.append(h("div", { class: `review ${it.severity}` },
      h("div", {}, h("span", { class: `sev ${it.severity}` }, { high: "중요", mid: "보통", low: "참고" }[it.severity]), " ", h("b", {}, it.category_ko || ""), " ", ln ? h("span", { class: "jp muted" }, ln.display) : null),
      h("div", {}, it.issue_ko),
      it.suggestion_display_ja || it.suggestion_tts_ja ? h("div", { class: "sugg" },
        it.suggestion_display_ja ? h("div", { class: "jp" }, "화면 제안: " + it.suggestion_display_ja) : null,
        it.suggestion_tts_ja ? h("div", { class: "jp" }, "낭독 제안: " + it.suggestion_tts_ja) : null,
        it.suggestion_ko ? h("div", { class: "ko" }, it.suggestion_ko) : null,
        ln ? h("button", { class: "small", onclick: () => {
          snapshot("검토 제안 적용 전");
          if (it.suggestion_display_ja) ln.display = it.suggestion_display_ja;
          if (it.suggestion_tts_ja) ln.tts = it.suggestion_tts_ja;
          else if (it.suggestion_display_ja) ln.tts = it.suggestion_display_ja;
          if (it.suggestion_ko) ln.ko = it.suggestion_ko;
          changed(true); rerender();
        } }, "이 제안 적용") : null) : null));
  }
  return box;
}

async function loadChecks(box) {
  clear(box).append(h("div", { class: "muted" }, "점검 중…"));
  try {
    await flush();
    const r = await api.get(`/api/projects/${state.project.id}/checks`);
    clear(box);
    if (!r.lint.length && !r.overlap.length) { box.append(h("div", { class: "ok-text" }, "점검 항목이 없습니다.")); return; }
    if (r.lint.length) box.append(h("table", { class: "tbl" }, h("tr", {}, h("th", {}, "위치"), h("th", {}, "분류"), h("th", {}, "해당"), h("th", {}, "안내")),
      r.lint.map((x) => h("tr", {}, h("td", {}, x.where), h("td", {}, x.category), h("td", { class: "jp" }, x.match), h("td", {}, x.message)))));
    if (r.overlap.length) box.append(h("h4", {}, "이전 프로젝트와 비슷한 문장"), h("table", { class: "tbl" }, h("tr", {}, h("th", {}, "현재"), h("th", {}, "이전 프로젝트"), h("th", {}, "유사도")),
      r.overlap.map((x) => h("tr", {}, h("td", { class: "jp" }, x.text), h("td", {}, x.other_project, h("div", { class: "jp muted" }, x.other_text)), h("td", {}, x.score)))));
  } catch (e) { clear(box); showError(e); }
}

function historyPanel() {
  const s = state.project.script;
  const hist = s.history || [];
  return section("대본 기록(되돌리기)", hist.length ? hist.slice().reverse().map((hs, i) => h("div", { class: "row between hist" },
    h("span", {}, `${fmtTime(hs.at)} · ${hs.reason} · 장면 ${hs.scenes.length}개`),
    h("button", { class: "small", onclick: async () => {
      if (!(await confirmBox("대본 되돌리기", "현재 대본을 기록에 넣고 선택한 시점으로 되돌립니다.", "되돌리기"))) return;
      const idx = hist.length - 1 - i;
      const target = hist[idx];
      snapshot("되돌리기 전");
      s.scenes = JSON.parse(JSON.stringify(target.scenes));
      changed(true); rerender();
    } }, "되돌리기"))) : empty("기록이 없습니다. AI 생성·삭제·부분 재생성 전에 자동 보관됩니다."));
}
