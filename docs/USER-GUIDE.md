# work-kb 사용자 안내서 (Neovim 판)

이 문서는 `nvim` 브랜치의 work-kb를 설치하고 매일 쓰는 방법을 설명한다. 왜 이렇게 만들었는지, 어떤 파일과 폴더가 어디에 있어야 하는지, 어떻게 설치하는지, 어떤 키로 무엇을 하는지를 차례로 적는다.
Neovim 자체(Neovide, 폰트, 데몬, `nvd` 명령)는 별도 저장소 nvim-config의 몫이고, 그 사용법은 nvim-config의 `docs\USER_GUIDE.md`(특히 "work-kb" 절과 "파일 위치" 표)에 있다. 이 문서는 그 내용을 되풀이하지 않고 work-kb 쪽만 다룬다.
Python 쪽 명령(`kb.py`), 볼트 구조, Jira·Confluence·LLM 동작은 `docs/README.md`, 회사 PC 이식은 `docs/DEPLOY.md`, 오류 대응은 `docs/TROUBLESHOOTING.md`에 있다.

## 1. 취지

업무 메모, 메모에서 편찬한 위키, Jira 일감 캐시가 흩어져 있으면 쓰는 쪽이 분류와 정리에 시간을 쓰게 된다. work-kb는 이것을 **평범한 마크다운 폴더 하나**(볼트)에 모으고, 하나의 Python 명령(`scripts\kb.py`)으로 다룬다. Obsidian은 폴더를 읽는 창이고, 편집기는 그 명령을 부르는 입력 창이다.

- 사람은 분류하지 않는다. 구조는 날짜뿐이고, 한 줄 던지는 데 키 하나면 된다.
- Jira가 진실의 원천이다. 로컬에는 읽기 캐시만 두고, 쓰기는 작은 것(댓글, 전환, 워크로그, 생성)만 큐를 거쳐 보낸다.
- LLM 없이도 완결된다. LLM이 없으면 위키 편찬 단계 하나만 빠진다.
- 파일은 그대로 마크다운이라 어느 도구로도 열 수 있다.

Neovim 판은 이 입력 창을 Neovim 플러그인으로 만든 것이다. 같은 저장소의 `main` 브랜치는 Emacs 판이다.

## 2. 목표

| 목표 | 구현 방법 | 확인된 결과 |
|---|---|---|
| 키 하나로 메모 캡처 | `<leader>k` 접두 키 + 한 글자, `:WorkKb <이름>` 명령 | Neovim 헤드리스 시험(Lua 스펙)으로 확인. 실제 Neovide 창에서는 미확인 |
| 편집기가 느리지 않을 것 | 플러그인은 처음 쓸 때 기능 모듈을 불러오고, 상주 도우미 `kb.py serve`가 명령을 대신 실행한다 | 명령 하나당 Python 시작 비용 170~235 ms(집 PC 측정, 빈 Python은 42 ms)를 줄인다. 도우미가 없으면 한 번 실행 방식으로 되돌아간다 |
| 일반 Neovim 설정과 한 PC에 공존 | work-kb는 플러그인 하나. nvim-config가 환경 변수 `WORK_KB_ROOT`로 불러온다 | nvim-config의 스모크 시험(가짜 플러그인) 통과. 실제 nvim-config와 실제 플러그인을 헤드리스 Neovim에서 연결해 키 13개, `:checkhealth work-kb`, `status` 실행까지 확인. 실제 Neovide 창에서는 미확인 |
| 설치가 되돌릴 수 있을 것 | `install.ps1`은 `.venv`, 사용자 환경 변수 `WORK_KB_ROOT`, 점검만 한다. Neovim 설정 폴더에는 쓰지 않는다 | 가짜 프로필에서 Windows PowerShell 5.1과 PowerShell 7로 설치와 제거 시험 |
| 안전한 쓰기 | 자동 Jira 전환 없음, 작은 쓰기는 outbox 큐, 삭제는 `_trash\`로 이동, 기계는 관리 구역만 고침 | Python 쪽 자동 시험으로 확인 |
| 회사 PC로 이식 | 저장소 폴더 복사 + `kb.py init` | `docs/DEPLOY.md`. 실제 회사 서버에서는 미확인 |

범위 밖: Windows 외 운영체제, Neovim 자체 설정(폰트, LSP, 플러그인 관리자)은 이 저장소가 다루지 않는다.

## 3. 전체 구조

```text
 nvim-config 저장소 (별도)                     이 저장소 (nvim 브랜치)
 ┌──────────────────────────────┐            ┌─────────────────────────────────┐
 │ nvd ─ Neovide 창             │            │ nvim\lua\work-kb\*.lua  플러그인 │
 │        │                     │  WORK_KB_  │ nvim\plugin\work-kb.lua :WorkKb  │
 │ 숨김 Neovim (데몬)  ─────────┼─ ROOT ────▶│ nvim\after\ftplugin\markdown.lua │
 │  lua\plugins\work-kb.lua     │            └──────────────┬──────────────────┘
 └──────────────────────────────┘                           │ JSON 줄 (표준 입출력)
                                                            ▼
                                       kb.py serve (상주 도우미, 30분 놀면 스스로 끝남)
                                                            │
                         ┌──────────────────────────────────┼───────────────────────┐
                         ▼                                  ▼                       ▼
                 볼트 폴더 (daily\ inbox\ raw\ jira\ wiki\ _trash\ .state\)   Jira · Confluence · LLM
                         ▲
                         └── Obsidian이 같은 폴더를 연다
