// Google Flow 클립 계획·연결·프롬프트·생성 영상 (모델명·허용 길이는 서버 /api/flow/config 한 곳에서 받음)
// 최종 프롬프트 = [등장 요소 고정 묘사] + [장소·화풍] + [클립별 가변 설명] + [공통 제약] — 조립은 서버(app/visual.py)
import { api, mediaUrl } from "../api.js";
import { state, changed, flush } from "../state.js";
import { h, section, field, bindInput, select, confirmBox, toast, fmtSec, copyText, showError } from "../ui.js";
import { runAi, aiReady } from "../aiflow.js";
import { go, rerender } from "../nav.js";
import { vis, inspect, syncComposed, uploadImages, extractFrames, latestFrame, thumb, clipLabel, GEN_MODES, ROLES, elById, assetById } from "../vis.js";

let CFG = { durations: [], model_label: "", product_label: "Google Flow", aspect: "9:16" };
let INSP = {};
const dyn = new Map(); // clip id → 점검 결과로 다시 그리는 함수
let timer = null;

function flowState() {
  const p = state.project;
  p.flow = p.flow || { clips: [], style: null, based_on: null, timing: null };
  return p.flow;
}

// 편집할 때마다(잠시 뒤) 서버 점검 → 최종 프롬프트 재조립·충돌·검수 상태 갱신
export function refreshSoon(delay = 400) {
  clearTimeout(timer);
  timer = setTimeout(refreshNow, delay);
}
async function refreshNow() {
  const p = state.project;
  if (!p?.flow?.clips?.length) return;
  try {
    INSP = await inspect(p);
    syncComposed(p, INSP);
    for (const f of dyn.values()) f();
  } catch (e) { showError(e); }
}
const edited = () => { changed(); refreshSoon(); };

async function planClips(confirmDrop = true) {
  const p = state.project;
  const fl = flowState();
  await flush();
  try {
    const r = await api.post(`/api/projects/${p.id}/flow/plan`);
    const current = new Set((fl.clips || []).map((c) => c.id));
    const lost = r.dropped.filter((d) => current.has(d.id) && (d.had_prompt || d.item_id));
    if (confirmDrop && lost.length && !(await confirmBox("클립 계획 다시 계산",
      `시간이 바뀌어 기존 클립 ${lost.length}개의 구간이 달라집니다.\n그 클립의 프롬프트·연결 영상은 '이전 클립'으로 보관되고, 새 클립은 프롬프트를 다시 만들어야 합니다.\n등장 요소·참조·생성 방식·편집 지시 연결은 같은 장면의 겹치는 클립에서 이어받습니다.`, "다시 계산"))) return;
    // 이번 계획에 없는 클립(다른 제작 방식으로 바꾼 장면 등)은 프롬프트·파일 연결과 함께 보관, 다시 Flow로 돌아오면 되살림
    const keptIds = new Set(r.clips.map((c) => c.id));
    const pool = [...(fl.previous_clips || []), ...fl.clips].filter((c) => !keptIds.has(c.id));
    fl.previous_clips = [...new Map(pool.map((c) => [c.id, c])).values()].slice(-60);
    Object.assign(fl, { clips: r.clips, based_on: r.based_on, timing: r.timing, planned_at: new Date().toISOString() });
    changed(true);
    toast(`클립 ${r.clips.length}개를 계획했습니다(${r.timing === "실제" ? "최종 자막 시간 기준" : "대본 길이로 추정한 시간"}).`, "ok");
    rerender();
  } catch (e) { showError(e); }
}

async function makePrompts(clipIds, instruction) {
  const p = state.project;
  const fl = flowState();
  const targets = clipIds ? fl.clips.filter((c) => clipIds.includes(c.id)) : fl.clips;
  if (targets.some((c) => c.prompt_en || c.var_en) && !(await confirmBox("가변 설명 다시 만들기",
    "AI는 클립별 가변 설명(행동·구도·카메라·시간 배분)만 다시 씁니다. 확정한 화풍·등장 요소의 고정 묘사는 바뀌지 않습니다.\n직접 고른 등장 요소·생성 방식은 유지하고, 이전 프롬프트는 클립마다 1개씩 보관됩니다(되돌리기).", "만들기"))) return;
  const r = await runAi("flow", { clip_ids: clipIds, instruction }, { guard: () => JSON.stringify([fl.style, fl.clips.map((c) => [c.id, c.var_en, c.prompt_en, c.duration])]), what: "Flow 프롬프트·클립 길이" });
  if (!r) return;
  try {
    const out = await api.post("/api/flow/apply-ai", { project: p, result: r, clip_ids: clipIds });
    fl.clips = out.clips;
    fl.style = out.style;
    changed(true);
    rerender();
  } catch (e) { showError(e); }
}

