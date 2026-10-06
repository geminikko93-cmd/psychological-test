// 현재 프로젝트 상태와 자동 저장. 화면이 project 객체를 직접 고치고 changed()를 부르면
// 0.8초 뒤 서버에 저장한다. 서버 리비전이 다르면(다른 창/작업) 덮어쓰기 전에 묻는다.
import { api } from "./api.js";
import { h, modal, toast, showError } from "./ui.js";

export const state = {
  project: null,
  status: null,
  saving: false,
  dirty: false,
  lastSavedAt: null,
  settings: null,
  secrets: null,
};

const listeners = new Set();
export const onState = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
const emit = (what) => listeners.forEach((f) => f(what));

let timer = null;
let inflight = null;

export async function openProject(pid) {
  await flush();
  const r = await api.get(`/api/projects/${pid}`);
  state.project = r.project;
  state.status = r.status;
  state.dirty = false;
  localStorage.setItem("jpss:last", pid);
  emit("project");
}

export function closeProject() {
  state.project = null;
  state.status = null;
  emit("project");
}

export function changed(immediate = false) {
  if (!state.project) return;
  state.dirty = true;
  emit("dirty");
  clearTimeout(timer);
  timer = setTimeout(() => save().catch(showError), immediate ? 0 : 800);
}

export async function save(force = false) {
  if (!state.project) return;
  if (inflight) { await inflight; if (!state.dirty && !force) return; }
  clearTimeout(timer);
  const p = state.project;
  state.saving = true;
  emit("saving");
  inflight = (async () => {
    try {
      const r = await api.put(`/api/projects/${p.id}`, { project: p, base_rev: p.rev, force });
      // 저장 중 사용자가 더 고쳤을 수 있으므로 rev·시간만 반영
      p.rev = r.project.rev;
      p.updated_at = r.project.updated_at;
      state.status = r.status;
      state.dirty = false;
      state.lastSavedAt = new Date();
      emit("saved");
    } catch (e) {
      if (e.status === 409) {
        await handleConflict();
      } else {
        emit("save-error");
        throw e;
      }
    } finally {
      state.saving = false;
      inflight = null;
    }
  })();
  return inflight;
}

async function handleConflict() {
  const choice = await modal("저장 충돌",
    h("div", {}, h("p", {}, "다른 창이나 작업에서 이 프로젝트가 먼저 저장되었습니다."),
      h("p", {}, "현재 화면 내용으로 덮어쓰면, 서버에 있던 내용은 프로젝트의 backups 폴더에 백업됩니다.")),
    [{ label: "서버 내용 다시 불러오기", value: "reload" }, { label: "현재 화면으로 덮어쓰기", value: "force", kind: "danger" }]);
  if (choice === "force") {
    state.saving = false;
    inflight = null;
    await save(true);
    toast("현재 화면 내용으로 저장했습니다(이전 내용은 백업됨).", "ok");
  } else {
    await openProject(state.project.id);
    toast("서버의 최신 내용을 다시 불러왔습니다.", "info");
  }
}

export async function flush() {
  if (state.project && state.dirty) await save();
  else if (inflight) await inflight;
}

export async function refreshStatus() {
  if (!state.project) return;
  const r = await api.get(`/api/projects/${state.project.id}`);
  if (r.project.rev === state.project.rev) { state.status = r.status; emit("status"); }
}

export function notify(what) { emit(what); }

export function logAi(entry) {
  if (!entry || !state.project) return;
  state.project.ai_log = [...(state.project.ai_log || []), entry].slice(-100);
}

window.addEventListener("beforeunload", (e) => {
  if (state.dirty) { save(); e.preventDefault(); e.returnValue = ""; }
});
