"""Persistence layer — SQLite by default, PlanetScale MySQL when configured."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from services import config as cfg

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Config-driven backend selection
# ---------------------------------------------------------------------------
DB_CFG = (cfg.CONFIG.get("database") or {})
DB_BACKEND = (DB_CFG.get("backend") or "sqlite").lower()
DB_PATH = DATA / (DB_CFG.get("sqlite_path") or "virusgpt.db")
if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_CFG.get("sqlite_path")

if DB_BACKEND == "planetscale" or DB_BACKEND == "mysql":
    try:
        import pymysql
        import pymysql.cursors
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"database backend '{DB_BACKEND}' selected but pymysql is not installed: {exc}") from exc
    _MYSQL_DSN = DB_CFG.get("mysql_dsn") or ""
    _MYSQL_HOST = DB_CFG.get("mysql_host", "")
    _MYSQL_PORT = int(DB_CFG.get("mysql_port", 3306))
    _MYSQL_USER = DB_CFG.get("mysql_user", "")
    _MYSQL_PASSWORD = DB_CFG.get("mysql_password", "")
    _MYSQL_DB = DB_CFG.get("mysql_database", "")
    _MYSQL_SSL = DB_CFG.get("mysql_ssl", True)
    _mysql_conn = None

    def _ensure_mysql_connection():
        global _mysql_conn
        if _mysql_conn is None:
            _mysql_conn = pymysql.connect(
                host=_MYSQL_HOST,
                port=_MYSQL_PORT,
                user=_MYSQL_USER,
                password=_MYSQL_PASSWORD,
                database=_MYSQL_DB,
                ssl=_MYSQL_SSL,
                connect_timeout=15,
                read_timeout=30,
                write_timeout=30,
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
            )
        return _mysql_conn

else:
    def _ensure_mysql_connection():
        raise RuntimeError("MySQL connection requested but SQLite is the active backend.")


def _conn():
    if DB_BACKEND in ("planetscale", "mysql"):
        return _ensure_mysql_connection()
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ---------------------------------------------------------------------------
# Backup + corruption recovery (SQLite only)
# ---------------------------------------------------------------------------
BACKUP_DIR = DATA / "db_backups"
BACKUP_KEEP = 10  # keep the 10 most recent backups


def _sqlite_active() -> bool:
    return DB_BACKEND not in ("planetscale", "mysql")


def backup_db(tag: str = "") -> Optional[str]:
    """Snapshot virusgpt.db (+ WAL/SHM) into data/db_backups/. Returns path or None."""
    if not _sqlite_active():
        return None
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        # Flush WAL so the snapshot is complete + consistent.
        try:
            c = sqlite3.connect(str(DB_PATH))
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()
        except Exception:
            pass
        ts = time.strftime("%Y%m%d-%H%M%S")
        stamp = f"{ts}{('-' + tag) if tag else ''}"
        dst = BACKUP_DIR / f"virusgpt-{stamp}.db"
        import shutil
        if DB_PATH.exists():
            shutil.copy2(DB_PATH, dst)
            # also snapshot the -wal / -shm siblings if present
            for ext in ("-wal", "-shm"):
                sib = DB_PATH.parent / (DB_PATH.stem + ext)
                if sib.exists():
                    shutil.copy2(sib, dst.parent / (dst.stem + ext))
        # prune old backups (keep the BACKUP_KEEP most recent).
        # NOTE: delete the .db file itself (old) plus its -wal/-shm siblings,
        # which backup_db names as "<stem>-wal" / "<stem>-shm". The earlier
        # `old.stem + ""` form dropped the ".db" extension and so never deleted
        # the main backup — that made the prune a silent no-op.
        backs = sorted(BACKUP_DIR.glob("virusgpt-*.db"), key=lambda p: p.stat().st_mtime)
        for old in backs[:-BACKUP_KEEP]:
            for sib in (old, old.parent / (old.stem + "-wal"),
                        old.parent / (old.stem + "-shm")):
                if sib.exists():
                    try:
                        sib.unlink()
                    except Exception:
                        pass
        return str(dst)
    except Exception as e:  # noqa: BLE001
        print("db backup failed:", e)
        return None


def verify_db(path=None) -> bool:
    """Run PRAGMA integrity_check; True if the DB is healthy."""
    target = Path(path) if path else DB_PATH
    if not target.exists():
        return False
    try:
        c = sqlite3.connect(str(target))
        c.row_factory = None
        rows = c.execute("PRAGMA integrity_check").fetchall()
        c.close()
        return all(r[0] == "ok" for r in rows)
    except Exception:
        return False


def list_backups() -> List[str]:
    if not BACKUP_DIR.exists():
        return []
    return [str(p) for p in sorted(BACKUP_DIR.glob("virusgpt-*.db"),
                                   key=lambda p: p.stat().st_mtime, reverse=True)]


def restore_db(backup_path: str) -> bool:
    """Replace the live DB with a backup (and its WAL/SHM if present)."""
    if not _sqlite_active() or not Path(backup_path).exists():
        return False
    try:
        import shutil
        # close any open handles by forcing a checkpoint + remove live files
        try:
            c = sqlite3.connect(str(DB_PATH))
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()
        except Exception:
            pass
        for ext in ("", "-wal", "-shm"):
            live = DB_PATH.parent / (DB_PATH.stem + ext)
            if live.exists():
                live.unlink()
        src = Path(backup_path)
        shutil.copy2(src, DB_PATH)
        for ext in ("-wal", "-shm"):
            sib = src.parent / (src.stem + ext)
            if sib.exists():
                shutil.copy2(sib, DB_PATH.parent / (DB_PATH.stem + ext))
        return verify_db()
    except Exception as e:  # noqa: BLE001
        print("db restore failed:", e)
        return False


def auto_heal_db() -> dict:
    """If the live DB is corrupted, restore the newest verified backup.

    Returns {healed, action, backup}. Called at startup before any writes.
    """
    if not _sqlite_active():
        return {"healed": False, "action": "skip-mysql", "backup": None}
    if verify_db():
        return {"healed": False, "action": "ok", "backup": None}
    # corrupted -> find newest good backup
    for b in list_backups():
        if verify_db(b):
            ok = restore_db(b)
            if ok:
                return {"healed": True, "action": "restored", "backup": b}
    return {"healed": False, "action": "no-good-backup", "backup": None}


def init_db():
    conn = _conn()
    try:
        if DB_BACKEND in ("planetscale", "mysql"):
            # Best-effort schema creation; use utf8mb4 for emoji/text.
            conn.cursor().execute(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    id VARCHAR(64) PRIMARY KEY,
                    goal TEXT,
                    status VARCHAR(32) DEFAULT 'created',
                    planner VARCHAR(64),
                    requires_approval TINYINT(1) DEFAULT 0,
                    approval_state VARCHAR(32),
                    final_result TEXT,
                    created_at VARCHAR(32),
                    updated_at VARCHAR(32),
                    completed_at VARCHAR(32)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """
            )
            conn.cursor().execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id VARCHAR(64) PRIMARY KEY,
                    mission_id VARCHAR(64),
                    title TEXT,
                    description TEXT,
                    status VARCHAR(32) DEFAULT 'pending',
                    priority INT DEFAULT 60,
                    agent VARCHAR(64),
                    dependencies JSON,
                    attempts INT DEFAULT 0,
                    max_attempts INT DEFAULT 2,
                    result TEXT,
                    verification TEXT,
                    created_at VARCHAR(32),
                    started_at VARCHAR(32),
                    completed_at VARCHAR(32)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """
            )
            conn.cursor().execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id VARCHAR(64) PRIMARY KEY,
                    mission_id VARCHAR(64),
                    task_id VARCHAR(64),
                    agent VARCHAR(64),
                    event VARCHAR(128),
                    data TEXT,
                    created_at VARCHAR(32)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """
            )
            conn.cursor().execute(
                """
                CREATE TABLE IF NOT EXISTS agent_memory (
                    id VARCHAR(64) PRIMARY KEY,
                    agent VARCHAR(64),
                    mission_id VARCHAR(64),
                    namespace VARCHAR(128),
                    content TEXT,
                    importance REAL DEFAULT 0.5,
                    created_at VARCHAR(32)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """
            )
            conn.cursor().execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    id VARCHAR(64) PRIMARY KEY,
                    mission_id VARCHAR(64),
                    task_id VARCHAR(64),
                    agent VARCHAR(64),
                    kind VARCHAR(64),
                    path TEXT,
                    meta TEXT,
                    created_at VARCHAR(32)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """
            )
            for idx, table, column in (
                ("idx_tasks_mission", "tasks", "mission_id"),
                ("idx_events_mission", "events", "mission_id"),
                ("idx_memory_agent", "agent_memory", "agent"),
            ):
                conn.cursor().execute(
                    f"CREATE INDEX IF NOT EXISTS {idx} ON {table} ({column})"
                )
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                goal TEXT,
                status TEXT DEFAULT 'created',
                planner TEXT,
                requires_approval INTEGER DEFAULT 0,
                approval_state TEXT,
                final_result TEXT,
                created_at TEXT,
                updated_at TEXT,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                title TEXT,
                description TEXT,
                status TEXT DEFAULT 'pending',
                priority INTEGER DEFAULT 60,
                agent TEXT,
                dependencies TEXT,
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 2,
                result TEXT,
                verification TEXT,
                created_at TEXT,
                started_at TEXT,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                task_id TEXT,
                agent TEXT,
                event TEXT,
                data TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_memory (
                id TEXT PRIMARY KEY,
                agent TEXT,
                mission_id TEXT,
                namespace TEXT,
                content TEXT,
                importance REAL DEFAULT 0.5,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                mission_id TEXT,
                task_id TEXT,
                agent TEXT,
                kind TEXT,
                path TEXT,
                meta TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS personas (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE,
                data TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                name TEXT PRIMARY KEY,
                data TEXT,
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_mission ON tasks(mission_id);
            CREATE INDEX IF NOT EXISTS idx_events_mission ON events(mission_id);
            CREATE INDEX IF NOT EXISTS idx_memory_agent ON agent_memory(agent);
            """
        )
        # Forward-migrate: ensure the resume-payload column exists on existing DBs.
        _migrate(conn)
    finally:
        if DB_BACKEND not in ("planetscale", "mysql"):
            conn.close()


