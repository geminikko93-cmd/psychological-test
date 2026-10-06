// 현재 프로젝트 상태와 자동 저장.
//
// 저장 규칙
// - 화면이 project 객체를 고치고 changed()를 부르면 localVersion이 1 올라간다.
// - 저장은 한 번에 하나씩(saveLoop). 요청마다 그 순간의 스냅샷과 버전 번호를 함께 보낸다.
// - 서버가 받은 버전(savedVersion)이 localVersion보다 작으면 '미저장'을 유지하고 곧바로 다시 저장한다.
//   → 저장 요청이 진행 중일 때 추가로 고친 내용도 반드시 이어서 저장된다.
// - 409(다른 창/작업이 먼저 저장)는 저장 루프가 끝난 뒤 처리한다.
//   → 충돌 처리에서 '서버 내용 다시 불러오기'가 진행 중인 저장을 다시 기다리며 멈추지 않는다.
import { api } from "./api.js";
import { h, modal, toast, showError } from "./ui.js";

export const state = {
  project: null,
  status: null,
  saving: false,
  dirty: false,
  lastSavedAt: null,
  saveError: null,
  settings: null,
  secrets: null,
  localVersion: 0,
  savedVersion: 0,
};

const listeners = new Set();
export const onState = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
const emit = (what) => listeners.forEach((f) => f(what));

let timer = null;
let loop = null;          // 진행 중인 저장 루프 Promise
let conflictPending = false;

function setLoaded(r) {
  state.project = r.project;
  state.status = r.status;
  state.localVersion = 0;
  state.savedVersion = 0;
  state.dirty = false;
  state.saveError = null;
}

export async function openProject(pid) {
  if (state.project && state.project.id !== pid) {
    const ok = await flush();
    if (!ok && !(await confirmLeave())) throw new Error("이동을 취소했습니다.");
  }
  const r = await api.get(`/api/projects/${pid}`);
  setLoaded(r);
  try { localStorage.setItem("jpss:last", pid); } catch { /* 무시 */ }
  emit("project");
}

// 충돌 해결용: 진행 중인 저장을 기다리지 않고 서버 내용으로 바꾼다.
async function reloadFromServer(pid) {
  clearTimeout(timer);
  const r = await api.get(`/api/projects/${pid}`);
  setLoaded(r);
  emit("project");
}

export function closeProject() {
  state.project = null;
  state.status = null;
  state.dirty = false;
  emit("project");
}

export function changed(immediate = false) {
  if (!state.project) return;
  state.localVersion += 1;
  state.dirty = true;
  emit("dirty");
  clearTimeout(timer);
  timer = setTimeout(() => { save().catch(() => {}); }, immediate ? 0 : 800);
}

// 저장 요청. 이미 루프가 돌고 있으면 그 루프가 최신 버전까지 이어서 저장한다.
export function save(force = false) {
  if (!state.project) return Promise.resolve(true);
  clearTimeout(timer);
  if (loop) return loop;
  loop = saveLoop(force).finally(() => { loop = null; });
  return loop;
}

async function saveLoop(force) {
  const p = state.project;
  state.saving = true;
  state.saveError = null;
  emit("saving");
  let conflict = false;
  try {
    // 로컬 변경 번호가 서버 반영 번호보다 크거나, 강제 저장이면 계속 저장
    while (state.project === p && (force || state.localVersion > state.savedVersion)) {
      const version = state.localVersion;
      const snapshot = JSON.parse(JSON.stringify(p)); // 이 요청이 보낼 내용 고정
      let r;
      try {
        r = await api.put(`/api/projects/${p.id}`, { project: snapshot, base_rev: p.rev, force });
      } catch (e) {
        if (e.status === 409) { conflict = true; break; }
        throw e;
      }
      if (state.project !== p) break; // 저장 중 다른 프로젝트로 바뀜
      p.rev = r.project.rev;
      p.updated_at = r.project.updated_at;
      state.status = r.status;
      state.savedVersion = Math.max(state.savedVersion, version);
      state.lastSavedAt = new Date();
      force = false;
    }
    if (state.project === p) state.dirty = state.localVersion > state.savedVersion;
    return !state.dirty;
  } catch (e) {
    state.saveError = e;
    state.dirty = true;
    showError(e);
    return false;
  } finally {
    state.saving = false;
    emit(state.saveError ? "save-error" : "saved");
    if (conflict) {
      // 루프가 끝난 뒤(loop 해제 후) 처리해야 다시 불러오기가 자기 자신을 기다리지 않는다
      setTimeout(() => handleConflict(p), 0);
    }
  }
}

async function handleConflict(p) {
  if (conflictPending || state.project !== p) return;
  conflictPending = true;
  try {
    const choice = await modal("저장 충돌",
      h("div", {}, h("p", {}, "다른 창이나 작업에서 이 프로젝트가 먼저 저장되었습니다."),
        h("p", {}, "서버 내용을 다시 불러오면 이 창에서 저장되지 않은 수정은 버려집니다(브라우저에 임시 보관)."),
        h("p", {}, "현재 화면으로 덮어쓰면, 서버에 있던 내용은 프로젝트의 backups 폴더에 백업됩니다.")),
      [{ label: "서버 내용 다시 불러오기", value: "reload" }, { label: "현재 화면으로 덮어쓰기", value: "force", kind: "danger" }]);
    if (choice === "force") {
      const ok = await save(true);
      if (ok) toast("현재 화면 내용으로 저장했습니다(이전 서버 내용은 백업됨).", "ok");
    } else {
      try { localStorage.setItem(`jpss:conflict:${p.id}`, JSON.stringify({ at: new Date().toISOString(), project: p })); } catch { /* 무시 */ }
      await reloadFromServer(p.id);
      toast("서버의 최신 내용을 다시 불러왔습니다.", "info");
    }
  } catch (e) {
    showError(e);
  } finally {
    conflictPending = false;
  }
}

// 미저장 변경을 저장한다. 성공하면 true. (진행 중 저장 + 그 이후 변경까지 포함)
export async function flush() {
  if (!state.project) return true;
  if (!state.dirty && !loop) return true;
  return await save();
}

async function confirmLeave() {
  const r = await modal("저장되지 않은 변경",
    h("p", {}, "마지막 수정 내용을 저장하지 못했습니다. 이동하면 이 수정은 사라집니다."),
    [{ label: "머무르기", value: false, kind: "primary" }, { label: "버리고 이동", value: true, kind: "danger" }]);
  return r === true;
}
export { confirmLeave };

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

// 새로고침·창 닫기 보호: 미저장이거나 저장 중이면 브라우저 확인 창을 띄운다.
// (떠나는 순간 몰래 저장 요청을 보내면, 사용자가 '머무르기'를 고른 경우 리비전이 어긋나므로 보내지 않는다)
window.addEventListener("beforeunload", (e) => {
  if (!state.project || (!state.dirty && !loop)) return;
  e.preventDefault();
  e.returnValue = "";
});
