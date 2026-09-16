"""셀렉터 격리 구역.

네이버가 에디터를 바꾸면 여기만 고친다. 다른 파일에는 셀렉터 문자열을 두지 않는다.

각 항목은 후보 리스트다. 위에서부터 시도해서 처음 잡히는 걸 쓴다.

후보 순서 원칙 (2026-08-25 실측으로 갱신):
  1. data-click-area / data-testid / data-name / data-a11y-title
     — 네이버 자체 클릭로그·테스트용 앵커. 리뉴얼에도 잘 살아남는다.
  2. aria-label
  3. se-* 클래스 — 스마트에디터 ONE 의 의미 기반 클래스. 난독화되지 않는다.
  4. class*= 접두 매칭 — 블로그 셸의 난독화 클래스(publish_btn__m9KHH)는
     해시 부분만 도는 경우가 많아 접두만 잡으면 해시 변경에 견딘다.
  5. 난독화 클래스 전체 — 최후수단. 해시가 돌면 바로 깨진다.

텍스트 기반 셀렉터 주의:
  - :text-is() 는 "텍스트를 담은 가장 작은 요소" 에 exact 매칭한다.
    발행/저장 버튼은 <button><span>발행</span><i>…</i></button> 구조라
    button:text-is('발행') 은 0개다. (span 이 최소 요소)
  - :has-text() 는 부분 문자열이다. button:has-text('취소') 는 툴바의
    "취소선" 버튼을 잡는다. 실제로 이것 때문에 매 실행마다 본문 취소선이 켜졌다.
    텍스트 매칭은 컨테이너로 스코프를 좁힌 뒤에만 쓸 것.

[실측] = 2026-08-25 verify_selectors.py 로 확인함
[미실측] = 아직 확인 안 됨
"""

import asyncio

WRITE_URL = "https://blog.naver.com/{blog_id}?Redirect=Write"
POST_URL = "https://blog.naver.com/{blog_id}/{log_no}"

# 에디터 본체가 들어있는 iframe  [실측]
EDITOR_FRAME = ["#mainFrame", "iframe[title*='에디터']", "iframe#se2_iframe"]

# 제목 입력 영역  [실측]
# se-placeholder 는 글자가 들어가면 사라진다. 기존 글을 다시 열 때를 대비해
# 사라지지 않는 data-a11y-title 을 앞에 둔다.
TITLE = [
    "[data-a11y-title='제목']",
    ".se-documentTitle .se-text-paragraph",
    "span.se-placeholder:has-text('제목')",
]

# 제목이 비었는지 판정  [실측]
# 글자가 들어가면 se-placeholder 가 사라진다. 빈 글 발행을 막는 데 쓴다.
TITLE_PLACEHOLDER = [
    "[data-a11y-title='제목'] .se-placeholder",
    ".se-documentTitle .se-placeholder",
]

# 본문 입력 영역  [실측]
BODY = [
    "[data-a11y-title='본문']",
    ".se-component.se-text .se-text-paragraph",
    ".se-component-content .se-text-paragraph",
]

# 툴바 - 이미지 버튼  [실측]
# data-name='image' 는 2개다: 상단 툴바(보임) + 본문 삽입메뉴(숨김).
# se-image-toolbar-button 으로 상단 툴바 쪽을 명시한다.
IMAGE_BUTTON = [
    "button[data-name='image'].se-image-toolbar-button",
    "button.se-image-toolbar-button",
    "button[data-name='image']",
]

# 문서의 모든 블록 컴포넌트 (제목 포함)  [실측]
# 붙여넣기가 실제로 반영됐는지 판정할 때 쓴다. 본문 컴포넌트만 재면 안 된다 —
# 인용구/표/코드는 별도 컴포넌트가 되므로 본문 길이가 그대로여서 "무반응" 으로 오판한다.
DOC_COMPONENT = [".se-component"]

