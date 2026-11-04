# tests/fixtures/llm

compile(T1~T4) 테스트용 허구 데이터. 실제 회사·인명·주소 없음.

| 파일 | 내용 |
|---|---|
| `tag.json`, `decisions.json`, `entity_summary.json`, `topic_proposal.json` | mock_llm `canned` 모드의 작업별 답(`KB-TASK: <작업>` 표시 줄로 고름). 줄 id는 요청마다 `L1`부터, 이미지 추출문은 `X1`부터, 언급은 `M1`부터 |
| `vault/daily/2026-11-04.md` | L1 결정 줄(`[[KB-12]] @김철수`), L2 할 일 줄(`[[OPS-3]]`), L3 개인 체크박스, L4 이미지 임베드 |
| `vault/daily/2026-11-05.md` | L1 `[[OPS-3]] @이영희`, L2 `[[KB-12]]` |
| `vault/inbox/2026-11-04-1420-whiteboard.md` | 이미지 사이드카(`extracted` 두 줄: X1, X2) |
| `vault/jira/KB-12.md` | 동기화된 일감 캐시(요약·상태) |

`decisions.json`에는 일부러 버려져야 할 출력이 들어 있다: 입력에 없는 `L99`, 범위 밖 키 `ZZ-9`,
입력에 없는 사람 `@없는사람`, 체크박스 줄(L3)의 할 일.

복사 예: `shutil.copytree(FIXTURES / "llm" / "vault", vault, dirs_exist_ok=True)`.