def _add_column(conn, table: str, column: str, col_type: str):
    """Add a column if it does not already exist (SQLite + MySQL)."""
    if DB_BACKEND in ("planetscale", "mysql"):
        cur = conn.cursor()
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except Exception:
            # Column likely already exists; nothing to do.
            pass
        finally:
            cur.close()
    else:
        existing = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def _migrate(conn):
    """Schema migrations applied at startup. Idempotent."""
    _add_column(conn, "missions", "personas", "TEXT")


# ---------------------------------------------------------------------------
# Models / helpers shared by both backends
# ---------------------------------------------------------------------------

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if DB_BACKEND in ("planetscale", "mysql"):
        return dict(row)
    return dict(row)


def _execute(conn, sql, params=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        # MySQL connections are opened with autocommit=True; SQLite needs an
        # explicit commit (and there is one connection per call here).
        if DB_BACKEND not in ("planetscale", "mysql"):
            conn.commit()
    finally:
        cur.close()


def _fetchone(conn, sql, params=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        row = cur.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        cur.close()


def _fetchall(conn, sql, params=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        cur.close()


@dataclass
class Mission:
    id: str = field(default_factory=lambda: f"M-{int(time.time())}-{uuid.uuid4().hex[:6]}")
    goal: str = ""
    status: str = "created"
    planner: str = ""
    requires_approval: bool = False
    approval_state: Optional[str] = None
    final_result: Optional[str] = None
    personas: Optional[str] = None  # JSON-encoded room persona list, for cross-restart resume
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    completed_at: Optional[str] = None


@dataclass
class Task:
    id: str = field(default_factory=lambda: f"T-{int(time.time())}-{uuid.uuid4().hex[:6]}")
    mission_id: str = ""
    title: str = ""
    description: str = ""
    status: str = "pending"
    priority: int = 60
    agent: str = ""
    dependencies: List[str] = field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 2
    result: Optional[str] = None
    verification: Optional[str] = None
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass
class Event:
    id: str = field(default_factory=lambda: f"E-{uuid.uuid4().hex[:10]}")
    mission_id: str = ""
    task_id: str = ""
    agent: str = ""
    event: str = ""
    data: str = ""
    created_at: str = field(default_factory=_now)


@dataclass
class AgentMemory:
    id: str = field(default_factory=lambda: f"M-{uuid.uuid4().hex[:10]}")
    agent: str = ""
    mission_id: str = ""
    namespace: str = ""
    content: str = ""
    importance: float = 0.5
    created_at: str = field(default_factory=_now)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------
class Repository:
    def __init__(self):
        init_db()

    def _conn(self):
        return _conn()

    def create_mission(self, m: Mission) -> Mission:
        conn = self._conn()
        try:
            _execute(
                conn,
                "INSERT INTO missions (id,goal,status,planner,requires_approval,approval_state,final_result,personas,created_at,updated_at,completed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                if DB_BACKEND in ("planetscale", "mysql")
                else "INSERT INTO missions (id,goal,status,planner,requires_approval,approval_state,final_result,personas,created_at,updated_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    m.id,
                    m.goal,
                    m.status,
                    m.planner,
                    int(m.requires_approval),
                    m.approval_state,
                    m.final_result,
                    m.personas,
                    m.created_at,
                    m.updated_at,
                    m.completed_at,
                ),
            )
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()
        return m

    def update_mission(self, m: Mission):
        conn = self._conn()
        try:
            _execute(
                conn,
                "UPDATE missions SET status=%s,planner=%s,approval_state=%s,final_result=%s,personas=%s,updated_at=%s,completed_at=%s WHERE id=%s"
                if DB_BACKEND in ("planetscale", "mysql")
                else "UPDATE missions SET status=?,planner=?,approval_state=?,final_result=?,personas=?,updated_at=?,completed_at=? WHERE id=?",
                (m.status, m.planner, m.approval_state, m.final_result, m.personas, m.updated_at, m.completed_at, m.id),
            )
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    def get_mission(self, mid: str) -> Optional[Mission]:
        conn = self._conn()
        try:
            row = _fetchone(conn, "SELECT * FROM missions WHERE id=%s" if DB_BACKEND in ("planetscale", "mysql") else "SELECT * FROM missions WHERE id=?", (mid,))
            if not row:
                return None
            return Mission(**row)
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    def list_missions(self, limit: int = 50) -> List[Mission]:
        conn = self._conn()
        try:
            rows = _fetchall(conn, "SELECT * FROM missions ORDER BY updated_at DESC LIMIT %s" if DB_BACKEND in ("planetscale", "mysql") else "SELECT * FROM missions ORDER BY updated_at DESC LIMIT ?", (limit,))
            return [Mission(**r) for r in rows]
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    def create_task(self, t: Task) -> Task:
        conn = self._conn()
        try:
            _execute(
                conn,
                "INSERT INTO tasks (id,mission_id,title,description,status,priority,agent,dependencies,attempts,max_attempts,result,verification,created_at,started_at,completed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                if DB_BACKEND in ("planetscale", "mysql")
                else "INSERT INTO tasks (id,mission_id,title,description,status,priority,agent,dependencies,attempts,max_attempts,result,verification,created_at,started_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    t.id,
                    t.mission_id,
                    t.title,
                    t.description,
                    t.status,
                    t.priority,
                    t.agent,
                    json.dumps(t.dependencies),
                    t.attempts,
                    t.max_attempts,
                    t.result,
                    t.verification,
                    t.created_at,
                    t.started_at,
                    t.completed_at,
                ),
            )
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()
        return t

    def update_task(self, t: Task):
        conn = self._conn()
        try:
            _execute(
                conn,
                "UPDATE tasks SET status=%s,attempts=%s,result=%s,verification=%s,started_at=%s,completed_at=%s WHERE id=%s"
                if DB_BACKEND in ("planetscale", "mysql")
                else "UPDATE tasks SET status=?,attempts=?,result=?,verification=?,started_at=?,completed_at=? WHERE id=?",
                (t.status, t.attempts, t.result, t.verification, t.started_at, t.completed_at, t.id),
            )
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    def get_task(self, tid: str) -> Optional[Task]:
        conn = self._conn()
        try:
            row = _fetchone(conn, "SELECT * FROM tasks WHERE id=%s" if DB_BACKEND in ("planetscale", "mysql") else "SELECT * FROM tasks WHERE id=?", (tid,))
            if not row:
                return None
            if isinstance(row.get("dependencies"), str):
                row["dependencies"] = json.loads(row.get("dependencies") or "[]")
            return Task(**row)
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    def list_mission_tasks(self, mission_id: str) -> List[Task]:
        conn = self._conn()
        try:
            sql = (
                "SELECT * FROM tasks WHERE mission_id=%s ORDER BY priority DESC, created_at ASC"
                if DB_BACKEND in ("planetscale", "mysql")
                else "SELECT * FROM tasks WHERE mission_id=? ORDER BY priority DESC, created_at ASC"
            )
            rows = _fetchall(conn, sql, (mission_id,))
            out: List[Task] = []
            for r in rows:
                if isinstance(r.get("dependencies"), str):
                    r["dependencies"] = json.loads(r.get("dependencies") or "[]")
                out.append(Task(**r))
            return out
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    def mission_artifacts(self, mission_id: str) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            sql = (
                "SELECT * FROM artifacts WHERE mission_id=%s ORDER BY created_at ASC"
                if DB_BACKEND in ("planetscale", "mysql")
                else "SELECT * FROM artifacts WHERE mission_id=? ORDER BY created_at ASC"
            )
            return _fetchall(conn, sql, (mission_id,))
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    def add_event(self, e: Event):
        conn = self._conn()
        try:
            _execute(
                conn,
                "INSERT INTO events (id,mission_id,task_id,agent,event,data,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                if DB_BACKEND in ("planetscale", "mysql")
                else "INSERT INTO events (id,mission_id,task_id,agent,event,data,created_at) VALUES (?,?,?,?,?,?,?)",
                (e.id, e.mission_id, e.task_id, e.agent, e.event, e.data, e.created_at),
            )
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    def mission_events(self, mission_id: str, limit: int = 100) -> List[Event]:
        conn = self._conn()
        try:
            sql = (
                "SELECT * FROM events WHERE mission_id=%s ORDER BY created_at DESC LIMIT %s"
                if DB_BACKEND in ("planetscale", "mysql")
                else "SELECT * FROM events WHERE mission_id=? ORDER BY created_at DESC LIMIT ?"
            )
            rows = _fetchall(conn, sql, (mission_id, limit))
            return [Event(**r) for r in rows]
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    def add_memory(self, m: AgentMemory):
        conn = self._conn()
        try:
            _execute(
                conn,
                "INSERT INTO agent_memory (id,agent,mission_id,namespace,content,importance,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                if DB_BACKEND in ("planetscale", "mysql")
                else "INSERT INTO agent_memory (id,agent,mission_id,namespace,content,importance,created_at) VALUES (?,?,?,?,?,?,?)",
                (m.id, m.agent, m.mission_id, m.namespace, m.content, m.importance, m.created_at),
            )
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    def recent_memory(self, agent: str, limit: int = 20) -> List[AgentMemory]:
        conn = self._conn()
        try:
            sql = (
                "SELECT * FROM agent_memory WHERE agent=%s ORDER BY created_at DESC LIMIT %s"
                if DB_BACKEND in ("planetscale", "mysql")
                else "SELECT * FROM agent_memory WHERE agent=? ORDER BY created_at DESC LIMIT ?"
            )
            rows = _fetchall(conn, sql, (agent, limit))
            return [AgentMemory(**r) for r in rows]
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    # ------------------------------------------------------------------
    # Personas — server-persisted (replaces localStorage vg_personas)
    # ------------------------------------------------------------------
    def get_personas(self) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            rows = _fetchall(conn, "SELECT * FROM personas ORDER BY updated_at ASC")
            return [json.loads(r["data"]) for r in rows if r.get("data")]
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    def save_personas(self, personas: List[Dict[str, Any]]) -> None:
        conn = self._conn()
        try:
            _execute(conn, "DELETE FROM personas")
            now = _now()
            for p in personas:
                pid = p.get("id") or f"P-{uuid.uuid4().hex[:8]}"
                _execute(
                    conn,
                    "INSERT INTO personas (id,name,data,updated_at) VALUES (?,?,?,?)",
                    (pid, p.get("name", pid), json.dumps(p), now),
                )
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    # ------------------------------------------------------------------
    # Sessions (browser/chat rooms) — server-persisted
    # ------------------------------------------------------------------
    def get_sessions(self) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            rows = _fetchall(conn, "SELECT * FROM sessions ORDER BY updated_at ASC")
            return [json.loads(r["data"]) for r in rows if r.get("data")]
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    def save_session(self, session: Dict[str, Any]) -> None:
        conn = self._conn()
        try:
            name = session.get("name") or "default"
            _execute(
                conn,
                "INSERT INTO sessions (name,data,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (name, json.dumps(session), _now()),
            )
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()

    def delete_session(self, name: str) -> None:
        conn = self._conn()
        try:
            _execute(conn, "DELETE FROM sessions WHERE name=?", (name,))
        finally:
            if DB_BACKEND not in ("planetscale", "mysql"):
                conn.close()
