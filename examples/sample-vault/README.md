# 샘플 볼트

허구 데이터만 담은 작은 볼트다. 사람 이름·주소는 모두 지어낸 것이다.

- `config.yaml`: 저장소 `config.yaml`의 복사본
- `config.local.example.yaml`: 쓸 때 `config.local.yaml`로 복사한다(`environment: home`, `project_keys: [KB, OPS]`)
- `daily/2026-11-01.md` ~ `2026-11-05.md`: 한국어 기록, 맨 글자·`[[…]]` 키(KB-12, KB-7, OPS-3), `@김철수`·`@이영희`,
  개인 `- [ ]` 할 일, 코드 블록 안의 가짜 키(KB-999), URL 안의 키 모양 문자열(KB-404), 인라인 코드(KB-555).
  `2026-11-03.md`는 CRLF 줄끝, `2026-11-01.md`는 뼈대 없이 `## 기록`만 있다.
- `inbox/`: front matter(created, title)가 있는 메모 2개. 이미지는 Phase 4에서 더한다.
- `wiki/`, `jira/`: 비어 있다. `kb.py link`가 만든다.

원본을 더럽히지 않게 임시 폴더로 복사해서 쓴다.

```powershell
$tmp = Join-Path $env:TEMP "work-kb-sample"
Copy-Item -Recurse examples\sample-vault $tmp
Copy-Item "$tmp\config.local.example.yaml" "$tmp\config.local.yaml"
.\.venv\Scripts\python scripts\kb.py link --root $tmp
.\.venv\Scripts\python scripts\kb.py search "@김철수" --root $tmp
.\.venv\Scripts\python scripts\kb.py status --root $tmp
```
