import { api, runJob, mediaUrl } from "../api.js";
import { state, changed, flush } from "../state.js";
import { h, clear, section, field, bindInput, select, confirmBox, modal, toast, empty, fmtSec, fmtTime, copyText, showError } from "../ui.js";
import { go, rerender } from "../nav.js";

const MODES = [["natural", "자연스럽게"], ["fast", "빠르게"], ["custom", "직접 설정"]];
const DEFAULTS = {
  natural: { min_silence_ms: 300, gap_ms: 420, lead_ms: 150, tail_ms: 350, guard_before_ms: 60, guard_after_ms: 130 },
  fast: { min_silence_ms: 200, gap_ms: 240, lead_ms: 80, tail_ms: 220, guard_before_ms: 40, guard_after_ms: 100 },
};
const PARAM_INFO = [
  ["min_silence_ms", "이 길이(ms) 이상인 무음만 줄임", "문장 안의 짧은 쉼(쉼표 등)은 그대로 둡니다."],
  ["gap_ms", "문장 사이에 남길 간격(ms)", "긴 무음을 이 길이로 줄입니다."],
  ["lead_ms", "맨 앞에 남길 무음(ms)", ""],
  ["tail_ms", "맨 끝에 남길 무음(ms)", ""],
  ["guard_after_ms", "소리 끝난 뒤 보호 여유(ms)", "약한 어미(す·た 등)가 잘리지 않게 남기는 최소 여유"],
  ["guard_before_ms", "소리 시작 전 보호 여유(ms)", "첫 자음이 잘리지 않게 남기는 최소 여유"],
];

let analysis = null; // 마지막 분석 결과(저장하지 않음)
let analysisKey = "";

function silenceCfg() {
  const a = state.project.audio;
  a.silence = a.silence || { mode: "natural", params: {}, protect_ranges: [], custom_ranges: [] };
  a.silence.protect_ranges = a.silence.protect_ranges || [];
  a.silence.custom_ranges = a.silence.custom_ranges || [];
  return a.silence;
}

function reqBody() {
  const c = silenceCfg();
  const params = c.mode === "custom" ? { ...DEFAULTS.natural, ...c.params } : {};
  if (c.params?.threshold_mode === "manual") { params.threshold_mode = "manual"; params.threshold_db = c.params.threshold_db; }
  if (c.params?.threshold_offset_db) params.threshold_offset_db = c.params.threshold_offset_db;
  return { mode: c.mode, params, protect_ranges: c.protect_ranges, custom_ranges: c.custom_ranges, auto_holds: c.auto_holds !== false };
}

// 구간 재생
function playRange(audioEl, start, end) {
  audioEl.currentTime = Math.max(0, start);
  audioEl.play();
  const stop = () => { if (audioEl.currentTime >= end) { audioEl.pause(); audioEl.removeEventListener("timeupdate", stop); } };
  audioEl.addEventListener("timeupdate", stop);
}

function mapTime(segments, t) {
  for (const [os, oe, ns] of segments) {
    if (t < os) return ns;
    if (t <= oe) return ns + (t - os);
  }
  if (segments.length) { const [os, oe, ns] = segments[segments.length - 1]; return ns + (oe - os); }
  return t;
}

