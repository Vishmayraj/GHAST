import pytest

import storage


class Conn:
    def __init__(self):
        self.calls = []

    async def executemany(self, sql, params):
        self.calls.append((sql, params))


class Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_):
                return False
        return Ctx()


def test_position_insert_skips_duplicates_instead_of_failing():
    assert "ON CONFLICT DO NOTHING" in storage._INSERT_POSITION_SQL


@pytest.mark.asyncio
async def test_write_positions_uses_the_dedupe_safe_insert():
    conn = Conn()
    writer = storage.TimescaleWriter("dsn")
    writer._pool = Pool(conn)
    record = {"received_at": 1, "mmsi": 2, "ship_name": None, "message_type": "PositionReport", "latitude": 1.0, "longitude": 2.0}
    await writer.write_positions([record, record])
    sql, params = conn.calls[0]
    assert sql is storage._INSERT_POSITION_SQL and len(params) == 2


@pytest.mark.asyncio
async def test_connect_runs_the_migrator_not_a_schema_file(monkeypatch):
    ran = []

    async def fake_migrations(dsn):
        ran.append(dsn)
        return ["0003_x.sql"]

    async def fake_pool(dsn, **_):
        return object()

    monkeypatch.setattr(storage, "run_migrations", fake_migrations)
    monkeypatch.setattr(storage.asyncpg, "create_pool", fake_pool)
    await storage.TimescaleWriter("postgresql://x").connect()
    assert ran == ["postgresql://x"]