# 업로드된 이미지 컴포넌트  [실측]
# 업로드 완료 판정에 쓴다. 존재 여부가 아니라 "개수가 늘었는지" 로 봐야 한다 —
# 이미 이미지가 있는 글에서는 존재 검사가 즉시 통과해버린다.
IMAGE_COMPONENT = ["[data-a11y-title='사진']", ".se-component.se-image", ".se-image"]
# 이미지 캡션 입력란 (placeholder: "사진 설명을 입력하세요.")  [실측]
# 반드시 해당 이미지 컴포넌트 안으로 스코프를 좁혀서 쓸 것.
IMAGE_CAPTION = [".se-caption", "[class*='se-caption']"]

# ---------------------------------------------------------------- 발행된 글 삭제
# 글 페이지(#mainFrame)의 "삭제" 링크.  [실측]
# 텍스트로 찾지 말 것 — 연관글 제목에 "삭제" 가 들어가면 그것도 잡힌다.
# _deletePost 클래스로 찾고, 보이는 것만 쓴다 (숨김인 "삭제하기" 도 같은 클래스다).
# 확인은 네이티브 dialog: "삭제된 글은 복구할 수 없습니다."
# class~= 로 토큰 정확 매칭한다. class*= 는 _deletePostConfirm 까지 잡는다.
POST_DELETE = ["a[class~='_deletePost']"]

# ---------------------------------------------------------------- 인라인 서식
# 붙여넣기가 <a href> 를 지우므로 링크는 툴바로 넣어야 한다.  [실측]
# 텍스트를 선택한 상태에서: LINK_BUTTON → LINK_INPUT 에 URL → LINK_APPLY.
LINK_BUTTON = ["button[data-name='text-link']", "button.se-link-toolbar-button"]
LINK_INPUT = ["input.se-custom-layer-link-input", "input[placeholder*='URL']"]
LINK_APPLY = ["button.se-custom-layer-link-apply-button"]

# 툴바 토글 버튼이 켜져 있음을 나타내는 클래스.  [실측]
# 취소선은 선택에 적용한 뒤에도 토글이 켜진 채 남아서, 이후 타이핑에 전부 번진다.
TOGGLE_ON_CLASS = "se-is-selected"

# 에디터가 링크를 표현하는 방식.  [실측]
# <a href> 가 아니라 <span class="se-link" data-href="..."> 다. 검증할 때 헷갈리지 말 것.
LINK_APPLIED = ["span.se-link[data-href]", ".se-link"]

# 글자 크기 드롭다운.  [실측]
# 스마트에디터 ONE 에는 H1~H3 같은 다단계 제목이 없다.
# text-format 은 본문/소제목/인용구 셋뿐이고, 우리가 "제목" 이라 부르는 것은 글자 크기다.
# 붙여넣기가 만드는 크기와 맞춘다 (h3 는 본문과 같은 15라 사실상 구분이 없다).
FONT_SIZE_BUTTON = ["button[data-name='font-size']", "button.se-font-size-code-toolbar-button"]
FONT_SIZE_OPTION = ["[data-name='font-size-code'] button[data-value='{value}']"]
HEADING_SIZE = {1: "fs24", 2: "fs19", 3: "fs15"}
BODY_SIZE = "fs15"

# 취소선 토글.  [실측]
# ControlOrMeta+Shift+S 는 동작하지 않는다 (검증함). 선택 상태에서 이 버튼을 눌러야 한다.
# 굵게/기울임/밑줄은 ControlOrMeta+B/I/U 단축키가 정상 동작한다.
STRIKE_BUTTON = ["button[data-name='strikethrough']", "button.se-strikethrough-toolbar-button"]
# 서식 초기화용 토글. 켜져 있으면 TOGGLE_ON_CLASS 가 붙는다(2026-09-16 실측).
BOLD_BUTTON = ["button[data-name='bold']"]
ITALIC_BUTTON = ["button[data-name='italic']"]
UNDERLINE_BUTTON = ["button[data-name='underline']"]

