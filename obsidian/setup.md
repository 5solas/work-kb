# Obsidian 설정

Obsidian은 **열람 창**이다. 쓰기는 Emacs와 `kb.py`가 한다. `jira/`와 `wiki/`의 관리 구역은 기계가 다시 쓰므로 Obsidian에서 고치지 않는다(`AGENTS.md`).

`.obsidian/` 폴더는 커밋하지 않는다(`.gitignore`에 있음). 아래 설정은 볼트마다 한 번 한다. 확인한 버전은 Obsidian 1.15.0이다(`docs/DECISIONS.md` D64).

## 1. 볼트 열기

- **볼트 루트 = 저장소 루트**(`work-kb/`, `AGENTS.md`가 있는 폴더). "폴더를 볼트로 열기"로 연다.
- 기존 개인 볼트(OneDrive 등)와 섞지 말고 새 볼트로 연다.
- `KB_ROOT`(Emacs는 `work-kb-vault`)로 볼트를 저장소와 다른 곳에 두었다면 그 폴더를 연다. 이때 저장소의 두 `.base` 파일 `jira/_views/일감.base`와 `wiki/엔티티.base`를 볼트의 같은 경로로 손으로 복사한다. `sync`·`link`는 `.base` 파일을 만들지 않는다(표 내용만 채운다).

## 2. 폴더

볼트 안에서 쓰는 폴더는 다섯 개다. 사람이 만드는 구조는 날짜뿐이다.

| 폴더 | 누가 쓰나 | 내용 |
|---|---|---|
| `daily/` | 사람(Emacs 캡처) | `YYYY-MM-DD.md`. `## 오늘`·`## 이월`은 `kb.py today`가, `## 기록`은 사람이 쓴다 |
| `inbox/` | 사람 | 제목 있는 긴 글, 끌어다 놓은 이미지. 이미지 사이드카(`.md`)는 `kb.py ingest`가 만든다 |
| `raw/assets/` | 사람·도구 | 이미지·첨부(Obsidian 첨부 폴더) |
| `jira/` | 기계(`kb.py sync`) | 일감 캐시 `KEY-123.md`, `_views/` 결과표와 `일감.base` |
| `wiki/` | 기계(`link`·`compile`·`lint`) | `projects/`·`people/`·`topics/` 엔티티 페이지, `decisions.md`, `actions.md`, `_proposals.md`, `_lint.md`, `엔티티.base` |

`_trash/`는 지운 파일 보관함이고 `.state/`는 도구의 원장이다. 둘 다 Obsidian에서 볼 일이 없다.

## 3. 코어 플러그인

설정 → 코어 플러그인(Core plugins)에서 켠다.

| 플러그인 | 설정 |
|---|---|
| Bases | 켠다. 볼트마다 따로 켜야 한다 |
| Daily notes | 새 파일 위치(New file location) `daily`, 날짜 형식(Date format) `YYYY-MM-DD`, 템플릿 없음 |

Daily notes 파일 이름은 `daily/2026-11-05.md` 꼴이어야 한다. 뼈대와 관리 구역은 `kb.py today`가 만든다. Obsidian에서 오늘 노트를 먼저 만들었다면 `kb.py today`를 한 번 실행하면 뼈대가 채워진다.

## 4. 파일 및 링크

설정 → 파일 및 링크(Files and links).

| 항목 | 값 |
|---|---|
| 위키링크 사용(Use [[Wikilinks]]) | 켬 |
| 새 링크 형식(New link format) | 볼트 기준 절대 경로(Absolute path in vault) |
| 새 첨부 파일 기본 위치(Default location for new attachments) | 아래 지정 폴더(In the folder specified below) → `raw/assets` |
| 제외 파일(Excluded files) | 아래 폴더 |

링크 형식: 도구는 `[[KB-12]]`, `[[김철수]]`, `![[raw/assets/2026-11-05-1420-메일캡처.png]]`처럼 쓴다. Obsidian이 만드는 `[[jira/KB-12]]`, `[[jira/KB-12|별칭]]`, `[[jira/KB-12#설명]]` 꼴도 `link`가 같은 일감으로 알아본다. 사람 이름은 노트에 `@김철수`로 쓰면 `link`가 `wiki/people/김철수.md`에 엮는다.

제외할 폴더:

```
examples/
tests/
scripts/
emacs/
docs/
prompts/
obsidian/
_trash/
wheels/
.venv/
.venv313/
```

`.venv*`처럼 점으로 시작하는 폴더는 Obsidian이 원래 숨기지만, 보이면 위처럼 이름마다 추가한다. `examples/`와 `tests/`에는 허구 일감(`KB-12` 등)이 들어 있어 제외하지 않으면 검색과 그래프에 섞인다.

## 5. 이미지

