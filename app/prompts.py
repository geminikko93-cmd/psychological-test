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
12. 動物や物を選んだというだけで、その人の実際の性格・習慣を断定しない。選ぶ状況と結果が自然につながるようにする。
13. 結果を見せるまでに不要な待ち時間を作らない。締めの行動提案は意味がある時だけ入れる。
14. 企画・台本段階の秒数は推定であり、最終の長さは実際の Typecast 音声で決まる。
15. 結果は創作の娯楽としての解釈や生活の共感として書く。物を一つ選んだことを理由に、実際の性格・精神状態・健康を判定しない。
   「本性を正確に見抜く」「絶対当たる」などの文言や、根拠のない心理学の説明を書かない。
16. 質問・選択肢(A/B/Cなど)・結果の文字は編集で字幕として入れる。映像の中に文字を描かせる前提にしない。
   音なしで見ても質問・選択・結果が分かる構成にする。

出力は指定された JSON オブジェクトのみ。前置き・後書き・コードフェンスは付けない。"""


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


def history_block(history: list[dict]) -> str:
    if not history:
        return "(過去作品なし)"
    return _json(history)


CONTENT_TYPES = ["選択型の問いかけ", "観察・あるある共感", "短い物語・場面", "比較・対決", "想像シナリオ",
                 "クイズ・推理", "小さな実験・やってみる", "事実の発見(根拠必要)"]
VIEWER_ACTIONS = ["選ぶ", "思い出す", "当てる・推理する", "共感する", "想像する", "やってみる", "コメントで答える"]
ENDING_TYPES = ["結果公開", "どんでん返し", "問いを残す", "小さな行動提案", "オチ・笑い", "次への引き"]

CANDIDATE_SCHEMA = """{
   "id": "A",
   "core_idea_ko": "핵심 아이디어 한 줄",
   "content_type": "콘텐츠 종류(아래 목록 중 하나 또는 새 이름)",
   "viewer_action": "시청자가 영상 중에 실제로 하는 행동",
   "ending_type": "결말 유형",
   "approach_name_ko": "접근 방식 이름",
   "format_ko": "전개 방식 설명(2~3문장)",
   "content_kind": "entertainment | factual",
   "why_watch_ko": "시청자가 볼 이유",
   "first_scene_ko": "첫 장면 설명",
   "first_line_ja": "첫 문장(일본어)",
   "first_line_ko": "첫 문장의 한국어 의미",
   "structure_ko": ["장면 1: ...", "장면 2: ..."],
   "scene_count": 4,
   "choices": {"use": false, "count": null, "reason_ko": "선택지를 쓰는/안 쓰는 이유와 개수 판단"},
   "payoff_ko": "결말·결과의 재미(구체적으로)",
   "ending_ko": "마지막 장면과 마무리(행동 제안은 의미가 있을 때만)",
   "next_video_pull_ko": "다음 영상도 보고 싶게 만드는 요소(업로드 약속 표현 없이)",
   "visuals_ko": ["필요한 시각 자료"],
   "production_load_ko": "제작 부담(소스·편집 난이도, 낮음/보통/높음과 이유)",
   "source_difficulty_ko": "소스 확보 난이도(AI 추정)",
   "claims_ko": "주장 수준과 근거 필요 여부",
   "risks_ko": "주의점",
   "differs_from_ko": "다른 후보와 무엇이 실질적으로 다른지(시청자 행동·전개 순서·결말·시각 자료 기준)",
   "overlap_check_ko": "과거 작품과 겹치지 않게 한 점",
   "estimated_seconds": 35
  }"""


def _production_block(production: dict | None) -> str:
    if not production:
        return ""
    return f"\n\n## 映像の制作方式(制作者が選択)\n{_json(production)}\n(この方式で作りやすい場面・視覚素材にすること)"


def plan_prompt(idea: dict, history: list[dict], feedback: str = "", previous_candidates: list | None = None,
                production: dict | None = None) -> str:
    prev = ""
    if previous_candidates:
        prev = ("\n\n前回提案した候補(これらと異なる方向を出すこと。ただしフィードバックがあればそれを優先):\n"
                + _json([{k: c.get(k) for k in ("core_idea_ko", "content_type", "viewer_action", "ending_type", "first_line_ja")}
                         for c in previous_candidates]))
    fb = f"\n\n制作者からのフィードバック(韓国語):\n{feedback}" if feedback.strip() else ""
    return f"""次のアイデアについて、ショート動画の企画方向を3案提案してください。

