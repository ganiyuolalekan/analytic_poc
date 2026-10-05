"""Reference data: entity cards, countries, parties, banks (loaded from YAML, mirrored to the database)."""
from __future__ import annotations

import random
import sqlite3
from functools import lru_cache

from nsw_sim import clock, logos
from nsw_sim.config import yaml_config
from nsw_sim.sim import ids


@lru_cache(maxsize=1)
def ref() -> dict:
    return yaml_config("reference_data")


@lru_cache(maxsize=1)
def entity_cards() -> dict:
    return yaml_config("entities")["entities"]


def entity_codes() -> list[str]:
    return list(entity_cards().keys())


def fee_entities() -> list[str]:
    """Entities that earn fee revenue from consignments."""
    return [c for c, v in entity_cards().items() if v["type"] in ("collector", "operator", "regulator", "platform")]


def consolidated_entities() -> list[str]:
    ex = set(yaml_config("entities").get("consolidation_excludes", []))
    return [c for c in entity_codes() if c not in ex]


def country_codes() -> list[str]:
    return list(ref()["countries"].keys())


def seed_reference(conn: sqlite3.Connection) -> None:
    """Idempotently insert entities, countries, parties. Parties are deterministic (seeded)."""
    now = clock.iso(clock.utcnow())
    for code, c in entity_cards().items():
        lp = logos.logo_path(code)
        conn.execute("INSERT INTO entities(entity_id,code,name,type,colour,logo_path,created_at,mandate) "
                     "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(entity_id) DO UPDATE SET name=excluded.name, "
                     "type=excluded.type, colour=excluded.colour, logo_path=excluded.logo_path, mandate=excluded.mandate",
                     (code, code, c["name"], c["type"], c["colour"], str(lp) if lp else None, now, c["mandate"]))
    for iso2, c in ref()["countries"].items():
        conn.execute("INSERT OR REPLACE INTO countries(iso2,name,region,currency,risk_tier,ecowas,transit_days) "
                     "VALUES(?,?,?,?,?,?,?)", (iso2, c["name"], c["region"], c["currency"], c["risk_tier"], c["ecowas"],
                                               c["transit_days"]))
    if conn.execute("SELECT COUNT(*) FROM parties").fetchone()[0] == 0:
        conn.executemany("INSERT INTO parties(party_id,kind,name,tin,rc_number,size_tier) VALUES(?,?,?,?,?,?)",
                         generate_parties())


def generate_parties(seed: int = 7) -> list[tuple]:
    """400 traders, 60 customs agents, 12 shipping lines, 8 airlines, 6 banks (all obviously fictional)."""
    rnd = random.Random(seed)
    w = ref()["name_words"]
    rows: list[tuple] = []
    seen: set[str] = set()
    i = 0
    while i < 400:
        name = f"{rnd.choice(w['prefix'])} {rnd.choice(w['middle'])} {rnd.choice(w['sector'])} {rnd.choice(w['suffix'])}"
        if name in seen:
            continue
        seen.add(name)
        i += 1
        tier = rnd.choices(["large", "medium", "small"], weights=[0.12, 0.33, 0.55])[0]
        rows.append((f"TRD{i:04d}", "trader", name, ids.tin(rnd), ids.rc_number(rnd), tier))
    for j in range(60):
        name = f"{rnd.choice(ref()['agent_words'])} {rnd.choice(['Clearing', 'Forwarding', 'Agency', 'Customs Services'])} " \
               f"{rnd.choice(w['suffix'])}"
        rows.append((f"AGT{j + 1:03d}", "agent", f"{name} {j + 1}", ids.tin(rnd), ids.rc_number(rnd), "agent"))
    for s in ref()["shipping_lines"]:
        rows.append((f"SHP-{s['code']}", "shipping_line", s["name"], "", "", "carrier"))
    for a in ref()["airlines"]:
        rows.append((f"AIR-{a['code']}", "airline", a["name"], "", "", "carrier"))
    for b, v in ref()["banks"].items():
        rows.append((f"BNK-{b}", "bank", v["name"], "", "", "bank"))
    return rows
