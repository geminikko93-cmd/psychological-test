// 7단계: 결과 비교·검수(사람이 눈으로 확인). 자동 동일성 판정은 하지 않는다.
import { state, changed } from "../state.js";
import { h, section, bindInput, select, toast, empty, fmtSec } from "../ui.js";
import { go, rerender } from "../nav.js";
import { vis, inspect, syncComposed, extractFrames, latestFrame, thumb, clipLabel, GEN_MODES, ROLES, CHECK_ITEMS, elById, assetById } from "../vis.js";

export async function renderReview(page) {
  const p = state.project;
  const clips = p.flow?.clips || [];
  page.append(h("div", { class: "page-head" }, h("h1", {}, "7. 결과 비교 · 일관성 검수"),
    h("div", { class: "muted" }, "기준 이미지와 실제로 편집에 쓰는 구간의 시작·중간·끝 프레임을 나란히 놓고 눈으로 비교합니다. ",
      "텍스트·참조 이미지를 써도 완벽한 동일성은 보장되지 않습니다. 이 프로그램은 얼굴·사물이 같은지 자동으로 판정하지 않습니다.")));
  if (!clips.length) { page.append(section(null, empty("클립 계획이 없습니다. 6단계에서 클립을 계획하고 영상을 넣으세요."))); return; }
  const insp = await inspect(p);
  syncComposed(p, insp);
  const counts = {};
  for (const c of clips) { const s = insp[c.id]?.review?.state || "no_media"; counts[s] = (counts[s] || 0) + 1; }
  page.append(h("div", { class: "row" }, [["pass", "통과"], ["todo", "검수 전"], ["recheck", "재검토 필요"], ["fix", "수정 필요"], ["no_media", "영상 미등록"]]
    .map(([k, lab]) => h("span", { class: `badge ${k === "pass" ? "ok" : k === "recheck" || k === "fix" ? "stale" : "todo"}` }, `${lab} ${counts[k] || 0}`))));
  for (const c of clips) page.append(reviewCard(p, c, insp[c.id], clips));
  page.append(h("div", { class: "row end" }, h("button", { class: "primary", onclick: () => go("export") }, "8. 게시·내보내기 단계로 →")));
}

