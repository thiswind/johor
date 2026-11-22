# JOHOR Agent

[English](README.md) · [中文](README.zh-CN.md) · [العربية](README.ar.md)

一个专为 **Telegram** 打造的单用户个人 AI 智能体。

JOHOR 只服务一位指定用户——带持久人设的对话、视觉理解、图像生成、联网搜索、语音双向（听与说）、点歌式音乐创作、定时任务，以及可持续学习的技能系统。整仓克隆、配置、即可运行。

这是一个独立产品，不是祖先项目的配置分支。它发源于 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 0.13.0 的快照（2026 年 8 月），此后刻意分道：原树约 60% 已被裁掉。JOHOR 不追踪上游，按自己的节奏演进。

## 为什么「窄」胜过「通用」

hermes-agent 是通用智能体框架：几十个消息平台、一切皆可配置、功能在所有目标间取平均。这种通用性有代价——一个适配器要伺候十二个平台，就哪个都做不到极致。

JOHOR 押了相反的注：

- **只做 Telegram。** 一个平台适配器，深度调校：媒体处理、语音条、长消息、发出后编辑。
- **只服务一位指定用户。** 没有多租户的任何东西。会话、记忆、人设状态都假设只有一个人。
- **一条短而固执的能力栈**（见下），每一环都在生产中每日检验。

Telegram 本身配得上这份专一：免费的 Bot API、宽裕的消息与文件限额、原生语音条、一流媒体支持、没有平台税、每个操作系统都有正经客户端。对个人智能体来说，它就是最好的底座。

瘦身砍掉的：19 个未用的平台适配器、文档站、可选技能目录、内置 TUI——全部移除。留下的是 Telegram 个人智能体真正会跑的东西。

## 能力一览

| 能力 | 用户看到什么 |
|---|---|
| 对话 | 带持久人设的长对话；上下文自动压缩，对话中不失忆 |
| 视觉 | 发照片或截图，直接问 |
| 图像生成 | 描述画面，收到图 |
| 联网搜索 | 实时检索，答案带来源 |
| 语音（听） | 发语音条，自动转写并理解 |
| 语音（说） | 回复可合成语音播放 |
| 音乐 | 描述风格/情绪/语言，一两分钟出整曲 |
| 定时任务 | 自然语言约提醒、约周期巡检 |
| 技能 | 可加载的技能文档；智能体能学会新技能 |

## 模型要求（先读这里）

JOHOR 是重度工具型智能体：一个回合往往串联多次工具调用，还要求长上下文下的指令遵循。**主模型是这一切好不好的最大变量。** 模型太弱，工具调用会肉眼可见地退化——丢参数、忘计划、断循环。

主模型最低门槛（任选其一）：

| 供应商 | 最低要求 |
|---|---|
| Anthropic | Claude Opus 5（`claude-opus-5`）或更新 |
| OpenAI | GPT-6 Astra（`gpt-6-astra`）或更新 |
| DeepSeek | DeepSeek-V4.1 系列或更新 |
| 智谱（Z.ai） | GLM-5.3 或更新 |

经验法则：跑不动 agentic 工具链的模型就是不达标；达到这些档位的都能跑好。

## 支持栈

这是一个**精选版（curated build）**：下表列出的服务就是被选中、接线、优化并每日测试的那些。**没列出的就是不支持**——没有一长串半残的集成等你踩坑，也没有任何「未来会支持」的排队清单。这个智能体在意的能力都在表上；不在表上的，它就不做。

| 角色 | 服务 / 模型 |
|---|---|
| 主模型 | GLM-5.3（Anthropic 协议端点） |
| 视觉 + 辅助任务 | GLM-5.3-Flash |
| 图像生成 | gpt-image-2 |
| 联网搜索 | **Perplexity（sonar）——唯一的一等搜索后端** |
| 网页提取 | Tavily |
| 语音转文字 | scribe_v3 |
| 文字转语音 | eleven_v4（Rachel 声线） |
| 音乐生成 | Suno |
| 上下文引擎 | cursor_style（内置；见下） |

