"""NSW-style identifiers (Section 6). SIMULATED formats; container and IMO numbers carry valid check digits."""
from __future__ import annotations

import random
import re
import sqlite3
import threading
import uuid

_ISO6346_VALUES: dict[str, int] = {}
_v = 10
for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    if _v % 11 == 0:
        _v += 1
    _ISO6346_VALUES[_ch] = _v
    _v += 1

_CONTAINER_RE = re.compile(r"^[A-Z]{3}[UJZ]\d{7}$")
_IMO_RE = re.compile(r"^\d{7}$")


def container_check_digit(prefix10: str) -> int:
    """ISO 6346 check digit for the first 10 characters (3 owner letters + category letter + 6 serial digits)."""
    total = 0
    for i, ch in enumerate(prefix10):
        val = _ISO6346_VALUES[ch] if ch.isalpha() else int(ch)
        total += val * (2 ** i)
    return (total % 11) % 10


def make_container(owner4: str, serial6: int) -> str:
    base = f"{owner4}{serial6:06d}"
    return base + str(container_check_digit(base))


def valid_container(number: str) -> bool:
    return bool(_CONTAINER_RE.match(number)) and container_check_digit(number[:10]) == int(number[10])


def imo_check_digit(first6: str) -> int:
    return sum(int(d) * w for d, w in zip(first6, (7, 6, 5, 4, 3, 2))) % 10


def make_imo(first6: int) -> str:
    s = f"{first6:06d}"
    return s + str(imo_check_digit(s))


def valid_imo(number: str) -> bool:
    return bool(_IMO_RE.match(number)) and imo_check_digit(number[:6]) == int(number[6])


def scramble10(n: int) -> int:
    """Bijection on [0, 10^10): 2654435761 is odd and not divisible by 5, so coprime with 10^10."""
    return (n * 2654435761) % 10 ** 10


def payment_ref(n: int) -> str:
    """12-digit unique payment reference (RRR-style): '26' + a bijective 10-digit scramble of ``n``."""
    return f"26{scramble10(n):010d}"


def tin(rnd: random.Random) -> str:
    return f"{rnd.randrange(10 ** 7, 10 ** 8)}-{rnd.randrange(1, 10 ** 4):04d}"


def rc_number(rnd: random.Random) -> str:
    return f"RC{rnd.randrange(10 ** 5, 10 ** 7)}"


def ulid_like() -> str:
    """Time-ordered unique id for live events: evt_ + 26 hex chars."""
    import time
    return "evt_" + f"{int(time.time() * 1000):012x}" + uuid.uuid4().hex[:14]


PERMIT_PATTERNS = {
    "NAFDAC": "NAFDAC/IP/{y}/{n:06d}",
    "SON": "SONCAP/CoC/{y}/{n:07d}",
    "NAQS": "NAQS/PHY/{y}/{n:06d}",
    "NESREA": "NESREA/EP/{y}/{n:05d}",
}


class IdFactory:
    """Deterministic, monotonic id source. Counters are rebuilt from the database on resume."""

    def __init__(self, conn: sqlite3.Connection | None = None) -> None:
        self._lock = threading.Lock()
        self.n = 0                                   # global consignment ordinal
        self.pay_n = 0                               # global payment ordinal
        self.exp_n = 0                               # expense / funding ordinal
        self._nsw_seq: dict[str, int] = {}
        self._je_seq: dict[tuple[str, str], int] = {}
        self._alert_seq: dict[str, int] = {}
        self._conn = conn
        if conn is not None:
            self.load(conn)

    def load(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        row = conn.execute("SELECT COALESCE(MAX(CAST(substr(rotation_no, -6) AS INTEGER)),0) FROM consignments").fetchone()
        n = int(row[0])
        row = conn.execute("SELECT value FROM sim_state WHERE key='seq_consignment'").fetchone()
        self.n = max(n, int(row[0]) if row else 0)
        row = conn.execute("SELECT value FROM sim_state WHERE key='seq_payment'").fetchone()
        self.pay_n = int(row[0]) if row else 0
        row = conn.execute("SELECT value FROM sim_state WHERE key='seq_expense'").fetchone()
        self.exp_n = int(row[0]) if row else 0
        self._nsw_seq = {k: int(v) for k, v in conn.execute(
            "SELECT substr(nsw_ref,1,length(nsw_ref)-8), MAX(CAST(substr(nsw_ref,-7) AS INTEGER)) FROM consignments GROUP BY 1")}

    def persist(self, conn: sqlite3.Connection) -> None:
        conn.execute("INSERT OR REPLACE INTO sim_state(key,value) VALUES('seq_consignment',?)", (str(self.n),))
        conn.execute("INSERT OR REPLACE INTO sim_state(key,value) VALUES('seq_payment',?)", (str(self.pay_n),))
        conn.execute("INSERT OR REPLACE INTO sim_state(key,value) VALUES('seq_expense',?)", (str(self.exp_n),))

    def next_consignment(self, yyyymm: str, port: str) -> tuple[int, str]:
        with self._lock:
            self.n += 1
            key = f"NSW-{yyyymm}-{port}"
            seq = self._nsw_seq.get(key, 0) + 1
            self._nsw_seq[key] = seq
            return self.n, f"{key}-{seq:07d}"

    def n_expense(self) -> int:
        with self._lock:
            self.exp_n += 1
            return self.exp_n

    def next_payment(self) -> tuple[int, str]:
        with self._lock:
            self.pay_n += 1
            return self.pay_n, payment_ref(self.pay_n)

    def journal_id(self, entity: str, yyyymmdd: str) -> str:
        key = (entity, yyyymmdd)
        with self._lock:
            if key not in self._je_seq:
                seq = 0
                if self._conn is not None:
                    pre = f"JE-{entity}-{yyyymmdd}-"
                    row = self._conn.execute("SELECT MAX(entry_id) FROM journal_entries WHERE entry_id>=? AND entry_id<?",
                                             (pre, pre + "~")).fetchone()
                    if row and row[0]:
                        seq = int(row[0][-6:])
                self._je_seq[key] = seq
            self._je_seq[key] += 1
            return f"JE-{entity}-{yyyymmdd}-{self._je_seq[key]:06d}"

    def alert_id(self, yyyymmdd: str) -> str:
        with self._lock:
            if yyyymmdd not in self._alert_seq:
                seq = 0
                if self._conn is not None:
                    pre = f"ALT-{yyyymmdd}-"
                    row = self._conn.execute("SELECT MAX(alert_id) FROM alerts WHERE alert_id>=? AND alert_id<?",
                                             (pre, pre + "~")).fetchone()
                    if row and row[0]:
                        seq = int(row[0][-5:])
                self._alert_seq[yyyymmdd] = seq
            self._alert_seq[yyyymmdd] += 1
            return f"ALT-{yyyymmdd}-{self._alert_seq[yyyymmdd]:05d}"


def batch_id(yyyymmdd: str, bank: str, seq: int = 1) -> str:
    return f"STL-{yyyymmdd}-{bank}-{seq:03d}"


def remittance_id(entity: str, yyyymm: str, seq: int = 1) -> str:
    return f"REM-{entity}-{yyyymm}-{seq:03d}"