function reviewCard(p, c, r, clips) {
  const rv = r?.review || { state: "no_media", label_ko: "영상 미등록", reasons: [] };
  const box = h("section", { class: `card review ${rv.state}` });
  const idx = clips.findIndex((x) => x.id === c.id);
  const prev = idx > 0 ? clips[idx - 1] : null;
  const head = h("div", { class: "row between" },
    h("div", { class: "row tight" }, h("h3", {}, clipLabel(c)), h("span", { class: "tag" }, ROLES[c.role] || "역할 없음"),
      h("span", { class: "tag" }, GEN_MODES[c.gen_mode || "text"]), h("span", { class: "muted small" }, `${fmtSec(c.start)} → ${fmtSec(c.end)}`)),
    h("div", { class: "row tight" },
      h("span", { class: `badge ${rv.registered ? "ok" : "todo"}` }, rv.registered ? "영상 등록 완료" : "영상 미등록"),
      h("span", { class: `badge ${rv.state === "pass" ? "ok" : rv.state === "recheck" || rv.state === "fix" ? "stale" : "todo"}` }, `일관성 검수: ${rv.label_ko}`)));
  box.append(head);
  if (rv.reasons?.length) box.append(h("div", { class: "note warn small" }, h("b", {}, "재검토 사유(파일·수동 편집 내용은 그대로 보존됨)"), rv.reasons.map((x) => h("div", {}, "• " + x))));
  if (!rv.registered) { box.append(h("div", { class: "muted small" }, "6단계에서 영상(또는 정지 이미지·재사용 소재)을 연결하면 검수할 수 있습니다.")); return box; }

  // 비교 이미지
  const basis = r.frame_basis;
  const refs = (c.element_ids || []).map((id) => elById(p, id)).filter(Boolean)
    .map((e) => thumb(p, assetById(p, e.primary_ref_id)?.file, `기준: ${e.name}${e.locked ? "" : " (초안)"}`));
  const fr = ["start", "mid", "end"].map((k) => latestFrame(p, c.id, `review_${k}`, basis));
  const lab = { start: "사용 구간 시작", mid: "중간", end: "끝" };
  const frames = ["start", "mid", "end"].map((k, i) => thumb(p, fr[i]?.file, fr[i] ? `${lab[k]} ${fr[i].t}초` : `${lab[k]} (추출 전)`));
  const sf = r.start_frame;
  const prevEnd = prev ? latestFrame(p, prev.id, "last_used", null) : null;
  box.append(h("div", { class: "compare" },
    h("div", {}, h("div", { class: "field-label" }, "기준 이미지"), h("div", { class: "thumbs" }, refs.length ? refs : h("span", { class: "muted small" }, "연결된 요소의 기준 이미지 없음"))),
    h("div", {}, h("div", { class: "field-label" }, `실제 사용 구간 ${basis ? `${basis.use_start}~${Math.round((basis.use_start + basis.use_len) * 100) / 100}초` : ""}`),
      h("div", { class: "thumbs" }, frames),
      h("button", { class: "small", onclick: async () => { const f = await extractFrames(c.id, ["start", "mid", "end"]); if (f.length) rerender(); } }, fr.every(Boolean) ? "다시 추출" : "시작·중간·끝 프레임 추출")),
    sf?.file || (c.continues_previous && prevEnd) ? h("div", {}, h("div", { class: "field-label" }, "연결 기준"),
      h("div", { class: "thumbs" }, sf?.file ? thumb(p, sf.file, "시작 프레임: " + sf.label) : thumb(p, prevEnd.file, `이전 ${prevEnd.clip_label} 끝 ${prevEnd.t}초`))) : null));

  // 체크 항목
  const work = c.review_draft = c.review_draft || { checks: { ...(c.review?.checks || {}) }, memo: c.review?.memo || "" };
  box.append(h("div", { class: "checks" }, CHECK_ITEMS.map(([k, label]) => h("div", { class: "row tight" },
    h("span", { class: "check-label" }, label),
    select([["", "-"], ["ok", "통과"], ["ng", "수정 필요"], ["na", "해당 없음"]], work.checks[k] || "", (val) => { work.checks[k] = val; changed(); })))),
  h("label", { class: "field" }, h("span", { class: "field-label" }, "메모"), bindInput(work, "memo", () => changed(), { multiline: true, rows: 2 })));
  const save = (stateVal) => {
    if (stateVal === "pass" && Object.values(work.checks).includes("ng")) { toast("'수정 필요' 항목이 있어 통과로 표시할 수 없습니다.", "error"); return; }
    // 지금 상태(기준 자료·화풍·프롬프트·영상·사용 구간·시작 프레임)의 지문을 함께 저장 → 이후 바뀌면 '재검토 필요'
    c.review = { state: stateVal, checks: { ...work.checks }, memo: work.memo, reviewed_at: new Date().toISOString(), basis: r.basis, by: "사용자 확인" };
    delete c.review_draft;
    changed(true); toast(stateVal === "pass" ? "검수 통과로 표시했습니다(사람 확인)." : "'수정 필요'로 표시했습니다.", stateVal === "pass" ? "ok" : "info"); rerender();
  };
  box.append(h("div", { class: "row end" },
    c.review?.reviewed_at ? h("span", { class: "muted small" }, `마지막 검수 ${new Date(c.review.reviewed_at).toLocaleString("ko-KR")}`) : null,
    h("button", { class: "danger", onclick: () => save("fix") }, "수정 필요"),
    h("button", { class: "primary", onclick: () => save("pass") }, "눈으로 확인 — 통과")));
  return box;
}
