"""vil-agent-cli: VilAgent 的单次 CLI 入口

单次 CLI 模式：一次 task 跑完即退出。
多轮对话请用 vil-agent-frontend（REPL）。

会话策略（与 frontend 一致）:
    默认        续接上次会话（无则新建），实现跨次 CLI 历史复用
    --new       强制新建 session
    --continue <id|last>  续接指定会话 ID（'last' 等价于默认行为）

用法（模式即子命令，必须显式指定）:
    python vil-agent-cli.py ask 列出当前目录文件并说明项目结构
    python vil-agent-cli.py do 在项目根加 hello.py
    python vil-agent-cli.py do --new 隔离执行不相关任务
    python vil-agent-cli.py review 审查 VilAgent/loop.py
    python vil-agent-cli.py history list
    python vil-agent-cli.py config set llm.temperature 0.3

结构（经典 MVC，Model/View 共享自 VilAgent/agent_app.py）:
    Model       AgentModel      配置 + LLM/State/Context 组件装配
    View        TerminalView    事件渲染 + 权限交互输入
    Controller  本文件           解析参数、装配 Model+View、驱动 agent
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from VilAgent import SessionManager, State, load_config, set_value, get_value, CONFIG_FILE
from VilAgent.agent_app import (
    AgentModel, TerminalView,
    SESSIONS_DIR as _SESSIONS_DIR,
    console as _console,
)


# ===================== Controller =====================

# 写类模式：默认 trust=True（允许写操作）
_WRITE_MODES = {"do"}


def _add_mode_flags(sp: argparse.ArgumentParser) -> None:
    """给模式子命令加公共 flag"""
    sp.add_argument("--no-stream", action="store_true", help="关闭流式输出")
    sp.add_argument("--max-steps", type=int, default=None, help="最大步数（默认 50）")
    sp.add_argument("--continue", dest="continue_session", metavar="SESSION",
                    help="续接指定会话 ID（'last' 表示最近一次，默认行为）")
    sp.add_argument("--new", dest="new_session", action="store_true",
                    help="强制新建 session（不续接上次）")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vil-agent",
        description="Vil Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  vil-agent ask 列出当前目录文件并说明项目结构\n"
            "  vil-agent do 在项目根加 hello.py\n"
            "  vil-agent do --new 隔离执行不相关任务\n"
            "  vil-agent do --continue <id> 续接指定会话\n"
            "  vil-agent review 审查 VilAgent/loop.py\n"
            "  vil-agent history list\n"
            "  vil-agent history show <session_id>\n"
            "  vil-agent history export <session_id> -o out.md\n"
            "  vil-agent config set llm.temperature 0.3"
        ),
    )
    sub = p.add_subparsers(dest="command", metavar="<command>")

    # agent 模式即子命令：必须显式指定
    for name, help_text in [
        ("ask", "只读分析/规划"),
        ("do", "执行（允许写操作）"),
        ("review", "代码审查"),
    ]:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("task", nargs="+", help="任务文本")
        _add_mode_flags(sp)

    # history：按 session 物理隔离，每个会话一个 jsonl 文件
    ph = sub.add_parser("history", help="对话历史管理（按 session 隔离）")
    hsub = ph.add_subparsers(dest="action", required=False, metavar="<action>")
    hsub.add_parser("list", help="列出所有会话（最近优先）")
    sh = hsub.add_parser("show", help="显示最近或指定会话内容")
    sh.add_argument("session", nargs="?", help="会话 ID（缺省=最近一次）")
    sh.add_argument("-n", type=int, default=10, help="显示消息条数（默认 10）")
    cl = hsub.add_parser("clear", help="删除最近或指定会话")
    cl.add_argument("session", nargs="?", help="会话 ID（缺省=最近一次）")
    ex = hsub.add_parser("export", help="导出会话为 Markdown")
    ex.add_argument("session", help="会话 ID")
    ex.add_argument("-o", "--out", help="输出文件路径（默认 session_<id>.md）")

    # config
    pc = sub.add_parser("config", help="全局配置管理")
    csub = pc.add_subparsers(dest="action", required=False, metavar="<action>")
    ps = csub.add_parser("set", help="set KEY VAL"); ps.add_argument("key"); ps.add_argument("value")
    pg = csub.add_parser("get", help="get KEY"); pg.add_argument("key")
    csub.add_parser("list", help="列出合并后配置（敏感字段掩码）")
    csub.add_parser("path", help="显示配置文件路径")

    p._history_sp = ph
    p._config_sp = pc
    return p


async def run_agent(args: argparse.Namespace) -> None:
    """agent 模式：mode 由子命令决定，trust 由模式决定

    会话：默认新建；--continue <id|last> 续接，避免跨任务历史污染。
    """
    model = AgentModel()
    view = TerminalView()

    mode = args.command
    trust = mode in _WRITE_MODES
    cfg = model.cfg
    stream = not args.no_stream and cfg.get("stream", True)
    max_steps = args.max_steps or cfg.get("max_steps", 50)

    task = " ".join(args.task)

    # —— 会话解析：默认续接上次 / --new 强制新建 / --continue <id> 指定 ——
    sm = SessionManager(_SESSIONS_DIR)
    # 启动时清理空 session（启动后立刻中断残留的）
    sm.cleanup_empty()
    cont = getattr(args, "continue_session", None)
    force_new = getattr(args, "new_session", False)

    if cont:
        # 显式 --continue <id|last>
        sid = cont
        if sid == "last":
            # 优先持久化的当前会话指针，失效再按最近活跃时间兜底
            sid = sm.get_current() or sm.last_session_id() or ""
            if not sid:
                raise SystemExit("[session] 没有可续接的会话")
        meta = sm.get(sid)
        if not meta:
            raise SystemExit(f"[session] 会话 {sid} 不存在")
        # 续接时以当前子命令的 mode 为准（允许 do 续接为 ask 等切换）
        sm.update(sid, status="active", mode=mode)
        _console.print(f"[dim]\\[session] 续接 {sid}[/dim]")
    elif force_new:
        # 显式 --new
        sid = sm.create(mode=mode, task=task)
        _console.print(f"[dim]\\[session] 新建 {sid}[/dim]")
    else:
        # 默认：续接上次会话（无则新建）
        last_sid = sm.get_current() or sm.last_session_id()
        if last_sid and sm.get(last_sid):
            sid = last_sid
            sm.update(sid, status="active", mode=mode)
            _console.print(f"[dim]\\[session] 续接上次 {sid}[/dim]")
        else:
            sid = sm.create(mode=mode, task=task)
            _console.print(f"[dim]\\[session] 新建 {sid}[/dim]")

    sm.set_current(sid)  # 记录当前会话，供下次默认续接

    # State 路径指向该 session 的独立 jsonl 文件
    state = State(sm.path_for(sid))

    agent = model.build_agent(
        mode=mode, trust=trust, stream=stream,
        max_steps=max_steps, permission_handler=view.prompt_permission,
        state=state,
        session_id=sid,
        session_manager=sm,
    )

    # 联合 callback：view 渲染 + 把最终 stats / summary 写回 session
    last = {"content": ""}

    async def on_event(event: dict) -> None:
        await view.on_event(event)
        e = event.get("event")
        if e == "done":
            last["content"] = event.get("content", "")
        elif e == "stats":
            # status 从 loop.py 透传：done / interrupted / loop_detected / max_steps
            status = event.get("status", "done")
            sm.update(
                sid,
                tokens_total=event["usage"].get("total_tokens", 0),
                steps_total=event["steps"],
                status=status,
                summary=last["content"][:200],
            )

    try:
        await agent.run(task, callback=on_event)
    finally:
        await model.close()


def _fmt_time(ts: float) -> str:
    if not ts:
        return "?"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def cmd_history(args: argparse.Namespace) -> int:
    sm = SessionManager(_SESSIONS_DIR)
    action = args.action

    if action == "list":
        return _history_list(sm, limit=50)

    if action == "show":
        sid = args.session
        if sid is None:
            sid = sm.last_session_id()
            if not sid:
                print("[history] (空)")
                return 0
        meta = sm.get(sid)
        if not meta:
            print(f"[history] 会话 {sid} 不存在")
            return 1
        state = State(sm.path_for(sid))
        msgs = state.load()
        print(f"=== session {sid} (mode={meta.get('mode')}, "
              f"steps={meta.get('steps_total', 0)}, "
              f"tokens={meta.get('tokens_total', 0)}) ===")
        print(f"task: {meta.get('task', '')}")
        n = args.n
        for m in msgs[-n:]:
            role = m.get("role", "?")
            content = m.get("content", "")
            if isinstance(content, str):
                preview = content[:120] + ("..." if len(content) > 120 else "")
            else:
                preview = str(content)[:120]
            print(f"[{role}] {preview}")
        print(f"[history] 共 {len(msgs)} 条，显示最近 {min(n, len(msgs))} 条")
        return 0

    if action == "clear":
        sid = args.session
        if sid is None:
            sid = sm.last_session_id()
            if not sid:
                print("[history] (空)")
                return 0
        if sm.delete(sid):
            print(f"[history] 已删除会话 {sid}")
            return 0
        print(f"[history] 会话 {sid} 不存在")
        return 1

    if action == "export":
        sid = args.session
        meta = sm.get(sid)
        if not meta:
            print(f"[history] 会话 {sid} 不存在")
            return 1
        state = State(sm.path_for(sid))
        msgs = state.load()
        md = sm.export_markdown(sid, msgs)
        out = args.out or f"session_{sid}.md"
        Path(out).write_text(md, encoding="utf-8")
        print(f"[history] 已导出 {out}")
        return 0

    return 1


def _history_list(sm: SessionManager, limit: int = 20) -> int:
    items = sm.list_all()
    if not items:
        print("[history] (空)")
        return 0
    print(f"{'ID':<14} {'MODE':<8} {'STEPS':<6} {'TOKENS':<8} "
          f"{'UPDATED':<17} {'TASK'}")
    for it in items[:limit]:
        task = (it.get("task") or "")[:50]
        print(f"{it['id']:<14} {it.get('mode', '?'):<8} "
              f"{it.get('steps_total', 0):<6} "
              f"{it.get('tokens_total', 0):<8} "
              f"{_fmt_time(it.get('last_active_at', 0)):<17} "
              f"{task}")
    print(f"[history] 共 {len(items)} 个会话，显示最近 {min(limit, len(items))} 个")
    return 0


def _parse_value(s: str):
    """尝试 JSON 解析（true/false/数字/null），否则当字符串"""
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return s


def _mask_value(v):
    """敏感字符串值用星号掩码：前 4 + **** + 后 4"""
    if not isinstance(v, str) or len(v) <= 8:
        return "****" if isinstance(v, str) and v else v
    return v[:4] + "****" + v[-4:]


def _mask_config(obj):
    """递归掩码敏感字段（key/token/secret/password/credential）的值"""
    sensitive = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    if isinstance(obj, dict):
        return {k: (_mask_value(v) if any(s in k.upper() for s in sensitive)
                    and isinstance(v, (str, int, float))
                    else _mask_config(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_config(i) for i in obj]
    return obj


def cmd_config(args: argparse.Namespace) -> int:
    if args.action == "set":
        set_value(args.key, _parse_value(args.value))
        print(f"[config] {args.key} = {args.value}")
        return 0
    if args.action == "get":
        # 显式取值显示原文（方便核对）
        print(get_value(args.key))
        return 0
    if args.action == "list":
        print(json.dumps(_mask_config(load_config()),
                         ensure_ascii=False, indent=2))
        return 0
    if args.action == "path":
        print(CONFIG_FILE)
        return 0
    return 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cmd = args.command
    if cmd is None:
        parser.print_help()
        return 0
    if cmd in ("ask", "do", "review"):
        try:
            asyncio.run(run_agent(args))
        except KeyboardInterrupt:
            print("\n[interrupt]", file=sys.stderr)
            return 130
        return 0
    if cmd == "history":
        if args.action is None:
            parser._history_sp.print_help()
            return 0
        return cmd_history(args)
    if cmd == "config":
        if args.action is None:
            parser._config_sp.print_help()
            return 0
        return cmd_config(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
