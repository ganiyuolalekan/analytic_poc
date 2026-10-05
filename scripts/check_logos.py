#!/usr/bin/env python
"""Print which logo files matched which entities, unmatched files, and entities with no logo."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nsw_sim import logos  # noqa: E402
from nsw_sim.sim.reference import entity_cards  # noqa: E402

if __name__ == "__main__":
    dirs = logos.logo_dirs()
    print("logo folders:", [str(d) for d in dirs] or "NONE FOUND")
    m = logos.match_logos()
    print(f"\nMATCHED ({len(m['matched'])}/{len(entity_cards())}):")
    for code, p in m["matched"].items():
        print(f"  {code:7s} <- {p.name}")
    print(f"\nUNMATCHED FILES ({len(m['unmatched_files'])}):")
    for p in m["unmatched_files"]:
        print(f"  {p.name}")
    print(f"\nENTITIES WITH NO LOGO ({len(m['missing_entities'])}) -> generated initials badge:")
    for c in m["missing_entities"]:
        print(f"  {c}")
    sys.exit(0)
