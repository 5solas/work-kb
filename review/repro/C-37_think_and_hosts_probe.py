"""Unit-level probes (no network). Run with repo scripts/ on sys.path."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from lib import config, llm_client  # noqa: E402

BL = config.CODE_BLOCKLIST
print("=== host check ===")
urls = [
    "https://api.deepinfra.com/v1/openai",
    "https://API.DEEPINFRA.COM/v1",
    "https://api.deepinfra.com./v1",
    "https://api.deepinfra.com:443/v1",
    "https://x@api.deepinfra.com/v1",
    "https://api.deepinfra.com%2F@evil/v1",
    "https://api.deepinfra%2ecom/v1",
    "https://api.deepinfra.com\\@internal.corp/v1",
    "api.deepinfra.com/v1",
    "//api.deepinfra.com/v1",
    "https://[::1]/v1",
    "https://104.18.0.1/v1",
    "https://2130706433/v1",
    "https://api。deepinfra。com/v1",
    "https://xn--deepinfr-7fg.com/v1",
    "https://deepinfra.com.internal.corp/v1",
    "https://notdeepinfra.com/v1",
    "http://llm.corp.example/v1",
    "HTTPS://generativelanguage.GOOGLEAPIS.com/v1beta/openai",
]
for u in urls:
    print(f"{config.is_public_host(u, BL)!s:5}  checked={config.checked_host(u)!r:28}  {u}")

print("=== env override probes ===")
for env in ({"KB_LLM_PROFILES": "{main: {base_url: https://api.deepinfra.com}}"},
            {"KB_ENVIRONMENT": "home"},
            {"kb_llm_public_hosts_blocklist": "[]"}):
    try:
        print(env, "->", config.parse_env_overrides(env))
    except Exception as exc:
        print(env, "-> raises", type(exc).__name__, exc)

print("=== normalize_message ===")
cases = {
    "plain": {"content": "<think>a</think>{\"x\":1}"},
    "upper": {"content": "<THINK>reasoning {here}</THINK>{\"x\":1}"},
    "thinking_tag": {"content": "<thinking>reasoning {here}</thinking>{\"x\":1}"},
    "orphan_close": {"content": "reasoning</think>{\"x\":1}"},
    "unclosed_leading_ws": {"content": "  \n<think>still thinking"},
    "literal_in_json": {"content": "{\"decisions\":[{\"title\":\"t\",\"content\":\"use </think> to end\",\"source_line\":\"L1\"}],\"actions\":[]}"},
    "think_after_answer": {"content": "{\"x\":1}\n<think>trailing"},
}
for name, msg in cases.items():
    content, reasoning = llm_client.normalize_message(msg)
    try:
        parsed = llm_client.parse_json_object(content)
        verdict = f"parsed={parsed}"
    except Exception as exc:
        verdict = f"PARSE FAIL: {exc}"
    print(f"{name:20} content={content[:60]!r:64} -> {verdict}")

bad = []
for name in ("literal_in_json", "upper", "thinking_tag"):
    content, _ = llm_client.normalize_message(cases[name])
    try:
        llm_client.parse_json_object(content)
    except Exception:
        bad.append(name)
if not config.is_public_host("https://104.18.0.1/v1", BL):
    print("NOTE (C-43): public IP address host is not blocked in company mode")
if bad:
    print("FAIL (C-37/C-38): JSON not recovered for", bad)
    sys.exit(1)
print("OK")
