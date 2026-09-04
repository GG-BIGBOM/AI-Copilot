# 数据出境与日志治理

> **这份文件回答一个问题：用户的东西，哪些会离开这台服务器，去了哪，做什么用。**
>
> 只写**能从代码和配置里核实**的事实，每一条都指到具体文件。
> 核不实的一律标 `TODO`，**不写推测**——一份读起来很完整、但有几条是猜的
> 数据说明，比没有更坏：它会让人以为某条边界是有保证的。
>
> 校对基准：`main` @ `aa0505c`（2026-09-03）。
> 相关：[ARCHITECTURE.md](ARCHITECTURE.md) 第三节（隔离）、
> [OPERATIONS.md](OPERATIONS.md)（保留与清理）、[DECISIONS.md](DECISIONS.md) ADR-15（可观测性）。

---

## 一、一句话

**这台机器上不跑任何模型**（[ARCHITECTURE.md](ARCHITECTURE.md)：1.6GB 内存的
硬约束）。所以**每一次问答都必然有数据出境**——向量化、重排、生成三步全在
云端 API 上。哪些字节出去、出到哪，就是下面这张表。

---

## 二、会离开这台服务器的数据

| 数据 | 发给谁 | 什么时候 | 用途 | 代码 |
|---|---|---|---|---|
| **用户的提问原文**（多轮时是改写后的检索式） | SiliconFlow | 每一次提问 | 算查询向量 | `providers/siliconflow.py::embed_query` |
| **提问 + 召回的 20~25 块正文** | SiliconFlow | 每一次提问 | 重排打分 | `providers/siliconflow.py::rerank` |
| **文档正文全文**（切成块） | SiliconFlow | 入库 / 重新入库 | 算块向量 | `providers/siliconflow.py::embed_documents` |
| **system prompt + 最近几轮对话 + 召回材料 + 本轮提问** | DeepSeek（简答档） | 每一次提问 | 生成答案 | `providers/llm.py::stream_parts` |
| 同上 | Moonshot / Kimi（详解档） | 每一次提问 | 生成答案 | 同上，`llm_deep_*` 那组配置 |
| **图片字节**（重编码后的 JPEG） | Moonshot | 上传图片 / 扫描件 PDF 解析 | 转写成文字 | `providers/vision.py::transcribe` |
| **多轮改写的提示词 + 历史** | DeepSeek | 有历史时 | 把「那不良品呢」补成独立问句 | `qa.py`（改写那一段） |

⚠️⚠️ **「私有文档」并不例外。** 用户上传的文档同样要向量化、同样会在被召回
时进入重排和生成的上下文。`owner_id` 隔离管的是**「谁能搜到它」**，
不是「它出不出境」——这两件事经常被混为一谈。

⚠️ **Provider 的密钥只在 `.env`**（`chmod 600`，不进仓库）。
`config.py` 里全部是空字符串默认值，没有任何硬编码。

### 不发出去的

| | 说明 |
|---|---|
| 邮箱、密码哈希 | 一次都不出现在发给 provider 的 payload 里 |
| `user_id` / `owner_id` | 同上。⚠️ 它也**不是 Agent 工具的入参**（`agent/deps.py`），只从 cookie 的登录态来 |
| 原始文件名 | 落盘用 uuid 重命名；文件名只进数据库（`routes/docs.py` 文件头第 2 条） |
| 会话 id / trace id | 不进 provider 请求 |

### 出境方向的第三方：语雀

`sources/yuque.py` 只**拉**公开知识库的正文，不上传任何东西。
请求里除了 `yuque_user_agent` 没有本站数据。

---

## 三、原文会不会被记下来

### 3.1 Provider 侧

**TODO —— 这一格只能由账号持有人去各家控制台确认，代码里看不出来。**
需要确认并回填的是三件事：DeepSeek / Moonshot / SiliconFlow 各自的
**输入是否留存、留存多久、是否用于训练**。
在回填之前，本文件不对此作任何承诺。

### 3.2 应用日志（journald，留在本机）

代码里对提问原文的记录**一律截断**，而且只在异常路径上：

| 位置 | 记了什么 |
|---|---|
| `routes/chat.py`（三条路的 except） | `question[:80]` |
| `qa.py` 改写失败 / 命中标准答案 / 追加主体约束 | `question[:60]` |
| `agent/runner.py` 硬防线拦下时 | `question[:60]`、`answer[:120]` |

⚠️ **没有任何一处把文档正文或召回材料写进日志**（本轮逐条 grep 核实）。
`ARCHITECTURE.md` 那条「不在日志里新增 private document 全文」的禁令
今天是成立的。

### 3.3 请求台账 `request_trace`（留在本机数据库）

