# work-kb (nvim 브랜치)

업무 메모, 그 메모에서 편찬한 위키, Jira 일감 캐시를 평범한 마크다운 폴더 하나에 모으고 Python CLI 하나(`scripts/kb.py`)로 다루는 Windows용 도구이다. Obsidian은 열람 창이고, 편집기는 명령을 부르는 입력 창이다.

- 사람은 분류하지 않는다. 키 하나로 오늘 노트에 한 줄을 던진다.
- Jira가 진실의 원천이다. 쓰기는 작은 것만 큐를 거쳐 보내고, 자동 상태 전환은 없다.
- LLM 없이도 동작한다. 없으면 위키 편찬 단계만 빠진다.
- 이 브랜치(`nvim`)는 Neovim 플러그인(`nvim/`)을 담는다. Neovim 자체(폰트, LSP, `nvd` 데몬)는 별도 저장소 [nvim-config](https://github.com/5solas/nvim-config)(비공개)가 맡고, work-kb는 환경 변수 `WORK_KB_ROOT`로 그 위에 플러그인 하나로 붙는다.
- `main` 브랜치는 같은 저장소의 Emacs 판이다. Python 쪽은 두 브랜치가 같다.

## 빠른 설치 (새 Windows PC)

1. nvim-config를 설치한다(PC당 한 번): `git clone https://github.com/5solas/nvim-config D:\work\nvim-config` 후 `pwsh -File D:\work\nvim-config\scripts\install.ps1`
2. work-kb를 받는다: `git clone -b nvim https://github.com/5solas/work-kb D:\work\work-kb`
3. 설치한다: `cd D:\work\work-kb` 후 `powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1`
4. 새 터미널을 열고 `nvd --restart`, 이어서 `.venv\Scripts\python.exe scripts\kb.py init`
5. Neovim(`nvd`)에서 `:checkhealth work-kb`, 그다음 Space k c로 한 줄을 캡처한다.

Python 3.13 또는 3.14와 Neovim 0.11 이상이 필요하다. 설치 위치와 전체 절차는 사용자 안내서에 있다.

## 문서

- [docs/USER-GUIDE.md](docs/USER-GUIDE.md): 취지, 목표, 파일·폴더 위치, 설치, 사용법(키 표), 검증 상태
- [docs/README.md](docs/README.md): 명령 표, 폴더 구조, 종료 코드, outbox, LLM 설정
- [docs/DEPLOY.md](docs/DEPLOY.md): 회사 PC로 옮기기
- [nvim-config](https://github.com/5solas/nvim-config): Neovim 쪽 설정과 `nvd` 사용법(`docs/USER_GUIDE.md`)
