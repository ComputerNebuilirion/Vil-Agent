"""TodoWrite 工具：Agent 自主规划和跟踪任务进度

设计：
- 整列表替换模式：LLM 每次传完整 todos 数组，工具直接覆盖存储
- readonly=True：规划是只读操作，三模式（ask/do/review）均可用
- 持久化到 {sessions_dir}/{sid}.todo.json
"""
import json
from pathlib import Path

from . import tool
from ..i18n import L, t as _t


@tool(
    name="todo_write",
    description=L(
        "规划和跟踪任务进度。当任务需要多个步骤时，用此工具维护待办清单。"
        "每次调用传入完整的 todos 数组（整列表替换模式）。"
        "简单任务（1-2步）不需要使用。",
        "Plan and track task progress. Use this tool to maintain a todo list when a task "
        "needs multiple steps. Pass the full todos array on every call (whole-list replace). "
        "Not needed for simple tasks (1-2 steps).",
    ),
    parameters={
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": L("完整的待办清单（整列表替换）。每项含 content 和 status。",
                                 "The full todo list (whole-list replace). Each item has content and status."),
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": L("任务描述（简洁明确，如 '实现登录功能'）", "Task description (concise, e.g. 'implement login')"),
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                            "description": L("pending=待办, in_progress=进行中, completed=已完成",
                                             "pending=todo, in_progress=doing, completed=done"),
                        },
                    },
                    "required": ["content", "status"],
                },
            },
        },
        "required": ["todos"],
    },
    readonly=True,
)
def todo_write(todos: list, _ctx: dict) -> str:
    """保存/更新待办清单。每次调用替换整个列表。

    :param todos: 完整待办列表 [{"content": "...", "status": "pending|in_progress|completed"}, ...]
    :param _ctx: 运行时上下文，需含 session_id 和 sessions_dir
    :return: 操作结果摘要
    """
    session_id = _ctx.get("session_id", "")
    sessions_dir = _ctx.get("sessions_dir", "")
    if not session_id or not sessions_dir:
        return _t("Error: todo_write 未配置 session 信息",
                  "Error: todo_write has no session info configured")

    # 校验 todos
    valid_statuses = {"pending", "in_progress", "completed"}
    for i, item in enumerate(todos):
        if not isinstance(item, dict):
            return _t(f"Error: todos[{i}] 必须是对象", f"Error: todos[{i}] must be an object")
        content = item.get("content", "")
        status = item.get("status", "pending")
        if not content or not isinstance(content, str):
            return _t(f"Error: todos[{i}].content 不能为空", f"Error: todos[{i}].content cannot be empty")
        if status not in valid_statuses:
            return _t(f"Error: todos[{i}].status 必须是 pending/in_progress/completed",
                      f"Error: todos[{i}].status must be pending/in_progress/completed")

    # 过滤多余字段，只保留 content + status
    cleaned = [{"content": t["content"].strip(), "status": t["status"]}
               for t in todos]

    # 写入 {sid}.todo.json
    todo_path = Path(sessions_dir) / f"{session_id}.todo.json"
    todo_path.parent.mkdir(parents=True, exist_ok=True)
    todo_path.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 生成摘要
    total = len(cleaned)
    done = sum(1 for t in cleaned if t["status"] == "completed")
    in_prog = sum(1 for t in cleaned if t["status"] == "in_progress")
    pending = sum(1 for t in cleaned if t["status"] == "pending")

    lines = [_t(f"已更新待办清单（共 {total} 项）：", f"Todo list updated ({total} items):")]
    for t in cleaned:
        icon = {"pending": "○", "in_progress": "◐", "completed": "●"}.get(
            t["status"], "○")
        lines.append(f"  {icon} [{t['status']}] {t['content']}")

    return "\n".join(lines)
