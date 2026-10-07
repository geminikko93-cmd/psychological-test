"""실험용 초기 추천값: 채널 방향(일본 20~30대 가설)·기획 후보·화풍 프리셋·쇼츠 구성 템플릿.

여기 값은 '검증된 흥행 공식'이 아니라 처음 시험해 볼 출발점이다.
- 기존 채널 설정·프로젝트에 자동으로 덮어쓰지 않는다. 사용자가 화면에서 고른 항목만 적용한다.
- 화풍 프리셋은 특정 작가·기존 캐릭터의 모방이 아니라 일반적인 시각 특징으로만 정의한다.
"""
from __future__ import annotations

PRESET_NOTE_KO = ("실험용 초기 추천값입니다. 일본 시청자 전체의 취향이나 검증된 흥행 공식이 아니며, "
                  "몇 편을 만들어 보며 직접 고치는 출발점으로 쓰세요.")

# ---------------------------------------------------------------- 채널 방향(일본 20~30대 실험 가설)

CHANNEL_PRESET = {
    "id": "jp_2030_calm_v1",
    "label_ko": "일본 20~30대 · 차분한 선택형 심리테스트 (실험 가설)",
    "note_ko": PRESET_NOTE_KO,
    "fields": {
        "genre": "창작 오락용 심리테스트·선택형 자기 탐색·생활 공감. 실제 성격·정신 상태·건강을 판정하지 않음",
        "audience": "(초기 가설) 20~30대 일본어 시청자. 쉬는 시간·자기 전에 가볍게 보는 층. 소리 없이 보는 경우도 많음",
        "value": "일상·휴식·취향·가벼운 인간관계 소재로 '오늘의 나'를 가볍게 돌아보는 재미. 결과는 단정 대신 창작 해석·생활 공감",
        "format": "기본 목표 35초(대체로 25~40초), 세로 9:16. 사물·공간 중심 화면(얼굴이 나오는 인물은 선택). "
                  "소리 없이 봐도 질문·선택·결과를 알 수 있게 질문·선택지·결과는 편집 자막으로. "
                  "초기에는 제작 관리가 쉬운 선택형을 기본으로 하되, 주제에 맞으면 다른 형식도 사용",
        "tone": "짧고 자연스러운 일본어, 한 영상 안에서 です/ます 등 말투 통일. 따뜻하고 차분하면서 조금 궁금해지는 분위기",
        "visual_style": "화풍 프리셋 중 하나를 프로젝트마다 확정(기본 추천: 따뜻한 그림책풍). 사물·공간 중심",
        "target_seconds": 35,
        "length_range": "25~40초",
        "description_notice_ja": "※この動画の心理テストは、娯楽として楽しむ内容です。",
    },
    "rules_add": [
        "결과는 창작 오락용 해석이나 생활 공감으로 쓰고, 물건 하나를 골랐다는 이유로 실제 성격·정신 상태·건강을 판정하지 않는다",
        "「本性を正確に見抜く」「絶対当たる」 같은 문구, 근거 없는 심리학 설명을 쓰지 않는다",
        "같은 질문에서 물건 이름만 바꾼 기획을 반복하지 않고, 질문 의도·선택 방식·결과의 관점을 바꾼다",
        "질문·선택지·결과 문자는 편집 자막으로 넣고, 영상 생성 모델에 일본어 글자를 그리게 하지 않는다",
    ],
}

# ---------------------------------------------------------------- 기획 후보(수정 가능, 주제 목록에 '추가'만 함)

