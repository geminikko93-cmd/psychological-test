// Google Flow(Gemini Omni Flash 1.1) 클립 계획·프롬프트·생성 영상 연결
import { api, mediaUrl } from "../api.js";
import { state, changed, flush } from "../state.js";
import { h, section, field, bindInput, select, confirmBox, toast, empty, fmtSec, copyText, showError } from "../ui.js";
import { runAi, aiReady } from "../aiflow.js";
import { rerender } from "../nav.js";

const DURS = [4, 6, 8, 10];
const MODE = { text: "텍스트로 영상", first_frame: "첫 프레임 이미지 사용", ingredients: "재료(참조 이미지) 사용" };

function flowState() {
  const p = state.project;
  p.flow = p.flow || { clips: [], style: null, based_on: null, timing: null };
  return p.flow;
}

async function planClips(confirmDrop = true) {
  const p = state.project;
  const fl = flowState();
  await flush();
  try {
    const r = await api.post(`/api/projects/${p.id}/flow/plan`);
    const lost = r.dropped.filter((d) => d.had_prompt || d.item_id);
    if (confirmDrop && lost.length && !(await confirmBox("클립 계획 다시 계산",
      `시간이 바뀌어 기존 클립 ${lost.length}개의 구간이 달라집니다.\n그 클립의 프롬프트·연결 영상은 '이전 클립'으로 보관되고, 새 클립은 프롬프트를 다시 만들어야 합니다.`, "다시 계산"))) return;
    if (lost.length) fl.previous_clips = [...(fl.previous_clips || []), ...fl.clips.filter((c) => lost.some((d) => d.id === c.id))].slice(-30);
    Object.assign(fl, { clips: r.clips, based_on: r.based_on, timing: r.timing, planned_at: new Date().toISOString() });
    changed(true);
    toast(`클립 ${r.clips.length}개를 계획했습니다(${r.timing === "실제" ? "최종 자막 시간 기준" : "대본 길이로 추정한 시간"}).`, "ok");
    rerender();
  } catch (e) { showError(e); }
}

async function makePrompts(clipIds, instruction) {
  const fl = flowState();
  const targets = clipIds ? fl.clips.filter((c) => clipIds.includes(c.id)) : fl.clips;
  if (targets.some((c) => c.prompt_en) && !(await confirmBox("프롬프트 다시 만들기",
    "이미 있는 프롬프트는 바뀝니다(이전 프롬프트는 클립마다 1개씩 보관됩니다).", "만들기"))) return;
  const r = await runAi("flow", { clip_ids: clipIds, instruction });
  if (!r) return;
  if (!fl.style?.look_en || !clipIds) fl.style = r.style;
  for (const out of r.clips) {
    const c = fl.clips.find((x) => x.id === out.clip_id);
    if (!c) continue;
    if (c.prompt_en) c.prompt_prev = { en: c.prompt_en, ko: c.prompt_ko };
    Object.assign(c, { prompt_en: out.prompt_en, prompt_ko: out.prompt_ko, beats_ko: out.beats_ko, mode: out.mode,
      mode_note_ko: out.mode_note_ko, risk_ko: out.risk_ko, prompt_for_duration: c.duration });
  }
  changed(true);
  rerender();
}

