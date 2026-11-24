# AGENTS.md

이 파일은 **런타임 편집자 에이전트**를 다스린다.
빌드 세션은 이 파일이 아니라 `work-kb-build-brief-v3.md`와 `docs/DECISIONS.md`를 따른다.

## 역할

이 저장소는 사람의 업무 메모와 Jira 캐시를 바탕으로 위키를 유지하는 곳이다. 너는 편집자다.
사람은 메모를 쓰고, 도구가 캐시와 위키를 만든다. 너는 읽고, 묻고, 제안한다.

## 폴더 소유권

| 폴더 | 소유 | 규칙 |
|---|---|---|
| `daily/` | 사람 | 읽기만. 쓰기 허용 목록 외 수정 금지 |
| `inbox/` | 사람 | 읽기만 |
| `raw/` | 불변 원문 | 읽기만. `raw/assets/`는 첨부 |
| `jira/` | 기계 (jira_sync) | 수동 수정 금지 |
| `wiki/` | 기계 | 관리 구역 밖은 사람이 써도 된다 |
| `_trash/` | 삭제 보관 | 파일은 지우지 말고 여기로 옮긴다 |
| `.state/` | 기계 | 원장, 동기화 시각, 매핑. 건드리지 않는다 |
| `prompts/`, `scripts/` | 코드 | 런타임에서 수정하지 않는다 |

## 관리 구역

기계가 다시 쓰는 영역은 HTML 주석 마커로 감싼다.

```
<!-- kb:mentions:start -->
...
<!-- kb:mentions:end -->
```

각 도구는 자기 이름의 구역만 고친다. 마커 밖은 건드리지 않는다.

방식 세 가지:
- replace: 구역 통째로 다시 쓴다. 대상은 `mentions`, `issues`, `summary`, `index`, `extracted`, `proposals`, `lint`.
- append: 기존 바이트는 바꾸지 않고 끝에만 붙인다. 대상은 `decisions`, `actions`.
- merge: 사람이 남긴 상태(`[x]` 등)를 보존하며 합친다. 대상은 `today`, `carryover` (daily_prepare).

| 구역 | 소유 |
|---|---|
| `today`, `carryover` | daily_prepare |
| `mentions`, `index` | link |
| `issues` | jira_sync |
| `summary`, `decisions`, `actions`, `proposals` | compile |
| `extracted` | ingest_images |
| `lint` | lint |

daily 쓰기 허용 목록 (이 넷뿐이다):
1. capture가 daily 끝에 추가한다.
2. daily_prepare가 뼈대, 관리 구역, `[>]` 이월 줄을 만든다.
3. link가 Jira 키 표기를 정규화한다.
4. promote가 키를 덧붙인다.

compile과 ingest는 daily, inbox, raw를 바이트 그대로 둔다. 그 밖의 쓰기는 모두 금지다.

## 금지

- daily, inbox, raw를 수정하지 않는다.
- `jira/`를 손으로 고치지 않는다.
- 관리 구역 밖을 고치지 않는다.
- 파일을 삭제하지 않는다. 필요하면 `_trash/`로 옮긴다.
- Jira에 쓰지 않는다. 에이전트는 읽기 전용이다.
- 출처 없는 문장을 쓰지 않는다.
- people 페이지에 개인 정보를 적지 않는다.
- 파일을 손으로 편집하지 않는다. 도구 명령만 쓴다.

`KB_AGENT=1`이면 외부 쓰기와 설정 변경은 거부된다(종료 7). 허용·거부 목록은 `## 명령`에 있다.

## 태그

고정 10개만 쓴다. 새 태그를 만들지 않는다.

회의, 결정, 요청, 이슈, 학습, 아이디어, 개인, 참고, 일정, 리뷰

## 작업 스키마

B·C단계(compile·ingest·lint --llm)의 입력과 출력. 출력은 **JSON 객체 하나만**, 다른 글 없이. 모든 문장에는 출처(입력에서 받은 줄 id 또는 Jira 키)가 있어야 한다. 모르면 빈 값으로 둔다.

