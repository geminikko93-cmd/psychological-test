import { state, changed } from "../state.js";
import { h, section, field, bindInput, select, confirmBox, toast, empty, fmtTime, promptBox } from "../ui.js";
import { runAi, aiReady, usageText } from "../aiflow.js";
import { go, rerender } from "../nav.js";
import { presets, vis } from "../vis.js";

const KIND = [["undecided", "미정 (AI가 판단)"], ["entertainment", "창작 오락 콘텐츠"], ["factual", "사실 기반 콘텐츠(근거 필요)"]];

function nowIso() { return new Date().toISOString(); }

function planFromCandidate(c) {
  return {
    source: { candidate_id: c.id, selected_at: nowIso() },
    approach_name_ko: c.approach_name_ko, format_ko: c.format_ko, content_kind: c.content_kind,
    why_watch_ko: c.why_watch_ko, first_scene_ko: c.first_scene_ko, first_line_ja: c.first_line_ja, first_line_ko: c.first_line_ko,
    structure_ko: (c.structure_ko || []).join("\n"), scene_count: c.scene_count,
    choices_use: !!c.choices?.use, choices_count: c.choices?.count ?? "", choices_reason_ko: c.choices?.reason_ko || "",
    payoff_ko: c.payoff_ko, next_video_pull_ko: c.next_video_pull_ko, visuals_ko: (c.visuals_ko || []).join("\n"),
    source_difficulty_ko: c.source_difficulty_ko, claims_ko: c.claims_ko, risks_ko: c.risks_ko,
    estimated_seconds: c.estimated_seconds, updated_at: nowIso(),
    core_idea_ko: c.core_idea_ko || "", content_type: c.content_type || "", viewer_action: c.viewer_action || "",
    ending_type: c.ending_type || "", ending_ko: c.ending_ko || "", production_load_ko: c.production_load_ko || "",
  };
}

const MODES = [["mixed", "장면마다 다르게(혼합)"], ["flow", "Google Flow로 AI 영상 생성"], ["stock", "스톡 영상·사진"],
  ["upload", "직접 촬영·보유 파일"], ["image", "이미지·일러스트(정지 화면 + 움직임)"], ["text", "텍스트·단색 화면"]];
export { MODES as SOURCE_MODES };

function candidateCard(c, onPick, picked, flags, onRedo) {
  const row = (label, v) => v ? h("div", { class: "kv" }, h("span", { class: "k" }, label), h("span", { class: "v pre" }, Array.isArray(v) ? v.join("\n") : v)) : null;
  return h("div", { class: `cand ${picked ? "picked" : ""} ${flags?.length ? "similar" : ""}` },
    h("div", { class: "cand-head" }, h("b", {}, `${c.id}. ${c.approach_name_ko || "(이름 없음)"}`),
      h("span", { class: "tag" }, c.content_kind === "factual" ? "사실 기반" : "창작 오락"),
      c.estimated_seconds ? h("span", { class: "tag", title: "기획 단계 추정. 최종 길이는 실제 음성으로 결정" }, `약 ${c.estimated_seconds}초(추정)`) : null),
    c.core_idea_ko ? h("div", { class: "core" }, c.core_idea_ko) : null,
    h("div", { class: "chips" },
      c.content_type ? h("span", { class: "chip2" }, "종류: ", c.content_type) : null,
      c.viewer_action ? h("span", { class: "chip2" }, "시청자: ", c.viewer_action) : null,
      c.ending_type ? h("span", { class: "chip2" }, "결말: ", c.ending_type) : null),
    flags?.length ? h("div", { class: "note warn small" }, h("b", {}, "비슷한 후보로 보입니다 "), "(규칙 기반 자동 비교, 품질 점수 아님)",
      flags.map((f) => h("div", {}, "· " + f)), h("button", { class: "small", onclick: onRedo }, "이 후보만 다시 만들기 (AI 1회)")) : null,
    row("첫 장면", c.first_scene_ko),
    h("div", { class: "jp" }, c.first_line_ja), h("div", { class: "ko small" }, c.first_line_ko),
    row("결말", c.ending_ko || c.payoff_ko),
    row("제작 부담(AI 추정)", c.production_load_ko || c.source_difficulty_ko),
    row("다른 후보와 차이", c.differs_from_ko),
    h("details", {}, h("summary", {}, "자세히"),
      row("전개", c.format_ko), row("볼 이유", c.why_watch_ko), row("구성", c.structure_ko),
      row("선택지", c.choices?.use ? `${c.choices.count ?? "?"}개 — ${c.choices.reason_ko}` : `사용 안 함 — ${c.choices?.reason_ko || ""}`),
      row("결과의 재미", c.payoff_ko), row("다음 영상", c.next_video_pull_ko), row("시각 자료", c.visuals_ko),
      row("소스 확보(AI 추정)", c.source_difficulty_ko), row("주장 수준", c.claims_ko), row("주의", c.risks_ko), row("중복 회피", c.overlap_check_ko)),
    h("div", { class: "row end" },
      !flags?.length ? h("button", { class: "small", onclick: onRedo }, "이 후보만 다시") : null,
      h("button", { class: picked ? "" : "primary", onclick: onPick }, picked ? "선택됨 (다시 적용)" : "이 방향 선택")));
}

