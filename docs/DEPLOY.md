# 회사 PC로 옮기기 (DEPLOY)

집에서 만든 저장소 폴더를 회사 Windows PC로 복사하고 `kb.py init`으로 연결한다.
인용문(`>`)은 체크리스트(지시서 §9) 원문이고 그 아래가 보충이다. 7단계는 Neovim에 맞게 고친 문구다(원문은 Emacs 판).

> 주소 예시(`https://jira.example.invalid` 등)는 자리표시자다. 실제 주소는 `config.local.yaml`에만 쓴다.

## 0. 준비물

- Python **3.14** (64비트). 지원 버전은 이것 하나다.
- Neovim 0.12 이상(Neovide는 선택), Obsidian(Bases 지원 버전).
- 사내 Jira·Confluence·LLM 서버에 닿는 네트워크. pip가 막혀 있으면 `wheels/`로 설치한다.
- `kb.py demo`는 `tests/`가 없어 회사에서 못 쓴다(집 전용). 회사에서는 6단계 dry-run으로 확인한다.

## 체크리스트

### 1. 저장소 폴더 복사

> 저장소 폴더 복사(`config.local.yaml`, `.state/`, `_trash/`, `examples/`, 집에서 만든 daily 제외).

빼는 것:
- 집 설정·상태: `config.local.yaml`, `.env`, `.state/`, `_trash/`, `examples/`
- 집에서 만든 내용: `daily/*.md`, `inbox/`·`raw/` 안의 파일(`raw/assets` 포함), `jira/*.md`, `jira/_views/*.md`, `jira/outbox.jsonl`, `jira/.cache.sqlite*`
- 생성된 위키: `wiki/` 아래 `*.md` 전부(`wiki/index.md`, `wiki/projects/*.md`, `wiki/people/*.md`, `wiki/topics/*.md` 등)
- 개발·편집기 흔적(어느 폴더든): `.venv*`, `.git`, `.pytest_cache`, `__pycache__`, `tests/`, `.obsidian/`, `.*.tmp`, `*~`, `#*#`, `.#*`

남기는 것: 코드·문서·`config.yaml`·`requirements*.txt`, `wheels/`(만들었다면), 폴더의 `.gitkeep`, `jira/_views/일감.base`, `wiki/엔티티.base`.
집에서 `python scripts/deploy_check.py <대상 폴더>`가 이 규칙대로 복사하고 검사한다(`docs/PACKAGING.md` 3절). 경로에 한글·공백이 있어도 된다.

### 2. Python과 의존성

> Python 최신 확인, `pip install -r requirements.txt`(불가 시 `--no-index --find-links wheels`). 환경변수 `PYTHONUTF8=1`.

```powershell
python --version                      # 3.14.x가 아니면 py -3.14 -m venv .venv
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
# pip가 막혀 있을 때
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels -r requirements.txt
setx PYTHONUTF8 1                     # 새 터미널부터 적용
```

- 이후 명령은 `.venv\Scripts\python.exe scripts\kb.py …`로 실행한다. 스케줄과 Neovim은 `.venv`를 자동으로 쓴다.
- 3.15는 PyYAML wheel이 없어 오프라인 설치가 실패한다. 3.14로 만든다.
- `wheels/`는 집에서 `python scripts/kb.py wheels`로 만든다(`docs/PACKAGING.md`).

### 3. PAT 만들기

> Jira·Confluence PAT 생성.

각 서비스의 프로필 화면에서 만든다. `init` 입력창에만 붙여 넣고 파일·메모·채팅에 적지 않는다.

### 4. 연결 마법사

> `python scripts/kb.py init` → environment=company → Jira(프로젝트 키 선택) → Confluence(개인 스페이스) → LLM main(사내 Nemotron, 자동 탐지) → fallback(Gemma) → 스케줄.

```powershell
.venv\Scripts\python.exe scripts\kb.py init
```

- `environment`는 반드시 `company`. LLM 주소에 공개 클라우드 호스트·공개 IP를 넣을 수 없다.
- 스케줄 단계는 명령을 출력만 한다. 등록은 9단계.
- 비밀값은 Windows 자격 증명 저장소에 들어가고 `config.local.yaml`에는 참조(`*_ref`)만 남는다.
- 일부만 다시: `kb.py init --section jira`(또는 `confluence`, `llm`). LLM 탐지만 다시: `kb.py init --redetect`.

### 5. doctor

> `python scripts/kb.py doctor` 전부 ✓. 공개 호스트 ✗가 하나라도 있으면 진행 금지.

```powershell
.venv\Scripts\python.exe scripts\kb.py doctor
```

- ✓여야 할 줄: Python, 의존성, config.yaml, config.local.yaml(`environment: company`), 공개 호스트 차단, Jira, Confluence, LLM 프로필, 쓰기 권한.
- `-` 줄은 참고다. 종료 코드와 무관하다.
- ✗ 줄은 끝의 `→ 해결 방법`을 따른다(`docs/TROUBLESHOOTING.md`).
- 예외: GPO가 PowerShell 실행 정책을 막아 생긴 ✗는 두고 진행한다(종료 1). 이미지는 `F`로 캡처한다.
- 공개 호스트 ✗(종료 5)면 진행 금지. `kb.py init --section llm`으로 고친다.
- 프로필별 `json_mode`·`vision`·`reasoning_toggle`을 본다. `method: none`이면 TROUBLESHOOTING을 본다.