```

- 데몬은 nvim-config가 띄우는 화면 없는 Neovim이다. 시작 직후 모든 플러그인을 미리 불러오므로 work-kb도 그때 불러와지고 `kb.py serve`가 뜬다. 그냥 `nvim`으로 열면 첫 `<leader>k…` 키나 `:WorkKb`에서 불러온다.
- 도우미(`kb.py serve`)는 요청이 없으면 30분 뒤 스스로 끝나고, 다음 요청에서 다시 뜬다. 쓸 수 없으면 명령을 한 번 실행 방식으로 돌린다.

저장소의 두 브랜치(GitHub의 5solas/work-kb 한 저장소):

| 브랜치 | 편집기 | 비고 |
|---|---|---|
| `main` | Emacs (`emacs\work-kb.el`) | |
| `nvim` | Neovim (`nvim\`) | 이 문서의 대상. Emacs 파일은 없다 |

Python 쪽, 볼트 구조, 설정 파일, Jira·Confluence·LLM 동작은 두 브랜치가 같다. 작성자의 집 PC에서는 `main`을 `D:\work\work-kb`에, `nvim`을 git worktree로 `D:\work\work-kb-nvim`에 두었다. 한 PC에서 한 브랜치만 쓰면 worktree는 필요 없다.

## 4. 파일과 폴더 위치

**규칙**

- 저장소 폴더는 어디에 두어도 된다(예: `D:\work\work-kb`). 경로에 한글·공백이 있어도 된다.
- 플러그인이 어디에 있는지는 사용자 환경 변수 `WORK_KB_ROOT`(저장소 폴더 경로)가 가리킨다. `install.ps1`이 정한다.
- **플러그인을 Neovim 설정 폴더(`%LOCALAPPDATA%\nvim`)에 복사하거나 링크하지 않는다.** 저장소의 `nvim\` 폴더가 플러그인 원본이고, nvim-config가 그 자리에서 직접 불러온다.

`<repo>`는 저장소 폴더이다.

| 경로 | 내용 | 만드는 주체 | git |
|---|---|---|---|
| `<repo>\scripts\kb.py`, `scripts\lib\` | 단일 CLI와 모듈 | git | 포함 |
| `<repo>\nvim\lua\work-kb\*.lua` | Neovim 플러그인 모듈 | git | 포함 |
| `<repo>\nvim\plugin\work-kb.lua` | `:WorkKb` 명령 등록 | git | 포함 |
| `<repo>\nvim\after\ftplugin\markdown.lua` | 볼트 안 Markdown 버퍼에만 적용되는 설정 | git | 포함 |
| `<repo>\nvim\capture.ahk` | 선택 사항: AutoHotkey v2 전역 단축키 (미확인, 6.7절) | git | 포함 |
| `<repo>\install.ps1`, `uninstall.ps1` | 이 저장소의 설치·제거 스크립트 | git | 포함 |
| `<repo>\config.yaml` | 구조 설정(연결 값 없음) | git | 포함 |
| `<repo>\config.local.yaml` | 이 PC의 연결 설정 | `kb.py init` | 제외 |
| `<repo>\.venv\` | Python 가상환경 | `install.ps1` | 제외 |
| `<repo>\daily\`, `inbox\`, `raw\assets\`, `jira\`, `wiki\`, `_trash\`, `.state\` | 볼트 내용과 상태 | 사용자, `kb.py` | 내용은 제외 |
| `<repo>\wheels\` | 오프라인 설치용 패키지(선택) | `kb.py wheels` | 제외 |
| 사용자 환경 변수 `WORK_KB_ROOT` = `<repo>` | nvim-config에 플러그인 위치를 알린다 | `install.ps1` | 해당 없음 |
| `%LOCALAPPDATA%\work-kb\install-manifest.json` | `install.ps1`이 만든 것의 기록(제거에 쓰임) | `install.ps1` | 해당 없음 |
| `<nvim-config>\lua\plugins\work-kb.lua` | nvim-config에서 work-kb를 불러오는 파일 하나 | nvim-config 저장소 | 그 저장소 |
| `%LOCALAPPDATA%\nvim` → `<nvim-config>` 정션 | Neovim이 설정을 읽는 자리 | nvim-config의 설치 스크립트 | 해당 없음 |
| Windows 자격 증명 관리자, 서비스 이름 `work-kb` | Jira·Confluence 토큰 (파일에는 적지 않는다) | `kb.py init` | 해당 없음 |

볼트를 코드 폴더와 따로 두고 싶으면 명령의 `--root` 또는 환경 변수 `KB_ROOT`를 쓴다(`docs/README.md` 2절). 이 안내서의 설치 절차는 볼트가 저장소 폴더와 같은 기본 배치를 전제로 한다.

## 5. 설치

### 5.1 준비물

Windows 10/11, git, PowerShell, Python 3.13 또는 3.14(또는 `uv`), Neovim 0.11 이상. Neovide는 선택이지만 nvim-config는 이를 전제로 한다.
Neovim 버전: `install.ps1`의 점검은 0.11 이상이면 통과하고, `:checkhealth work-kb`는 0.12 미만이면 오류로 표시한다. 작성자는 0.12에서 썼다. 0.12를 권한다.

### 5.2 새 PC에 설치 (순서가 중요하다)

1. **nvim-config를 먼저 설치한다 (PC당 한 번).** 저장소를 받고 설치 스크립트를 실행한다. scoop으로 도구를 깔고, 폰트·정션·`nvd`의 PATH 등록·플러그인 설치까지 한다. 저장소 주소와 자세한 내용은 nvim-config의 `docs\USER_GUIDE.md` 4절.

   ```powershell
   git clone <nvim-config 저장소 주소> D:\work\nvim-config
   pwsh -File D:\work\nvim-config\scripts\install.ps1
   ```

2. **work-kb 저장소를 `nvim` 브랜치로 받고 설치한다.** 저장소 주소는 루트 `README.md`의 빠른 설치에 있다.

   ```powershell
   git clone -b nvim <work-kb 저장소 주소> D:\work\work-kb
   cd D:\work\work-kb
   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
   ```

   `install.ps1`은 세 단계를 차례로 하고, 각 단계마다 `STEP <이름> <상태> <설명>` 한 줄을 출력한다.

   | 단계 | 하는 일 |
   |---|---|
   | `venv` | Python 3.13/3.14 `.venv`가 있으면 유지하고 없으면 만든다. `kb.py --help`가 돌고 `requirements.txt`의 패키지가 모두 설치돼 있으면 그대로 두고, 아니면 `requirements.txt`를 설치한다. 인터넷이 막혀 있으면 `wheels\`에서 설치한다 |
   | `env` | 사용자 환경 변수 `WORK_KB_ROOT`를 이 저장소 폴더로 정한다. 이 스크립트가 정하지 않은 다른 값이 이미 있으면 바꿀지 묻는다(`-Yes`면 묻지 않고 바꾼다). 이전 값은 기록해 둔다 |
   | `check` | 읽기 전용 점검: `nvim` 버전, nvim-config가 work-kb를 불러오는지, `nvd`가 PATH에 있는지, `WORK_KB_ROOT`, 옛 설치 잔재를 보고 `kb.py doctor`를 실행한다. 문제는 경고(`warn`)로만 알린다. nvim-config를 받거나 설치하지는 않는다 |

   옵션: `-WhatIf`(할 일만 출력하고 아무것도 바꾸지 않는다), `-Only venv,env,check` 또는 `-Skip …`(단계 선택), `-Yes`(질문에 자동 승인), `-Offline`(인터넷 없이 `wheels\`에서만 설치), `-WheelsDir <폴더>`(wheels 위치 지정). 종료 코드는 성공 0, 단계 실패 1, 인자 오류 2이다. 끝에 "놓은 것"과 "다음 단계"를 출력한다.

   이 스크립트가 만드는 것은 `.venv`, 사용자 환경 변수 `WORK_KB_ROOT`, 매니페스트 파일뿐이다. `%LOCALAPPDATA%\nvim*`, `%APPDATA%\neovide`, nvim-config 폴더, PATH, 시작프로그램 폴더, 폰트는 건드리지 않는다.

3. **새 터미널을 열고 데몬을 다시 시작한다.** 사용자 환경 변수는 그 뒤에 시작한 프로그램에만 보이고, 데몬의 환경은 시작할 때 고정된다.

   ```powershell
   nvd --restart
   ```

   수정한 버퍼가 있으면 거부하므로 먼저 `:wa`로 저장한다. 데몬이 떠 있지 않았다면 `nvd`만 실행한다.

4. **연결 설정과 점검.**

   ```powershell
   cd D:\work\work-kb
   .venv\Scripts\python.exe scripts\kb.py init
   .venv\Scripts\python.exe scripts\kb.py doctor
   ```

   집에서 모의 서버로 시험하려면 `kb.py init --dev`(`docs/README.md`)를 쓴다. 회사 PC는 `docs/DEPLOY.md`의 4~5단계를 따른다.

5. **Neovim에서 확인한다.** `nvd`로 창을 열고 `:checkhealth work-kb`를 실행한다(코드 루트, 볼트, 접두 키, Python, 상주 서버를 점검한다). 그다음 `Space k c`로 한 줄을 캡처한다.

### 5.3 다른 PC에서 갱신

```powershell
cd D:\work\work-kb
git pull
powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
nvd --restart
```

`git pull` 한 번으로 플러그인 코드가 바뀐다. 데몬은 시작할 때 코드를 읽으므로 `nvd --restart`가 필요하다. 수정한 버퍼가 있으면 먼저 저장한다.

### 5.4 오프라인 PC, 회사 PC

폴더를 복사하는 절차와 제외 목록, `wheels\`로 설치하는 방법은 `docs/DEPLOY.md`가 정한다. 복사한 뒤에는 위 2~5 단계를 같은 순서로 한다(`install.ps1`에 `-Offline`을 준다). 회사 규칙(회사에서 생긴 파일을 집으로 가져오지 않는다 등)은 그대로 적용된다.

nvim-config를 회사 PC에 설치하는 방법은 이 저장소의 범위 밖이고 확인하지 못했다. 그 설치 스크립트는 scoop과 GitHub 접근을 쓴다. 접근이 막혀 nvim-config를 쓸 수 없는 PC에서는 두 가지 길이 있다.

- Neovim 없이 `kb.py` 명령만 쓴다(`docs/README.md`의 명령 표). 캡처·동기화·편찬은 모두 명령으로 된다.
- 이미 있는 Neovim 설정에 두 줄을 넣어 플러그인을 직접 불러온다. `<repo>`는 실제 경로로 바꾼다.

  ```lua
  vim.opt.rtp:append("<repo>/nvim")
  require("work-kb").setup({ root = "<repo>", prefix = "<leader>k" })
  ```

  이 방식은 헤드리스 시험(Lua 스펙)과 같은 불러오기 방식이다. `nvim\after\ftplugin\markdown.lua`까지 쓰려면 `<repo>/nvim/after`도 `rtp`에 더한다.

### 5.5 폴더를 옮길 때

환경 변수가 옛 경로를 가리키므로 순서를 지킨다.

1. 옛 자리에서 `uninstall.ps1`을 실행한다.
2. 폴더를 옮긴다.
3. 새 자리에서 `install.ps1`을 실행한다.
4. 새 터미널에서 `nvd --restart`.

### 5.6 제거

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1
```

