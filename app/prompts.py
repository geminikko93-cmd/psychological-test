"""AI 작업별 지시문.

고정 문구에 단어만 바꾸는 템플릿이 아니라, AI가 주제·시청자 상황에 맞춰 구성 자체를 판단하게 한다.
출력은 프로그램이 처리할 수 있도록 JSON으로 받되, 내용과 구성(장면 수·선택지 유무 등)은 고정하지 않는다.
"""
from __future__ import annotations

import json

SYSTEM = """あなたは日本語ショート動画(YouTube Shorts / Instagram Reels, 縦型9:16, 20〜60秒)の企画・構成作家です。
制作者は韓国語話者で、日本語は初級です。制作者が内容を判断できるよう、説明やメモは必ず韓国語で書き、
日本語のセリフには必ず韓国語の意味を添えてください。

制作体制:
- ナレーションは Typecast(日本語TTS) で制作者がWebサイトから生成する。
- 編集は制作者が CapCut で行う。顔出し・撮影なし。素材はストック映像/写真/イラストや制作者が用意する画像。
- 投稿は週3回程度。

守るべき原則(すべて必須):
1. 創作の娯楽コンテンツと、根拠が必要な事実コンテンツを区別する。どちらかを明示する。
2. 存在しない出典・統計・研究・心理学理論・専門家の発言を作らない。事実コンテンツで根拠が必要な箇所は
   「要出典確認」と韓国語メモで指摘し、具体的な数値を創作しない。
3. 創作の質問や遊びを、科学的な性格診断のように装わない。「3秒で本当の性格が分かる」「当たりすぎ」
   「絶対」などの誇張を既定値として使わない。
4. タイトル・台本・説明文の主張の強さを揃える(タイトルだけ強い主張をしない)。
5. 「猫を選んだからマイペース」のような予想通りの結論の繰り返しを避ける。結果や結末には
   具体性・意外性・小さな発見を入れる。
6. 選択と結果、最後の行動提案が自然につながるようにする。
7. 「毎日」「明日も」「また明日」など、投稿頻度を約束する表現を自動で入れない。
8. 日本語は自然で短い話し言葉にする。1文は原則20字前後まで。です/ます調を基本とし、混在させない。
9. 毎回同じ「質問→3択→性格結果」の型にしない。テーマに合わせて構成・場面数・選択肢の有無と数を判断する。
10. 制作者の過去作品と、質問・結果・展開・言い回しが重ならないようにする。
11. あなたの日本語チェックはネイティブ校閲ではない。不確かな表現は韓国語の注意メモで知らせる。

出力は指定された JSON オブジェクトのみ。前置き・後書き・コードフェンスは付けない。"""


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


def history_block(history: list[dict]) -> str:
    if not history:
        return "(過去作品なし)"
    return _json(history)


def plan_prompt(idea: dict, history: list[dict], feedback: str = "", previous_candidates: list | None = None) -> str:
    prev = ""
    if previous_candidates:
        prev = ("\n\n前回提案した候補(これらと異なる方向を出すこと。ただしフィードバックがあればそれを優先):\n"
                + _json([{k: c.get(k) for k in ("approach_name_ko", "format_ko", "first_line_ja")} for c in previous_candidates]))
    fb = f"\n\n制作者からのフィードバック(韓国語):\n{feedback}" if feedback.strip() else ""
    return f"""次のアイデアについて、ショート動画の企画方向を3案提案してください。

## 制作者の入力(韓国語の場合あり)
{_json(idea)}

## 過去作品の要約(重複を避けるための参考)
{history_block(history)}{prev}{fb}

## 求めること
- 3案は「表現違い」ではなく「アプローチ違い」にする。例: 問いかけ型 / 意外な事実から入る型 / 小さな物語型 /
  比較・ランキング型 / 視聴者参加型 / 観察・あるある型 など。テーマに合うものを自分で判断し、型に縛られない。
- 各案で次を判断する: 視聴者が興味を持つ理由、最初の場面と最初の一文、展開方法、必要な場面数と順番、
  選択肢が必要か(必要なら何個が適切か、不要ならなぜか)、結果や結末の面白さ、次の動画も見たくなる要素、
  必要な視覚素材とその入手しやすさ、娯楽/事実の区別と主張の強さ、リスク。
- 目標尺: {idea.get('target_seconds', 35)}秒前後。

## 出力 JSON 形式
{{
 "candidates": [
  {{
   "id": "A",
   "approach_name_ko": "접근 방식 이름",
   "format_ko": "전개 방식 설명(2~3문장)",
   "content_kind": "entertainment | factual",
   "why_watch_ko": "시청자가 볼 이유",
   "first_scene_ko": "첫 장면 설명",
   "first_line_ja": "첫 문장(일본어)",
   "first_line_ko": "첫 문장의 한국어 의미",
   "structure_ko": ["장면 1: ...", "장면 2: ..."],
   "scene_count": 5,
   "choices": {{"use": true, "count": 2, "reason_ko": "선택지를 쓰는/안 쓰는 이유와 개수 판단"}},
   "payoff_ko": "결과·결말의 재미(구체적으로)",
   "next_video_pull_ko": "다음 영상도 보고 싶게 만드는 요소(업로드 약속 표현 없이)",
   "visuals_ko": ["필요한 시각 자료"],
   "source_difficulty_ko": "소스 확보 난이도와 대안",
   "claims_ko": "주장 수준과 근거 필요 여부",
   "risks_ko": "주의점",
   "overlap_check_ko": "과거 작품과 겹치지 않게 한 점",
   "estimated_seconds": 35
  }}
 ],
 "comparison_ko": "세 안의 차이 요약"
}}"""