## 制作者の入力(韓国語の場合あり)
{_json(idea)}{_production_block(production)}

## 過去作品の要約(重複を避けるための参考)
{history_block(history)}{prev}{fb}

## 候補の分け方(最重要)
- 3案は「表現違い」ではなく「視聴者の体験が違う」案にする。次の軸で比べたとき、どの2案も少なくとも2つの軸で異なること:
  1) content_type(コンテンツの種類) 例: {", ".join(CONTENT_TYPES)}
  2) viewer_action(視聴者が動画の間に実際にすること) 例: {", ".join(VIEWER_ACTIONS)}
  3) 展開の順番(structure_ko)
  4) ending_type(結末の種類) 例: {", ".join(ENDING_TYPES)}
  5) 必要な視覚素材
- 同じ質問で動物・物・結果の言い回しだけを変えた案は「同じ案」とみなす。そのような案を出さない。
- チャンネルの方向性は守るが、毎回「質問→選択→結果」の型に固定しない。テーマに合えば選択肢を使わない案も検討する。
- 各案の differs_from_ko に、他の2案と具体的に何が違うかを書く。

## 内容の原則
- 創作の娯楽の問いを科学的な性格診断のように見せない。
- 動物や物を選んだというだけで、その人の実際の性格・習慣を断定しない(「〜かもしれない」「〜な見方もできる」程度に)。
- 選ぶ状況と結果が自然につながるようにする(なぜその選択からその結果になるのかが伝わること)。
- 結果を見せるまでに不要な待ち時間を作らない。
- 締めの行動提案は意味がある時だけ入れる。
- 秒数は企画段階の推定であり、最終の長さは実際の Typecast 音声で決まる。
- 目標尺: {idea.get('target_seconds', 35)}秒前後(推定)。

## 出力 JSON 形式
{{
 "candidates": [
  {CANDIDATE_SCHEMA}
 ],
 "comparison_ko": "세 안이 시청자 경험(행동·전개·결말·시각 자료) 면에서 어떻게 다른지 요약"
}}"""


def plan_one_prompt(idea: dict, history: list[dict], others: list[dict], target_id: str, reason: str,
                    instruction: str, production: dict | None = None) -> str:
    ins = f"\n\n## 制作者の追加指示(韓国語)\n{instruction}" if instruction.strip() else ""
    return f"""ショート動画の企画候補のうち1案だけを作り直してください。

## 制作者の入力
{_json(idea)}{_production_block(production)}

## 残す他の候補(これらと視聴者の体験が違う案にすること)
{_json(others)}

## 作り直す理由(自動比較の結果、韓国語)
{reason}{ins}

## 過去作品の要約
{history_block(history)}

## ルール
- content_type・viewer_action・展開の順番・ending_type・視覚素材のうち、少なくとも2つで他のどの候補とも異なること。
- 同じ質問で選択肢や結果の言い回しだけ変えた案は不可。
- 娯楽の問いを科学的診断のように見せない。選んだ物だけで性格・習慣を断定しない。結果まで不要な待ちを作らない。
- id は "{target_id}" のまま。

## 出力 JSON 形式
{{"candidate": {CANDIDATE_SCHEMA}}}"""


def script_prompt(idea: dict, plan: dict, history: list[dict], notes: str = "", production: dict | None = None) -> str:
    extra = f"\n\n## 制作者の追加指示(韓国語)\n{notes}" if notes.strip() else ""
    return f"""選ばれた企画をもとに、日本語ナレーション台本を作ってください。