`install.ps1`이 정한 `WORK_KB_ROOT`를 이전 값으로 되돌리거나(없었으면 지운다) 한다. 누가 그 뒤에 값을 바꿨으면 그대로 둔다. `-RemoveVenv`를 주면 `install.ps1`이 만든 `.venv`도 지운다. `-WhatIf`로 먼저 볼 수 있다. 저장소, 볼트 내용, nvim-config, `%LOCALAPPDATA%\nvim*`는 건드리지 않는다. 끝나면 새 터미널에서 `nvd --restart`를 한다(`WORK_KB_ROOT`가 없으면 nvim-config는 work-kb를 불러오지 않는다).

## 6. 사용법

### 6.1 하루 흐름

리더 키는 Space이므로 `<leader>kc`는 Space, k, c를 차례로 누른다.

1. 아침: `<leader>kt`로 오늘 daily를 연다. `## 오늘`(Jira 일감과 할 일)과 `## 이월`이 준비되어 있다.
2. 하루 중: `<leader>kc`로 한 줄씩 던진다. `daily\오늘.md`의 `## 기록` 끝에 `- HH:MM …`으로 쌓인다. 분류하지 않는다. 스크린샷은 `<leader>kC`(클립보드) 또는 `<leader>kF`(파일), 제목이 필요한 긴 글은 `<leader>ki`.
3. 찾기와 열기: `<leader>ks`로 검색하고, 커서가 Jira 키나 링크 위에 있을 때 `<leader>ko`로 연다.
4. 연결: `<leader>kl`이 노트의 Jira 키와 `@이름`을 엔티티 페이지에 엮는다(LLM 불필요).
5. Jira: `<leader>kj` 메뉴에서 댓글, 전환, 워크로그, 동기화를 한다(6.4절).
6. 편찬: `<leader>kG`로 미리 보고 `<leader>kg`로 이미지 추출(ingest)과 위키 편찬(compile)을 돌린다. LLM이 없으면 이 단계만 빠진다. 주제 제안은 `<leader>ka`로 승인한다.
7. 상태: `<leader>k?`.