# 구분선 모양 고르기(2026-09-16 실측). 옆 화살표로 메뉴를 열고 data-value 로 고른다.
# default=짧은 선, line1=긴 가는 선, line2=가운데 짧은 굵은 선, line3=가운데 V,
# line4=가운데 마름모, line5=점선, line6=사선, line7=세로선. 붙여넣은 <hr> 은 line1 이 된다.
DIVIDER_MENU = ["button[data-name='horizontal-line'].se-document-toolbar-select-option-button"]
DIVIDER_OPTION = ["button.se-toolbar-option-icon-button[data-name='horizontal-line'][data-value='{value}']"]
DIVIDER_COMPONENT = [".se-component.se-horizontalLine"]

# ---------------------------------------------------------------- 코드블록
# 툴바 버튼으로만 만들 수 있다.  [실측]
#  - 붙여넣기: <pre><code> 는 살균기가 지워서 문단으로 뭉개진다
#  - 타이핑: ``` 는 그냥 텍스트로 남는다
# 내용 영역이 contenteditable 이 아니라 textarea 다. keyboard.type 은 안 먹고 fill() 을 쓴다.
# 언어 지정 수단은 없다. 컨텍스트 툴바에 배경색(흰/회색/어두운)만 있다.
CODE_BUTTON = ["button[data-name='code'].se-code-toolbar-button", "button[data-name='code']"]
CODE_COMPONENT = ["[data-a11y-title='코드']", ".se-component.se-code"]
CODE_TEXTAREA = ["textarea.se-code-source-editor", ".se-code-source textarea"]

# ---------------------------------------------------------------- 수식
# 비주얼 수식편집기(NME)지만 스크립트 입력창이 따로 있다.  [실측]
# fill() 은 먹지 않는다 — 편집기가 키 입력으로만 스크립트를 파싱한다.
# press_sequentially 로 실제 타이핑해야 한다.
FORMULA_BUTTON = ["button[data-name='formula']", "button.se-formula-toolbar-button"]
FORMULA_POPUP = [".se-popup-math-editor"]
FORMULA_INPUT = ["textarea.nme_script_editor"]
FORMULA_SUBMIT = ["button.nme_button_submit"]
FORMULA_COMPONENT = ["[data-a11y-title='수식']", ".se-component.se-formula"]

# ---------------------------------------------------------------- 장소
# 검색 → 결과의 "추가" → 확인.  [실측]
# 결과 링크(se-place-map-search-result-link)를 누르는 건 지도 미리보기일 뿐 선택이 아니다.
# "추가" 버튼은 항목에 hover 해야 보인다. 추가 전에는 확인 버튼이 disabled 다.
MAP_BUTTON = ["button[data-name='map']", "button.se-map-toolbar-button"]
MAP_POPUP = [".se-popup-placesMap"]
MAP_INPUT = ["input.react-autosuggest__input", "input[placeholder*='장소명']"]
MAP_SEARCH = ["button.se-place-search-button"]
MAP_RESULT_ITEM = [".se-place-map-search-result-item"]
MAP_ADD = ["button.se-place-add-button"]
MAP_CONFIRM = ["button.se-popup-button-confirm"]
MAP_COMPONENT = ["[data-a11y-title='장소']", ".se-component.se-placesMap"]

# ---------------------------------------------------------------- 파일 첨부
# 이미지와 다르다. 툴바 버튼이 곧장 파일 다이얼로그를 띄우지 않고
# "내 컴퓨터 / 네이버 MYBOX" 를 고르는 팝업이 한 단계 더 있다.  [실측]
FILE_BUTTON = [
    "button[data-name='file'].se-file-toolbar-button",
    "button.se-file-toolbar-button",
    "button[data-name='file']",
]
FILE_POPUP = [".se-popup-file", ".se-popup.se-popup-file"]
# 이걸 눌러야 네이티브 파일 다이얼로그가 뜬다 (multiple=False, 파일당 10MB).
FILE_SOURCE_LOCAL = [
    "button[data-log='lfile.local']",
    ".se-popup-file-source-button-local",
]
FILE_POPUP_CLOSE = [".se-popup-file .se-popup-close-button", "button[data-log='lfile.close']"]
# 업로드 완료 판정. 이미지와 마찬가지로 개수 증가로 봐야 한다.
FILE_COMPONENT = ["[data-a11y-title='파일']", ".se-component.se-file"]

