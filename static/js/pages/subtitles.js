import { api, runJob, mediaUrl } from "../api.js";
import { state, changed, flush } from "../state.js";
import { h, clear, section, modal, confirmBox, toast, empty, fmtSec, fmtTime, showError } from "../ui.js";
import { go, rerender } from "../nav.js";

const CONF = { high: ["높음", "ok"], mid: ["보통", "mid"], low: ["낮음", "low"] };
const rid = () => `c_${Math.random().toString(16).slice(2, 10)}`;

function srtTime(t) {
  const ms = Math.round(Math.max(0, t) * 1000);
  const hh = Math.floor(ms / 3600000), mm = Math.floor((ms % 3600000) / 60000), ss = Math.floor((ms % 60000) / 1000), r = ms % 1000;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")},${String(r).padStart(3, "0")}`;
}
function buildSrt(cues) {
  return cues.slice().sort((a, b) => a.start - b.start).map((c, i) =>
    `${i + 1}\r\n${srtTime(c.start)} --> ${srtTime(c.end)}\r\n${c.text.split("\n").filter((x) => x.trim()).join("\r\n")}\r\n`).join("\r\n");
}

export async function renderSubtitles(page) {
  const p = state.project;
  const subs = p.subtitles;
  const proc = p.audio?.processed;
  page.append(h("div", { class: "page-head" }, h("h1", {}, "4. 자막"),
    h("div", { class: "muted" }, "최종(무음 정리된) 음성을 기준으로 자막 시간을 맞춥니다. 자막 문구는 대본의 '화면 표시' 일본어를 그대로 씁니다(음성 인식 결과로 바꾸지 않음).")));
  if (!proc) {
    page.append(section(null, empty("최종 음성이 없습니다. 음성 단계에서 무음 정리를 적용하세요."), h("button", { onclick: () => go("audio") }, "← 음성으로")));
    return;
  }
  const st = state.status?.steps?.subtitles;
  if (st?.state === "stale") page.append(h("div", { class: "banner stale" }, st.notes.join(" ")));

  let ws = null;
  try { ws = await api.get("/api/whisper/status"); } catch { /* 무시 */ }
  const opt = { asr: false };
  const methodBox = h("div", { class: "methods" },
    h("label", { class: "radio" }, h("input", { type: "radio", name: "m", checked: true, onchange: () => (opt.asr = false) }),
      h("div", {}, h("b", {}, "무음 경계 정렬 (기본)"), h("div", { class: "muted small" }, "실제 음성의 쉼 위치와 문장별 예상 길이를 맞춰 문장 경계를 찾습니다. 추가 설치 없음, 내 PC에서 처리."))),
    h("label", { class: "radio" }, h("input", { type: "radio", name: "m", disabled: !ws?.installed, onchange: () => (opt.asr = true) }),
      h("div", {}, h("b", {}, "음성 인식 보조 정렬 (선택)"),
        h("div", { class: "muted small" }, ws?.installed
          ? `faster-whisper(${ws.model_size} 모델, ${ws.sizes?.[ws.model_size] || ""}) 사용. 음성은 내 PC에서만 처리합니다. ${ws.model_downloaded ? "모델 준비됨." : "처음 한 번 Hugging Face에서 모델을 내려받습니다(인터넷 필요)."} 쉼이 거의 없는 음성에서 더 정확합니다.`
          : "설치되어 있지 않습니다. install_whisper.bat 실행 시 사용 가능(약 100MB 프로그램 + 모델 150MB~1.5GB)."))));

  page.append(section("자막 시간 맞추기", methodBox,
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: () => runAlign(opt.asr) }, subs.cues?.length ? "다시 맞추기" : "자막 생성"),
      subs.generated_at ? h("span", { class: "muted small" }, `${subs.method} · ${fmtTime(subs.generated_at)} · 음성 버전 ${subs.based_on_audio_version}`) : null)));

  if (!subs.cues?.length) return;

  // 미리보기 플레이어 + 현재 자막 표시
  const audio = h("audio", { controls: true, preload: "auto", src: mediaUrl(p.id, proc.file, proc.sha256 || proc.created_at) });
  const preview = h("div", { class: "phone" }, h("div", { class: "phone-sub jp" }, ""));
  const rowsById = new Map();
  audio.addEventListener("timeupdate", () => {
    const t = audio.currentTime;
    const c = subs.cues.find((x) => x.start <= t && t < x.end);
    preview.firstChild.textContent = c ? c.text : "";
    rowsById.forEach((row, id) => row.classList.toggle("playing", c?.id === id));
  });

  const issuesBox = h("div", {});
  const tbl = h("div", { class: "cues" });
  const cues = subs.cues.sort((a, b) => a.start - b.start);
  let issueMap = new Map();

  const markChanged = () => { changed(); scheduleCheck(); };
  let chkTimer = null;
  const scheduleCheck = () => { clearTimeout(chkTimer); chkTimer = setTimeout(runCheck, 600); };
  const runCheck = async () => {
    try {
      const r = await api.post(`/api/projects/${p.id}/subtitles/check`, { cues });
      issueMap = new Map();
      for (const i of r.issues) issueMap.set(i.cue_id, [...(issueMap.get(i.cue_id) || []), i]);
      clear(issuesBox);
      const err = r.issues.filter((i) => i.level === "error").length, warn = r.issues.length - err;
      issuesBox.append(h("div", { class: `note ${err ? "warn" : warn ? "" : "ok"}` },
        err || warn ? `오류 ${err}개 · 확인 권장 ${warn}개 (표에서 노란/빨간 줄)` : "자막 점검 통과: 겹침·길이 초과·너무 짧음 없음"));
      rowsById.forEach((row, id) => {
        const list = issueMap.get(id) || [];
        row.classList.toggle("err", list.some((i) => i.level === "error"));
        row.classList.toggle("warnrow", list.length > 0 && !list.some((i) => i.level === "error"));
        const box = row.querySelector(".cue-issues");
        clear(box).append(...list.map((i) => h("div", { class: `iss ${i.level}` }, i.message)));
      });
    } catch (e) { showError(e); }
  };

  const renderRows = () => {
    clear(tbl);
    rowsById.clear();
    cues.forEach((c, i) => {
      const startIn = h("input", { type: "number", step: "0.01", class: "num", value: c.start.toFixed(2) });
      const endIn = h("input", { type: "number", step: "0.01", class: "num", value: c.end.toFixed(2) });
      const ta = h("textarea", { class: "jp cue-text", rows: Math.max(2, c.text.split("\n").length) }, c.text);
      startIn.addEventListener("change", () => { c.start = Math.max(0, Number(startIn.value)); c.manual_time = true; markChanged(); });
      endIn.addEventListener("change", () => { c.end = Math.max(0, Number(endIn.value)); c.manual_time = true; markChanged(); });
      ta.addEventListener("input", () => { c.text = ta.value; c.manual_text = true; markChanged(); });
      const nudge = (which, d) => { c[which] = Math.max(0, +(c[which] + d).toFixed(3)); c.manual_time = true; (which === "start" ? startIn : endIn).value = c[which].toFixed(2); markChanged(); };
      const [cl, cc] = CONF[c.conf] || ["-", "low"];
      const row = h("div", { class: "cue" },
        h("div", { class: "cue-idx" }, i + 1, h("button", { class: "small", title: "이 자막 구간 재생", onclick: () => { audio.currentTime = c.start; audio.play(); const stop = () => { if (audio.currentTime >= c.end) { audio.pause(); audio.removeEventListener("timeupdate", stop); } }; audio.addEventListener("timeupdate", stop); } }, "▶")),
        h("div", { class: "cue-times" },
          h("div", { class: "row tight" }, "시작", startIn, h("button", { class: "tiny", onclick: () => nudge("start", -0.05) }, "-"), h("button", { class: "tiny", onclick: () => nudge("start", 0.05) }, "+")),
          h("div", { class: "row tight" }, "종료", endIn, h("button", { class: "tiny", onclick: () => nudge("end", -0.05) }, "-"), h("button", { class: "tiny", onclick: () => nudge("end", 0.05) }, "+")),
          h("div", { class: "muted small" }, `${(c.end - c.start).toFixed(2)}초`, " · 신뢰도 ", h("span", { class: `conf ${cc}` }, cl), c.manual_time ? " · 시간 수동" : "", c.manual_text ? " · 문구 수동" : "")),
        h("div", { class: "cue-body" }, ta, c.note ? h("div", { class: "muted small" }, c.note) : null, h("div", { class: "cue-issues" })),
        h("div", { class: "cue-actions" },
          h("button", { class: "small", title: "커서 위치에서 자막 나누기", onclick: () => splitCue(i, ta.selectionStart) }, "나누기"),
          h("button", { class: "small", title: "다음 자막과 합치기", disabled: i === cues.length - 1, onclick: () => mergeCue(i) }, "합치기"),
          h("button", { class: "small", title: "문절 기준 자동 줄바꿈", onclick: async () => {
            const r = await api.post("/api/jp/tools", { text: c.text.replace(/\n/g, "") });
            c.text = r.wrapped; ta.value = c.text; c.manual_text = true; markChanged();
          } }, "줄바꿈"),
          h("button", { class: "small danger", onclick: async () => {
            if (!(await confirmBox("자막 삭제", "이 자막을 삭제합니다.", "삭제", true))) return;
            cues.splice(i, 1); changed(true); renderRows(); runCheck();
          } }, "삭제")));
      rowsById.set(c.id, row);
      tbl.append(row);
    });
  };

  const splitCue = (i, pos) => {
    const c = cues[i];
    const flat = c.text;
    if (!pos || pos <= 0 || pos >= flat.length) { toast("자막 문구 안에서 나눌 위치에 커서를 두고 누르세요.", "info"); return; }
    const a = flat.slice(0, pos).replace(/\n+$/, ""), b = flat.slice(pos).replace(/^\n+/, "");
    const ratio = a.replace(/\n/g, "").length / Math.max(1, (a + b).replace(/\n/g, "").length);
    const mid = +(c.start + (c.end - c.start) * ratio).toFixed(3);
    const c2 = { ...c, id: rid(), part: (c.part || 0) + 0.5, text: b, start: mid, manual_text: true, manual_time: true, conf: "low", note: "직접 나눈 자막입니다. 나눈 시간은 글자 비율 추정이므로 재생해서 맞추세요." };
    Object.assign(c, { text: a, end: mid, manual_text: true, manual_time: true });
    cues.splice(i + 1, 0, c2);
    changed(true); renderRows(); runCheck();
  };
  const mergeCue = (i) => {
    const c = cues[i], n = cues[i + 1];
    Object.assign(c, { text: (c.text.replace(/\n/g, "") + n.text.replace(/\n/g, "")), end: n.end, manual_text: true });
    cues.splice(i + 1, 1);
    changed(true); renderRows(); runCheck();
  };

  page.append(section("자막 편집",
    h("div", { class: "sub-preview" }, h("div", {}, audio, h("div", { class: "muted small" }, "재생하면 오른쪽 화면에 현재 자막이 표시되고, 표에서 해당 줄이 강조됩니다.")), preview),
    issuesBox, tbl,
    h("div", { class: "row" },
      h("button", { onclick: () => {
        const blob = new Blob([buildSrt(cues)], { type: "application/x-subrip;charset=utf-8" });
        const a = h("a", { href: URL.createObjectURL(blob), download: `${p.name || "subtitles"}_ja.srt` });
        document.body.append(a); a.click(); a.remove();
      } }, "SRT만 따로 저장"),
      h("button", { onclick: () => modal("SRT 미리보기", h("textarea", { class: "full jp", rows: 18, readonly: true }, buildSrt(cues)), [{ label: "닫기", value: null }], { wide: true }) }, "SRT 미리보기"))));
  renderRows();
  runCheck();

  if (subs.previous?.length) {
    page.append(section("이전 자막(덮어쓰기 전 보관본)", subs.previous.slice().reverse().map((pv, i) => h("div", { class: "row between hist" },
      h("span", {}, `${fmtTime(pv.saved_at)} · ${pv.reason} · ${pv.cues.length}개 · 음성 버전 ${pv.audio_version ?? "-"}`),
      h("button", { class: "small", onclick: async () => {
        if (!(await confirmBox("이전 자막 복원", "현재 자막을 보관하고 이 자막으로 되돌립니다. 음성 버전이 다르면 시간이 맞지 않을 수 있습니다.", "복원"))) return;
        const idx = subs.previous.length - 1 - i;
        const pick = subs.previous.splice(idx, 1)[0];
        keepPrevious("복원 전 보관");
        subs.cues = pick.cues; subs.based_on_audio_version = pick.audio_version; subs.based_on_display_hash = pick.display_hash;
        changed(true); rerender();
      } }, "복원")))));
  }
  page.append(h("div", { class: "row end" }, h("button", { class: "primary", onclick: () => go("sources") }, "영상소스 단계로 →")));
}

