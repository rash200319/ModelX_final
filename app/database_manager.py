"""
MODEL-X Database Manager
Advanced SQLite layer with WAL, FTS5 full-text search, trend aggregation,
anomaly history, automatic pruning, and advisory lock.
"""
import sqlite3
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from config import DB_PATH, RETENTION_DAYS, MAX_DB_RECORDS

logger = logging.getLogger(__name__)

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA cache_size=-16000;

-- ── Core risk signals ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS risks (
    id               TEXT    PRIMARY KEY,
    source           TEXT    NOT NULL,
    signal           TEXT    NOT NULL,
    link             TEXT,
    published        TEXT,
    risk_score       INTEGER,
    category         TEXT,
    location         TEXT,
    district         TEXT,
    province         TEXT,
    confidence       REAL    DEFAULT 1.0,
    keywords         TEXT,
    sentiment_score  REAL,
    risk_breakdown   TEXT,
    ai_summary       TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_historical    INTEGER DEFAULT 0,
    data_source      TEXT,
    raw_data         TEXT
);

-- ── Full-text search (FTS5) ────────────────────────────────────────────────
CREATE VIRTUAL TABLE IF NOT EXISTS risks_fts USING fts5(
    id,
    signal,
    category,
    keywords,
    content='risks',
    content_rowid='rowid'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS risks_ai AFTER INSERT ON risks BEGIN
    INSERT INTO risks_fts(rowid, id, signal, category, keywords)
    VALUES (new.rowid, new.id, new.signal, new.category, new.keywords);
END;
CREATE TRIGGER IF NOT EXISTS risks_ad AFTER DELETE ON risks BEGIN
    INSERT INTO risks_fts(risks_fts, rowid, id, signal, category, keywords)
    VALUES ('delete', old.rowid, old.id, old.signal, old.category, old.keywords);
END;
CREATE TRIGGER IF NOT EXISTS risks_au AFTER UPDATE ON risks BEGIN
    INSERT INTO risks_fts(risks_fts, rowid, id, signal, category, keywords)
    VALUES ('delete', old.rowid, old.id, old.signal, old.category, old.keywords);
    INSERT INTO risks_fts(rowid, id, signal, category, keywords)
    VALUES (new.rowid, new.id, new.signal, new.category, new.keywords);
END;

-- ── Hourly / daily aggregates for trend charts ────────────────────────────
CREATE TABLE IF NOT EXISTS risk_hourly (
    bucket       TEXT    NOT NULL,    -- ISO hour: 2024-06-01T14
    category     TEXT    NOT NULL,
    avg_score    REAL,
    max_score    INTEGER,
    event_count  INTEGER,
    PRIMARY KEY (bucket, category)
);

-- ── Anomaly log (persisted, so history survives restarts) ─────────────────
CREATE TABLE IF NOT EXISTS anomaly_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    category     TEXT,
    score_z      REAL,       -- z-score that triggered the anomaly
    peak_score   INTEGER,
    event_count  INTEGER,
    window_hours INTEGER,
    description  TEXT
);

-- ── Alert dispatch log ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alert_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    channel      TEXT,       -- email | webhook | slack
    risk_id      TEXT REFERENCES risks(id),
    payload      TEXT,
    success      INTEGER DEFAULT 0,
    error_msg    TEXT
);

-- ── Source collection audit ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS data_collection_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source           TEXT    NOT NULL,
    status           TEXT    NOT NULL,
    items_collected  INTEGER DEFAULT 0,
    error_message    TEXT,
    timestamp        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── Location reference ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS locations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    type         TEXT    NOT NULL,
    province     TEXT,
    lat          REAL,
    lon          REAL,
    population   INTEGER,
    risk_profile TEXT,
    parent_id    INTEGER REFERENCES locations(id)
);

