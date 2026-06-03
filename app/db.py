"""SQLite 持久化层 (§13 数据模型)。

启动期建表;表结构与方案 §13 对齐。

不依赖 ORM,直接用 sqlite3 (M0-M5 足够)。后期切 PostgreSQL 时再上 SQLAlchemy。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  username        TEXT PRIMARY KEY,
  display_name    TEXT,
  role            TEXT NOT NULL DEFAULT 'user',
  groups          TEXT,
  created_at      TEXT DEFAULT (datetime('now')),
  last_login_at   TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  id                  TEXT PRIMARY KEY,
  username            TEXT NOT NULL,
  source              TEXT NOT NULL,          -- skill | chat | rerun
  skill_id            TEXT,
  skill_version       INTEGER,
  question            TEXT,
  params              TEXT,                   -- JSON
  status              TEXT NOT NULL,          -- pending | running | done | failed
  error               TEXT,
  row_count           INTEGER,
  latency_ms          INTEGER,
  llm_model           TEXT,
  llm_input_tokens    INTEGER,
  llm_output_tokens   INTEGER,
  created_at          TEXT DEFAULT (datetime('now')),
  finished_at         TEXT,
  parent_task_id      TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_user_time ON tasks(username, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_skill ON tasks(skill_id);

CREATE TABLE IF NOT EXISTS task_files (
  id           TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL,
  filename     TEXT NOT NULL,
  path         TEXT NOT NULL,
  size_bytes   INTEGER,
  preview_json TEXT,
  created_at   TEXT DEFAULT (datetime('now')),
  FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  username        TEXT NOT NULL,
  action          TEXT NOT NULL,             -- chat | run_skill | export | login | logout | switch_model
  task_id         TEXT,
  question        TEXT,
  service         TEXT,
  odata_url       TEXT,
  row_count       INTEGER,
  latency_ms      INTEGER,
  llm_model       TEXT,
  llm_tokens      INTEGER,
  ip              TEXT,
  created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(username, created_at DESC);

CREATE TABLE IF NOT EXISTS sensitive_fields (
  service     TEXT NOT NULL,
  field       TEXT NOT NULL,
  mask_mode   TEXT NOT NULL,                 -- redact | partial | hash
  added_by    TEXT,
  created_at  TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (service, field)
);

-- LLM provider API keys —— 每用户独立 (用户 + env_var 主键)
-- 一期: 内网部署 + FS 权限保护 + base64 混淆;二期升级为 AES-256-GCM + OS Keyring
CREATE TABLE IF NOT EXISTS llm_api_keys (
  username    TEXT NOT NULL,
  env_var     TEXT NOT NULL,                 -- 如 DASHSCOPE_API_KEY
  value_b64   TEXT NOT NULL,                 -- base64 混淆后的 key
  updated_at  TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (username, env_var)
);
"""


