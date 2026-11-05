# 회사 PC로 옮기기 (DEPLOY)

집에서 만든 저장소 폴더를 회사 Windows PC로 복사하고 `kb.py init` 한 번으로 연결한다.
각 단계의 인용문(`>`)이 체크리스트 원문이고, 그 아래가 보충이다.

> 이 문서의 주소 예시(`https://jira.example.invalid` 등)는 자리표시자다. 실제 주소는 `config.local.yaml`에만 들어간다.

## 0. 준비물

| 항목 | 내용 |
|---|---|
| Python | **3.13 또는 3.14** (64비트) 권장. 3.15는 PyPI에 PyYAML wheel이 없다. 인터넷이 되면 pip가 원본(sdist)에서 순수 Python으로 빌드하지만(느리고 libyaml 가속 없음), `--no-index --find-links wheels` 설치는 실패한다. 더 낮은 버전은 거부된다 |
| pip | `python -m venv` + `pip`면 충분하다. 다른 도구는 필요 없다 |
| Neovim / Obsidian | Neovim 0.11 이상(Neovide는 선택), Obsidian(Bases 지원 버전) |
| 네트워크 | 사내 Jira·Confluence·LLM 서버에 닿으면 된다. pip가 막혀 있으면 `wheels/`로 설치 |
| `kb.py demo` | `tests/`를 제외하므로 회사 PC에서는 **쓸 수 없다**(집 전용). 회사에서는 6단계의 dry-run으로 확인한다 |

## 체크리스트

### 1. 저장소 폴더 복사

> 저장소 폴더 복사(`config.local.yaml`, `.state/`, `_trash/`, `examples/`, 집에서 만든 daily 제외).

제외 목록(D60 포함). 복사본에 **있으면 안 되는** 것:

| 경로 | 이유 |
|---|---|
| `config.local.yaml` | 집 연결 값. 회사에서 `init`이 새로 만든다 |
| `.state/` | 원장·동기화 시각·로그 |
| `_trash/` | 집에서 지운 것 |
| `examples/` | 샘플 볼트 |
| `daily/*.md`, `inbox/` 내용, `raw/assets/` 내용 | 집에서 만든 노트·이미지 (폴더와 `.gitkeep`은 남긴다) |
| `jira/*.md`, `jira/_views/*.md`, `jira/outbox.jsonl`, `jira/.cache.sqlite*` | 집 모의 서버 캐시와 큐 |
| `wiki/index.md` `wiki/decisions.md` `wiki/actions.md` `wiki/_proposals.md` `wiki/_lint.md`, `wiki/projects/*.md` `wiki/people/*.md` `wiki/topics/*.md` | 집에서 생성된 페이지 |
| `.venv*` (`.venv`, `.venv313`) | 가상환경은 경로가 박혀 있어 옮길 수 없다. 회사에서 새로 만든다 |
| `.git` | 이력은 옮기지 않는다 |
| `.pytest_cache`, `__pycache__` | 캐시 |
| `tests/` | 회사에서 테스트·demo를 돌리지 않는다. 모의 서버와 허구 fixture가 들어 있다 |
| `.obsidian/` | 볼트별 설정 |

남기는 것: `scripts/`, `nvim/`, `install.ps1`, `uninstall.ps1`, `obsidian/`, `prompts/`, `docs/`, `config.yaml`, `config.local.example.yaml`, `requirements*.txt`, `AGENTS.md`, `CLAUDE.md`, `.gitignore`, `.gitattributes`, **`wheels/`**(만들었다면), 빈 폴더 구조(`daily/` `inbox/` `raw/assets/` `jira/_views/` `wiki/…` 의 `.gitkeep`).

집에서는 `python scripts/deploy_check.py <대상 폴더>`가 같은 제외 규칙으로 복사하고 제외 경로가 없는지 확인한다(있을 때).
폴더 이름에 한글·공백이 있어도 된다(`D:\업무 도구\work-kb`처럼).

### 2. Python과 의존성

