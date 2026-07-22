"""Unit tests for the SQLite-backed query_sql tool."""

import pytest

from dashboard_agent.database import build_connection, run_query


@pytest.fixture()
def conn():
    return build_connection()


def test_sector_funding_query(conn):
    r = run_query("SELECT sector, funding_usd FROM egypt_sector_funding ORDER BY funding_usd DESC", conn)
    assert r["row_count"] == 5
    assert r["columns"] == ["sector", "funding_usd"]
    assert r["rows"][0]["sector"] == "Food & Cash"  # largest share


def test_quarterly_delta_query(conn):
    r = run_query("SELECT quarter, people_reached FROM egypt_quarterly ORDER BY quarter", conn)
    quarters = {row["quarter"]: row["people_reached"] for row in r["rows"]}
    assert quarters["Q1 2026"] == 1_900_000
    assert quarters["Q2 2026"] == 2_400_000


def test_aggregate_query(conn):
    r = run_query("SELECT SUM(funding_usd) AS total FROM egypt_sector_funding", conn)
    assert r["rows"][0]["total"] == 54_000_000 + 28_000_000 + 19_000_000 + 14_000_000 + 11_000_000


def test_iran_and_canada_tables(conn):
    assert run_query("SELECT * FROM iran_resources", conn)["row_count"] == 5
    assert run_query("SELECT * FROM canada_water_advisories ORDER BY year", conn)["rows"][0]["advisories"] == 51


def test_with_cte_allowed(conn):
    r = run_query(
        "WITH top AS (SELECT * FROM egypt_sector_funding ORDER BY funding_usd DESC LIMIT 2) SELECT COUNT(*) AS n FROM top",
        conn,
    )
    assert r["rows"][0]["n"] == 2


@pytest.mark.parametrize(
    "bad",
    [
        "INSERT INTO egypt_quarterly VALUES (1,2,3,4,5)",
        "UPDATE egypt_quarterly SET funded_pct=100",
        "DELETE FROM iran_resources",
        "DROP TABLE canada_wash_access",
        "SELECT 1; DROP TABLE iran_resources",
    ],
)
def test_non_select_rejected(conn, bad):
    r = run_query(bad, conn)
    assert "error" in r


def test_bad_sql_returns_error(conn):
    r = run_query("SELECT * FROM nonexistent_table", conn)
    assert "error" in r and "SQL error" in r["error"]


def test_empty_query_rejected(conn):
    assert "error" in run_query("   ", conn)