# 발행 패널 열기  [실측]
# button:has-text('발행') 은 쓰지 말 것 — 숨김 상태인 "예약 발행 0건" 이 먼저 잡힌다.
PUBLISH_OPEN = [
    "button[data-click-area='tpb.publish']",
    "button[class*='publish_btn']",
    ".publish_btn__m9KHH",
]

# 발행 레이어 안의 최종 발행 버튼  [실측]
PUBLISH_CONFIRM = [
    "button[data-testid='seOnePublishBtn']",
    "button[data-click-area='tpb*i.publish']",
    "button[class*='confirm_btn']",
    ".confirm_btn__WEaBq",
]

# 저장(임시저장)  [실측]
SAVE_DRAFT = [
    "button[data-click-area='tpb.save']",
    "button[class*='save_btn']",
    ".save_btn__bzc5B",
]

# 카테고리 선택  [실측]
# 주의: 발행 레이어(PUBLISH_OPEN 클릭) 안에만 존재한다. 에디터 첫 화면에는 없다.
# 에디터 우측의 se-flayer-unified-category-dropdown-trigger 는 "글감 검색" 필터이지
# 글 카테고리가 아니다. 헷갈리지 말 것.
CATEGORY_OPEN = [
    "button[data-click-area='tpb*i.category']",
    "button[aria-label='카테고리 목록 버튼']",
    "button[class*='selectbox_button']",
    ".selectbox_button__jb1Dt",
]
# 카테고리 목록 컨테이너  [실측]
# 중요: ul[class*='list__'] 와 li[class*='item__'] 는 공개설정/댓글허용/예약 목록에도
# 똑같이 쓰인다 (스코프 없이 세면 28개). div[class*='option_category'] 로 좁혀야
# 진짜 카테고리 12개만 남는다.
CATEGORY_SCOPE = "div[class*='option_category']"
CATEGORY_ITEM_ALL = [
    CATEGORY_SCOPE + " li[class*='item__']",
    ".option_category___kpJc .item__sAGX9",
]
# 하위 카테고리는 innerText 가 "하위 카테고리\n<이름>" 이다 (span.blind 접두).
# 이름만 쓰려면 CATEGORY_CHILD_MARK 접두를 떼어낼 것.
CATEGORY_CHILD_MARK = "하위 카테고리"
# 부분 매칭이라 한 이름이 다른 이름의 부분집합이면 오매칭한다. 스코프 덕에 후보가
# 12개로 줄었지만 여전히 주의. editor.set_category() 가 정확 일치를 먼저 시도한다.
CATEGORY_ITEM = [
    CATEGORY_SCOPE + " li[class*='item__']:has-text('{name}')",
    "li[class*='item__']:has-text('{name}')",
]

# 공개 설정 라디오 (발행 레이어 안)  [실측]
# 커스텀 라디오라 input 을 Playwright 로 직접 클릭하면 안 먹을 수 있다.
# JS element.click() 으로 눌러서 동작 확인했다.
VISIBILITY = {
    "public": ["#open_public"],
    "neighbor": ["#open_neighbor"],
    "both_neighbor": ["#open_both_neighbor"],
    "private": ["#open_private"],
}

# 태그 입력  [실측]
# 주의: 카테고리와 마찬가지로 발행 레이어 안에만 존재한다.
TAG_INPUT = ["input#tag-input", "input[placeholder*='태그']"]

# ---------------------------------------------------------------- 임시저장 목록
# SAVE_COUNT 버튼을 누르면 레이어가 뜬다.  [실측]
# 글쓰기 화면은 임시저장 글을 자동 복구하지 않는다. 발행하려면 여기서 불러와야 한다.
DRAFT_LAYER = [
    "div[aria-label='임시저장 글 보기']",
    "div[class*='layer_popup'][class*='isShow']",
]
# 항목을 클릭하면 그 글이 에디터로 불러와진다 (확인 팝업 없음).
DRAFT_ITEM = [
    "button[data-click-area='tpb*s.tlist']",
    "ul[aria-label='임시저장된 글'] button[class*='article_button']",
]
DRAFT_ITEM_TITLE = ["strong[class*='title__']", ".title__p1G9u"]
DRAFT_ITEM_DATE = ["span[class*='date__']", ".date__toLrn"]
DRAFT_LAYER_CLOSE = ["button[class*='close_button']", ".close_button__YWXJ_"]