function keepPrevious(reason) {
  const subs = state.project.subtitles;
  if (!subs.cues?.length) return;
  subs.previous = [...(subs.previous || []), { saved_at: new Date().toISOString(), reason, cues: JSON.parse(JSON.stringify(subs.cues)),
    audio_version: subs.based_on_audio_version, display_hash: subs.based_on_display_hash }].slice(-5);
}

async function runAlign(useAsr) {
  const p = state.project;
  await flush();
  let r;
  try {
    r = await runJob(api.post(`/api/projects/${p.id}/subtitles/align`, { use_asr: useAsr }));
  } catch (e) { if (e.status !== 499) showError(e); return; }
  const subs = p.subtitles;
  let cues = r.cues;
  if (subs.cues?.length) {
    const manual = r.diff.manual;
    const body = h("div", {},
      h("p", {}, `새로 맞춘 자막 ${r.cues.length}개 (${r.method}). 변경 ${r.diff.changes.length}건.`),
      h("p", { class: "muted small" }, "현재 자막은 '이전 자막' 목록에 보관되어 언제든 복원할 수 있습니다."),
      manual.length ? h("div", {}, h("p", {}, h("b", {}, `직접 고친 자막 ${manual.length}개가 있습니다.`)),
        h("table", { class: "tbl" }, h("tr", {}, h("th", {}, "현재(수동)"), h("th", {}, "새 결과")),
          manual.map((m) => h("tr", {}, h("td", { class: "jp" }, m.old_text, h("div", { class: "muted small" }, `${fmtSec(m.old_start)}~${fmtSec(m.old_end)}`)),
            h("td", { class: "jp" }, m.new_text, h("div", { class: "muted small" }, `${fmtSec(m.new_start)}~${fmtSec(m.new_end)}`)))))) : null);
    const buttons = [{ label: "취소", value: null }];
    if (manual.some((m) => m.manual_text)) buttons.push({ label: "수동 문구는 유지하고 시간만 새로", value: "merge", primary: true });
    buttons.push({ label: "새 결과로 모두 바꾸기", value: "new", primary: !manual.length });
    const choice = await modal("자막 덮어쓰기 확인", body, buttons, { wide: true });
    if (!choice) return;
    if (choice === "merge") cues = r.diff.merged_keep_manual_text;
    keepPrevious(`다시 맞추기 전 (${choice === "merge" ? "수동 문구 유지" : "전체 교체"})`);
  }
  Object.assign(subs, { cues, method: r.method, generated_at: new Date().toISOString(), based_on_audio_version: r.audio_version, based_on_display_hash: r.display_hash });
  changed(true);
  const low = cues.filter((c) => c.conf === "low").length;
  toast(`자막 ${cues.length}개를 맞췄습니다.`, "ok", low ? `신뢰도 낮은 자막 ${low}개는 재생해서 확인하세요.` : "");
  rerender();
}
