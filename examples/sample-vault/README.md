# 샘플 볼트

허구 데이터만 담은 작은 볼트다. 사람 이름·주소는 모두 지어낸 것이다. 집(구축 PC)에서 도구를 시험하는 데만 쓴다.
회사로 복사할 때는 `examples/` 폴더째 뺀다(`docs/DEPLOY.md` 1단계).

## 들어 있는 것

- `config.yaml`: 저장소 `config.yaml`의 복사본
- `config.local.example.yaml`: 쓸 때 `config.local.yaml`로 복사한다(`environment: home`, `project_keys: [KB, OPS]`).
  `config.local.yaml`은 `.gitignore` 대상이라 이 이름으로 둔다. 연결 주소·비밀값은 없다.
- `daily/2026-11-01.md` ~ `2026-11-05.md`: 한국어 기록, 맨 글자·`[[…]]` 키(KB-12, KB-7, OPS-3), `@김철수`·`@이영희`,
  개인 `- [ ]` 할 일, 코드 블록 안의 가짜 키(KB-999), URL 안의 키 모양 문자열(KB-404), 인라인 코드(KB-555).
  `2026-11-03.md`는 CRLF 줄끝, `2026-11-01.md`는 뼈대 없이 `## 기록`만 있다.
- `inbox/`: front matter(created, title)가 있는 메모 2개와 드롭한 이미지 `회의실 화이트보드.png`(사이드카 없음).
- `raw/assets/2026-11-05-1420-메일캡처.png`: `daily/2026-11-05.md`의 `- 14:20 ![[raw/assets/…]]` 줄이 임베드한 이미지.
  두 이미지는 `tests/fixtures/make_images.py`가 그린 허구 그림의 복사본이다. `kb.py ingest`가 inbox 이미지를
  `raw/assets/`로 옮기고 두 장의 사이드카를 `inbox/`에 쓴다(비전 LLM 필요).
- `wiki/`, `jira/`: 비어 있다. `kb.py link`·`kb.py sync`가 만든다.

## LLM·Jira 없이 시험하기 (A단계)

원본을 더럽히지 않게 임시 폴더로 복사해서 쓴다. 명령은 저장소 루트에서 실행한다.

```powershell
$tmp = Join-Path $env:TEMP "work-kb-sample"
Copy-Item -Recurse examples\sample-vault $tmp
Copy-Item "$tmp\config.local.example.yaml" "$tmp\config.local.yaml"
.\.venv\Scripts\python scripts\kb.py link --root $tmp
.\.venv\Scripts\python scripts\kb.py search "@김철수" --root $tmp
.\.venv\Scripts\python scripts\kb.py today --root $tmp
.\.venv\Scripts\python scripts\kb.py status --root $tmp
```

`link`를 두 번째 실행하면 바뀌는 파일이 없어야 한다(멱등).

## 전체 흐름 시연 (`kb.py demo`)

`kb.py demo`는 이 볼트를 임시 폴더로 복사하고 모의 Jira·Confluence·LLM(`tests/mock_*.py`)을 띄워
임시 설정(`init --dev` 대신) → sync → link → ingest → compile → lint → push를 차례로 돌린 뒤 결과 트리를 보여 준다.
저장소의 `config.local.yaml`·자격 증명 저장소·`DEEPINFRA_API_KEY`는 쓰지 않는다(LLM 답은 "모의"로 표시된다).

```powershell
.\.venv\Scripts\python scripts\kb.py demo
.\.venv\Scripts\python scripts\kb.py demo --keep "$env:TEMP\work-kb-demo"
```

- `--keep`은 저장소 밖의 새 폴더(또는 빈 폴더)에 결과 볼트를 남긴다. Obsidian으로 열어 Bases를 확인할 때 쓴다
  (`jira/_views/일감.base`, `wiki/엔티티.base`를 같은 경로로 복사, `obsidian/setup.md` 8절).
- **집 전용**이다. 회사 사본에는 `examples/`와 `tests/`가 없어서 demo가 종료 4로 멈추고,
  `config.local.yaml`이 `environment: company`면 종료 7로 거부한다.
