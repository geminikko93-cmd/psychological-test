import { api, runJob, mediaUrl } from "../api.js";
import { state, changed, flush } from "../state.js";
import { h, clear, section, modal, confirmBox, toast, empty, fmtSec, fmtTime, showError } from "../ui.js";
import { go, rerender } from "../nav.js";

import { flatText, splitCueData, mergeCueData, removedFromCue } from "../subsrc.js";

const CONF = { high: ["높음", "ok"], mid: ["보통", "mid"], low: ["낮음", "low"] };

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
      h("div", {}, h("b", {}, "무음 경계 정렬 (기본, 추정)"),
        h("div", { class: "muted small" }, "실제 음성의 쉼 위치와 문장별 예상 발화 길이를 맞춰 문장 경계를 '추정'합니다. 음성의 내용이 대본과 같은지는 확인하지 않습니다. 추가 설치 없음, 내 PC에서 처리."))),
    h("label", { class: "radio" }, h("input", { type: "radio", name: "m", disabled: !ws?.installed, onchange: () => (opt.asr = true) }),
      h("div", {}, h("b", {}, "음성 인식 보조 정렬 (선택)"),
        h("div", { class: "muted small" }, ws?.installed
          ? `faster-whisper(${ws.model_size} 모델, ${ws.sizes?.[ws.model_size] || ""})로 내 PC에서 인식해 대본과 대조합니다. 인식이 대본과 다른 문장(누락·추가·다르게 읽음)을 따로 보여줍니다. 인식도 틀릴 수 있으니 직접 들어 확인하세요. ${ws.model_downloaded ? "모델 준비됨." : "처음 한 번 Hugging Face에서 모델을 내려받습니다(인터넷 필요)."}`
          : "설치되어 있지 않습니다. install_whisper.bat 실행 시 사용 가능(약 100MB 프로그램 + 모델 150MB~1.5GB)."))));

  page.append(section("자막 시간 맞추기", methodBox,
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: () => runAlign(opt.asr) }, subs.cues?.length ? "다시 맞추기" : "자막 생성"),
      subs.generated_at ? h("span", { class: "muted small" }, `${subs.method} · ${fmtTime(subs.generated_at)} · 음성 버전 ${subs.based_on_audio_version}`) : null)));

  if (subs.content_check) page.append(contentCheckPanel(subs.content_check, p));
  if (!subs.cues?.length) return;

  // 미리보기 플레이어 + 현재 자막 표시
  const audio = h("audio", { controls: true, preload: "auto", src: mediaUrl(p.id, proc.file, proc.sha256 || proc.created_at) });
  const preview = h("div", { class: "phone" }, h("div", { class: "phone-sub jp" }, ""));
  const rowsById = new Map();
  let loopCue = null;
  audio.addEventListener("timeupdate", () => {
    const t = audio.currentTime;
    if (loopCue && t >= loopCue.end + 0.3) { audio.currentTime = Math.max(0, loopCue.start - 0.3); }
    const c = subs.cues.find((x) => x.start <= t && t < x.end);
    preview.firstChild.textContent = c ? c.text : "";
    rowsById.forEach((row, id) => row.classList.toggle("playing", c?.id === id));
  });

  const issuesBox = h("div", {});
  const tbl = h("div", { class: "cues" });
  subs.cues.sort((a, b) => a.start - b.start);
  const cues = subs.cues;
  let issueMap = new Map();
  const flaggedLines = new Set((subs.content_check?.items || []).map((x) => x.line_id));

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
        err || warn ? `형식 오류 ${err}개 · 확인 권장 ${warn}개 (표에서 노란/빨간 줄)` : "형식 점검 통과: 겹침·음성 길이 초과·너무 짧음 없음 (시간이 말과 맞는지는 들어서 확인하세요)"));
      rowsById.forEach((row, id) => {
        const list = issueMap.get(id) || [];
        row.classList.toggle("err", list.some((i) => i.level === "error"));
        row.classList.toggle("warnrow", list.length > 0 && !list.some((i) => i.level === "error"));
        const box = row.querySelector(".cue-issues");
        clear(box).append(...list.map((i) => h("div", { class: `iss ${i.level}` }, i.message)));
      });
    } catch (e) { showError(e); }
  };

  const needsCheck = (c) => c.conf === "low" || (issueMap.get(c.id) || []).length || flaggedLines.has(c.line_id) || (c.src || []).some((r) => flaggedLines.has(r.line_id));
  let focusIdx = -1;
  const focusCue = (i) => {
    if (i < 0 || i >= cues.length) return;
    focusIdx = i;
    const c = cues[i];
    const row = rowsById.get(c.id);
    rowsById.forEach((r) => r.classList.remove("focused"));
    row?.classList.add("focused");
    row?.scrollIntoView({ block: "center", behavior: "smooth" });
    audio.currentTime = Math.max(0, c.start - 0.3);
    if (loopToggle.checked) loopCue = c;
    audio.play().catch(() => {});
  };
  const jump = (dir) => {
    for (let k = 1; k <= cues.length; k++) {
      const i = (focusIdx + dir * k + cues.length * 2) % cues.length;
      if (needsCheck(cues[i])) { focusCue(i); return; }
    }
    toast("확인이 필요한 자막이 없습니다.", "info");
  };
  const loopToggle = h("input", { type: "checkbox", onchange: (e) => { loopCue = e.target.checked && focusIdx >= 0 ? cues[focusIdx] : null; } });

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
      const nudge = (which, d) => { c[which] = Math.max(0, +(c[which] + d).toFixed(3)); c.manual_time = true; (which === "start" ? startIn : endIn).value = c[which].toFixed(2); markChanged(); if (loopCue === c) audio.currentTime = Math.max(0, c.start - 0.3); };
      const [cl, cc] = CONF[c.conf] || ["-", "low"];
      const row = h("div", { class: "cue" },
        h("div", { class: "cue-idx" }, i + 1, h("button", { class: "small", title: "이 자막 구간 재생(반복 재생이 켜져 있으면 반복)", onclick: () => focusCue(i) }, "▶")),
        h("div", { class: "cue-times" },
          h("div", { class: "row tight" }, "시작", startIn, h("button", { class: "tiny", onclick: () => nudge("start", -0.05) }, "-"), h("button", { class: "tiny", onclick: () => nudge("start", 0.05) }, "+")),
          h("div", { class: "row tight" }, "종료", endIn, h("button", { class: "tiny", onclick: () => nudge("end", -0.05) }, "-"), h("button", { class: "tiny", onclick: () => nudge("end", 0.05) }, "+")),
          h("div", { class: "muted small" }, `${(c.end - c.start).toFixed(2)}초`, " · 위치 추정 신뢰도 ", h("span", { class: `conf ${cc}` }, cl),
            c.manual_time ? " · 시간 수동" : "", c.manual_text ? " · 문구 수동" : "", c.manual_struct ? " · 나누기/합치기" : "")),
        h("div", { class: "cue-body" }, ta, c.note ? h("div", { class: "muted small" }, c.note) : null, h("div", { class: "cue-issues" })),
        h("div", { class: "cue-actions" },
          h("button", { class: "small", title: "커서 위치에서 자막 나누기", onclick: () => splitCue(i, ta.selectionStart) }, "나누기"),
          h("button", { class: "small", title: "다음 자막과 합치기", disabled: i === cues.length - 1, onclick: () => mergeCue(i) }, "합치기"),
          h("button", { class: "small", title: "문절 기준 자동 줄바꿈", onclick: async () => {
            const r = await api.post("/api/jp/tools", { text: flatText(c.text) });
            c.text = r.wrapped; ta.value = c.text; markChanged();
          } }, "줄바꿈"),
          h("button", { class: "small danger", onclick: async () => {
            if (!(await confirmBox("자막 삭제", "이 자막을 삭제합니다. 다시 맞추기를 해도 이 부분은 비워 둡니다(그때 다시 넣기를 선택할 수 있음).", "삭제", true))) return;
            subs.removed = [...(subs.removed || []), ...removedFromCue(c)];
            cues.splice(i, 1); changed(true); renderRows(); runCheck();
          } }, "삭제")));
      rowsById.set(c.id, row);
      tbl.append(row);
    });
  };

  const splitCue = (i, pos) => {
    const r = splitCueData(cues[i], pos);
    if (!r) { toast("자막 문구 안에서 나눌 위치에 커서를 두고 누르세요.", "info"); return; }
    cues.splice(i, 1, r[0], r[1]);
    changed(true); renderRows(); runCheck();
  };
  const mergeCue = (i) => {
    cues.splice(i, 2, mergeCueData(cues[i], cues[i + 1]));
    changed(true); renderRows(); runCheck();
  };

  page.append(section("자막 편집",
    h("div", { class: "sub-preview" }, h("div", {}, audio,
      h("div", { class: "row" },
        h("button", { class: "small", onclick: () => jump(-1) }, "◀ 이전 확인 필요"),
        h("button", { class: "small", onclick: () => jump(1) }, "다음 확인 필요 ▶"),
        h("label", { class: "chk small" }, loopToggle, " 선택한 자막 반복 재생")),
      h("div", { class: "muted small" }, "'확인 필요'는 위치 신뢰도 낮음·형식 문제·음성-대본 불일치 표시가 있는 자막입니다. 반복 재생을 켜고 시작·종료의 −/+ 로 맞추세요.")), preview),
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

  if (subs.removed?.length) {
    page.append(section(`삭제한 부분 (${subs.removed.length})`, h("div", { class: "muted small" }, "다시 맞추기를 해도 비워 둡니다."),
      subs.removed.map((r, i) => h("div", { class: "row between hist" }, h("span", { class: "jp" }, r.text),
        h("button", { class: "small", onclick: () => { subs.removed.splice(i, 1); changed(true); toast("다음 '다시 맞추기' 때 이 부분을 다시 자막으로 넣습니다.", "info"); rerender(); } }, "다음 정렬 때 다시 넣기")))));
  }
  if (subs.previous?.length) {
    page.append(section("이전 자막(덮어쓰기 전 보관본)", subs.previous.slice().reverse().map((pv, i) => h("div", { class: "row between hist" },
      h("span", {}, `${fmtTime(pv.saved_at)} · ${pv.reason} · ${pv.cues.length}개 · 음성 버전 ${pv.audio_version ?? "-"}`),
      h("button", { class: "small", onclick: async () => {
        if (!(await confirmBox("이전 자막 복원", "현재 자막을 보관하고 이 자막으로 되돌립니다. 음성 버전이 다르면 시간이 맞지 않을 수 있습니다.", "복원"))) return;
        const idx = subs.previous.length - 1 - i;
        const pick = subs.previous.splice(idx, 1)[0];
        keepPrevious("복원 전 보관");
        Object.assign(subs, { cues: pick.cues, based_on_audio_version: pick.audio_version, based_on_display_hash: pick.display_hash,
          based_on_tts_hash: pick.tts_hash, line_texts: pick.line_texts, removed: pick.removed || [] });
        changed(true); rerender();
      } }, "복원")))));
  }
  page.append(h("div", { class: "row end" }, h("button", { class: "primary", onclick: () => go("sources") }, "영상소스 단계로 →")));
}

function contentCheckPanel(cc, p) {
  const lines = new Map((p.script?.scenes || []).flatMap((s) => s.lines).map((l) => [l.id, l]));
  return section("음성 ↔ 대본 내용 확인",
    h("div", { class: `note ${cc.verified ? "" : "warn"}` }, cc.verified
      ? "음성 인식으로 대본과 대조했습니다. 인식도 틀릴 수 있으니 아래 문장은 직접 들어 확인하세요."
      : "기본 정렬은 쉼과 예상 길이만으로 위치를 추정합니다. 음성 내용이 대본과 같은지는 검증하지 않았습니다. 아래는 길이로 본 의심 구간입니다."),
    cc.items?.length ? h("table", { class: "tbl" }, h("tr", {}, h("th", {}, "문장"), h("th", {}, "내용"), h("th", {}, "들린 말(인식)")),
      cc.items.map((x) => h("tr", {}, h("td", { class: "jp" }, x.line_id ? (lines.get(x.line_id)?.display || "") : "(대본에 없음)"),
        h("td", {}, x.message), h("td", { class: "jp" }, x.heard || "")))) : h("div", { class: "ok-text" }, cc.verified ? "인식 결과에서 큰 불일치를 찾지 못했습니다." : "길이로 볼 때 눈에 띄는 이상은 없습니다(내용 일치 검증은 아님)."));
}

function keepPrevious(reason) {
  const subs = state.project.subtitles;
  if (!subs.cues?.length) return;
  subs.previous = [...(subs.previous || []), { saved_at: new Date().toISOString(), reason, cues: JSON.parse(JSON.stringify(subs.cues)),
    audio_version: subs.based_on_audio_version, display_hash: subs.based_on_display_hash, tts_hash: subs.based_on_tts_hash,
    line_texts: subs.line_texts, removed: subs.removed || [] }].slice(-5);
}

function applyResult(subs, r, chosen, reason) {
  keepPrevious(reason);
  Object.assign(subs, { cues: chosen.cues, line_texts: chosen.line_texts, removed: chosen.removed || [], method: r.method,
    generated_at: new Date().toISOString(), based_on_audio_version: r.audio_version, based_on_display_hash: r.display_hash,
    based_on_tts_hash: r.tts_hash, content_check: r.content_check || null });
}

async function runAlign(useAsr) {
  const p = state.project;
  if (!(await flush())) return;
  let r;
  try {
    r = await runJob(api.post(`/api/projects/${p.id}/subtitles/align`, { use_asr: useAsr }));
  } catch (e) { if (e.status !== 499) showError(e); return; }
  const subs = p.subtitles;
  let proposal = r.proposal;
  const fresh = proposal.fresh;
  if (!subs.cues?.length) {
    applyResult(subs, r, fresh, "처음 생성");
  } else if (!proposal.keep_manual) {
    const ok = await confirmBox("자막 덮어쓰기", `새로 맞춘 자막 ${fresh.cues.length}개로 바꿉니다. 직접 고친 문구·나누기·합치기·삭제 기록은 없습니다.\n현재 자막은 '이전 자막'에 보관되어 복원할 수 있습니다.`, "바꾸기");
    if (!ok) return;
    applyResult(subs, r, fresh, "다시 맞추기 전");
  } else {
    const choices = {};
    let restoreRemoved = false;
    const body = h("div", {});
    const render = () => {
      const km = proposal.keep_manual, rep = km.report, cov = km.coverage;
      clear(body).append(
        h("p", {}, h("b", {}, rep.time_only ? "대본 문구는 그대로이고, 음성(시간)만 바뀌었습니다." : `대본(화면 표시) 문구가 바뀐 줄 ${rep.changed_lines.length}개가 있습니다.`)),
        rep.changed_lines.length ? h("table", { class: "tbl" }, h("tr", {}, h("th", {}, "이전 문구"), h("th", {}, "지금 문구")),
          rep.changed_lines.map((x) => h("tr", {}, h("td", { class: "jp" }, x.old || "-"), h("td", { class: "jp" }, x.new)))) : null,
        h("p", {}, `직접 고친 문구·나누기·합치기를 유지하는 자막 ${rep.kept}개, 나머지 부분을 새로 만든 자막 ${rep.regenerated}개.`),
        rep.manual_time_reset ? h("p", { class: "muted small" }, `시간만 직접 고친 자막 ${rep.manual_time_reset}개는 새 음성 기준으로 다시 맞춰집니다.`) : null,
        (subs.removed || []).length ? h("label", { class: "chk" }, h("input", { type: "checkbox", checked: restoreRemoved, onchange: async (e) => { restoreRemoved = e.target.checked; await recompute(); } }),
          ` 삭제했던 부분 ${(subs.removed || []).length}개도 다시 자막으로 넣기`) : null,
        rep.uncertain.length ? h("div", {}, h("p", {}, h("b", {}, `자동으로 맞추기 어려운 자막 ${rep.uncertain.length}개 — 하나씩 고르세요`)),
          rep.uncertain.map((u) => h("div", { class: "diff" },
            h("div", { class: "jp" }, `「${u.text}」`), h("div", { class: "muted small" }, u.reason),
            u.lines.map((l) => h("div", { class: "small jp" }, `이전: ${l.old || "-"} → 지금: ${l.new || "(지워짐)"}`)),
            h("label", { class: "chk" }, h("input", { type: "radio", name: `u_${u.cue_id}`, checked: u.choice !== "keep", onchange: async () => { choices[u.cue_id] = "new"; await recompute(); } }), " 새 대본 기준으로 다시 만들기"),
            h("label", { class: "chk" }, h("input", { type: "radio", name: `u_${u.cue_id}`, checked: u.choice === "keep", onchange: async () => { choices[u.cue_id] = "keep"; await recompute(); } }), " 내가 고친 문구 그대로 유지")))) : null,
        h("div", { class: `note ${cov.ok ? "ok" : "warn"}` }, cov.ok
          ? "✔ 대본의 모든 문구가 자막에 빠짐없이·중복 없이 들어 있습니다(직접 삭제한 부분 제외)."
          : h("div", {}, h("b", {}, "누락·중복이 있어 이 방식으로는 적용할 수 없습니다:"), cov.problems.map((x) => h("div", { class: "jp small" }, x.message)))),
        h("details", {}, h("summary", {}, `결과 미리보기 (${km.cues.length}개)`),
          h("table", { class: "tbl" }, km.cues.map((c) => h("tr", {}, h("td", { class: "nowrap" }, `${fmtSec(c.start)}~${fmtSec(c.end)}`),
            h("td", { class: "jp" }, c.text), h("td", { class: "small muted" }, c.manual_text || c.manual_struct ? "수동 유지" : "새로 만듦"))))),
        h("p", { class: "muted small" }, "현재 자막은 '이전 자막'에 보관되어 언제든 복원할 수 있습니다."));
      keepBtn.disabled = !cov.ok;
    };
    const recompute = async () => {
      try {
        proposal = await api.post(`/api/projects/${p.id}/subtitles/merge`, { auto_cues: r.cues, line_times: r.line_times, duration: r.duration, choices, restore_removed: restoreRemoved });
        render();
      } catch (e) { showError(e); }
    };
    let keepBtn;
    const done = new Promise((resolve) => {
      const back = h("div", { class: "modal-back" });
      const close = (v) => { back.remove(); resolve(v); };
      keepBtn = h("button", { class: "primary", onclick: () => close("keep") }, "수동 수정 유지하고 적용");
      back.append(h("div", { class: "modal wide" },
        h("div", { class: "modal-head" }, h("h3", {}, "자막 다시 맞추기 — 수동 수정 확인"), h("button", { class: "icon", onclick: () => close(null) }, "✕")),
        h("div", { class: "modal-body" }, body),
        h("div", { class: "modal-foot" }, h("button", { onclick: () => close(null) }, "취소"),
          h("button", { onclick: () => close("new") }, "새 결과로 모두 바꾸기"), keepBtn)));
      document.body.append(back);
    });
    render();
    const choice = await done;
    if (!choice) return;
    if (choice === "keep") {
      if (!proposal.keep_manual.coverage.ok) { toast("누락·중복이 있어 적용하지 않았습니다.", "error"); return; }
      applyResult(subs, r, proposal.keep_manual, "다시 맞추기 전 (수동 수정 유지)");
    } else {
      applyResult(subs, r, proposal.fresh, "다시 맞추기 전 (전체 교체)");
    }
  }
  changed(true);
  const low = subs.cues.filter((c) => c.conf === "low").length;
  toast(`자막 ${subs.cues.length}개를 맞췄습니다.`, "ok", low ? `위치 신뢰도 낮은 자막 ${low}개는 '다음 확인 필요'로 이동해 확인하세요.` : "");
  rerender();
}