- 노트에 붙여 넣은 이미지는 `raw/assets/`에 저장된다(위 첨부 폴더 설정).
- 탐색기나 Obsidian에서 **`inbox/`에 끌어다 놓은 이미지**(`png`, `jpg`, `jpeg`, `webp`)는 `kb.py ingest`가 처리한다. `raw/assets/YYYY-MM-DD-HHMM-<원래이름>.<ext>`로 옮기고 `inbox/`에 사이드카 `.md`를 남긴다. 사이드카의 읽은 글도 `link`가 엮는다.
- 비전 LLM이 없으면 이미지는 그대로 남고 `kb.py status`에 "미처리 이미지"로 보인다.

## 6. Bases

저장소에 두 파일이 들어 있다. 사이드바에서 열면 표가 보인다.

| 파일 | 내용 |
|---|---|
| `jira/_views/일감.base` | 미완료 Jira 일감. 키·제목·상태·담당·마감·갱신. 프로젝트별 묶음, 갱신 최신순. `막힘` 뷰는 상태가 `Blocked`인 것만 |
| `wiki/엔티티.base` | `wiki/projects`, `wiki/people`, `wiki/topics` 페이지. 종류·제목/이름·갱신. 종류별 뷰(프로젝트·사람·주제) |

일감 표는 `jira/` 아래에서 `status_category` 속성이 있고 값이 `done`이 아닌 파일만 보인다. 링크가 만든 스텁(`stub: true`)은 이 속성이 없어서 빠지고, `jira/_views/` 결과표도 빠진다. 표가 비어 있으면 먼저 `kb.py sync`를 실행한다.

**`in_scope` 표시**: 전체 동기화 때 범위(`project_keys` × `jql_mine`)에서 빠진 일감(담당이 바뀜, 지워짐, 볼 권한 없음)은 파일을 지우지 않고 front matter에 `in_scope: false`를 단다. 다른 키로 옮겨진 일감은 `moved_to: 새 키`도 달고 새 키 파일이 생긴다. `일감.base`는 `in_scope` 속성이 있는 파일을 숨긴다(`!file.hasProperty("in_scope")`). 나중에 다시 범위에 들어오면 다음 동기화가 표시를 지우고 표에 다시 보인다. 숨은 일감은 `jira/KEY.md`를 직접 열면 볼 수 있다.

`일감.base`의 `막힘` 뷰는 상태 이름이 정확히 `Blocked`일 때만 보인다. 회사 Jira의 상태 이름이 다르면(예: `보류`) 그 뷰의 필터 값을 고친다.

## 7. Dataview (선택)

커뮤니티 플러그인 Dataview를 쓴다면 예시는 이것 하나다. Bases로 충분하면 설치하지 않아도 된다. 이 예시는 Obsidian에서 돌려 보지 않았다(Bases와 같은 조건을 Dataview 문법으로 옮긴 것).

```dataview
TABLE summary AS 제목, status AS 상태, assignee AS 담당, due AS 마감, updated AS 갱신
FROM "jira"
WHERE status_category AND status_category != "done" AND !contains(file.frontmatter, "in_scope") AND !contains(file.folder, "_views")
SORT updated DESC
```

## 8. 처음 열었을 때 확인할 것 (사용자 확인)

구축 PC에서는 앱 화면을 자동으로 확인하지 못했다. 볼트를 처음 열 때 아래를 한 번 확인한다. 집에서는 `kb.py demo --keep <저장소 밖 새 폴더>`로 허구 일감이 채워진 볼트를 만들고, 두 `.base` 파일을 그 볼트의 같은 경로(`jira/_views/`, `wiki/`)로 복사한 뒤 열어 확인할 수 있다(demo 볼트에는 `.base` 파일이 들어가지 않는다).

1. 설정 → 코어 플러그인에서 Bases가 켜져 있다.
2. `jira/_views/일감.base`의 열 이름이 키·제목·상태·담당·마감·갱신으로 보인다.
3. 완료(Done) 일감이 없다. `in_scope: false`인 일감도 없다.
4. 프로젝트(예: KB, OPS)별로 묶인다.
5. 갱신 열이 날짜 순서로 정렬된다(최신이 위). 정렬 설정(`sort`)은 문서에 없는 형식이라 앱이 다르게 읽을 수 있다. 정렬이 안 되면 앱에서 정렬을 다시 고르고 저장한다.
6. `막힘` 뷰가 열린다(비어 있어도 된다).
7. `wiki/엔티티.base`에 프로젝트·사람·주제 페이지가 종류별로 보인다.
8. 키 열을 누르면 `jira/KEY.md`가 열리고, 문서의 `[[KB-12]]` 링크도 같은 파일로 간다.
9. 새로 붙여 넣은 이미지가 `raw/assets/`에 저장된다.
10. 도움말 → 정보에서 Obsidian 버전을 적어 둔다. 1.15.0과 다르고 표가 깨지면 `docs/TROUBLESHOOTING.md`의 Bases 항목을 본다.
