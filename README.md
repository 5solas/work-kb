# work-kb (main 브랜치, Emacs 판)

업무 메모, 그 메모에서 편찬한 위키, Jira 일감 캐시를 평범한 마크다운 폴더 하나에 모으고 Python CLI 하나(`scripts/kb.py`)로 다루는 Windows용 도구이다. Obsidian은 열람 창이고, 편집기는 명령을 부르는 입력 창이다.

- 사람은 분류하지 않는다. 키 하나로 오늘 노트에 한 줄을 던진다.
- Jira가 진실의 원천이다. 쓰기는 작은 것만 큐를 거쳐 보내고, 자동 상태 전환은 없다.
- LLM 없이도 동작한다. 없으면 위키 편찬 단계만 빠진다.

## 브랜치

| 브랜치 | 편집기 | 안내 |
|---|---|---|
| `main` (이 브랜치) | Emacs (`emacs/work-kb.el`) | [docs/README.md](docs/README.md) |
| `nvim` | Neovim 플러그인 (`nvim/`). 일반 Neovim 설정은 별도 저장소 [nvim-config](https://github.com/5solas/nvim-config)가 맡는다 | `nvim` 브랜치의 `docs/USER-GUIDE.md` |

Python 쪽(`scripts/`), 볼트 구조, 설정 파일, Jira·Confluence·LLM 동작은 두 브랜치가 같다.

## 문서

- [docs/README.md](docs/README.md): 무엇을 하나, 폴더 구조, 빠른 시작, 명령 표, Emacs 키
- [docs/DEPLOY.md](docs/DEPLOY.md): 회사 PC로 옮기기
- [docs/SCHEDULING.md](docs/SCHEDULING.md): 작업 스케줄러
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): 문제 해결
- [docs/DECISIONS.md](docs/DECISIONS.md): 설계 결정과 사용자 확인 대기 목록

## 빠른 시작

```powershell
git clone https://github.com/5solas/work-kb D:\work\work-kb
cd D:\work\work-kb
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\kb.py init
.venv\Scripts\python.exe scripts\kb.py doctor
```

Python 3.13 또는 3.14가 필요하다. Emacs 설정과 키는 `docs/README.md`에 있다. Neovim을 쓰려면 `git clone -b nvim`으로 `nvim` 브랜치를 받는다.
