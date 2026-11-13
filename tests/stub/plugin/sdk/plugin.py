"""最小 N.E.K.O SDK 桩：只为在本机把插件类导入并跑一遍。

它**不是** N.E.K.O 的替代品：真机行为（权限、UI 渲染、推送、10 秒就绪窗口、
LLM 工具注册 IPC）必须在宿主里验证。装饰器签名与真 SDK 保持一致，
这样写错参数名会在本地就炸，而不是等到市场 CI。

元数据属性名与 dataclass 形状对齐真实 SDK ``plugin.sdk.plugin.llm_tool``：
属性 ``__neko_llm_tool_meta__``，对象带 ``name / description / parameters /
timeout_seconds`` 字段。
"""

import logging

_TOOL_NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")

LLM_TOOL_META_ATTR = "__neko_llm_tool_meta__"


class SdkError(Exception):
    def __init__(self, message="", *, code=None, details=None):
        super().__init__(message)
        self.code = code
        self.details = details


class Ok:
    __slots__ = ("value",)

    def __init__(self, value=None):
        self.value = value

    def is_ok(self):
        return True

    def is_err(self):
        return False

    def unwrap(self):
        return self.value

    def __repr__(self):
        return f"Ok({self.value!r})"


class Err:
    __slots__ = ("error",)

    def __init__(self, error=None):
        self.error = error

    def is_ok(self):
        return False

    def is_err(self):
        return True

    def unwrap(self):
        if isinstance(self.error, BaseException):
            raise self.error
        raise SdkError(str(self.error))

    def __repr__(self):
        return f"Err({self.error!r})"


class _ToolMeta:
    """对齐真实 ``LlmToolMeta`` dataclass 的只读形状。"""

    def __init__(self, *, name, description, parameters, timeout_seconds):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.timeout_seconds = timeout_seconds


def neko_plugin(cls):
    return cls


def lifecycle(*, id, name=None, description="", metadata=None):
    allowed = {"startup", "shutdown", "reload", "freeze", "unfreeze", "config_change"}
    if id not in allowed:
        raise TypeError(f"lifecycle() id 必须是 {sorted(allowed)} 之一，收到 {id!r}")

    def wrap(func):
        return func

    return wrap


def llm_tool(*, name=None, description="", parameters=None, timeout=30.0, role=None, reply_tail=False):
    resolved = name if isinstance(name, str) and name else None
    if resolved is not None:
        bad = set(resolved) - _TOOL_NAME_OK
        if bad or not (1 <= len(resolved) <= 64):
            raise TypeError(f"llm_tool name 不合法：{resolved!r}")
    params = parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}}

    def wrap(func):
        setattr(func, LLM_TOOL_META_ATTR, _ToolMeta(
            name=resolved or func.__name__,
            description=description,
            parameters=params,
            timeout_seconds=float(timeout),
        ))
        return func

    return wrap


class NekoPluginBase:
    def __init__(self, ctx):
        self.ctx = ctx
        self.config = ctx.config
        self.logger = logging.getLogger("stub.plugin")

    def push_message(self, **_):
        return None
