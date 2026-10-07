// 5단계: 화풍 확정 → 반복 등장 요소 정의 → 기준 이미지 등록·확정
import { state, changed } from "../state.js";
import { h, section, field, bindInput, select, confirmBox, modal, toast, empty } from "../ui.js";
import { runAi, aiReady } from "../aiflow.js";
import { go, rerender } from "../nav.js";
import { presets, vis, ELEMENT_TYPES, FEATURE_KEYS, uploadImages, thumb, clipLabel } from "../vis.js";

function newId(prefix) { return `${prefix}_${Math.random().toString(16).slice(2, 10)}`; }

function styleFromPreset(pr) {
  return { preset_id: pr.id, locked: false, version: 0, label_ko: pr.label_ko, desc_ko: pr.desc_ko, style_en: pr.style_en,
    palette: pr.palette.map((c) => ({ ...c })), lighting_en: pr.lighting_en, material_en: pr.material_en, line_en: pr.line_en,
    motion_en: pr.motion_en, motion_ko: pr.motion_ko, avoid_en: pr.avoid_en, accent: { name: "", hex: "", note_ko: "" } };
}

// 요소 내용 지문(확정 시 버전 증가 판단)
const elContent = (e) => JSON.stringify([e.type, e.fixed_en, e.must_keep, e.features, e.primary_ref_id, e.name]);

export async function renderVisual(page) {
  const p = state.project;
  const v = vis(p);
  const P = await presets();
  page.append(h("div", { class: "page-head" }, h("h1", {}, "5. 화풍 · 반복 등장 요소"),
    h("div", { class: "muted" }, "여러 클립에서 같은 인물·사물·장소가 같아 보이도록, 바뀌면 안 되는 것(고정)을 여기서 정하고 잠급니다. ",
      "고정 묘사는 AI가 다시 쓰지 않고 프로그램이 모든 프롬프트에 원문 그대로 넣습니다. 클립별 행동·구도·카메라(가변)는 6단계에서 씁니다.")));
  page.append(h("div", { class: "steps-guide" },
    ["① 화풍 프리셋 고르기 → 확정", "② 반복 등장 요소 정하기(AI 초안 가능)", "③ 요소마다 기준 이미지 올리고 비교 → 하나 선택", "④ 요소 확정(잠금)", "⑤ 6단계에서 클립마다 연결"]
      .map((t) => h("span", {}, t))));
  if (v.example_note_ko) page.append(h("div", { class: "note" }, v.example_note_ko));

  renderStyle(page, p, v, P);
  renderElements(page, p, v);
  page.append(h("div", { class: "row end" }, h("button", { class: "primary", onclick: () => go("sources") }, "6. 영상소스·Flow 단계로 →")));
}

