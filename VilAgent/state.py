"""对话历史管理：JSONL 存储 + token 预算 + 摘要压缩"""
import json
from pathlib import Path

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:
    _ENC = None


def count_tokens(text: str) -> int:
    """估算 token 数。优先用 tiktoken BPE；不可用时退回字符数 / 3。"""
    if not text:
        return 0
    if _ENC is not None:
        return len(_ENC.encode(text))
    # 降级：中英混合约 3 字符/token
    return max(1, len(text) // 3)


def count_message_tokens(msg: dict) -> int:
    """估算单条 message 的 token：content + role 元数据开销（约 4 token）。"""
    content = msg.get("content", "")
    if isinstance(content, list):
        # 多模态：拼接后估算
        content = json.dumps(content, ensure_ascii=False)
    n = count_tokens(str(content)) + 4  # role + 结构开销
    # tool_calls 也占 token
    tcs = msg.get("tool_calls")
    if tcs:
        n += count_tokens(json.dumps(tcs, ensure_ascii=False))
    return n


def count_messages_tokens(msgs: list[dict]) -> int:
    """估算 messages 列表总 token"""
    return sum(count_message_tokens(m) for m in msgs)


class State:
    """
    JSONL 对话历史，每条消息一行 JSON

    用法：
        state = State(Path("~/.vil/history.jsonl"))
        state.append({"role": "user", "content": "hello"})
        msgs = state.all_messages()
    """

    def __init__(self, path: Path | str, max_length: int = 30):
        self.path = Path(path)
        self.max_length = max_length
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(self, message: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    def load(self) -> list[dict]:
        messages = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return messages

    def all_messages(self, compress: bool = False,
                     token_budget: int | None = None) -> list[dict]:
        """加载消息，按需压缩或 token 预算截断。

        :param compress: 启用 L2 工具结果压缩（旧 tool result 替换占位符）
        :param token_budget: 若给定，超预算时用摘要替代最旧消息，
                             而非永久丢弃——保留长会话的决策上下文
        """
        messages = self.load()

        # token 预算模式：超预算时摘要旧消息
        if token_budget is not None:
            return self._apply_token_budget(messages, token_budget)

        # 原有条数滑窗逻辑（向后兼容，无 token_budget 时走旧路径）
        if len(messages) <= self.max_length:
            return self._compress_tool_results(messages) if compress else messages

        first = messages[0]
        rest = messages[-(self.max_length - 1):]
        while rest and rest[0].get("role") == "tool":
            rest = rest[1:]
        result = [first] + rest
        return self._compress_tool_results(result) if compress else result

    def _apply_token_budget(self, messages: list[dict],
                            budget: int) -> list[dict]:
        """按 token 预算构建 messages：超预算时摘要最旧部分。

        返回的列表**不含 system 前缀**（由调用方 loop.py 添加自己的 system
        prompt）。仅返回历史消息（含摘要 system 消息若有缓存）。
        """
        if not messages:
            return []

        # 预算的 80% 给历史，20% 给 system prompt + 当前任务
        hist_budget = int(budget * 0.8)

        # 从最新往回累加，确定保留范围
        kept = []
        used = 0
        for msg in reversed(messages):
            if msg.get("role") == "system":
                continue  # 跳过原 system，由 loop 加
            n = count_message_tokens(msg)
            if used + n > hist_budget and kept:
                break
            used += n
            kept.append(msg)

        kept.reverse()  # 恢复时间顺序

        # 确保开头不是孤立 tool（无对应 assistant.tool_calls）
        while kept and kept[0].get("role") == "tool":
            kept = kept[1:]

        # 加载摘要缓存（若 loop.py 已生成）
        summary = self._load_summary()
        result = []
        if summary:
            result.append({
                "role": "system",
                "content": f"[Earlier conversation summary]\n{summary}",
            })
        result.extend(kept)
        return result

    def _load_summary(self) -> str | None:
        """读取摘要缓存（由外部 summarize() 写入）"""
        p = self.path.with_suffix(".summary.json")
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data.get("summary")
        except (json.JSONDecodeError, OSError):
            return None

    def _save_summary(self, summary: str, covered_count: int) -> None:
        """保存摘要 + 已覆盖的消息数（供下次增量摘要）"""
        p = self.path.with_suffix(".summary.json")
        data = {"summary": summary, "covered_count": covered_count}
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")

    def get_messages_to_summarize(self, keep_recent: int = 10) -> list[dict]:
        """返回需要摘要的旧消息（全部 - 最近 keep_recent 条 - 首条 system）。

        供 loop.py 在 run 开始时调用：取这些消息生成摘要，然后调
        _save_summary 缓存。后续 _apply_token_budget 会读缓存。
        """
        messages = self.load()
        if len(messages) <= keep_recent + 1:
            return []
        # 跳过首条 system，取中间段
        first = messages[0] if messages[0].get("role") == "system" else None
        start = 1 if first else 0
        # 末尾 keep_recent 条保留原文
        end = len(messages) - keep_recent
        if end <= start:
            return []
        old = messages[start:end]
        # 确保不摘要孤立的 tool 消息（需要配对的 assistant）
        while old and old[0].get("role") == "tool":
            old = old[1:]
        return old

    def _compress_tool_results(self, msgs: list[dict],
                               keep_recent: int = 3) -> list[dict]:
        """保留最近 keep_recent 个 tool 消息全文，更早的替换为占位符"""
        tool_indices = [i for i, m in enumerate(msgs) if m.get("role") == "tool"]
        if len(tool_indices) <= keep_recent:
            return msgs
        keep_set = set(tool_indices[-keep_recent:])
        result = []
        for i, m in enumerate(msgs):
            if i in keep_set or m.get("role") != "tool":
                result.append(m)
            else:
                orig = m.get("content", "")
                orig_len = len(orig) if isinstance(orig, str) else 0
                new_msg = dict(m)  # 浅拷贝，不动原 dict
                new_msg["content"] = (
                    f"[tool result cleared, was {orig_len} chars, "
                    f"tool_call_id={m.get('tool_call_id', '?')}]"
                )
                result.append(new_msg)
        return result

    def truncate(self) -> None:
        """将文件截断为 max_length 条（仅条数模式用，token 预算模式不删原始消息）"""
        messages = self.all_messages()
        with open(self.path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def clear(self) -> None:
        self.path.write_text("", encoding="utf-8")
        # 清理摘要缓存
        sp = self.path.with_suffix(".summary.json")
        if sp.exists():
            sp.unlink()
