# naver-blog-mcp

네이버 블로그에 **서식·이미지·표·수식·장소가 들어간 글**을 쓰는 MCP 서버.
Playwright로 스마트에디터 ONE을 직접 조작합니다.

마크다운을 주면 임시저장까지 해주고, 발행은 별도 툴로 분리돼 있습니다.

```markdown
## 오늘의 기록

**굵게** 와 [링크](https://naver.com) 가 들어간 문단.

- 목록도
- 됩니다

| 항목 | 지원 |
|------|------|
| 표   | O    |

![캡션](/path/photo.png)

:::file /path/report.pdf:::
:::formula x^2 + y^2 = z^2:::
:::place 강남역:::
```

---

## 먼저 읽어주세요

- **네이버 계정으로 로그인한 브라우저를 자동 조작합니다.** 과도하게 쓰면 계정이
  제재될 수 있습니다. 대량 자동 포스팅 용도로 만들지 않았고, 그런 기능을 넣지 마세요.
- **비밀번호는 저장하지 않습니다.** 사람이 직접 로그인해서 만든 쿠키 파일만 읽습니다.
- 그 쿠키 파일(`playwright-state/storage_state.json`)은 **계정 접근권한 그 자체**입니다.
  `.gitignore`에 들어 있지만, 실수로 공유하지 않도록 주의하세요.
- 네이버가 에디터를 바꾸면 깨질 수 있습니다. 그때는 `verify_selectors.py`로 진단합니다
  (아래 "셀렉터가 깨졌을 때").

## 요구사항

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- 네이버 블로그 계정

## 설치

```bash
git clone <이 저장소>
cd naver-blog-mcp
uv sync
uv run playwright install chromium
```

## 로그인 (최초 1회)

```bash
uv run python login_setup.py
```

브라우저가 열리면 **직접 로그인**하고 본인 블로그 홈까지 이동한 뒤 터미널에서 엔터를 치세요.
CAPTCHA·2차인증·기기등록은 전부 사람이 처리합니다.
쿠키가 `playwright-state/storage_state.json`에 저장되고, 서버는 그 파일만 읽습니다.

세션이 만료되면 이 명령을 다시 실행하면 됩니다.

## MCP 등록

### Claude Code