export async function renderFlow(page) {
  const p = state.project;
  CFG = await api.get("/api/flow/config");
  const fl = flowState();
  const v = vis(p);
  const scenes = p.script.scenes;
  const items = p.sources?.items || [];
  const st = state.status?.steps?.sources;
  dyn.clear();

  const instr = { text: "" };
  page.append(section("클립 계획",
    h("div", { class: "note" },
      `${CFG.product_label}: ${CFG.durations.join("·")}초 클립. 제작 방식이 'Flow'인 장면만 클립을 계획합니다. 각 장면의 내레이션 구간을 문장 경계에서 나눠, 구간을 덮는 가장 짧은 길이를 고릅니다(남는 꼬리는 CapCut에서 잘라냄). `,
      "Flow 사이트에서 직접 생성합니다(자동 로그인·비공식 연동 없음)."),
    CFG.doc_note_ko ? h("div", { class: "muted small" }, CFG.doc_note_ko, " ", h("a", { href: CFG.doc_url, target: "_blank", rel: "noreferrer" }, "공식 도움말")) : null,
    st?.notes?.length && fl.clips?.length ? h("div", { class: "muted small" }, st.notes.join(" · ")) : null,
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: () => planClips(true) }, fl.clips?.length ? "클립 계획 다시 계산" : "클립 계획 만들기"),
      fl.clips?.length ? h("span", { class: "muted small" }, `클립 ${fl.clips.length}개 · 시간 기준: ${fl.timing === "실제" ? "최종 자막(실제)" : "대본 길이 추정(음성·자막 후 다시 계산 권장)"} · Flow 생성 ${fl.clips.filter((c) => !["still", "reuse"].includes(c.gen_mode)).length}개`) : null)));
  if (!fl.clips?.length) return;

  // 고정 블록 상태(화풍·요소) 요약 — 고치는 곳은 5단계
  const style = v.style;
  const legacy = !style?.style_en;
  fl.style = fl.style || { look_en: "", look_ko: "", avoid_en: "", recurring_ko: [], palette_ko: "" };
  page.append(section("고정 블록 (모든 클립에 원문 그대로 들어감)",
    legacy
      ? h("div", {}, h("div", { class: "note warn" }, "이 프로젝트에는 아직 확정한 화풍이 없습니다(예전 방식의 공통 스타일을 씁니다). 5단계에서 화풍 프리셋을 고르고 확정하는 것을 권장합니다."),
        h("div", { class: "grid2" },
          field("공통 스타일(영어, 예전 방식)", bindInput(fl.style, "look_en", edited, { multiline: true, rows: 3 })),
          field("스타일 의미(한국어)", bindInput(fl.style, "look_ko", () => changed(), { multiline: true, rows: 3 }))),
        field("피할 요소(영어)", bindInput(fl.style, "avoid_en", edited)))
      : h("div", { class: "row" }, h("b", {}, `화풍: ${style.label_ko}`), h("span", { class: `badge ${style.locked ? "ok" : "todo"}` }, style.locked ? `확정 v${style.version}` : "확정 전"),
        style.accent?.name ? h("span", { class: "tag" }, `강조색 ${style.accent.name}`) : null),
    h("div", { class: "row" }, h("span", { class: "small" }, `등장 요소 ${v.elements.length}개 (확정 ${v.elements.filter((e) => e.locked).length}개)`),
      h("button", { class: "small", onclick: () => go("visual") }, "5단계에서 화풍·요소 고치기")),
    field("AI에게 추가 지시(선택)", bindInput(instr, "text", null, { placeholder: "예: 결과 화면은 모두 선택 화면 재사용 / 카메라 고정" })),
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: () => makePrompts(null, instr.text) }, fl.clips.some((c) => c.var_en || c.prompt_en) ? "모든 클립 가변 설명 다시 만들기(AI)" : "모든 클립 가변 설명 만들기(AI)"),
      !aiReady() ? h("span", { class: "muted small" }, "AI 연결 전: 가변 설명을 직접 써도 최종 프롬프트가 조립됩니다.") : null,
      h("button", { onclick: () => copyText(allPromptsText(), "전체 프롬프트") }, "전체 프롬프트 복사(번호 포함, 참고용)"))));

  // 장면별 클립
  scenes.forEach((sc, si) => {
    const cl = fl.clips.filter((c) => c.scene_id === sc.id);
    if (!cl.length) return;
    page.append(h("section", { class: "card scene" },
      h("div", { class: "scene-head" }, h("h3", {}, `장면 ${si + 1}`), h("span", { class: "muted" }, sc.purpose || ""),
        sc.no_source_needed ? h("span", { class: "badge ok" }, "소스 불필요 표시됨") : null),
      sc.visual ? h("div", { class: "recommend" }, h("small", {}, "대본 단계의 권장 화면(참고)"), h("div", {}, sc.visual)) : null,
      cl.map((c) => clipCard(c, sc, items))));
  });

  if (fl.previous_clips?.length) {
    page.append(h("details", { class: "card" }, h("summary", {}, `이전 클립 ${fl.previous_clips.length}개(다시 계산 전 프롬프트 보관)`),
      fl.previous_clips.slice().reverse().map((c) => h("div", { class: "hist" },
        h("div", { class: "small" }, `장면 ${c.scene_no} · 클립 ${c.index_in_scene} · ${c.duration}초`),
        h("div", { class: "small mono" }, c.prompt_en || "(프롬프트 없음)"),
        c.prompt_en ? h("button", { class: "small", onclick: () => copyText(c.prompt_en, "이전 프롬프트") }, "복사") : null))));
  }
  await refreshNow();
}

