"""테스트 하위 프로세스용 네트워크 차단. 루프백이 아닌 소켓 접속을 막는다.

conftest.run_kb는 하위 프로세스의 PYTHONPATH에 tests/를 넣는다. 그래서 하위 Python이 시작할 때
site 모듈이 이 파일을 sitecustomize로 읽어 들여 `socket.socket.connect`(·`connect_ex`)를 감싼다.
KB_ALLOW_NETWORK=1이면 아무것도 하지 않는다(live 테스트가 하위 프로세스를 쓸 때만 켠다).

conftest는 같은 감싸개(`guard`)를 파일 경로로 읽어 테스트 프로세스 안에서도 쓴다.
그때는 모듈 이름이 sitecustomize가 아니므로 여기서 바로 설치하지 않는다.
"""
from __future__ import annotations

import os
import socket

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def blocked(address: object) -> bool:
    host = address[0] if isinstance(address, tuple) and address else None
    return host is not None and host not in LOOPBACK


def guard(real):
    def guarded(self, address):
        if blocked(address):
            raise RuntimeError(f"테스트에서 외부 접속 차단: {address!r}")
        return real(self, address)

    guarded.kb_network_guard = True
    return guarded


def install() -> None:
    socket.socket.connect = guard(socket.socket.connect)
    socket.socket.connect_ex = guard(socket.socket.connect_ex)


if __name__ == "sitecustomize" and os.environ.get("KB_ALLOW_NETWORK") != "1":
    install()
