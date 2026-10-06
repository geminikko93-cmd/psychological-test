import { api, runJob, mediaUrl } from "../api.js";
import { state, changed, flush } from "../state.js";
import { h, clear, section, field, bindInput, select, confirmBox, toast, empty, fmtSec, showError } from "../ui.js";
import { go, rerender } from "../nav.js";
import { renderFlow } from "./flow.js";

export async function renderSources(page) {
  const p = state.project;
  page.append(h("div", { class: "page-head" }, h("h1", {}, "5. 영상 (Google Flow)"),
    h("div", { class: "muted" }, "기획·대본과 최종 음성 시간에 맞춰 Flow(Gemini Omni Flash 1.1)용 4·6·8·10초 클립을 계획하고 프롬프트를 만듭니다. Flow 사이트에서 직접 생성한 MP4를 클립마다 넣으세요.")));
  if (!(p.script?.scenes || []).length) { page.append(section(null, empty("대본이 없습니다."))); return; }
  const subStale = state.status?.steps?.subtitles?.state === "stale";
  if (subStale) page.append(h("div", { class: "banner stale" }, "자막이 최신이 아니어서 클립 배치 시간이 정확하지 않을 수 있습니다. 자막을 다시 맞추세요."));
  await renderFlow(page);
  const other = h("details", { class: "card other-sources" }, h("summary", {}, "기타 소스(선택): 내 영상·이미지 파일 넣기 / 스톡 검색"));
  page.append(other);
  let loaded = false;
  other.addEventListener("toggle", async () => { if (other.open && !loaded) { loaded = true; await renderStock(other); } });
  page.append(h("div", { class: "row end" }, h("button", { class: "primary", onclick: () => go("export") }, "게시·내보내기 단계로 →")));
}

export function sceneTimes(p) {
  const cues = p.subtitles?.cues || [];
  const scenes = p.script?.scenes || [];
  const out = scenes.map((sc) => {
    const ids = new Set(sc.lines.map((l) => l.id));
    const cs = cues.filter((c) => ids.has(c.line_id));
    return { id: sc.id, start: cs.length ? Math.min(...cs.map((c) => c.start)) : null, speechEnd: cs.length ? Math.max(...cs.map((c) => c.end)) : null };
  });
  const dur = p.audio?.processed?.duration;
  out.forEach((s, i) => {
    const nxt = out.slice(i + 1).find((o) => o.start !== null);
    s.end = nxt ? nxt.start : (dur ?? s.speechEnd);
  });
  return Object.fromEntries(out.map((o) => [o.id, o]));
}

async function renderStock(page) {
  const p = state.project;
  const scenes = p.script?.scenes || [];
  p.sources = p.sources || { items: [] };
  if (!scenes.length) return;
  let prov = {};
  try { prov = await api.get("/api/sources/status"); } catch { /* 무시 */ }
  page.append(h("div", { class: "note" },
    "검색: ", prov.pexels ? "Pexels 사용 가능" : "Pexels 키 없음", " · ", prov.pixabay ? "Pixabay 사용 가능" : "Pixabay 키 없음",
    !prov.pexels && !prov.pixabay ? " — [설정]에서 무료 API 키를 넣으면 검색할 수 있습니다. 키 없이도 내 파일은 넣을 수 있습니다." : "",
    " 대본 단계의 '영상소스 구하기 쉬운 장면으로' 다시 만들기로 장면 자체를 바꿀 수도 있습니다."));
  const times = sceneTimes(p);
  scenes.forEach((sc, i) => page.append(sceneBox(sc, i, times[sc.id], prov)));
}

