"""테스트용 keyring 백엔드. JSON 파일 하나에 저장하며 실제 자격 증명 저장소를 건드리지 않는다.

하위 프로세스에서는 `PYTHON_KEYRING_BACKEND=fake_keyring.FileKeyring`과
`KB_FAKE_KEYRING_FILE=<경로>`로 고른다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import keyring.backend
from keyring import errors


class FileKeyring(keyring.backend.KeyringBackend):
    priority = 100  # 프로세스 안에서 import되면 실제 백엔드보다 먼저 선택된다(안전망)

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        super().__init__()
        self.path = Path(path or os.environ.get("KB_FAKE_KEYRING_FILE") or "fake-keyring.json")

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    @staticmethod
    def _key(service: str, username: str | None) -> str:
        return f"{service}\n{username or ''}"

    def get_password(self, service: str, username: str | None) -> str | None:
        return self._load().get(self._key(service, username))

    def set_password(self, service: str, username: str | None, password: str) -> None:
        data = self._load()
        data[self._key(service, username)] = password
        self._save(data)

    def delete_password(self, service: str, username: str | None) -> None:
        data = self._load()
        key = self._key(service, username)
        if key not in data:
            raise errors.PasswordDeleteError(f"{service}/{username} 없음")
        del data[key]
        self._save(data)