## アイデア
{_json(idea)}

## 選ばれた企画(制作者が編集済みの場合あり。これを最優先)
{_json(plan)}{_production_block(production)}

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
                instruction: str, only_ids: list[str] | None, fixed: dict | None = None) -> str:
    """AI는 클립별 '가변 부분'만 쓴다. 화풍·등장 요소의 고정 묘사는 프로그램이 저장된 원문을 직접 끼워 넣는다."""
    target = f"\n\n## 今回プロンプトを書くクリップ\n{_json(only_ids)}\n(これ以外のクリップは文脈として参照のみ。出力しない)" if only_ids else ""
    ins = f"\n\n## 制作者の追加指示(韓国語)\n{instruction}" if instruction.strip() else ""
    fixed = fixed or {}
    has_fixed_style = bool(fixed.get("style_locked_text"))
    from .flow import ALLOWED, ASPECT, MODEL_LABEL
    durs = "/".join(str(d) for d in ALLOWED)
    style_rule = ("- 画風は制作者が確定済み(下の固定スタイル)。style_bible は出力しない(出力しても無視される)。"
                  if has_fixed_style else
                  "- 画風が未確定のため、style_bible に全クリップ共通のスタイル案を書く(既存スタイルガイドがあれば維持)。")
    return f"""Google Flow の動画生成モデル {MODEL_LABEL} に入力するプロンプトを作ってください。
制作者は各クリップを Flow で生成し、CapCut でナレーション(Typecast音声)と字幕を重ねて編集します。

## 重要: 固定部分と可変部分の分離
最終プロンプトはプログラムが次の順で組み立てます:
  [登場要素の固定描写(保存済み原文)] + [場所・共通スタイル(保存済み原文)] + [あなたが書く可変部分 var_en] + [共通の制約]
- あなたが書くのは var_en(そのクリップの行動・構図・カメラの動き・時間配分)だけ。
- 登場要素(人物・キャラクター・物・場所)の外見・色・服装・形・素材を var_en に書き直さない。名前(name)で呼ぶだけにする。
- 「same person」「same as previous scene」のような表現で外見を代用しない(外見はプログラムが毎回挿入する)。
- そのクリップに実際に映る要素の id だけを element_ids に入れる。全要素を全クリップに入れない。
{style_rule}
- 意図的に服装・場所・照明を変える場合は var_en に書かず intended_change_en / intended_change_ko に書く。

## アイデア
{_json(idea)}

## 企画
{_json(plan)}

## 台本(シーンごと、表示用日本語と韓国語の意味)
{_json(scenes)}

## 登場要素(制作者が定義。固定描写は参照のみ・書き換え禁止)
{_json(fixed.get("elements") or [])}

## 固定スタイル(参照のみ)
{fixed.get("style_locked_text") or "(未確定)"}

## 構成テンプレート(推奨、変更可)
{_json(fixed.get("structure") or {})}

## クリップ計画(各クリップは最終音声のこの区間に置く。duration は Flow で選ぶ長さ: {durs}秒)
{_json(clips)}

## 既存スタイルガイド(画風未確定時のみ)
{_json(style or {})}{target}{ins}

## var_en の書き方
- 英語。構成: 動作 → 構図(画角) → カメラの動き → 時間配分。例: "0-2s: ..., 2-5s: ..."(duration 秒に収める)。
- 縦長 {ASPECT} の構図(主題を画面中央〜上2/3に。下部は字幕と UI で隠れる)。
- 画面内に文字・字幕・ロゴ・看板の文字を描かせない。日本語・韓国語の文字を var_en に入れない。質問・選択肢(A/B/C)・結果の文字は CapCut で字幕として入れる。
- 話す人物・口の動き・セリフは入れない(ナレーションは別)。
- 選択肢の画面(role=choice)では: 選択中に物の形・位置・数を変えない、選択肢を同じくらいの大きさと見やすさで見せる、特定の選択肢だけを強調する動きや照明をしない。必要なら静止画を推奨(gen_mode=still)。
- 動きは雰囲気のため(カーテンの小さな揺れ、窓の外の雨、弱い光の変化、ゆっくりしたカメラ移動)。
- 質問・選択・結果ごとに必ず新しい動画を作らない。確定画像の静止表示(still)や他クリップの素材の再利用(reuse)を積極的に提案してよい。
- 実在の人物・有名人・商標・特定ブランド・特定の作家や既存キャラクターの画風を描写しない。子どもを主役にしない。
- 写実的な人物を使う場合はリスクとして韓国語で知らせる。
- gen_mode: "text" / "ingredients"(登場要素の基準画像を参照) / "first_frame"(開始画面の画像から続ける) / "first_last" / "still"(確定画像を静止表示、Flow生成なし) / "reuse"(他クリップの素材を再利用、Flow生成なし)。
  continues_previous: 直前クリップの動作を連続させる場合だけ true(その時は実際の使用区間の終わりのフレームを開始フレームにする)。それ以外は false(元の基準画像から新しく始める)。
- var_ko は var_en の自然な韓国語訳、beats_ko は秒単位の展開を韓国語で。

## 出力 JSON 形式
{{
 "style_bible": {{"look_en": "", "look_ko": "", "recurring_ko": [], "palette_ko": "", "avoid_en": ""}},
 "clips": [
  {{"clip_id": "既存のclip id", "element_ids": ["実際に映る要素のid"], "role": "intro | choice | think | result | ending | other",
    "var_en": "0-3s: ...", "var_ko": "", "beats_ko": "0~3초: ... / 3~6초: ...",
    "gen_mode": "text | ingredients | first_frame | first_last | still | reuse", "continues_previous": false,
    "intended_change_en": "", "intended_change_ko": "", "mode_note_ko": "", "risk_ko": ""}}
 ]
}}"""


