// 영상 일관성 공용 도우미: 프리셋, 서버 점검(최종 프롬프트 조립·충돌·검수 상태), 이미지 올리기, 프레임 찾기.
// 최종 프롬프트 조립·충돌 검사·검수 상태 계산은 서버(app/visual.py) 한 곳에서만 한다.
import { api, mediaUrl } from "./api.js";
import { state, changed, flush } from "./state.js";
import { h, toast, showError } from "./ui.js";

let PRESETS = null;
export async function presets() {
  if (!PRESETS) PRESETS = await api.get("/api/presets");
  return PRESETS;
}

export const ELEMENT_TYPES = { person: "인물", character: "캐릭터", object: "사물", place: "장소" };
export const FEATURE_KEYS = {
  person: [["face", "얼굴"], ["hair", "머리"], ["body", "체형"], ["accessories", "안경·액세서리"], ["outfit", "의상(색상·소재·형태)"]],
  character: [["silhouette", "실루엣"], ["proportions", "비율"], ["face", "얼굴 구조"], ["pattern", "무늬"], ["colors", "색상"]],
  object: [["shape", "형태"], ["color", "색상"], ["material", "재질"], ["parts", "부품"], ["unique", "고유 특징"]],
  place: [["layout", "공간 구조"], ["furniture", "가구·물건 배치"], ["openings", "창문·문 위치"]],
};
export const GEN_MODES = {
  text: "텍스트로 영상", ingredients: "재료(참조 이미지)", first_frame: "첫 프레임 이미지", first_last: "첫·마지막 프레임",
  still: "확정 이미지 정지 화면(생성 없음)", reuse: "다른 클립 소재 재사용(생성 없음)",
};
export const ROLES = { intro: "도입", choice: "선택지 화면", think: "고르는 시간", result: "결과", ending: "마무리", other: "기타" };
export const CHECK_ITEMS = [["face", "얼굴·캐릭터 형태"], ["outfit", "의상·액세서리"], ["object", "사물의 형태·색상·개수"],
  ["layout", "배경 배치"], ["continuity", "장면 연결"], ["choice_match", "선택 화면과 결과 화면의 소재 일치"]];

export function vis(p = state.project) {
  p.visual = p.visual || {};
  const v = p.visual;
  v.elements = v.elements || [];
  v.assets = v.assets || [];
  v.frames = v.frames || [];
  return v;
}

export const elById = (p, id) => vis(p).elements.find((e) => e.id === id);
export const assetById = (p, id) => vis(p).assets.find((a) => a.id === id);
export const clipLabel = (c) => c ? `S${c.scene_no}-C${c.index_in_scene}` : "(없음)";

// 서버 점검: 화면의 현재(저장 전 포함) 프로젝트로 계산. 결과는 저장하지 않는다.
export async function inspect(p = state.project) {
  const r = await api.post("/api/flow/inspect", { project: p });
  return r.clips;
}

// 자동 조립 클립의 최종 프롬프트를 최신 고정 블록으로 맞춘다(직접 수정한 프롬프트·예전 방식 프롬프트는 건드리지 않음).
export function syncComposed(p, insp) {
  let n = 0;
  for (const c of p.flow?.clips || []) {
    const r = insp[c.id];
    if (r && r.prompt_source === "composed" && r.out_of_date) { c.prompt_en = r.composed_en; n++; }
  }
  if (n) changed();
  return n;
}

export async function uploadImages(files, { kind = "reference", element_id = "", clip_id = "", note = "" } = {}) {
  const p = state.project;
  if (!files?.length) { toast("이미지 파일을 선택하세요.", "info"); return []; }
  await flush();
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("kind", kind); fd.append("element_id", element_id); fd.append("clip_id", clip_id); fd.append("note", note);
  try {
    const r = await api.upload(`/api/projects/${p.id}/visual/upload`, fd);
    for (const e of r.errors) toast(`${e.name}: ${e.error}`, "error", e.hint || "");
    vis(p).assets.push(...r.assets);
    if (r.assets.length) changed(true);
    return r.assets;
  } catch (e) { showError(e); return []; }
}

// 실제 사용 구간 프레임 추출(서버). 기록을 visual.frames에 추가(예전 기록·파일은 지우지 않음).
export async function extractFrames(clipId, kinds) {
  const p = state.project;
  if (!(await flush())) { toast("저장되지 않은 수정이 있어 추출하지 않았습니다.", "error"); return []; }
  try {
    const r = await api.post(`/api/projects/${p.id}/flow/frames`, { clip_id: clipId, kinds });
    vis(p).frames.push(...r.frames);
    changed(true);
    return r.frames;
  } catch (e) { showError(e); return []; }
}

// 이 클립의 현재 사용 구간과 같은 기준으로 뽑은 최신 프레임(없으면 null)
export function latestFrame(p, clipId, kind, basis) {
  const fr = vis(p).frames.filter((f) => f.clip_id === clipId && f.kind === kind);
  const ok = basis ? fr.filter((f) => f.item_sig === basis.item_sig && Math.abs(f.use_start - basis.use_start) < 1e-3 && Math.abs(f.use_len - basis.use_len) < 1e-3) : fr;
  return ok[ok.length - 1] || null;
}

export function thumb(p, file, label = "", cls = "thumb") {
  if (!file) return h("div", { class: `${cls} empty-thumb` }, label || "없음");
  return h("figure", { class: cls }, h("a", { href: mediaUrl(p.id, file), target: "_blank" }, h("img", { src: mediaUrl(p.id, file), loading: "lazy" })),
    label ? h("figcaption", {}, label) : null);
}