function planUsage(p) {
  const logs = (p.ai_log || []).filter((x) => (x.task || "").startsWith("기획"));
  const tin = logs.reduce((a, x) => a + (x.usage?.input_tokens || 0) + (x.usage?.cache_creation_input_tokens || 0) + (x.usage?.cache_read_input_tokens || 0), 0);
  const tout = logs.reduce((a, x) => a + (x.usage?.output_tokens || 0), 0);
  return logs.length ? `이 프로젝트의 기획 AI 요청 ${logs.length}회 · 입력 ${tin.toLocaleString()} · 출력 ${tout.toLocaleString()} 토큰(서버 보고값)` : "";
}

export async function renderPlan(page) {
  const p = state.project;
  const idea = p.idea;
  const save = () => changed();

  page.append(h("div", { class: "page-head" }, h("h1", {}, "1. 기획"),
    h("div", { class: "muted" }, "주제와 상황을 적으면 AI가 접근 방식이 서로 다른 기획 방향을 제안합니다. 선택한 뒤 자유롭게 고칠 수 있습니다.")));

  const nameInp = bindInput(p, "name", () => { save(); document.querySelector(".proj-name") && (document.querySelector(".proj-name").textContent = p.name); });
  page.append(section("아이디어",
    field("프로젝트 이름", nameInp),
    field("주제·아이디어", bindInput(idea, "topic", save, { multiline: true, rows: 2, placeholder: "예: 처음 보는 방에서 먼저 눈이 가는 물건으로 해 보는 놀이형 질문" })),
    field("시청자와 상황", bindInput(idea, "audience", save, { multiline: true, rows: 2, placeholder: "예: 출퇴근 중 소리 없이 보는 20~30대 일본어 사용자" })),
    h("div", { class: "grid3" },
      field("콘텐츠 구분", select(KIND, idea.content_kind || "undecided", (v) => { idea.content_kind = v; save(); })),
      field("목표 길이(초)", bindInput(idea, "target_seconds", save, { type: "number" })),
      field("피하고 싶은 것", bindInput(idea, "avoid", save, { placeholder: "예: 동물 선택지, 연애 주제" }))),
    field("추가 메모", bindInput(idea, "notes", save, { multiline: true, rows: 2, placeholder: "참고할 점, 원하는 분위기 등" })),
    field("영상 제작 방식(기획·대본·소스 프롬프트에 반영)", select(MODES, (p.production || {}).source_mode || "mixed", (v) => { p.production = { ...(p.production || {}), source_mode: v }; save(); }),
      "장면마다 다르게 하려면 '혼합'을 고르고, 영상 단계에서 장면별로 정하세요.")));

  await directionSection(page, p);

  // 후보
  const pc = p.plan_candidates || (p.plan_candidates = { items: [] });
  const fb = { text: pc.feedback || "" };
  const candBox = h("div", { class: "cands" });
  const pick = async (c) => {
    if (p.plan && p.plan.updated_at && p.plan.source?.candidate_id !== c.id) {
      const ok = await confirmBox("기획 바꾸기", "현재 편집 중인 기획을 이 후보로 바꿉니다.\n이전 기획은 '이전 기획' 목록에 보관됩니다.", "바꾸기");
      if (!ok) return;
    }
    if (p.plan) p.plan_history = [...(p.plan_history || []), p.plan].slice(-10);
    p.plan = planFromCandidate(c);
    if (c.content_kind && p.idea.content_kind === "undecided") p.idea.content_kind = c.content_kind;
    changed(true);
    toast("기획 방향을 선택했습니다. 아래에서 고칠 수 있습니다.", "ok");
    rerender();
  };
  const redo = async (c) => {
    const instr = await promptBox(`${c.id}안만 다시 만들기`, "추가로 바라는 점(선택, 비워도 됨). AI 요청 1회가 추가로 사용됩니다.", "");
    if (instr === null) return;
    const r = await runAi("plan_one", { candidate_id: c.id, instruction: instr }, { guard: () => JSON.stringify(pc.items), what: "기획 후보 목록" });
    if (!r) return;
    pc.items = pc.items.map((x) => (x.id === c.id ? r.candidate : x));
    pc.similarity = r.similarity;
    pc.redo_count = (pc.redo_count || 0) + 1;
    changed(true); rerender();
  };
  if (pc.items?.length) {
    const flagged = pc.similarity?.flagged || {};
    for (const c of pc.items) candBox.append(candidateCard(c, () => pick(c), p.plan?.source?.candidate_id === c.id && p.plan?.source?.selected_at > (pc.generated_at || ""),
      flagged[c.id], () => redo(c)));
  } else {
    candBox.append(empty(aiReady() ? "아직 제안이 없습니다. [AI 기획 방향 제안 받기]를 누르세요." : "AI 연결 전입니다. [설정]에서 연결하거나, 아래 '직접 기획 작성'으로 시작하세요."));
  }
  const fbInput = bindInput(fb, "text", () => {}, { multiline: true, rows: 2, placeholder: "예: 셋 다 질문형이라 비슷하다 / 결말이 더 구체적이면 좋겠다 / 동물 말고 사물로" });
  page.append(section("AI 기획 방향 제안",
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: async () => {
        const r = await runAi("plan", { feedback: "" });
        if (r) { p.plan_candidates = { ...r, log: undefined }; changed(true); rerender(); }
      } }, pc.items?.length ? "새로 제안 받기" : "AI 기획 방향 제안 받기"),
      pc.generated_at ? h("span", { class: "muted small" }, `제안 시각 ${fmtTime(pc.generated_at)} · 참고한 이전 프로젝트 ${pc.history_used ?? 0}개${pc.redo_count ? ` · 후보 다시 만들기 ${pc.redo_count}회` : ""}`) : null),
    planUsage(p) ? h("div", { class: "muted small" }, planUsage(p)) : null,
    pc.similarity ? h("div", { class: "muted small" }, Object.keys(pc.similarity.flagged || {}).length
      ? `자동 비교: 비슷해 보이는 후보 ${Object.keys(pc.similarity.flagged).length}개(아래 노란 표시). 다시 만들지는 직접 정하세요 — 자동으로 다시 요청하지 않습니다.`
      : "자동 비교: 콘텐츠 종류·시청자 행동·결말과 문장 유사도 기준으로 크게 겹치는 후보는 없습니다(품질 점수 아님).") : null,
    pc.comparison_ko ? h("div", { class: "note" }, h("b", {}, "후보 차이(AI 설명): "), pc.comparison_ko) : null,
    candBox,
    pc.items?.length ? h("div", { class: "feedback" }, field("마음에 들지 않는 점을 적고 다시 제안 받기", fbInput),
      h("button", { onclick: async () => {
        if (!fb.text.trim()) { toast("무엇을 바꾸고 싶은지 적어 주세요.", "info"); return; }
        const r = await runAi("plan", { feedback: fb.text });
        if (r) { p.plan_candidates = { ...r, log: undefined }; changed(true); rerender(); }
      } }, "의견 반영해 다시 제안")) : null));

  // 선택한 기획 편집
  if (!p.plan) {
    page.append(section("선택한 기획", empty("후보 중 하나를 선택하면 여기서 고칠 수 있습니다."),
      h("button", { onclick: () => { p.plan = planFromCandidate({ id: "manual", approach_name_ko: "직접 작성", content_kind: idea.content_kind === "factual" ? "factual" : "entertainment" }); p.plan.source = { candidate_id: "manual", selected_at: nowIso() }; changed(true); rerender(); } }, "직접 기획 작성")));
    return;
  }
  const plan = p.plan;
  const touch = () => { plan.updated_at = nowIso(); changed(); };
  const f = (label, key, opts = {}) => field(label, bindInput(plan, key, touch, opts));
  page.append(section("선택한 기획 (자유롭게 수정)",
    h("div", { class: "grid2" }, f("접근 방식", "approach_name_ko"), field("콘텐츠 구분", select(KIND.slice(1), plan.content_kind || "entertainment", (v) => { plan.content_kind = v; p.idea.content_kind = v; touch(); }))),
    f("핵심 아이디어", "core_idea_ko"),
    h("div", { class: "grid3" }, f("콘텐츠 종류", "content_type"), f("시청자 행동", "viewer_action"), f("결말 유형", "ending_type")),
    f("전개 방식", "format_ko", { multiline: true, rows: 2 }),
    f("시청자가 볼 이유", "why_watch_ko", { multiline: true, rows: 2 }),
    h("div", { class: "grid2" }, f("첫 장면", "first_scene_ko", { multiline: true, rows: 2 }), h("div", {}, f("첫 문장(일본어)", "first_line_ja"), f("첫 문장 의미", "first_line_ko"))),
    f("장면 구성(한 줄에 한 장면)", "structure_ko", { multiline: true, rows: 5 }),
    h("div", { class: "grid3" },
      field("선택지 사용", select([["true", "사용"], ["false", "사용 안 함"]], String(!!plan.choices_use), (v) => { plan.choices_use = v === "true"; touch(); })),
      f("선택지 개수", "choices_count"), f("판단 이유", "choices_reason_ko")),
    f("결과·결말의 재미", "payoff_ko", { multiline: true, rows: 2 }),
    f("다음 영상도 보고 싶게 만드는 요소", "next_video_pull_ko", { multiline: true, rows: 2 }),
    f("필요한 시각 자료", "visuals_ko", { multiline: true, rows: 3 }),
    h("div", { class: "grid2" }, f("주장 수준·근거", "claims_ko", { multiline: true, rows: 2 }), f("주의점", "risks_ko", { multiline: true, rows: 2 })),
    h("div", { class: "row end" }, h("button", { class: "primary", onclick: () => go("script") }, "대본 단계로 →"))));

  if (p.plan_history?.length) {
    page.append(section("이전 기획", h("div", { class: "muted small" }, "다른 후보로 바꾸기 전 기획입니다. 되돌릴 수 있습니다."),
      p.plan_history.slice().reverse().map((old, i) => h("div", { class: "row between hist" },
        h("span", {}, `${old.approach_name_ko || "(이름 없음)"} · ${fmtTime(old.updated_at)}`),
        h("button", { class: "small", onclick: async () => {
          if (!(await confirmBox("이전 기획으로 되돌리기", "현재 기획을 이전 기획 목록에 넣고 선택한 기획으로 되돌립니다.", "되돌리기"))) return;
          const idx = p.plan_history.length - 1 - i;
          const restored = p.plan_history[idx];
          p.plan_history.splice(idx, 1);
          p.plan_history.push(p.plan);
          p.plan = { ...restored, updated_at: nowIso() };
          changed(true); rerender();
        } }, "되돌리기")))));
  }
  if (p.ai_log?.length) {
    const last = p.ai_log.filter((x) => x.task === "기획 제안").slice(-1)[0];
    if (last) page.append(h("div", { class: "muted small" }, "마지막 기획 요청 사용량: ", usageText(last)));
  }
}


