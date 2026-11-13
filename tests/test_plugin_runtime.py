"""在 SDK 桩上把插件类真跑一遍：导入、启动、配置兜底、事件解析、工具返回形态。

这是一条**结构**测试：证明入口签名、装饰器参数、返回值形态不会当场炸。
真机行为（LLM 工具注册 IPC、推送渲染、真实收消息 / 回发）仍需在 N.E.K.O 里验。

注意：市场 CI 会把插件挂载到真实 N.E.K.O 源码树里跑 pytest，此时 ``plugin.sdk``
解析到真实 SDK 而不是 ``tests/stub``。因此本测试同时对齐真实 SDK 契约：
工具元数据挂在 ``__neko_llm_tool_meta__`` 属性上（对象带 ``name`` / ``parameters``
字段）；运行时配置通过覆写 ``plugin.config`` 注入，而不是依赖桩 ctx。
"""

import asyncio
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT = os.path.dirname(ROOT)
STUB = os.path.join(ROOT, "tests", "stub")
for p in (STUB, PARENT):
    if p not in sys.path:
        sys.path.insert(0, p)

PLUGIN_PKG = "qqbot_onebot_bridge"
LLM_TOOL_META_ATTR = "__neko_llm_tool_meta__"


def _load_plugin_module():
    """用 importlib 从 ROOT/__init__.py 加载插件包，避免模块级插件 import 落在
    sys.path 操作之后触发 isort / E402 规则（与市场 CI 的 ruff 对齐）。"""
    spec = importlib.util.spec_from_file_location(
        PLUGIN_PKG,
        os.path.join(ROOT, "__init__.py"),
        submodule_search_locations=[ROOT],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PLUGIN_PKG] = mod
    spec.loader.exec_module(mod)
    return mod


class _Config:
    def __init__(self, section):
        self._section = dict(section or {})

    async def dump(self, *, timeout=5.0):
        return {"qqbot_onebot_bridge": dict(self._section)}


class _Ctx:
    def __init__(self, section=None):
        self.config = _Config(section)
        self.pushed = []

    def push_message(self, **kwargs):
        self.pushed.append(kwargs)
        return None


def _err_text(res):
    return str(getattr(res, "error", res))


class TestPluginRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_plugin_module()
        cls.plugin_cls = cls.mod.QQBotOneBotBridgePlugin
        cls.parse_event = staticmethod(cls.mod.vendor.bridge.parse_event)

    def _make(self, section=None):
        return self.plugin_cls(_Ctx(section))

    def _start(self, section=None):
        plugin = self._make(section)
        # 真实 SDK 基类构造时把 self.config 换成宿主 PluginConfig；测试里覆写回
        # 注入的桩配置，使 _reload_config 能读到本节传入的配置段。
        plugin.config = _Config(section)
        res = asyncio.run(plugin.on_startup())
        return plugin, res

    def test_startup_returns_ok(self):
        plugin, res = self._start({})
        try:
            self.assertTrue(res.is_ok(), _err_text(res))
            self.assertEqual(res.value["status"], "ready")
        finally:
            asyncio.run(plugin.on_shutdown())

    def test_settings_fall_back_to_defaults(self):
        plugin, res = self._start({})
        try:
            self.assertTrue(res.is_ok(), _err_text(res))
            self.assertEqual(plugin.ws_url, "ws://127.0.0.1:3001")
            self.assertTrue(plugin.relay_group)
            self.assertTrue(plugin.relay_private)
            self.assertEqual(plugin.ai_behavior, "read")
        finally:
            asyncio.run(plugin.on_shutdown())

    def test_settings_are_read_from_section(self):
        section = {
            "ws_url": "ws://127.0.0.1:3002",
            "relay_group": False,
            "relay_private": False,
            "ai_behavior": "blind",
            "max_recent_messages": 12,
        }
        plugin, res = self._start(section)
        try:
            self.assertTrue(res.is_ok(), _err_text(res))
            self.assertEqual(plugin.ws_url, "ws://127.0.0.1:3002")
            self.assertFalse(plugin.relay_group)
            self.assertFalse(plugin.relay_private)
            self.assertEqual(plugin.ai_behavior, "blind")
            self.assertEqual(plugin._max_recent, 12)
        finally:
            asyncio.run(plugin.on_shutdown())

    def test_event_parser_group_and_private(self):
        group = self.parse_event({
            "post_type": "message", "message_type": "group",
            "group_id": 111, "user_id": 222, "raw_message": "hi",
        })
        self.assertTrue(group.is_group)
        self.assertFalse(group.is_private)
        self.assertEqual(group.group_id, 111)

        private = self.parse_event({
            "post_type": "message", "message_type": "private",
            "user_id": 333, "raw_message": "hello",
        })
        self.assertFalse(private.is_group)
        self.assertTrue(private.is_private)
        self.assertEqual(private.user_id, 333)

        self.assertIsNone(self.parse_event({"post_type": "meta_event", "meta_type": "lifecycle"}))
        self.assertIsNone(self.parse_event("not-a-dict"))

    def test_recent_messages_entry(self):
        plugin, res = self._start({})
        try:
            self.assertTrue(res.is_ok(), _err_text(res))
            out = asyncio.run(plugin.recent_messages(limit=5))
            self.assertTrue(out.is_ok(), _err_text(out))
            self.assertIsInstance(out.value["messages"], list)
        finally:
            asyncio.run(plugin.on_shutdown())

    def test_send_guarded_when_bridge_not_running(self):
        plugin = self._make({})
        res = asyncio.run(plugin.send_group_msg(group_id=1, message="x"))
        self.assertFalse(res.is_ok())
        self.assertIn("未运行", _err_text(res))

        res = asyncio.run(plugin.send_private_msg(user_id=1, message="x"))
        self.assertFalse(res.is_ok())
        self.assertIn("未运行", _err_text(res))

    def test_send_rejects_empty_args(self):
        plugin, res = self._start({})
        try:
            self.assertTrue(res.is_ok(), _err_text(res))
            out = asyncio.run(plugin.send_group_msg(group_id=0, message=""))
            self.assertFalse(out.is_ok())
            out = asyncio.run(plugin.send_private_msg(user_id=0, message=""))
            self.assertFalse(out.is_ok())
        finally:
            asyncio.run(plugin.on_shutdown())

    def test_llm_tool_methods_registered(self):
        expected = {
            "get_status": "qqbot_status",
            "recent_messages": "qqbot_recent_messages",
            "send_group_msg": "qqbot_send_group_msg",
            "send_private_msg": "qqbot_send_private_msg",
        }
        for method, tool_name in expected.items():
            fn = getattr(self.plugin_cls, method, None)
            self.assertIsNotNone(fn, method)
            meta = getattr(fn, LLM_TOOL_META_ATTR, None)
            self.assertIsNotNone(meta, f"{method} 缺少 llm_tool 元数据")
            self.assertEqual(meta.name, tool_name)
            self.assertIn("properties", meta.parameters)


if __name__ == "__main__":
    unittest.main(verbosity=2)
