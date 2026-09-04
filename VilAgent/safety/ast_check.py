"""L1 AST 预检层：代码分类 + 风险评级（非安全边界，仅预警/供 L2 决策）

设计哲学（参考 Claude Code）：AST 不是安全边界，混淆代码永远能绕过字符串匹配。
真正隔离靠 L3 OS 沙箱；本层只做"快速分类 + 风险提示"，给 L2 权限层和用户看。
"""
import ast
import re

# ---- 分类用模块/函数表 ----
NETWORK_MODULES = {
    'socket', 'urllib', 'urllib2', 'requests', 'http.client',
    'ftplib', 'smtplib', 'poplib', 'imaplib', 'websocket',
}
EXEC_MODULES = {'subprocess', 'multiprocessing'}
# 高危模块（可在用户态绕过 OS 沙箱，如 ctypes 直接调 Win32）
DANGEROUS_MODULES = {
    'ctypes', 'ctypes.windll', 'ctypes.cdll',
    'win32api', 'win32con', 'win32job', 'win32process',
}

# 高危函数（exec/eval/compile/__import__ 能逃逸任何静态分析）
HIGH_RISK_CALLS = {'exec', 'eval', 'compile', '__import__', 'globals', 'locals'}
# 执行外部命令
EXEC_CALLS = {
    'os.system', 'os.popen', 'os.popen2', 'os.popen3', 'os.popen4',
    'subprocess.run', 'subprocess.Popen', 'subprocess.call',
    'subprocess.check_output', 'subprocess.check_call',
    'os.spawnl', 'os.spawnle', 'os.spawnlp', 'os.spawnlpe',
    'os.spawnv', 'os.spawnve', 'os.spawnvp', 'os.spawnvpe',
}
# 文件/系统写操作
WRITE_CALLS = {
    'os.remove', 'os.unlink', 'os.rename', 'os.chmod', 'os.chown',
    'os.makedirs', 'os.symlink', 'os.link', 'os.chdir',
    'shutil.rmtree', 'shutil.move', 'shutil.copy', 'shutil.copytree',
}


def classify(code: str) -> dict:
    """
    分类 Python 代码的风险等级和操作类型（L1 预检）

    :param code: Python 源码
    :return: {"risk": "low"|"medium"|"high",
              "cmd_type": "read"|"write"|"network"|"exec"|"unknown",
              "reasons": [str, ...]}
    """
    reasons: list[str] = []
    found_types: set[str] = set()
    risk = "low"

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"risk": "high", "cmd_type": "unknown",
                "reasons": [f"语法错误: {e}"]}

    for node in ast.walk(tree):
        # import 语句
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if _match_module(name, NETWORK_MODULES):
                    found_types.add("network")
                    reasons.append(f"导入网络模块: {name}")
                if _match_module(name, EXEC_MODULES):
                    found_types.add("exec")
                    reasons.append(f"导入执行模块: {name}")
                if _match_module(name, DANGEROUS_MODULES):
                    risk = "high"
                    reasons.append(f"导入高危模块: {name}")

        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if _match_module(mod, NETWORK_MODULES):
                found_types.add("network")
                reasons.append(f"从网络模块 {mod} 导入")
            if _match_module(mod, EXEC_MODULES):
                found_types.add("exec")
                reasons.append(f"从执行模块 {mod} 导入")
            if _match_module(mod, DANGEROUS_MODULES):
                risk = "high"
                reasons.append(f"从高危模块 {mod} 导入")

        elif isinstance(node, ast.Call):
            full = _get_full_name(node.func)
            if not full:
                continue
            if full in HIGH_RISK_CALLS:
                risk = "high"
                reasons.append(f"调用高危函数: {full}")
            if full in EXEC_CALLS:
                found_types.add("exec")
                reasons.append(f"调用执行函数: {full}")
            if full in WRITE_CALLS:
                found_types.add("write")
                reasons.append(f"调用写操作: {full}")
            # open() 模式分析
            if full == "open" or full.endswith(".open"):
                mode = _get_open_mode(node)
                if mode and any(c in mode for c in ("w", "a", "x", "+")):
                    found_types.add("write")
                    reasons.append(f"open(mode='{mode}') 写文件")
                else:
                    found_types.add("read")

    # cmd_type 优先级：exec > network > write > read
    if "exec" in found_types:
        cmd_type = "exec"
    elif "network" in found_types:
        cmd_type = "network"
    elif "write" in found_types:
        cmd_type = "write"
    elif "read" in found_types:
        cmd_type = "read"
    else:
        cmd_type = "unknown"

    # 风险升级（未被高危函数定 high 的）
    if risk != "high":
        if cmd_type in ("exec", "network"):
            risk = "medium"
        elif cmd_type == "write":
            risk = "medium"
        else:
            risk = "low"

    return {"risk": risk, "cmd_type": cmd_type, "reasons": reasons}


def check_safety(code: str) -> tuple[bool, str]:
    """
    兼容旧接口：高风险 → 不安全。

    :return: (是否通过预检, 说明)
    """
    c = classify(code)
    if c["risk"] == "high":
        return False, "; ".join(c["reasons"]) or "高风险操作"
    return True, f"通过预检 (risk={c['risk']}, type={c['cmd_type']})"


def _match_module(name: str, modules) -> bool:
    """匹配 import 名：urllib.request 匹配 urllib；os 匹配 os"""
    return any(name == m or name.startswith(m + ".") for m in modules)


def _startswith_any(name: str, patterns) -> bool:
    return _match_module(name, patterns)


def _get_full_name(node) -> str | None:
    """获取 AST 节点完整名称，如 os.system"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _get_full_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
    return None


def _get_open_mode(call_node: ast.Call) -> str | None:
    """提取 open() 的 mode 参数"""
    # 位置参数：open(path, mode)
    if len(call_node.args) >= 2:
        v = call_node.args[1]
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            return v.value
    # 关键字：open(path, mode=...)
    for kw in call_node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def is_valid_python(code: str) -> tuple[bool, str]:
    """验证代码是否为有效 Python"""
    try:
        ast.parse(code)
        return True, "有效"
    except SyntaxError as e:
        return False, f"语法错误: {e}"


def contains_unsafe_patterns(code: str) -> tuple[bool, str]:
    """正则快速扫不安全模式（补充 AST 漏检的混淆写法提示）"""
    unsafe_patterns = [
        (r'\b__import__\s*\(', "__import__ 动态导入"),
        (r'\bglobals\s*\(\s*\)', "globals()"),
        (r'\blocals\s*\(\s*\)', "locals()"),
        (r'\bgetattr\s*\(\s*\w+\s*,\s*["\']', "getattr 动态属性访问（可能绕过静态检查）"),
        (r'__\w+__\s*\(', "dunder 方法调用"),
    ]
    for pattern, desc in unsafe_patterns:
        if re.search(pattern, code):
            return True, f"检测到可疑模式: {desc}"
    return False, "安全"
