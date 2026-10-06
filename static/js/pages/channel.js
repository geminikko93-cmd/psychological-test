// 채널 기본 설정(한 번 입력)과 주제 목록
import { api } from "../api.js";
import { h, clear, section, field, bindInput, select, confirmBox, toast, empty, showError, fmtTime } from "../ui.js";
import { go, rerender } from "../nav.js";

let filter = "unused";

export async function renderChannel(page) {
  const data = await api.get("/api/channel");
  const prof = data.profile || (data.profile = {});
  let timer = null;
  const saveState = h("span", { class: "muted small" }, data.updated_at ? `저장됨 ${fmtTime(data.updated_at)}` : "");
  const save = (now = false) => {
    saveState.textContent = "변경됨(곧 저장)";
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try { const r = await api.put("/api/channel", { channel: data }); data.updated_at = r.updated_at; saveState.textContent = `저장됨 ${fmtTime(r.updated_at)}`; }
      catch (e) { showError(e); saveState.textContent = "저장 실패"; }
    }, now ? 0 : 700);
  };

  page.append(h("div", { class: "page-head" }, h("h1", {}, "채널 · 주제"),
    h("div", { class: "muted" }, "여기에 한 번 적어 두면 새 프로젝트의 기획 칸이 자동으로 채워지고, 모든 AI 요청(기획·대본·검토·게시 정보·Flow 프롬프트)에 함께 전달됩니다.")));

  const f = (label, key, opts = {}, help = "") => field(label, bindInput(prof, key, () => save(), opts), help);
  const rules = { text: (prof.rules || []).join("\n") };
  page.append(section("채널 기본 설정",
    prof.source_note ? h("div", { class: "note" }, prof.source_note) : null,
    h("div", { class: "grid2" },
      f("채널 이름", "channel_name", {}, prof.channel_name_note || ""),
      f("업로드 계획", "upload_plan")),
    f("장르·위치", "genre", { multiline: true, rows: 2 }),
    f("시청자와 상황", "audience", { multiline: true, rows: 2 }),
    f("시청자가 얻는 것(채널의 가치)", "value", { multiline: true, rows: 2 }),
    f("영상 형식", "format", { multiline: true, rows: 2 }),
    h("div", { class: "grid2" },
      f("말투·문장 규칙", "tone", { multiline: true, rows: 2 }),
      f("영상 스타일(Flow 공통 스타일의 기준)", "visual_style", { multiline: true, rows: 2 })),
    field("지켜야 할 원칙(한 줄에 하나)", bindInput(rules, "text", () => { prof.rules = rules.text.split("\n").map((x) => x.trim()).filter(Boolean); save(); }, { multiline: true, rows: 7 })),
    h("div", { class: "grid2" },
      f("설명란 고지 문구(일본어)", "description_notice_ja", { class: "full jp" }),
      f("기본 목표 길이(초)", "target_seconds", { type: "number" })),
    h("div", { class: "row" }, saveState)));

  // 주제 목록
  const listBox = h("div", {});
  const counts = { all: data.topics.length, unused: data.topics.filter((t) => t.status !== "used").length };
  counts.used = counts.all - counts.unused;
  const renderList = () => {
    clear(listBox);
    const rows = data.topics.filter((t) => filter === "all" || (filter === "used" ? t.status === "used" : t.status !== "used"));
    if (!rows.length) { listBox.append(empty(filter === "unused" ? "아직 쓰지 않은 주제가 없습니다." : "주제가 없습니다.")); return; }
    for (const t of rows) {
      const memo = { text: t.memo || "" };
      listBox.append(h("div", { class: `topic ${t.status === "used" ? "used" : ""}` },
        h("div", { class: "topic-main" },
          h("div", {}, h("b", {}, `${t.no ?? ""}. `), h("span", { class: "jp big" }, t.title_ja), " ", h("span", { class: "ko" }, t.title_ko),
            t.status === "used" ? h("span", { class: "badge ok" }, "사용함") : null),
          t.angle_ko ? h("div", { class: "small" }, "관점: ", t.angle_ko) : null,
          t.first_line_ja ? h("div", { class: "small jp" }, "첫 문장 후보: ", t.first_line_ja) : null,
          t.example_choices ? h("div", { class: "small muted" }, "보고서 예시 선택지(참고): ", h("span", { class: "jp" }, t.example_choices)) : null,
          t.caution_ko ? h("div", { class: "small warn-text" }, "주의: ", t.caution_ko) : null,
          t.series_ko ? h("div", { class: "small muted" }, "후속편: ", t.series_ko) : null,
          (t.live_project_ids || []).length ? h("div", { class: "small" }, "프로젝트: ",
            t.live_project_ids.map((pid, i) => h("button", { class: "chip", onclick: () => go("plan", pid) }, `열기 ${i + 1}`))) : null,
          h("details", {}, h("summary", {}, t.memo ? `메모: ${t.memo}` : "메모"),
            bindInput(memo, "text", () => { t.memo = memo.text; save(); }, { multiline: true, rows: 2 }))),
        h("div", { class: "topic-actions" },
          h("button", { class: "primary small", onclick: async () => {
            if (t.status === "used" && !(await confirmBox("이미 사용한 주제", "이 주제로 만든 프로젝트가 있습니다. 새 프로젝트를 하나 더 만들까요?", "만들기"))) return;
            try {
              clearTimeout(timer); await api.put("/api/channel", { channel: data });
              const r = await api.post(`/api/channel/topics/${t.id}/start`);
              toast("기획 칸을 채운 새 프로젝트를 만들었습니다.", "ok"); go("plan", r.project.id);
            } catch (e) { showError(e); }
          } }, "이 주제로 새 프로젝트"),
          h("button", { class: "small", onclick: () => { t.status = t.status === "used" ? "unused" : "used"; save(true); renderList(); } }, t.status === "used" ? "안 씀으로" : "사용함으로"),
          h("button", { class: "small danger", onclick: async () => {
            if (!(await confirmBox("주제 삭제", `'${t.title_ja}' 주제를 목록에서 지웁니다(프로젝트는 지우지 않음).`, "삭제", true))) return;
            data.topics = data.topics.filter((x) => x.id !== t.id); save(true); renderList();
          } }, "삭제"))));
    }
  };
  const add = { text: "" };
  page.append(section("주제 목록",
    h("div", { class: "row" },
      select([["unused", `안 쓴 주제 (${counts.unused})`], ["used", `사용한 주제 (${counts.used})`], ["all", `전체 (${counts.all})`]], filter, (v) => { filter = v; renderList(); }),
      h("span", { class: "muted small" }, "보고서의 예시 선택지는 참고용입니다. 선택지 수·구성은 AI가 주제에 맞게 정합니다.")),
    listBox,
    h("details", {}, h("summary", {}, "주제 추가"),
      field("한 줄에 하나 (일본어|한국어 형식도 가능)", bindInput(add, "text", null, { multiline: true, rows: 4, placeholder: "好きなおにぎりの具は？|좋아하는 주먹밥 속은?" })),
      h("div", { class: "row" },
        h("button", { onclick: async () => {
          if (!add.text.trim()) return;
          try { clearTimeout(timer); await api.put("/api/channel", { channel: data }); const r = await api.post("/api/channel/topics", { text: add.text }); toast(`${r.added}개 추가했습니다.`, "ok"); rerender(); } catch (e) { showError(e); }
        } }, "추가"),
        h("button", { onclick: async () => {
          try { clearTimeout(timer); await api.put("/api/channel", { channel: data }); const r = await api.post("/api/channel/reseed"); toast(r.added ? `보고서 주제 ${r.added}개를 다시 넣었습니다.` : "빠진 보고서 주제가 없습니다.", "ok"); rerender(); } catch (e) { showError(e); }
        } }, "보고서 기본 주제 중 지운 것 다시 불러오기")))));
  renderList();
}
