from pathlib import Path

import pytest

import migrate
from migrate import Migration, MigrationError, discover, migrate as run, pending, snapshot


def mig(version, name, sql="SELECT 1;"):
    import hashlib
    return Migration(version, f"{version:04d}_{name}.sql", sql, hashlib.sha256(sql.encode()).hexdigest())


class FakeConnection:
    """Records statements. `applied` plays the schema_migrations table."""

    def __init__(self, applied=None, fail_on=None):
        self.applied = dict(applied or {})
        self.log, self.fail_on, self.in_tx = [], fail_on, False
        self.committed, self.rolled_back = [], []

    async def execute(self, sql, *args):
        self.log.append((sql.strip().split("\n")[0][:60], args))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("boom")
        if sql.strip().startswith("INSERT INTO schema_migrations"):
            self.applied[args[0]] = args[2]

    async def fetch(self, sql, *args):
        return [{"version": v, "checksum": c} for v, c in sorted(self.applied.items())]

    def transaction(self):
        conn = self

        class Tx:
            async def __aenter__(self):
                conn.snapshot = dict(conn.applied)

            async def __aexit__(self, exc_type, *_):
                if exc_type:
                    conn.applied = conn.snapshot  # rolled back
                    conn.rolled_back.append(True)
                return False
        return Tx()


def statements(conn):
    return [s for s, _ in conn.log]


@pytest.mark.asyncio
async def test_applies_pending_in_order_and_records_each():
    conn = FakeConnection()
    done = await run(conn, [mig(1, "a", "CREATE a;"), mig(2, "b", "CREATE b;")])
    assert done == ["0001_a.sql", "0002_b.sql"]
    assert set(conn.applied) == {1, 2}
    stmts = statements(conn)
    assert stmts.index("CREATE a;") < stmts.index("CREATE b;")
    assert stmts[0].startswith("SELECT pg_advisory_lock") and stmts[-1].startswith("SELECT pg_advisory_unlock")


@pytest.mark.asyncio
async def test_second_run_applies_nothing():
    conn = FakeConnection()
    migrations = [mig(1, "a"), mig(2, "b")]
    await run(conn, migrations)
    before = len(conn.log)
    assert await run(conn, migrations) == []
    assert not any(s.startswith("SELECT 1;") for s in statements(conn)[before:])


@pytest.mark.asyncio
async def test_only_the_new_migration_runs_against_an_existing_database():
    first = mig(1, "a", "CREATE a;")
    conn = FakeConnection(applied={1: first.checksum})
    assert await run(conn, [first, mig(2, "b", "CREATE b;")]) == ["0002_b.sql"]
    assert "CREATE a;" not in statements(conn)


@pytest.mark.asyncio
async def test_failure_rolls_back_that_migration_stops_and_still_unlocks():
    conn = FakeConnection(fail_on="CREATE bad")
    with pytest.raises(RuntimeError):
        await run(conn, [mig(1, "a", "CREATE a;"), mig(2, "b", "CREATE bad;"), mig(3, "c", "CREATE c;")])
    assert set(conn.applied) == {1}  # 2 rolled back, 3 never tried
    assert "CREATE c;" not in statements(conn)
    assert statements(conn)[-1].startswith("SELECT pg_advisory_unlock")


def test_editing_an_applied_migration_is_refused():
    with pytest.raises(MigrationError, match="edited"):
        pending([mig(1, "a", "CREATE a2;")], {1: mig(1, "a", "CREATE a;").checksum})


def test_database_ahead_of_checkout_is_refused():
    with pytest.raises(MigrationError, match="does not"):
        pending([mig(1, "a")], {1: mig(1, "a").checksum, 2: "x"})


def test_discover_checks_names_and_numbering(tmp_path):
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0002_b.sql").write_text("SELECT 2;")
    assert [m.version for m in discover(tmp_path)] == [1, 2]
    (tmp_path / "0004_gap.sql").write_text("x")
    with pytest.raises(MigrationError, match="without gaps"):
        discover(tmp_path)
    (tmp_path / "0004_gap.sql").unlink()
    (tmp_path / "03_short.sql").write_text("x")
    with pytest.raises(MigrationError, match="named NNNN"):
        discover(tmp_path)
    (tmp_path / "03_short.sql").unlink()
    (tmp_path / "0002_dupe.sql").write_text("x")
    with pytest.raises(MigrationError, match="share a version"):
        discover(tmp_path)


def test_the_repos_migrations_are_valid_and_schema_sql_is_their_snapshot():
    migrations = discover()
    assert len(migrations) >= 3 and migrations[0].name == "0001_baseline.sql"
    assert migrate.SCHEMA_SNAPSHOT.read_text(encoding="utf-8") == snapshot(migrations), "run: python scripts/migrate.py snapshot"
    assert migrate.main(["snapshot", "--check"]) == 0


def test_baseline_and_provenance_migrations_are_idempotent_by_construction():
    for name in ("0001_baseline.sql", "0002_jamming_zone_provenance.sql"):
        text = (migrate.MIGRATIONS_DIR / name).read_text().upper()
        for statement in ("CREATE TABLE ", "CREATE INDEX ", "CREATE UNIQUE INDEX ", "ADD COLUMN ", "CREATE EXTENSION "):
            assert statement not in text.replace(statement.strip() + " IF NOT EXISTS", ""), f"{name}: {statement} without IF NOT EXISTS"


def test_dedupe_migration_deletes_before_it_creates_the_unique_index_and_includes_the_time_column():
    text = (migrate.MIGRATIONS_DIR / "0003_position_dedupe.sql").read_text()
    assert text.index("DELETE FROM vessel_position") < text.index("CREATE UNIQUE INDEX")
    assert "(mmsi, received_at, latitude, longitude)" in text


def test_snapshot_check_fails_when_out_of_date(tmp_path, monkeypatch, capsys):
    stale = tmp_path / "schema.sql"
    stale.write_text("-- old")
    monkeypatch.setattr(migrate, "SCHEMA_SNAPSHOT", stale)
    assert migrate.main(["snapshot", "--check"]) == 1
    assert "out of date" in capsys.readouterr().err
    assert migrate.main(["snapshot"]) == 0 and migrate.main(["snapshot", "--check"]) == 0


def test_up_without_a_dsn_is_a_usage_error(monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    with pytest.raises(SystemExit):
        migrate.main(["up"])
