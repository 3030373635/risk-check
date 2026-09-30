"""基于 SQLite 的 Web 审核任务仓储。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from risk_audit_web.task_contracts import (
    SCHEMA_VERSION,
    TASK_STATUSES,
    TaskEvent,
    TaskRecord,
    TaskRequest,
    TaskState,
)


DATABASE_VERSION = 1
BUSY_TIMEOUT_MILLISECONDS = 10_000
TERMINAL_STATUSES = frozenset({"completed", "partial", "failed", "cancelled", "interrupted"})
ACTIVE_STATUSES = TASK_STATUSES - TERMINAL_STATUSES
STATE_COLUMNS = frozenset({
    "status",
    "stage",
    "completed_units",
    "total_units",
    "progress_percent",
    "current_entity",
    "current_business",
    "current_file",
    "worker_pid",
    "started_at",
    "heartbeat_at",
    "message",
    "error_code",
    "result_summary",
})


class TaskDatabaseError(RuntimeError):
    """表示任务数据库不可用、损坏或结构不受支持。"""


@dataclass(frozen=True)
class StoredTask:
    """保存同一数据库行还原出的任务请求、状态和取消标志。"""

    record: TaskRecord
    request: TaskRequest
    state: TaskState
    cancel_requested: bool


@dataclass(frozen=True)
class TaskQueryResult:
    """保存可用任务记录以及被隔离的数据警告。"""

    tasks: list[TaskRecord]
    warnings: list[str]


@dataclass(frozen=True)
class RecoveryResult:
    """保存重启恢复后的任务以及单任务隔离警告。"""

    tasks: list[StoredTask]
    warnings: list[str]


class TaskRepository:
    """使用独立短连接管理 SQLite 中的全部任务数据。"""

    def __init__(self, database_path: Path) -> None:
        """初始化仓储对象；database_path 为 SQLite 数据库文件路径。"""
        self.database_path = database_path.resolve(strict=False)

    def _connect(self) -> sqlite3.Connection:
        """创建配置完成的独立连接；无参数，返回 SQLite 连接。"""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.database_path,
                timeout=BUSY_TIMEOUT_MILLISECONDS / 1000,
            )
            connection.row_factory = sqlite3.Row
            # 每个进程和线程都会创建自己的连接，因此连接级参数必须逐次设置。
            connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MILLISECONDS}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            return connection
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            raise TaskDatabaseError(f"任务数据库无法打开：{error}") from error

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """提供自动提交、回滚并关闭的短连接；无参数。"""
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            # sqlite3.Connection 的上下文协议不会关闭连接，必须显式释放文件锁。
            connection.close()

    def initialize(self) -> None:
        """初始化并验证数据库结构；无参数。"""
        try:
            with self._connection() as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise TaskDatabaseError(f"任务数据库完整性检查失败：{integrity}")
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                tables = self._user_tables(connection)
                if version == 0 and not tables:
                    self._create_schema(connection)
                elif version != DATABASE_VERSION:
                    raise TaskDatabaseError(
                        f"不支持的任务数据库版本：{version}，当前版本：{DATABASE_VERSION}"
                    )
                self._validate_schema(connection)
        except TaskDatabaseError:
            raise
        except sqlite3.Error as error:
            raise TaskDatabaseError(f"任务数据库初始化失败：{error}") from error

    @staticmethod
    def _user_tables(connection: sqlite3.Connection) -> set[str]:
        """读取用户表名；connection 为当前数据库连接。"""
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        return {str(row[0]) for row in rows}

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        """创建第一版表和索引；connection 为已打开的数据库连接。"""
        statuses = ", ".join(f"'{status}'" for status in sorted(TASK_STATUSES))
        # 请求、状态和取消标志同处一行，可通过字段级 UPDATE 避免并发覆盖。
        connection.executescript(
            f"""
            BEGIN IMMEDIATE;
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                display_name TEXT NOT NULL,
                input_root TEXT NOT NULL,
                output_root TEXT NOT NULL UNIQUE,
                rulepack TEXT NOT NULL,
                entity_file TEXT NOT NULL,
                baseline_root TEXT NOT NULL,
                config_file TEXT,
                soffice_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ({statuses})),
                stage TEXT NOT NULL,
                completed_units INTEGER NOT NULL CHECK (completed_units >= 0),
                total_units INTEGER CHECK (total_units IS NULL OR total_units >= 0),
                progress_percent INTEGER CHECK (
                    progress_percent IS NULL OR progress_percent BETWEEN 0 AND 100
                ),
                current_entity TEXT,
                current_business TEXT,
                current_file TEXT,
                worker_pid INTEGER,
                started_at TEXT,
                heartbeat_at TEXT NOT NULL,
                message TEXT NOT NULL,
                error_code TEXT,
                result_summary_json TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1))
            );
            CREATE TABLE task_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                event TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                details_json TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            );
            CREATE INDEX idx_tasks_created_at ON tasks(created_at DESC);
            CREATE INDEX idx_task_events_task_id ON task_events(task_id, event_id);
            PRAGMA user_version = {DATABASE_VERSION};
            COMMIT;
            """
        )

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        """验证必需表列；connection 为当前数据库连接。"""
        required_columns = {
            "tasks": {
                "task_id", "schema_version", "display_name", "input_root", "output_root",
                "rulepack", "entity_file", "baseline_root", "config_file", "soffice_path",
                "created_at", "status", "stage", "completed_units", "total_units",
                "progress_percent", "current_entity", "current_business", "current_file",
                "worker_pid", "started_at", "heartbeat_at", "message", "error_code",
                "result_summary_json", "cancel_requested",
            },
            "task_events": {
                "event_id", "task_id", "schema_version", "event", "occurred_at", "details_json",
            },
        }
        for table_name, expected in required_columns.items():
            columns = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table_name})")
            }
            missing = expected - columns
            if missing:
                raise TaskDatabaseError(
                    f"任务数据库结构缺少字段 {table_name}：{', '.join(sorted(missing))}"
                )
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        missing_indexes = {
            "idx_tasks_created_at",
            "idx_task_events_task_id",
        } - indexes
        if missing_indexes:
            raise TaskDatabaseError(
                f"任务数据库结构缺少索引：{', '.join(sorted(missing_indexes))}"
            )

    def create_task(
        self,
        request: TaskRequest,
        state: TaskState,
        event: TaskEvent,
    ) -> TaskRecord:
        """原子创建任务及首个事件；参数依次为请求、初始状态和创建事件。"""
        if request.task_id != state.task_id or request.task_id != event.task_id:
            raise ValueError("任务请求、状态和事件的 task_id 必须一致")
        summary_json = self._serialize_summary(state.result_summary)
        details_json = json.dumps(event.details, ensure_ascii=False, sort_keys=True)
        values = (
            request.task_id, request.schema_version, request.display_name, request.input_root,
            request.output_root, request.rulepack, request.entity_file, request.baseline_root,
            request.config_file, request.soffice_path, request.created_at, state.status,
            state.stage, state.completed_units, state.total_units, state.progress_percent,
            state.current_entity, state.current_business, state.current_file, state.worker_pid,
            state.started_at, state.heartbeat_at, state.message, state.error_code, summary_json,
        )
        try:
            with self._connection() as connection:
                # 两条 INSERT 处于同一事务，任何一步失败都不会留下无事件任务。
                connection.execute(
                    """
                    INSERT INTO tasks (
                        task_id, schema_version, display_name, input_root, output_root, rulepack,
                        entity_file, baseline_root, config_file, soffice_path, created_at, status,
                        stage, completed_units, total_units, progress_percent, current_entity,
                        current_business, current_file, worker_pid, started_at, heartbeat_at,
                        message, error_code, result_summary_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                connection.execute(
                    """
                    INSERT INTO task_events (task_id, schema_version, event, occurred_at, details_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (event.task_id, event.schema_version, event.event, event.occurred_at, details_json),
                )
        except sqlite3.Error as error:
            raise TaskDatabaseError(f"任务创建失败：{error}") from error
        return self._record_from_request(request)

    def get_task(self, task_id: str) -> StoredTask | None:
        """按编号读取完整任务；task_id 为任务编号，不存在时返回 None。"""
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise TaskDatabaseError(f"任务 {task_id} 读取失败：{error}") from error
        if row is None:
            return None
        try:
            return self._stored_from_row(row)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise TaskDatabaseError(f"任务 {task_id} 数据损坏：{error}") from error

    def list_tasks(self) -> TaskQueryResult:
        """按创建时间倒序读取全部有效记录；无参数。"""
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC, task_id DESC"
                ).fetchall()
        except sqlite3.Error as error:
            raise TaskDatabaseError(f"任务列表读取失败：{error}") from error
        tasks: list[TaskRecord] = []
        warnings: list[str] = []
        for row in rows:
            try:
                stored = self._stored_from_row(row)
                tasks.append(stored.record)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                warnings.append(f"任务 {row['task_id']} 已隔离：{error}")
        return TaskQueryResult(tasks, warnings)

    def output_root_exists(self, output_root: Path) -> bool:
        """判断输出路径是否已登记；output_root 为待检查的绝对目录。"""
        normalized = str(output_root.resolve(strict=False))
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT 1 FROM tasks WHERE output_root = ? LIMIT 1",
                    (normalized,),
                ).fetchone()
        except sqlite3.Error as error:
            raise TaskDatabaseError(f"任务输出路径检查失败：{error}") from error
        return row is not None

    def read_request(self, task_id: str) -> TaskRequest:
        """读取任务请求；task_id 为任务编号。"""
        stored = self.get_task(task_id)
        if stored is None:
            raise KeyError(f"任务不存在：{task_id}")
        return stored.request

    def read_state(self, task_id: str) -> TaskState:
        """读取任务状态；task_id 为任务编号。"""
        stored = self.get_task(task_id)
        if stored is None:
            raise KeyError(f"任务不存在：{task_id}")
        return stored.state

    def update_task_state(self, task_id: str, changes: Mapping[str, Any]) -> TaskState:
        """字段级更新任务状态；task_id 为任务编号，changes 为待更新字段。"""
        unknown = set(changes) - STATE_COLUMNS
        if unknown:
            raise ValueError(f"不允许更新任务字段：{', '.join(sorted(unknown))}")
        if not changes:
            return self.read_state(task_id)
        normalized = dict(changes)
        if "status" in normalized and normalized["status"] not in TASK_STATUSES:
            raise ValueError(f"未知任务状态：{normalized['status']}")
        if "progress_percent" in normalized:
            progress = normalized["progress_percent"]
            if progress is not None and (type(progress) is not int or not 0 <= progress <= 100):
                raise ValueError(f"任务进度必须位于 0 到 100：{progress}")
        if "result_summary" in normalized:
            normalized["result_summary_json"] = self._serialize_summary(
                normalized.pop("result_summary")
            )
        assignments = ", ".join(f"{column} = ?" for column in normalized)
        values = [*normalized.values(), task_id]
        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    f"UPDATE tasks SET {assignments} WHERE task_id = ?",
                    values,
                )
                if cursor.rowcount != 1:
                    raise KeyError(f"任务不存在：{task_id}")
        except KeyError:
            raise
        except sqlite3.Error as error:
            raise TaskDatabaseError(f"任务 {task_id} 状态更新失败：{error}") from error
        return self.read_state(task_id)

    def append_event(self, event: TaskEvent) -> None:
        """追加任务事件；event 为事件对象。"""
        details_json = json.dumps(event.details, ensure_ascii=False, sort_keys=True)
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO task_events (task_id, schema_version, event, occurred_at, details_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (event.task_id, event.schema_version, event.event, event.occurred_at, details_json),
                )
        except sqlite3.Error as error:
            raise TaskDatabaseError(f"任务 {event.task_id} 事件写入失败：{error}") from error

    def list_events(self, task_id: str) -> list[TaskEvent]:
        """按写入顺序读取任务事件；task_id 为任务编号。"""
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT * FROM task_events WHERE task_id = ? ORDER BY event_id",
                    (task_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise TaskDatabaseError(f"任务 {task_id} 事件读取失败：{error}") from error
        return [
            TaskEvent(
                schema_version=row["schema_version"],
                task_id=row["task_id"],
                event=row["event"],
                occurred_at=row["occurred_at"],
                details=json.loads(row["details_json"]),
            )
            for row in rows
        ]

    def request_cancel(self, task_id: str, occurred_at: str) -> bool:
        """原子请求取消活动任务；task_id 为任务编号，occurred_at 为事件时间。"""
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT status, cancel_requested FROM tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"任务不存在：{task_id}")
                if row["status"] not in ACTIVE_STATUSES or bool(row["cancel_requested"]):
                    connection.commit()
                    return False
                connection.execute(
                    "UPDATE tasks SET cancel_requested = 1 WHERE task_id = ?",
                    (task_id,),
                )
                connection.execute(
                    """
                    INSERT INTO task_events (task_id, schema_version, event, occurred_at, details_json)
                    VALUES (?, ?, 'cancel_requested', ?, '{}')
                    """,
                    (task_id, SCHEMA_VERSION, occurred_at),
                )
                connection.commit()
                return True
        except KeyError:
            raise
        except sqlite3.Error as error:
            raise TaskDatabaseError(f"任务 {task_id} 取消请求失败：{error}") from error

    def is_cancel_requested(self, task_id: str) -> bool:
        """读取取消标志；task_id 为任务编号。"""
        stored = self.get_task(task_id)
        if stored is None:
            raise KeyError(f"任务不存在：{task_id}")
        return stored.cancel_requested

    def mark_interrupted(self, task_id: str, message: str) -> TaskState:
        """将任务标记为中断；task_id 为任务编号，message 为用户提示。"""
        return self.update_task_state(
            task_id,
            {
                "status": "interrupted",
                "stage": "interrupted",
                "message": message,
                "worker_pid": None,
            },
        )

    def recover_tasks(
        self,
        *,
        is_process_alive: Callable[[int], bool],
        now: datetime | None = None,
        heartbeat_timeout_seconds: int = 30,
    ) -> RecoveryResult:
        """恢复任务；参数为 PID 探测器、当前时间和心跳超时秒数。"""
        current_time = now or datetime.now().astimezone()
        query = self.list_tasks()
        recovered: list[StoredTask] = []
        warnings = list(query.warnings)
        for record in query.tasks:
            try:
                stored = self.get_task(record.task_id)
                if stored is None:
                    continue
                state = stored.state
                if state.status not in TERMINAL_STATUSES:
                    try:
                        heartbeat_age = (
                            current_time - datetime.fromisoformat(state.heartbeat_at)
                        ).total_seconds()
                    except (TypeError, ValueError):
                        heartbeat_age = heartbeat_timeout_seconds + 1
                    live = state.worker_pid is not None and is_process_alive(state.worker_pid)
                    if not live or heartbeat_age > heartbeat_timeout_seconds:
                        self.mark_interrupted(
                            record.task_id,
                            "任务进程已中断，可重新创建任务",
                        )
                        stored = self.get_task(record.task_id)
                if stored is not None:
                    recovered.append(stored)
            except (KeyError, TaskDatabaseError, TypeError, ValueError) as error:
                warnings.append(f"任务 {record.task_id} 恢复失败：{error}")
        return RecoveryResult(recovered, warnings)

    @staticmethod
    def _serialize_summary(value: dict[str, Any] | None) -> str | None:
        """序列化结果摘要；value 必须是字典或 None。"""
        if value is not None and not isinstance(value, dict):
            raise ValueError("任务结果摘要必须是对象或空值")
        return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _record_from_request(request: TaskRequest) -> TaskRecord:
        """由请求生成公开索引记录；request 为任务请求。"""
        return TaskRecord(
            schema_version=request.schema_version,
            task_id=request.task_id,
            display_name=request.display_name,
            input_root=request.input_root,
            output_root=request.output_root,
            created_at=request.created_at,
        )

    @classmethod
    def _stored_from_row(cls, row: sqlite3.Row) -> StoredTask:
        """由数据库行还原完整任务；row 为 SQLite 行。"""
        summary = None
        if row["result_summary_json"] is not None:
            summary = json.loads(row["result_summary_json"])
            if not isinstance(summary, dict):
                raise ValueError("result_summary_json 必须是对象")
        request = TaskRequest(
            schema_version=row["schema_version"],
            task_id=row["task_id"],
            display_name=row["display_name"],
            input_root=row["input_root"],
            output_root=row["output_root"],
            rulepack=row["rulepack"],
            entity_file=row["entity_file"],
            baseline_root=row["baseline_root"],
            config_file=row["config_file"],
            soffice_path=row["soffice_path"],
            created_at=row["created_at"],
        )
        state = TaskState(
            schema_version=row["schema_version"],
            task_id=row["task_id"],
            status=row["status"],
            stage=row["stage"],
            completed_units=row["completed_units"],
            total_units=row["total_units"],
            progress_percent=row["progress_percent"],
            current_entity=row["current_entity"],
            current_business=row["current_business"],
            current_file=row["current_file"],
            worker_pid=row["worker_pid"],
            started_at=row["started_at"],
            heartbeat_at=row["heartbeat_at"],
            message=row["message"],
            error_code=row["error_code"],
            result_summary=summary,
        )
        # 复用契约校验，避免手工修改数据库后把非法状态暴露给上层。
        request = TaskRequest.from_dict(request.to_dict())
        state = TaskState.from_dict(state.to_dict())
        return StoredTask(
            record=cls._record_from_request(request),
            request=request,
            state=state,
            cancel_requested=bool(row["cancel_requested"]),
        )