function allPromptsText() {
  const fl = flowState();
  return fl.clips.map((c) => `[장면 ${c.scene_no} · 클립 ${c.index_in_scene} · ${c.duration}초 · ${GEN_MODES[c.gen_mode] || GEN_MODES.text}]\n${c.prompt_en || "(프롬프트 없음)"}`).join("\n\n");
}

function prevClip(c) {
  const cl = flowState().clips;
  const i = cl.findIndex((x) => x.id === c.id);
  return i > 0 ? cl[i - 1] : null;
}

function clipCard(c, sc, items) {
  const p = state.project;
  const v = vis(p);
  const fl = flowState();
  const lines = sc.lines.filter((l) => c.line_ids.includes(l.id));
  const item = items.find((i) => i.id === c.item_id && i.status === "acquired");
  const tooShort = c.duration + 0.05 < c.need_s;
  const durChanged = (c.prompt_en || c.var_en) && c.prompt_for_duration && c.prompt_for_duration !== c.duration;
  const mode = c.gen_mode || "text";
  const isFlow = !["still", "reuse"].includes(mode);
  const prev = prevClip(c);

  // 1) 등장 요소 연결 — 실제로 나오는 것만
  const elBox = h("div", { class: "chips" }, v.elements.length ? v.elements.map((e) => {
    const on = (c.element_ids || []).includes(e.id);
    return h("label", { class: `chip-check ${on ? "on" : ""}` }, h("input", { type: "checkbox", checked: on, onchange: (ev) => {
      const s = new Set(c.element_ids || []);
      if (ev.target.checked) s.add(e.id); else s.delete(e.id);
      c.element_ids = [...s]; c.elements_manual = true; changed(true); rerender();
    } }), ` ${e.name || "(이름 없음)"}`, e.locked ? "" : " (초안)");
  }) : h("span", { class: "muted small" }, "등록한 등장 요소가 없습니다(5단계)."));
  const aiEls = c.elements_manual && c.ai_element_ids && JSON.stringify([...c.ai_element_ids].sort()) !== JSON.stringify([...(c.element_ids || [])].sort())
    ? h("div", { class: "muted small" }, "AI 제안: ", c.ai_element_ids.map((id) => elById(p, id)?.name || id).join(", ") || "(없음)", " ",
      h("button", { class: "tiny", onclick: () => { c.element_ids = [...c.ai_element_ids]; changed(true); rerender(); } }, "제안대로")) : null;

  // 2) 생성 방식과 모드별 자료
  const modeSel = select(Object.entries(GEN_MODES), mode, (val) => { c.gen_mode = val; c.gen_mode_manual = true; c.mode = { first_last: "first_frame", still: "text", reuse: "text" }[val] || val; changed(true); rerender(); });
  const roleSel = select([["", "(역할 없음)"], ...Object.entries(ROLES)], c.role || "", (val) => { c.role = val; c.role_manual = true; changed(true); rerender(); });
  const startAssets = v.assets.filter((a) => a.kind === "start" || a.kind === "reference" || a.kind === "pose");
  const modeBox = h("div", { class: "mode-box" });
  if (mode === "ingredients") {
    const explicit = new Set(c.ref_asset_ids || []);
    modeBox.append(h("div", { class: "small" }, "재료로 선택할 기준 이미지: 연결한 요소의 확정 기준 이미지가 자동으로 들어갑니다. 더 넣을 이미지:"),
      h("div", { class: "thumbs" }, v.assets.filter((a) => a.kind !== "end").map((a) => h("label", { class: `cand-img small ${explicit.has(a.id) ? "picked" : ""}` },
        thumb(p, a.file, (elById(p, a.element_id)?.name || "") + " " + (a.orig_name || "")),
        h("span", {}, h("input", { type: "checkbox", checked: explicit.has(a.id), onchange: (ev) => {
          if (ev.target.checked) explicit.add(a.id); else explicit.delete(a.id);
          c.ref_asset_ids = [...explicit]; changed(true); refreshSoon(0);
        } }), " 추가")))));
  }
  if (mode === "first_frame" || mode === "first_last") modeBox.append(startFrameBox(p, c, prev, startAssets, "start_frame", "시작 프레임(이 클립이 실제로 시작하는 화면)"));
  if (mode === "first_last") modeBox.append(startFrameBox(p, c, null, v.assets, "end_frame", "마지막 프레임 이미지"));
  if (mode === "still") {
    modeBox.append(h("div", { class: "small" }, "정지 화면으로 쓸 확정 이미지:"),
      select([["", "(선택)"], ...startAssets.map((a) => [a.id, `${elById(p, a.element_id)?.name || a.kind} · ${a.orig_name}`])], c.still_asset_id || "", (val) => { c.still_asset_id = val || null; changed(true); rerender(); }),
      c.still_asset_id ? thumb(p, assetById(p, c.still_asset_id)?.file, "정지 화면") : null,
      candidatesBox(p, c));
  }
  if (mode === "reuse") {
    const others = fl.clips.filter((x) => x.id !== c.id && x.gen_mode !== "reuse");
    modeBox.append(h("div", { class: "small" }, "재사용할 소재(다른 클립): "),
      select([["", "(선택)"], ...others.map((x) => [x.id, `${clipLabel(x)} · ${ROLES[x.role] || ""} · ${GEN_MODES[x.gen_mode || "text"]}`])], c.reuse_of || "", (val) => { c.reuse_of = val || null; changed(true); rerender(); }),
      h("div", { class: "muted small" }, "같은 소재를 여러 클립에 연결해도 사용 구간·크롭·확대·편집 의도는 클립마다 따로 적습니다(아래 편집 지시)."));
  }

  // 3) 가변 설명(행동·구도·카메라·시간 배분) — AI·사용자가 쓰는 부분
  const varBox = isFlow ? h("div", { class: "var-box" },
    field("가변 설명(영어: 행동·구도·카메라 움직임·시간 배분) — 외형은 쓰지 마세요", bindInput(c, "var_en", () => { if (c.prompt_source !== "manual") c.prompt_source = "composed"; edited(); }, { multiline: true, rows: 3, class: "full mono", placeholder: "0-3s: slow push-in toward the window; curtains sway slightly. 3-6s: hold." })),
    h("div", { class: "grid2" },
      field("의미(한국어)", bindInput(c, "var_ko", (k) => { c.prompt_ko = c.var_ko; changed(); }, { multiline: true, rows: 2 })),
      field("초 단위 진행(한국어)", bindInput(c, "beats_ko", () => changed(), { multiline: true, rows: 2 }))),
    changesBox(c)) : null;

  // 4) 서버 점검 결과(최종 프롬프트·충돌·Flow 설정·검수) — 편집할 때마다 갱신
  const live = h("div", { class: "live" });
  dyn.set(c.id, () => renderLive(live, p, c, item));
  const instr = { text: "" };

  // 5) 편집 지시(프로그램이 적용하지 않음 — CapCut용)
  c.edit = c.edit || {};
  const editBox = h("details", { class: "edit-box", open: !!(c.edit.crop || c.edit.zoom || c.edit.intent) },
    h("summary", {}, "CapCut 편집 지시(크롭·확대·정지·편집 의도) — 프로그램은 적용하지 않고 내보내기에 지시로만 씀"),
    h("div", { class: "grid3" },
      field("크롭", bindInput(c.edit, "crop", () => changed(), { placeholder: "예: 시계가 중앙에 오도록" })),
      field("확대", bindInput(c.edit, "zoom", () => changed(), { placeholder: "예: 100%→160% 천천히" })),
      field("편집 의도", bindInput(c.edit, "intent", () => changed(), { placeholder: "예: 'B 時計' 자막" }))),
    h("label", { class: "chk" }, h("input", { type: "checkbox", checked: !!c.edit.hold_still, onchange: (ev) => { c.edit.hold_still = ev.target.checked; changed(); } }), " 선택하는 동안 정지 화면 유지"));

  return h("div", { class: `clip ${item ? "has-file" : ""}`, id: `clip-${c.id}` },
    h("div", { class: "clip-head" },
      h("b", {}, `${clipLabel(c)}`),
      h("span", { class: "tag" }, `배치 ${fmtSec(c.start)} → ${fmtSec(c.end)} · 필요 ${c.need_s}초 (${c.timing})`),
      h("span", {}, "역할 "), roleSel,
      isFlow ? [h("span", {}, "Flow 길이 "),
        select(CFG.durations.map((d) => [String(d), `${d}초`]), String(c.duration), (val) => { c.duration = Number(val); c.duration_manual = true; changed(true); rerender(); })] : null),
    c.inherited_from && !c.var_en ? h("div", { class: "note warn small" }, "클립 구간이 바뀌어 등장 요소·생성 방식 연결만 이전 클립에서 이어받았습니다. 가변 설명을 다시 만들거나 쓰세요.") : null,
    tooShort && isFlow ? h("div", { class: "note warn" }, `선택한 ${c.duration}초가 필요한 ${c.need_s}초보다 짧습니다. 더 긴 길이를 고르거나 [클립 계획 다시 계산]을 누르세요.`) : null,
    durChanged && isFlow ? h("div", { class: "note warn" }, `가변 설명은 ${c.prompt_for_duration}초 기준으로 쓰였습니다. 길이를 바꿨으니 시간 배분을 확인하세요.`) : null,
    h("div", { class: "clip-lines" }, lines.map((l) => h("div", {}, h("span", { class: "jp" }, l.display.replace(/\n/g, "")), " ", h("span", { class: "ko" }, l.ko)))),
    h("div", { class: "clip-grid" },
      h("div", {}, h("div", { class: "field-label" }, "① 등장 요소(이 클립에 실제로 나오는 것만)"), elBox, aiEls),
      h("div", {}, h("div", { class: "field-label" }, "② 생성 방식"), h("div", { class: "row tight" }, modeSel,
        c.ai_gen_mode && c.ai_gen_mode !== mode ? h("span", { class: "muted small" }, `AI 제안: ${GEN_MODES[c.ai_gen_mode]}`) : null),
      prev && isFlow ? h("label", { class: "chk small" }, h("input", { type: "checkbox", checked: !!c.continues_previous, onchange: (ev) => { c.continues_previous = ev.target.checked; c.continues_manual = true; changed(true); refreshSoon(0); } }),
        ` 직전 클립(${clipLabel(prev)}) 동작을 이어 감`) : null)),
    modeBox,
    varBox,
    live,
    isFlow ? h("details", {}, h("summary", {}, "이 클립만 AI로 가변 설명 다시 만들기"),
      h("div", { class: "row" }, bindInput(instr, "text", null, { placeholder: "예: 더 밝게 / 손만 나오게 / 카메라 고정", class: "grow" }),
        h("button", { class: "small", onclick: () => makePrompts([c.id], instr.text) }, "다시 만들기")),
      c.prompt_prev ? h("div", { class: "small" }, "직전 프롬프트: ", h("span", { class: "mono" }, c.prompt_prev.var_en || c.prompt_prev.en), " ",
        h("button", { class: "small", onclick: () => undoPrompt(c) }, "되돌리기")) : null) : null,
    editBox,
    isFlow ? videoBox(p, c, item) : null);
}

