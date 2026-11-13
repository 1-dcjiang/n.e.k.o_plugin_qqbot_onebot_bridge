# qqbot_onebot_bridge · Neko 适配插件

把 [qqbot-onebot-bridge](https://github.com/jusspound-netizen/qqbot-onebot-bridge)
这个 OneBot 11 Python 客户端适配到 [N.E.K.O](https://project-neko.online/) 插件体系。

**它是桥梁，不是重写的 QQ 接入插件。** 插件内部直接复用上游 `OneBotBridge`，
自身不登录 QQ、不处理签名、不实现协议；登录与签名仍由你自托管的 NapCat
（或任意 OneBot 11 服务端）完成。

## 它做什么

- 在 Neko 生命周期内启停一条到 OneBot 11 服务端的正向 WebSocket 连接（带断线重连）。
- 收到群聊 / 私聊消息后，标准化为记录推入内存缓冲，并按配置推送到 Neko 聊天。
- 暴露 LLM 工具，让 Neko 侧的 AI 通过同一桥接回发群消息 / 私聊、查看状态与最近消息。

```
┌──────────────┐  WebSocket (OneBot 11)  ┌─────────────────────────┐
│  NapCat /    │ ◀────────────────────▶ │  Neko 插件               │
│  OneBot 服务端│                         │  ├─ vendor/bridge (上游) │
│  (登录/签名)  │                         │  ├─ 入站消息 -> Neko 聊天│
└──────────────┘                         │  └─ 出站消息 -> 回发 QQ  │
                                         └─────────────────────────┘
```

## 部署

1. 把整个目录放到 Neko 源码树插件目录：
   `N.E.K.O/plugin/plugins/qqbot_onebot_bridge/`
   （目录名、`plugin.id`、entry 包名三者保持一致。）
2. 确认依赖：`websockets>=12.0`。若宿主 Python 环境未内置，先在该环境安装：
   `pip install "websockets>=12.0"`。插件启动时会探测该库，缺失则返回明确错误。
3. 在插件运行时配置里填写 `[qqbot_onebot_bridge]` 段（参考 `config.example.toml`）。
4. 启动 NapCat / OneBot 服务端并开放正向 WebSocket，再在 Neko 里启动 / Reload 本插件。
5. 静态检查：`uv run neko-plugin check qqbot_onebot_bridge --strict`。

> 本工程未在干净 Neko SDK 上端到端跑通；首次加载后请在目标版本 SDK 上验证
> 启动、收消息、回发三条链路。

## 配置项

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `ws_url` | string | `ws://127.0.0.1:3001` | OneBot 11 正向 WebSocket 地址 |
| `reconnect_delay` | float | `3.0` | 断线重连延迟（秒） |
| `relay_group` | bool | `true` | 是否转发群聊消息 |
| `relay_private` | bool | `true` | 是否转发私聊消息 |
| `relay_to_chat` | bool | `true` | 是否把消息推送进 Neko 聊天 |
| `ai_behavior` | string | `read` | `read` 进上下文不打断 / `respond` 立即回复 / `blind` 忽略 |
| `max_recent_messages` | int | `50` | 内存保留的最近消息条数 |

## 提供的 LLM 工具

| 工具名 | 用途 |
|---|---|
| `qqbot_status` | 查看运行状态、连接地址、转发开关与最近消息数 |
| `qqbot_recent_messages` | 读取内存中最近收到的 QQ 消息 |
| `qqbot_send_group_msg` | 向指定群号发送文本消息 |
| `qqbot_send_private_msg` | 向指定 QQ 号发送私聊文本 |

> 工具以 `@llm_tool` 注册，由宿主在启动时登记到 main_server 工具表；
> 真机上能否被 AI 检索到，需在目标 SDK 上确认注册链路。

## 本地测试

`tests/test_plugin_runtime.py` 在最小 SDK 桩上跑结构测试：导入、启动、
配置兜底、上游事件解析、工具返回形态与空参守卫。运行：

```
python -m unittest tests.test_plugin_runtime -v
```

桩不是真机替代；LLM 工具注册 IPC、消息推送渲染、真实收 / 发消息仍需在
N.E.K.O 宿主里验证。

## ⚠️ 风险提示（沿用上游，务必保留）

本桥接使用**非官方 OneBot 11 协议**。腾讯将这类客户端视为未授权软件，
**可能导致账号被冻结**。请使用**小号**，不要使用主号；本插件与上游代码
均**不绕过检测**。登录、签名由 NapCat 完成，本插件不替代官方 API 保号。

## 边界（不做的事）

- 不登录 QQ（NapCat 做）
- 不处理签名（NapCat 做）
- 不替代官方 API 做账号安全保障
- 不做自动回复策略 / 概率控制 / 信任体系（那是上层机器人插件的职责）

## 协议与来源

- 上游客户端 `vendor/bridge/` 来自
  [jusspound-netizen/qqbot-onebot-bridge](https://github.com/jusspound-netizen/qqbot-onebot-bridge)，
  MIT；`vendor/bridge/` 内四个文件与上游 `main` 分支逐字一致，未改动协议逻辑。
- 本适配层 MIT。
