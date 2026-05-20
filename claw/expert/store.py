"""专家模板存储：SQLite 持久化 + EXPERT.md 导入/导出。

职责：
1. Expert 对象与 SQLite experts 表互转
2. EXPERT.md（YAML frontmatter + Markdown body）解析与生成
3. bundled 专家（包内只读）和 local 专家统一存入 SQLite
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from claw.expert.types import Expert, ExpertMeta

logger = logging.getLogger(__name__)

_EXPERT_FILE = "EXPERT.md"

# 包内 bundled 专家目录
_PKG_BUNDLED_DIR = Path(__file__).parent / "bundled"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteExpertStore:
    """SQLite 持久化专家存储。

    bundled 专家随包分发，首次 init_bundled 时从 EXPERT.md 导入到 SQLite。
    local 专家通过 install_from_file 写入 SQLite。
    查询统一走 SQLite，不再区分来源目录。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---- EXPERT.md 解析 ----

    @staticmethod
    def parse_expert_md(content: str, source: str = "local", path: str | None = None) -> Expert:
        """解析 EXPERT.md 内容（YAML frontmatter + Markdown body）为 Expert 对象。"""
        if not content.startswith("---"):
            raise ValueError("EXPERT.md 必须以 YAML frontmatter 开头")

        parts = content.split("---", 2)
        if len(parts) < 3:
            raise ValueError("EXPERT.md frontmatter 格式错误")

        try:
            data = yaml.safe_load(parts[1])
        except yaml.YAMLError as e:
            raise ValueError(f"YAML 解析失败: {e}") from e

        if not isinstance(data, dict):
            raise ValueError(f"frontmatter 格式错误: 期望字典，得到 {type(data).__name__}")

        name = data.get("name", "")
        if not name:
            raise ValueError("EXPERT.md 缺少 name 字段")
        if not Expert.is_valid_name(name):
            raise ValueError(f"专家名称不合法: '{name}'")

        body = parts[2].strip()
        if not body:
            raise ValueError(f"专家 '{name}' 缺少 system_prompt（Markdown body）")

        # 解析 meta
        meta_data = data.get("meta", {}) or {}
        meta = ExpertMeta(
            version=str(meta_data.get("version", "0.1.0")),
            author=str(meta_data.get("author", "")),
            tags=meta_data.get("tags", []) or [],
            category=str(meta_data.get("category", "")),
            avatar=str(meta_data.get("avatar", "")),
            extra=meta_data.get("extra", {}) or {},
        )

        return Expert(
            name=name,
            display_name=data.get("display_name", name),
            description=data.get("description", ""),
            system_prompt=body,
            default_skills=data.get("default_skills", []) or [],
            default_tools=data.get("default_tools", []) or [],
            default_mcp_servers=data.get("default_mcp_servers", []) or [],
            default_model=data.get("default_model", {}) or {},
            default_memory=data.get("default_memory", {}) or {},
            default_sandbox=data.get("default_sandbox", {}) or {},
            meta=meta,
            source=source,
            path=path,
        )

    @staticmethod
    def parse_expert_md_file(file_path: str | Path, source: str = "local") -> Expert:
        """从文件路径读取并解析 EXPERT.md。"""
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        return SqliteExpertStore.parse_expert_md(content, source=source, path=str(path))

    @staticmethod
    def expert_to_md(expert: Expert) -> str:
        """将 Expert 对象转为 EXPERT.md 格式字符串。"""
        data: dict[str, Any] = {
            "name": expert.name,
            "display_name": expert.display_name,
            "description": expert.description,
        }
        if expert.default_skills:
            data["default_skills"] = expert.default_skills
        if expert.default_tools:
            data["default_tools"] = expert.default_tools
        if expert.default_mcp_servers:
            data["default_mcp_servers"] = expert.default_mcp_servers
        if expert.default_model:
            data["default_model"] = expert.default_model
        if expert.default_memory:
            data["default_memory"] = expert.default_memory
        if expert.default_sandbox:
            data["default_sandbox"] = expert.default_sandbox
        data["meta"] = {
            "version": expert.meta.version,
            "author": expert.meta.author,
            "tags": expert.meta.tags,
            "category": expert.meta.category,
            "avatar": expert.meta.avatar,
        }
        if expert.meta.extra:
            data["meta"]["extra"] = expert.meta.extra

        frontmatter = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
        return f"---\n{frontmatter}---\n\n{expert.system_prompt}\n"

    # ---- SQLite CRUD ----

    def save(self, expert: Expert) -> None:
        """保存专家到 SQLite（INSERT OR REPLACE）。"""
        now = _now_iso()
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO experts (
                    name, display_name, description, system_prompt,
                    default_skills_json, default_tools_json, default_mcp_servers_json,
                    default_model_json, default_memory_json, default_sandbox_json,
                    meta_json, source, source_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    expert.name,
                    expert.display_name,
                    expert.description,
                    expert.system_prompt,
                    json.dumps(expert.default_skills, ensure_ascii=False),
                    json.dumps(expert.default_tools, ensure_ascii=False),
                    json.dumps(expert.default_mcp_servers, ensure_ascii=False),
                    json.dumps(expert.default_model, ensure_ascii=False),
                    json.dumps(expert.default_memory, ensure_ascii=False),
                    json.dumps(expert.default_sandbox, ensure_ascii=False),
                    json.dumps(self._meta_to_dict(expert.meta), ensure_ascii=False),
                    expert.source,
                    expert.path,
                    now,
                    now,
                ),
            )

    def get(self, name: str) -> Expert | None:
        """按名称获取专家，不存在返回 None。"""
        row = self._conn.execute(
            "SELECT * FROM experts WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_expert(row)

    def list_all(self) -> list[Expert]:
        """列出所有专家。"""
        rows = self._conn.execute(
            "SELECT * FROM experts ORDER BY name"
        ).fetchall()
        return [self._row_to_expert(r) for r in rows]

    def delete(self, name: str) -> bool:
        """删除专家，返回是否成功。"""
        cursor = self._conn.execute("DELETE FROM experts WHERE name = ?", (name,))
        self._conn.commit()
        return cursor.rowcount > 0

    def exists(self, name: str) -> bool:
        """检查专家是否存在。"""
        row = self._conn.execute(
            "SELECT 1 FROM experts WHERE name = ?", (name,)
        ).fetchone()
        return row is not None

    # ---- bundled 专家初始化 ----

    def init_bundled(self, bundled_dir: Path | None = None) -> list[Expert]:
        """扫描 bundled 目录，将内置专家导入 SQLite。

        已存在的 bundled 专家不会被覆盖（幂等）。
        返回本次新导入的专家列表。
        """
        bundled_path = bundled_dir or _PKG_BUNDLED_DIR
        if not bundled_path.exists():
            return []

        imported: list[Expert] = []
        for expert_file in sorted(bundled_path.glob(f"*/{_EXPERT_FILE}")):
            try:
                expert = self.parse_expert_md_file(expert_file, source="bundled")
                # 幂等：已存在则跳过
                if self.exists(expert.name):
                    continue
                self.save(expert)
                imported.append(expert)
                logger.info("导入 bundled 专家: %s", expert.name)
            except Exception as e:
                logger.warning("跳过无效 bundled 专家文件 %s: %s", expert_file, e)

        return imported

    # ---- 内部转换 ----

    @staticmethod
    def _meta_to_dict(meta: ExpertMeta) -> dict[str, Any]:
        return {
            "version": meta.version,
            "author": meta.author,
            "tags": meta.tags,
            "category": meta.category,
            "avatar": meta.avatar,
            "extra": meta.extra,
        }

    @staticmethod
    def _dict_to_meta(data: dict[str, Any]) -> ExpertMeta:
        return ExpertMeta(
            version=str(data.get("version", "0.1.0")),
            author=str(data.get("author", "")),
            tags=data.get("tags", []) or [],
            category=str(data.get("category", "")),
            avatar=str(data.get("avatar", "")),
            extra=data.get("extra", {}) or {},
        )

    def _row_to_expert(self, row: sqlite3.Row) -> Expert:
        """将 SQLite 行转为 Expert 对象。"""
        return Expert(
            name=row["name"],
            display_name=row["display_name"],
            description=row["description"],
            system_prompt=row["system_prompt"],
            default_skills=json.loads(row["default_skills_json"]),
            default_tools=json.loads(row["default_tools_json"]),
            default_mcp_servers=json.loads(row["default_mcp_servers_json"]),
            default_model=json.loads(row["default_model_json"]),
            default_memory=json.loads(row["default_memory_json"]),
            default_sandbox=json.loads(row["default_sandbox_json"]),
            meta=self._dict_to_meta(json.loads(row["meta_json"])),
            source=row["source"],
            path=row["source_path"],
        )