一轮一行，**含提问原文**（`question[:2000]`，`api/trace.py::QUESTION_LIMIT`），
**不含答案正文**（`TraceDraft.answer` 只用来判 `answer_source`，不落这张表）。
答案本身在 `messages` 表里，那是用户自己的会话记录。

保留策略见 [OPERATIONS.md](OPERATIONS.md)：普通 30 天、被踩过的和出错的 90 天，
`copilot prune-traces` 执行（**默认只预演**）。

⚠️ 管理台的**概览页不出现任何原始问题文本**——是后端就不给，不是靠前端
不显示（`api/routes/admin.py` 文件头第 2 条）。看全文要点进用户详情或反馈
详情，那是管理员的一次明确动作。

### 3.4 span 树 / OpenTelemetry

`TRACING_ENABLED=false`（默认关，且是可选依赖）。**打开之后也不会带原文**：
逐个核对过所有 `obs.span(...)` 和 `.set(...)` 的属性，只有

```
question_chars / query_chars     长度，不是内容
chunk_count / private_hits / hits / candidates / passed
top_score / threshold / ttfb_ms / tokens / answer_chars
route / mode / decision / model / answer_source / verified / rewritten
```

——**没有一个字段装文本**。所以接 Langfuse 之类的托管后端时，
出去的是指标，不是内容。

⚠️ 这条性质是靠"当初这么写的"维持的，**今天没有任何机制在守它**。
新增一个 `obs.span("...", question=q)` 不会有任何东西变红。
见第五节。

### 3.5 流给浏览器的东西

⭐ **2026-09-03 之前这里有一个真实的旁路，已经修掉。**
详解档曾把模型的 `reasoning_content`（原始思维链）逐字转发给前端。
那段草稿是模型在**完整上下文**里自言自语——里面有 system prompt、
召回材料原文（含私有文档）、以及材料里可能夹带的注入内容。
三道防幻觉闸门管的是**正文**，草稿那一路一个字都管不到。

现在直路只发系统自己的阶段进度，全部是写死的常量
（`api/progress.py`，`tests/test_multiturn.py` 里有白名单断言守着）。
Agent 那条路一直就是丢掉草稿的（`agent/tools.py`）。

⚠️ Agent 的工具步骤会把工具返回值的**前 300 字**发给前端
（`agent/runner.py::_translate` 的 `tool-output-available`）。今天挂在
主 Agent 上的工具里，`answer_kb` 的返回值是给模型的一句状态说明、
不是材料；`my_documents` 返回的是**这个用户自己**的文档标题。
⚠️ **加新工具时这一条要重新过一遍**：一个返回原始材料的工具挂上去，
它的前 300 字就直接到浏览器了。

---

## 四、生命周期

| 事件 | 会发生什么 |
|---|---|
| 用户删一篇文档 | 文档行、块、图片资产、落盘文件一起删（`tests/test_delete_lifecycle.py`）。⚠️ **已经算出去的向量在 provider 那边的留存不受我们控制**，见 3.1 |
| 用户删一条会话 | 会话与消息删干净。`request_trace` **刻意不跟着删**——它记的是「系统那天表现如何」，不是「他说过什么」（`cli.py` 保留策略那段） |
| 停用一个账号 | `copilot disable <email>`。手里的 JWT 下一次请求就 401（每次请求都查库读 `is_active`）。数据全留着，`--undo` 可恢复 |
| 台账过期 | 30 / 90 天，`copilot prune-traces` |
| 悬空纠错截图 | 24 小时，`copilot prune-images`（**默认只预演**） |
| 备份 | 每天 pg_dump + `uploads` + `private-images`，留 14 份；`data/images`（语雀公共图）每周一份 |

---

## 五、还没有保证的事（不要在这里写成已经做到了）

1. **Provider 的留存与训练策略未核实**（3.1）。这是这份文件最大的空白。
2. **「span 不带原文」没有闸门**（3.4）。今天成立，但靠的是当初这么写的。
   可行的形态和 `tests/test_schema_drift.py` 是同一个形状：
   把所有 `obs.span` / `.set` 的属性名收成一张白名单，多一个就红。
3. **日志脱敏没有中心化**。今天靠的是每一处调用方自己 `[:60]`。
   加一处 `logger.info("...%s", question)` 不会有任何东西变红。
4. **没有"导出我的数据"/"删除我的账号"接口**。今天删账号要在服务器上跑 SQL；
   `copilot disable` 只是停用，不删数据。
5. **备份不是同一个 backup generation**（见 [OPERATIONS.md](OPERATIONS.md)）：
   数据库 dump 和文件 tar 是同一次运行里先后两步，中间被删掉的图片会让
   恢复出来的库里留下指不到文件的 image id。
