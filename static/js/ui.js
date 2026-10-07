// 화면 도우미. 사용자 입력은 항상 textContent로만 넣는다(HTML로 해석하지 않음).

// 조건부 요소(cond ? el : null)를 그대로 append해도 "null" 글자가 찍히지 않도록 빈 값은 건너뛴다.
const nativeAppend = Element.prototype.append;
Element.prototype.append = function (...nodes) {
  return nativeAppend.apply(this, nodes.flat(Infinity).filter((n) => n !== null && n !== undefined && n !== false));
};

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else if (k === "disabled") el.disabled = !!v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };

export function toast(msg, kind = "info", hint = "") {
  const box = document.getElementById("toasts");
  const t = h("div", { class: `toast ${kind}` }, h("div", { class: "toast-msg" }, msg), hint ? h("div", { class: "toast-hint" }, hint) : null);
  box.append(t);
  setTimeout(() => t.classList.add("hide"), kind === "error" ? 9000 : 4000);
  setTimeout(() => t.remove(), kind === "error" ? 9600 : 4600);
}

export function showError(e) {
  toast(e.message || String(e), "error", e.hint || "");
}

export function modal(title, body, buttons = [{ label: "닫기", value: null }], opts = {}) {
  return new Promise((resolve) => {
    const back = h("div", { class: "modal-back" });
    const close = (v) => { back.remove(); resolve(v); };
    const box = h("div", { class: `modal ${opts.wide ? "wide" : ""}` },
      h("div", { class: "modal-head" }, h("h3", {}, title), h("button", { class: "icon", title: "닫기", onclick: () => close(null) }, "✕")),
      h("div", { class: "modal-body" }, body),
      h("div", { class: "modal-foot" }, buttons.map((b) =>
        h("button", { class: b.kind || (b.primary ? "primary" : ""), onclick: () => close(typeof b.value === "function" ? b.value() : b.value) }, b.label))));
    back.append(box);
    back.addEventListener("keydown", (ev) => { if (ev.key === "Escape") close(null); });
    document.body.append(back);
    const f = box.querySelector("input, textarea, button.primary");
    if (f) f.focus();
  });
}

export async function confirmBox(title, message, okLabel = "확인", danger = false) {
  const r = await modal(title, h("p", { class: "pre" }, message),
    [{ label: "취소", value: false }, { label: okLabel, value: true, kind: danger ? "danger" : "primary" }]);
  return r === true;
}

export async function promptBox(title, label, initial = "") {
  const inp = h("input", { type: "text", value: initial, class: "full" });
  const r = await modal(title, h("label", { class: "field" }, h("span", {}, label), inp),
    [{ label: "취소", value: null }, { label: "확인", primary: true, value: () => inp.value }]);
  return r;
}

export async function copyText(text, what = "내용") {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${what}을(를) 복사했습니다.`, "ok");
  } catch {
    const ta = h("textarea", { class: "full", rows: 10 }, text);
    modal("직접 복사하세요", h("div", {}, h("p", {}, "자동 복사가 막혀 있습니다. 아래 내용을 선택해 Ctrl+C로 복사하세요."), ta));
    setTimeout(() => ta.select(), 50);
  }
}

export function fmtSec(t, ms = true) {
  if (t === null || t === undefined || isNaN(t)) return "-";
  const s = Math.max(0, t);
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}:${(ms ? r.toFixed(2) : Math.floor(r).toString()).padStart(ms ? 5 : 2, "0")}`;
}

export function fmtTime(iso) {
  if (!iso) return "-";
  try { return new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" }); } catch { return iso; }
}

export function badge(state) {
  const map = { done: ["완료", "ok"], todo: ["미완료", "todo"], stale: ["재생성 필요", "stale"], optional: ["선택", "todo"] };
  const [label, cls] = map[state] || ["-", "todo"];
  return h("span", { class: `badge ${cls}` }, label);
}

export function field(label, input, help = "") {
  return h("label", { class: "field" }, h("span", { class: "field-label" }, label), input, help ? h("small", { class: "help" }, help) : null);
}

// 값 바인딩 입력: obj[key]를 읽고 바꾸면 onChange 호출
export function bindInput(obj, key, onChange, opts = {}) {
  const tag = opts.multiline ? "textarea" : "input";
  const el = h(tag, { class: opts.class || "full", placeholder: opts.placeholder || "", rows: opts.rows || (opts.multiline ? 3 : undefined), type: opts.multiline ? undefined : (opts.type || "text") });
  el.value = obj[key] ?? "";
  el.addEventListener("input", () => {
    obj[key] = opts.type === "number" ? (el.value === "" ? null : Number(el.value)) : el.value;
    onChange && onChange(key, el);
  });
  return el;
}

export function select(options, value, onChange, cls = "") {
  const el = h("select", { class: cls }, options.map(([v, label]) => h("option", { value: v }, label)));
  el.value = value;
  el.addEventListener("change", () => onChange(el.value));
  return el;
}

export function section(title, ...children) {
  return h("section", { class: "card" }, title ? h("h2", {}, title) : null, ...children);
}

export function empty(text) {
  return h("div", { class: "empty" }, text);
}