def script_prompt(idea: dict, plan: dict, history: list[dict], notes: str = "") -> str:
    extra = f"\n\n## 制作者の追加指示(韓国語)\n{notes}" if notes.strip() else ""
    return f"""選ばれた企画をもとに、日本語ナレーション台本を作ってください。

## アイデア
{_json(idea)}

## 選ばれた企画(制作者が編集済みの場合あり。これを最優先)
{_json(plan)}

## 過去作品の要約(質問・結果・展開・言い回しを重ねない)
{history_block(history)}{extra}

## 作り方
- 場面数・順番・選択肢の有無は企画に従いつつ、内容に最適な形にする。型に当てはめない。
- 各セリフについて:
  - display_ja: 画面に表示する日本語(字幕)。漢字かな混じりで読みやすく。
  - tts_ja: Typecast に読ませる日本語。読み間違えやすい漢字・数字・英字・固有名詞はひらがな/カタカナに開く。
    場面番号・編集指示・韓国語・記号メモを絶対に入れない。
  - ko: 自然な韓国語の意味。
  - reading_note_ko: 発音・読みのメモ(例: 「最初(さいしょ)」, 数字の読み方)。不要なら空文字。
  - caution_ko: 表現上の注意(ニュアンス、不自然かもしれない点、誇張の恐れ)。不要なら空文字。
- 視聴者が選んだり考えたりする「意図的な間」が必要な場面は hold_ms(ミリ秒)と理由を入れる。不要なら0。
- 各場面に、推奨画面内容(韓国語)、素材検索キーワード(英語と日本語)、簡単な編集意図(韓国語)を付ける。
  素材は入手しやすいもの(ストック映像・写真・テキスト画面)を優先する。
- 事実コンテンツの場合、根拠が必要な主張には fact_check_ko で「무엇을 어디서 확인해야 하는지」を書く。数値を創作しない。

## 出力 JSON 形式
{{
 "content_kind": "entertainment | factual",
 "title_candidates": [{{"ja": "", "ko": ""}}],
 "scenes": [
  {{
   "purpose_ko": "이 장면의 역할",
   "visual_ko": "권장 화면 내용",
   "search_keywords": ["english keyword", "日本語キーワード"],
   "edit_intent_ko": "편집 의도",
   "hold_ms": 0,
   "hold_reason_ko": "",
   "fact_check_ko": "",
   "lines": [
    {{"display_ja": "", "tts_ja": "", "ko": "", "reading_note_ko": "", "caution_ko": ""}}
   ]
  }}
 ],
 "claims_level_ko": "이 영상의 주장 수준(오락/사실, 단정 정도)",
 "self_check_ko": ["원칙 점검 결과: 과장·근거·약속 표현·예상 가능한 결과 반복 여부 등"]
}}"""