> Python 최신 확인, `pip install -r requirements.txt`(불가 시 `--no-index --find-links wheels`). 환경변수 `PYTHONUTF8=1`.

```powershell
python --version                      # 3.13.x 또는 3.14.x
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
# pip가 막혀 있을 때 (집에서 kb.py wheels로 만든 wheels/ 사용)
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels -r requirements.txt
setx PYTHONUTF8 1                     # 새 터미널부터 적용
```

- `python`이 없으면 `py -3.14 -m venv .venv`. `py -3`이 동작하지 않는 PC도 있으니 안 되면 `python`을 쓴다.
- 3.15: PyPI에 PyYAML wheel이 없다. 인터넷이 되면 `pip`가 원본(sdist)에서 순수 Python wheel을 빌드해 설치한다(느리고 libyaml 가속 없음). `--no-index --find-links wheels`는 `wheels/`에 3.15용 wheel이 없어 실패한다. 3.14 또는 3.13으로 venv를 만드는 것을 권장한다.
- 스크립트는 `.venv\Scripts\python.exe scripts\kb.py …`로 실행한다. (스케줄과 Neovim 플러그인은 이 경로를 자동으로 쓴다.)
- `PYTHONUTF8=1`이 없어도 `kb.py`와 Neovim·스케줄은 `-X utf8`을 직접 넘기지만, 터미널에서 직접 칠 때를 위해 설정해 둔다.
- wheels 만들기(집): `python scripts/kb.py wheels --targets 3.13,3.14`. 결과는 `wheels/`와 `wheels/MANIFEST.txt`. 상세는 `docs/PACKAGING.md`.

### 3. PAT 만들기

> Jira·Confluence PAT 생성.

각 서비스의 프로필 화면에서 개인 액세스 토큰(Personal Access Token)을 만든다. 토큰은 `init` 입력창에만 붙여 넣는다(화면에 가려진다). 파일·메모·채팅에 적지 않는다.

### 4. 연결 마법사

> `python scripts/kb.py init` → environment=company → Jira(프로젝트 키 선택) → Confluence(개인 스페이스) → LLM main(사내 Nemotron, 자동 탐지) → fallback(Gemma) → 스케줄.

```powershell
.venv\Scripts\python.exe scripts\kb.py init
```

- `environment`는 반드시 `company`. 그러면 LLM 주소에 공개 클라우드 호스트를 넣을 수 없다.
- Jira: base_url → PAT → 연결 확인 → 프로젝트 목록에서 `project_keys` 선택(번호 입력).
- Confluence: base_url → PAT → 개인 스페이스 자동 탐지 → 부모 페이지(선택).
- LLM: 주소 → 키(있으면) → 모델 선택 → 자동 탐지(json_mode·vision·추론 토글·컨텍스트). fallback 프로필도 같은 절차.
- 마지막 단계의 스케줄은 명령을 **출력만** 한다. 등록은 9단계에서 직접 한다.
- 비밀값은 Windows 자격 증명 저장소(keyring)에 들어가고 `config.local.yaml`에는 참조(`*_ref`)만 남는다.
- 일부만 다시: `kb.py init --section jira`(또는 `confluence`, `llm`). LLM 탐지만 다시: `kb.py init --redetect`.

### 5. doctor

> `python scripts/kb.py doctor` 전부 ✓. 공개 호스트 ✗가 하나라도 있으면 진행 금지.

```powershell
.venv\Scripts\python.exe scripts\kb.py doctor
```

기대하는 출력:

