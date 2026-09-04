"""编辑工具：edit_file（patch 模式，局部修改而非整篇覆盖）"""
from pathlib import Path

from . import tool
from ..safety import is_path_safe


@tool(
    name="edit_file",
    description="局部修改文件内容：用 old_text 匹配替换为 new_text，比 write_file 整篇覆盖更省 token",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对于工作区）"},
            "old_text": {"type": "string", "description": "要被替换的原始文本（精确匹配，含换行）"},
            "new_text": {"type": "string", "description": "替换后的新文本"},
            "replace_all": {"type": "boolean", "description": "是否替换所有匹配（默认 False，只替换第一个）", "default": False},
        },
        "required": ["path", "old_text", "new_text"],
    },
    requires_trust=True,
)
def edit_file(path: str, old_text: str, new_text: str,
              replace_all: bool = False, _ctx=None) -> str:
    ws = Path(_ctx["workspace"])

    if not is_path_safe(path, ws):
        return f"Error: path '{path}' escapes workspace"

    full = (ws / path).resolve()
    if not full.is_file():
        return f"Error: not a file: {path}"

    try:
        content = full.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Error: cannot read file: {e}"

    occurrences = content.count(old_text)
    if occurrences == 0:
        return (f"Error: old_text not found in {path}. "
                f"Check whitespace/indentation. File has {len(content)} chars.")

    if not replace_all and occurrences > 1:
        return (f"Error: old_text appears {occurrences} times in {path}. "
                f"Set replace_all=true or provide more context to make it unique.")

    # 替换
    new_content = content.replace(old_text, new_text)

    # 塞 _file_diff 供 view 渲染（不进 LLM 上下文）
    if _ctx is not None:
        _ctx["_file_diff"] = {"path": path, "old": content, "new": new_content}

    try:
        full.write_text(new_content, encoding="utf-8")
    except PermissionError as e:
        return f"Error: permission denied: {e}"

    count = occurrences if replace_all else 1
    return f"edited {path}: replaced {count} occurrence(s)"
