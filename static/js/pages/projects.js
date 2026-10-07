import { api } from "../api.js";
import { openProject } from "../state.js";
import { h, section, toast, showError, promptBox, confirmBox, modal, fmtTime, badge, empty } from "../ui.js";
import { go, rerender } from "../nav.js";

const STEP_NAMES = { plan: "기획", script: "대본", audio: "음성", subtitles: "자막", visual: "화풍", sources: "소스", review: "검수", export: "내보내기" };

export async function renderProjects(page) {
  const { projects } = await api.get("/api/projects");
  const fileInput = h("input", { type: "file", accept: ".zip", style: { display: "none" } });
  fileInput.addEventListener("change", async () => {
    const f = fileInput.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await api.upload("/api/projects/import", fd);
      toast("프로젝트를 가져왔습니다.", "ok");
      go("plan", r.project.id);
    } catch (e) { showError(e); }
  });

  page.append(
    h("div", { class: "page-head" }, h("h1", {}, "프로젝트"),
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: async () => {
          const name = await promptBox("새 프로젝트", "프로젝트 이름(나중에 바꿀 수 있음)", "");
          if (name === null) return;
          try { const r = await api.post("/api/projects", { name: name || "새 프로젝트" }); go("plan", r.project.id); } catch (e) { showError(e); }
        } }, "+ 새 프로젝트"),
        h("button", { class: "primary", onclick: () => go("channel") }, "📺 주제 목록에서 시작"),
        h("button", { onclick: () => fileInput.click() }, "ZIP에서 프로젝트 가져오기"),
        h("button", { onclick: async () => {
          try { const r = await api.post("/api/examples/import", {}); toast("예제 프로젝트를 만들었습니다.", "ok"); go("plan", r.project.id); } catch (e) { showError(e); }
        } }, "예제 프로젝트 열기"),
      h("button", { title: "화풍·반복 등장 요소·소재 재사용까지 들어 있는 일관성 관리 예제(음성 없음)", onclick: async () => {
          try { const r = await api.post("/api/examples/small-room", {}); toast("'작은 방' 예제를 만들었습니다.", "ok"); go("visual", r.project.id); } catch (e) { showError(e); }
        } }, "예제: 작은 방(일관성 관리)"),
        fileInput)),
  );

  if (!projects.length) {
    page.append(section(null, empty("아직 프로젝트가 없습니다. [+ 새 프로젝트]로 시작하거나 [예제 프로젝트 열기]로 흐름을 둘러보세요.")));
    return;
  }
  const list = h("div", { class: "proj-list" });
  for (const p of projects) {
    const steps = p.steps || {};
    list.append(h("div", { class: "proj-card" },
      h("div", { class: "proj-main", onclick: () => go("plan", p.id) },
        h("div", { class: "proj-title" }, p.name || "(이름 없음)"),
        h("div", { class: "muted" }, p.topic || ""),
        h("div", { class: "proj-steps" }, Object.entries(STEP_NAMES).map(([k, n]) => h("span", { class: "mini" }, n, " ", badge(steps[k])))),
        h("div", { class: "muted small" }, `수정: ${fmtTime(p.updated_at)}  ·  ${p.next || ""}`)),
      h("div", { class: "proj-actions" },
        h("button", { class: "small", onclick: () => go("plan", p.id) }, "열기"),
        h("button", { class: "small", onclick: async () => {
          const mode = await modal("프로젝트 복제", h("p", {}, "어떻게 복제할까요?"), [
            { label: "취소", value: null },
            { label: "기획·대본만 (새 영상용)", value: "plan" },
            { label: "전체(음성·자막·소스 포함)", value: "all", primary: true }]);
          if (!mode) return;
          try { await api.post(`/api/projects/${p.id}/duplicate`, { mode }); toast("복제했습니다.", "ok"); rerender(); } catch (e) { showError(e); }
        } }, "복제"),
        h("button", { class: "small", onclick: async () => {
          try { const r = await api.post(`/api/projects/${p.id}/backup`); toast(`백업 ZIP을 만들었습니다: ${r.file}`, "ok", r.folder); } catch (e) { showError(e); }
        } }, "백업"),
        h("button", { class: "small danger", onclick: async () => {
          if (!(await confirmBox("프로젝트 삭제", `'${p.name}' 프로젝트를 휴지통 폴더로 옮깁니다.\n(완전히 지우지 않으며, 데이터 폴더의 trash에서 되살릴 수 있습니다.)`, "휴지통으로 이동", true))) return;
          try { const r = await api.del(`/api/projects/${p.id}`); toast(r.message, "ok"); rerender(); } catch (e) { showError(e); }
        } }, "삭제"))));
  }
  page.append(list);
}
