# Obsidian 설정

Obsidian은 **열람 창**이다. 쓰기는 Emacs와 `kb.py`가 한다. `jira/`와 `wiki/`의 관리 구역은 기계가 다시 쓰므로 Obsidian에서 고치지 않는다(`AGENTS.md`).

`.obsidian/` 폴더는 커밋하지 않는다(`.gitignore`에 있음). 아래 설정은 볼트마다 한 번 한다.

## 1. 볼트 열기

- **볼트 루트 = 저장소 루트**(`work-kb/`, `AGENTS.md`가 있는 폴더). "폴더를 볼트로 열기"로 연다.
- 기존 개인 볼트(OneDrive 등)와 섞지 말고 새 볼트로 연다.
- `KB_ROOT`로 볼트를 저장소와 다른 곳에 두었다면 그 폴더를 연다. 이때 `jira/_views/일감.base`와 `wiki/엔티티.base`를 볼트의 같은 경로로 복사한다.

## 2. 코어 플러그인

설정 → 코어 플러그인(Core plugins)에서 켠다.

| 플러그인 | 설정 |
|---|---|
| Bases | 켠다. 볼트마다 따로 켜야 한다 |
| Daily notes | 새 파일 위치(New file location) `daily`, 날짜 형식(Date format) `YYYY-MM-DD`, 템플릿 없음 |

Daily notes 파일 이름은 `daily/2026-11-05.md` 꼴이어야 한다. 뼈대와 관리 구역은 `kb.py today`가 만든다.

## 3. 파일 및 링크

설정 → 파일 및 링크(Files and links).

| 항목 | 값 |
|---|---|
| 위키링크 사용(Use [[Wikilinks]]) | 켬 |
| 새 링크 형식(New link format) | 볼트 기준 절대 경로(Absolute path in vault) |
| 새 첨부 파일 기본 위치(Default location for new attachments) | 아래 지정 폴더(In the folder specified below) → `raw/assets` |
| 제외 파일(Excluded files) | 아래 폴더 |

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

`.venv*`처럼 점으로 시작하는 폴더는 Obsidian이 원래 숨기지만, 보이면 위처럼 이름마다 추가한다.

## 4. 이미지

- 노트에 붙여 넣은 이미지는 `raw/assets/`에 저장된다(위 첨부 폴더 설정).
- 탐색기나 Obsidian에서 **`inbox/`에 끌어다 놓은 이미지**(`png`, `jpg`, `jpeg`, `webp`)는 `kb.py ingest`가 처리한다. `raw/assets/YYYY-MM-DD-HHMM-<원래이름>.<ext>`로 옮기고 `inbox/`에 사이드카 `.md`를 남긴다.

## 5. Bases

저장소에 두 파일이 들어 있다. 사이드바에서 열면 표가 보인다.

| 파일 | 내용 |
|---|---|
| `jira/_views/일감.base` | 미완료 Jira 일감. 키·제목·상태·담당·마감·갱신. 프로젝트별 묶음, 갱신 최신순. `막힘` 뷰는 상태가 `Blocked`인 것만 |
| `wiki/엔티티.base` | `wiki/projects`, `wiki/people`, `wiki/topics` 페이지. 종류·제목/이름·갱신 |

일감 표는 `jira/` 아래에서 `status_category` 속성이 있고 값이 `done`이 아닌 파일만 보인다. 링크가 만든 스텁(`stub: true`)은 이 속성이 없어서 빠지고, `jira/_views/` 결과표도 빠진다. 표가 비어 있으면 먼저 `kb.py sync`를 실행한다.

처음 열었을 때 확인할 것:

1. 열 이름이 키·제목·상태·담당·마감·갱신으로 보인다.
2. 완료(Done) 일감이 없다.
3. 프로젝트(예: KB, OPS)별로 묶인다.
4. 갱신 열이 날짜 순서로 정렬된다(최신이 위).
5. 도움말 → 정보에서 Obsidian 버전을 적어 둔다.

## 6. Dataview (선택)

커뮤니티 플러그인 Dataview를 쓴다면 예시는 이것 하나다. Bases로 충분하면 설치하지 않아도 된다.

```dataview
TABLE summary AS 제목, status AS 상태, assignee AS 담당, due AS 마감, updated AS 갱신
FROM "jira"
WHERE status_category AND status_category != "done" AND !contains(file.folder, "_views")
SORT updated DESC
```