export async function renderFlow(page) {
  const p = state.project;
  const fl = flowState();
  const scenes = p.script.scenes;
  const items = p.sources?.items || [];
  const st = state.status?.steps?.sources;

  const instr = { text: "" };
  page.append(section("클립 계획",
    h("div", { class: "note" },
      "Flow의 Gemini Omni Flash 1.1은 4·6·8·10초 클립을 만듭니다. 각 장면의 내레이션 구간을 문장 경계에서 나눠, 구간을 덮는 가장 짧은 길이를 고릅니다(남는 꼬리는 CapCut에서 잘라냄). ",
      "Flow 사이트에서 직접 생성합니다(자동 로그인·비공식 연동 없음)."),
    st?.notes?.length && fl.clips?.length ? h("div", { class: "muted small" }, st.notes.join(" · ")) : null,
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: () => planClips(true) }, fl.clips?.length ? "클립 계획 다시 계산" : "클립 계획 만들기"),
      fl.clips?.length ? h("span", { class: "muted small" }, `클립 ${fl.clips.length}개 · 시간 기준: ${fl.timing === "실제" ? "최종 자막(실제)" : "대본 길이 추정(음성·자막 후 다시 계산 권장)"} · Flow 총 ${fl.clips.reduce((a, c) => a + c.duration, 0)}초 생성`) : null)));
  if (!fl.clips?.length) return;

  // 스타일 가이드
  fl.style = fl.style || { look_en: "", look_ko: "", avoid_en: "", recurring_ko: [], palette_ko: "" };
  page.append(section("공통 스타일 (모든 클립에 같은 화풍·인물·색감 유지)",
    h("div", { class: "muted small" }, "Flow는 클립마다 따로 생성하므로, AI가 각 프롬프트에 이 스타일 요점을 반복해 넣습니다. 직접 고친 뒤 '프롬프트 만들기'를 누르면 반영됩니다."),
    h("div", { class: "grid2" },
      field("스타일(영어, Flow용)", bindInput(fl.style, "look_en", () => changed(), { multiline: true, rows: 3 })),
      field("스타일 의미(한국어)", bindInput(fl.style, "look_ko", () => changed(), { multiline: true, rows: 3 }))),
    field("피할 요소(영어)", bindInput(fl.style, "avoid_en", () => changed(), { placeholder: "on-screen text, logos, talking people…" })),
    fl.style.recurring_ko?.length ? h("div", { class: "muted small" }, "반복 등장: " + fl.style.recurring_ko.join(", ") + (fl.style.palette_ko ? ` · 색감: ${fl.style.palette_ko}` : "")) : null,
    field("AI에게 추가 지시(선택)", bindInput(instr, "text", null, { placeholder: "예: 실사 대신 따뜻한 3D 미니어처 느낌 / 인물 없이 사물 중심" })),
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: () => makePrompts(null, instr.text) }, fl.clips.some((c) => c.prompt_en) ? "모든 클립 프롬프트 다시 만들기" : "모든 클립 프롬프트 만들기"),
      !aiReady() ? h("span", { class: "muted small" }, "AI 연결 전: 프롬프트를 직접 써도 됩니다.") : null,
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
}

function allPromptsText() {
  const fl = flowState();
  return fl.clips.map((c) => `[장면 ${c.scene_no} · 클립 ${c.index_in_scene} · ${c.duration}초]\n${c.prompt_en || "(프롬프트 없음)"}`).join("\n\n");
}