class DB:
    """轻量 SQLite 包装。一进程一实例。"""

    def __init__(self, db_path: Path) -> None:
        self.path = Path(db_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            # 一次性迁移: 旧版 llm_api_keys 以 env_var 为主键,新版加 username。
            # 若发现旧结构(无 username 列),整表删除,数据由用户重新填一次。
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_api_keys'"
            ).fetchone()
            if row:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(llm_api_keys)").fetchall()]
                if "username" not in cols:
                    conn.execute("DROP TABLE llm_api_keys")
            conn.executescript(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            yield cur
        finally:
            conn.close()

    # ---------- users ----------
    def upsert_user(self, username: str, display_name: str = "", role: str = "user") -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO users(username, display_name, role, last_login_at) "
                "VALUES(?,?,?,datetime('now')) "
                "ON CONFLICT(username) DO UPDATE SET last_login_at=datetime('now'), "
                "  display_name=COALESCE(NULLIF(?,''), display_name), role=COALESCE(?, role)",
                (username, display_name, role, display_name, role),
            )

    def get_user(self, username: str) -> dict[str, Any] | None:
        with self.cursor() as cur:
            row = cur.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            return dict(row) if row else None

    # ---------- tasks ----------
    def create_task(
        self, *, username: str, source: str, skill_id: str | None = None,
        question: str = "", params: dict[str, Any] | None = None,
    ) -> str:
        task_id = "t_" + uuid.uuid4().hex[:10]
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks(id, username, source, skill_id, question, params, status) "
                "VALUES(?,?,?,?,?,?,?)",
                (task_id, username, source, skill_id, question,
                 json.dumps(params or {}, ensure_ascii=False), "running"),
            )
        return task_id

    def finish_task(
        self, task_id: str, *, status: str, error: str | None = None,
        row_count: int = 0, latency_ms: int = 0,
        llm_model: str = "", llm_input_tokens: int = 0, llm_output_tokens: int = 0,
    ) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE tasks SET status=?, error=?, row_count=?, latency_ms=?, "
                "  llm_model=?, llm_input_tokens=?, llm_output_tokens=?, finished_at=datetime('now') "
                "WHERE id=?",
                (status, error, row_count, latency_ms, llm_model,
                 llm_input_tokens, llm_output_tokens, task_id),
            )

    def add_task_file(
        self, task_id: str, *, filename: str, path: str,
        size_bytes: int, preview: list[dict[str, Any]] | None = None,
    ) -> str:
        file_id = "f_" + uuid.uuid4().hex[:10]
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO task_files(id, task_id, filename, path, size_bytes, preview_json) "
                "VALUES(?,?,?,?,?,?)",
                (file_id, task_id, filename, path, size_bytes,
                 json.dumps(preview or [], ensure_ascii=False, default=str)),
            )
        return file_id

    def list_tasks(self, username: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            rows = cur.execute(
                "SELECT t.*, f.filename, f.path AS file_path, f.size_bytes AS file_size "
                "FROM tasks t "
                "LEFT JOIN task_files f ON f.task_id=t.id "
                "WHERE t.username=? ORDER BY t.created_at DESC LIMIT ?",
                (username, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.cursor() as cur:
            row = cur.execute(
                "SELECT t.*, f.filename, f.path AS file_path, f.size_bytes AS file_size, "
                "  f.preview_json "
                "FROM tasks t "
                "LEFT JOIN task_files f ON f.task_id=t.id WHERE t.id=?",
                (task_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("preview_json"):
                try:
                    d["preview"] = json.loads(d.pop("preview_json"))
                except Exception:
                    d["preview"] = []
            return d

    # ---------- audit ----------
    def write_audit(self, **fields: Any) -> None:
        cols = [
            "username", "action", "task_id", "question", "service",
            "odata_url", "row_count", "latency_ms", "llm_model", "llm_tokens", "ip",
        ]
        values = [fields.get(c) for c in cols]
        placeholders = ",".join(["?"] * len(cols))
        with self.cursor() as cur:
            cur.execute(
                f"INSERT INTO audit_log({','.join(cols)}) VALUES({placeholders})",
                values,
            )

    def list_audit(self, *, username: str | None = None, action: str | None = None,
                   limit: int = 200) -> list[dict[str, Any]]:
        clauses, params = [], []
        if username:
            clauses.append("username=?")
            params.append(username)
        if action:
            clauses.append("action=?")
            params.append(action)
        sql = "SELECT * FROM audit_log"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.cursor() as cur:
            return [dict(r) for r in cur.execute(sql, params).fetchall()]

    # ---------- sensitive fields ----------
    def list_sensitive_fields(self) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            return [dict(r) for r in cur.execute(
                "SELECT * FROM sensitive_fields ORDER BY service, field").fetchall()]

    def upsert_sensitive_field(self, *, service: str, field: str, mask_mode: str, added_by: str = "") -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO sensitive_fields(service, field, mask_mode, added_by) "
                "VALUES(?,?,?,?) "
                "ON CONFLICT(service, field) DO UPDATE SET mask_mode=excluded.mask_mode, "
                "  added_by=excluded.added_by",
                (service, field, mask_mode, added_by),
            )

    def delete_sensitive_field(self, service: str, field: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "DELETE FROM sensitive_fields WHERE service=? AND field=?",
                (service, field),
            )

    # ---------- llm api keys (每用户独立) ----------
    def upsert_user_api_key(self, username: str, env_var: str, value: str) -> None:
        """保存某用户某 provider 的 key (base64 混淆)。"""
        import base64
        b64 = base64.b64encode(value.encode("utf-8")).decode("ascii")
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO llm_api_keys(username, env_var, value_b64, updated_at) "
                "VALUES(?,?,?, datetime('now')) "
                "ON CONFLICT(username, env_var) DO UPDATE SET value_b64=excluded.value_b64, "
                "  updated_at=datetime('now')",
                (username, env_var, b64),
            )

    def get_user_api_key(self, username: str, env_var: str) -> str | None:
        import base64
        with self.cursor() as cur:
            row = cur.execute(
                "SELECT value_b64 FROM llm_api_keys WHERE username=? AND env_var=?",
                (username, env_var),
            ).fetchone()
            if not row:
                return None
            try:
                return base64.b64decode(row["value_b64"]).decode("utf-8")
            except Exception:
                return None

    def list_user_api_keys_meta(self, username: str) -> list[dict[str, Any]]:
        """只回元信息,不回真值。"""
        import base64
        out: list[dict[str, Any]] = []
        with self.cursor() as cur:
            for row in cur.execute(
                "SELECT env_var, value_b64, updated_at FROM llm_api_keys WHERE username=?",
                (username,),
            ).fetchall():
                try:
                    decoded = base64.b64decode(row["value_b64"]).decode("utf-8")
                    tail = decoded[-4:] if len(decoded) >= 4 else "****"
                except Exception:
                    tail = "????"
                out.append({
                    "env_var": row["env_var"],
                    "tail": tail,
                    "updated_at": row["updated_at"],
                })
        return out

    def delete_user_api_key(self, username: str, env_var: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "DELETE FROM llm_api_keys WHERE username=? AND env_var=?",
                (username, env_var),
            )
