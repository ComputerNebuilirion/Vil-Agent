"""L3 沙箱执行层（Windows 主力）

Windows + pywin32: Job Object 限制 内存/CPU，环境清理剥离敏感变量，
                  超时用 TerminateJobObject 杀整个 Job（含后代）。
无 pywin32:        退化为 subprocess + timeout（best-effort，非真正沙箱）。

Linux/macOS: TODO（等有平台条件再补 bubblewrap / sandbox-exec 路径）。
"""
import os
import subprocess
import sys
import tempfile
import uuid

from .ast_check import check_safety

# Windows pywin32 检测
if sys.platform == "win32":
    try:
        import win32job
        import win32con
        import win32api
        _HAS_WIN32 = True
    except ImportError:
        _HAS_WIN32 = False
else:
    _HAS_WIN32 = False


def sandbox_run(code: str,
                timeout: float = 30,
                mem_mb: int = 256,
                cwd: str | None = None) -> tuple[int, str]:
    """
    在沙盒里执行一段 Python 代码

    Windows + pywin32: Job Object 限制内存/CPU，环境清理，TerminateJobObject 杀后代
    其他: subprocess + timeout (best-effort)

    :return: (exit_code, 合并后的 stdout/stderr)
    """
    if cwd is None:
        cwd = tempfile.mkdtemp(prefix="sandbox_")

    # L1 预检：高危代码不进沙箱（ctypes/exec/eval 能逃逸 Job Object）
    is_safe, msg = check_safety(code)
    if not is_safe:
        return -1, f"安全检查失败: {msg}"

    env = _clean_env()

    if _HAS_WIN32:
        return _run_with_job_object(code, timeout, mem_mb, cwd, env)
    return _run_fallback(code, timeout, cwd, env)


def _clean_env(env: dict | None = None) -> dict:
    """剥离敏感环境变量（API key/token/secret/password）"""
    base = dict(env if env is not None else os.environ)
    sensitive = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
    for k in list(base.keys()):
        if any(s in k.upper() for s in sensitive):
            base.pop(k, None)
    # 显式清几个常见凭证变量名
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY",
              "VIL_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        base.pop(k, None)
    # 保留 PATH / SYSTEMROOT / TEMP 等基础变量，足以让 python 跑
    return base


def _run_with_job_object(code: str, timeout: float, mem_mb: int,
                         cwd: str, env: dict) -> tuple[int, str]:
    """Windows Job Object 沙箱执行"""
    hJob = win32job.CreateJobObject(None, "")

    basic_limit = win32job.QueryInformationJobObject(
        hJob, win32job.JobObjectExtendedLimitInformation)
    basic_limit['ProcessMemoryLimit'] = mem_mb * 1024 * 1024
    basic_limit['BasicLimitInformation']['LimitFlags'] |= (
        win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY |
        win32job.JOB_OBJECT_LIMIT_JOB_TIME
    )
    basic_limit['BasicLimitInformation']['PerJobUserTimeLimit'] = int(timeout * 10000000)
    win32job.SetInformationJobObject(
        hJob, win32job.JobObjectExtendedLimitInformation, basic_limit)

    # UI 限制：禁止关机/重启/注销
    ui_limit = win32job.QueryInformationJobObject(
        hJob, win32job.JobObjectBasicUIRestrictions)
    ui_limit['UIRestrictionsClass'] |= win32job.JOB_OBJECT_UILIMIT_EXITWINDOWS
    win32job.SetInformationJobObject(
        hJob, win32job.JobObjectBasicUIRestrictions, ui_limit)

    # 写临时脚本（uuid 后缀避免与本模块 sandbox.py 撞名，防子进程误 import 自身）
    script = os.path.join(cwd, f"sandbox_{uuid.uuid4().hex}.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(code)

    # 启动子进程（带清理后的 env）
    proc = subprocess.Popen(
        [sys.executable, script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8", errors="replace",
        cwd=cwd,
        env=env,
    )

    # 关进 Job（失败则退化为普通 timeout，不阻断执行）
    job_assigned = True
    try:
        win32job.AssignProcessToJobObject(
            hJob,
            win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, proc.pid)
        )
    except Exception:
        job_assigned = False

    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # 用 TerminateJobObject 杀整个 Job（含所有后代进程），比 proc.kill() 更彻底
        if job_assigned:
            try:
                win32job.TerminateJobObject(hJob, 1)
            except Exception:
                proc.kill()
        else:
            proc.kill()
        out = "SANDBOX TIMEOUT"
    finally:
        try:
            win32api.CloseHandle(hJob)
        except Exception:
            pass
        try:
            os.unlink(script)
        except OSError:
            pass

    return proc.returncode, out


def _run_fallback(code: str, timeout: float, cwd: str, env: dict) -> tuple[int, str]:
    """非 Windows / 无 pywin32：subprocess + timeout（best-effort）"""
    script = os.path.join(cwd, f"sandbox_{uuid.uuid4().hex}.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(code)

    try:
        result = subprocess.run(
            [sys.executable, script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8", errors="replace",
            cwd=cwd,
            env=env,
            timeout=timeout,
        )
        return result.returncode, result.stdout
    except subprocess.TimeoutExpired:
        return -1, "SANDBOX TIMEOUT"
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass
