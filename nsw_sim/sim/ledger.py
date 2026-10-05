"""Chart of accounts and the double-entry journal buffer. Every entry is balanced in code (Section 9.2)."""
from __future__ import annotations

import sqlite3

from nsw_sim import clock
from nsw_sim.llm.schemas import EntityProfile
from nsw_sim.sim.ids import IdFactory

# code -> (name, class, statement_line). Revenue accounts are added per entity from its fee rules.
STANDARD = {
    "1100": ("Bank", "Asset", "Cash and bank"),
    "1105": ("Funds in transit (NSW collection)", "Asset", "Funds in transit"),
    "1200": ("Fees receivable", "Asset", "Fees receivable"),
    "1300": ("Prepaid expenses", "Asset", "Prepayments"),
    "1500": ("Fixed assets (net)", "Asset", "Fixed assets (net)"),
    "2100": ("Payables", "Liability", "Payables"),
    "2150": ("Unapplied receipts (refundable)", "Liability", "Unapplied receipts"),
    "2300": ("Remittance payable (to Treasury)", "Liability", "Remittance payable"),
    "2400": ("Deferred grants", "Liability", "Deferred grants"),
    "2500": ("Concessional loans payable", "Liability", "Concessional loans"),
    "3100": ("Accumulated surplus", "Equity", "Accumulated surplus"),
    "3200": ("Distributions to Treasury", "Equity", "Distributions to Treasury"),
    "4800": ("Government appropriation", "Revenue", "Government appropriation"),
    "4850": ("Partner grants", "Revenue", "Partner grants"),
    "4950": ("FX gains", "Revenue", "FX gains"),
    "5100": ("Personnel", "Expense", "Personnel"),
    "5200": ("Operations", "Expense", "Operations"),
    "5300": ("ICT", "Expense", "ICT"),
    "5400": ("Maintenance", "Expense", "Maintenance"),
    "5500": ("Collection costs", "Expense", "Collection costs"),
    "5600": ("Depreciation", "Expense", "Depreciation"),
    "5950": ("FX losses", "Expense", "FX losses"),
}
CATEGORY_ACCOUNT = {"Personnel": "5100", "Operations": "5200", "ICT": "5300", "Maintenance": "5400", "Depreciation": "5600"}


def coa_rows(entity: str, profile: EntityProfile | None, etype: str) -> list[tuple]:
    rows = []
    if etype == "settlement":
        return [(entity, "1100", "Settlement account (CBN)", "Asset", "Settlement account"),
                (entity, "2100", "Payable to agencies", "Liability", "Payable to agencies")]
    if etype == "treasury":
        return [(entity, "1100", "Bank (Federation Account)", "Asset", "Cash and bank"),
                (entity, "4900", "Remittances received", "Revenue", "Remittances received")]
    for code, (name, cls, line) in STANDARD.items():
        rows.append((entity, code, name, cls, line))
    if profile:
        for r in profile.fee_rules:
            rows.append((entity, r.revenue_account, r.name.replace(" (simulated)", ""), "Revenue", r.name.replace(" (simulated)", "")))
    return rows


def build_coa(conn: sqlite3.Connection, profiles: dict[str, EntityProfile], types: dict[str, str]) -> None:
    for ent, etype in types.items():
        conn.executemany("INSERT OR REPLACE INTO chart_of_accounts(entity_id,account_code,name,class,statement_line) VALUES(?,?,?,?,?)",
                         coa_rows(ent, profiles.get(ent), etype))


def revenue_account_for(profile: EntityProfile, fee_code: str) -> str | None:
    for r in profile.fee_rules:
        if r.fee_code == fee_code:
            return r.revenue_account
    return None


class Ledger:
    """Buffers balanced journal entries; ``flush`` writes them. Lines: (account, debit, credit, ccy, origin, process)."""

    def __init__(self, ids: IdFactory) -> None:
        self.ids = ids
        self.entries: list[tuple] = []
        self.lines: list[tuple] = []

    def post(self, entity: str, t: float | str, ref_type: str, ref_id: str, nsw_ref: str | None, memo: str,
             lines: list[tuple]) -> str:
        debit = sum(x[1] for x in lines)
        credit = sum(x[2] for x in lines)
        if debit != credit:
            raise AssertionError(f"unbalanced entry {entity} {ref_type} {ref_id}: Dr {debit} != Cr {credit}")
        if debit == 0:
            return ""
        ts = clock.iso(t)
        eid = self.ids.journal_id(entity, clock.wat_day(ts).replace("-", ""))
        self.entries.append((eid, entity, ts, ref_type, ref_id, nsw_ref, memo))
        for acct, dr, cr, ccy, origin, proc in lines:
            if dr or cr:
                self.lines.append((eid, entity, acct, int(dr), int(cr), ccy, origin, proc, ts))
        return eid

    def take(self) -> tuple[list[tuple], list[tuple]]:
        e, l_ = self.entries, self.lines
        self.entries, self.lines = [], []
        return e, l_

    @staticmethod
    def flush(conn: sqlite3.Connection, entries: list[tuple], lines: list[tuple]) -> None:
        conn.executemany("INSERT INTO journal_entries(entry_id,entity_id,occurred_at,ref_type,ref_id,nsw_ref,memo) VALUES(?,?,?,?,?,?,?)", entries)
        conn.executemany("INSERT INTO journal_lines(entry_id,entity_id,account_code,debit_minor,credit_minor,currency,origin_country,"
                         "process_code,occurred_at) VALUES(?,?,?,?,?,?,?,?,?)", lines)
