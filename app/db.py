"""SQLite 持久化层 (§13 数据模型)。

启动期建表;表结构与方案 §13 对齐。

不依赖 ORM,直接用 sqlite3 (M0-M5 足够)。后期切 PostgreSQL 时再上 SQLAlchemy。
"""
from __future__ import annotations

import base64
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
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
  parent_task_id      TEXT,
  title               TEXT                       -- 会话标题(可重命名;空则用首条问题)
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

-- 每用户一组 LLM 设置三元组 (对标 DataAgent BYOK): key + base_url + model,
-- 指向任意 OpenAI 兼容端点。任一字段留空则回退 .env 默认。key base64 混淆暂存。
CREATE TABLE IF NOT EXISTS user_llm_settings (
  username    TEXT PRIMARY KEY,
  api_key_b64 TEXT,                          -- base64 混淆后的 key (NULL=清空,用 .env 兜底)
  base_url    TEXT,                          -- OpenAI 兼容端点 (NULL/空=用 .env)
  model       TEXT,                          -- 模型名 (NULL/空=用 .env)
  updated_at  TEXT DEFAULT (datetime('now'))
);

-- 自由对话/多轮的消息历史 (一个 task = 一段会话)
CREATE TABLE IF NOT EXISTS task_messages (
  id           TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL,
  role         TEXT NOT NULL,                -- user | assistant | system
  text         TEXT,
  blocks_json  TEXT,                          -- 工具调用 / 表格等结构化块
  created_at   TEXT DEFAULT (datetime('now')),
  FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_taskmsg_task_time ON task_messages(task_id, created_at);

-- 收藏 (F7)
CREATE TABLE IF NOT EXISTS favorites (
  username    TEXT NOT NULL,
  kind        TEXT NOT NULL,                 -- skill | task
  ref_id      TEXT NOT NULL,
  created_at  TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (username, kind, ref_id)
);

-- 加密 BW 凭据 (§14 二期; v0.3 用 AES-256-GCM,密钥从 BW_CRED_KEY env 取)
CREATE TABLE IF NOT EXISTS bw_creds (
  cred_id     TEXT PRIMARY KEY,
  username    TEXT NOT NULL,
  ciphertext  BLOB NOT NULL,
  nonce       BLOB NOT NULL,
  expires_at  TEXT NOT NULL,
  created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bwcreds_user ON bw_creds(username);

-- Skill 生命周期 (§9.5 draft/active/deprecated/archived)
CREATE TABLE IF NOT EXISTS skill_status (
  skill_id    TEXT NOT NULL,
  version     INTEGER NOT NULL,
  status      TEXT NOT NULL DEFAULT 'active',  -- draft | active | deprecated | archived
  changed_at  TEXT DEFAULT (datetime('now')),
  changed_by  TEXT,
  PRIMARY KEY (skill_id, version)
);

-- Per-user LLM token 配额 (§7.8 风险缓解)
CREATE TABLE IF NOT EXISTS llm_quota (
  username       TEXT NOT NULL,
  month          TEXT NOT NULL,                -- YYYY-MM
  input_tokens   INTEGER NOT NULL DEFAULT 0,
  output_tokens  INTEGER NOT NULL DEFAULT 0,
  call_count     INTEGER NOT NULL DEFAULT 0,
  updated_at     TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (username, month)
);

CREATE TABLE IF NOT EXISTS llm_quota_limits (
  username       TEXT PRIMARY KEY,
  monthly_tokens INTEGER,                       -- NULL = 无限
  set_by         TEXT,
  updated_at     TEXT DEFAULT (datetime('now'))
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
            # 迁移:老库的 tasks 补 title 列(会话重命名用;CREATE IF NOT EXISTS 不会自动加列)。
            tcols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
            if tcols and "title" not in tcols:
                conn.execute("ALTER TABLE tasks ADD COLUMN title TEXT")
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

    def list_chat_sessions(self, username: str, limit: int = 100) -> list[dict[str, Any]]:
        """列出用户的"自由对话"会话(每个 chat task 一行,不连 task_files,避免重复)。"""
        with self.cursor() as cur:
            rows = cur.execute(
                "SELECT id, title, question, status, created_at, finished_at "
                "FROM tasks WHERE username=? AND source='chat' "
                "ORDER BY created_at DESC LIMIT ?",
                (username, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def set_task_title(self, task_id: str, title: str) -> None:
        with self.cursor() as cur:
            cur.execute("UPDATE tasks SET title=? WHERE id=?", (title, task_id))

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

    # ---------- 每用户 LLM 设置三元组 (DataAgent 式 BYOK) ----------
    def get_user_llm_settings(self, username: str) -> dict[str, Any] | None:
        """返回 {api_key, base_url, model, updated_at};无记录返回 None。
        api_key 已解码(可能为 None=未设私有 key)。"""
        with self.cursor() as cur:
            row = cur.execute(
                "SELECT api_key_b64, base_url, model, updated_at "
                "FROM user_llm_settings WHERE username=?",
                (username,),
            ).fetchone()
        if not row:
            return None
        api_key: str | None = None
        if row["api_key_b64"]:
            try:
                api_key = base64.b64decode(row["api_key_b64"]).decode("utf-8")
            except Exception:                                  # noqa: BLE001
                api_key = None
        return {
            "api_key": api_key,
            "base_url": row["base_url"] or "",
            "model": row["model"] or "",
            "updated_at": row["updated_at"],
        }

    def set_user_llm_settings(
        self,
        username: str,
        *,
        api_key: str | None = None,        # None=保持原值;""=清空;非空=更新
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        cur_row = self.get_user_llm_settings(username) or {}
        # api_key: None 保持原值,空串清空,非空更新
        if api_key is None:
            new_key = cur_row.get("api_key")
        else:
            new_key = api_key.strip() or None
        new_b64 = base64.b64encode(new_key.encode("utf-8")).decode("ascii") if new_key else None
        new_base = (base_url if base_url is not None else cur_row.get("base_url", "")) or None
        new_model = (model if model is not None else cur_row.get("model", "")) or None
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO user_llm_settings(username, api_key_b64, base_url, model, updated_at) "
                "VALUES(?,?,?,?,datetime('now')) "
                "ON CONFLICT(username) DO UPDATE SET "
                "  api_key_b64=excluded.api_key_b64, base_url=excluded.base_url, "
                "  model=excluded.model, updated_at=excluded.updated_at",
                (username, new_b64, new_base, new_model),
            )

    # ---------- task messages (自由对话多轮) ----------
    def add_task_message(
        self, task_id: str, *, role: str, text: str = "",
        blocks: Any | None = None,
    ) -> str:
        msg_id = "m_" + uuid.uuid4().hex[:10]
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO task_messages(id, task_id, role, text, blocks_json) "
                "VALUES(?,?,?,?,?)",
                (msg_id, task_id, role, text,
                 json.dumps(blocks, ensure_ascii=False, default=str) if blocks is not None else None),
            )
        return msg_id

    def list_task_messages(self, task_id: str) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM task_messages WHERE task_id=? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if d.get("blocks_json"):
                try:
                    d["blocks"] = json.loads(d.pop("blocks_json"))
                except Exception:
                    d["blocks"] = None
            else:
                d.pop("blocks_json", None)
                d["blocks"] = None
            out.append(d)
        return out

    # ---------- favorites ----------
    def add_favorite(self, username: str, kind: str, ref_id: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO favorites(username, kind, ref_id) VALUES(?,?,?)",
                (username, kind, ref_id),
            )

    def remove_favorite(self, username: str, kind: str, ref_id: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "DELETE FROM favorites WHERE username=? AND kind=? AND ref_id=?",
                (username, kind, ref_id),
            )

    def list_favorites(self, username: str, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM favorites WHERE username=?"
        params: list[Any] = [username]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY created_at DESC"
        with self.cursor() as cur:
            return [dict(r) for r in cur.execute(sql, params).fetchall()]

    def is_favorite(self, username: str, kind: str, ref_id: str) -> bool:
        with self.cursor() as cur:
            row = cur.execute(
                "SELECT 1 FROM favorites WHERE username=? AND kind=? AND ref_id=?",
                (username, kind, ref_id),
            ).fetchone()
            return bool(row)

    # ---------- BW creds (AES-256-GCM, §14) ----------
    def save_bw_cred(
        self, *, username: str, ciphertext: bytes, nonce: bytes, ttl_seconds: int,
    ) -> str:
        cred_id = "c_" + uuid.uuid4().hex[:16]
        expires = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%d %H:%M:%S")
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO bw_creds(cred_id, username, ciphertext, nonce, expires_at) "
                "VALUES(?,?,?,?,?)",
                (cred_id, username, ciphertext, nonce, expires),
            )
        return cred_id

    def get_bw_cred(self, cred_id: str) -> dict[str, Any] | None:
        with self.cursor() as cur:
            row = cur.execute(
                "SELECT cred_id, username, ciphertext, nonce, expires_at FROM bw_creds "
                "WHERE cred_id=? AND expires_at > datetime('now')",
                (cred_id,),
            ).fetchone()
            return dict(row) if row else None

    def delete_bw_cred(self, cred_id: str) -> None:
        with self.cursor() as cur:
            cur.execute("DELETE FROM bw_creds WHERE cred_id=?", (cred_id,))

    def cleanup_expired_bw_creds(self) -> int:
        with self.cursor() as cur:
            cur.execute("DELETE FROM bw_creds WHERE expires_at <= datetime('now')")
            return cur.rowcount

    # ---------- Skill status ----------
    def set_skill_status(
        self, skill_id: str, version: int, status: str, changed_by: str = "",
    ) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO skill_status(skill_id, version, status, changed_at, changed_by) "
                "VALUES(?,?,?,datetime('now'),?) "
                "ON CONFLICT(skill_id, version) DO UPDATE SET status=excluded.status, "
                "  changed_at=datetime('now'), changed_by=excluded.changed_by",
                (skill_id, version, status, changed_by),
            )

    def get_skill_status(self, skill_id: str, version: int) -> str:
        with self.cursor() as cur:
            row = cur.execute(
                "SELECT status FROM skill_status WHERE skill_id=? AND version=?",
                (skill_id, version),
            ).fetchone()
            return row["status"] if row else "active"

    def list_skill_statuses(self) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            return [dict(r) for r in cur.execute(
                "SELECT * FROM skill_status ORDER BY skill_id, version DESC").fetchall()]

    # ---------- LLM quota ----------
    def get_user_quota_usage(self, username: str, month: str) -> dict[str, int]:
        with self.cursor() as cur:
            row = cur.execute(
                "SELECT input_tokens, output_tokens, call_count "
                "FROM llm_quota WHERE username=? AND month=?",
                (username, month),
            ).fetchone()
        if not row:
            return {"input_tokens": 0, "output_tokens": 0, "call_count": 0}
        return dict(row)

    def add_user_quota_usage(
        self, username: str, month: str, *,
        input_tokens: int = 0, output_tokens: int = 0,
    ) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO llm_quota(username, month, input_tokens, output_tokens, call_count, updated_at) "
                "VALUES(?,?,?,?,1, datetime('now')) "
                "ON CONFLICT(username, month) DO UPDATE SET "
                "  input_tokens=input_tokens + excluded.input_tokens, "
                "  output_tokens=output_tokens + excluded.output_tokens, "
                "  call_count=call_count + 1, "
                "  updated_at=datetime('now')",
                (username, month, input_tokens, output_tokens),
            )

    def get_user_quota_limit(self, username: str) -> int | None:
        with self.cursor() as cur:
            row = cur.execute(
                "SELECT monthly_tokens FROM llm_quota_limits WHERE username=?",
                (username,),
            ).fetchone()
            return row["monthly_tokens"] if row else None

    def set_user_quota_limit(
        self, username: str, monthly_tokens: int | None, set_by: str = "",
    ) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO llm_quota_limits(username, monthly_tokens, set_by, updated_at) "
                "VALUES(?,?,?, datetime('now')) "
                "ON CONFLICT(username) DO UPDATE SET monthly_tokens=excluded.monthly_tokens, "
                "  set_by=excluded.set_by, updated_at=datetime('now')",
                (username, monthly_tokens, set_by),
            )

    def list_quota_status(self) -> list[dict[str, Any]]:
        """All users' current-month usage + their limit (for admin)."""
        month = datetime.now(UTC).strftime("%Y-%m")
        with self.cursor() as cur:
            rows = cur.execute(
                "SELECT q.username, q.input_tokens, q.output_tokens, q.call_count, "
                "       l.monthly_tokens AS limit_tokens "
                "FROM llm_quota q LEFT JOIN llm_quota_limits l ON l.username=q.username "
                "WHERE q.month=? ORDER BY (q.input_tokens + q.output_tokens) DESC",
                (month,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_task(self, task_id: str, username: str) -> bool:
        """Delete a task (and cascade messages/files) — only the owning user can delete."""
        file_paths: list[str] = []
        with self.cursor() as cur:
            rows = cur.execute(
                "SELECT f.path FROM task_files f "
                "JOIN tasks t ON t.id=f.task_id "
                "WHERE t.id=? AND t.username=?",
                (task_id, username),
            ).fetchall()
            file_paths = [r["path"] for r in rows if r["path"]]
            cur.execute(
                "DELETE FROM tasks WHERE id=? AND username=?",
                (task_id, username),
            )
            deleted = cur.rowcount > 0
        if deleted:
            for raw_path in file_paths:
                try:
                    Path(raw_path).unlink(missing_ok=True)
                except OSError:
                    continue
        return deleted