PLAN_IDEAS = [
    {"id": "r01", "title_ja": "この部屋で、いちばん気になるものは？", "title_ko": "작은 방에서 눈길이 가는 것",
     "angle_ko": "관점: 지금 눈이 가는 곳 = 오늘의 기분(창작 해석). 구성: 방 전체 → 선택 → 같은 물건 확대 → 결과",
     "first_line_ja": "この部屋で、いちばん気になるものは？", "example_choices": "窓 / 時計 / 本棚",
     "caution_ko": "성격 판정 대신 '오늘은 ~한 기분일지도' 정도의 창작 해석. 선택 화면과 결과 화면은 같은 방 소재 재사용",
     "series_ko": "같은 방의 다른 계절·시간대", "style_hint": "picturebook"},
    {"id": "r02", "title_ja": "雨の日、ひと息つくならどの席？", "title_ko": "비 오는 날의 카페 자리",
     "angle_ko": "관점: 쉬는 방식의 취향. 구성: 카페 전체 → 자리 선택 → 선택한 자리의 분위기",
     "first_line_ja": "雨の日、ひと息つくならどの席？", "example_choices": "窓際 / 奥の席 / カウンター",
     "caution_ko": "자리 = 성격 단정 금지. 각 자리에서 보내는 시간의 공감으로", "series_ko": "맑은 날·밤의 카페",
     "style_hint": "miniature"},
    {"id": "r03", "title_ja": "小さな旅に、一つだけ持っていくなら？", "title_ko": "작은 여행의 소지품",
     "angle_ko": "관점: 여행에서 무엇을 남기고 싶은지. 구성: 같은 테이블 위 소품 → 선택 → 결과",
     "first_line_ja": "小さな旅に、一つだけ持っていくなら？", "example_choices": "手帳 / カメラ / 本",
     "caution_ko": "소품 3개의 형태·배치를 선택·결과 화면에서 같게 유지", "series_ko": "", "style_hint": "retro"},
    {"id": "r04", "title_ja": "友だちに贈るなら、どれを選ぶ？", "title_ko": "친구를 위한 작은 선물",
     "angle_ko": "관점: 마음을 전하는 방식에 대한 창작 해석(나 자신이 아니라 관계 쪽). 구성: 선물 선택 → 전하는 방식 해석",
     "first_line_ja": "友だちに贈るなら、どれを選ぶ？", "example_choices": "お茶 / 花 / 手紙",
     "caution_ko": "인간관계 조언·단정 대신 가벼운 공감", "series_ko": "", "style_hint": "retro"},
    {"id": "r05", "title_ja": "予定のない休日。まず何をしたい？", "title_ko": "계획 없는 휴일",
     "angle_ko": "관점: 시간을 보내는 방식에 대한 공감(선택보다 상황 공감 중심도 가능). 구성: 휴일 상황 → 선택 → 공감",
     "first_line_ja": "予定のない休日。まず何をしたい？", "example_choices": "散歩 / 読書 / 部屋の片づけ",
     "caution_ko": "'게으름' 같은 부정 평가 없이", "series_ko": "", "style_hint": "picturebook"},
]

# ---------------------------------------------------------------- 화풍 프리셋

