// 시험용: 화면의 자막 나누기·합치기·삭제·문구 수정 코드(static/js/subsrc.js)를 Node에서 그대로 실행
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const root = new URL("..", import.meta.url);
const m = await import(new URL("static/js/subsrc.js", root).href);
const input = JSON.parse(readFileSync(0, "utf-8"));
let cues = input.cues;
let removed = input.removed || [];
for (const op of input.ops) {
  const i = op.index;
  if (op.op === "split") { const r = m.splitCueData(cues[i], op.pos); if (!r) throw new Error("split failed"); cues.splice(i, 1, r[0], r[1]); }
  else if (op.op === "merge") cues.splice(i, 2, m.mergeCueData(cues[i], cues[i + 1]));
  else if (op.op === "delete") { removed = removed.concat(m.removedFromCue(cues[i])); cues.splice(i, 1); }
  else if (op.op === "edit") { cues[i] = { ...cues[i], text: op.text, manual_text: true }; }
}
process.stdout.write(JSON.stringify({ cues, removed }));
void pathToFileURL;