```bash
claude mcp add naver-blog \
  -e NAVER_BLOG_ID=<블로그아이디> \
  -e NAVER_STATE=/절대경로/naver-blog-mcp/playwright-state/storage_state.json \
  -- /절대경로/naver-blog-mcp/.venv/bin/naver-blog-mcp
```

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`
(Windows: `%APPDATA%\Claude\claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "naver-blog": {
      "command": "/절대경로/naver-blog-mcp/.venv/bin/naver-blog-mcp",
      "env": {
        "NAVER_BLOG_ID": "<블로그아이디>",
        "NAVER_STATE": "/절대경로/naver-blog-mcp/playwright-state/storage_state.json"
      }
    }
  }
}
```

저장 후 Claude Desktop을 완전히 종료(Cmd+Q)했다가 다시 켜세요.

> **경로는 반드시 절대경로로.** GUI 앱은 셸 PATH를 물려받지 않고 작업 디렉터리도 다릅니다.
> `NAVER_STATE`를 생략하면 기본값이 상대경로라 세션 파일을 못 찾습니다.

### 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `NAVER_BLOG_ID` | (필수) | 블로그 아이디. `blog.naver.com/<여기>` |
| `NAVER_STATE` | `playwright-state/storage_state.json` | 쿠키 파일 경로. **절대경로 권장** |
| `HEADLESS` | `false` | `true`면 브라우저 창을 띄우지 않음 |
| `NAVER_COVER` | `images/00-cover.*` | 대표 이미지로 쓸 표지 파일. `0`/`off` 면 넣지 않음 |

`HEADLESS` 기본값이 `false`인 건 의도입니다 — CAPTCHA가 뜨면 사람이 풀어야 하니까요.
`true`로 두면 그런 상황에서 조용히 실패합니다.

## 툴

| 툴 | 설명 |
|---|---|
| `check_session()` | 세션이 살아있는지 확인 |
| `list_categories()` | 카테고리 목록 (하위 카테고리는 들여쓰기) |
| `list_drafts()` | 임시저장 글 목록 |
| `create_draft(title, markdown, category, tags)` | 글을 쓰고 **임시저장**. 발행하지 않음 |
| `publish_draft(confirm, title, visibility)` | 임시저장 글을 불러와 발행 |
| `delete_draft(confirm, title)` | 임시저장 글 삭제 |
| `delete_post(url_or_log_no, confirm)` | 발행된 글 삭제 |

기본 흐름은 **임시저장 → 눈으로 확인 → 발행**입니다.

파괴적인 툴(`publish_draft`, `delete_draft`, `delete_post`)은 `confirm=True`를 요구합니다.
앞의 둘은 대상이 애매하면(제목이 여러 글과 맞거나, 지정 없이 임시저장이 2건 이상)
거부하고 후보를 보여줍니다.

`visibility`는 `public` / `neighbor` / `both_neighbor` / `private` 중 하나입니다.

## 마크다운 지원 범위

전부 실제 에디터에 넣어보고 확인한 결과입니다.

| 문법 | 결과 |
|---|---|
| `# ## ###` 제목 | 글자 크기 24 / 19 / 15 |
| `**굵게**` `*기울임*` `~~취소선~~` | 지원 |
| `[텍스트](url)` | 문단·제목 안에서 지원 |
| `> 인용` | 인용구 컴포넌트 |
| `- 목록` / `1. 목록` | 순서/비순서 목록 |
| ` ```코드``` ` | 소스코드 컴포넌트 |
| `---` | 구분선 |
| GFM 파이프 표 | 표 컴포넌트 (셀 안 서식 유지) |
| `![캡션](로컬경로)` | 사진 + 캡션 |
| `:::file 로컬경로:::` | 파일 첨부 (개당 10MB) |
| `:::formula ...:::` | 수식 |
| `:::place 검색어:::` | 장소 (검색 결과 첫 번째) |
| `:::video 유튜브 주소:::` | 유튜브 영상 플레이어 (watch, youtu.be, shorts 주소만) |

이미지·파일은 **로컬 경로만** 됩니다 (URL 불가).

### 알려진 한계

- **인용문과 표 안의 링크는 사라집니다.** 링크를 넣으려면 타이핑 경로를 타야 하는데,
  타이핑으로는 인용구·표 컴포넌트 자체를 만들 수 없습니다. 블록 형태를 지키고 링크를 버립니다.
- **인라인 코드(`` `code` ``)는 지원하지 않습니다.** 네이버에 대응 기능이 없습니다.
- **장소는 검색 결과 중 첫 번째**를 씁니다. 정확히 지정하려면 검색어를 구체적으로 주세요
  (`:::place 강남역 2호선:::`). 어느 장소를 골랐는지는 결과에 표시됩니다.
- 수식에 언어·스타일 지정 수단은 없습니다.

## 셀렉터가 깨졌을 때

네이버가 에디터를 바꾸면 툴이 `"...을 못 찾음"`을 반환합니다. 진단 스크립트가 있습니다.

```bash
export NAVER_BLOG_ID=<블로그아이디>
uv run python verify_selectors.py            # 창 띄움
HEADLESS=true uv run python verify_selectors.py   # 창 없이
```

에디터 첫 화면과 발행 레이어를 2단계로 검사해 `OK` / `HIDDEN` / `MISS`를 출력합니다.
`MISS`가 있으면 `dom_probe.txt`(클릭·입력 가능한 요소 목록)와 `dom_dump.html`을 남기니,
그걸 보고 `selectors.py`만 고치면 됩니다.

**셀렉터 문자열은 전부 `src/naver_blog_mcp/selectors.py` 한 파일에 있습니다.**
다른 파일에는 두지 마세요.

## 구조

```
src/naver_blog_mcp/
  selectors.py   셀렉터 격리 구역. 네이버가 바뀌면 여기만 고친다
  ir.py          마크다운 → 블록 IR → HTML. 붙여넣기 가능/불가능 라우팅
  editor.py      에디터 구동부 (붙여넣기, 업로드, 툴바 조작)
  session.py     쿠키 로드/저장. 비밀번호는 다루지 않는다
  server.py      MCP 툴 정의
login_setup.py   최초 1회 사람이 직접 로그인
verify_selectors.py  셀렉터 진단
```

