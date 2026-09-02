# AI Copilot — 实施计划

> **这份文件只留还没做的事。** 更早的详细任务台账、执行记录、
> 每一次排查和 A/B 的证据，都在 [ARCHIVE.md](ARCHIVE.md)。

## NOW

**方向已定（2026-08-30）：不再面向面试作品扩张功能面，转为把旗舰版
知识库系统本身做扎实，然后正式上线使用，有问题再逐步修。**
多知识版本（原 M18）、校验 Agent（W3.2）、MCP server（W3.1）三样已移除——
都是当时为多产品版本或面试叙事准备的，现在不需要。决策过程仍留在
[DECISIONS.md](DECISIONS.md) 的 ADR-22 / ADR-23（各自开头标了移除日期）；
检索隔离机制（`knowledge_space_id` / `_space_filter`）没有动，
旗舰版的隔离仍然靠它，只是现在只有 flagship 一个可聊天的空间。

✅ **判分器已恢复，正式评测门禁 PASS**（2026-08-30 重跑）：公共库直路、
公共库 Agent、私有库直路、私有库 Agent、风险边界、路由六项全部通过
（跨空间那一项随 M18 一起移除）。没有更换判分模型、没有降低阈值。

### 这一轮做了什么（2026-08-30）

1. **停止 M18 语料导入并清空**——客户端企业版已抓的约 620 篇本地文件删除，
   开发库确认两个企业版空间均 0 篇。
2. **删除三块不需要的代码**：
   - `copilot/verifier.py` + 全部接入点（`qa.py` / `config.py` / `risk_boundary.py`）
   - `copilot/mcp_server.py` + `cli.py` 的 `mcp` 命令 + pyproject 的 `mcp` extra
   - `copilot spaces` 命令组、`ingest`/`sync-yuque` 的 `--space` 参数、
     `ENTERPRISE_DESKTOP`/`ENTERPRISE_WEB` 常量与种子、
     `eval/cross_space.py`+`.yaml`（整套跨空间门禁）
   - 新增 alembic 迁移删掉两个企业版 `knowledge_spaces` 行（迁移前查过
     六张关联表全是 0 行引用，`ondelete="RESTRICT"` 兜底）
3. **求职材料清理**：`docs/case-*.md`、`docs/demo-*.md`、`docs/一页纸-*.md`
   随本轮一并删除；README/plan.md 的面试叙事段落同步删掉。
4. **顺带清出两个和这次删代码无关、但影响旗舰版数据质量的真发现**：
   - flagship 196 篇 + enterprise_desktop 7 篇孤儿测试文档（两轮 pytest
     留在共享开发库里的），清完 flagship 文档数（749）和生产库对上
   - **[ISSUES.md](ISSUES.md) I-13**：flagship 另有 50 篇"统计"类文档
     是语雀内嵌表格（lakesheet 格式），从抓取那天起就是 0 块、一个字
     都搜不到——文档在库里，但读不出来，比"语料没覆盖"更隐蔽。还没修。
5. **Windows 控制台编码 bug**：GBK codepage 下 CLI 输出的 ✓/✗ 会
   `UnicodeEncodeError` 崩溃，`cli.py` 启动时已修。
6. **补上架构图**：`docs/img/architecture.svg`，README 已引用。
7. **生产环境只读核实**：服务器在 `b7e91c4d2a08`（已是最新），四个服务
   健康，两个企业版空间在生产上也确认是 0 篇——这次改动不会和线上冲突。
   顺带修正 plan.md 0.1 步一个真实存在的文档 bug（数据库名写错）。

### 还开着的事

✅ **验证这一轮删代码没删坏东西**（2026-08-30 补跑）：后端 846 个测试全绿、
`ruff check . ../tests ../eval` 干净；前端 `npm run verify`（单测 + lint +
类型 + 构建）全绿。删代码这一轮没有回归。

✅ **[ISSUES.md](ISSUES.md) I-13 已决定**（2026-08-30）：不写 lakesheet
解析器。查过频率后发现这 50 篇 100% 集中在 `tongji`（统计）一个库、
且无一例外是纯表格——量封顶、格式又和"带引用问答"的核心场景不匹配，
两条理由缺一都不够。顺带确认了这 50 篇的 `source_url` 全部真实，
不会被孤儿判据误删。详见 ISSUES.md 里的决定记录。