### 6. 첫 동기화

> `jira_sync --jql "key = <실제키>" --dry-run` 확인 후 `kb.py sync --full`.

```powershell
.venv\Scripts\python.exe scripts\kb.py sync --jql "key = <실제키>" --dry-run
.venv\Scripts\python.exe scripts\kb.py sync --full
```

- dry-run은 아무것도 쓰지 않는다. 맞으면 `sync --full`로 `project_keys × jql_mine` 범위를 받는다.
- 범위 밖 기존 일감의 403은 `in_scope: false`로 표시하고 넘어간다(토큰이 무효면 종료 2).
- `kb.py status`로 동기화 시각을 확인한다.

### 7. Neovim

> Neovim: nvim-config 설치 확인, `install.ps1`, 새 터미널에서 `nvd --restart`, `:checkhealth work-kb`, 캡처 테스트. 사용법: `docs/USER-GUIDE.md`.

nvim-config가 먼저 설치돼 있어야 한다. 그다음 저장소 폴더에서 한다. `install.ps1`의 단계와 옵션(`-Offline`, `-WhatIf`, `-Yes`)은 `docs/USER-GUIDE.md` 5.2절.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1   # pip가 막혔으면 -Offline
nvd --restart                                                      # 새 터미널에서
```

- `nvd --restart`는 **새 터미널**에서 한다. 수정한 버퍼가 있으면 거부하니 먼저 저장한다.
- `:checkhealth work-kb`로 점검한다.
- 캡처 테스트: `<leader>k` 다음 `c`(쓰고 `C-c C-c`)로 한 줄, `C`나 `F`로 이미지 하나.
- 접두 키 교체: nvim-config `lua\plugins\work-kb.lua`의 `opts = { prefix = "<F8>" }`, 또는 `vim.keymap.set("n", "<F8>c", function() require("work-kb").call("capture") end)`. 바꾼 뒤 `nvd --restart`(`docs/USER-GUIDE.md` 6.2절).

### 8. Obsidian

> Obsidian: 볼트 열기, `obsidian/setup.md`.

볼트 루트를 열고 `obsidian/setup.md`를 따른다. 볼트를 저장소와 따로 두었다면 두 `.base` 파일을 볼트의 같은 경로로 복사한다. 표는 첫 `sync`·`link` 뒤에 채워진다.

### 9. 스케줄 등록

> 스케줄 등록(`SCHEDULING.md` 또는 init이 만든 명령).

```powershell
.venv\Scripts\python.exe scripts\kb.py schedule --company
```

출력된 `schtasks /Create …` 줄을 사람이 실행한다. ingest·compile 계열은 **비활성**으로 만들어진다(10단계에서 켠다). 상세는 `docs/SCHEDULING.md`.

### 10. 첫 2주와 상시화

> 첫 2주: 캡처 + `link` + `sync`. 그다음 `ingest`·`compile`, 마지막 Confluence 푸시 검토 후 상시화.

1. 캡처·link·sync만 쓴다.
2. `kb.py ingest`·`kb.py compile`을 `--dry-run`으로 검토한 뒤 실행하고, 비활성 스케줄을 켠다.
3. `kb.py push --dry-run`을 검토한 뒤 푸시한다.

### 11. 보안

> 보안: 회사에서 생긴 파일은 집으로 가져오지 않는다. 개선점은 말로 적어 집에서 반영.

- 회사의 노트·캐시·로그·`config.local.yaml`·스크린샷을 집으로 옮기지 않는다.
- 오류는 **내용 없이** 증상, 종료 코드, `doctor`의 `✗` 줄 이름만 적는다.
- 회사 자료를 집의 LLM에 보내지 않는다. `environment: company` 가드가 코드로도 막는다.

## 첫 실행 확인 (당일)

1. `kb.py doctor`: 5단계 기준.
2. `kb.py sync --jql "key = <실제키>" --dry-run`: 일감이 기대대로 나오는지.
3. `kb.py status`: `company`와 프로필 탐지 결과가 보이는지.
4. 캡처 한 줄 → `daily/오늘.md`의 `## 기록` 끝에 `- HH:MM …`이 생겼는지.
5. `kb.py link --dry-run` 후 `kb.py link`를 두 번: 두 번째는 바뀌는 파일이 0개.
6. 탐지 결과가 `docs/LIVE.md`의 예와 비교해 말이 되는지.

## 첫 2주에 확인할 것

- 캡처: 분류하지 않고 던진다. 고르고 싶어지면 설계 문제로 적어 둔다.
- link: `wiki/projects/`·`wiki/people/`·`wiki/topics/`의 `언급`이 맞게 쌓이는지, `project_keys` 밖 낱말을 키로 오인하지 않는지.
- sync: `kb.py status`의 마지막 동기화가 최근인지, 이슈 수와 이동(moved)이 Jira와 맞는지.
- 로그: `.state/logs/`에 오류가 쌓이지 않는지.
- 쓰기: 작은 댓글·전환 하나 뒤 `kb.py act list`에 `pending`·`failed`·`uncertain`이 남지 않는지.
- ingest/compile: `제안`의 출처 줄, 사이드카 품질, `포기한 편찬 단위` 수, 걸리는 시간.
- Confluence: `kb.py push --dry-run`이 `push_globs`만 포함하는지.
- 막힌 점·어색한 문구·빠진 기능은 11단계대로 말로 적는다.