PARTIAL_PRESETS = {
    "hook": "첫 문장(훅)만 다시 만들기. 같은 기획 의도 안에서 접근을 바꿔 더 궁금하게.",
    "payoff": "결과·결말이 뻔하다. 예상 가능한 결론 대신 구체적이고 작은 발견이 있게 고치기.",
    "natural": "일본어를 더 자연스럽고 짧게 다듬기(의미는 유지).",
    "faster": "전개를 빠르게. 군더더기 문장·장면을 줄이고 핵심만 남기기.",
    "sources": "영상소스를 구하기 쉬운 장면으로 바꾸기(스톡 영상·사진·텍스트 화면으로 가능한 장면).",
    "overlap": "과거 작품과 겹치는 질문·결과·전개·표현을 다른 것으로 바꾸기.",
}


def partial_prompt(idea: dict, plan: dict, script_view: dict, scope: dict, instruction: str,
                   history: list[dict]) -> str:
    return f"""既存の台本の「指定範囲だけ」を修正してください。範囲外は変更しないこと。

## アイデア
{_json(idea)}

## 企画
{_json(plan)}

## 現在の台本(id付き)
{_json(script_view)}

## 修正範囲
{_json(scope)}

## 修正指示(韓国語)
{instruction}

## 過去作品の要約
{history_block(history)}

## ルール
- 範囲が line_ids ならそのセリフだけ、scene_ids ならその場面だけを変更する。範囲が "all" の場合のみ場面の追加・削除をしてよい。
- tts_ja には場面番号・編集指示・韓国語を入れない。
- 変更したセリフには必ず ko(韓国語の意味)、必要なら reading_note_ko / caution_ko を付ける。

## 出力 JSON 形式
{{
 "changes": [
  {{"op": "replace_line", "line_id": "既存id", "display_ja": "", "tts_ja": "", "ko": "", "reading_note_ko": "", "caution_ko": ""}},
  {{"op": "replace_scene", "scene_id": "既存id", "scene": {{"purpose_ko": "", "visual_ko": "", "search_keywords": [], "edit_intent_ko": "", "hold_ms": 0, "hold_reason_ko": "", "fact_check_ko": "", "lines": [{{"display_ja": "", "tts_ja": "", "ko": "", "reading_note_ko": "", "caution_ko": ""}}]}}}},
  {{"op": "insert_scene_after", "after_scene_id": "既存id または null(先頭)", "scene": {{ ... 同じ形式 ... }}}},
  {{"op": "delete_scene", "scene_id": "既存id"}}
 ],
 "explanation_ko": "무엇을 왜 바꿨는지"
}}"""


def review_prompt(idea: dict, script_view: dict, publish: dict) -> str:
    return f"""次の台本を確認し、韓国語話者の制作者が判断できるよう問題点を挙げてください。
あなたの確認はネイティブ校閲ではありません。断定せず、不確かな点はそう書いてください。

## アイデア
{_json(idea)}

## 台本(id付き)
{_json(script_view)}

## 投稿用タイトル・説明(未作成の場合あり)
{_json(publish)}

## 確認項目
- 日本語の自然さ(不自然・硬い・意味が曖昧)、です/ます の混在、文の長さ
- TTSの読み間違えが起きそうな漢字・数字・固有名詞(tts_ja の表記を提案)
- 誇張・断定・根拠のない主張・創作の出典、事実コンテンツなら要確認箇所
- 予想通りすぎる結果、選択と結果と最後の行動提案のつながり
- 投稿頻度の約束表現
- タイトル・台本・説明文の主張の強さの一致

## 出力 JSON 形式
{{
 "items": [
  {{"line_id": "対象id または null", "severity": "high | mid | low", "category_ko": "자연스러움/읽기/과장/근거/구성/일관성 등",
    "issue_ko": "문제 설명", "suggestion_display_ja": "", "suggestion_tts_ja": "", "suggestion_ko": "제안의 한국어 의미"}}
 ],
 "consistency_ko": "제목·대본·설명문 주장 수준 일치 여부",
 "overall_ko": "전체 의견"
}}"""