✅ **隔离数据库全量 pytest 跑过了**（2026-09-02）：[PR #1](https://github.com/GG-BIGBOM/AI-Copilot/pull/1)
触发 CI 在每次重建的 `pgvector/pg16` 上跑全套，**846 passed**——和本机
共享开发库上的数字一模一样。ISSUES.md I-12 那个"共享库掩盖了什么"的
担心，这一轮排除了。之后快进合并进 main，历史仍然线性。

✅ **上线了**（2026-09-02）：`deploy.sh ai` 走完 7 步，公网四条路由全 200。
生产从 `b7e91c4d2a08` 一次跑完三个迁移到 `83d51db7d7a7`，`content_tsv`
回填 4575 块（NULL 0 块），两个企业版空间已删除（现只剩 `common` 0 篇
和 `flagship` 749 篇 / 4575 块）。

⚠️ **上线路上清出两个原本看不见的 bug，都已修并补了测试**：

- **`deploy.sh` 从 2026-08-29 起就跑不完**（`bad9b8f`）。CRLF 闸门里
  一句注释被写断成两行，内嵌 Python 语法错，卡在 `[1/7]`。引入它的
  `26c6e9f` 标题正是「写文件一律 LF」。**这解释了为什么 main 上的
  `c8d3f1a704be` / `d9c4f2e81b36` 在生产外面躺了四天**——不是没人部署，
  是部署脚本自己死了，而且全程零告警。
- **部署门禁会静默丢掉语料指纹**（`4efff90`）。`run_gate()` 声明
  `encoding="utf-8"` 解子进程输出，但子进程按 locale（cp936）写。
  实测那份乱码：证据日期是纯 ASCII，穿过 58 个 U+FFFD 照样抠到；
  语料指纹要匹配中文，一乱就没了。**在控制台编得出那些字符的机器上
  它根本不会崩**，只会安静地打出「语料指纹：不可用」，状态照常 PASS。

两个都属于"只在部署那一刻才暴露"的类型，现在各有一条测试钉着
（`tests/test_ci_contract.py` 第五节 / `tests/test_deploy_gate.py`）。

### 2026-09-02 下半场

✅ **生产开了两个开关**（都在 `/opt/copilot/.env`，改前已备份）：

```
HISTORY_BUDGET_ENABLED=true    W2.1 上下文预算装配器 + 滚动摘要
AGENT_ROLLOUT=1.0              所有账号每一句都走 Agent
```

⚠️⚠️ **这两项是「代码默认值和生产实际值不一致」的地方。**
`config.py` 里 `history_budget_enabled` / `agent_rollout` 的默认值仍是
`False` / `0.0`，`.env.example` 也写着关——只看代码会以为线上是关的。
两处 `.env` 注释里都写了依据和回滚步骤。

⭐ Agent 全量顺带把 **[ISSUES.md](ISSUES.md) I-9 的暴露面关掉了**：那条
边界闸门原来只长在 Agent 路上，而线上绝大多数人走的是直路。
**注意是遮住了，不是修好了**——灰度调回来就复现。

✅ **台账清了一轮**：I-3（邀请码泄漏）、I-5（autocrlf 打架）、
I-6 前半（角标裸露）、I-9（直路边界闸门，默认关等 A/B）都修了；
I-7（21 条无关来源）、I-8（两处语料笔误）复核为**早已修好，只是台账没销**。
每条为什么能活到今天都记在 ISSUES.md 里。

✅ **三个前端修复 + 一个新功能**：纠错贴图位置、思考态过早消失、
角标裸露；新增**回答导出 Markdown / PDF**（PDF 走浏览器打印，
理由见 `frontend/lib/export-answer.ts` 文件头——1.6GB 内存和 2.4 的许可
红线各否掉了一种服务端做法）。

### 以下几项需要你本人操作，我做不了

1. **[ISSUES.md](ISSUES.md) I-6 后半「配图编号跳号」需要一个真实例子**
   （截图或当时那句提问）。后端编号查过是连续的，找到的唯一机制是
   `inlineImages` 删掉后端没有的图号；**不确定是不是报的那个，盲改会改错地方**。
2. **`DIRECT_BOUNDARY_ENABLED` 要不要开**：代码就位、默认关。
   开之前按规矩跑 `eval/longchat.py` 两边各一次（收费），
   `cross_window_ref` 要涨、`in_window_control` 一分都不许掉。
3. **Docker 真机验收**：本机没有 Docker。生产跑的是 systemd 不是 Docker，
   `docker-compose.yml` 是给别人复现用的，优先级最低。
4. **Langfuse 接上**（可选，需要注册账号）：`TRACING_ENABLED` 生产上没
   显式设置，走代码默认；本机看树不需要账号。

✅ **已完成**：

- **部署后手工验收全过**（2026-09-02，用户本人在生产上走了一遍）：
  导出 Markdown / 导出 PDF（PDF 里有提问标题）、思考态在首字之前一直在动、
  Agent 全量后看得到「正在分析」和工具步骤、多轮之后问「那个功能在哪配置来着」
  拿到边界话术、来源清单条数收窄、带图纠错 → `/admin/corrections` 发布 →
  再问一遍拿到订正原文。
- 阿里云安全组只留 22/80/443（用户自行处理）
- 通知用的人两件行为变化（纠错改成审核制）
- span 树截图——目标从"面试作品"改成"实际上线"后砍掉

---

## 第 0 步 — 开工前（半天）

### 0.1 确认线上跑的是哪一版

动任何东西之前先量一次。本机和线上不是同一版代码的话，后面所有验收都是假的：

```bash
# 服务器上
sudo -u postgres psql -d kb -c "select version_num from alembic_version;"
# ⚠️ 数据库名是 `kb`，不是 `copilot`——`.env` 里的 DATABASE_URL 写的是
# `.../kb`。按应用名猜库名会拿到「database "copilot" does not exist」，
# 看起来像库没建，实际上只是名字猜错了（2026-08-30 核实）。
# ⚠️ 单元名是 `copilot-api`，不是 `copilot`。写错的表现是
# 「Unit copilot.service could not be found」——看起来像服务没装，
# 实际上它跑得好好的（2026-08-29 差点据此得出"线上挂了"的结论）
systemctl status copilot-api copilot-worker
```

- 停在 `a2c8f47b91d6` → 三笔改动**没上线**，走 0.2。
- 停在 `b7e91c4d2a08` → 已上线，跳过 0.2。

✅ **2026-08-30 核实：生产停在 `b7e91c4d2a08`，已上线，0.2 可跳过。**
四个服务（`copilot-api` / `copilot-worker` / `nginx` / `postgresql`）均
`active`；内存 1.6Gi 总量、已用 723Mi、可用 889Mi，比文档写的"余量约
300MB"更宽松；`enterprise_desktop` / `enterprise_web` 均 `inactive`、
0 篇文档——M18 语料确实还没碰过生产库，本地的抓取/导入不会和线上打架。

### 0.2 部署攒下的三笔改动（如果还没）

三笔：M19-A 评测契约与门禁、来源清单只列正文引用过的那几条、纠错传图 P0/P1/P2。

```bash
bash deploy/deploy.sh      # 它自己会先备份
```

⚠️ 带 migration `b7e91c4d2a08`。**它的 downgrade 会删掉所有纠错图**，
回滚优先用备份，不要跑 downgrade。

部署完手工走一遍：登录 → 提问 → 看来源清单条数是否收窄 → 提交一条带图纠错 →
`/admin/corrections` 发布 → 再问一遍拿到订正原文。

### 0.3 通知用的人两件行为变化

已经上线但没说过：

1. 「答错了，我来改」现在是**提交审核**，管理员在 `/admin/corrections` 发布之后才生效；
2. 上传多了 xlsx；Word / PPT / PDF 里的截图会跟着正文一起进知识库（私有，只有本人看得到）。

**不花时间，但它带来真实使用数据**——那正是 M20 唯一缺的东西，而且靠时间买不到，
越早通知越好。

---

## ~~Week 1 — 看得见~~　✅ 2026-08-28 完成

> **这一节按本文件的规矩（只留还没做的事）删掉了任务清单。**
> 交付了什么、A/B 的数字、以及留下的三件事，都在最上面的 NOW；
> 三条设计取舍各有一份 ADR（[ADR-15](DECISIONS.md) OTel、
> [ADR-16](DECISIONS.md) 混合检索、[ADR-17](DECISIONS.md) compose）；
> 检索那一路怎么工作见 [ARCHITECTURE.md 第三节](ARCHITECTURE.md)，
> 关键词题集的口径见 [EVALUATION.md 第四·五节](EVALUATION.md)。

---

## Week 2 — 记得住（上下文工程）

> **W2.2 和 W2.3 已落代码，这一节按本文件的规矩删掉了它们的任务清单。**
> 交付了什么、免费 A/B 的数字、还欠哪几轮付费评测，都在最上面的 NOW；
> 设计取舍各有一份 ADR（[ADR-19](DECISIONS.md) 结构化事实表、
> [ADR-20](DECISIONS.md) 注入防线两层）；题集口径见
> [EVALUATION.md 第四·六节](EVALUATION.md)（长会话）和
> [第五节](EVALUATION.md)（注入）。

### W2.1　从固定窗口到预算装配器　✅ 做完了，2026-09-02 在生产开启

**做了什么**　`qa.assemble_messages` 的内部从 `history[-HISTORY_TURNS:]`
换成按预算装配的**分区上下文**，四个区全部落地：

```
[系统指令（含已确认事实）]     ← W2.2 的 session_facts
[更早对话的滚动摘要]           ← ✅ qa.history_digest()，超预算时才生成
[窗口内原文]                   ← 由字符预算决定留几条，不再是固定 6 条
[本轮检索材料 + 问题]          ← W2.3 的围栏
```

⭐⭐ **停了很久的那三个产品决定，答案是「都不需要」**（[ADR-21](DECISIONS.md)）。

这一节原来写着「停在这里等你拍三件事：摘要用哪个模型、摘要存哪里、
超预算阈值定多少」，理由是滚动摘要会引入一笔**按轮数计的经常性成本**。

⚠️ **那三个问题只在「摘要必须由模型来写」这个前提下才存在。** 而这份题集
要的东西抽取就够了：跨窗口那四道题问的是「我一开始说的是哪个版本 / 几个仓 /
哪家客户 / 哪些平台」——答案全是**用户自己打过的原字**。让模型重写一遍，
换来的不是更准，是一次调用、一份延迟，外加一条**会写错的路**。

于是做法变成「把挤出窗口的用户发言按时间列出来，各自截断」，三个问题一起消失：

```
经常性成本    0（纯函数，不调模型、不写库、不缓存）
存在哪里      不存。每轮从历史现算
阈值          HISTORY_CHAR_BUDGET，见 config
```

**A/B 数字**（三份付费评测，longchat 11 题，见 [EVALUATION.md](EVALUATION.md) 第四·六节）：

```
                       两个都关   只开 W2.2   只开 W2.1   两个都开
上下文命中率             28.6%  →   57.1%  →    100.0%  →  100.0%
cross_window_fact 答对     1/4  →     2/4  →       4/4  →     4/4
in_window_control 答对     3/3  →     3/3  →       3/3  →     2/3  ⚠️
```

⚠️⚠️ **最后一列是这一轮最值钱的一个数：两个开关一起开，对照组掉了一道。**
所以结论是**开 W2.1、W2.2 继续关着**，而不是"两个都开"。
没有对照组的话，这份报告会写成「跨窗口 +3 道」，而不会有人发现窗口内掉了一道。

✅ **2026-09-02 在生产开启**：`/opt/copilot/.env` 加 `HISTORY_BUDGET_ENABLED=true`。
⚠️ 在此之前，这个结论**量出来了却一直没被执行**——`config.py` 的默认值是
`False`、生产 `.env` 里也没设，于是跨窗口 4/4 那份收益一分都没拿到。
`SESSION_FACTS_ENABLED` 保持不设（即关）。回滚是删掉那一行再重启，
`.env` 里写了步骤。

---

## ~~Week 3 — 接得上~~　⏸️ 2026-08-30 部分移除

> 原计划做 MCP server（W3.1）、校验 Agent（W3.2）、M18 企业版语料
> （W3.3）。三样都实际写出来过、量过，2026-08-30 转向"旗舰版单独上线"
> 后前两样连代码一起移除，M18 停在"代码就位、语料未导入"。
> 决策与 A/B 数字见 [DECISIONS.md](DECISIONS.md) ADR-22 / ADR-23，
> 详细执行记录在 ARCHIVE.md。

---

## ~~Week 4 — 讲得出~~　⏸️ 2026-08-30 部分移除

> 原计划是面试材料（案例文、demo 脚本、三份岗位一页纸）。目标从"面试作品"
> 变成"实际上线"后，这几份纯求职向的文档随本轮一并删除。
> README 架构图（`docs/img/architecture.svg`）和 ADR 补齐（DECISIONS.md
> 23 份）保留，仍然有效——它们是项目本身的文档，不是面试专用材料。

---

## 红线取舍

项目一直有条红线（「七、约定 5」和「已核对的实现边界」）：不引入 Redis / Celery /
ES / 向量库 / MCP / 多 Agent / Docker。**该破就破，但每破一条写一份 ADR。**

| 红线 | 决定 | 结果 |
|---|---|---|
| OTel / Langfuse | 🔴 **破** | ✅ W1.1 已破，[ADR-15](DECISIONS.md)。默认关 + 可选依赖 + 生产采样 |
| Docker | 🔴 **有限度破** | ✅ W1.3 已破，[ADR-17](DECISIONS.md)。只加本地 compose，**生产仍不用，ADR-1 一字未改** |
| Elasticsearch | ⚪ **不破** | ✅ 兑现了。W1.2 的 BM25 走 Postgres `tsvector` + GIN，一个新服务都没加（[ADR-16](DECISIONS.md)） |
| MCP | 🔴 **破了又收回** | Week 3 做出来、真连通过 Claude Desktop（[ADR-23](DECISIONS.md)），2026-08-30 转向旗舰版单独上线后移除——判断过程仍然成立，只是这一轮不需要 |
| 多 Agent | 🔴 **破了又收回** | 加过一个有指标理由的 verifier（[ADR-22](DECISIONS.md)），A/B 显示指标一个点没动，2026-08-30 随同移除 |
| Qdrant / Milvus | ⚪ **不破** | pgvector 够用。**能说清「什么规模才需要换」比换掉它值钱** |
| Redis / Celery | ⚪ **不破** | Postgres 队列已在生产跑着。「用最少的组件解决同样的问题」是资深信号 |

⭐ **「不引入混合检索」这条 2026-08-28 撤销了一半**，而且值得说清楚撤销的是哪一半：
当初 ADR-3 把"混合检索"和"为混合检索多养一个有状态服务"写成了同一件事。
W1.2 只做了前者。ES 那一条**因此更站得住了**，不是被推翻了。

---

## 明确不做的事

写下来是为了不反复捡起来。这些 JD 上都有，但对**这个项目**是堆砌——
面试官一问「它解决了你的什么问题」就穿帮。

- **微调（fine-tuning）**——问题是知识时效和隔离，不是模型能力。微调解决不了「语料更新了怎么办」。
- **语音 Agent / 浏览器 Agent**——和 ERP 知识库没关系，加了就是关键词填空。
- **GraphRAG / 知识图谱**——4568 块用不上，而且语料是操作手册，实体关系稀疏。
- **换向量库**——见上面红线表。
- **嵌图视觉转写**（原路线图 39）——先用 M19-A 的配图指标量一遍「转写对检索有没有用」
  再决定。一份 PPT 就是几十次付费视觉调用。
- **把 trace 拆成 router / retrieval / rerank / generation 四段做缓存或并行**——
  W1.1 的 span 树已经把段分好了，但**线上还没开采样，归因数据一条都没有**。
  拿到线上 p95 的分段构成之前不动。**先看见，再优化。**

---

## ~~M18 执行细节~~　⏸️ 2026-08-30 移除

> 这一节原本是企业版语料导入的详细执行步骤（抓取 → 入库 → 改写跨空间题集 →
> 激活 → 部署验收）。多空间管理层随本轮一起移除，步骤不再适用。
> 保留的地基：`retrieve.py` 的 `_space_filter`、`Document/Chunk.knowledge_space_id`、
> `tests/test_ingest_spaces.py` 里"判重必须按空间分开"的回归——这些是旗舰版
> 自己的隔离机制依赖的部分，没有跟着删。真要重启多空间，`git log` 里
> 这一段历史还在，不用从头设计。

---

## 推迟：M19-B / M20

不作废，但四周内不做。**要在 README 的 roadmap 里写清楚它们是什么、为什么还没做**——
「有清晰的下一步、且知道每一步的前置条件」比「做完了一切」更像一个真实在维护的项目。

### M19-B — 评测中心与持续回归

多空间部分（原步骤 2）随 M18 一起移除，其余对旗舰版仍然成立：

1. **给 `request_trace` 补几列**：`verified_answer_id`、`general_knowledge_used`、
   `image_count`、`correction_id`。都可空、老数据不回填。
   ⚠️ `answer_source` 口径不变：KB + 常识兜底时仍是 `kb` + `general_knowledge_used=true`，
   **不新增 source 值**，否则历史统计的分母会变。
2. **给评测本身写测试**（人工构造的用例）：Temu 问题 + 小红书证据 → 跨平台污染；
   引用编号存在但内容不支持 → 无支撑引用；图片编号存在但属于另一平台 → 配图串台。
   这条有先例：08-24 发现「配图串台率的第一版判据是错的」，靠人眼看出来的，
   不该指望第二次还有人看得出来。
3. `copilot eval-publish` + 只读的 `/admin/evaluations`。
   **评测不在生产网页里实时跑**——一次全量是两百多次付费调用，放进网页等于给了一个
   「点一下花几十块」的按钮。
4. 闭环最后一段：标准答案发布后**生成一条回归用例进题集**。第一版手工就够
   （发布页多一个「加入回归题集」按钮，导出 yaml 片段人工贴）。

### M20 — 生产验证与 Agent 路由收敛

删掉 `_chat_stream`、`AGENT_TRIGGERS`、`profile is not None` 旧粘性路由。
前置条件（一条不满足就不删）：

- 真实用户用满一周，`quality-report --route agent --days 7` 里没有用户可见的失败。
  ⚠️ 08-24 那次 16 轮带 error 里有 11 轮是 `UsageLimitExceeded`——**那不是用户看到的失败**，
  runner 早把它降级成「这一轮到此为止」，答案照发。报告要能把降级和真失败分开。
- 路由评测 `route_accuracy >= 95`、`bypass_rate == 0` 稳定（不是单轮）。
- ✅ **回滚演练做过并留证**（2026-09-02，见下面「回滚演练留证」一节）。
  ⚠️ 已写入用户新数据的生产库**不做会丢数据的 downgrade**——这次演练全程
  只换代码，`alembic_version` 一动没动。
- 全量门禁退出码 0。

删完立刻重跑全量评测 + 门禁——删代码最容易出的问题是「顺手删掉了还有人用的分支」。

**为什么现在删不了**：灰度还是白名单模式，线上活跃用户只有 1 个（就是自己），
按 `agent_rollout` 的注释，3 个注册账号谈百分比灰度等于观察零样本。
而 `UsageLimitExceeded` 那个 bug 正说明 Agent 路还在长新形态的故障，
它当时的表现是**完全没有症状**。

⭐ 面试时这本身是个好答案：「我知道现在还不能删，因为活跃用户只有 1 个。」

### 回滚演练留证（2026-09-02）

**结论：能回滚，数据不丢，用户可见中断约 6 秒。**

真走了一遍 `5af5466` → `c4099b8` → `5af5466`，只换代码、不碰 alembic。

```
                          耗时      用户可见中断    数据
回滚   5af5466 → c4099b8   413 秒      6 秒        9 张表行数不变
回滚回 c4099b8 → 5af5466   422 秒      5 秒        9 张表行数不变
```

⭐⭐ **「部署耗时」和「用户可见中断」差了 70 倍，而只有后者是用户的事。**
七分钟里绝大部分是**本机**的自检和构建（pytest 5.5 分钟 + 前端 verify + build），
线上全程照常服务；真正的断点只有 systemd 重启到 uvicorn 开始监听那几秒，
脚本自己打出来的那行 `API 就绪（等了 6s）` 就是它。
⚠️ 报「回滚要七分钟」会让人以为要挂七分钟，那是个会影响决策的错误结论。

**怎么确认回滚真的生效了**（而不是脚本假装成功）——两个独立信号互相印证：

```
前端   /var/www/copilot 的 CSS 里 `data-printing`   有 → 没有 → 有
后端   copilot.qa.boundary_reply 这个属性            有 → 没有 → 有
```

⚠️ **只看退出码和 200 是不够的**：旧版同样会退出 0、同样四条路由 200。
必须挑一个**只有新版才有、且能从外部观测**的东西。

**数据完好性判据**　三次快照（回滚前 / 回滚后 / 回滚回来后）比九张表的行数：

```
users 5 · documents 750 · chunks 4577（tsv 4577）· conversations 11
messages 32 · answer_corrections 3 · image_assets 6107 · request_trace 354
```

三次**逐项相同**，`alembic_version` 全程停在 `83d51db7d7a7`。
其中 conversations / messages / answer_corrections 那几条正是当天人工验收
产生的真实数据——**演练要能证明的就是它们没被弄丢**。

**演练前先做了一次新鲜备份**　`kb-20260902-151643.dump`（23M），退出码 0、
无 `FAILED` 标记。同日还对最新那份跑了一次 `restore-drill.sh`：恢复到临时库
`kb_drill_*`（**永远不叫 kb**）、数据对得上、**在恢复出来的库上真跑通了一次
向量检索**。上一次恢复演练是 08-20，之后加了三个迁移，这次确认那三个迁移
没有让备份失效。

**⚠️ 演练撞出来的一个缺口，已修**　服务器上原本查不到「现在跑的是哪个 commit」：
`DEPLOY_AUDIT` 那行只打在本机 stdout 上。没有基准，「从哪个版本回滚到哪个版本」
这句话根本写不出来。`deploy.sh` 现在往 `/opt/copilot/DEPLOYED` 写
commit / at / scope / dirty 四行。
⭐ 回滚到 `c4099b8` 的那一档，`DEPLOYED` 文件**再次消失**了——因为那一版
还没有这个功能。这反过来证明了它的必要性。

**没有做的**　没有从 dump 恢复数据库。这次要验的是「代码回滚」，而数据库
恢复那一路由 `restore-drill.sh` 单独覆盖（它恢复到临时库，不碰生产）。
真要从备份恢复生产库，是另一套流程、另一次演练。

---

### Release Blockers（上线前逐条留证）

多数已经有测试，这一步是**把证据对齐到条目上**，对不上的就是真缺口：

```
跨用户私有文档泄漏 / 跨用户私有图片泄漏
Agent 丢 images / 错图显示
普通用户可访问 Admin API
普通用户可发布 Verified Answer
Verified Answer 跨版本生效
Verified Answer 用私有资料作公共来源
用户删除私有文档后图片仍可访问
用户禁用后旧 JWT 还能调用
纠错图片可通过公开 URL 绕过权限
Markdown 可注入危险 HTML / script
Judge 网络错误被算模型答错              ← UNRELIABLE 三态已实现
```

---

## 记账项

老问题，各自独立，随时可捡：

- 引用角标偶发裸露；
- 配图编号跳号；
- 方案题挂 21 条无关来源（`08631f8` 已修一部分，要复核）；
- **两处语料笔误待你定夺**（只有你能定）：自动审核失败重试是 **48 次还是 10 次**；
  抖音共享面单修复的**「1327 版本」是不是 1.3.2.7**。定了之后走勘误层
  `copilot correct` 改一次、重灌一次。

---

## 每阶段执行规则

1. 先用真实代码和现有测试写 implementation delta；
2. **先补失败测试，再改实现**；不得用局部评测替代全量门禁；
3. 跑相关测试 + 全量后端测试 + ruff，涉及前端时跑 lint / build；
4. 记录 migration upgrade/downgrade、回填校验、备份/回滚和线上证据；
5. 每个逻辑阶段独立 commit，并同步这份文件的 NOW；**未通过不得标完成**。

```bash
# ── 本机自检（和 CI、deploy.sh 第 1 步同一批命令）──
# ⚠️ `../tests ../eval` 不能省（ADR-18）：这两个目录在仓库根，
#    `ruff check .` 一行扫不到，而漏掉它们的表现是**这一步照样绿**
cd backend && .venv/Scripts/python.exe -m ruff check . ../tests ../eval            && .venv/Scripts/python.exe -m pytest -q      # Linux 换 .venv/bin/python
cd ../frontend && npm run verify   # = 单测 + lint + 类型 + 构建，和 CI / deploy.sh 同一条

# ── 评测（只在本机跑）──
cd backend
uv run python ../eval/gate.py                    # 退出码必须 0
```

⚠️ 本机 `backend/` 下**别裸跑 `uv sync`**（会卸掉 parse / agent / eval 三个 extra，
下次 deploy 自检当场红）。
⚠️ 服务器上只能 `.venv/bin/copilot`。
⚠️ 单跑一个测试文件要带 `-c`，否则 rootdir 变成 `Copilot/`，读不到 `asyncio_mode=auto`，
async 测试全红。

---

## Context

### 为什么重来

前两次尝试死在同一个地方：

1. **Fork Onyx**（2026-08-13 起）——选型逻辑对（MIT 双许可，不堵死商业化），但 20 万行产品级代码，删 `ee/` 后每次 merge upstream 都要人工判冲突，一个人维护不住；且 **Docker 至今未装**，部署从第一天就阻塞。目录已清空。
2. **erp-copilot**——路线图铺到 M7 + MCP 二期，实际只有 2 个 commit 和一个 `/hello` 骨架，语料区空的。终点太远，没走到有反馈的地方。

**这次的核心约束：让"别人打开网址就能用"的时间点尽早出现。** M5 就是那个点，之后所有功能都在一个活着、有人用的系统上迭代。

### 目标

| 维度 | 决定 |
|---|---|
| 形态 | 公网网站，注册登录后提问 |
| 知识源 | **语雀公开知识库（爬公开页，无需 token）** + Markdown；PDF 次要 |
| 数据隔离 | **公共库 + 个人库**：语雀内容人人可搜；用户自传文档仅自己可搜 |
| 上传 | 自助上传 → 后台自动解析 → 立即可用 |
| 注册 | **邀请码制** |
| 能力 | RAG 带引用 + 会追问、会调工具 |
| 部署 | 阿里云 ECS → `https://liushun666.cn/` |

### 已核实的事实

| 项 | 值 | 影响 |
|---|---|---|
| 域名 | `liushun666.cn` **已备案**，现跑 Aura Note 落地页 | agent 占根路径；**Aura Note 挪到 `/aura` 保留，不删** |
| 语雀 token | 拿不到（需超级会员） | 走公开页 `appData` 解析，路径已验证 |
| ECS 配置 | **2 核 / 1.6Gi 可用内存 / 40G 磁盘（已用 4.6G）**，Ubuntu，已占 591Mi | 内存是唯一紧约束；磁盘充裕。**不升配**，按 2GB 极简部署 |
| 本机 | Intel Arc 核显无 N 卡；Python 3.14.6（torch 无 wheel）；uv 0.11.4；Node 24.14.1 | uv pin 3.12；模型推理一律走 API |

---

## 一、1.6GB 内存下的三条硬约束

服务器实测 2 核 / 1.6Gi 可用 / 已占 591Mi，**剩约 1.0Gi**。

这套架构能在这个盒子里活，是因为**最吃内存的部分（embedding / rerank / LLM 推理）全在云端 API，服务器上一个模型都不跑**。剩下的常驻进程：

| 进程 | 预算 |
|---|---|
| Nginx | ~40 MB |
| PostgreSQL（调小 `shared_buffers` 到 128MB） | ~250 MB |
| FastAPI（uvicorn 单 worker） | ~250 MB |
| 解析 worker | ~150 MB |
| **合计** | **~700 MB** |

余量约 300MB，很薄。因此下面三条是**硬性的，不是建议**：

### 1. 前端静态导出，且必须本机构建

`next build` 峰值吃 1GB+，在服务器上跑必 OOM。流程固定为：**本机 `npm run build` → 传 `out/` 产物 → Nginx 直接服务静态文件**。

Next.js 16 + AI SDK 7 完整保留，`useChat` 照常工作——只是 `output: 'export'`，服务器上不跑 Node 进程。这同时省下 100–200MB。

> 副作用：不能用 Next.js 的服务端能力（Server Actions、Route Handlers）。本方案里 Next.js 本来就只做 UI、所有逻辑在 FastAPI，**所以零损失**。

### 2. 加 2GB swap

33G 空闲磁盘，这是免费保险，兜住突发峰值不至于进程被 OOM killer 干掉。

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

### 3. 服务器端不跑 Docling 的 PDF ML 管线

Docling 解析 PDF 要加载版面检测模型，1–2GB 内存起步，这台机上必 OOM。但 **md / txt / docx / pptx 走非-ML 路径，很轻**，不受影响。

PDF 的处理方式：
- **服务器端**：只做纯文本提取（`pypdf`，BSD 许可），扫描件不支持
- **需要高质量 PDF 解析时**：本机跑 Docling 完整管线 → 结果推服务器数据库

> 你原话就是「PDF 如果比较难就先不做」，所以这个限制不构成实际损失。

### 4. 内存回收（已勘察，约可回收 107–268MB）

`ps aux` 实测结果，591MB 的构成：

| 进程 | 占用 | 处置 |
|---|---|---|
| `python server.py`（容器 `erp-copilot-mcp:0.1.0`） | **71MB** | ✅ **删**——是已停掉的 erp-copilot 项目的 MCP server，绑 `127.0.0.1:8000`（正好是我们要用的端口） |
| `dockerd` + `containerd` | **90MB** | ✅ **删**——服务器上装了 Docker（本机没装，两回事）。容器删掉后 Docker 无用 |
| `AliYunDunMonitor` | 43MB | 保留。公网站点需要入侵告警 |
| `argusagent`（云监控） | 17MB | 保留。控制台监控图表 |
| `fwupd`（固件更新） | 41MB | ✅ **关**——云主机无物理固件 |
| `tuned`（性能调优） | 28MB | ✅ **关**——对本负载无意义 |
| `multipathd`（多路径存储） | 27MB | ✅ **关**——只有一块 `/dev/vda` |
| `udisksd`（可移动磁盘） | 11MB | ✅ **关**——服务器没人插 U 盘 |
| systemd / journald / resolved 等 | 余下 | 系统必需，不动 |

**确定可回收：107MB**（fwupd + tuned + multipathd + udisksd）
**待查明后可能再回收：161MB**（server.py + Docker）

> ### ✅ 已执行完毕（2026-08-15）
>
> | 指标 | 清理前 | 清理后 |
> |---|---|---|
> | 已用内存 | 591Mi | **327Mi** |
> | 可用内存 | 1.0Gi | **1.3Gi** |
> | `8000` 端口 | 被容器占用 | **空闲**（留给 FastAPI） |
> | 待更新 / 重启 | 17 个 / 需重启 | **0 / 不需要** |
> | 磁盘占用 | 11.6% | 9.7%（Docker 回收 561.9MB） |
>
> | Swap | 0 | **2.0Gi** ✅ |
>
> 备份留在 `/root/backup-2026-08-15/`（192MB，含 nginx 配置、erp-copilot、/var/www）。
> Nginx 保持运行，80/443 正常，`/var/www/myprogram` 按计划保留。
> Docker 的 systemd 单元已全部移除（不自启、不占内存）；`/usr/bin/docker*` 二进制残留仅占磁盘，无影响。
>
> **服务器准备完成，可直接进入 M0。**

**勘察结论（已完成）**：

- 容器是 `erp-copilot-mcp:0.1.0` —— 已停掉的 erp-copilot 项目的 MCP server
- **网站由 Nginx 服务**（占 80/443），容器只绑 `127.0.0.1:8000` → **删容器不影响网站**，无需占位页
- `/opt/erp-copilot` + `/opt/erp-copilot.git`（push 部署用的裸仓库）一并删
- Nginx 已装且运行中，M5 直接改配置即可

**执行顺序**（用户决定全删）：

| 段 | 动作 | 回收 |
|---|---|---|
| 1 | **备份** → `/root/backup-<date>/`：nginx 配置、`/opt/erp-copilot*`、`/var/www` | — |
| 2 | `systemctl list-units \| grep -iE 'erp\|copilot\|mcp'` 查关联服务 | — |
| 3 | 停删容器 → `docker system prune -af --volumes` → `apt purge` docker/containerd → `rm -rf /var/lib/docker` | **~161MB** |
| 4 | `rm -rf /opt/erp-copilot /opt/erp-copilot.git` | 磁盘 |
| 5 | `systemctl disable --now fwupd tuned multipathd udisks2` + `apt autoremove`；查 snapd | **~107MB**（+snapd 30–50MB） |
| 6 | `apt upgrade -y && reboot` | — |
| 7 | 验收：`free -h`；`ss -tlnp \| grep -E ':(80\|443\|8000)'` | — |

**预期：已用 591MB → 260–320MB，可用 1.0GB → ~1.3GB。** 塞下 Postgres + FastAPI + worker（~700MB）后从"很挤"变"够用"。附带 `8000` 端口腾出，正是 FastAPI 要用的。

> ✅ **保留** `/var/www/myprogram`（Aura Note）：占磁盘不占内存，现在删和 M5 删对内存零区别，留着域名不空窗。
> ✅ **保留** `AliYunDunMonitor`(43MB) + `argusagent`(17MB)：公网带注册的站点，入侵告警值这 60MB。

---

## 一·五、安装位置约定（用户规定）

**所有软件一律装到 D 盘。** C 盘只留项目源码。

| 项 | 位置 | 怎么设 |
|---|---|---|
| PostgreSQL 16 + pgvector | `D:\PostgreSQL\16`，数据目录 `D:\PostgreSQL\16\data` | 安装向导里改路径 |
| uv 托管的 Python 3.12 | `D:\dev\uv\python` | 环境变量 `UV_PYTHON_INSTALL_DIR` |
| uv 缓存（依赖包） | `D:\dev\uv\cache` | 环境变量 `UV_CACHE_DIR` |
| npm 缓存 | `D:\dev\npm-cache` | `npm config set cache` |
| 项目源码 | `C:\Users\liushun\Desktop\Copilot` | 当前工作目录，不属"安装" |

> ⚠️ **环境变量必须在装任何东西之前设好**。uv 默认把 Python 和缓存塞在 `%LOCALAPPDATA%`（C 盘），装完再搬要重来一遍。这是 M0 的第一个动作。

---

## 二、技术选型（2026 主流栈）

### 2.1 全景

| 层 | 选型 | 为什么是它 |
|---|---|---|
| Agent 框架 | **Pydantic AI 2.0** | 2026 新项目的默认选择。类型安全、工具签名即 Python 类型、自带 OpenTelemetry。比 LangChain 轻，比手写省事 |
| Web 框架 | **FastAPI** | Python AI 服务事实标准，原生 async + SSE |
| 数据库 | **PostgreSQL 16 + pgvector** | **千万级以下向量的最优解**：向量、文档、用户、任务队列全在一个库，SQL join 直接用，零额外基础设施 |
| 文档解析 | **Docling**（MIT，IBM → Linux Foundation），**非-ML 模式** | 一个库通吃 DOCX/PPTX/XLSX/HTML/MD，输出保留语义层级的结构化文档，替代 pdfplumber+python-docx+python-pptx 的拼装。**PDF 的 ML 管线在 1.6GB 上关闭**（见第一节） |
| Embedding | `BAAI/bge-m3`（SiliconFlow，免费） | 中文最强开源 embedding。**DeepSeek / KIMI 都不提供 embeddings 接口，Gemini 提供但国内服务器访问不了 Google API —— SiliconFlow 是唯一可行解** |
| Rerank | `BAAI/bge-reranker-v2-m3`（SiliconFlow，免费） | 精排，对准确率提升最明显 |
| LLM（生成） | **DeepSeek**（已有 Key），KIMI 备用 | OpenAI 兼容，一套代码切换 |
| LLM（评测裁判） | Gemini（已有 Key，**仅本机用**） | M8 做 LLM-as-Judge，换个模型打分更客观 |
| 前端 | **Next.js 16（静态导出）+ Vercel AI SDK 7** | 2026 AI 聊天界面事实标准。`useChat` 直接搬走流式/工具调用/中断/重试。`output: 'export'` 本机构建，服务器不跑 Node |
| UI | Tailwind + shadcn/ui | 2026 主流组件方案 |
| 任务队列 | **Postgres `FOR UPDATE SKIP LOCKED`** | 已经有 Postgres，不再引入 Redis/Celery。这是标准做法，不是妥协 |

### 2.2 前后端怎么接（关键细节）

**Next.js 只做 UI，所有 AI 逻辑在 Python。** 不用 Vercel 官方 chatbot 模板的全 TS 方案——那会把认证和持久化推到 TS 侧，业务逻辑劈成两半，是维护灾难。

衔接靠 **AI SDK Data Stream Protocol**（SSE 格式）：

```
Next.js  useChat({ transport: new DefaultChatTransport({ api: '/api/chat' }) })
   ↓  nginx 反代
FastAPI  POST /api/chat  →  返回 AI SDK Data Stream Protocol 的 SSE
```

这是 **Vercel 官方支持的路径**（官方模板 "AI SDK Python Streaming"），协议有公开规范；也有 `fastapi-ai-sdk` 包直接实现。不是野路子。

### 2.3 中文混合检索

pgvector 管 dense 向量；中文 BM25 需要分词扩展：

- 首选 `pg_jieba` 或 `zhparser`（需编译安装）
- **装不上就降级**：dense-only + bge-reranker 精排。bge-m3 的中文语义召回本来就强，加上 reranker，纯向量方案的效果损失有限

> **不要为了混合检索卡住主线。** M2 先跑 dense + rerank，BM25 作为 M7 的优化项——用评测数字证明它值得加，而不是凭感觉先上。

### 2.4 ⚠️ 许可红线

**不用 PyMuPDF**——AGPL-3.0，商用需向 Artifex 买授权或整个项目开源。这和你当初「弃 MaxKB（GPL）选 Onyx（MIT）以保留商业化可能」**是同一个陷阱**，且这次是**公网对外提供服务**，AGPL 触发条件比内部自用严格得多。

Docling 是 MIT，一个库覆盖所有格式，顺带解决了这个问题。引入任何新依赖前先看许可。

### 2.5 语雀抓取（无 token）

**实测后改用语雀内部 JSON 接口，比原计划「全靠正则解析 HTML」稳得多：**

```
1. GET /{login}                          → 正则解析 appData 拿 group.id   ⚠️唯一依赖 HTML 的一步
2. GET /api/groups/{group_id}/books      → 知识库列表（只认数字 id，传 login 返回 422）
3. GET /api/catalog_nodes?book_id={id}   → 目录树（文档标识字段叫 url，不是 slug）
4. GET /api/docs/{slug}?book_id={id}&merge_dynamic_data=false
                                         → 正文（content 字段，Lake HTML）+ content_updated_at
```

**语雀改版的脆弱面从「全链路」缩小到「一个数字」**——group_id 拿到后可缓存，
其余三步都是结构化 JSON。

**增量判定改用 `content_updated_at`**（语雀自己给的时间戳），比正文 hash 直接。

正文是语雀 Lake HTML，用 `markdownify` 转 Markdown——标题层级得以保留，
切分时的 `heading` 溯源信息全靠它。

**实测目标空间**：`wdterpqjb` = 「旗舰版ERP」，group_id `22819707`，
**14 个公开知识库 / 807 篇**（群组声称 19 个，匿名只能见 14 个）。

工程要点（这类爬虫成败全在细节）：

- **限速 1–2 req/s** + 抖动；正常 User-Agent，别用默认的 `python-requests`
- 失败**指数退避重试** 3 次；单篇失败记日志继续，不中断整库
- **增量靠正文 content hash**（公开页无可靠 `updated_at`）——hash 没变就跳过 embedding，这是省钱关键
- **解析器隔离在单文件** + 固化 HTML 样本回归测试。语雀改版时只有这个文件会碎，测试立刻告诉你碎在哪

**目标知识库**：`https://www.yuque.com/wdterpqjb` —— 「旗舰版ERP」，已确认可公开访问、无需登录。M2 开工时先跑一遍 `fetch_toc` 摸清文档规模，再决定要不要分批同步。

### 2.6 数据隔离（本项目最不能出的 bug）

```sql
owner_id IS NULL          -- 公共库（语雀），所有登录用户可见
owner_id = '<user_id>'    -- 私有库（用户上传），仅本人
```

检索时统一：`WHERE owner_id IS NULL OR owner_id = :current_user`

**红线：这个条件只允许在 `retrieve.py` 一处出现**，绝不允许调用方自己拼 SQL。配测试守住——用户 A 检索绝不能命中用户 B 的文档。

---

## 三、目标结构

```
Copilot/
├── backend/
│   ├── pyproject.toml          # uv，requires-python = ">=3.12,<3.13"
│   ├── .env.example
│   ├── alembic/                # 数据库迁移
│   └── src/copilot/
│       ├── config.py           # pydantic-settings
│       ├── cli.py              # ingest / ask / sync-yuque / invite / serve / worker
│       ├── db/
│       │   ├── models.py       # SQLAlchemy 七张表：User/InviteCode/Document/Chunk/Job/Conversation/Message
│       │   └── session.py      # async engine
│       ├── providers/
│       │   ├── base.py         # Embedder / Reranker 的 Protocol
│       │   ├── siliconflow.py  # bge-m3 + bge-reranker-v2-m3
│       │   └── llm.py          # OpenAI 兼容流式（DeepSeek / 通义 / KIMI）
│       ├── sources/
│       │   ├── yuque.py        # ⭐ 语雀 JSON 接口链路 + 限速 + 重试
│       │   └── sync.py         # 落成带 frontmatter 的 Markdown + 增量台账
│       ├── ingest/
│       │   ├── parsers.py      # 上传文件 → Markdown（md/txt/docx/pptx/pdf，轻量库）
│       │   ├── chunker.py      # 中文切分，带溯源元数据
│       │   └── pipeline.py     # 切分 → 嵌入 → 写 pgvector；⭐ write_chunks 是写 owner_id 的唯一处
│       ├── retrieve.py         # ⭐ 隔离过滤唯一收敛点；检索 → rerank → 带编号引用
│       ├── qa.py               # ⭐ 防幻觉双闸门 + is_no_answer()
│       ├── agent/              # （M7）
│       │   ├── agent.py        # Pydantic AI Agent 定义
│       │   └── tools.py        # search_kb / collect_requirement / gen_checklist / export_excel
│       ├── auth/
│       │   ├── security.py     # bcrypt（SHA-256 预摘要）+ JWT + 邀请码生成
│       │   ├── invites.py      # 发码 + 原子核销
│       │   └── deps.py         # FastAPI 依赖：CurrentUser / SessionDep / cookie 读写
│       ├── jobs/
│       │   ├── queue.py        # ⭐ SKIP LOCKED 取放 + 状态机 + 僵尸任务回收
│       │   └── worker.py       # 独立进程的消费循环（优雅停止）
│       └── api/
│           ├── app.py          # FastAPI 装配 + lifespan + CORS
│           ├── stream.py       # ⭐ AI SDK 协议编码器（字段名错一个前端就空白）
│           ├── schemas.py      # 请求 / 响应模型
│           ├── providers.py    # 进程内共享的 embedder / reranker / llm
│           └── routes/
│               ├── auth.py     # 注册（验邀请码）/ 登录 / 登出 / me
│               ├── chat.py     # ⭐ SSE 流 + 会话历史
│               └── docs.py     # 上传 / 列表 / 删除　（M6）
├── frontend/                   # Next.js 16，output: 'export'
│   ├── next.config.ts          # ⭐ output: 'export' + trailingSlash（本机构建，产出 out/）
│   ├── app/
│   │   ├── page.tsx            # 分流：登录了去 /chat，没登录去 /login
│   │   ├── login/ · register/  # 注册页填邀请码
│   │   ├── chat/page.tsx       # useChat + 侧栏历史
│   │   └── documents/page.tsx  # 上传（拖拽）+ 解析状态轮询 + 删除
│   ├── components/
│   │   ├── chat/               # chat-view / message-list / composer / citations / sidebar
│   │   └── ui/                 # shadcn/ui
│   └── lib/
│       ├── api.ts              # ⭐ 所有请求 credentials:"include"；API_BASE 是构建期常量
│       ├── auth-guard.ts       # 客户端路由守卫（静态导出无 middleware）
│       └── chat-types.ts       # data-citations / data-conversation 的类型
├── deploy/
│   ├── nginx.conf              # / → out/ 静态；/api → FastAPI；/aura → Aura Note 保留
│   ├── copilot-api.service     # systemd: uvicorn（MemoryMax=600M）
│   ├── copilot-worker.service  # systemd: 解析 worker（MemoryMax=400M，优雅停止）
│   ├── setup-server.sh         # swap + postgres + pgvector + certbot 一次性初始化
│   └── deploy.sh               # 本机构建 out/ → rsync 上传 → 重启服务
├── eval/
│   ├── dataset.yaml
│   └── run.py
└── tests/
    ├── test_yuque_parse.py     # 固化样本，防语雀改版静默失效
    ├── test_chunker.py         # 切分策略
    ├── test_isolation.py       # ⭐ 跨用户隔离
    ├── test_auth.py            # 密码哈希 / JWT / 邀请码
    ├── test_stream_protocol.py # ⭐ AI SDK 协议字段固化
    ├── test_api_auth.py        # 注册 / 登录 / 登出 端到端
    ├── test_api_chat.py        # ⭐ SSE 序列 + 「不知道就不挂来源」
    ├── samples.py              # 造真 docx / pptx / pdf 样例（含扫描件 PDF）
    ├── test_parsers.py         # 编码兜底 / 表格不跑位 / 扫描件要报错
    ├── test_jobs.py            # ⭐ SKIP LOCKED + 重试语义 + worker 端到端
    └── test_api_documents.py   # ⭐ 上传安全项逐条 + 跨用户删不掉
```

---

## 四、进度总览

| 阶段 | 里程碑 | 状态 |
|---|---|---|
| 地基期 | M0 地基 / M1 检索内核 / M2 语雀入库 | ✅ |
| 上线期 | M3 认证+聊天 API / M4 Next.js 前端 / **M5 上线** ⭐ | ✅ |
| 增强期 | M6 上传+私有库 / M7 Agent 化 / M8 评测+优化 | ✅ |
| 运维期 | M9 删除+勘误+图片 | ✅ |
| 收敛期 | M10 全 Agent 化 / M11 可运维化 / M12 常识兜底 / M13 可靠性硬化 | ✅ |
| 企业化 | M14-A 空间与隔离 / M14-B ImageAsset / M15-A 只读管理台 | ✅ 2026-08-23 上线 |
| | M16 答案纠错与标准答案 / M17 嵌图解析 | ✅ 2026-08-23 上线 |
| | M19-A 评测契约与跨空间门禁 | ✅ 2026-08-24，门禁 7/7 |
| | M18 企业版首次导入 | ⏸️ 2026-08-30 移除，转做旗舰版单独上线 |
| | M19-B 评测中心 / M20 路由收敛 | ⬜ 推迟 |

每个里程碑的详细任务、验收和排查记录在 [ARCHIVE.md](ARCHIVE.md)。

---

## 五、详细任务台账

已移到 [ARCHIVE.md](ARCHIVE.md)：M0–M13 的逐项任务、验收命令、每一次排查和 A/B 的证据，
以及 M14–M20 的全部执行记录。**还没做的事在这份文件上半部分。**

---

## 六、风险登记

按「会不会拖垮进度」排序，不是按技术难度。

| # | 风险 | 影响 | 概率 | 预案 |
|---|---|---|---|---|
| R1 | **语雀改版**导致 `appData` 解析失效 | M2 停摆，公共库无法更新 | 中 | 解析器隔离在单文件 + 固化 HTML 样本回归测试，碎了立刻知道碎在哪；备用 `catalog_nodes` 接口 |
| R2 | **1.6GB 内存 OOM** | 线上服务被 kill | 中 | swap 兜底；永不在服务器 build；永不加载 ML 模型；上线后盯 `free -h` |
| R3 | **检索效果不达预期** | 整个产品价值不成立 | 中 | M1 就要发现，不要拖到 M8。地基期超 8 天答不准就停下来查 chunk 策略 |
| R4 | **跨用户数据泄漏** | 最严重，信任归零 | 低 | 过滤条件单点收敛 + `test_isolation.py` + M6 线上换账号实测 |
| R5 | SiliconFlow 免费额度取消或限流 | embedding/rerank 中断 | 低 | Provider 是 Protocol，切通义/百炼是改配置；付费版 ¥0.07 也不贵 |
| R6 | **战线拖长导致弃坑** | 第三次失败 | **中高** | M5 压在第 15 天；每个里程碑独立可验收；卡住超 1 天就跳过去做不依赖的部分 |
| R7 | **DeepSeek 余额耗尽** | 全站问答直接 402，且报错看不懂 | **高**（2026-08-18 只剩 ¥2.13） | 及时充值；Kimi 已接成可切换 provider（改 `.env` 两行），但切之前要用评测集重跑一轮 |
| R8 | ~~**数据库零备份**~~ | 3 个真实账号、私有文档、会话历史、邀请码全不可再生 | 中 | ✅ **2026-08-20 处理完**（M11 P0）。每天 pg_dump + uploads，留 14 份，本机来拉做异地；**恢复演练在生产备份上真跑通**（3 用户 / 750 文档 / 4572 块 / 向量检索）。备份失败会写 FAILED，`deploy.sh` 每次上线检查 LAST_OK 的年龄，超 48 小时判部署不通过 |

> **R6 是历史概率最高的那个。** 前两次都死在这里，不是技术问题。

---

## 七、贯穿始终的约定

1. **`data/`、`.env` 必须在 `.gitignore` 里**，M0 第一件事。用户上传的文档进了 Git 历史，清理成本极高。
2. **隔离过滤只有一处实现**，配测试守住。
3. **不用 PyMuPDF**（AGPL）。引入依赖前先看许可。
4. **每个里程碑过验收才往下走。** 前两次失败的根因就是终点太远、中途没反馈。
5. **不为假想需求先付工程费**：混合检索等评测证明它值得再加；Redis 等 Postgres 队列扛不住再上。
6. **密钥只在 `.env`**，服务器 `chmod 600`，永不进仓库。
7. **服务器上永不执行 `npm run build`**，永不加载 ML 模型。这两条是 1.6GB 的生死线。
8. **所有软件装 D 盘**（见「一·五」）。新增任何需要安装的东西前，先确认它的安装路径和缓存目录都指向 D。

---

## 八、端到端验证

```bash
# ── 本地 ─────────────────────────────
cd backend && uv sync
cp .env.example .env          # SILICONFLOW_API_KEY / DEEPSEEK_API_KEY / JWT_SECRET / DATABASE_URL
uv run alembic upgrade head

uv run copilot sync-yuque <URL>   # 期望：N 篇 / M 块
uv run copilot sync-yuque <URL>   # 再跑：期望 0 变更
uv run copilot ingest             # 期望：切分 + 向量化入公共库
uv run copilot ask "<有的问题>"    # 期望：答案 + [1] 来源链接
uv run copilot ask "公司年会在哪开" # 期望："知识库暂无此内容"
uv run copilot invite -n 3        # 发邀请码
uv run copilot serve              # 起 API，另开一个终端跑下面的 curl
uv run copilot worker             # 另起一个进程解析上传（--once 则清空队列就退出）
uv run copilot prune-junk         # 索引体检：有二进制垃圾块就报出来（--apply 才删）
uv run pytest && uv run ruff check

# ── 勘误层（M9）─────────────────────
uv run copilot correct 京东面单          # 搜文档 → 编辑器改 → 存成勘误文件
uv run copilot correct 某文档 --retire   # 整篇作废
uv run copilot corrections               # 列出所有勘误，过期的标黄
uv run copilot corrections --check       # 有过期就退出码非 0（deploy.sh 用）
uv run copilot ingest                    # 勘误在这一步生效
# ⚠️ 只 commit 不部署 = 本机对了、线上还是错的

# ── 评测（M8/M9，只在本机跑）───────────
uv sync --extra parse --extra eval    # ⚠️ uv sync 是声明式的，两组要一起列，
                                       #    否则后一次会把前一组卸掉
uv run python ../eval/run.py --check           # 只验检索，不花钱
uv run python ../eval/run.py --tag now
uv run python ../eval/run.py --compare v3-final now
# ⚠️ 单跑一个测试文件要带 -c：`pytest ../tests/x.py` 时 rootdir 变成 Copilot/，
#    读不到 backend/pyproject.toml 里的 asyncio_mode=auto，async 测试会全红
uv run pytest -c pyproject.toml ../tests/test_jobs.py

cd ../frontend && npm run dev  # 本机全链路
npm run build                  # 必须能产出 out/

# ── 上线前最后一步：.env 改两个值 ───────
# COOKIE_SECURE=true    （HTTPS 下 cookie 才存得住，同时关掉 /api/docs）
# CORS_ORIGINS=         （线上前后端同源，留空即可）

# ── 部署 ─────────────────────────────
./deploy/deploy.sh             # 本机构建 → rsync out/ → 重启 systemd

# ── 线上 https://liushun666.cn ───────
# 1. 邀请码注册 → 登录
# 2. 提问 → 流式输出 + 引用可点击跳语雀原文
# 3. 上传 md/docx → pending → done（`systemctl status copilot-worker` 看进展）
# 4. 提问命中新文档
# 5. 换账号 → 搜不到那份文档          ← 隔离的线上验证
# 6. "帮我做个实施方案" → Agent 追问 → 下载 xlsx
# 7. ssh 上去 free -h，剩余内存 > 200MB   ← 1.6GB 的健康线
#    （M5 起 Aura Note 已下线，别再去验 /aura；恢复办法在 deploy/nginx.conf 注释里）
```

---

## 九、开工前需要你提供

| # | 需要的东西 | 状态 | 最晚什么时候要 |
|---|---|---|---|
| 1 | SiliconFlow API Key | ✅ 已有（bge-m3 / bge-reranker-v2-m3 **免费额度**） | — |
| 2 | DeepSeek API Key | ✅ 已有 | — |
| 3 | 语雀知识库 URL | ✅ `https://www.yuque.com/wdterpqjb`（旗舰版ERP） | — |
| 4 | 服务器 | ✅ 2 核 / 1.6Gi / 40G，Ubuntu 24.04 | — |
| 5 | **容器身份查明** | ✅ 已查明并清理完毕（见第一节） | — |
| 6 | **SSH 访问方式** | ⬜ **M5 的唯一阻塞项**；不给的话我只写脚本、你自己执行 | **M5** |

**M0–M4 全部完成。** 只剩第 6 项待定，它决定 M5 由谁来按下执行键。

### 密钥存放

- 一律放 `.env`，`.env` 进 `.gitignore`，服务器上 `chmod 600`
- Gemini 仅本机可用（国内服务器访问不了 Google API），只用于 M8 的 LLM-as-Judge

**第一步产出**：本计划落成 `Copilot/plan.md` 作为进度台账，此后每完成一项就勾一个框。

---

## 参考

- [Pydantic AI](https://github.com/pydantic/pydantic-ai) · [Pydantic AI + FastAPI + pgvector 参考实现](https://github.com/serkanyasr/agentic_rag_project)
- [AI SDK Stream Protocol（自定义后端规范）](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol) · [Vercel 官方 FastAPI 流式模板](https://vercel.com/templates/next.js/ai-sdk-python-streaming) · [fastapi-ai-sdk](https://pypi.org/project/fastapi-ai-sdk/)
- [Docling](https://pypi.org/project/docling/)（MIT）· [PyMuPDF 许可](https://pymupdf.readthedocs.io/en/latest/about.html)
- [SiliconFlow Embeddings](https://docs.siliconflow.cn/cn/api-reference/embeddings/create-embeddings) · [定价（bge-m3 / reranker 免费）](https://siliconflow.cn/pricing)
- [pg_jieba](https://github.com/jaiminpan/pg_jieba)（中文 BM25，M8 可选）
- [语雀 appData 解析思路](https://cloud.tencent.com/developer/article/2239704)
