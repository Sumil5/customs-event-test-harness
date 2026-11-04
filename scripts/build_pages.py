"""Build a small static site from the latest demo run's reports (for GitLab Pages).

python scripts/build_pages.py reports public
"""

from __future__ import annotations

import json
import shutil
import sys
from html import escape
from pathlib import Path


def main(reports: Path, public: Path) -> None:
    run_id = (reports / "LATEST").read_text().strip()
    run_dir = reports / run_id
    public.mkdir(parents=True, exist_ok=True)
    for name in ("reconciliation.html", "sla.html", "reconciliation.json", "sla.json"):
        if (run_dir / name).exists():
            shutil.copy(run_dir / name, public / name)

    recon = json.loads((run_dir / "reconciliation.json").read_text())
    sla = json.loads((run_dir / "sla.json").read_text())
    summary = recon["summary"]
    p95 = sla["end_to_end_ms"]["p95"]
    rows = [
        ("Run", run_id),
        ("Messages", f"{summary['messages_read']:,}"),
        (
            "Reconciliation",
            "PASSED" if recon["passed"] else f"FAILED ({summary['discrepancies']} discrepancies)",
        ),
        ("SLA (13s)", "PASSED" if sla["passed"] else f"FAILED ({len(sla['breaches'])} breaches)"),
        ("p95 end to end", f"{p95:.0f} ms" if p95 is not None else "-"),
    ]
    table = "\n".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in rows)
    (public / "index.html").write_text(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Harness reports</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 16px}}
th{{text-align:left;padding:4px 16px 4px 0;color:#555}}a{{display:block;margin:6px 0}}</style></head>
<body><h1>Latest pipeline run</h1><table>{table}</table>
<a href="reconciliation.html">Reconciliation report</a><a href="sla.html">SLA trace report</a>
</body></html>""",
        encoding="utf-8",
    )
    print(f"Pages built for {run_id} in {public}/")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
