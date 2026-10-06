import { api, runJob } from "../api.js";
import { state } from "../state.js";
import { h, clear, section, field, bindInput, select, toast, showError, confirmBox } from "../ui.js";
import { usageText } from "../aiflow.js";

export async function renderSettings(page, reload) {
  const r = await api.get("/api/settings");
  state.settings = r;
  const s = r.settings;
  const ai = s.ai;
  const sec = r.secrets;
  page.append(h("div", { class: "page-head" }, h("h1", {}, "설정"),
    h("div", { class: "muted" }, `설정 파일: ${r.config_dir} · 프로젝트 데이터: ${r.data_dir}`)));

  const saveSettings = async (quiet = false) => {
    try {
      const out = await api.put("/api/settings", { settings: s });
      state.settings = { ...state.settings, settings: out.settings, secrets: out.secrets };
      if (!quiet) toast("설정을 저장했습니다.", "ok");
      reload && reload();
    } catch (e) { showError(e); }
  };

  // AI 연결
  const testBox = h("div", {});
  const keyIn = h("input", { type: "password", class: "full", placeholder: sec.ai_api_key ? "저장됨 — 바꾸려면 새 키 입력" : "API 키 입력", autocomplete: "off" });
  const hdrIn = h("textarea", { class: "full", rows: 3, placeholder: sec.ai_extra_header_names.length ? `저장된 헤더: ${sec.ai_extra_header_names.join(", ")} — 바꾸려면 전체를 다시 입력` : "이름: 값 (한 줄에 하나)\n예) X-Relay-Project: shorts" });
  const customName = field("인증 헤더 이름", bindInput(ai, "custom_header_name", null, { placeholder: "예: X-Relay-Key" }));
  customName.style.display = ai.auth === "custom" ? "" : "none";

  const modelList = h("datalist", { id: "relay-models" },
    ["claude-opus-4-8", "claude-sonnet-5", "claude-fable-5", "claude-haiku-4-5-20251001"].map((m) => h("option", { value: m })));
  const envBox = sec.ai_key_source?.startsWith(".env")
    ? h("div", { class: "note ok" }, h("b", {}, "API 키: .env 파일에서 읽는 중 "), `(${sec.ai_key_source}, ${sec.ai_env_auth === "bearer" ? "Authorization: Bearer" : "x-api-key"}로 전송)`,
        h("div", { class: "small" }, `파일 위치: ${sec.env_file} — 키를 바꾸려면 이 파일을 메모장으로 고치세요. 아래 '인증 방식'과 화면에서 저장한 키보다 .env가 우선합니다.`))
    : h("div", { class: "note warn" }, h("b", {}, ".env에 API 키가 없습니다. "),
        sec.env_file_exists ? `${sec.env_file} 를 메모장으로 열어 ANTHROPIC_AUTH_TOKEN= 뒤에 키를 넣고 저장하세요.` : `프로그램 폴더의 .env.example 을 복사해 .env 로 저장한 뒤 ANTHROPIC_AUTH_TOKEN= 뒤에 키를 넣으세요. (${sec.env_file})`,
        h("div", { class: "small" }, "저장 후 [연결 테스트]를 누르면 됩니다(프로그램 재시작 불필요)."));
  page.append(section("AI 연결 (앤트로픽 API 중개서버)",
    h("div", { class: "note" }, "요청 규격: Anthropic Messages (POST {서버 주소}{경로}, content 블록 응답). 사용자 확인에 따라 이 규격만 지원합니다. OpenAI 호환 형식으로 응답하면 오류로 알려 드립니다."),
    envBox, modelList,
    h("div", { class: "grid2" },
      field("서버 기본 주소", bindInput(ai, "base_url", null, { placeholder: "https://... (중개서버 주소)" })),
      field("요청 경로", bindInput(ai, "path", null, { placeholder: "/v1/messages" }))),
    h("div", { class: "grid3" },
      field("모델 식별자", (() => { const el = bindInput(ai, "model", null, { placeholder: "중개서버에서 쓰는 모델 이름" }); el.setAttribute("list", "relay-models"); return el; })(), "목록에서 고르거나 직접 입력. 비워 두면 .env의 ANTHROPIC_MODEL을 씁니다."),
      field("인증 방식", select([["x-api-key", "x-api-key 헤더"], ["bearer", "Authorization: Bearer"], ["custom", "사용자 지정 헤더"], ["none", "인증 없음"]], ai.auth, (v) => { ai.auth = v; customName.style.display = v === "custom" ? "" : "none"; })),
      customName),
    h("div", { class: "grid3" },
      field("anthropic-version 헤더", bindInput(ai, "anthropic_version", null)),
      field("버전 헤더 보내기", select([["true", "보냄"], ["false", "보내지 않음"]], String(ai.send_version_header), (v) => (ai.send_version_header = v === "true"))),
      field("스트리밍", select([["false", "사용 안 함"], ["true", "사용(긴 응답에 유리, 서버 지원 필요)"]], String(ai.stream), (v) => (ai.stream = v === "true")))),
    h("div", { class: "grid3" },
      field("최대 출력 토큰", bindInput(ai, "max_tokens", null, { type: "number" }), "대본이 잘리면 늘리세요."),
      field("시간 제한(초)", bindInput(ai, "timeout_s", null, { type: "number" })),
      field("단가 통화", bindInput(ai, "price_currency", null))),
    h("div", { class: "grid2" },
      field("입력 단가(100만 토큰당, 선택)", bindInput(ai, "price_input_per_mtok", null, { type: "number" }), "입력하면 서버가 보고한 토큰 수로 비용을 '추정'해 표시합니다."),
      field("출력 단가(100만 토큰당, 선택)", bindInput(ai, "price_output_per_mtok", null, { type: "number" }))),
    field("추가 요청 본문(JSON, 선택)", bindInput(ai, "extra_body_json", null, { multiline: true, rows: 3, placeholder: '예) {"thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}}  — 모델이 지원하는 경우에만' }),
      "messages·model·system은 덮어쓸 수 없습니다."),
    h("div", { class: "row" }, h("button", { class: "primary", onclick: () => saveSettings() }, "연결 설정 저장")),
    h("hr"),
    h("details", {}, h("summary", {}, ".env 대신 화면에서 키 저장(선택)"),
    field(`API 키 (${sec.stored_ai_api_key ? "저장됨" : "없음"}${sec.encrypted ? " · Windows 계정으로 암호화 저장" : ""})`, keyIn,
      ".env에 키가 있으면 .env가 우선합니다. 키는 화면·프로젝트·내보내기 파일·로그에 들어가지 않습니다."),
    h("div", { class: "row" },
      h("button", { onclick: async () => {
        if (!keyIn.value.trim()) { toast("키를 입력하세요.", "info"); return; }
        try { const o = await api.put("/api/secrets", { ai_api_key: keyIn.value.trim() }); keyIn.value = ""; state.settings.secrets = o.secrets; toast("API 키를 저장했습니다.", "ok"); reload && reload(); } catch (e) { showError(e); }
      } }, "키 저장"),
      sec.stored_ai_api_key ? h("button", { class: "danger", onclick: async () => {
        if (!(await confirmBox("키 삭제", "저장된 API 키를 지웁니다.", "삭제", true))) return;
        const o = await api.put("/api/secrets", { ai_api_key: "" }); state.settings.secrets = o.secrets; toast("키를 지웠습니다.", "ok"); reload && reload();
      } }, "키 삭제") : null)),
    field("추가 헤더(중개서버가 요구하는 경우, 비밀값으로 저장)", hdrIn),
    h("div", { class: "row" }, h("button", { onclick: async () => {
      const obj = {};
      for (const line of hdrIn.value.split("\n")) {
        const i = line.indexOf(":");
        if (i > 0) obj[line.slice(0, i).trim()] = line.slice(i + 1).trim();
      }
      try { const o = await api.put("/api/secrets", { ai_extra_headers: obj }); state.settings.secrets = o.secrets; hdrIn.value = ""; toast(Object.keys(obj).length ? "추가 헤더를 저장했습니다." : "추가 헤더를 지웠습니다.", "ok"); reload && reload(); } catch (e) { showError(e); }
    } }, "추가 헤더 저장(비우고 저장하면 삭제)")),
    h("hr"),
    h("div", { class: "row" }, h("button", { class: "primary", onclick: async () => {
      await saveSettings(true);
      clear(testBox).append(h("div", { class: "muted" }, "테스트 중… (아주 짧은 요청 1회를 보냅니다)"));
      try {
        const t = await runJob(api.post("/api/ai/test"));
        clear(testBox).append(h("div", { class: "note ok" },
          h("div", {}, h("b", {}, "연결 성공 "), `(${t.format})`),
          h("div", {}, `응답: ${t.reply}`), h("div", {}, `서버가 보고한 모델: ${t.model || "-"}`),
          h("div", {}, usageText({ usage: t.usage, cost: t.cost, latency_s: t.latency_s }))));
      } catch (e) {
        clear(testBox).append(h("div", { class: "note warn" }, h("b", {}, "연결 실패: "), e.message, e.hint ? h("div", { class: "small" }, "해결 방법: " + e.hint) : null));
      }
    } }, "연결 테스트")),
    testBox));

  // 소스 사이트 키
  const pxIn = h("input", { type: "password", class: "full", autocomplete: "off", placeholder: sec.pexels_key ? "저장됨" : "Pexels API 키" });
  const pbIn = h("input", { type: "password", class: "full", autocomplete: "off", placeholder: sec.pixabay_key ? "저장됨" : "Pixabay API 키" });
  page.append(section("영상소스 검색 키 (선택)",
    h("div", { class: "muted small" }, "Pexels(pexels.com/api)와 Pixabay(pixabay.com/api/docs)의 공식 무료 API 키입니다. 없어도 내 파일은 넣을 수 있습니다."),
    h("div", { class: "grid2" }, field(`Pexels (${sec.pexels_key ? "저장됨" : "없음"})`, pxIn), field(`Pixabay (${sec.pixabay_key ? "저장됨" : "없음"})`, pbIn)),
    h("button", { onclick: async () => {
      const body = {};
      if (pxIn.value.trim()) body.pexels_key = pxIn.value.trim();
      if (pbIn.value.trim()) body.pixabay_key = pbIn.value.trim();
      if (!Object.keys(body).length) { toast("새 키를 입력하세요.", "info"); return; }
      try { await api.put("/api/secrets", body); toast("저장했습니다.", "ok"); reload && reload(); } catch (e) { showError(e); }
    } }, "키 저장")));

  // 자막·음성 인식
  const sub = s.subtitle;
  page.append(section("자막 기본값",
    h("div", { class: "grid3" },
      field("한 줄 최대 글자", bindInput(sub, "max_line_chars", null, { type: "number" })),
      field("최대 줄 수", bindInput(sub, "max_lines", null, { type: "number" })),
      field("최소 표시 시간(초)", bindInput(sub, "min_cue_s", null, { type: "number" }))),
    h("div", { class: "grid3" },
      field("초당 최대 글자", bindInput(sub, "max_cps", null, { type: "number" })),
      field("이 시간보다 짧은 쉼은 자막을 이어 표시(초)", bindInput(sub, "fill_gaps_under_s", null, { type: "number" })),
      field("SRT에 BOM 넣기", select([["false", "넣지 않음(UTF-8)"], ["true", "넣음(UTF-8 BOM)"]], String(sub.srt_bom), (v) => (sub.srt_bom = v === "true")))),
    field("음성 인식 모델 크기(선택 기능)", select([["base", "base (약 150MB, 빠름)"], ["small", "small (약 480MB, 권장)"], ["medium", "medium (약 1.5GB, 느림)"]], s.whisper.model_size, (v) => (s.whisper.model_size = v))),
    h("button", { class: "primary", onclick: () => saveSettings() }, "저장")));
}
