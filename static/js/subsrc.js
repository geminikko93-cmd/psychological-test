// 자막 원문 위치(src) 계산 — 화면(DOM)과 무관한 순수 함수(시험에서 Node로 직접 실행)
// src = [{ line_id, a, b }]: 자막이 대본 줄(화면 표시 문구, 줄바꿈 제외)의 a번째 글자부터 b번째 글자 앞까지를 덮음
export const rid = () => `c_${Math.random().toString(16).slice(2, 10)}`;
export const flatText = (t) => (t || "").replace(/\r?\n/g, "");

export function splitSrc(src, cut) {
  const a = [], b = [];
  let pos = 0;
  for (const r of src || []) {
    const len = r.b - r.a;
    if (pos + len <= cut) a.push({ ...r });
    else if (pos >= cut) b.push({ ...r });
    else { const m = r.a + (cut - pos); a.push({ ...r, b: m }); b.push({ ...r, a: m }); }
    pos += len;
  }
  return [a, b];
}

// 나누기: pos는 자막 문구(줄바꿈 포함) 안의 커서 위치. 두 조각이 원문 위치를 나눠 갖는다.
export function splitCueData(c, pos) {
  const before = c.text.slice(0, pos), after = c.text.slice(pos);
  const ta = before.replace(/\n+$/, ""), tb = after.replace(/^\n+/, "");
  const fa = flatText(ta).length, ftot = flatText(c.text).length;
  if (!fa || fa >= ftot) return null;
  const srcLen = (c.src || []).reduce((s, r) => s + (r.b - r.a), 0);
  const exact = !c.manual_text && srcLen === ftot && !c.src_approx;
  const cut = exact ? fa : Math.round((fa / ftot) * srcLen);
  const [sa, sb] = splitSrc(c.src, cut);
  const mid = +(c.start + (c.end - c.start) * (fa / ftot)).toFixed(3);
  const common = { manual_struct: true, manual_time: true, src_approx: !exact || !!c.src_approx };
  const first = { ...c, ...common, text: ta, end: mid, src: sa };
  const second = { ...c, ...common, id: rid(), text: tb, start: mid, src: sb, conf: "low",
    note: "직접 나눈 자막입니다. 나눈 시간은 글자 비율 추정이므로 재생해서 맞추세요." };
  return [first, second];
}

// 합치기: 두 자막의 원문 위치를 이어 붙인다(서로 다른 대본 줄이어도 됨).
export function mergeCueData(c, n) {
  return { ...c, text: flatText(c.text) + flatText(n.text), end: n.end, src: [...(c.src || []), ...(n.src || [])],
    manual_struct: true, manual_text: !!(c.manual_text || n.manual_text), src_approx: !!(c.src_approx || n.src_approx),
    speech_end: n.speech_end ?? n.end };
}

// 삭제: 지운 원문 위치를 기록해 다시 맞추기 때 비워 둔다.
export function removedFromCue(c) {
  return (c.src || []).map((r) => ({ ...r, text: c.text, at: new Date().toISOString() }));
}