def publish_prompt(idea: dict, plan: dict, script_view: dict, platforms: list[str]) -> str:
    return f"""この動画の投稿用タイトルと説明文の案を作ってください。

## アイデア
{_json(idea)}

## 企画
{_json(plan)}

## 台本
{_json(script_view)}

## 投稿先
{_json(platforms)}

## ルール
- タイトル・説明文は台本と同じ強さの主張にする。台本にない主張・誇張・数値を足さない。
- 娯楽コンテンツなら説明文に「娯楽として楽しむ内容」である旨を自然な日本語で入れる。
- 投稿頻度の約束(毎日・明日も等)を入れない。
- クレジット(音声・素材の出典)はプログラムが実際の記録から自動で付けるので、あなたは書かない。出典URLを創作しない。
- ハッシュタグは5個以内、実在する一般的な日本語タグ。

## 出力 JSON 形式
{{
 "titles": [{{"ja": "", "ko": "", "note_ko": "이 제목의 의도"}}],
 "description_ja": "",
 "description_ko": "설명문의 한국어 의미",
 "hashtags": ["#..."],
 "notes_ko": "주의점"
}}"""


def flow_prompt(idea: dict, plan: dict, scenes: list[dict], clips: list[dict], style: dict | None,
                instruction: str, only_ids: list[str] | None) -> str:
    target = f"\n\n## 今回プロンプトを書くクリップ\n{_json(only_ids)}\n(これ以外のクリップは文脈として参照のみ。出力しない)" if only_ids else ""
    ins = f"\n\n## 制作者の追加指示(韓国語)\n{instruction}" if instruction.strip() else ""
    keep_style = "既存のスタイルガイドを維持し、style_bible は同じ内容で返す。" if style and style.get("look_en") else "最初にスタイルガイドを決める。"
    return f"""Google Flow の動画生成モデル Gemini Omni Flash 1.1 に入力するプロンプトを作ってください。
制作者は各クリップを Flow で生成し、CapCut でナレーション(Typecast音声)と字幕を重ねて編集します。

## アイデア
{_json(idea)}

## 企画
{_json(plan)}

## 台本(シーンごと、表示用日本語と韓国語の意味)
{_json(scenes)}

## クリップ計画(各クリップは最終音声のこの区間に置く。duration は Flow で選ぶ長さ: 4/6/8/10秒)
{_json(clips)}

## 既存スタイルガイド
{_json(style or {})}{target}{ins}

## 書き方のルール
- {keep_style} 全クリップで人物・場所・色調・画風・カメラの言語を統一し、各プロンプトにスタイルの要点を毎回含める(Flowは各生成が独立しているため)。
- prompt_en は英語。構成: 被写体 → 動作 → 場所 → カメラ(画角・動き) → 光・色 → スタイル → 時間配分。
  例: "0-2s: ..., 2-5s: ..." のように、そのクリップが覆うナレーションのタイミングに合わせて展開を書く(duration 秒に収める)。
- 縦長 9:16 の構図を前提にする(主題を画面中央〜上2/3に。下部は字幕と UI で隠れる)。
- 画面内に文字・字幕・ロゴ・透かしを入れない(字幕は CapCut で入れる)。"no on-screen text, no captions, no logos" を含める。
- ナレーションは別に入れるので、話す人物・口の動き・セリフは入れない。音は環境音のみ("no dialogue, no speech, ambient sound only")。
- 実在の人物・有名人・商標・特定ブランドを描写しない。子どもを主役にしない。
- 写実的な人物を使う場合はリスクとして韓国語で知らせる(プラットフォームのAI表示義務の可能性)。
- 台本の内容と合わない映像、誇張した演出、根拠のない事実描写をしない。
- mode: "text"(テキストのみ) / "first_frame"(前のクリップの最後のフレーム画像を最初のフレームに使うと連続性が上がる場合) / "ingredients"(同じ人物・物を複数クリップで使う場合に参照画像を使う)。理由を mode_note_ko に。
- prompt_ko は prompt_en の自然な韓国語訳、beats_ko は秒単位の展開を韓国語で。

## 出力 JSON 形式
{{
 "style_bible": {{"look_en": "全クリップ共通のスタイル記述(英語、1〜3文)", "look_ko": "한국어 설명",
   "recurring_ko": ["반복 등장 요소(인물·물건·장소)"], "palette_ko": "색감", "avoid_en": "避ける要素(英語)"}},
 "clips": [
  {{"clip_id": "既存のclip id", "prompt_en": "", "prompt_ko": "", "beats_ko": "0~2초: ... / 2~6초: ...",
    "mode": "text | first_frame | ingredients", "mode_note_ko": "", "risk_ko": ""}}
 ]
}}"""
