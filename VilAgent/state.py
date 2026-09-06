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
                     token_budget: int | None = None,
                     _apply_summary: bool = True) -> list[dict]:
        """加载消息，按需压缩或 token 预算截断。

        :param compress: 启用 L2 工具结果压缩（旧 tool result 替换占位符）
        :param token_budget: 若给定，超预算时用摘要替代最旧消息，
                             而非永久丢弃——保留长会话的决策上下文
        :param _apply_summary: 是否用摘要缓存顶替最旧历史（仅供 prompt 视图；
                             持久化/截断时传 False，避免合成的 system 摘要落盘）
        """
        messages = self.load()

        # 有摘要缓存 → 用 [Earlier conversation summary] 顶替最旧 covered 条
        # 让手动 /compress 生成的摘要在滑窗与预算两种模式都生效（运行时替换，
        # 不影响 JSONL 原文）
        if _apply_summary:
            info = self.summary_meta()
            if info:
                covered = self._effective_covered(info, len(messages))
                if covered > 0:
                    messages = ([self._build_summary_message(info)]
                                + messages[covered:])

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

        注意：摘要前缀由 all_messages(_apply_summary=True) 注入到 messages[0]
        （role="system"），此处负责保留它，再对剩余历史按预算截尾。
        """
        if not messages:
            return []

        # 预算的 80% 给历史，20% 给 system prompt + 当前任务
        hist_budget = int(budget * 0.8)

        # 摘要 system 消息（若有）单独保留，不参与截断
        summary_msg = None
        if messages and messages[0].get("role") == "system":
            summary_msg = messages[0]

        # 从最新往回累加，确定保留范围（跳过中途 system）
        kept = []
        used = 0
        for msg in reversed(messages):
            if msg.get("role") == "system":
                continue  # 跳过 system，由 loop 加
            n = count_message_tokens(msg)
            if used + n > hist_budget and kept:
                break
            used += n
            kept.append(msg)

        kept.reverse()  # 恢复时间顺序

        # 确保开头不是孤立 tool（无对应 assistant.tool_calls）
        while kept and kept[0].get("role") == "tool":
            kept = kept[1:]

        result = []
        if summary_msg:
            result.append(summary_msg)
        result.extend(kept)
        return result

    def _load_summary(self) -> str | None:
        """读取摘要缓存文本（由外部 summarize() 写入）；无缓存返回 None"""
        info = self.summary_meta()
        return info.get("summary") if info else None

    def summary_meta(self) -> dict | None:
        """读取摘要缓存元数据：{"summary", "covered_count", "keep_recent"}"""
        p = self.path.with_suffix(".summary.json")
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not data.get("summary"):
                return None
            return {
                "summary": data["summary"],
                "covered_count": int(data.get("covered_count") or 0),
                # 旧格式无 keep_recent → 默认 10
                "keep_recent": int(data.get("keep_recent") or 10),
            }
        except (json.JSONDecodeError, OSError, KeyError):
            return None

    def _save_summary(self, summary: str, covered_count: int,
                      keep_recent: int = 10) -> None:
        """保存摘要 + 已覆盖的消息数 + 尾部保留原文条数（供下次增量摘要）"""
        p = self.path.with_suffix(".summary.json")
        data = {
            "summary": summary,
            "covered_count": covered_count,
            "keep_recent": keep_recent,
        }
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")

    def _build_summary_message(self, info: dict) -> dict:
        """把摘要缓存转成一条运行时 system 消息"""
        return {
            "role": "system",
            "content": f"[Earlier conversation summary]\n{info['summary']}",
        }

    def _effective_covered(self, info: dict, total: int) -> int:
        """摘要可顶替的消息数：不超过 covered_count，且不吞掉尾部
        keep_recent 条原文（truncate 裁短文件后 covered 可能过期）"""
        keep = info.get("keep_recent") or 10
        return min(info.get("covered_count") or 0, max(0, total - keep))

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
        """将文件截断为 max_length 条（仅条数模式用，token 预算模式不删原始消息）

        用 _apply_summary=False：合成的摘要 system 消息只活在运行时 prompt，
        不写回 JSONL 历史文件。
        """
        messages = self.all_messages(_apply_summary=False)
        with open(self.path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def clear(self) -> None:
        self.path.write_text("", encoding="utf-8")
        # 清理摘要缓存
        sp = self.path.with_suffix(".summary.json")
        if sp.exists():
            sp.unlink()