# 삭제 확인 dialog 문구에 반드시 들어가는 낱말.  [실측]
#   임시저장: "선택된 1개의 임시저장 글을 삭제하시겠습니까? / 삭제된 글은 복구되지 않습니다."
#   발행글:   "삭제된 글은 복구할 수 없습니다. / 삭제하시겠습니까?"
# 아무 dialog 나 수락하면 "로그인이 필요합니다" 같은 다른 알림까지 삼키고
# 아무것도 안 지웠으면서 "삭제 완료" 로 보고하게 된다.
DELETE_CONFIRM_HINTS = ("삭제", "복구")

# 삭제 경로  [실측]
# '편집' 을 누르면 항목별 삭제 버튼(tpb*s.del)은 사라지고 체크박스 선택 모드로 바뀐다:
# 전체 삭제 / 선택 삭제 / 완료.
# button:text-is('편집') 은 "태그 편집" 을 안 잡는다 (exact 매칭이라).
DRAFT_EDIT_MODE = ["button:text-is('편집')"]
# 체크박스는 label 이 덮고 있어 input 을 직접 클릭할 수 없다. label 을 눌러야 한다.
DRAFT_CHECK_LABEL = ["ul[aria-label='임시저장된 글'] li label", "li label[class*='label__']"]
DRAFT_CHECKBOX = ["input[name='itemCheck']"]
DRAFT_DELETE_SELECTED = ["button:text-is('선택 삭제')"]
DRAFT_DELETE_ALL = ["button:text-is('전체 삭제')"]
DRAFT_EDIT_DONE = ["button:text-is('완료')"]
# 편집 모드 밖에서만 존재하고 숨김이다. 지금은 쓰지 않는다.
DRAFT_DELETE = ["button[data-click-area='tpb*s.del']", "#post_delete_button"]

# 발행 설정 레이어 본체  [실측]
# 열기 전에는 DOM 에 아예 없다가 발행 버튼을 누르면 생긴다. 열림 판정에 쓴다.
PUBLISH_LAYER = ["div[class*='layer_publish']", ".layer_publish__vA9PX"]

# 임시저장 개수 버튼  [실측]
# aria-label 이 "임시저장된 글 보기, 3개" 형태다. 저장 전후로 읽어서
# 임시저장이 실제로 됐는지 확인할 수 있다.
SAVE_COUNT = ["button[class*='save_count_btn']", ".save_count_btn__ZTLNa"]
# 임시저장 목록 레이어를 여는 버튼이기도 하다 (DRAFT_* 참조).
DRAFT_LIST_OPEN = SAVE_COUNT

# 로그인 여부 판별용  [실측 — 그대로 쓰면 안 됨]
# 2026-08-25 측정: 로그인 상태에서도 두 마크 모두 존재하지만 visible=False 이고,
# 최상위 문서가 아니라 #mainFrame 안에 있다. S.first() 는 visible 을 요구하므로
# 세션이 멀쩡해도 "만료" 로 오판한다.
#
# 권장: DOM 마크 대신 https://blog.naver.com/MyBlog.naver 로 이동해서
#       최종 URL 이 nid.naver.com 으로 튕기는지 보는 것. 리뉴얼에 훨씬 강하다.
#       (server.check_session 이 아직 이 방식으로 안 바뀌어 있음)
LOGGED_IN_MARK = [
    "a[href*='nidlogin.logout']",
    "#gnb_logout_button",
    ".MyView-module__link_login___HpHMW",
]
# 로그인 판별용 리다이렉트 프로브  [실측]
MYBLOG_PROBE_URL = "https://blog.naver.com/MyBlog.naver"