def elements_prompt(idea: dict, plan: dict, script_view: dict, existing: list[dict], style_text: str, instruction: str) -> str:
    ins = f"\n\n## 制作者の追加指示(韓国語)\n{instruction}" if instruction.strip() else ""
    return f"""台本から、複数のクリップに繰り返し登場する要素(人物・キャラクター・物・場所)を抽出し、外見を固定するための下書きを作ってください。
これは下書きで、制作者が修正・確定します。

## アイデア
{_json(idea)}

## 企画
{_json(plan)}

## 台本
{_json(script_view)}

## 既に定義済みの要素(確定済みのものは変更提案しない。同じ物を重複して作らない)
{_json(existing)}

## プロジェクトの画風(参照)
{style_text or "(未確定)"}{ins}

## ルール
- 2つ以上の場面に出る、または選択肢・結果で同じ見た目を保つ必要がある要素だけ。
- type: person(人物) / character(キャラクター) / object(物) / place(場所)。チャンネルは物・空間中心なので人物は必要な場合だけ。
- fixed_en: 外見を固定する英語の描写(1〜3文)。形・色・素材・部品・配置など具体的に。曖昧な語(nice, beautiful)は避ける。
- features: type ごとの英語の短い記述。
  person: face, hair, body, accessories, outfit / character: silhouette, proportions, face, pattern, colors /
  object: shape, color, material, parts, unique / place: layout, furniture, openings
- must_keep_en: 変えてはいけない特徴(英語、短い語句の配列)。
- 実在の人物・ブランド・既存キャラクター・特定作家の画風を使わない。
- desc_ko と reason_ko は韓国語。

## 出力 JSON 形式
{{
 "elements": [
  {{"name": "Window", "type": "object", "desc_ko": "", "fixed_en": "", "features": {{}}, "must_keep_en": [],
    "appears_in_scene_ids": ["既存 scene id"], "reason_ko": ""}}
 ],
 "notes_ko": ""
}}"""