function sceneBox(sc, i, t, prov) {
  const p = state.project;
  const items = p.sources.items.filter((x) => x.scene_id === sc.id);
  const acquired = items.filter((x) => x.status === "acquired" && !x.clip_id);
  const fileIn = h("input", { type: "file", multiple: true, accept: "video/*,image/*,.mp4,.mov,.webm,.jpg,.jpeg,.png,.webp" });
  const resultsBox = h("div", { class: "results" });
  const q = { query: (sc.search_keywords || [])[0] || "", provider: prov.pexels ? "pexels" : "pixabay", media: "video" };
  const qIn = bindInput(q, "query", null, { placeholder: "검색어(영어 권장)" });

  const card = section(null,
    h("div", { class: "scene-head" }, h("h3", {}, `장면 ${i + 1}`),
      h("span", { class: "tag" }, t?.start !== null && t?.start !== undefined ? `${fmtSec(t.start)} → ${fmtSec(t.end)} (${(t.end - t.start).toFixed(1)}초)` : "시간 미정(자막 생성 후 표시)"),
      acquired.length ? h("span", { class: "badge ok" }, `확보됨 ${acquired.length}`) : sc.no_source_needed ? h("span", { class: "badge ok" }, "소스 불필요") : h("span", { class: "badge todo" }, "미확보")),
    h("div", { class: "grid2" },
      h("div", {}, sc.lines.map((l) => h("div", { class: "line-view" }, h("div", { class: "jp" }, l.display), h("div", { class: "ko" }, l.ko)))),
      h("div", {},
        h("div", { class: "recommend" }, h("small", {}, "AI 추천 — 이런 화면이 필요합니다(아직 파일 아님)"), h("div", {}, sc.visual || "(추천 없음)"),
          sc.edit_intent ? h("div", { class: "muted small" }, "편집 의도: " + sc.edit_intent) : null),
        h("div", { class: "row wrap" }, (sc.search_keywords || []).map((k) => h("button", { class: "chip", onclick: () => { q.query = k; qIn.value = k; } }, k))),
        h("label", { class: "chk" }, h("input", { type: "checkbox", checked: !!sc.no_source_needed, onchange: (e) => { sc.no_source_needed = e.target.checked; changed(true); rerender(); } }), " 이 장면은 소스 불필요(단색 배경·텍스트 화면 등)"))),
    h("h4", {}, "확보한 파일"),
    acquired.length ? h("div", { class: "items" }, acquired.map((it) => itemCard(it))) : h("div", { class: "muted small" }, "아직 확보한 파일이 없습니다."),
    h("div", { class: "row" }, h("span", {}, "내 파일 넣기"), fileIn, h("button", { onclick: async () => {
      if (!fileIn.files.length) { toast("파일을 선택하세요.", "info"); return; }
      await flush();
      const fd = new FormData();
      for (const f of fileIn.files) fd.append("files", f);
      fd.append("scene_id", sc.id);
      try {
        const r = await api.upload(`/api/projects/${p.id}/sources/upload`, fd);
        p.sources.items.push(...r.items);
        for (const e of r.errors) toast(`${e.name}: ${e.error}`, "error", e.hint || "");
        if (r.items.length) { toast(`${r.items.length}개 파일을 넣었습니다.`, "ok"); changed(true); rerender(); }
      } catch (e) { showError(e); }
    } }, "넣기")),
    (prov.pexels || prov.pixabay) ? h("details", { class: "search" }, h("summary", {}, "스톡 소스 검색 (Pexels / Pixabay 공식 API)"),
      h("div", { class: "row" },
        select([...(prov.pexels ? [["pexels", "Pexels"]] : []), ...(prov.pixabay ? [["pixabay", "Pixabay"]] : [])], q.provider, (v) => (q.provider = v)),
        select([["video", "영상"], ["image", "사진"]], "video", (v) => (q.media = v)),
        qIn,
        h("button", { onclick: async () => {
          clear(resultsBox).append(h("div", { class: "muted" }, "검색 중…"));
          try {
            const r = await api.post("/api/sources/search", q);
            clear(resultsBox);
            if (!r.items.length) { resultsBox.append(empty("결과가 없습니다. 다른 검색어나 다른 장면 구성을 고려하세요.")); return; }
            for (const c of r.items) resultsBox.append(h("div", { class: "result" },
              c.thumb ? h("img", { src: c.thumb, loading: "lazy", alt: "" }) : h("div", { class: "noimg" }, "미리보기 없음"),
              h("div", { class: "small" }, `${c.kind === "video" ? "영상" : "사진"} ${c.width || "?"}×${c.height || "?"}${c.duration ? ` · ${c.duration}초` : ""}`),
              h("div", { class: "small muted" }, `작가: ${c.author || "-"}`),
              h("div", { class: "small muted" }, "후보(아직 확보 아님)"),
              h("button", { class: "small primary", onclick: async () => {
                await flush();
                try {
                  const it = await runJob(api.post(`/api/projects/${p.id}/sources/download`, { candidate: c, scene_id: sc.id }));
                  p.sources.items.push(it); changed(true); toast("내려받아 확인했습니다. 이용 조건을 확인한 뒤 체크하세요.", "ok"); rerender();
                } catch (e) { if (e.status !== 499) showError(e); }
              } }, "내려받아 사용")));
          } catch (e) { clear(resultsBox); showError(e); }
        } }, "검색")),
      resultsBox) : null);
  return card;
}

function itemCard(it) {
  const p = state.project;
  const url = mediaUrl(p.id, it.file);
  const preview = it.kind === "video" ? h("video", { src: url, muted: true, controls: true, preload: "metadata" }) : h("img", { src: url, alt: "" });
  return h("div", { class: "item" }, preview,
    h("div", { class: "item-body" },
      h("div", {}, h("b", {}, it.orig_name), " ", h("span", { class: "tag" }, it.provider === "local" ? "내 파일" : it.provider)),
      it.page_url ? h("div", { class: "small" }, "원본: ", h("a", { href: it.page_url, target: "_blank", rel: "noopener noreferrer" }, it.page_url)) : null,
      it.author ? h("div", { class: "small" }, "작가: ", it.author) : null,
      h("div", { class: "small muted" }, it.license_note),
      field("크레딧 표기", bindInput(it, "credit_text", () => changed(), { placeholder: it.provider === "local" ? "필요하면 입력" : "" })),
      field("메모(이용 조건 확인 내용 등)", bindInput(it, "memo", () => changed())),
      h("label", { class: "chk" }, h("input", { type: "checkbox", checked: !!it.rights_checked, onchange: (e) => { it.rights_checked = e.target.checked; changed(); } }), " 이용 조건을 직접 확인함"),
      h("button", { class: "small danger", onclick: async () => {
        if (!(await confirmBox("소스 빼기", "이 장면에서 파일을 뺍니다(파일은 프로젝트 폴더에 남습니다).", "빼기", true))) return;
        p.sources.items = p.sources.items.filter((x) => x.id !== it.id); changed(true); rerender();
      } }, "빼기")));
}