function clipCard(c, sc, items) {
  const p = state.project;
  const lines = sc.lines.filter((l) => c.line_ids.includes(l.id));
  const item = items.find((i) => i.id === c.item_id && i.status === "acquired");
  const tooShort = c.duration + 0.05 < c.need_s;
  const durChanged = c.prompt_en && c.prompt_for_duration && c.prompt_for_duration !== c.duration;
  const fileIn = h("input", { type: "file", accept: "video/mp4,video/*" });
  const frameBox = h("div", {});
  const ta = bindInput(c, "prompt_en", () => { c.prompt_manual = true; changed(); }, { multiline: true, rows: 5, class: "full mono" });
  const instr = { text: "" };

  return h("div", { class: `clip ${item ? "has-file" : ""}` },
    h("div", { class: "clip-head" },
      h("b", {}, `클립 ${c.index_in_scene}`),
      h("span", { class: "tag" }, `배치 ${fmtSec(c.start)} → ${fmtSec(c.end)} · 필요 ${c.need_s}초 (${c.timing})`),
      h("span", {}, "Flow 길이 "),
      select(DURS.map((d) => [String(d), `${d}초`]), String(c.duration), (v) => { c.duration = Number(v); c.duration_manual = true; changed(true); rerender(); }),
      item ? h("span", { class: "badge ok" }, "영상 넣음") : c.prompt_en ? h("span", { class: "badge todo" }, "프롬프트만") : h("span", { class: "badge todo" }, "프롬프트 없음")),
    tooShort ? h("div", { class: "note warn" }, `선택한 ${c.duration}초가 필요한 ${c.need_s}초보다 짧습니다. 더 긴 길이를 고르거나 [클립 계획 다시 계산]을 누르세요.`) : null,
    durChanged ? h("div", { class: "note warn" }, `프롬프트는 ${c.prompt_for_duration}초 기준으로 쓰였습니다. 길이를 바꿨으니 이 클립 프롬프트를 다시 만드는 것을 권장합니다.`) : null,
    h("div", { class: "clip-lines" }, lines.map((l) => h("div", {}, h("span", { class: "jp" }, l.display.replace(/\n/g, "")), " ", h("span", { class: "ko" }, l.ko)))),
    field("Flow 프롬프트 (영어 — Flow에 붙여 넣기)", ta),
    h("div", { class: "row" },
      h("button", { class: "small primary", disabled: !c.prompt_en, onclick: () => copyText(c.prompt_en, "프롬프트") }, "프롬프트 복사"),
      h("span", { class: "muted small" }, `Flow 설정: Gemini Omni Flash 1.1 · 세로(9:16) · ${c.duration}초 · ${MODE[c.mode] || "텍스트로 영상"}`)),
    c.prompt_ko ? h("div", { class: "ko small" }, "의미: ", c.prompt_ko) : null,
    c.beats_ko ? h("div", { class: "small" }, "진행: ", c.beats_ko) : null,
    c.mode_note_ko ? h("div", { class: "muted small" }, "모드 이유: ", c.mode_note_ko) : null,
    c.risk_ko ? h("div", { class: "note warn small" }, c.risk_ko) : null,
    h("details", {}, h("summary", {}, "이 클립만 다시 만들기"),
      h("div", { class: "row" }, bindInput(instr, "text", null, { placeholder: "예: 더 밝게 / 손만 나오게 / 카메라 고정", class: "grow" }),
        h("button", { class: "small", onclick: () => makePrompts([c.id], instr.text) }, "다시 만들기")),
      c.prompt_prev ? h("div", { class: "small" }, "직전 프롬프트: ", h("span", { class: "mono" }, c.prompt_prev.en), " ",
        h("button", { class: "small", onclick: () => { const cur = { en: c.prompt_en, ko: c.prompt_ko }; c.prompt_en = c.prompt_prev.en; c.prompt_ko = c.prompt_prev.ko; c.prompt_prev = cur; changed(true); rerender(); } }, "되돌리기")) : null),
    h("div", { class: "clip-file" },
      item ? h("div", { class: "clip-video" },
        h("video", { src: mediaUrl(p.id, item.file), controls: true, muted: true, preload: "metadata" }),
        h("div", {},
          h("div", { class: "small" }, item.orig_name, item.duration ? ` · ${item.duration}초` : ""),
          item.duration && item.duration + 0.05 < c.need_s ? h("div", { class: "note warn small" }, `영상(${item.duration}초)이 배치 구간(${c.need_s}초)보다 짧습니다.`) : null,
          h("div", { class: "muted small" }, item.license_note),
          h("label", { class: "chk" }, h("input", { type: "checkbox", checked: !!item.rights_checked, onchange: (e) => { item.rights_checked = e.target.checked; changed(); } }), " 이용약관·AI 표시 필요 여부 확인함"),
          h("div", { class: "row" },
            h("button", { class: "small", title: "다음 클립을 '첫 프레임 이미지'로 이어 만들 때 사용", onclick: async () => {
              try { const r = await api.post(`/api/projects/${p.id}/flow/lastframe`, { item_id: item.id }); frameBox.replaceChildren(
                h("a", { href: mediaUrl(p.id, r.file, Date.now()), download: `scene${c.scene_no}_clip${c.index_in_scene}_last.png` },
                  h("img", { src: mediaUrl(p.id, r.file, Date.now()), class: "lastframe" }), h("div", { class: "small" }, "클릭해서 저장 → 다음 클립의 첫 프레임으로 Flow에 올리기"))); } catch (e) { showError(e); }
            } }, "마지막 프레임 저장"),
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
            if (item) p.sources.items = p.sources.items.filter((x) => x.id !== item.id);
            p.sources.items.push(r.items[0]);
            c.item_id = r.items[0].id;
            changed(true);
            const d = r.items[0].duration;
            toast("영상을 넣었습니다.", "ok", d && d + 0.05 < c.need_s ? `영상 ${d}초가 필요한 ${c.need_s}초보다 짧습니다.` : "");
            rerender();
          } catch (e) { showError(e); }
        } }, "넣기"))));
}