function undoPrompt(c) {
  const cur = { en: c.prompt_en, ko: c.prompt_ko, var_en: c.var_en || "", var_ko: c.var_ko || "", beats_ko: c.beats_ko || "", source: c.prompt_source || "legacy" };
  const pv = c.prompt_prev;
  c.prompt_en = pv.en; c.prompt_ko = pv.ko; c.var_en = pv.var_en || ""; c.var_ko = pv.var_ko || ""; c.beats_ko = pv.beats_ko ?? c.beats_ko;
  c.prompt_source = pv.source || (pv.var_en ? "composed" : "legacy");
  c.prompt_prev = cur;
  changed(true); rerender();
}

function changesBox(c) {
  c.changes = c.changes || [];
  const sug = c.ai_change_suggestion;
  return h("details", { open: c.changes.length > 0 },
    h("summary", {}, `의도적 변경 ${c.changes.length ? `(${c.changes.length})` : ""} — 이 클립에서만 장소·의상·조명을 일부러 바꿀 때`),
    c.changes.map((ch, i) => h("div", { class: "row tight" },
      select([["outfit", "의상"], ["place", "장소"], ["lighting", "조명"], ["other", "기타"]], ch.kind || "other", (val) => { ch.kind = val; changed(); }),
      bindInput(ch, "en", edited, { class: "grow", placeholder: "영어: wears a navy raincoat (only in this shot)" }),
      bindInput(ch, "ko", () => changed(), { class: "grow", placeholder: "한국어 메모" }),
      h("button", { class: "tiny danger", onclick: () => { c.changes.splice(i, 1); changed(true); rerender(); } }, "삭제"))),
    h("div", { class: "row" }, h("button", { class: "small", onclick: () => { c.changes.push({ kind: "other", en: "", ko: "" }); changed(true); rerender(); } }, "+ 변경 기록"),
      sug ? h("span", { class: "small" }, `AI 제안: ${sug.ko || sug.en} `,
        h("button", { class: "tiny", onclick: () => { c.changes.push({ kind: "other", en: sug.en, ko: sug.ko }); delete c.ai_change_suggestion; changed(true); rerender(); } }, "기록에 추가"),
        h("button", { class: "tiny", onclick: () => { delete c.ai_change_suggestion; changed(true); rerender(); } }, "무시")) : null));
}

