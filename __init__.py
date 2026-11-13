"""N.E.K.O 适配层：把 qqbot-onebot-bridge 接入 Neko。

本插件不重写 QQ 接入逻辑，只做两件事：
  1. 复用 vendor 进来的 ``OneBotBridge``，在 Neko 生命周期内启停 WebSocket
     桥接，把 NapCat / OneBot 11 服务端发来的群聊、私聊消息转发进 Neko；
  2. 暴露 LLM 工具，让 Neko 侧的 AI 通过同一桥接回发群消息 / 私聊、查看状态。

上游客户端：https://github.com/jusspound-netizen/qqbot-onebot-bridge
边界（沿用上游）：本插件不登录 QQ、不处理签名、不替代官方 API 保号。
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections import deque
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
)

from .vendor.bridge import OneBotBridge, OneBotEvent


@neko_plugin
class QQBotOneBotBridgePlugin(NekoPluginBase):
    """OneBot 11 -> Neko 桥接适配插件。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)

        # 配置（startup / config_change 时从 self.config 重载）
        self.ws_url: str = "ws://127.0.0.1:3001"
        self.reconnect_delay: float = 3.0
        self.relay_group: bool = True
        self.relay_private: bool = True
        self.relay_to_chat: bool = True
        self.ai_behavior: str = "read"
        self._max_recent: int = 50

        # 运行态
        self._bridge: OneBotBridge | None = None
        self._task: asyncio.Task | None = None
        self._connected: bool = False
        self._recent: deque[dict[str, Any]] = deque(maxlen=self._max_recent)
        self._lock: asyncio.Lock | None = None

    # ─────────────────────────── 配置 ───────────────────────────

    async def _reload_config(self) -> None:
        """读运行时配置段；宿主读不到就回退默认值，绝不让启动失败。"""
        try:
            raw = await self.config.dump(timeout=5.0)
            section = raw.get("qqbot_onebot_bridge", {}) if isinstance(raw, dict) else {}
            if not isinstance(section, dict):
                section = {}
        except Exception as exc:  # 无真实 ctx 或读配置接口不可用时走兜底
            self.logger.debug("读取配置失败，回退默认值: %s", exc)
            section = {}

        self.ws_url = str(section.get("ws_url", "ws://127.0.0.1:3001"))
        self.reconnect_delay = float(section.get("reconnect_delay", 3.0))
        self.relay_group = bool(section.get("relay_group", True))
        self.relay_private = bool(section.get("relay_private", True))
        self.relay_to_chat = bool(section.get("relay_to_chat", True))
        self.ai_behavior = str(section.get("ai_behavior", "read"))
        self._max_recent = int(section.get("max_recent_messages", 50))

        kept = list(self._recent)[-self._max_recent:]
        self._recent = deque(kept, maxlen=self._max_recent)

    # ─────────────────────────── 生命周期 ───────────────────────────

    @lifecycle(id="startup")
    async def on_startup(self, **_: Any) -> Ok | Err:
        try:
            await self._reload_config()
        except Exception as exc:  # 防御性：异常不冒泡
            self.logger.exception("config load failed")
            return Err(SdkError(f"配置读取失败: {exc}"))

        if importlib.util.find_spec("websockets") is None:
            return Err(SdkError("缺少依赖 websockets，请先在插件依赖中安装"))

        if self._lock is None:
            self._lock = asyncio.Lock()

        self._task = asyncio.create_task(self._run_bridge())
        self.logger.info("qqbot bridge started -> %s", self.ws_url)
        return Ok({"status": "ready", "ws_url": self.ws_url})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_: Any) -> Ok | Err:
        await self._stop_bridge()
        self.logger.info("qqbot bridge stopped")
        return Ok({"status": "stopped"})

    @lifecycle(id="config_change")
    async def on_config_change(self, **_: Any) -> Ok | Err:
        # 配置变更后重启桥接任务，使新地址 / 开关立即生效。
        await self._reload_config()
        if self._task is not None:
            await self._stop_bridge()
            self._task = asyncio.create_task(self._run_bridge())
        return Ok({"status": "reloaded"})

    # ─────────────────────────── 桥接运行 ───────────────────────────

    async def _run_bridge(self) -> None:
        self._bridge = OneBotBridge(
            self.ws_url,
            self._on_event,
            reconnect_delay=self.reconnect_delay,
        )
        try:
            await self._bridge.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("bridge run exited")

    async def _stop_bridge(self) -> None:
        if self._bridge is not None:
            await self._bridge.close()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._bridge = None
        self._connected = False

    # ─────────────────────────── 入站消息 ───────────────────────────

    async def _on_event(self, event: OneBotEvent) -> None:
        if event.is_private and not self.relay_private:
            return
        if event.is_group and not self.relay_group:
            return

        record: dict[str, Any] = {
            "type": "group" if event.is_group else "private",
            "group_id": event.group_id,
            "user_id": event.user_id,
            "text": event.raw_message or "",
            "time": event.time,
            "self_id": event.self_id,
        }

        if self._lock is not None:
            async with self._lock:
                self._recent.append(record)
        else:
            self._recent.append(record)
        self._connected = True

        if self.relay_to_chat:
            await self._push_to_chat(record)

    async def _push_to_chat(self, record: dict[str, Any]) -> None:
        group = record["group_id"]
        user = record["user_id"]
        if record["type"] == "group":
            head = f"[QQ 群 {group or '?'}] {user or '?'}"
        else:
            head = f"[QQ 私聊] {user or '?'}"
        text = f"{head}: {record['text']}"

        push = getattr(self, "push_message", None) or getattr(self.ctx, "push_message", None)
        if push is None:
            self.logger.warning("push_message unavailable; message kept in recent buffer")
            return
        try:
            push(
                source="qqbot_onebot_bridge",
                visibility=["chat"],
                ai_behavior=self.ai_behavior,
                parts=[{"type": "text", "text": text}],
                priority=3,
            )
        except Exception:
            self.logger.exception("push_message failed")

    # ─────────────────────────── LLM 工具入口 ───────────────────────────

    @llm_tool(
        name="qqbot_status",
        description=(
            "查看 QQ OneBot 桥接的运行状态、连接地址、转发开关与最近消息数。"
            "何时用：怀疑桥接掉线、想确认插件是否连上 NapCat / OneBot 服务端，"
            "或在回发消息前先核对连接状态。前置：无。限制：只读，不修改配置、不重连。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
    )
    async def get_status(self, **_: Any) -> Ok | Err:
        return Ok({
            "running": self._task is not None and not self._task.done(),
            "connected": self._connected,
            "ws_url": self.ws_url,
            "reconnect_delay": self.reconnect_delay,
            "relay_group": self.relay_group,
            "relay_private": self.relay_private,
            "relay_to_chat": self.relay_to_chat,
            "ai_behavior": self.ai_behavior,
            "recent_count": len(self._recent),
        })

    @llm_tool(
        name="qqbot_recent_messages",
        description=(
            "读取桥接最近收到的 QQ 消息（群聊 / 私聊），仅保留内存中的最近若干条，不查历史记录。"
            "何时用：需要回顾最近谁在群里或私聊说了什么，再决定是否回复。前置：桥接已收到过消息。"
            "限制：只读缓冲，不联网、不拉取远端聊天记录。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回条数，默认 20，上限为缓冲大小"},
            },
        },
    )
    async def recent_messages(self, limit: int = 20, **_: Any) -> Ok | Err:
        n = max(1, min(int(limit), len(self._recent)))
        return Ok({"messages": list(self._recent)[-n:]})

    @llm_tool(
        name="qqbot_send_group_msg",
        description=(
            "通过 OneBot 桥接向指定 QQ 群发送一条文本消息。"
            "何时用：用户要求把某条内容发到某个 QQ 群。前置：桥接处于连接状态，且已拿到正确群号。"
            "限制：仅发送纯文本；不做 @、图片、文件；⚠️ 走非官方协议，注意只用小号，"
            "不要发送违规内容，本工具不绕过任何检测。发送失败会返回 Err 并附原因。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "group_id": {"type": "integer", "description": "QQ 群号"},
                "message": {"type": "string", "description": "要发送的文本"},
            },
            "required": ["group_id", "message"],
        },
    )
    async def send_group_msg(self, group_id: int = 0, message: str = "", **_: Any) -> Ok | Err:
        if self._bridge is None:
            return Err(SdkError("桥接未运行"))
        if not group_id or not message:
            return Err(SdkError("group_id 与 message 不能为空"))
        try:
            result = await self._bridge.send_group_msg(int(group_id), message)
            return Ok({"sent": True, "result": result})
        except Exception as exc:
            return Err(SdkError(f"发送群消息失败: {exc}"))

    @llm_tool(
        name="qqbot_send_private_msg",
        description=(
            "通过 OneBot 桥接向指定 QQ 用户发送一条私聊文本消息。"
            "何时用：用户要求把某条内容私聊给某个 QQ 号。前置：桥接处于连接状态，且已拿到正确 QQ 号。"
            "限制：仅发送纯文本；不做图片、文件；⚠️ 走非官方协议，注意只用小号，"
            "不要发送违规内容，本工具不绕过任何检测。发送失败会返回 Err 并附原因。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "QQ 号"},
                "message": {"type": "string", "description": "要发送的文本"},
            },
            "required": ["user_id", "message"],
        },
    )
    async def send_private_msg(self, user_id: int = 0, message: str = "", **_: Any) -> Ok | Err:
        if self._bridge is None:
            return Err(SdkError("桥接未运行"))
        if not user_id or not message:
            return Err(SdkError("user_id 与 message 不能为空"))
        try:
            result = await self._bridge.send_private_msg(int(user_id), message)
            return Ok({"sent": True, "result": result})
        except Exception as exc:
            return Err(SdkError(f"发送私聊消息失败: {exc}"))
