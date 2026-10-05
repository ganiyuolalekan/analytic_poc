import random
from decimal import Decimal

import pytest

from nsw_sim import clock, db, logos, money
from nsw_sim.sim import ids, reference


# ------------------------------------------------------------------ money
def test_kobo_roundtrip_and_rounding():
    assert money.to_minor("12.345") == 1235          # half up
    assert money.to_minor(Decimal("0.005")) == 1
    assert money.from_minor(123456) == Decimal("1234.56")
    assert money.round_minor(2.5) == 3 and money.round_minor(-2.5) == -3


@pytest.mark.parametrize("minor,expected", [
    (123_000_000_000, "₦1.23bn"), (45_670_000_000, "₦456.7m"), (1_234_500, "₦12,345"),
    (0, "₦0"), (-123_000_000_000, "-₦1.23bn"), (2_000_000_000_000_00, "₦2.00tn"),
])
def test_ngn_format(minor, expected):
    assert money.fmt_ngn(minor) == expected


def test_exact_format_has_kobo():
    assert money.fmt_ngn(123456789, exact=True) == "₦1,234,567.89"


# ------------------------------------------------------------------ ids
def test_iso6346_known_good_numbers():
    for good in ("CSQU3054383", "MSKU9070323"):
        assert ids.valid_container(good), good
    assert not ids.valid_container("CSQU3054384")


def test_generated_containers_valid_and_unique():
    rnd = random.Random(1)
    seen = set()
    for i in range(2000):
        c = ids.make_container(rnd.choice(["AMLU", "BRNU", "CSLU"]), i)
        assert ids.valid_container(c)
        seen.add(c)
    assert len(seen) > 1900


def test_imo_check_digit():
    assert ids.valid_imo("9074729")
    assert not ids.valid_imo("9074728")
    assert all(ids.valid_imo(ids.make_imo(n)) for n in range(100000, 100500))


def test_payment_refs_unique_12_digits():
    refs = {ids.payment_ref(n) for n in range(1, 50001)}
    assert len(refs) == 50000
    assert all(len(r) == 12 and r.isdigit() for r in refs)


def test_id_factory_formats_and_resume(conn):
    f = ids.IdFactory(conn)
    n1, r1 = f.next_consignment("202608", "NGAPP")
    n2, r2 = f.next_consignment("202608", "NGAPP")
    assert r1 == "NSW-202608-NGAPP-0000001" and r2.endswith("0000002") and n2 == n1 + 1
    je = f.journal_id("NCS", "20260901")
    assert je == "JE-NCS-20260901-000001"
    conn.execute("INSERT INTO journal_entries(entry_id,entity_id,occurred_at) VALUES(?,?,?)", (je, "NCS", "x"))
    f2 = ids.IdFactory(conn)           # resume: continues the sequence
    assert f2.journal_id("NCS", "20260901") == "JE-NCS-20260901-000002"


# ------------------------------------------------------------------ clock
def test_timestamps_are_utc_iso_and_wat_display():
    t = clock.to_dt("2026-10-05T10:42:17Z")
    assert clock.iso(t) == "2026-10-05T10:42:17Z"
    assert clock.fmt_wat(t) == "05 Oct 2026 11:42 WAT"
    assert clock.wat_day("2026-10-05T23:30:00Z") == "2026-10-06"       # 00:30 WAT next day
    start, end = clock.day_bounds("2026-10-05")
    assert start == "2026-10-04T23:00:00Z" and end == "2026-10-05T23:00:00Z"


def test_sim_start_is_july_first_wat(monkeypatch):
    s = clock.sim_start()
    assert clock.wat(s).strftime("%m-%d %H:%M") == "07-01 00:00"


# ------------------------------------------------------------------ logos
def test_all_entities_match_a_logo_in_project_folder():
    m = logos.match_logos()
    assert set(m["matched"]) == set(reference.entity_codes())
    assert not m["missing_entities"]
    assert any("Immigration" in p.name for p in m["unmatched_files"])      # not an entity in the brief


def test_logo_falls_back_to_badge_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(logos, "LOGO_DIR", tmp_path)
    logos.refresh()
    assert logos.match_logos()["matched"] == {}
    uri = logos.logo_data_uri("NCS")
    assert uri.startswith("data:image/svg+xml;base64,")
    assert "NCS" in logos.badge_svg("NCS")
    logos.refresh()


def test_logo_uri_for_real_file_is_png_or_svg():
    logos.refresh()
    assert logos.logo_data_uri("NPA").startswith("data:image/png;base64,")
    assert logos.logo_data_uri("CBN").startswith("data:image/svg+xml;base64,")


# ------------------------------------------------------------------ db & as_of views
def test_schema_and_views_compile(conn):
    r = db.reader("2026-10-05T00:00:00Z")
    for v in db.VIEW_NAMES:
        r.execute(f"SELECT * FROM {v} LIMIT 1").fetchall()


def test_reader_is_read_only_and_as_of_filters(conn):
    conn.execute("INSERT INTO fee_assessments(assessment_id,nsw_ref,entity_id,amount_ngn_minor,occurred_at) "
                 "VALUES('a1','r','NCS',100,'2026-07-01T10:00:00Z'),('a2','r','NCS',200,'2026-07-02T10:00:00Z')")
    r = db.reader("2026-07-01T12:00:00Z")
    assert r.execute("SELECT COUNT(*) FROM v_assessments").fetchone()[0] == 1
    r = db.reader("2026-07-03T00:00:00Z")
    assert r.execute("SELECT SUM(amount_ngn_minor) FROM v_assessments").fetchone()[0] == 300
    with pytest.raises(Exception):
        r.execute("DELETE FROM fee_assessments")


def test_reference_seed_idempotent_and_fictional(conn):
    reference.seed_reference(conn)
    reference.seed_reference(conn)
    assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 12
    assert conn.execute("SELECT COUNT(*) FROM countries").fetchone()[0] == 20
    kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM parties GROUP BY kind"))
    assert kinds["trader"] == 400 and kinds["agent"] == 60 and kinds["shipping_line"] == 12 and kinds["airline"] == 8
    assert kinds["bank"] == 6
    assert db.kv_get(conn, "schema_version") == str(db.SCHEMA_VERSION)