핵심 설계는 `CLAUDE.md`에 이유와 함께 적혀 있습니다. 특히:

- 본문은 **클립보드 HTML 붙여넣기**가 1차 경로입니다. 툴바 클릭보다 훨씬 안정적입니다.
- 붙여넣기가 뭉개는 것(목록·코드블록·링크)만 툴바·키보드로 따로 만듭니다.
- 이미지·파일·수식·장소는 붙여넣기가 불가능해 툴바를 거칩니다.

## 기여

셀렉터가 깨진 걸 발견하면 `verify_selectors.py` 출력과 함께 이슈를 남겨주세요.
`selectors.py`의 후보 리스트는 의미 기반(`data-click-area`, `data-testid`, `data-name`,
`aria-label`) → 클래스 접두 → 난독화 클래스 순서로 둡니다. 네이버의 난독화 클래스는
해시만 도는 경우가 많아 `class*=` 접두 매칭이 잘 견딥니다.

## 라이선스

MIT — [LICENSE](LICENSE)

## 이 포크에서 더한 것 (Johnhyeon)

- `create_draft_from_folder(folder)` — 글 파일과 이미지가 같은 폴더에 있을 때 폴더째 임시저장한다.
  이미지의 상대경로를 절대경로로 바꾸고, `meta.json` 의 제목·카테고리·태그를 읽고,
  글 첫 줄의 `# 제목` 을 제목으로 쓴다. **브라우저를 열기 전에** 이미지 파일을 모두 확인해서
  하나라도 없거나 10MB 를 넘으면 아무것도 하지 않고 목록으로 알려준다.
- `NAVER_BLOG_READONLY=1` — 발행·삭제 도구를 **도구 목록에서 아예 뺀다.** 임시저장까지만 돌리는 운영용.
- 카테고리를 `"종목 분석 > 국장"` 처럼 경로로 줘도 된다. 마지막 칸으로 고른다.
- `NAVER_KEEP_OPEN=1` — 일이 끝나도 브라우저 창을 닫지 않는다. 사람이 결과를 보고 창을 닫으면 그때 종료(기본 10분, 숫자를 주면 그 초만큼).
- `delete_draft`, `publish_draft` 에 `index` — 같은 제목으로 여러 번 임시저장했을 때 목록 순번(1이 최신)으로 고른다.
- **대표 이미지 자동 지정** — 글 폴더에 `images/00-cover.png` 가 있으면 본문 맨 앞에 넣고
  대표 이미지(검색 결과·블로그 목록 섬네일)로 지정한다. 네이버는 **본문에 들어간 이미지 중에서만**
  대표를 고르게 해서 따로 올리는 칸이 없다(2026-09-18 실측). 그래서 표지를 섬네일로 쓰려면
  본문 맨 앞에 두는 수밖에 없고, 그 자리는 독자에게도 보인다. 본문이 이미 그 파일을 쓰고 있으면
  두 번 넣지 않는다. 끄려면 `NAVER_COVER=0`.

## 글감 카드 (fork 추가, 2026-09-16)

마크다운 한 줄로 네이버 글감 카드를 넣는다.

```
:::news 머니투데이 | 한국첨단소재, ETRI와 광통신용 초고속 반도체 연결 기술 이전 계약:::
:::stock 072950:::
:::book 주식투자를 잘한다는 것 | 기본형:::
```

- 뉴스는 매체와 제목이 둘 다 맞아야 넣는다. 같은 제목을 다른 매체가 실은 경우가 흔하다
- 기본 모양은 요약형(제목 전체와 매체 한 줄). `| 기본형` 이면 썸네일 카드
- 쓰기 전에 전부 검색해 보고, 못 찾은 카드는 `매체, 제목` 글자로 바꿔 쓴다
- 증권 카드는 넣는 순간 시세를 굳힌다(애프터마켓 시간에는 정규장 종가와 다르다)
