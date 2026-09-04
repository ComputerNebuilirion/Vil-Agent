"""Vil 全局配置（~/.vil/config.json）

仅全局层。项目层（.vil/）和 init 命令以后再做。

用法：
    from VilAgent import load_config, set_value, get_value
    set_value("llm.endpoint", "http://localhost:11434/v1")
    cfg = load_config()   # 与 DEFAULTS 合并
"""
import json
from pathlib import Path

# 语义化版本号：MAJOR.MINOR.PATCH
# MAJOR：breaking 变更；MINOR：新 feature（向后兼容）；PATCH：bug fix
__version__ = "1.0.0"

CONFIG_DIR = Path.home() / ".vil"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS: dict = {
    "llm": {"endpoint": "", "model": "", "api_key": None, "temperature": 0.2},
    "stream": True,
    "default_mode": "ask",
    "max_steps": 50,
}


def _ensure() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text("{}", encoding="utf-8")
    return CONFIG_FILE


def _read_raw() -> dict:
    _ensure()
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    """读取全局配置，与 DEFAULTS 深合并（用户值覆盖默认）"""
    return _merge(DEFAULTS, _read_raw())


def save_config(cfg: dict) -> None:
    """整体写入（覆盖）"""
    _ensure()
    CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_value(key: str):
    """dotted key 读取，如 get_value('llm.endpoint')"""
    cur = load_config()
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_value(key: str, value) -> None:
    """dotted key 设值（只写用户填的字段，不把 DEFAULTS 污染进文件）"""
    cfg = _read_raw()
    parts = key.split(".")
    cur = cfg
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value
    save_config(cfg)