function candidatesBox(p, c) {
  const v = vis(p);
  const fileIn = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", multiple: true });
  const mine = v.assets.filter((a) => a.kind === "start" && a.clip_id === c.id);
  return h("div", { class: "cands" },
    h("div", { class: "small" }, "시작 이미지 후보(선택): Flow 등에서 만든 이미지를 여러 장 올려 나란히 비교하고 확정하세요."),
    mine.length ? h("div", { class: "thumbs" }, mine.map((a) => {
      const picked = c.start_frame?.asset_id === a.id || c.still_asset_id === a.id;
      return h("div", { class: `cand-img ${picked ? "picked" : ""}` }, thumb(p, a.file, a.orig_name),
        h("button", { class: "tiny", onclick: () => {
          if (c.gen_mode === "still") c.still_asset_id = a.id; else c.start_frame = { source: "asset", asset_id: a.id };
          changed(true); rerender();
        } }, picked ? "확정됨" : "이걸로 확정"));
    })) : null,
    h("div", { class: "row" }, fileIn, h("button", { class: "small", onclick: async () => {
      const added = await uploadImages(fileIn.files, { kind: "start", clip_id: c.id });
      if (added.length) rerender();
    } }, "후보 올리기")));
}

function startFrameBox(p, c, prev, assets, key, title) {
  const cur = c[key] || {};
  const box = h("div", { class: "sf-box" }, h("div", { class: "small" }, h("b", {}, title)));
  const opts = [["", "(없음)"], ...assets.map((a) => [`asset:${a.id}`, `이미지: ${elById(p, a.element_id)?.name || a.kind} · ${a.orig_name}`])];
  const frames = vis(p).frames.filter((f) => f.kind === "last_used");
  const seen = new Set();
  for (const f of frames.slice().reverse()) {
    if (seen.has(f.clip_id)) continue;
    seen.add(f.clip_id);
    opts.push([`frame:${f.id}`, `${f.clip_label} 실제 사용 끝 프레임 (${f.t}초)`]);
  }
  const curVal = cur.source === "asset" ? `asset:${cur.asset_id}` : cur.source === "frame" ? `frame:${cur.frame_id}` : "";
  if (curVal && !opts.some((o) => o[0] === curVal)) opts.push([curVal, "(지정한 자료가 없음)"]);
  box.append(select(opts, curVal, (val) => {
    if (!val) c[key] = { source: "none" };
    else { const [src, id] = val.split(":"); c[key] = src === "asset" ? { source: "asset", asset_id: id } : { source: "frame", frame_id: id }; }
    changed(true); rerender();
  }));
  if (key === "start_frame" && prev) {
    box.append(h("button", { class: "small", title: "이전 클립에서 편집에 실제로 쓰는 구간의 마지막 프레임(파일 끝 아님)", onclick: async () => {
      const fr = await extractFrames(prev.id, ["last"]);
      if (!fr.length) return;
      c.start_frame = { source: "frame", frame_id: fr[0].id }; c.continues_previous = true; c.continues_manual = true;
      changed(true); toast(`${clipLabel(prev)}의 실제 사용 끝 프레임(${fr[0].t}초)을 시작 프레임으로 연결했습니다.`, "ok"); rerender();
    } }, `${clipLabel(prev)} 실제 사용 끝 프레임으로 이어 가기`));
  }
  box.append(h("div", { class: "muted small" }, "모든 클립을 이전 마지막 프레임으로 잇지 마세요. 동작이 이어지지 않으면 원래 기준 이미지나 확정 이미지에서 새로 시작하세요."));
  if (key === "start_frame") box.append(candidatesBox(p, c));
  return box;
}

