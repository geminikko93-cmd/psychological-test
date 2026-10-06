// AI 작업 공통 흐름: 연결 확인 → 저장 → 작업 실행(진행/취소) → 사용량 표시.
import { api, runJob } from "./api.js";
import { state, flush, logAi, changed } from "./state.js";
import { h, modal, toast, showError } from "./ui.js";
import { go } from "./nav.js";

export function aiReady() {
  const s = state.settings?.settings?.ai;
  return !!(s && s.base_url && s.model && (s.auth === "none" || state.settings?.secrets?.ai_api_key));
}

export async function needConnection() {
  const r = await modal("AI 연결이 필요합니다",
    h("div", {}, h("p", {}, "이 기능은 앤트로픽 API 중개서버에 연결되어 있어야 동작합니다."),
      h("p", {}, "연결 전에는 예시 문구나 샘플을 AI 결과처럼 보여주지 않습니다. 직접 입력해서 작업을 계속할 수도 있습니다.")),
    [{ label: "닫기", value: null }, { label: "설정으로 이동", value: "go", primary: true }]);
  if (r === "go") go("settings");
}

export function usageText(log) {
  if (!log) return "";
  const u = log.usage || {};
  const parts = [];
  if (u.input_tokens !== undefined) parts.push(`입력 ${u.input_tokens.toLocaleString()}토큰`);
  if (u.output_tokens !== undefined) parts.push(`출력 ${u.output_tokens.toLocaleString()}토큰`);
  if (u.cache_read_input_tokens) parts.push(`캐시 읽기 ${u.cache_read_input_tokens.toLocaleString()}`);
  let s = parts.length ? parts.join(" · ") + " (서버 보고값)" : "서버가 사용량을 알려주지 않았습니다";
  if (log.cost) s += ` · 추정 비용 약 ${log.cost.estimate} ${log.cost.currency} (입력한 단가 기준 추정)`;
  if (log.latency_s) s += ` · ${log.latency_s}초`;
  return s;
}

export async function runAi(task, body = {}) {
  if (!aiReady()) { await needConnection(); return null; }
  await flush();
  try {
    const res = await runJob(api.post(`/api/projects/${state.project.id}/ai/${task}`, body));
    if (res?.log) { logAi(res.log); changed(); toast("AI 응답을 받았습니다.", "ok", usageText(res.log)); }
    return res;
  } catch (e) {
    if (e.status !== 499) showError(e);
    else toast("AI 요청을 취소했습니다.", "info");
    return null;
  }
}