- `✓` 줄: Python, 의존성, config.yaml, config.local.yaml(`environment: company`), 공개 호스트 차단, Jira, Confluence, LLM 프로필, 쓰기 권한.
- `-` 줄은 정상이다(종료 코드에 영향 없음): `keyring` 백엔드 이름, `Jira 서버` 버전, `sprint 필드`, 볼트 경로의 한글 안내, PowerShell 실행 정책(막히지 않았을 때), 시간대, Neovim·Neovide·Obsidian 경로, 푸시 대상 파일 수(`푸시는 kb.py push로 직접 합니다`), `/models`가 없는 서버의 "연결됨, /models 없음".
- `✗` 줄이 있으면 줄 끝의 `→ 해결 방법`을 따른다. 대부분 `docs/TROUBLESHOOTING.md`에 있다.
- **허용되는 ✗ 하나**: 그룹 정책(GPO)이 PowerShell 실행 정책을 `Restricted`·`AllSigned`로 막아 PowerShell 줄이 ✗이면, 사용자가 고칠 수 없으므로 이것만은 ✗인 채로 진행한다(doctor는 종료 1). 클립보드 이미지 캡처(`C`) 대신 `F`(파일 선택)로 캡처한다. 다른 ✗는 모두 고친 뒤 진행한다.
- **공개 호스트 ✗(종료 5)**: 진행 금지. `kb.py init --section llm`으로 사내 주소로 고친다.
- 탐지 확인: 프로필별 줄에 `json_mode`·`vision`·`reasoning_toggle` 결과가 요약된다. `method: none`이면 TROUBLESHOOTING의 수동 설정을 본다.

### 6. 첫 동기화

> `jira_sync --jql "key = <실제키>" --dry-run` 확인 후 `kb.py sync --full`.

```powershell
.venv\Scripts\python.exe scripts\kb.py sync --jql "key = <실제키>" --dry-run
.venv\Scripts\python.exe scripts\kb.py sync --full
```

`--jql … --dry-run`은 받기만 하고 아무것도 쓰지 않는다. 결과가 기대와 맞으면 `sync --full`로 `project_keys × jql_mine` 범위를 받는다. 이어서 `kb.py status`로 동기화 시각을 확인한다.

### 7. Neovim

> Neovim: nvim-config 설치 확인, `install.ps1`, 새 터미널에서 `nvd --restart`, `:checkhealth work-kb`, 캡처 테스트. 사용법: `docs/USER-GUIDE.md`.