主模型可换（任何达到上述门槛的供应商，走同一套端点配置即可），但多模态服务就是这个版本优化所对的靶心。

关于搜索，一个立场是刻意的：**Perplexity 是唯一的一等搜索后端。** 这个智能体用来做严肃的事，严肃的事需要检索质量——关键词时代的引擎返回的是包装成答案的垃圾。其他后端技术上仍可配置，但它们是二等的，待遇也是如此。

你有没有这些服务的 API key 是你自己的事。代码不关心 key 从哪来。

### 不想一家家买

上面的服务直接向厂商购买意味着好几个账号和好几张账单。两个聚合平台用单一账号和 key 覆盖栈里的大部分：

- [OpenRouter](https://openrouter.ai) —— 模型目录广，一张账单
- [apinexus.pro](https://apinexus.pro) —— 上述多模态服务的一站式接入

任选其一，把拿到的 base URL + key 填进 `config.yaml`，你跑的就是支持栈。

## 快速开始

```bash
git clone https://github.com/thiswind/johor
cd johor
uv sync                                   # 按锁文件建环境
cp config.example.yaml ~/.hermes/config.yaml
$EDITOR ~/.hermes/.env                    # 填入你的 API key
python -m hermes_cli.main gateway run     # 启动 Telegram 网关
```

你需要从 [@BotFather](https://t.me/BotFather) 拿一个 Telegram bot token；其余 key 都在 `.env`。`config.example.yaml` 对每个配置节都有注释和公开默认值——端点、模型名、开关——密钥位留空给你填。

公开树与生产环境跑的是同一份代码，差的只是凭据和私有部署细节。克隆下来跑不起来就是 bug。

## 上下文引擎：cursor_style

长对话由 Cursor 风格的引擎压缩（`plugins/context_engine/cursor_style/`，仓内默认）：

- 只保护系统提示——每轮对话皆可压缩，话题可以真正漂移，而不是被拽回第一轮
- tiktoken 精确计数，CJK 感知（中文/阿语比朴素 `chars//4` 准 2.5–2.9 倍）
- 压缩前完整对话归档为 JSONL——摘要可回链，细节可回查
- 工具输出修剪、增量摘要、防抖冷却一应俱全

引擎在 [hermes-cursor-compressor](https://github.com/thiswind/hermes-cursor-compressor) 开源开发；本仓内置一份作为默认。

## 安全模型

参考部署将智能体跑在 systemd 强沙箱下：`ProtectSystem=strict` 只留一条可写家目录路径、`NoNewPrivileges`、空能力集、seccomp `@system-service`。沙箱单元模板随仓提供（`customizations/johor-agent.service`）。依赖钉死（`uv.lock` 精确版本），自升级通道按设计禁用。

## 测试

```bash
scripts/run_tests.sh          # 全量（建议 Linux/WSL）
scripts/run_tests.sh tests/agent/ -q
```

只有生产在用链路带测试（tools/web、音乐、生图、TTS、转写、gateway/telegram、agent 核心）；随瘦身移除的平台，其测试一并移除。

## 与上游的关系 / 授权

hermes-agent 0.13.0 快照（2026-08-29，单提交基线，不共享 git 历史，不追踪上游）。基线定制：Perplexity 注册表兜底 + 后端漂移告警、cursor_style 引擎、部署参考件。此后分野：品牌更名、重连加固、TTS 迁移、音乐生成、瘦身手术、cursor_style 升默认。

以 **GPL-3.0** 授权（[LICENSE](LICENSE)）；上游 MIT 声明保留于 [LICENSE.upstream-mit](LICENSE.upstream-mit)。感谢 Nous Research 打下的地基。

## 状态

持续维护——这棵树每天作为某人的真实个人智能体在跑。引擎与栈稳定；开发是增量的（依赖边界、测试、文档）。公开发布是私有生产仓的脱敏快照，在有意义的里程碑落地时切出。

### 变更记录

- **2026-11（v2 快照）**——首次全量重切：瘦身后树（比 2026-08 基线小约 60%）、cursor_style 默认引擎、README 重写、`config.example.yaml`、全新单提交历史。
- **2026-08（v1 快照）**——首次公开发布。
