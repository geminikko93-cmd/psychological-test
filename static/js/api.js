// 로컬 서버 API 호출. 변경 요청에는 x-jpss 헤더를 붙인다(다른 사이트의 요청 차단용).

export class ApiError extends Error {
  constructor(message, hint = "", status = 0) { super(message); this.hint = hint; this.status = status; }
}

async function request(method, url, body, isForm = false) {
  const opts = { method, headers: { "x-jpss": "1" } };
  if (body !== undefined) {
    if (isForm) opts.body = body;
    else { opts.headers["content-type"] = "application/json"; opts.body = JSON.stringify(body); }
  }
  let res;
  try {
    res = await fetch(url, opts);
  } catch {
    throw new ApiError("프로그램 서버에 연결할 수 없습니다.", "검은 콘솔 창(start.bat)이 켜져 있는지 확인하세요.");
  }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    if (typeof data === "object" && data) throw new ApiError(data.error || `오류 (HTTP ${res.status})`, data.hint || "", res.status);
    throw new ApiError(`오류 (HTTP ${res.status})`, "", res.status);
  }
  return data;
}

export const api = {
  get: (u) => request("GET", u),
  post: (u, b = {}) => request("POST", u, b),
  put: (u, b = {}) => request("PUT", u, b),
  del: (u) => request("DELETE", u),
  upload: (u, form) => request("POST", u, form, true),
};

// 작업 진행 표시 + 취소. onProgress(job)로 상태 전달, 완료 시 result 반환.
const jobListeners = new Set();
export function onJobUpdate(fn) { jobListeners.add(fn); return () => jobListeners.delete(fn); }

export async function runJob(startPromise) {
  let job = await startPromise;
  jobListeners.forEach((f) => f(job));
  while (job.status === "running") {
    await new Promise((r) => setTimeout(r, 500));
    try {
      job = await api.get(`/api/jobs/${job.id}`);
    } catch (e) {
      jobListeners.forEach((f) => f({ ...job, status: "error", error: e.message }));
      throw e;
    }
    jobListeners.forEach((f) => f(job));
  }
  if (job.status === "done") return job.result;
  if (job.status === "cancelled") throw new ApiError("작업을 취소했습니다.", "", 499);
  throw new ApiError(job.error || "작업이 실패했습니다.", job.hint || "");
}

export const cancelJob = (id) => api.post(`/api/jobs/${id}/cancel`);

export function mediaUrl(pid, path, bust = "") {
  return `/api/projects/${encodeURIComponent(pid)}/media?path=${encodeURIComponent(path)}${bust ? `&v=${encodeURIComponent(bust)}` : ""}`;
}
