"""In-memory SQLite seeded from the corpus — powers the read-only `query_sql` tool.

This gives the agent a "data crunching" surface: it can write real SQL against
structured tables and turn the rows into dashboard widgets. The DB is built
in memory from the same numbers as the RAG corpus, so SQL and datasearch agree.
"""

from __future__ import annotations

import re
import sqlite3
from functools import lru_cache
from typing import Any

from .corpus import CORPUS


def _doc(doc_id: str) -> dict[str, Any]:
    return next(d for d in CORPUS if d["id"] == doc_id)["data"]


SCHEMA_DDL = """
CREATE TABLE egypt_quarterly (
    quarter TEXT, people_reached INTEGER, appeal_usd INTEGER,
    funded_usd INTEGER, funded_pct INTEGER
);
CREATE TABLE egypt_sector_funding (sector TEXT, funding_usd INTEGER);
CREATE TABLE egypt_monthly_reach (month TEXT, people_reached INTEGER);
CREATE TABLE iran_resources (resource TEXT, count INTEGER);
CREATE TABLE iran_displaced_by_province (province TEXT, displaced_people INTEGER);
CREATE TABLE canada_wash_access (region TEXT, access_pct INTEGER);
CREATE TABLE canada_water_advisories (year TEXT, advisories INTEGER);
"""

# Human-readable schema handed to the agent so it can write correct SQL.
SCHEMA_DESCRIPTION = """Available tables (SQLite):
- egypt_quarterly(quarter, people_reached, appeal_usd, funded_usd, funded_pct)   -- Q1 & Q2 2026
- egypt_sector_funding(sector, funding_usd)                                       -- Q2 2026 by sector
- egypt_monthly_reach(month, people_reached)                                      -- Apr/May/Jun 2026
- iran_resources(resource, count)                                                -- assistance available to displaced families
- iran_displaced_by_province(province, displaced_people)
- canada_wash_access(region, access_pct)                                          -- water access by region type
- canada_water_advisories(year, advisories)                                      -- long-term advisories 2022-2026
Only SELECT queries are allowed."""


def build_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.executescript(SCHEMA_DDL)

    q1, q2 = _doc("egypt-aid-q1-2026"), _doc("egypt-aid-q2-2026")
    cur.executemany(
        "INSERT INTO egypt_quarterly VALUES (?,?,?,?,?)",
        [
            ("Q1 2026", q1["people_reached"], q1["appeal_usd"], q1["funded_usd"], q1["funded_pct"]),
            ("Q2 2026", q2["people_reached"], q2["appeal_usd"], q2["funded_usd"], q2["funded_pct"]),
        ],
    )
    cur.executemany("INSERT INTO egypt_sector_funding VALUES (?,?)", list(q2["sector_funding_usd"].items()))
    cur.executemany("INSERT INTO egypt_monthly_reach VALUES (?,?)", list(q2["monthly_people_reached"].items()))

    iran = _doc("iran-displaced-2026")
    cur.executemany("INSERT INTO iran_resources VALUES (?,?)", list(iran["resources"].items()))
    cur.executemany(
        "INSERT INTO iran_displaced_by_province VALUES (?,?)",
        list(iran["displaced_by_province"].items()),
    )

    canada = _doc("canada-wash-2026")
    cur.executemany("INSERT INTO canada_wash_access VALUES (?,?)", list(canada["access_by_region"].items()))
    cur.executemany("INSERT INTO canada_water_advisories VALUES (?,?)", list(canada["advisories_trend"].items()))

    conn.commit()
    return conn


@lru_cache(maxsize=1)
def _connection() -> sqlite3.Connection:
    return build_connection()


_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|vacuum)\b",
    re.IGNORECASE,
)


def run_query(query: str, conn: sqlite3.Connection | None = None, max_rows: int = 100) -> dict[str, Any]:
    """Execute a read-only SELECT and return {columns, rows, row_count} or {error}."""
    q = (query or "").strip().rstrip(";").strip()
    if not q:
        return {"error": "empty query"}
    lowered = q.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return {"error": "only SELECT queries are allowed"}
    if ";" in q:
        return {"error": "multiple statements are not allowed"}
    if _FORBIDDEN.search(q):
        return {"error": "only read-only SELECT queries are allowed"}

    conn = conn or _connection()
    try:
        cur = conn.execute(q)
    except sqlite3.Error as exc:
        return {"error": f"SQL error: {exc}"}
    columns = [c[0] for c in cur.description] if cur.description else []
    fetched = cur.fetchmany(max_rows)
    rows = [dict(zip(columns, row)) for row in fetched]
    return {"columns": columns, "rows": rows, "row_count": len(rows)}