-- Indexes ──────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_risks_source     ON risks(source);
CREATE INDEX IF NOT EXISTS idx_risks_score      ON risks(risk_score);
CREATE INDEX IF NOT EXISTS idx_risks_created    ON risks(created_at);
CREATE INDEX IF NOT EXISTS idx_risks_published  ON risks(published);
CREATE INDEX IF NOT EXISTS idx_risks_category   ON risks(category);
CREATE INDEX IF NOT EXISTS idx_hourly_bucket    ON risk_hourly(bucket);
CREATE INDEX IF NOT EXISTS idx_anomaly_detected ON anomaly_log(detected_at);
"""


class DatabaseManager:
    """Thread-safe SQLite database manager with advanced query helpers."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── Internals ─────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA cache_size=-16000;")
        return conn

    def _init_schema(self):
        with self._connect() as conn:
            for stmt in _SCHEMA.split(";"):
                s = stmt.strip()
                if s:
                    try:
                        conn.execute(s)
                    except sqlite3.OperationalError as e:
                        logger.debug(f"Schema init: {e}")
            self._migrate_legacy_schema(conn)
            conn.commit()

    def _migrate_legacy_schema(self, conn: sqlite3.Connection):
        """Add missing columns for older DB files without dropping any data."""
        try:
            existing_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(risks)").fetchall()
            }
        except sqlite3.Error as e:
            logger.debug(f"Schema migration skipped: {e}")
            return

        required_columns = {
            "confidence": "REAL DEFAULT 1.0",
            "keywords": "TEXT",
            "sentiment_score": "REAL",
            "risk_breakdown": "TEXT",
            "ai_summary": "TEXT",
            "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "is_historical": "INTEGER DEFAULT 0",
            "data_source": "TEXT",
            "raw_data": "TEXT",
        }

        for col, col_def in required_columns.items():
            if col in existing_cols:
                continue
            try:
                conn.execute(f"ALTER TABLE risks ADD COLUMN {col} {col_def}")
                logger.info(f"Migrated DB: added risks.{col}")
            except sqlite3.OperationalError as e:
                logger.debug(f"Could not add risks.{col}: {e}")

    def _try_add_missing_risks_column(self, conn: sqlite3.Connection, error: Exception) -> bool:
        """Attempt to heal inserts when DB schema is behind expected model."""
        msg = str(error)
        marker = "table risks has no column named "
        if marker not in msg:
            return False

        missing_col = msg.split(marker, 1)[-1].strip()
        if not missing_col:
            return False

        definitions = {
            "confidence": "REAL DEFAULT 1.0",
            "keywords": "TEXT",
            "sentiment_score": "REAL",
            "risk_breakdown": "TEXT",
            "ai_summary": "TEXT",
            "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "is_historical": "INTEGER DEFAULT 0",
            "data_source": "TEXT",
            "raw_data": "TEXT",
        }
        col_def = definitions.get(missing_col)
        if not col_def:
            logger.warning(f"Unknown missing column on risks table: {missing_col}")
            return False

        try:
            conn.execute(f"ALTER TABLE risks ADD COLUMN {missing_col} {col_def}")
            logger.info(f"Auto-migrated DB during insert: added risks.{missing_col}")
            return True
        except sqlite3.Error as migrate_err:
            logger.warning(f"Auto-migration failed for risks.{missing_col}: {migrate_err}")
            return False

    @staticmethod
    def _make_id(source: str, signal: str) -> str:
        return hashlib.md5(f"{source}|{signal}".encode()).hexdigest()

    # ── Write ─────────────────────────────────────────────────────────────

    def insert_risk(self, record: Dict[str, Any]) -> bool:
        return self.batch_insert_risks([record]) > 0

    def batch_insert_risks(self, records: List[Dict[str, Any]]) -> int:
        if not records:
            return 0
        inserted = 0
        _keys = [
            "id", "source", "signal", "link", "published", "risk_score",
            "category", "location", "district", "province", "confidence",
            "keywords", "sentiment_score", "risk_breakdown", "ai_summary",
            "created_at",
        ]
        sql = """
            INSERT OR IGNORE INTO risks
              (id, source, signal, link, published, risk_score,
               category, location, district, province, confidence,
               keywords, sentiment_score, risk_breakdown, ai_summary,
               created_at)
            VALUES
              (:id,:source,:signal,:link,:published,:risk_score,
               :category,:location,:district,:province,:confidence,
               :keywords,:sentiment_score,:risk_breakdown,:ai_summary,
               :created_at)
        """
        with self._connect() as conn:
            for rec in records:
                # Deterministic dedup ID
                if not rec.get("id") or rec["id"][:5] in ("news_", "reddi"):
                    rec["id"] = self._make_id(rec.get("source", ""), rec.get("signal", ""))
                clean = {k: rec.get(k) for k in _keys}
                clean.setdefault("created_at", datetime.now().isoformat())
                try:
                    conn.execute(sql, clean)
                    if conn.execute("SELECT changes()").fetchone()[0]:
                        inserted += 1
                except sqlite3.Error as e:
                    healed = self._try_add_missing_risks_column(conn, e)
                    if healed:
                        try:
                            conn.execute(sql, clean)
                            if conn.execute("SELECT changes()").fetchone()[0]:
                                inserted += 1
                            continue
                        except sqlite3.Error as retry_err:
                            logger.warning(f"Insert retry error for {clean['id'][:8]}: {retry_err}")
                    else:
                        logger.warning(f"Insert error for {clean['id'][:8]}: {e}")
            conn.commit()
        if inserted:
            self._update_hourly_aggregates()
        return inserted

    # ── Read ──────────────────────────────────────────────────────────────

    def get_risks(
        self,
        limit: int = 200,
        offset: int = 0,
        min_score: Optional[int] = None,
        max_score: Optional[int] = None,
        sources: Optional[List[str]] = None,
        categories: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        location: Optional[str] = None,
        fts_query: Optional[str] = None,
    ) -> pd.DataFrame:
        """Rich filtering including full-text search."""
        if fts_query:
            return self._fts_query(fts_query, limit)

        where, params = ["1=1"], {}
        if min_score  is not None: where.append("risk_score >= :min_score");  params["min_score"]  = min_score
        if max_score  is not None: where.append("risk_score <= :max_score");  params["max_score"]  = max_score
        if start_date is not None: where.append("published >= :start_date");  params["start_date"] = start_date
        if end_date   is not None: where.append("published <= :end_date");    params["end_date"]   = end_date
        if location   is not None: where.append("location LIKE :location");   params["location"]   = f"%{location}%"

        if sources:
            ph = [f":src_{i}" for i in range(len(sources))]
            where.append(f"source IN ({','.join(ph)})")
            for i, s in enumerate(sources): params[f"src_{i}"] = s

        if categories:
            cat_clauses = [f"category LIKE :cat_{i}" for i in range(len(categories))]
            where.append(f"({' OR '.join(cat_clauses)})")
            for i, c in enumerate(categories): params[f"cat_{i}"] = f"%{c}%"

        sql = f"""
            SELECT * FROM risks
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(published, created_at) DESC
            LIMIT :limit OFFSET :offset
        """
        params.update({"limit": max(1, int(limit)), "offset": max(0, int(offset))})

        try:
            with self._connect() as conn:
                return pd.read_sql_query(sql, conn, params=params)
        except Exception as e:
            logger.error(f"get_risks: {e}")
            return pd.DataFrame()

    def _fts_query(self, query: str, limit: int) -> pd.DataFrame:
        """Full-text search via FTS5."""
        sql = """
            SELECT r.* FROM risks r
            JOIN risks_fts f ON r.id = f.id
            WHERE risks_fts MATCH :q
            ORDER BY rank
            LIMIT :limit
        """
        try:
            with self._connect() as conn:
                return pd.read_sql_query(sql, conn, params={"q": query, "limit": limit})
        except Exception as e:
            logger.error(f"FTS query failed: {e}")
            return pd.DataFrame()

    def get_risk_stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            c = conn.cursor()
            total = c.execute("SELECT COUNT(*) FROM risks").fetchone()[0]
            high  = c.execute(f"SELECT COUNT(*) FROM risks WHERE risk_score >= 8").fetchone()[0]
            avg   = c.execute("SELECT ROUND(AVG(risk_score),2) FROM risks").fetchone()[0]
            sources = [dict(r) for r in c.execute(
                "SELECT source, COUNT(*) as count FROM risks GROUP BY source ORDER BY count DESC"
            ).fetchall()]
            cats = [dict(r) for r in c.execute(
                "SELECT category, COUNT(*) as count FROM risks GROUP BY category ORDER BY count DESC LIMIT 10"
            ).fetchall()]
            recent_24h = c.execute(
                "SELECT COUNT(*) FROM risks WHERE created_at >= datetime('now','-1 day')"
            ).fetchone()[0]
        return {
            "total_risks": total,
            "high_risk_count": high,
            "avg_risk_score": avg,
            "recent_24h": recent_24h,
            "sources": sources,
            "categories": cats,
        }

    # ── Trend / Time-Series ───────────────────────────────────────────────

    def get_hourly_trend(self, hours: int = 48) -> pd.DataFrame:
        sql = """
            SELECT bucket, category, avg_score, max_score, event_count
            FROM risk_hourly
            WHERE bucket >= :since
            ORDER BY bucket ASC
        """
        since = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H")
        try:
            with self._connect() as conn:
                return pd.read_sql_query(sql, conn, params={"since": since})
        except Exception as e:
            logger.error(f"get_hourly_trend: {e}")
            return pd.DataFrame()

    def _update_hourly_aggregates(self):
        """Rebuild last 2 hours of hourly buckets from raw risks table."""
        since = (datetime.utcnow() - timedelta(hours=2)).strftime("%Y-%m-%dT%H")
        sql_agg = """
            INSERT OR REPLACE INTO risk_hourly (bucket, category, avg_score, max_score, event_count)
            SELECT
                strftime('%Y-%m-%dT%H', COALESCE(published, created_at)) AS bucket,
                COALESCE(category, 'General') AS category,
                ROUND(AVG(risk_score), 2),
                MAX(risk_score),
                COUNT(*)
            FROM risks
            WHERE strftime('%Y-%m-%dT%H', COALESCE(published, created_at)) >= :since
            GROUP BY bucket, category
        """
        try:
            with self._connect() as conn:
                conn.execute(sql_agg, {"since": since})
                conn.commit()
        except Exception as e:
            logger.debug(f"Hourly agg update: {e}")

    def get_daily_trend(self, days: int = 30) -> pd.DataFrame:
        sql = """
            SELECT
                DATE(COALESCE(published, created_at)) AS day,
                ROUND(AVG(risk_score), 2) AS avg_score,
                MAX(risk_score)           AS max_score,
                COUNT(*)                  AS event_count
            FROM risks
            WHERE DATE(COALESCE(published, created_at)) >= DATE('now', :offset)
            GROUP BY day
            ORDER BY day ASC
        """
        try:
            with self._connect() as conn:
                return pd.read_sql_query(sql, conn, params={"offset": f"-{days} days"})
        except Exception as e:
            logger.error(f"get_daily_trend: {e}")
            return pd.DataFrame()

    def get_category_timeline(self, days: int = 14) -> pd.DataFrame:
        sql = """
            SELECT
                DATE(COALESCE(published, created_at)) AS day,
                category,
                ROUND(AVG(risk_score), 2) AS avg_score,
                COUNT(*)                   AS count
            FROM risks
            WHERE DATE(COALESCE(published, created_at)) >= DATE('now', :offset)
              AND category IS NOT NULL
            GROUP BY day, category
            ORDER BY day ASC
        """
        try:
            with self._connect() as conn:
                return pd.read_sql_query(sql, conn, params={"offset": f"-{days} days"})
        except Exception as e:
            logger.error(f"get_category_timeline: {e}")
            return pd.DataFrame()

    # ── Anomaly ───────────────────────────────────────────────────────────

    def log_anomaly(self, category: str, score_z: float, peak: int,
                    count: int, window_hours: int, desc: str):
        sql = """
            INSERT INTO anomaly_log
              (category, score_z, peak_score, event_count, window_hours, description)
            VALUES (?,?,?,?,?,?)
        """
        try:
            with self._connect() as conn:
                conn.execute(sql, (category, round(score_z, 3), peak, count, window_hours, desc))
                conn.commit()
        except Exception as e:
            logger.error(f"log_anomaly: {e}")

    def get_recent_anomalies(self, hours: int = 48) -> pd.DataFrame:
        sql = """
            SELECT * FROM anomaly_log
            WHERE detected_at >= datetime('now', :offset)
            ORDER BY detected_at DESC
        """
        try:
            with self._connect() as conn:
                return pd.read_sql_query(sql, conn, params={"offset": f"-{hours} hours"})
        except Exception as e:
            return pd.DataFrame()

    # ── Alert log ─────────────────────────────────────────────────────────

    def log_alert(self, channel: str, risk_id: str, payload: str,
                  success: bool, error: str = ""):
        sql = """
            INSERT INTO alert_log (channel, risk_id, payload, success, error_msg)
            VALUES (?,?,?,?,?)
        """
        try:
            with self._connect() as conn:
                conn.execute(sql, (channel, risk_id, payload, int(success), error))
                conn.commit()
        except Exception as e:
            logger.error(f"log_alert: {e}")

    # ── Maintenance ───────────────────────────────────────────────────────

    def prune_old_records(self) -> int:
        """Delete records older than RETENTION_DAYS and keep DB under MAX_DB_RECORDS."""
        deleted = 0
        cutoff = (datetime.utcnow() - timedelta(days=RETENTION_DAYS)).isoformat()
        try:
            with self._connect() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM risks WHERE created_at < ?", (cutoff,))
                deleted += c.rowcount
                # Trim if still over cap
                total = c.execute("SELECT COUNT(*) FROM risks").fetchone()[0]
                if total > MAX_DB_RECORDS:
                    excess = total - MAX_DB_RECORDS
                    c.execute("""
                        DELETE FROM risks WHERE id IN (
                            SELECT id FROM risks ORDER BY created_at ASC LIMIT ?
                        )
                    """, (excess,))
                    deleted += c.rowcount
                conn.commit()
        except Exception as e:
            logger.error(f"prune_old_records: {e}")
        if deleted:
            logger.info(f"Pruned {deleted} old records from DB.")
        return deleted

    def vacuum(self):
        """Run VACUUM to reclaim disk space."""
        try:
            with self._connect() as conn:
                conn.execute("VACUUM")
        except Exception as e:
            logger.error(f"vacuum: {e}")


# Singleton
db = DatabaseManager()

# ── Backward-compat helpers ──────────────────────────────────────────────────
def init_db(): pass

def save_risks(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    return db.batch_insert_risks(df.to_dict("records"))