# 도움말 패널.  [실측 2026-08-27]
# 로그인 직후 첫 글쓰기 화면에서 자동으로 열리고 **툴바 클릭을 통째로 막는다.**
# 한 번 열리면 localStorage 의 se3#HELP_PANEL//STORAGE 에 {"alreadyOpened":true} 가
# 기록돼 다시 안 뜬다. 그래서 재현하려면 그 키를 지우고 새로고침하면 된다.
HELP_PANEL = ["[class*='se-help-panel']", "[class*='se-help']"]
HELP_PANEL_CLOSE = [
    "button.se-help-panel-close-button",
    "[class*='se-help-panel'] button[class*='close']",
]
HELP_PANEL_FLAG = "se3#HELP_PANEL//STORAGE"

# 팝업/레이어 (작성중 글 복구 등) — 뜨면 닫아야 진행됨  [미실측: 팝업을 못 재현함]
# 반드시 팝업 컨테이너로 스코프를 좁힐 것. 스코프 없는 button:has-text('취소') 는
# 툴바의 "취소선" 버튼을 눌러버린다.
RECOVER_POPUP_CANCEL = [
    ".se-popup-button-cancel",
    ".se-popup button[class*='cancel']",
    ".se-popup-container button:has-text('취소')",
]


async def first(scope, candidates: list[str], timeout: int = 3000, **fmt):
    """후보를 순서대로 시도해서 처음 **보이는** 로케이터를 반환. 없으면 None.

    한 셀렉터에 여러 요소가 걸릴 때 .first 만 보면 안 된다. 네이버 DOM 에는
    숨김 요소가 앞에 오는 경우가 흔해서 실제로 두 번 걸렸다 (2026-08-25):
      - PUBLISH_OPEN: 숨김 "예약 발행 0건" 이 "발행" 보다 앞
      - POST_DELETE:  숨김 "삭제하기" 가 보이는 "삭제" 보다 앞
    그래서 매칭 전체를 훑어 보이는 것을 고른다.

    timeout 은 후보별이 아니라 전체 예산이다.
    """
    deadline = asyncio.get_event_loop().time() + timeout / 1000
    while True:
        for sel in candidates:
            loc = scope.locator(sel.format(**fmt) if fmt else sel)
            try:
                n = await loc.count()
            except Exception:
                continue
            for i in range(min(n, 30)):
                cand = loc.nth(i)
                try:
                    if await cand.is_visible():
                        return cand
                except Exception:
                    continue
        if asyncio.get_event_loop().time() >= deadline:
            return None
        await asyncio.sleep(0.25)

# ---------------------------------------------------------------- 글감(뉴스, 증권, 책)
# 본문 아래 떠 있는 글감 바. 접혀 있으면 툴바 '글감' 버튼으로 편다(2026-09-16 실측).
MATERIAL_BAR = [".se-floating-material-container"]
MATERIAL_TOOLBAR = ["button[data-name='search'].se-document-toolbar-toggle-button"]
MATERIAL_CATEGORY_TRIGGER = [".se-flayer-unified-category-dropdown-trigger"]
MATERIAL_INPUT = [".se-floating-material-container input"]
MATERIAL_ITEM = ["li.se-flayer-item"]
MATERIAL_ITEM_TITLE = [".se-flayer-material-title"]
MATERIAL_ITEM_DESC = [".se-flayer-material-detail-description"]
# '문서에 추가' 는 hover 해야 보이는 div[role=button]. dispatch_event 로 누른다.
MATERIAL_ADD = [".se-flayer-material-button"]
MATERIAL_POPUP_CLOSE = [".se-popup-material-item-flayer [class*=close]"]
MATERIAL_COMPONENT = [".se-component.se-material"]
# 카드를 누르면 뜨는 모양 버튼. value = material_basic(기본형) | material_small(요약형)
MATERIAL_LAYOUT = ["button.se-context-toolbar-group-toggle-button[data-name='material-layout'][data-value='{value}']",
                   "button[data-name='material-layout'][data-value='{value}']"]