function renderStyle(page, p, v, P) {
  const st = v.style;
  const locked = !!st?.locked;
  const cards = h("div", { class: "grid3" }, Object.values(P.styles).map((pr) => {
    const cur = st?.preset_id === pr.id;
    return h("div", { class: `preset ${cur ? "picked" : ""}` },
      h("div", { class: "row between" }, h("b", {}, pr.label_ko), pr.recommended ? h("span", { class: "tag" }, "기본 추천") : null),
      h("div", { class: "swatches" }, pr.palette.map((c) => h("span", { class: "sw", style: { background: c.hex }, title: `${c.name} ${c.hex}` }))),
      h("div", { class: "small" }, pr.desc_ko),
      h("div", { class: "muted small" }, "어울리는 소재: ", pr.suits_ko),
      h("div", { class: "muted small" }, "권장 움직임: ", pr.motion_ko),
      h("div", { class: "row end" }, h("button", { class: cur ? "small" : "small primary", disabled: locked, onclick: async () => {
        if (st?.style_en && !(await confirmBox("화풍 바꾸기", "지금 프로젝트 화풍 내용(직접 고친 부분 포함)을 이 프리셋으로 바꿉니다.", "바꾸기"))) return;
        v.style = styleFromPreset(pr); changed(true); rerender();
      } }, cur ? "선택됨(다시 불러오기)" : "이 화풍 선택")));
  }));
  const body = [h("div", { class: "muted small" }, P.note_ko, " 프리셋은 특정 작가·기존 캐릭터의 모방이 아니라 일반적인 시각 특징으로만 정의했습니다."), cards];
  if (st?.style_en) {
    const dis = (el) => { el.disabled = locked; return el; };
    const pal = { text: (st.palette || []).map((c) => `${c.name} ${c.hex || ""}`.trim()).join("\n") };
    st.accent = st.accent || { name: "", hex: "", note_ko: "" };
    body.push(h("div", { class: `style-box ${locked ? "locked" : ""}` },
      h("div", { class: "row between" }, h("b", {}, `이 프로젝트의 화풍: ${st.label_ko || st.preset_id}`),
        h("span", { class: `badge ${locked ? "ok" : "todo"}` }, locked ? `확정됨 v${st.version}` : "확정 전")),
      locked ? h("div", { class: "muted small" }, "확정된 화풍은 프롬프트 재생성(전체·일부)으로 바뀌지 않습니다. 바꾸려면 [확정 풀기]를 누르세요.") : null,
      field("고정 영어 스타일 블록(모든 프롬프트에 그대로 들어감)", dis(bindInput(st, "style_en", () => changed(), { multiline: true, rows: 3, class: "full mono" }))),
      h("div", { class: "grid3" },
        field("조명", dis(bindInput(st, "lighting_en", () => changed()))),
        field("재질", dis(bindInput(st, "material_en", () => changed()))),
        field("선 표현", dis(bindInput(st, "line_en", () => changed())))),
      h("div", { class: "grid2" },
        field("기본 색상(한 줄에 '이름 #색상')", dis(bindInput(pal, "text", () => {
          st.palette = pal.text.split("\n").map((x) => x.trim()).filter(Boolean).map((x) => { const m = x.match(/^(.*?)\s*(#[0-9a-fA-F]{3,8})?$/); return { name: m[1].trim(), hex: m[2] || "" }; });
          changed();
        }, { multiline: true, rows: 3 }))),
        field("피할 요소(영어)", dis(bindInput(st, "avoid_en", () => changed(), { multiline: true, rows: 3 })))),
      field("권장 움직임", dis(bindInput(st, "motion_ko", () => changed()))),
      h("div", { class: "accent-box" }, h("b", { class: "small" }, "이번 회차 강조색(확정 후에도 바꿀 수 있음, 기본 색상은 유지)"),
        h("div", { class: "grid3" },
          field("강조색 이름(영어)", bindInput(st.accent, "name", () => changed(), { placeholder: "dusty coral" })),
          field("색상 코드", bindInput(st.accent, "hex", () => changed(), { placeholder: "#D98E73" })),
          field("메모", bindInput(st.accent, "note_ko", () => changed(), { placeholder: "작은 소품에만" }))),
        h("div", { class: "muted small" }, "강조색을 바꾸면 이미 검수한 클립은 '재검토 필요'로 표시됩니다.")),
      h("div", { class: "row" },
        locked
          ? h("button", { onclick: async () => {
            if (!(await confirmBox("화풍 확정 풀기", "확정을 풀면 화풍 내용을 고칠 수 있습니다. 고친 뒤 다시 확정하면 버전이 올라가고, 검수한 클립은 재검토가 필요해집니다.", "확정 풀기"))) return;
            st.locked = false; changed(true); rerender();
          } }, "확정 풀기")
          : h("button", { class: "primary", onclick: () => {
            const sig = JSON.stringify([st.style_en, st.palette, st.lighting_en, st.material_en, st.line_en, st.avoid_en]);
            if (sig !== st.locked_sig) { st.version = (st.version || 0) + 1; st.locked_sig = sig; }
            st.locked = true; st.locked_at = new Date().toISOString(); changed(true); toast(`화풍을 확정했습니다(v${st.version}).`, "ok"); rerender();
          } }, "이 화풍으로 확정(잠금)"))));
    // 마스코트(선택)
    const mascot = v.elements.find((e) => e.mascot);
    body.push(h("details", { class: "mascot" }, h("summary", {}, "마스코트(선택 사항)"),
      h("div", { class: "muted small" }, "쓰는 경우 직접 준비한 오리지널 이미지(또는 미리 만든 포즈 이미지)를 기준 이미지로 올리고 모든 장면에서 그 이미지를 재사용합니다. ",
        "장면마다 새 캐릭터를 생성하지 않습니다. 기존 캐릭터를 닮은 디자인은 쓰지 마세요."),
      mascot ? h("div", { class: "small" }, `마스코트 요소: ${mascot.name} — 아래 등장 요소 목록에서 기준 이미지·포즈를 관리하세요.`)
        : h("button", { class: "small", onclick: () => {
          v.elements.push({ id: newId("el"), name: "Mascot", type: "character", mascot: true, desc_ko: "채널 오리지널 마스코트(선택)",
            fixed_en: "", features: {}, must_keep: ["exact original design from the reference image"], status: "draft", locked: false, version: 0, source: "manual" });
          changed(true); rerender();
        } }, "마스코트 요소 추가")));
  }
  page.append(section("① 화풍 (프로젝트마다 확정)", ...body));
}

function renderElements(page, p, v) {
  const clips = p.flow?.clips || [];
  const box = section("②~④ 반복 등장 요소 (인물·캐릭터·사물·장소)",
    h("div", { class: "muted small" }, "여러 클립에 나오거나 선택·결과 화면에서 같은 모양을 유지해야 하는 것만 등록하세요. 클립에는 실제로 나오는 요소만 연결합니다(6단계)."),
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: () => extract(p, v) }, "대본에서 AI로 초안 추출"),
      !aiReady() ? h("span", { class: "muted small" }, "AI 연결 전에는 직접 추가하세요.") : null,
      h("span", { class: "muted small" }, "직접 추가:"),
      Object.entries(ELEMENT_TYPES).map(([k, lab]) => h("button", { class: "small", onclick: () => {
        v.elements.push({ id: newId("el"), name: "", type: k, desc_ko: "", fixed_en: "", features: {}, must_keep: [], status: "draft", locked: false, version: 0, source: "manual" });
        changed(true); rerender();
      } }, `+ ${lab}`))));
  if (!v.elements.length) box.append(empty("아직 등록한 요소가 없습니다."));
  for (const e of v.elements) box.append(elementCard(p, v, e, clips));
  page.append(box);
}

function elementCard(p, v, e, clips) {
  const locked = !!e.locked;
  const dis = (el) => { el.disabled = locked; return el; };
  e.features = e.features || {};
  const keep = { text: (e.must_keep || []).join("\n") };
  const refs = v.assets.filter((a) => a.element_id === e.id && (a.kind === "reference" || a.kind === "pose"));
  const used = clips.filter((c) => (c.element_ids || []).includes(e.id));
  const fileIn = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", multiple: true });
  return h("div", { class: `element ${locked ? "locked" : ""}` },
    h("div", { class: "row between" },
      h("div", { class: "row tight" }, h("b", {}, e.name || "(이름 없음)"), h("span", { class: "tag" }, ELEMENT_TYPES[e.type] || e.type),
        e.mascot ? h("span", { class: "tag" }, "마스코트") : null,
        h("span", { class: `badge ${locked ? "ok" : "todo"}` }, locked ? `확정 v${e.version}` : "초안"),
        e.source === "ai" ? h("span", { class: "muted small" }, "AI 초안") : null),
      h("span", { class: "muted small" }, used.length ? `사용 클립: ${used.map(clipLabel).join(", ")}` : "아직 연결된 클립 없음")),
    h("div", { class: "grid2" },
      field("이름(영어 권장 — 프롬프트에 그대로 쓰임)", dis(bindInput(e, "name", () => changed()))),
      field("유형", dis(select(Object.entries(ELEMENT_TYPES), e.type, (val) => { e.type = val; changed(true); rerender(); })))),
    field("한국어 설명", dis(bindInput(e, "desc_ko", () => changed()))),
    field("고정 영어 묘사(모든 연결 클립에 원문 그대로 들어감)", dis(bindInput(e, "fixed_en", () => changed(), { multiline: true, rows: 3, class: "full mono" }))),
    h("div", { class: "grid3" }, (FEATURE_KEYS[e.type] || []).map(([k, lab]) => field(lab, dis(bindInput(e.features, k, () => changed()))))),
    field("변경하면 안 되는 특징(영어, 한 줄에 하나)", dis(bindInput(keep, "text", () => { e.must_keep = keep.text.split("\n").map((x) => x.trim()).filter(Boolean); changed(); }, { multiline: true, rows: 2 }))),
    h("div", { class: "refs" },
      h("div", { class: "small" }, h("b", {}, "기준 이미지"), " — 외형을 유지하기 위한 자료(시작 프레임과 다름). 후보를 여러 장 올려 나란히 비교하고 하나를 고르세요."),
      h("div", { class: "thumbs" }, refs.map((a) => h("div", { class: `cand-img ${e.primary_ref_id === a.id ? "picked" : ""}` },
        thumb(p, a.file, a.orig_name),
        h("div", { class: "row tight" },
          e.primary_ref_id === a.id ? h("span", { class: "badge ok" }, "기준") :
            h("button", { class: "tiny", disabled: locked, onclick: () => { e.primary_ref_id = a.id; changed(true); rerender(); } }, "이걸 기준으로"),
          h("select", { class: "tiny", disabled: locked, onchange: (ev) => { a.kind = ev.target.value; changed(); } },
            [["reference", "기준"], ["pose", "포즈"]].map(([val, lab]) => h("option", { value: val, selected: a.kind === val }, lab))))))),
      locked ? null : h("div", { class: "row" }, fileIn, h("button", { class: "small", onclick: async () => {
        const added = await uploadImages(fileIn.files, { kind: "reference", element_id: e.id });
        if (added.length && !e.primary_ref_id) e.primary_ref_id = added[0].id;
        if (added.length) { changed(true); rerender(); }
      } }, "이미지 올리기"))),
    h("details", {}, h("summary", {}, e.flow_name ? `Flow Characters 기록: ${e.flow_name}` : "Flow Characters 기능을 쓰는 경우(선택)"),
      h("div", { class: "muted small" }, "Flow에서 직접 Characters에 등록한 이름·메모를 기록만 합니다. 여기에 이름을 적어도 Flow에 자동 등록·연결되지 않습니다."),
      h("div", { class: "grid2" }, field("Flow에 등록한 이름", bindInput(e, "flow_name", () => changed())), field("메모", bindInput(e, "flow_note", () => changed()))),
      h("label", { class: "chk" }, h("input", { type: "checkbox", checked: !!e.flow_registered, onchange: (ev) => { e.flow_registered = ev.target.checked; changed(); } }), " Flow에서 직접 등록했음(사용자 확인)")),
    h("div", { class: "row end" },
      locked
        ? h("button", { class: "small", onclick: async () => {
          if (!(await confirmBox("요소 확정 풀기", "확정을 풀고 고치면, 이 요소를 쓰는 클립의 프롬프트가 바뀌고 검수한 클립은 '재검토 필요'가 됩니다.", "확정 풀기"))) return;
          e.locked = false; e.status = "draft"; changed(true); rerender();
        } }, "확정 풀기")
        : h("button", { class: "small primary", onclick: () => {
          if (!e.name?.trim() || !e.fixed_en?.trim()) { toast("이름과 고정 영어 묘사를 채운 뒤 확정하세요.", "error"); return; }
          const sig = elContent(e);
          if (sig !== e.locked_sig) {
            e.history = [...(e.history || []), ...(e.locked_sig ? [{ version: e.version, fixed_en: e.fixed_en, must_keep: e.must_keep, primary_ref_id: e.primary_ref_id, at: new Date().toISOString() }] : [])].slice(-20);
            e.version = (e.version || 0) + 1; e.locked_sig = sig;
          }
          e.locked = true; e.status = "confirmed"; e.locked_at = new Date().toISOString();
          if (!e.primary_ref_id) toast("기준 이미지 없이 확정했습니다. 텍스트 묘사만으로는 모양이 더 쉽게 달라질 수 있습니다.", "info");
          changed(true); rerender();
        } }, "확정(잠금)"),
      h("button", { class: "small danger", disabled: locked, onclick: async () => {
        if (!(await confirmBox("요소 삭제", used.length ? `이 요소는 클립 ${used.length}개에 연결되어 있습니다. 삭제하면 연결이 끊긴 것으로 표시됩니다(이미지 파일은 남음).` : "이 요소를 목록에서 지웁니다(이미지 파일은 남음).", "삭제", true))) return;
        v.elements = v.elements.filter((x) => x.id !== e.id); changed(true); rerender();
      } }, "삭제")));
}

async function extract(p, v) {
  const r = await runAi("elements", {}, { guard: () => JSON.stringify(v.elements.map((e) => [e.id, e.locked, e.fixed_en])), what: "등장 요소" });
  if (!r) return;
  const picks = r.add.map(() => ({ on: true }));
  const ups = r.update.map(() => ({ on: false }));
  const row = (prop, pick, extra) => {
    const cb = h("input", { type: "checkbox", checked: pick.on, onchange: (ev) => (pick.on = ev.target.checked) });
    return h("div", { class: "diff" }, h("label", { class: "chk" }, cb, h("b", {}, `${prop.name} (${ELEMENT_TYPES[prop.type]})`), extra),
      h("div", { class: "small mono" }, prop.fixed_en), h("div", { class: "ko small" }, prop.desc_ko), prop.reason_ko ? h("div", { class: "muted small" }, prop.reason_ko) : null);
  };
  const body = h("div", {},
    h("div", { class: "note" }, "AI 초안입니다. 고를 항목만 '초안'으로 추가됩니다. 확정·잠긴 요소는 바꾸지 않습니다."),
    r.add.length ? h("h4", {}, "새 요소") : null, r.add.map((x, i) => row(x, picks[i])),
    r.update.length ? h("h4", {}, "확정 전 요소의 고친 제안(기본: 적용 안 함)") : null,
    r.update.map((u, i) => row(u.proposal, ups[i], h("span", { class: "muted small" }, " → 기존 초안 덮어쓰기"))),
    r.skipped_locked.length ? h("div", { class: "muted small" }, "확정 요소라 바꾸지 않음: " + r.skipped_locked.map((x) => x.name).join(", ")) : null,
    r.notes_ko ? h("div", { class: "muted small" }, r.notes_ko) : null);
  const ok = await modal("AI 등장 요소 초안", body, [{ label: "적용 안 함", value: false }, { label: "체크한 항목 적용", value: true, primary: true }], { wide: true });
  if (!ok) return;
  r.add.forEach((x, i) => { if (picks[i].on) v.elements.push({ ...x, status: "draft", locked: false, version: 0 }); });
  r.update.forEach((u, i) => {
    const e = v.elements.find((x) => x.id === u.id);
    if (ups[i].on && e && !e.locked) Object.assign(e, { name: u.proposal.name, type: u.proposal.type, desc_ko: u.proposal.desc_ko, fixed_en: u.proposal.fixed_en, features: u.proposal.features, must_keep: u.proposal.must_keep });
  });
  changed(true); rerender();
}
