import { api, onJobUpdate, cancelJob } from "./api.js";
import { state, onState, openProject, closeProject, flush, confirmLeave } from "./state.js";
import { h, clear, badge, showError } from "./ui.js";
import { renderProjects } from "./pages/projects.js";
import { renderPlan } from "./pages/plan.js";
import { renderScript } from "./pages/script.js";
import { renderAudio } from "./pages/audio.js";
import { renderSubtitles } from "./pages/subtitles.js";
import { renderSources } from "./pages/sources.js";
import { renderExport } from "./pages/export.js";
import { renderSettings } from "./pages/settings.js";
import { renderChannel } from "./pages/channel.js";

const STEPS = [
  ["plan", "1. 기획", renderPlan],
  ["script", "2. 대본", renderScript],
  ["audio", "3. 음성", renderAudio],
  ["subtitles", "4. 자막", renderSubtitles],
  ["sources", "5. 영상소스", renderSources],
  ["export", "6. 게시·내보내기", renderExport],
];

let route = { page: "projects", pid: null };

function parseHash() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "p" && parts[1]) return { page: parts[2] || "plan", pid: parts[1] };
  if (parts[0] === "settings") return { page: "settings", pid: null };
  if (parts[0] === "channel") return { page: "channel", pid: null };
  return { page: "projects", pid: null };
}

export function go(page, pid = state.project?.id) {
  location.hash = page === "projects" ? "#/" : page === "settings" ? "#/settings" : page === "channel" ? "#/channel" : `#/p/${pid}/${page}`;
}
window.__go = go;

let lastHash = location.hash;
async function onRoute() {
  const next = parseHash();
  // 화면 이동·프로젝트 전환 전에 미저장 변경을 저장. 실패하면 이동할지 묻는다.
  if (state.project && (state.dirty || state.saving)) {
    const ok = await flush();
    if (!ok && !(await confirmLeave())) {
      history.replaceState(null, "", lastHash);
      return;
    }
  }
  lastHash = location.hash;
  route = next;
  try {
    if (route.pid && (!state.project || state.project.id !== route.pid)) await openProject(route.pid);
    if (!route.pid && (route.page === "projects" || route.page === "channel")) closeProject();
  } catch (e) {
    showError(e);
    location.hash = "#/";
    return;
  }
  render();
}

function renderSidebar() {
  const sb = clear(document.getElementById("sidebar"));
  sb.append(h("div", { class: "brand", onclick: () => go("projects") }, h("b", {}, "JP 숏폼 작업실"), h("small", {}, "기획 → 대본 → 음성 → 자막 → 소스 → CapCut")));
  sb.append(h("button", { class: `nav ${route.page === "projects" ? "active" : ""}`, onclick: () => go("projects") }, "📁 프로젝트 목록"));
  sb.append(h("button", { class: `nav ${route.page === "channel" ? "active" : ""}`, onclick: () => go("channel") }, "📺 채널 · 주제"));
  if (state.project) {
    sb.append(h("div", { class: "proj-name", title: state.project.name }, state.project.name));
    const steps = state.status?.steps || {};
    for (const [key, label] of STEPS) {
      sb.append(h("button", { class: `nav step ${route.page === key ? "active" : ""}`, onclick: () => go(key) },
        h("span", {}, label), badge(steps[key]?.state || "todo")));
    }
  }
  sb.append(h("div", { class: "spacer" }));
  sb.append(h("button", { class: `nav ${route.page === "settings" ? "active" : ""}`, onclick: () => go("settings") }, "⚙ 설정 (AI 연결·키)"));
  const ai = state.settings?.settings?.ai;
  const ready = ai && ai.base_url && ai.model && (ai.auth === "none" || state.settings?.secrets?.ai_api_key);
  sb.append(h("div", { class: `conn ${ready ? "ok" : "no"}` }, ready ? "AI 연결 설정됨" : "AI 연결 필요"));
}

function renderTopbar() {
  const tb = clear(document.getElementById("topbar"));
  if (!state.project) return;
  const st = state.status;
  const saveText = state.saving ? "저장 중…" : state.dirty ? "변경됨(곧 저장)" : state.lastSavedAt ? `저장됨 ${state.lastSavedAt.toLocaleTimeString("ko-KR")}` : "저장됨";
  tb.append(
    h("div", { class: "next" }, h("span", { class: "next-label" }, "다음 할 일"), h("span", {}, st?.next || "")),
    h("div", { class: `savestate ${state.dirty ? "dirty" : ""}` }, saveText),
  );
  const cur = st?.steps?.[route.page];
  if (cur && cur.notes?.length && cur.state !== "done") {
    tb.append(h("div", { class: `stepnotes ${cur.state}` }, cur.notes.map((n) => h("div", {}, "• " + n))));
  }
}

let renderToken = 0;
async function render() {
  renderSidebar();
  renderTopbar();
  const page = clear(document.getElementById("page"));
  const token = ++renderToken;
  try {
    if (route.page === "projects") await renderProjects(page);
    else if (route.page === "settings") await renderSettings(page, loadSettings);
    else if (route.page === "channel") await renderChannel(page);
    else {
      const step = STEPS.find((s) => s[0] === route.page);
      if (step && state.project) await step[2](page);
    }
  } catch (e) {
    if (token === renderToken) { page.append(h("div", { class: "card error" }, e.message || String(e))); showError(e); }
  }
}
window.__rerender = render;

onState((what) => {
  if (["saving", "saved", "dirty", "status", "save-error"].includes(what)) { renderTopbar(); renderSidebar(); }
  if (what === "project") render();
});

// 작업 진행 패널
const jobsBox = document.getElementById("jobs");
const jobEls = new Map();
onJobUpdate((job) => {
  let el = jobEls.get(job.id);
  if (!el) {
    el = h("div", { class: "job" });
    jobEls.set(job.id, el);
    jobsBox.append(el);
  }
  clear(el);
  const pct = Math.round((job.progress || 0) * 100);
  el.className = `job ${job.status}`;
  el.append(...[
    h("div", { class: "job-head" }, h("b", {}, job.label), h("span", {}, job.status === "running" ? `${job.elapsed ?? 0}초` : { done: "완료", error: "실패", cancelled: "취소됨" }[job.status] || "")),
    h("div", { class: "bar" }, h("div", { style: { width: `${job.status === "running" ? Math.max(5, pct) : 100}%` } })),
    h("div", { class: "job-msg" }, job.status === "error" ? job.error : job.message || ""),
    job.status === "running" ? h("button", { class: "small", onclick: () => cancelJob(job.id).catch(showError) }, "취소") : null,
  ].filter(Boolean));
  if (job.status !== "running") setTimeout(() => { el.remove(); jobEls.delete(job.id); }, job.status === "error" ? 8000 : 2500);
});

export async function loadSettings() {
  try { state.settings = await api.get("/api/settings"); } catch (e) { showError(e); }
  renderSidebar();
}

window.addEventListener("hashchange", onRoute);
await loadSettings();
await onRoute();
