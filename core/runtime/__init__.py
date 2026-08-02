from .markers import PLUGIN_ID

__all__ = ["DailyLifeRuntime", "PLUGIN_ID"]


def __getattr__(name: str):
    """按需加载运行时，避免 sight -> runtime -> live -> sight 的循环导入。"""
    if name == "DailyLifeRuntime":
        from .live import DailyLifeRuntime

        return DailyLifeRuntime
    raise AttributeError(name)
