# 포장: wheels 묶음과 이식 복사본

회사 PC에서 `pip`가 인터넷에 닿지 않을 때 쓰는 오프라인 설치 묶음(`wheels/`)과 이식 복사본 도구다.
이식 절차 전체는 `docs/DEPLOY.md`, 종료 코드는 `docs/README.md`의 종료 코드 표를 본다.

## 1. `kb.py wheels` — 오프라인 설치 묶음 (집에서)

```
.venv\Scripts\python.exe scripts\kb.py wheels             # 3.14, wheels\
.venv\Scripts\python.exe scripts\kb.py wheels --dry-run   # pip 명령만 보기
```

| 옵션 | 뜻 |
|---|---|
| `--targets 3.14` | 대상 Python. 3.14만 받는다(기본) |
| `--dir wheels` | 받을 폴더. 상대 경로는 저장소 기준 |
| `--no-sdist` | 3.15용 PyYAML 원본을 받지 않는다 |
| `--dry-run` | 명령만 출력한다(`pip` 없어도 됨) |

지금 venv의 pip로 cp314 wheel(`--only-binary=:all: --platform win_amd64 --abi cp314`)과 PyYAML 원본을 받는다.
네트워크가 필요하다.

```
wheels\
├── *.whl                 cp314·순수 Python wheel
├── sdist\pyyaml-X.tar.gz 3.15용 PyYAML 원본
└── MANIFEST.txt          wheel 목록, sha256·크기
```

`[cp314]` 구역에 `requirements.txt`의 다섯 패키지가 다 없으면 `누락:` 줄과 함께 실패한다.
`wheels/`는 커밋하지 않지만 이식 복사본에는 들어간다(§3).

## 2. 회사에서 설치

Python 3.14만 지원한다(D-NV1). 3.14 미만이거나 패키지가 없으면 `kb.py` 사전 점검이 설치 명령을 알리고 멈춘다.

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
REM 인터넷이 안 되면:
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels -r requirements.txt
```

Neovim 연동까지 하려면 `install.ps1 -Offline`을 쓴다. `wheels\`에서만 설치하고 `uv`도 아무것도 받지 않는다.
설치·제거 세부는 `docs/USER-GUIDE.md` 5.2·5.4·5.6절.

### Python 3.15

PyPI에 PyYAML cp315 wheel이 없어 3.15 venv는 사전 점검에서 멈춘다. `kb.py wheels --targets 3.15`도 거부된다.

집에서 해 본 결과(2026-11-05, PyYAML 6.0.3, Python 3.15.2):

- `python -m pip wheel wheels\sdist\pyyaml-6.0.3.tar.gz --no-deps -w <폴더>`로 cp315 wheel이 나온다.
  C 컴파일러가 없어 순수 Python(libyaml 없음)이고, 빌드 도구를 받을 네트워크가 필요하다.
- pillow·charset_normalizer는 cp315 wheel이 있다.
- 3.15 묶음이 꼭 필요하면 이 wheel을 `wheels\`에 넣고 나머지는 `pip download ... --python-version 3.15 --abi cp315`로 받는다.

## 3. `scripts/deploy_check.py` — 이식 복사본 만들기 (집에서)

```
.venv\Scripts\python.exe scripts\deploy_check.py "D:\옮길 곳\work-kb"
```

대상은 저장소 밖의 없거나 빈 폴더여야 한다. 제외 목록(지시서 §9.1 + D60):

| 범위 | 제외 |
|---|---|
| 어디서든 | `.git`, `.venv*`, `__pycache__`, `.pytest_cache`, `.obsidian`, 임시·백업 파일(`.*.tmp`, `*~`, `#*#`, `.#*`) |
| 최상위 | `config.local.yaml`, `.env`, `.state/`, `_trash/`, `examples/`, `tests/` |
| 볼트 내용 | `daily/`·`inbox/`·`raw/` 안의 파일 전부, `jira/*.md`, `jira/_views/*.md`, `jira/outbox.jsonl`, `jira/.cache.sqlite*`, `wiki/` 아래 `*.md` |

- `.gitkeep`, Bases 파일(`jira/_views/일감.base`, `wiki/엔티티.base`), `wheels/`는 복사한다.
- 복사본에는 `tests/`가 없어 `kb.py demo`가 안 된다.
- 끝나면 제외 대상이 없는지와 필수 파일이 있는지 다시 확인한다. 문제는 `✗` 줄로 보인다.

## 4. 테스트

시험용 `.venv`는 `uv venv --seed --python 3.14 .venv`로 만들고 `requirements-dev.txt`를 설치한다.

```
.venv\Scripts\python.exe -m pytest tests\test_packaging.py              # 네트워크 없음
.venv\Scripts\python.exe -m pytest tests\test_packaging.py -m packaging # 실제 내려받기 (집에서만)
.venv\Scripts\python.exe -m pytest tests\test_install_script.py         # install·uninstall.ps1
```

`-m packaging`은 묶음을 받아 한글·공백 경로의 새 3.14 venv에 오프라인 설치하고 `kb.py --help`를 확인한다.