function drawWave(canvas, peaks, duration, regions = [], playhead = null) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width = canvas.clientWidth * devicePixelRatio;
  const H = canvas.height = 120 * devicePixelRatio;
  ctx.clearRect(0, 0, W, H);
  const css = getComputedStyle(document.body);
  for (const r of regions) {
    const x0 = (r.start / duration) * W, x1 = (r.end / duration) * W;
    ctx.fillStyle = r.protected ? "rgba(46,160,90,.25)" : r.remove ? "rgba(220,70,70,.12)" : "rgba(120,120,120,.10)";
    ctx.fillRect(x0, 0, x1 - x0, H);
    if (r.remove) {
      ctx.fillStyle = "rgba(220,70,70,.45)";
      ctx.fillRect((r.remove[0] / duration) * W, 0, ((r.remove[1] - r.remove[0]) / duration) * W, H);
    }
    if (r.flags?.length) { ctx.strokeStyle = "rgba(230,160,0,.9)"; ctx.lineWidth = 2 * devicePixelRatio; ctx.strokeRect(x0, 1, x1 - x0, H - 2); }
  }
  ctx.fillStyle = css.getPropertyValue("--wave") || "#3b6fd8";
  const n = peaks.length;
  for (let i = 0; i < n; i++) {
    const [mn, mx] = peaks[i];
    const x = (i / n) * W;
    const y0 = H / 2 - mx * H / 2, y1 = H / 2 - mn * H / 2;
    ctx.fillRect(x, y0, Math.max(1, W / n), Math.max(1, y1 - y0));
  }
  if (playhead !== null) { ctx.fillStyle = "#e33"; ctx.fillRect((playhead / duration) * W, 0, 2 * devicePixelRatio, H); }
}

