"""轻量 i18n：输出字符串的 cn / en 双语支持。

设计：
- 只覆盖**输出字符串**（用户可见 / LLM 可见），注释与 docstring 保持中文。
- 不引入 key 目录，直接用 inline 双语，改动局部、可读性好。
- 两套取值方式：
    t(cn, en)  立即求值——运行时用户可见字符串（CLI / frontend / 报错）
    L(cn, en)  延迟求值——import 期就构建好、运行时才知语言的场景（工具 schema）

用法：
    from .i18n import set_lang, get_lang, t, L, resolve
    set_lang("en")
    print(t("已取消", "Cancelled"))
"""
_LANG = "cn"

_SUPPORTED = ("cn", "en")


def set_lang(lang) -> None:
    """设置全局语言；未知值回退 cn。"""
    global _LANG
    lang = lang or "cn"
    _LANG = lang if lang in _SUPPORTED else "cn"


def get_lang() -> str:
    return _LANG


def t(cn: str, en: str | None = None) -> str:
    """立即取值：当前语言为 en 且提供了 en 时返回 en，否则返回 cn。"""
    if _LANG == "en" and en is not None:
        return en
    return cn


class L:
    """延迟本地化字符串：cn/en 二选一，在 resolve() 时按当前语言求值。

    用于 import 期构建、运行期才确定语言的场景（如 @tool 的 description /
    parameters），str(L) 或 resolve() 得到当前语言文本。
    """

    __slots__ = ("cn", "en")

    def __init__(self, cn: str, en: str | None = None):
        self.cn = cn
        self.en = cn if en is None else en

    def resolve(self) -> str:
        return self.en if _LANG == "en" else self.cn

    def __str__(self) -> str:
        return self.resolve()

    def __repr__(self) -> str:
        return self.resolve()


def resolve(obj):
    """递归把结构里的 L 实例替换成当前语言字符串（dict / list / 标量）。"""
    if isinstance(obj, L):
        return obj.resolve()
    if isinstance(obj, dict):
        return {k: resolve(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve(v) for v in obj]
    return obj