// 채널 방향·화풍·구성 템플릿(이 프로젝트). 추천값은 사용자가 고를 때만 적용한다.
async function directionSection(page, p) {
  const P = await presets();
  const v = vis(p);
  const st = v.style;
  const styleSel = select([["", "(아직 고르지 않음)"], ...Object.values(P.styles).map((s) => [s.id, s.label_ko + (s.recommended ? " — 기본 추천" : "")])],
    st?.preset_id || "", (val) => {
      if (!val) return;
      const pr = P.styles[val];
      v.style = { preset_id: pr.id, locked: false, version: 0, label_ko: pr.label_ko, desc_ko: pr.desc_ko, style_en: pr.style_en,
        palette: pr.palette.map((c) => ({ ...c })), lighting_en: pr.lighting_en, material_en: pr.material_en, line_en: pr.line_en,
        motion_en: pr.motion_en, motion_ko: pr.motion_ko, avoid_en: pr.avoid_en, accent: { name: "", hex: "", note_ko: "" } };
      changed(true); rerender();
    });
  styleSel.disabled = !!st?.locked;
  const tpl = v.structure;
  const tplBox = h("div", {});
  if (tpl?.use) {
    tplBox.append(h("div", { class: "muted small" }, tpl.note_ko || ""),
      h("table", { class: "tbl" }, h("tr", {}, h("th", {}, "시작(초)"), h("th", {}, "끝(초)"), h("th", {}, "역할"), h("th", {}, "내용")),
        tpl.segments.map((sg) => h("tr", {},
          h("td", {}, bindInput(sg, "start", () => changed(), { type: "number", class: "num" })),
          h("td", {}, bindInput(sg, "end", () => changed(), { type: "number", class: "num" })),
          h("td", {}, sg.role || ""),
          h("td", {}, bindInput(sg, "label_ko", () => changed()))))),
      h("div", { class: "row" }, "선택지 수 ", bindInput(tpl, "choices", () => changed(), { type: "number", class: "num" }),
        h("span", { class: "muted small" }, "AI는 이 구성을 '추천'으로만 받고, 주제에 맞지 않으면 다른 형식을 고를 수 있습니다.")),
      h("details", {}, h("summary", {}, "선택 화면 원칙"), (tpl.choice_rules_ko || []).map((x) => h("div", { class: "small" }, "· " + x))));
  }
  page.append(section("채널 방향 · 화풍 · 구성 (이 프로젝트)",
    h("div", { class: "muted small" }, P.note_ko),
    h("div", { class: "grid2" },
      field("화풍 프리셋", styleSel, st?.locked ? "확정된 화풍은 5단계에서 [확정 풀기] 후 바꿀 수 있습니다." : "고른 뒤 5단계에서 확인·확정(잠금)합니다."),
      field("쇼츠 구성 템플릿", select([["", "사용 안 함(AI가 주제에 맞게 판단)"], ...Object.values(P.structures).map((t) => [t.id, t.label_ko])], tpl?.use ? tpl.id : "", (val) => {
        if (!val) { if (v.structure) v.structure.use = false; }
        else if (v.structure?.id === val) v.structure.use = true;
        else v.structure = { ...JSON.parse(JSON.stringify(P.structures[val])), use: true };
        changed(true); rerender();
      }), "시간과 선택지 수는 고칠 수 있습니다. 실제 길이는 일본어 음성·자막 시간으로 다시 맞춥니다.")),
    tplBox));
}