### 6.2 키 표

접두 키 `<leader>k` 다음에 누른다. 같은 기능을 `:WorkKb <이름>`으로도 실행한다.

| 키 | 이름 | 하는 일 |
|---|---|---|
| `c` | `capture` | 한 줄을 입력받아 오늘 daily `## 기록` 끝에 추가한다 |
| `C` | `capture-clipboard-image` | 클립보드의 이미지를 `raw\assets\`에 저장하고 daily에 링크를 남긴다. Windows PowerShell 5.1이 필요하다(회사 정책으로 막히면 `F`) |
| `F` | `capture-image-file` | 이미지 파일을 골라 같은 방식으로 캡처한다 |
| `i` | `capture-inbox` | 제목 있는 긴 글을 `inbox\`에 쓴다 |
| `t` | `today` | 오늘 daily를 준비하고(오늘·이월 구역) 연다 |
| `s` | `search` | daily·inbox·wiki·jira에서 낱말을 검색한다(공백은 AND) |
| `o` | `open-at-point` | 커서 위치의 Jira 키나 링크를 연다 |
| `l` | `link` | 키와 `@이름`을 엔티티 페이지에 연결한다 |
| `?` | `status` | 상태 요약(마지막 동기화, 대기 outbox, 미처리 제안·이미지 등) |
| `j` | `jira` | Jira 작업 메뉴를 연다(6.4절) |
| `g` | `ingest` | 이미지 글자 추출(ingest)에 이어 위키 편찬(compile)을 실행한다 |
| `G` | `ingest-dry-run` | 위와 같되 `--dry-run`으로 실행한다: 바뀔 내용만 보이고 파일은 바꾸지 않는다 |
| `a` | `proposals` | 주제 제안을 보고 승인한다 |

`:WorkKb`만 입력하면 이름 목록이 나오고, `:WorkKb today`처럼 이름을 주면 그 기능이 실행된다. 이름은 탭 완성이 된다. 인수는 받지 않는다(`jira`만 작업 이름을 받는다).
키는 `<leader>k` 접두 아래에 `nvim-config\lua\plugins\work-kb.lua`가 붙인다. 접두 키를 바꾸는 것은 그 파일(nvim-config 저장소)의 일이다.

### 6.3 비주얼 모드와 범위 지정

| 키 | 이름 | 하는 일 |
|---|---|---|
| `c` | `capture` | 선택한 글을 캡처 입력에 미리 넣는다 |
| `i` | `capture-inbox` | 선택한 글을 inbox 메모에 미리 넣는다 |

비주얼 모드에서 `<leader>kc`, `<leader>ki`를 누른다. 명령줄로는 범위를 준다(`:'<,'>WorkKb capture`).

### 6.4 Jira 메뉴

`<leader>kj`를 누르면 메뉴가 뜨고 키 하나를 더 누른다. Esc, Ctrl-C, Ctrl-G는 그만둔다. 직접 부르려면 `:WorkKb jira <작업 이름>`(예: `:WorkKb jira check-done`).

| 키 | 작업 이름 | 하는 일 |
|---|---|---|
| `v` | `view` | 커서·현재 줄의 Jira 키(없으면 물어본 키)의 `jira\KEY.md`를 연다 |
| `c` | `comment` | 댓글. 기본 본문은 현재 줄이고, daily 줄을 고치지 않고 보내면 그 줄을 근거로 보낸다 |
| `t` | `transition` | 가능한 전환 목록에서 고르고, 확인한 뒤에만 보낸다 |
| `a` | `assign` | 담당자(Jira 사용자명) 지정 |
| `w` | `worklog` | 워크로그. 시간은 `1h30m`, `2h`, `45m` 꼴만 된다. 설명은 선택 |
| `n` | `create` | 새 일감. 프로젝트(`project_keys`에서 고름)·요약·유형·설명을 받고 확인 뒤에 만든다 |
| `p` | `promote` | 현재 daily 줄을 새 일감으로 올린다. daily 파일에서만 되고 확인을 묻는다. 성공하면 줄 끝에 `[[새키]]`가 붙는다 |
| `q` | `query` | 라이브 JQL 조회. 캐시에는 쓰지 않는다 |
| `S` | `sync` | 동기화 (`kb.py sync`) |
| `f` | `flush` | outbox에 남은 대기 항목을 다시 보낸다 |
| `d` | `check-done` | 완료 전환 제안 목록. 제안만 보이고 아무것도 바꾸지 않는다 |

쓰기 작업은 outbox 큐를 거친다. 네트워크가 끊기면 큐에 남고 나중에 `f`로 보낸다. 인증 실패(PAT 만료)면 `kb.py init`으로 토큰을 다시 넣는다. 결과는 `*work-kb-jira*` 등의 버퍼에 남는다.

### 6.5 볼트 안 Markdown 버퍼의 설정

볼트 폴더 아래의 `.md` 파일을 열면 `nvim\after\ftplugin\markdown.lua`가 그 버퍼에만 다음을 적용한다. 볼트 밖의 Markdown과 다른 파일 형식은 건드리지 않는다. 볼트는 `setup`의 `vault`이고, 없으면 저장소 폴더이다.

| 설정 | 효과 |
|---|---|
| `wrap`, `linebreak`, `breakindent` | 긴 기록 줄을 화면에서만 접는다. 파일에는 줄바꿈을 넣지 않는다 |
| `textwidth=0` | 자동 줄바꿈 없음. 기록 한 줄이 파일 한 줄이다(`kb.py`가 줄 단위로 읽는다) |
| 들여쓰기 2칸(`expandtab`, `shiftwidth=2` 등) | 목록 들여쓰기를 `kb.py`가 쓰는 하위 목록과 맞춘다 |
| `nospell` | 한글 본문에 맞춤법 밑줄을 긋지 않는다 |
| 다시 읽기 | `BufEnter`·`FocusGained` 때, 디스크에서 바뀐 파일을 묻지 않고 다시 읽는다(예약 작업이 고친 daily 등). 수정 중인 버퍼는 그대로 둔다 |

또 볼트 안에서 새로 만드는 `.md`는 UTF-8·LF로 저장되고, 기존 파일은 줄바꿈 형식을 그대로 둔다.

### 6.6 상주 도우미와 설정

기본값은 `server.enable = true`, 놀면 끝나는 시간 1800초, `VimEnter` 뒤 300 ms에 띄운다. Python은 직접 지정 → `<repo>\.venv\Scripts\python.exe` → PATH의 `python` → `py -3` 순으로 찾는다. 도우미 상태는 `:checkhealth work-kb`에서 본다.

### 6.7 선택 사항: 전역 캡처 단축키 `capture.ahk` (미확인)

`nvim\capture.ahk`는 AutoHotkey v2 스크립트이다. 어느 프로그램에 있든 Ctrl+Alt+Shift+C로 `:WorkKb capture`, Ctrl+Alt+Shift+V로 `:WorkKb capture-clipboard-image`를 nvim-config 데몬에 보낸다(`nvd`로 창을 앞으로 가져온다). **미확인**: 이 파일은 한 번도 실행해 보지 못했다(개발 PC에 AutoHotkey가 없다). 데몬 쪽 절반만 헤드리스로 시험했다. 쓰려면 AutoHotkey v2를 설치하고 파일을 더블클릭하며, 처음에 두 단축키를 한 번씩 눌러 본다. 로그온 때 자동 실행은 `shell:startup` 폴더에 바로 가기를 넣는다(이 저장소는 넣지 않는다).

## 7. 안전 규칙

- 자동 Jira 전환은 없다. 전환·새 일감·승격은 고르고 확인한 뒤에만 보낸다. `d`는 제안만 보인다.
- 작은 Jira 쓰기(댓글, 전환, 워크로그, 생성)는 outbox 큐를 거친다. 결과를 모르는 항목(`uncertain`)은 자동으로 다시 보내지 않는다(`docs/README.md` 10절).
- 삭제는 `_trash\`로 옮기는 것이다.
- 기계는 `jira\`·`wiki\`의 관리 구역만 다시 쓴다. 그 구역은 직접 고치지 않는다.
- LLM이 없으면 편찬(compile) 단계만 빠진다. 회사 환경(`environment: company`)에서는 공개 LLM 호스트로 보내는 일이 코드로 막힌다.
- 토큰은 Windows 자격 증명 관리자에만 둔다. 파일, 메모, 채팅에 적지 않는다.

## 8. 문제 해결

| 증상 | 원인과 해결 |
|---|---|
| `<leader>k` 키나 `:WorkKb`가 없다 | 데몬이 `WORK_KB_ROOT` 없이 시작됐거나 `<값>\nvim\lua\work-kb\init.lua`가 없다. 새 터미널에서 `echo $env:WORK_KB_ROOT`로 확인한다. 비어 있으면 `install.ps1 -Only env`를 실행하고, **터미널을 한 번 더 새로 연 뒤** `nvd --restart`를 실행한다(데몬은 자기를 띄운 터미널의 환경을 물려받는다) |
| 설치는 됐는데 상태를 모르겠다 | Neovim에서 `:checkhealth work-kb` |
| `nvd`가 `daemon is not responding` | 데몬이 스왑 파일 질문 등에서 입력을 기다린다. 창에서 답하거나 `nvd --stop --force`(수정 내용은 사라진다). 개발 중 gitsigns가 `git`을 시작하는 순간 헤드리스 Neovim이 멈춘 적이 있다(원인 미확인) |
| `Python을 찾지 못했습니다` | `<repo>`에서 `.venv`를 만든다(`install.ps1 -Only venv`). 그래도 안 되면 `docs/TROUBLESHOOTING.md`의 "`py -3`이 동작하지 않는다" |
| `nvd --restart`가 거부한다 | 수정한 버퍼가 있다. `:wa`로 저장한 뒤 다시 실행한다 |
| `install.ps1`의 `check`가 경고한다 | 경고 줄 아래의 안내를 따른다(nvim-config 없음, `nvd` 없음 등). 새 터미널에서 다시 확인한다 |

그 밖의 오류(연결, 인코딩, outbox)는 `docs/TROUBLESHOOTING.md`, nvim-config 쪽 증상은 nvim-config의 `docs\USER_GUIDE.md` 7절을 본다.

## 9. 검증 상태

2026-11-06 기준.

**자동 시험으로 확인한 것 (집 PC)**

- Python 시험 전체: Python 3.13과 3.14.
- 플러그인의 Lua 스펙: 헤드리스 Neovim.
- `install.ps1`, `uninstall.ps1`: 가짜 프로필에서 Windows PowerShell 5.1과 PowerShell 7.
- nvim-config의 스모크 시험: 가짜 플러그인으로 연결 확인.

| 시험 | 결과 |
|---|---|
| 전체(`pytest`), Python 3.14 | 2170 통과, 3 건너뜀 |
| 전체(`pytest`), Python 3.13 | 2170 통과, 3 건너뜀 |
| 그중 플러그인 Lua 스펙 | 166개, 콘솔 상속과 창 없는 콘솔(cp949) 두 방식 모두 통과 |
| 그중 설치·제거 스크립트 | 37개 |

건너뛴 3개는 이 PC의 실행 조건 때문이다: 클립보드를 열 수 없는 세션 2개, 심볼릭 링크 권한 1개.

**확인하지 못한 것**

- 실제 Neovide 창에서의 동작(키, 입력창, 한글 입력기 IME).
- `nvim\capture.ahk`(실행해 본 적 없음).
- 실제 Jira·Confluence Data Center와 사내 LLM 서버(집 시험은 모의 서버).
- 새 PC에서 처음부터 설치하는 과정.

**알려진 미해결 점**: 개발 중 gitsigns 플러그인이 `git`을 시작하는 순간 헤드리스 Neovim이 멈춘 적이 있다. 원인은 찾지 못했다. `nvd`가 데몬이 응답하지 않는다고 하면 8절을 본다.
