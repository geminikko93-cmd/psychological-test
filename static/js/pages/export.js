import { api, runJob } from "../api.js";
import { state, changed, flush } from "../state.js";
import { h, clear, section, field, bindInput, confirmBox, toast, empty, fmtTime, copyText, showError } from "../ui.js";
import { runAi } from "../aiflow.js";
import { rerender } from "../nav.js";

function creditsText(p) {
  const out = [];
  if (p.typecast?.credit_text?.trim()) out.push(p.typecast.credit_text.trim());
  const seen = new Set();
  for (const it of p.sources?.items || []) {
    if (it.status !== "acquired") continue;
    const t = (it.credit_text || "").trim();
    if (t && !seen.has(t)) { seen.add(t); out.push(t); }
  }
  if (p.publish?.credits_extra?.trim()) out.push(p.publish.credits_extra.trim());
  return out.join("\n");
}

export async function renderExport(page) {
  const p = state.project;
  const pub = p.publish;
  page.append(h("div", { class: "page-head" }, h("h1", {}, "6. 게시 정보 · 내보내기"),
    h("div", { class: "muted" }, "CapCut에서 바로 쓸 수 있도록 최종 음성·SRT·소스·장면표·대본·게시 정보를 한 폴더(및 ZIP)로 묶습니다. API 키와 개인 설정은 포함하지 않습니다.")));

  const draftBox = h("div", {});
  const showDraft = () => {
    clear(draftBox);
    const d = pub.ai_draft;
    if (!d) return;
    draftBox.append(h("div", { class: "draft" },
      h("div", { class: "muted small" }, `AI 초안 · ${fmtTime(d.generated_at)} (적용 버튼을 눌러야 반영됩니다)`),
      h("h4", {}, "제목 후보"),
      (d.titles || []).map((t) => h("div", { class: "row between" }, h("div", {}, h("div", { class: "jp" }, t.ja), h("div", { class: "ko" }, t.ko + (t.note_ko ? ` — ${t.note_ko}` : ""))),
        h("button", { class: "small", onclick: () => { pub.title = t.ja; changed(); rerender(); } }, "제목으로"))),
      h("h4", {}, "설명문"), h("div", { class: "jp pre" }, d.description_ja), h("div", { class: "ko pre" }, d.description_ko),
      h("button", { class: "small", onclick: () => { pub.description = d.description_ja; changed(); rerender(); } }, "설명문으로"),
      d.hashtags?.length ? h("div", {}, h("h4", {}, "해시태그"), d.hashtags.join(" "), " ", h("button", { class: "small", onclick: () => { pub.hashtags = d.hashtags.join(" "); changed(); rerender(); } }, "사용")) : null,
      d.notes_ko ? h("div", { class: "note" }, d.notes_ko) : null));
  };
  showDraft();
  const cred = creditsText(p);
  page.append(section("게시 정보",
    h("button", { onclick: async () => {
      const r = await runAi("publish", {});
      if (r) { delete r.log; pub.ai_draft = r; changed(true); showDraft(); }
    } }, pub.ai_draft ? "AI 초안 다시 받기" : "AI로 제목·설명문 초안 받기"),
    draftBox,
    field("게시 제목(일본어)", bindInput(pub, "title", () => changed(), { class: "full jp" })),
    field("설명문(일본어)", bindInput(pub, "description", () => changed(), { multiline: true, rows: 5, class: "full jp" }), "크레딧은 아래 실제 기록에서 자동으로 덧붙습니다. 오락 콘텐츠라면 오락용임을 밝혀 두세요."),
    field("해시태그", bindInput(pub, "hashtags", () => changed())),
    field("추가 크레딧(BGM 등 직접 기록)", bindInput(pub, "credits_extra", () => changed(), { multiline: true, rows: 2 })),
    h("div", { class: "note" }, h("b", {}, "자동 크레딧(타입캐스트·확보한 소스 기록 기준): "), cred ? h("div", { class: "pre" }, cred) : "기록 없음"),
    h("button", { class: "small", onclick: () => copyText((pub.description || "") + (cred ? "\n\n" + cred : ""), "설명문+크레딧") }, "설명문+크레딧 복사")));

  const preBox = h("div", {});
  const resBox = h("div", {});
  const opt = { zip: true };
  const loadPre = async () => {
    clear(preBox).append(h("div", { class: "muted" }, "확인 중…"));
    try {
      await flush();
      const r = await api.get(`/api/projects/${p.id}/export/precheck`);
      clear(preBox);
      if (!r.items.length) preBox.append(h("div", { class: "ok-text" }, "내보내기 전 점검을 모두 통과했습니다."));
      const label = { error: "오류", warn: "확인", stale: "최신 아님" };
      for (const i of r.items) preBox.append(h("div", { class: `check ${i.level}` }, h("b", {}, `[${label[i.level] || i.level}] `), i.message, i.hint ? h("div", { class: "muted small" }, i.hint) : null));
      preBox.dataset.blocking = r.blocking ? "1" : "";
      preBox.dataset.stale = r.has_stale ? "1" : "";
    } catch (e) { clear(preBox); showError(e); }
  };
  const zipChk = h("input", { type: "checkbox", checked: true, onchange: (e) => (opt.zip = e.target.checked) });
  page.append(section("내보내기 전 점검", h("button", { onclick: loadPre }, "다시 점검"), preBox,
    h("div", { class: "row" },
      h("label", { class: "chk" }, zipChk, " ZIP도 함께 만들기"),
      h("button", { class: "primary", onclick: async () => {
        await loadPre();
        if (preBox.dataset.blocking) { toast("오류 항목을 먼저 고쳐 주세요.", "error"); return; }
        let allowStale = false;
        if (preBox.dataset.stale) {
          if (!(await confirmBox("최신이 아닌 항목", "최신이 아닌 자막이 있습니다. 그래도 내보낼까요?\n(권장: 자막 단계에서 다시 맞추기)", "그래도 내보내기", true))) return;
          allowStale = true;
        }
        try {
          const r = await runJob(api.post(`/api/projects/${p.id}/export`, { zip: opt.zip, allow_stale: allowStale }));
          p.last_export = { at: r.exported_at, folder: r.folder, zip: r.zip, version: r.version, basis: r.basis, project_rev: r.project_rev };
          p.export_history = [...(p.export_history || []), p.last_export].slice(-30);
          changed(true);
          showResult(resBox, r);
          toast("내보내기를 마쳤습니다.", "ok", r.folder);
        } catch (e) { if (e.status !== 499) showError(e); }
      } }, "내보내기")),
    resBox));
  loadPre();
  if (p.last_export) resBox.append(h("div", { class: "muted small" }, `마지막 내보내기: ${fmtTime(p.last_export.at)} · ${p.last_export.folder}`),
    h("button", { class: "small", onclick: () => api.post(`/api/projects/${p.id}/open-folder`, { path: p.last_export.folder }).catch(showError) }, "폴더 열기"));
}

function showResult(box, r) {
  const p = state.project;
  clear(box);
  box.append(h("div", { class: `note ${r.verify.all_ok ? "ok" : "warn"}` },
    r.verify.all_ok ? "내보낸 파일을 모두 확인했습니다(존재·재생·SRT 형식·API 키 미포함)." : "일부 파일 확인에 실패했습니다. 아래 표를 보세요."),
    h("div", { class: "row" }, h("code", {}, r.folder), h("button", { class: "small", onclick: () => api.post(`/api/projects/${p.id}/open-folder`, { path: r.folder }).catch(showError) }, "폴더 열기")),
    r.zip ? h("div", { class: "small" }, "ZIP: ", h("code", {}, r.zip)) : null,
    h("table", { class: "tbl" }, h("tr", {}, h("th", {}, "파일"), h("th", {}, "설명"), h("th", {}, "확인")),
      r.verify.files.filter((f) => !f.file.startsWith("09_")).map((f) => {
        const d = r.files.find((x) => x.file === f.file);
        return h("tr", {}, h("td", {}, f.file), h("td", {}, d?.desc || ""), h("td", {}, f.ok ? "✔" : `✖ ${f.note}`));
      })));
}