function renderLive(box, p, c, item) {
  const r = INSP[c.id];
  if (!r) { box.replaceChildren(h("div", { class: "muted small" }, "점검 중…")); return; }
  const isFlow = r.needs_flow;
  const parts = [];
  if (r.review) {
    const rv = r.review;
    parts.push(h("div", { class: "row tight" },
      h("span", { class: `badge ${rv.registered ? "ok" : "todo"}` }, rv.registered ? "영상 등록됨" : "영상 미등록"),
      h("span", { class: `badge ${rv.state === "pass" ? "ok" : rv.state === "recheck" || rv.state === "fix" ? "stale" : "todo"}` }, `검수: ${rv.label_ko}`),
      rv.registered ? h("button", { class: "tiny", onclick: () => go("review") }, "검수 화면") : null));
    if (rv.reasons?.length) parts.push(h("div", { class: "note warn small" }, rv.reasons.map((x) => h("div", {}, "• " + x))));
  }
  if (r.guidance?.length) parts.push(h("div", { class: "guide small" }, h("b", {}, "권장 안내 "), r.guidance.map((x) => h("div", {}, "· " + x))));
  if (r.conflicts?.length) parts.push(h("div", { class: "note err small" }, h("b", {}, "고정 설정과 충돌"), r.conflicts.map((x) => h("div", {}, `• ${x.message_ko} — ${x.hint_ko}`))));
  if (r.warnings?.length) parts.push(h("div", { class: "note warn small" }, r.warnings.map((x) => h("div", {}, "• " + x))));
  parts.push(h("div", { class: "flow-settings" }, h("div", { class: "field-label" }, "Flow에서 직접 고를 설정"), r.flow_settings.map((x) => h("div", { class: "small" }, "· " + x)),
    r.refs?.length ? h("div", { class: "thumbs" }, r.refs.map((x) => thumb(p, x.file, `참조 ${x.no}: ${x.element_name}`))) : null,
    r.start_frame?.file ? thumb(p, r.start_frame.file, "시작: " + r.start_frame.label) : null,
    h("div", { class: "muted small" }, "Flow 프로젝트에 이미지를 올리는 것과, 생성 요청에서 그 이미지를 재료·첫 프레임으로 '선택'하는 것은 다릅니다. 매 생성 요청마다 직접 선택하세요.")));
  if (isFlow) {
    const manual = c.prompt_source === "manual";
    const legacy = r.prompt_source === "legacy";
    const finalText = manual || legacy ? c.prompt_en : r.composed_en;
    const ta = h("textarea", { class: "full mono", rows: 7, readonly: !manual }, finalText || "");
    ta.addEventListener("input", () => { c.prompt_en = ta.value; changed(); });
    parts.push(h("div", { class: "final" },
      h("div", { class: "row between" }, h("div", { class: "field-label" }, manual ? "최종 프롬프트(직접 수정 중 — 고정 블록 자동 반영 안 됨)" : legacy ? "최종 프롬프트(예전 방식 — 고정/가변 분리 전)" : "최종 프롬프트(자동 조립 — Flow에 그대로 붙여 넣기)"),
        h("div", { class: "row tight" },
          h("button", { class: "small primary", disabled: !finalText, onclick: () => copyText(finalText, "최종 프롬프트") }, "프롬프트 복사"),
          manual || legacy
            ? h("button", { class: "small", disabled: !c.var_en, title: c.var_en ? "" : "가변 설명이 있어야 자동 조립할 수 있습니다", onclick: () => {
              c.prompt_prev = { en: c.prompt_en, ko: c.prompt_ko, var_en: c.var_en || "", var_ko: c.var_ko || "", beats_ko: c.beats_ko || "", source: c.prompt_source || "legacy" };
              c.prompt_source = "composed"; c.prompt_en = r.composed_en; changed(true); rerender();
            } }, "자동 조립으로 되돌리기")
            : h("button", { class: "small", onclick: () => { c.prompt_prev = { en: c.prompt_en, ko: c.prompt_ko, var_en: c.var_en || "", var_ko: c.var_ko || "", beats_ko: c.beats_ko || "", source: "composed" }; c.prompt_source = "manual"; changed(true); rerender(); } }, "직접 수정"))),
      ta,
      !manual && !legacy && !r.composed_en ? h("div", { class: "muted small" }, "가변 설명을 쓰거나 AI로 만들면 최종 프롬프트가 조립됩니다.") : null,
      manual && r.composed_en && r.composed_en !== c.prompt_en ? h("div", { class: "muted small" }, "직접 수정한 프롬프트는 화풍·요소를 바꿔도 자동으로 바뀌지 않습니다.") : null,
      c.var_ko || c.prompt_ko ? h("div", { class: "ko small" }, "의미: ", c.var_ko || c.prompt_ko) : null,
      c.beats_ko ? h("div", { class: "small" }, "진행: ", c.beats_ko) : null,
      c.mode_note_ko ? h("div", { class: "muted small" }, "방식 이유(AI): ", c.mode_note_ko) : null,
      c.risk_ko ? h("div", { class: "note warn small" }, c.risk_ko) : null));
  }
  box.replaceChildren(...parts);
}

