#!/usr/bin/env python
"""Print the model-usage cost ledger (tokens by role / model / day). ``make cost``."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nsw_sim import db  # noqa: E402
from nsw_sim.config import db_path  # noqa: E402
from nsw_sim.llm import ledger  # noqa: E402

if __name__ == "__main__":
    if not db_path().exists():
        print("No database yet (run `make seed`); nothing logged.")
        sys.exit(0)
    conn = db.connect()
    db.init_db(conn, indexes=False)
    print(ledger.format_summary(ledger.summary(conn)))
    df = ledger.ledger(conn)
    if not df.empty:
        print("\nBy day / role / model:")
        print(df.drop(columns=["validation_issues"]).to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