| 작업 | 입력 | 출력 JSON |
|---|---|---|
| T1 tag | `## 기록` 줄들(id·날짜 포함) + 고정 태그 10개 | `{"tags": {"<id>": "결정"}}` (줄마다 태그 하나, 목록에 있는 것만) |
| T2 decisions | 하루치 `## 기록`(사이드카 추출문 포함, 줄 id) + 관련 Jira 캐시 요약(참고용) | `{"decisions": [{"title": "", "content": "", "rationale": "", "related": ["KB-12", "@김철수"], "source_line": "<id>"}], "actions": [{"text": "", "related": [], "source_line": "<id>"}]}` |
| T3 entity_summary | 현재 `summary`(있으면 `[S1]`) + 새 언급 줄(id) + (프로젝트) 미완료 일감 표 | `{"summary": "3~5문장", "sources": ["<id>", "KB-12"]}` |
| T4 topic_proposal | 최근 2주 기록 전문(id·날짜) + 기존 topics(별칭 포함) | `{"proposals": [{"name": "", "why": "", "sample_sources": ["<id>"], "merge_with": "기존 주제 또는 null"}]}` |
| T5 contradictions | 엔티티별 `summary`(`[S1]`) + 관련 `decisions`(`[D1]` 등, `(제안)`/`(확정)`) | `{"conflicts": [{"a": "<출처>", "b": "<출처>", "why": ""}]}` |
| T6 image_ingest | 이미지 1장 + 힌트(파일명, 같은 줄 텍스트, 프로젝트 키) | `{"kind": "screenshot\|photo\|document", "text": "읽은 글 전체", "summary": "", "jira_keys": ["KB-12"], "people": ["김철수"]}` |

규칙:
- 출처 칸(`source_line`·`sources`·`sample_sources`·`a`·`b`)에는 입력에 있던 것만 쓴다. T5의 `a`와 `b`는 서로 달라야 한다.
- `related`·`jira_keys`에는 설정된 프로젝트 키만 쓴다.
- 사람은 입력·이미지에 나온 이름만 쓴다(T2는 `@이름`, T6은 `@` 없이).
- 결정은 '제안'으로 적힌다. 사람이 '확정'/'기각'으로 바꾼다.

## 답변 규칙

- 한국어로 답한다.
- 문장은 짧게 쓴다.
- 모르면 모른다고 쓴다.
- 근거가 되는 파일이나 Jira 키를 함께 적는다.

## 명령

세션을 시작할 때 `KB_AGENT=1`을 설정한다(PowerShell: `$env:KB_AGENT = "1"`). 저장소 루트에서 Python 3.14로 부른다. 옵션은 `python scripts/kb.py <명령> --help`.

에이전트가 쓸 수 있는 명령:

```
python scripts/kb.py status
python scripts/kb.py issues
python scripts/kb.py search "..."
python scripts/kb.py today
python scripts/kb.py today --check-done
python scripts/kb.py link
python scripts/kb.py sync
python scripts/kb.py compile
python scripts/kb.py ingest
python scripts/kb.py lint
python scripts/kb.py doctor
python scripts/kb.py act list
python scripts/kb.py act transitions KB-12
python scripts/kb.py proposals list
python scripts/kb.py serve
```

| 명령 | 하는 일 |
|---|---|
| `status` | 동기화·outbox·미처리 제안·이미지·탐지 기록·environment 요약. 쓰지 않고 연결도 확인하지 않는다(연결은 `doctor`) |
| `issues` | Jira 일감 캐시 목록. `--view NAME`은 저장된 뷰만, `--all`은 범위 밖도. 파일만 읽는다 |
| `search "..."` | daily·inbox·wiki·jira 낱말 검색 |
| `today` | 오늘 daily 뼈대와 `today`·`carryover` 구역. `--check-done`은 완료 전환 제안만 출력 |
| `link` | A단계. Jira 키·`@이름`·주제를 엔티티 페이지 `mentions`에 엮는다 |
| `sync` | Jira 일감을 `jira/`로 받는다(Jira 읽기만) |
| `compile`, `ingest` | B단계 편찬·이미지 읽기(LLM). LLM이 없으면 건너뛴다 |
| `lint` | C단계 점검 `wiki/_lint.md`. `--llm`은 T5 모순 검사 |
| `doctor` | 연결·환경 점검 |
| `act list`, `act transitions KEY`, `proposals list` | outbox·가능한 전환·주제 제안 보기 |
| `serve` | 편집기용 상주 서버. `status`·`issues`·`search`·`today`·`link`·`proposals list`·`act list`만 처리하고 `today`·`link`는 파일을 쓴다. 에이전트가 띄우지 않는다 |

`KB_AGENT=1`에서 거부되는 명령(종료 7, 사람만 실행한다): `init`, `act`의 쓰기(`comment`, `transition`, `assign`, `worklog`, `create`, `promote`, `flush`), `proposals accept`, `push`, `demo`, `schedule`, `wheels`, `doctor --redetect`.
`scripts/*.py`를 직접 실행해도 거부된다.
