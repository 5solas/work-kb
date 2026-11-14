"""A-24 재현: `llm_detect.detect_reasoning`의 기준선 요청이 일시적 500 오류 시 1회 재시도를 생략하고 즉시 실패한다."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

import fake_keyring  # noqa: E402
import mock_llm  # noqa: E402
from lib import config, llm_client, llm_detect  # noqa: E402

with tempfile.TemporaryDirectory(prefix="repro_a24_", ignore_cleanup_errors=True) as tmp:
    tmp_path = Path(tmp)
    vault = tmp_path / "vault"
    shutil.copytree(ROOT / "examples" / "sample-vault", vault)
    keyring_file = tmp_path / "keyring.json"
    keyring_file.write_text(json.dumps({"work-kb\nllm_main": "mock-tok"}), encoding="utf-8")

    with mock_llm.start(port=0, require_key="mock-tok", http_500_once=True) as ml:
        (vault / "config.local.yaml").write_text(
            f"environment: home\njira:\n  project_keys: [KB]\nllm:\n  enabled: true\n"
            f"  profiles:\n    main:\n      base_url: {ml.base_url}/v1\n"
            f"      model: mock-model\n      api_key_ref: keyring:work-kb/llm_main\n",
            encoding="utf-8",
        )
        old_backend = os.environ.get("PYTHON_KEYRING_BACKEND")
        old_file = os.environ.get("KB_FAKE_KEYRING_FILE")
        os.environ["KB_FAKE_KEYRING_FILE"] = str(keyring_file)
        import keyring
        keyring.set_keyring(fake_keyring.FileKeyring(keyring_file))
        try:
            cfg = config.load(vault)
            llm_detect._sleep = lambda _s: None
            client = llm_client.LlmClient(cfg)
            try:
                res = llm_detect.detect_reasoning(client, "main", llm_detect.Budget())
                print("Unexpectedly succeeded:", res)
            except llm_detect.DetectError as exc:
                print(f"chat calls={ml.counts['chat']} error={exc}")
                assert ml.counts["chat"] >= 2, (
                    f"Expected baseline to retry once after http_500_once, saw chat calls={ml.counts['chat']}"
                )
        finally:
            if old_backend is None:
                os.environ.pop("PYTHON_KEYRING_BACKEND", None)
            else:
                os.environ["PYTHON_KEYRING_BACKEND"] = old_backend
            if old_file is None:
                os.environ.pop("KB_FAKE_KEYRING_FILE", None)
            else:
                os.environ["KB_FAKE_KEYRING_FILE"] = old_file
