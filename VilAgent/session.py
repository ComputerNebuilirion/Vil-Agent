"""会话管理：按 session_uuid 物理隔离对话历史

目录结构:
    sessions_dir/
        index.json              # 全部 session 元数据（list[dict]）
        {session_id}.jsonl      # 每个 session 一个对话历史文件

设计要点:
- 物理隔离（按文件分）而非"单文件 + session_id 字段过滤"
- 元数据单独存 index.json，list/show 不必扫所有 jsonl
- State 类不感知 session，仍只管"对一个 jsonl 文件读写"
"""
import json
import re
import time
import uuid
from pathlib import Path


class SessionManager:
    """会话生命周期管理（不持有 State，仅管元数据 + 文件路径）"""

    def __init__(self, sessions_dir: Path | str):
        self.dir = Path(sessions_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dir / "index.json"
        if not self.index_path.exists():
            self.index_path.write_text("[]", encoding="utf-8")
        # "当前会话"指针文件：记录最后一次使用的 session_id，
        # 用于重启后精确恢复（/switch 切换的会话 last_active_at 不变，
        # 靠时间排序会恢复错）
        self.current_path = self.dir / ".current"

    # —— index.json 读写 ——

    def _load_index(self) -> list[dict]:
        try:
            data = self.index_path.read_text(encoding="utf-8") or "[]"
            return json.loads(data)
        except (json.JSONDecodeError, OSError):
            return []

    def _save_index(self, items: list[dict]) -> None:
        self.index_path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8")

    # —— 生命周期 ——

    def create(self, mode: str, task: str) -> str:
        """新建 session，返回 session_id（12 位 hex，避免 uuid4 太长）"""
        session_id = uuid.uuid4().hex[:12]
        now = time.time()
        item = {
            "id": session_id,
            "mode": mode,
            "task": (task or "")[:200],
            "created_at": now,
            "last_active_at": now,
            "tokens_total": 0,
            "steps_total": 0,
            "status": "active",
            "summary": "",
        }
        items = self._load_index()
        items.append(item)
        self._save_index(items)
        # 创建空白对话文件
        self.path_for(session_id).touch()
        return session_id

    def path_for(self, session_id: str) -> Path:
        """返回该 session 的 jsonl 路径（供 State 使用）"""
        return self.dir / f"{session_id}.jsonl"

    def todo_path(self, session_id: str) -> Path:
        """返回该 session 的 todo 文件路径"""
        return self.dir / f"{session_id}.todo.json"

    def get_todos(self, session_id: str) -> list:
        """读取待办清单；返回空列表（若无文件或解析失败）"""
        p = self.todo_path(session_id)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def set_todos(self, session_id: str, todos: list) -> None:
        """写入待办清单"""
        p = self.todo_path(session_id)
        p.write_text(
            json.dumps(todos, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def clear_todos(self, session_id: str) -> None:
        """删除待办文件"""
        p = self.todo_path(session_id)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    def get(self, session_id: str) -> dict | None:
        for it in self._load_index():
            if it["id"] == session_id:
                return it
        return None

    def list_all(self) -> list[dict]:
        """按 last_active_at 倒序返回"""
        items = self._load_index()
        return sorted(items,
                      key=lambda x: x.get("last_active_at", 0),
                      reverse=True)

    def update(self, session_id: str, **fields) -> None:
        """增量更新元数据；自动刷新 last_active_at"""
        items = self._load_index()
        for it in items:
            if it["id"] == session_id:
                it.update(fields)
                if "last_active_at" not in fields:
                    it["last_active_at"] = time.time()
                break
        self._save_index(items)

    def delete(self, session_id: str) -> bool:
        """删除 session 元数据 + 对话文件 + todo 文件"""
        items = self._load_index()
        new_items = [it for it in items if it["id"] != session_id]
        if len(new_items) == len(items):
            return False
        self._save_index(new_items)
        p = self.path_for(session_id)
        if p.exists():
            p.unlink()
        self.clear_todos(session_id)
        # 删的是当前会话 → 清掉指针
        try:
            if self.current_path.read_text(encoding="utf-8").strip() == session_id:
                self.current_path.unlink()
        except OSError:
            pass
        return True

    # —— 当前会话指针 ——

    def set_current(self, session_id: str) -> None:
        """持久化"当前会话"指针。

        在启动续接、/new、/switch 等任何"当前会话变化"的时刻调用，
        而非仅在退出时记录——进程崩溃/强杀时指针依然正确。
        """
        self.current_path.write_text(session_id, encoding="utf-8")

    def get_current(self) -> str | None:
        """读取当前会话指针；指向的会话已不存在则视为失效，返回 None"""
        try:
            sid = self.current_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return sid if sid and self.get(sid) else None

    def last_session_id(self) -> str | None:
        items = self.list_all()
        return items[0]["id"] if items else None

    def delete_all(self) -> int:
        """删除所有 session 元数据 + 对话文件 + todo 文件，返回删除数量。
        index.json 被清空为 []，但保留文件本身。
        """
        items = self._load_index()
        removed = len(items)
        for it in items:
            sid = it["id"]
            p = self.path_for(sid)
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
            self.clear_todos(sid)
        self._save_index([])
        self.current_path.unlink(missing_ok=True)
        return removed

    def cleanup_empty(self) -> int:
        """清理空 session（jsonl 不存在或 size=0）。

        启动时调用，删掉那些"启动后立刻 /exit"残留的空 session 元数据。
        返回清理的 session 数。
        """
        items = self._load_index()
        survivors = []
        removed = 0
        for it in items:
            sid = it["id"]
            p = self.path_for(sid)
            # jsonl 不存在或 size=0 → 删元数据 + 删空文件
            if not p.exists() or p.stat().st_size == 0:
                if p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        pass
                removed += 1
                continue
            survivors.append(it)
        if removed:
            self._save_index(survivors)
            # 指针指向的会话刚被清掉 → 失效指针一并清理
            try:
                cur = self.current_path.read_text(encoding="utf-8").strip()
                if cur and cur not in {it["id"] for it in survivors}:
                    self.current_path.unlink()
            except OSError:
                pass
        return removed

    # —— 导出 ——

    def export_markdown(self, session_id: str,
                        messages: list[dict]) -> str:
        meta = self.get(session_id) or {}
        ts = meta.get("created_at", 0)
        created = (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                   if ts else "?")
        lines = [
            f"# Session {session_id}",
            f"- mode: `{meta.get('mode', '?')}`",
            f"- task: {meta.get('task', '')}",
            f"- created: {created}",
            f"- steps: {meta.get('steps_total', 0)}",
            f"- tokens: {meta.get('tokens_total', 0)}",
            "",
        ]
        for m in messages:
            role = m.get("role", "?")
            content = m.get("content", "")
            if isinstance(content, list):
                content = json.dumps(content, ensure_ascii=False)
            content = str(content)
            lines.append(f"## [{role}]")
            if role == "tool":
                # 工具输出是原始文本（含 | # 等 markdown 特殊字符），
                # 用围栏代码块包裹，避免被 markdown 渲染器误解析。
                # 自适应围栏长度：比内容中最长的连续反引号多一个
                runs = re.findall(r"`+", content)
                fence = "`" * (max([len(r) for r in runs], default=0) + 3)
                lines.append(fence)
                lines.append(content)
                lines.append(fence)
            else:
                lines.append(content)
            lines.append("")
        return "\n".join(lines)
