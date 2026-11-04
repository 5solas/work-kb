# tests/fixtures/notes

볼트 `daily/`·`inbox/` 아래로 그대로 복사해 쓰는 작은 허구 노트. 프로젝트 키는 `KB`, `OPS`.

| 파일 | 특징 |
|---|---|
| `daily/2026-11-04.md` | LF. `[[KB-12]]`, 맨 글자 `OPS-3`·`KB-7`, `@김철수`, 개인 `- [ ]`, 인라인 코드 `KB-555`, 코드 블록 `KB-999`, URL 안 `KB-404` |
| `daily/2026-11-05.md` | CRLF. `[[OPS-3]]`, 맨 글자 `KB-12`, `@이영희`, 개인 `- [ ]`, 대소문자 `Cache` |
| `inbox/20261105-0930-회의 메모.md` | front matter(created, title), 한글·공백 파일명, `OPS-3`, `@김철수` |

복사 예: `shutil.copytree(FIXTURES / "notes" / "daily", vault / "daily", dirs_exist_ok=True)`.
