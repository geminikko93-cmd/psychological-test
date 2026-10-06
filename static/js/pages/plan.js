import { state, changed } from "../state.js";
import { h, section, field, bindInput, select, confirmBox, toast, empty, fmtTime } from "../ui.js";
import { runAi, aiReady, usageText } from "../aiflow.js";
import { go, rerender } from "../nav.js";

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
  };
}

function candidateCard(c, onPick, picked) {
  const row = (label, v) => v ? h("div", { class: "kv" }, h("span", { class: "k" }, label), h("span", { class: "v pre" }, Array.isArray(v) ? v.join("\n") : v)) : null;
  return h("div", { class: `cand ${picked ? "picked" : ""}` },
    h("div", { class: "cand-head" }, h("b", {}, `${c.id}. ${c.approach_name_ko || "(이름 없음)"}`),
      h("span", { class: "tag" }, c.content_kind === "factual" ? "사실 기반" : "창작 오락"),
      c.estimated_seconds ? h("span", { class: "tag" }, `약 ${c.estimated_seconds}초`) : null),
    h("div", { class: "jp big" }, c.first_line_ja), h("div", { class: "ko" }, c.first_line_ko),
    row("전개", c.format_ko), row("볼 이유", c.why_watch_ko), row("첫 장면", c.first_scene_ko),
    row("구성", c.structure_ko), row("선택지", c.choices?.use ? `${c.choices.count ?? "?"}개 — ${c.choices.reason_ko}` : `사용 안 함 — ${c.choices?.reason_ko || ""}`),
    row("결말의 재미", c.payoff_ko), row("다음 영상", c.next_video_pull_ko), row("시각 자료", c.visuals_ko),
    row("소스 난이도", c.source_difficulty_ko), row("주장 수준", c.claims_ko), row("주의", c.risks_ko), row("중복 회피", c.overlap_check_ko),
    h("div", { class: "row end" }, h("button", { class: picked ? "" : "primary", onclick: onPick }, picked ? "선택됨 (다시 적용)" : "이 방향 선택")));
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
    field("추가 메모", bindInput(idea, "notes", save, { multiline: true, rows: 2, placeholder: "참고할 점, 원하는 분위기 등" }))));

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
  if (pc.items?.length) {
    for (const c of pc.items) candBox.append(candidateCard(c, () => pick(c), p.plan?.source?.candidate_id === c.id && p.plan?.source?.selected_at > (pc.generated_at || "")));
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
      pc.generated_at ? h("span", { class: "muted small" }, `제안 시각 ${fmtTime(pc.generated_at)} · 참고한 이전 프로젝트 ${pc.history_used ?? 0}개`) : null),
    pc.comparison_ko ? h("div", { class: "note" }, h("b", {}, "후보 차이: "), pc.comparison_ko) : null,
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