STYLE_PRESETS = {
    "picturebook": {
        "id": "picturebook", "label_ko": "A. 따뜻한 그림책풍", "recommended": True,
        "desc_ko": "종이 질감과 부드러운 채색, 간결한 윤곽선. 아이보리·세이지 그린·갈색 중심의 차분한 일러스트로 "
                   "성인도 편하게 볼 수 있음. 움직임이 적어도 분위기가 유지됨.",
        "suits_ko": "작은 방, 서점, 창가, 휴일",
        "style_en": ("Warm picture-book illustration for adult viewers: visible paper grain, soft hand-painted coloring, "
                     "simple clean outlines, calm and quiet mood."),
        "palette": [{"name": "ivory", "hex": "#F2ECDD"}, {"name": "sage green", "hex": "#A7B49A"},
                    {"name": "warm brown", "hex": "#8A6A4B"}],
        "lighting_en": "soft diffused daylight, gentle low-contrast shadows",
        "material_en": "matte painted surfaces with paper texture",
        "line_en": "thin, simple, slightly hand-drawn outlines",
        "motion_ko": "커튼의 작은 흔들림, 창밖의 비, 약한 빛 변화, 느린 카메라 이동",
        "motion_en": "subtle ambient motion only (small curtain sway, light rain outside, slow light change, slow camera move)",
        "avoid_en": ("photorealism, glossy 3D render, thick black outlines, neon or oversaturated colors, busy patterns, "
                     "imitation of any specific artist or existing character"),
    },
    "miniature": {
        "id": "miniature", "label_ko": "B. 수공예 미니어처풍", "recommended": False,
        "desc_ko": "작은 모형 공간과 손으로 만든 듯한 나무·점토·천 질감. 단순하고 명확한 사물 형태. "
                   "선택지가 흐려지지 않도록 과도한 배경 흐림을 피함.",
        "suits_ko": "카페, 책상, 작은 가게, 소품 선택",
        "style_en": ("Handcrafted miniature diorama look: a small model space with handmade wood, clay and fabric textures, "
                     "simple and clearly readable object shapes."),
        "palette": [{"name": "light wood", "hex": "#C9A77C"}, {"name": "cream", "hex": "#EFE6D2"},
                    {"name": "muted teal", "hex": "#6F9A96"}],
        "lighting_en": "soft warm practical light as in a small tabletop set",
        "material_en": "wood grain, matte clay, woven fabric",
        "line_en": "no drawn outlines; clean physical edges",
        "motion_ko": "작은 소품의 미세한 흔들림, 김이 오르는 컵, 느린 카메라 이동",
        "motion_en": "gentle handmade-feel motion (tiny prop movement, rising steam, slow camera move)",
        "avoid_en": ("heavy tilt-shift or strong background blur that hides the options, plastic toy gloss, photoreal humans, "
                     "brand logos, imitation of any specific studio or existing character"),
    },
    "retro": {
        "id": "retro", "label_ko": "C. 차분한 레트로 일러스트풍", "recommended": False,
        "desc_ko": "정돈된 선과 절제된 인쇄 질감, 조금 바랜 따뜻한 색. 장식이 선택지보다 눈에 띄지 않도록 구성.",
        "suits_ko": "찻집, 편지, 여행, 추억",
        "style_en": ("Calm retro illustration: tidy lines, restrained print-like grain, slightly faded warm colors, "
                     "uncluttered composition."),
        "palette": [{"name": "faded mustard", "hex": "#C9A44C"}, {"name": "dusty teal", "hex": "#5E8A8A"},
                    {"name": "warm off-white", "hex": "#EFE4CF"}],
        "lighting_en": "flat soft light with gentle warm tint",
        "material_en": "subtle risograph-like print texture",
        "line_en": "tidy, even-weight lines",
        "motion_ko": "느린 패닝, 종이·커튼의 작은 흔들림, 약한 빛 변화",
        "motion_en": "minimal motion (slow pan, small paper or curtain movement, soft light change)",
        "avoid_en": ("decorative frames or patterns that draw more attention than the options, heavy halftone, "
                     "neon colors, imitation of any specific artist or existing character"),
    },
}
DEFAULT_STYLE = "picturebook"

# ---------------------------------------------------------------- 쇼츠 구성 템플릿(시간·선택지 수 수정 가능)

STRUCTURE_TEMPLATES = {
    "choice3_35": {
        "id": "choice3_35", "label_ko": "선택형 35초 (선택지 3개)", "choices": 3, "target_seconds": 35,
        "note_ko": "추천 출발점. 실제 길이는 일본어 음성·자막 시간으로 다시 맞춥니다. 결과를 다음 영상으로 미루지 않습니다.",
        "segments": [
            {"key": "intro", "role": "intro", "start": 0, "end": 3, "label_ko": "공간과 질문을 바로 보여주기"},
            {"key": "choices", "role": "choice", "start": 3, "end": 7, "label_ko": "선택지를 한 화면에서 확인(A/B/C는 편집 자막)"},
            {"key": "think", "role": "think", "start": 7, "end": 10, "label_ko": "짧게 고를 시간(정지 화면 유지 가능)"},
            {"key": "results", "role": "result", "start": 10, "end": 28, "label_ko": "결과를 선택지마다 약 6초씩"},
            {"key": "ending", "role": "ending", "start": 28, "end": 35, "label_ko": "선택지를 다시 보여주고 짧은 질문으로 마무리"},
        ],
        "choice_rules_ko": [
            "선택하는 동안 물건의 모양·위치·개수가 바뀌지 않기",
            "선택지를 비슷한 크기와 가독성으로 보여주기",
            "특정 선택지만 강조하는 움직임이나 조명을 피하기",
            "A/B/C와 질문은 편집용 자막으로 분리하기",
            "필요하면 정지 이미지를 유지하기",
        ],
    },
}

CLIP_ROLES = {"intro": "도입(공간·질문)", "choice": "선택지 화면", "think": "고르는 시간", "result": "결과",
              "ending": "마무리", "other": "기타"}


def all_presets() -> dict:
    return {"note_ko": PRESET_NOTE_KO, "channel": CHANNEL_PRESET, "ideas": PLAN_IDEAS, "styles": STYLE_PRESETS,
            "default_style": DEFAULT_STYLE, "structures": STRUCTURE_TEMPLATES, "roles": CLIP_ROLES}