function videoBox(p, c, item) {
  const fileIn = h("input", { type: "file", accept: "video/mp4,video/*" });
  const frameBox = h("div", {});
  const useStart = { v: c.use_start ?? 0 };
  const endFr = latestFrame(p, c.id, "last_used", INSP[c.id]?.frame_basis);
  if (endFr) frameBox.append(h("div", { class: "row" }, thumb(p, endFr.file, `실제 사용 끝 프레임 ${endFr.t}초 (사용 ${endFr.use_start}~${Math.round((endFr.use_start + endFr.use_len) * 100) / 100}초)`)));
  return h("div", { class: "clip-file" },
    h("div", { class: "field-label" }, "⑤ Flow에서 만든 영상"),
    item ? h("div", { class: "clip-video" },
      h("video", { src: mediaUrl(p.id, item.file), controls: true, muted: true, preload: "metadata" }),
      h("div", {},
        h("div", { class: "small" }, item.orig_name, item.duration ? ` · ${item.duration}초` : ""),
        h("div", { class: "row tight" }, h("span", { class: "small" }, "사용 시작(초) "),
          bindInput(useStart, "v", () => { c.use_start = Math.max(0, Number(useStart.v) || 0); edited(); }, { type: "number", class: "num" }),
          h("span", { class: "muted small" }, `→ 사용 구간 ${c.use_start || 0}~${Math.round(((c.use_start || 0) + c.need_s) * 100) / 100}초`)),
        item.duration && item.duration + 0.05 < (c.use_start || 0) + c.need_s ? h("div", { class: "note warn small" }, `영상(${item.duration}초)이 사용 구간 끝(${Math.round(((c.use_start || 0) + c.need_s) * 100) / 100}초)보다 짧습니다.`) : null,
        h("div", { class: "muted small" }, item.license_note),
        h("label", { class: "chk" }, h("input", { type: "checkbox", checked: !!item.rights_checked, onchange: (e) => { item.rights_checked = e.target.checked; changed(); } }), " 이용약관·AI 표시 필요 여부 확인함"),
        h("div", { class: "row" },
          h("button", { class: "small", title: "편집에서 실제로 쓰는 구간(사용 시작 ~ 사용 시작+필요 길이)의 마지막 프레임. 파일 끝 프레임이 아님", onclick: async () => {
            const fr = await extractFrames(c.id, ["last"]);
            if (!fr.length) return;
            const nxt = flowState().clips[flowState().clips.findIndex((x) => x.id === c.id) + 1];
            frameBox.replaceChildren(h("div", { class: "row" },
              h("a", { href: mediaUrl(p.id, fr[0].file, Date.now()), download: `${clipLabel(c)}_used_end_${fr[0].t}s.png` }, h("img", { src: mediaUrl(p.id, fr[0].file, Date.now()), class: "lastframe" })),
              h("div", { class: "small" }, `원본 ${fr[0].t}초 프레임(사용 구간 ${fr[0].use_start}~${Math.round((fr[0].use_start + fr[0].use_len) * 100) / 100}초의 끝${fr[0].clipped ? ", 파일이 짧아 파일 끝에서 자름" : ""})`,
                nxt ? h("div", {}, h("button", { class: "small", onclick: () => { nxt.start_frame = { source: "frame", frame_id: fr[0].id }; nxt.continues_previous = true; nxt.continues_manual = true; if (!["first_frame", "first_last"].includes(nxt.gen_mode)) { nxt.gen_mode = "first_frame"; nxt.mode = "first_frame"; } changed(true); toast(`${clipLabel(nxt)}의 시작 프레임으로 연결했습니다.`, "ok"); rerender(); } }, `다음 클립(${clipLabel(nxt)}) 시작 프레임으로 연결`)) : null)));
          } }, "실제 사용 끝 프레임 추출"),
          h("button", { class: "small danger", onclick: async () => {
            if (!(await confirmBox("영상 빼기", "이 클립에서 영상 연결을 뺍니다(파일은 프로젝트 폴더에 남습니다).", "빼기", true))) return;
            p.sources.items = p.sources.items.filter((x) => x.id !== item.id); c.item_id = null; changed(true); rerender();
          } }, "빼기")),
        frameBox)) : null,
    h("div", { class: "row" }, h("span", { class: "small" }, item ? "다른 영상으로 바꾸기" : "Flow에서 만든 영상 넣기"), fileIn,
      h("button", { class: "small", onclick: async () => {
        if (!fileIn.files[0]) { toast("파일을 선택하세요.", "info"); return; }
        await flush();
        const fd = new FormData();
        fd.append("files", fileIn.files[0]); fd.append("scene_id", c.scene_id); fd.append("clip_id", c.id);
        try {
          const r = await api.upload(`/api/projects/${p.id}/sources/upload`, fd);
          for (const e of r.errors) toast(`${e.name}: ${e.error}`, "error", e.hint || "");
          if (!r.items.length) return;
          p.sources = p.sources || { items: [] };
          // 예전 파일은 지우지 않는다. 다른 클립이 쓰지 않으면 '교체됨'으로 표시(내보내기 제외, 기록·파일은 보존)
          if (item && !flowState().clips.some((x) => x.id !== c.id && x.item_id === item.id)) { item.status = "replaced"; item.replaced_at = new Date().toISOString(); }
          p.sources.items.push(r.items[0]);
          c.item_id = r.items[0].id;
          changed(true);
          const d = r.items[0].duration;
          toast("영상을 넣었습니다.", "ok", d && d + 0.05 < c.need_s ? `영상 ${d}초가 필요한 ${c.need_s}초보다 짧습니다.` : (item ? "이 클립과, 이 클립의 끝 프레임으로 시작하는 클립은 재검토가 필요합니다." : ""));
          rerender();
        } catch (e) { showError(e); }
      } }, "넣기")));
}
