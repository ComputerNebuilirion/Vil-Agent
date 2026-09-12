"""pytest 配置：把仓库根加入 sys.path，使 `import VilAgent` 可用。

（用 `python -m unittest` 从仓库根运行时 cwd 已在 path 上，无需本文件；
本文件主要给 pytest 与 IDE 用。）
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