Neovim 쪽 일반 설정(Neovim, Neovide, 폰트, `nvd`)은 이 저장소가 아니라 별도 저장소 nvim-config가 설치한다. 먼저 그 PC에 nvim-config가 설치되어 있어야 한다(`docs/USER-GUIDE.md` 5.2절). 그다음 이 저장소 폴더에서 한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
nvd --restart
```

- `install.ps1`은 `.venv`(2단계에서 이미 만들었으면 유지), 사용자 환경 변수 `WORK_KB_ROOT`, 읽기 전용 점검만 한다. pip가 막혀 있으면 `-Offline`을 준다(`wheels/` 사용). 먼저 `-WhatIf`로 할 일만 볼 수 있다.
- 환경 변수는 새 터미널부터 보이고 데몬의 환경은 시작할 때 고정되므로, **새 터미널**에서 `nvd --restart`를 한다(수정한 버퍼가 있으면 거부하니 먼저 저장).
- Neovim에서 `:checkhealth work-kb`가 코드 루트·볼트·접두 키·Python·상주 서버를 점검한다.
- 캡처 테스트는 `<leader>k` 다음 `c`로 한 줄, `C`(클립보드 이미지)나 `F`(파일)로 이미지 한 번씩.

### 8. Obsidian

> Obsidian: 볼트 열기, `obsidian/setup.md`.

볼트 루트를 열고 `obsidian/setup.md`의 설정과 확인 목록을 따른다. Bases 파일(`jira/_views/일감.base`, `wiki/엔티티.base`)은 저장소에 들어 있어 1단계 복사본에 이미 있다(코드가 만들어 주지 않는다). 볼트를 저장소와 따로 두었다면 두 파일을 볼트의 같은 경로로 손으로 복사한다(`obsidian/setup.md` 1절). 표는 첫 `sync`·`link` 뒤에 채워진다.

### 9. 스케줄 등록

> 스케줄 등록(`SCHEDULING.md` 또는 init이 만든 명령).

```powershell
.venv\Scripts\python.exe scripts\kb.py schedule --company
```

출력된 `schtasks /Create … /XML …` 줄을 사람이 실행한다. 이 저장소의 어떤 코드도 `schtasks`를 대신 실행하지 않는다.
`--company`로 만들면 ingest·compile 계열 작업은 **비활성**으로 생성된다. 활성화 시점은 10단계. 절차와 확인·비활성 방법은 `docs/SCHEDULING.md`.

### 10. 첫 2주와 상시화

> 첫 2주: 캡처 + `link` + `sync`. 그다음 `ingest`·`compile`, 마지막 Confluence 푸시 검토 후 상시화.

아래 "첫 2주 확인 항목"을 본다. 순서는 (1) 캡처·link·sync만, (2) 문제가 없으면 `kb.py ingest`와 `kb.py compile --dry-run`으로 diff 검토 후 실제 실행, 비활성으로 만든 스케줄 활성화, (3) 마지막으로 `kb.py push --dry-run`을 검토한 뒤 푸시.

### 11. 보안

> 보안: 회사에서 생긴 파일은 집으로 가져오지 않는다. 개선점은 말로 적어 집에서 반영.

회사에서 생긴 노트·캐시·로그·`config.local.yaml`·스크린샷을 USB·메일·클라우드로 집에 옮기지 않는다. 오류가 나면 **내용 없이** 증상과 종료 코드, `doctor`의 `✗` 줄 이름만 말로 옮겨 적는다. 회사 자료는 집의 LLM(DeepInfra 등)에 절대 보내지 않는다 — `environment: company` 가드가 이를 코드로도 막는다.

## 첫 실행 확인 (당일)

1. `kb.py doctor`: 위 5단계 기준.
2. `kb.py sync --jql "key = <실제키>" --dry-run`: 일감이 기대대로 나오는지.
3. `kb.py status`: environment가 `company`, 프로필 연결·탐지 결과가 보이는지.
4. 접두 키 `c`로 캡처 한 줄 → `daily/오늘.md`의 `## 기록` 끝에 `- HH:MM …`이 생겼는지.
5. `kb.py link --dry-run` 후 `kb.py link`: 두 번째 실행은 바뀌는 파일이 0개여야 한다.
6. 탐지 결과 확인: `docs/LIVE.md`의 예와 비교해 `json_mode`·`vision`·`reasoning_toggle`이 말이 되는지.

## 첫 2주에 확인할 것

- **캡처 습관**: 하루 몇 줄이든 `접두 키 c`로 던지고 분류하지 않는다. 폴더·태그를 고르고 싶어지면 그건 설계 문제이니 적어 둔다.
- **link**: `wiki/projects/`·`wiki/people/`의 `언급`이 맞는 곳에 쌓이는지. 키가 잘못 인식되면(`project_keys` 밖의 `ABC-123` 같은 낱말) 목록을 적는다.
- **sync**: 15분 스케줄이 돌아 `kb.py status`의 마지막 동기화가 최근인지, 이슈 수가 Jira 화면과 맞는지, 이동된 일감(moved)이 따라가는지.
- **로그**: `.state/logs/`에 오류가 쌓이지 않는지(`docs/SCHEDULING.md`).
- **쓰기**: `kb.py act`로 작은 댓글 하나, 사용한다면 전환 하나. `kb.py act list`에 `pending`·`failed`·`uncertain`이 남지 않는지.
- **ingest/compile 도입 후**: 처음에는 `--dry-run` diff를 눈으로 보고, `wiki/decisions.md`의 `제안` 항목 근거(출처 줄)가 맞는지, 이미지 사이드카가 쓸 만한지, `포기한 편찬 단위`가 늘지 않는지.
- **비용·시간**: compile이 걸리는 시간, 탐지된 추론 방식이 실제 서버 응답과 맞는지.
- **Confluence**: `kb.py push --dry-run` 계획이 `push_globs`(wiki 페이지)만 포함하는지, 개인 스페이스에서 첫 페이지를 확인.
- **적어 둘 것**: 막힌 점, 어색한 한국어 문구, 빠진 기능. 11단계 규칙대로 말로 적어 집에서 반영한다.