export async function renderAudio(page) {
  const p = state.project;
  const a = p.audio;
  page.append(h("div", { class: "page-head" }, h("h1", {}, "3. 음성"),
    h("div", { class: "muted" }, "타입캐스트 사이트에서 직접 음성을 만든 뒤 파일을 넣고, 불필요한 무음을 정리합니다. 재생 속도는 바꾸지 않습니다.")));

  // 1) 타입캐스트 낭독문
  const scenes = p.script?.scenes || [];
  const full = scenes.flatMap((s) => s.lines.map((l) => (l.tts || "").trim()).filter(Boolean)).join("\n");
  const tc = p.typecast;
  page.append(section("타입캐스트용 낭독문",
    h("div", { class: "muted small" }, "장면 번호·편집 지시·한국어 번역이 들어가지 않은 일본어 낭독문입니다. 타입캐스트 웹사이트에 붙여 넣어 음성을 만드세요. (자동 로그인·비공식 연동은 하지 않습니다.)"),
    full ? h("textarea", { class: "full jp", rows: Math.min(14, full.split("\n").length + 1), readonly: true }, full) : empty("대본이 없습니다."),
    h("div", { class: "row" },
      h("button", { class: "primary", disabled: !full, onclick: () => copyText(full, "전체 낭독문") }, "전체 복사"),
      h("span", { class: "muted small" }, `${full.length}자 · ${full ? full.split("\n").length : 0}문장`)),
    scenes.length ? h("details", {}, h("summary", {}, "구간별 복사"),
      h("div", { class: "row wrap" }, scenes.map((s, i) => h("button", { class: "small", onclick: () => copyText(s.lines.map((l) => l.tts).filter(Boolean).join("\n"), `장면 ${i + 1} 낭독문`) }, `장면 ${i + 1}`)))) : null,
    h("div", { class: "grid2" },
      field("사용한 음성(캐릭터)", bindInput(tc, "voice", () => changed(), { placeholder: "예: 타입캐스트 ○○ 캐릭터" })),
      field("필요한 크레딧 표기", bindInput(tc, "credit_text", () => changed(), { placeholder: "요금제 조건에 따라 표기 문구를 기록" }), "사용 중인 타입캐스트 요금제의 상업 이용·표기 조건을 직접 확인해 적으세요.")),
    h("div", { class: "grid2" },
      field("음성 설정 메모(속도·감정 등)", bindInput(tc, "settings_memo", () => changed(), { multiline: true, rows: 2 })),
      field("기타 메모", bindInput(tc, "notes", () => changed(), { multiline: true, rows: 2 })))));

  // 2) 파일 넣기
  const fileIn = h("input", { type: "file", accept: ".wav,.mp3,.m4a,.aac,.ogg,.flac,audio/*" });
  const upBox = section("음성 파일 넣기",
    h("div", { class: "muted small" }, "타입캐스트에서 내려받은 WAV 또는 MP3(배경음악 없는 음성)를 넣으세요. 원본은 그대로 보관되고, 무음 정리는 별도 파일로 만들어집니다."),
    h("div", { class: "row" }, fileIn, h("button", { class: "primary", onclick: async () => {
      const f = fileIn.files[0];
      if (!f) { toast("파일을 선택하세요.", "info"); return; }
      if (a.original && !(await confirmBox("새 음성 넣기", "새 음성을 넣으면 현재 무음 정리본과 자막은 '재생성 필요'가 됩니다.\n기존 원본 파일은 지우지 않고 기록에 남깁니다.", "넣기"))) return;
      await flush();
      const fd = new FormData(); fd.append("file", f);
      try {
        const r = await api.upload(`/api/projects/${p.id}/audio/upload`, fd);
        if (a.original) a.originals_history = [...(a.originals_history || []), a.original];
        a.original = r.original;
        analysis = null;
        changed(true);
        toast("음성을 넣었습니다.", "ok");
        for (const w of r.original.warnings || []) toast(w, "warn");
        rerender();
      } catch (e) { showError(e); }
    } }, "넣기")));
  page.append(upBox);
  if (!a.original) return;

  const o = a.original;
  const origAudio = h("audio", { controls: true, preload: "auto", src: mediaUrl(p.id, o.file, o.sha256) });
  page.append(section("원본 음성",
    h("div", { class: "kvs" },
      kv("파일", o.orig_name), kv("길이", `${o.duration.toFixed(2)}초`), kv("형식", `${o.codec} · ${o.sr}Hz · ${o.channels === 1 ? "모노" : "스테레오"}`),
      kv("넣은 시각", fmtTime(o.imported_at)), kv("소리 크기", `말소리 약 ${o.levels?.speech_db}dB · 바닥 소음 ${o.levels?.floor_db}dB`)),
    (o.warnings || []).map((w) => h("div", { class: "note warn" }, w)),
    origAudio,
    (a.originals_history || []).length ? h("details", {}, h("summary", {}, `이전 원본 ${a.originals_history.length}개`),
      a.originals_history.slice().reverse().map((x, i) => h("div", { class: "row between hist" }, h("span", {}, `${x.orig_name} · ${x.duration}초 · ${fmtTime(x.imported_at)}`),
        h("button", { class: "small", onclick: async () => {
          if (!(await confirmBox("이전 원본으로 되돌리기", "이 원본을 다시 사용합니다. 무음 정리와 자막을 다시 해야 합니다.", "되돌리기"))) return;
          const idx = a.originals_history.length - 1 - i;
          const pick = a.originals_history.splice(idx, 1)[0];
          a.originals_history.push(a.original);
          a.original = pick; analysis = null; changed(true); rerender();
        } }, "이 원본 사용")))) : null));

  // 3) 무음 정리
  const cfg = silenceCfg();
  const paramBox = h("div", {});
  const renderParams = () => {
    clear(paramBox);
    const base = DEFAULTS[cfg.mode === "fast" ? "fast" : "natural"];
    if (cfg.mode !== "custom") {
      paramBox.append(h("div", { class: "muted small" }, cfg.mode === "natural"
        ? `자연스럽게: ${base.min_silence_ms}ms 이상 무음만 줄이고 문장 사이 ${base.gap_ms}ms를 남깁니다. 호흡감을 유지합니다.`
        : `빠르게: ${base.min_silence_ms}ms 이상 무음을 ${base.gap_ms}ms로 줄입니다. 보호 여유는 유지합니다.`));
    } else {
      cfg.params = { ...DEFAULTS.natural, ...cfg.params };
      paramBox.append(h("div", { class: "grid3" }, PARAM_INFO.map(([k, label, help]) => field(label, bindInput(cfg.params, k, () => changed(), { type: "number" }), help))));
    }
    cfg.params = cfg.params || {};
    const thr = h("div", { class: "grid3" },
      field("무음 판단 기준", select([["auto", "자동(음량 분석)"], ["manual", "직접 dB 지정"]], cfg.params.threshold_mode || "auto", (v) => { cfg.params.threshold_mode = v; changed(); renderParams(); })),
      cfg.params.threshold_mode === "manual"
        ? field("기준 dB(이보다 작으면 무음)", bindInput(cfg.params, "threshold_db", () => changed(), { type: "number", placeholder: "-55" }), "-60에 가까울수록 작은 소리도 소리로 봅니다(더 안전).")
        : field("자동 기준 보정(dB)", bindInput(cfg.params, "threshold_offset_db", () => changed(), { type: "number", placeholder: "0" }), "음수로 하면 더 작은 소리까지 보호합니다."));
    paramBox.append(thr);
  };
  renderParams();

  const holds = scenes.map((s, i) => [i + 1, s]).filter(([, s]) => s.hold_ms > 0);
  const anaBox = h("div", {});
  const canvas = h("canvas", { class: "wave" });
  const protectIn = { start: "", end: "" };

  const runAnalyze = async () => {
    await flush();
    try {
      analysis = await runJob(api.post(`/api/projects/${p.id}/audio/analyze`, reqBody()));
      analysisKey = JSON.stringify(reqBody()) + o.sha256;
      renderAnalysis();
    } catch (e) { if (e.status !== 499) showError(e); }
  };

  const renderAnalysis = () => {
    clear(anaBox);
    if (!analysis) { anaBox.append(empty("[분석(미리보기)]를 누르면 줄일 구간을 먼저 보여줍니다. 파일은 아직 바뀌지 않습니다.")); return; }
    const an = analysis;
    const changedRegions = an.regions.filter((r) => r.remove);
    const flagged = an.regions.filter((r) => r.flags?.length);
    anaBox.append(
      h("div", { class: "stats" },
        stat("원본 길이", `${an.duration.toFixed(2)}초`), stat("정리 후 예상", `${an.result_duration.toFixed(2)}초`),
        stat("줄어드는 시간", `${an.removed_s.toFixed(2)}초`), stat("무음 기준", `${an.threshold_db}dB`),
        stat("바뀌는 구간", `${changedRegions.length}개`), stat("확인 권장", `${flagged.length}개`)),
      an.bgm_suspect ? h("div", { class: "note warn" }, "바닥 소음이 커서 배경음악이 섞였을 수 있습니다. 무음 판단이 부정확할 수 있습니다.") : null,
      canvas,
      h("div", { class: "legend" }, h("span", { class: "lg red" }, "잘라낼 부분"), h("span", { class: "lg grey" }, "무음(유지)"), h("span", { class: "lg green" }, "보호 구간"), h("span", { class: "lg yellow" }, "확인 권장")));
    requestAnimationFrame(() => drawWave(canvas, an.peaks, an.duration, an.regions));
    canvas.onclick = (ev) => {
      const rect = canvas.getBoundingClientRect();
      const t = ((ev.clientX - rect.left) / rect.width) * an.duration;
      origAudio.currentTime = t; origAudio.play();
    };
    const KIND = { lead: "시작 무음", tail: "끝 무음", gap: "문장 사이" };
    const tbl = h("table", { class: "tbl regions" }, h("tr", {}, h("th", {}, "#"), h("th", {}, "종류"), h("th", {}, "원본 위치"), h("th", {}, "길이 → 정리 후"), h("th", {}, "처리"), h("th", {}, "확인할 점"), h("th", {}, "")));
    an.regions.forEach((r, i) => {
      const isProt = r.protected;
      const custom = cfg.custom_ranges.find((c) => c[0] <= (r.start + r.end) / 2 && (r.start + r.end) / 2 <= c[1]);
      tbl.append(h("tr", { class: r.flags?.length ? "flag" : "" },
        h("td", {}, i + 1), h("td", {}, KIND[r.kind] || r.kind), h("td", {}, `${fmtSec(r.start)} ~ ${fmtSec(r.end)}`),
        h("td", {}, `${(r.length * 1000).toFixed(0)}ms → ${(r.new_length * 1000).toFixed(0)}ms`),
        h("td", {}, isProt ? "유지(보호)" : r.remove ? (r.kind === "gap" ? "단축" : "잘라냄") : "그대로",
          r.custom_label ? h("div", { class: "small muted" }, `${r.custom_label} ${r.custom_keep_ms}ms`) : null),
        h("td", { class: "small" }, (r.flags || []).join(" ")),
        h("td", { class: "nowrap" },
          h("button", { class: "small", title: "원본에서 이 구간 앞뒤 듣기", onclick: () => playRange(origAudio, r.start - 0.8, r.end + 0.8) }, "▶ 원본"),
          isProt
            ? h("button", { class: "small", onclick: () => { cfg.protect_ranges = cfg.protect_ranges.filter((x) => !(x[0] < r.end && r.start < x[1])); changed(); runAnalyze(); } }, "보호 해제")
            : h("button", { class: "small", title: "이 무음은 줄이지 않음(시청자가 생각할 시간 등)", onclick: () => { cfg.protect_ranges.push([r.start, r.end, "사용자 지정 유지"]); changed(); runAnalyze(); } }, "유지"),
          r.kind === "gap" && !isProt ? h("button", { class: "small", title: "이 구간만 남길 길이 지정", onclick: async () => {
            const v = await modal("이 구간에 남길 길이", h("div", {}, h("p", {}, `현재 ${(r.length * 1000).toFixed(0)}ms. 남길 길이(ms)를 입력하세요.`), h("input", { id: "ckeep", type: "number", value: custom ? custom[2] : Math.round(r.new_length * 1000) })),
              [{ label: "취소", value: null }, custom ? { label: "직접 지정 해제", value: "clear" } : null, { label: "적용", primary: true, value: () => document.getElementById("ckeep").value }].filter(Boolean));
            if (v === null) return;
            cfg.custom_ranges = cfg.custom_ranges.filter((c) => !(c[0] <= (r.start + r.end) / 2 && (r.start + r.end) / 2 <= c[1]));
            if (v !== "clear") cfg.custom_ranges.push([r.start, r.end, Math.max(60, Number(v) || 0)]);
            changed(); runAnalyze();
          } }, custom ? `${custom[2]}ms` : "길이") : null)));
    });
    anaBox.append(tbl);
  };

  page.append(section("무음 정리",
    h("div", { class: "row" }, h("span", {}, "방식"), ...MODES.map(([v, l]) => {
      const b = h("button", { class: cfg.mode === v ? "seg active" : "seg", onclick: () => { cfg.mode = v; changed(); rerender(); } }, l);
      return b;
    })),
    paramBox,
    holds.length ? h("div", { class: "note" }, "대본에 의도적 멈춤이 있는 장면: ", holds.map(([i, s]) => `장면 ${i}(${s.hold_ms}ms${s.hold_reason ? ", " + s.hold_reason : ""})`).join(", "),
      h("div", {}, h("label", { class: "chk" }, h("input", { type: "checkbox", checked: cfg.auto_holds !== false, onchange: (e) => { cfg.auto_holds = e.target.checked; changed(); } }),
        " 해당 장면 마지막 문장 뒤의 쉼을 이 길이만큼 자동으로 남기기")),
      h("div", { class: "small muted" }, "문장 위치는 음성의 쉼으로 추정합니다. 분석 표의 '처리' 칸에서 맞는 구간인지 확인하고, 다르면 [유지]·[길이]로 직접 지정하세요.")) : null,
    h("details", {}, h("summary", {}, `보호 구간 직접 지정 (${cfg.protect_ranges.length}개)`),
      cfg.protect_ranges.map((r, i) => h("div", { class: "row" }, `${fmtSec(r[0])} ~ ${fmtSec(r[1])} ${r[2] || ""}`, h("button", { class: "small", onclick: () => { cfg.protect_ranges.splice(i, 1); changed(); rerender(); } }, "삭제"))),
      h("div", { class: "row" }, "원본 기준 시작(초)", bindInput(protectIn, "start", null, { type: "number", class: "num" }), "끝(초)", bindInput(protectIn, "end", null, { type: "number", class: "num" }),
        h("button", { class: "small", onclick: () => {
          const s0 = Number(protectIn.start), e0 = Number(protectIn.end);
          if (!(e0 > s0)) { toast("시작보다 큰 끝 시간을 넣으세요.", "info"); return; }
          cfg.protect_ranges.push([s0, e0, "직접 지정"]); changed(); rerender();
        } }, "추가"))),
    h("div", { class: "row" },
      h("button", { onclick: runAnalyze }, "분석(미리보기)"),
      h("button", { class: "primary", onclick: async () => {
        await flush();
        try {
          const r = await runJob(api.post(`/api/projects/${p.id}/audio/apply`, reqBody()));
          setProcessed(r.processed);
        } catch (e) { if (e.status !== 499) showError(e); }
      } }, "무음 정리 적용"),
      h("button", { onclick: async () => {
        if (!(await confirmBox("원본 그대로 사용", "무음을 정리하지 않고 원본을 최종 음성으로 씁니다(WAV로 변환만).", "사용"))) return;
        await flush();
        try { const r = await runJob(api.post(`/api/projects/${p.id}/audio/apply`, { ...reqBody(), use_original: true })); setProcessed(r.processed); } catch (e) { if (e.status !== 499) showError(e); }
      } }, "원본 그대로 사용")),
    anaBox));
  if (analysis && analysisKey === JSON.stringify(reqBody()) + o.sha256) renderAnalysis();
  else { analysis = null; renderAnalysis(); }

  // 4) 처리 결과와 비교
  const pr = a.processed;
  if (!pr) return;
  const stale = pr.source_sha !== o.sha256;
  const procAudio = h("audio", { controls: true, preload: "auto", src: mediaUrl(p.id, pr.file, pr.sha256 || pr.created_at) });
  const v = pr.verify || {};
  const pcanvas = h("canvas", { class: "wave" });
  const box = section(`최종 음성 (버전 ${pr.version})`,
    stale ? h("div", { class: "banner stale" }, "원본 음성이 바뀌었습니다. 무음 정리를 다시 적용하세요.") : null,
    h("div", { class: "stats" },
      stat("원본", `${(pr.original_duration ?? o.duration).toFixed(2)}초`), stat("최종", `${pr.duration.toFixed(2)}초`),
      stat("줄어든 시간", `${(pr.removed_s || 0).toFixed(2)}초 (${pr.original_duration ? Math.round((pr.removed_s / pr.original_duration) * 100) : 0}%)`),
      stat("방식", pr.mode === "original" ? "원본 그대로" : (MODES.find((m) => m[0] === pr.mode)?.[1] || pr.mode)),
      stat("바뀐 구간", `${pr.regions_changed ?? 0}개`), stat("확인 권장", `${pr.uncertain ?? 0}개`)),
    h("div", { class: `note ${v.ok ? "ok" : "warn"}` }, h("b", {}, "발음 잘림 검증: "), v.summary || "-",
      v.removed_max_db !== undefined ? ` (잘라낸 부분 최대 ${v.removed_max_db}dB / 기준 ${v.threshold_db}dB, 가장 가까운 소리까지 ${v.min_distance_to_speech_ms ?? "-"}ms)` : ""),
    h("div", { class: "muted small" }, "재생 속도는 바꾸지 않았습니다. 무음 구간의 가운데만 잘라냈습니다. 그래도 '확인 권장' 구간은 직접 들어 보세요."),
    h("div", { class: "ab" },
      h("div", {}, h("b", {}, "원본"), origAudio.cloneNode()),
      h("div", {}, h("b", {}, "정리본"), procAudio)),
    pcanvas,
    h("div", { class: "muted small" }, `만든 시각 ${fmtTime(pr.created_at)} · 파일 ${pr.file}`));
  // 원본 비교 플레이어는 같은 소스를 쓰도록 다시 설정
  box.querySelector(".ab audio").src = origAudio.src;
  page.append(box);
  flush().then(() => api.get(`/api/projects/${p.id}/audio/peaks?which=processed`)).then((r) => {
    drawWave(pcanvas, r.peaks, r.duration, []);
    pcanvas.onclick = (ev) => { const rect = pcanvas.getBoundingClientRect(); procAudio.currentTime = ((ev.clientX - rect.left) / rect.width) * r.duration; procAudio.play(); };
  }).catch(() => {});

  if (analysis && pr.segments) {
    const tbl = h("table", { class: "tbl" }, h("tr", {}, h("th", {}, "구간"), h("th", {}, "원본 듣기"), h("th", {}, "정리본 듣기")));
    analysis.regions.filter((r) => r.remove || r.flags?.length).forEach((r, i) => {
      const t0 = mapTime(pr.segments, r.start), t1 = mapTime(pr.segments, r.end);
      tbl.append(h("tr", {}, h("td", {}, `${fmtSec(r.start)} (${(r.length * 1000).toFixed(0)}→${(r.new_length * 1000).toFixed(0)}ms) ${r.flags?.length ? "⚠" : ""}`),
        h("td", {}, h("button", { class: "small", onclick: () => playRange(box.querySelector(".ab audio"), r.start - 1, r.end + 1) }, "▶ 원본")),
        h("td", {}, h("button", { class: "small", onclick: () => playRange(procAudio, t0 - 1, t1 + 1) }, "▶ 정리본"))));
    });
    page.append(section("전후 구간 비교 듣기", tbl));
  }

  if ((a.processed_history || []).length) {
    page.append(section("이전 처리본 (되돌리기)", a.processed_history.slice().reverse().map((x, i) => h("div", { class: "row between hist" },
      h("span", {}, `버전 ${x.version} · ${x.mode} · ${x.duration}초 · ${fmtTime(x.created_at)}${x.source_sha !== o.sha256 ? " (다른 원본)" : ""}`),
      h("button", { class: "small", onclick: async () => {
        if (!(await confirmBox("처리본 되돌리기", `버전 ${x.version}을 최종 음성으로 사용합니다. 자막을 다시 맞춰야 합니다.`, "사용"))) return;
        const idx = a.processed_history.length - 1 - i;
        const pick = a.processed_history.splice(idx, 1)[0];
        a.processed_history.push(a.processed);
        a.processed = pick; changed(true); rerender();
      } }, "이 버전 사용")))));
  }
  page.append(h("div", { class: "row end" }, h("button", { class: "primary", onclick: () => go("subtitles") }, "자막 단계로 →")));
}

function setProcessed(proc) {
  const a = state.project.audio;
  if (a.processed) a.processed_history = [...(a.processed_history || []), a.processed].slice(-10);
  a.processed = proc;
  changed(true);
  toast(`최종 음성 버전 ${proc.version}을 만들었습니다. ${proc.removed_s.toFixed(2)}초 줄었습니다.`, "ok",
    (state.project.subtitles?.cues || []).length ? "음성이 바뀌어 자막을 다시 맞춰야 합니다." : "");
  if (!proc.verify?.ok) toast("발음 잘림 검증에서 확인이 필요한 결과가 나왔습니다.", "warn", proc.verify?.summary);
  rerender();
}

const kv = (k, v) => h("div", { class: "kv" }, h("span", { class: "k" }, k), h("span", { class: "v" }, v ?? "-"));
const stat = (k, v) => h("div", { class: "stat" }, h("div", { class: "stat-v" }, v), h("div", { class: "stat-k" }, k));
