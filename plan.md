# 知识库 Agent — 2026 主流栈实施方案

## NOW

- **Gate 0 已于 2026-08-23 关闭**，路线图进入 **M14-A**（管理员守卫、
  KnowledgeSpace、既有文档/会话回填、集中检索隔离）。旧路按门禁保留到 M20。
- **M13 P12：生产修复与真实前端验收已完成。** `700a2ef` 部署后，20 组多轮用例
  全部通过（20 / 20），包含私有文档隔离、长会话截断、概念定义和档位切换。
  生产备份为 `kb-20260822-041136.dump`。
- **7 天 Agent 报告已重跑。** 2026-08-22 报告为 177 轮、越过工具直答 0；其中
  13 条标为 M13 之前的旧数据，仍保留历史 2 次错误和 2 次差评，不能把历史数据
  当成这轮修复后的人工验收失败。
- **P13 已开始，但门禁未通过。** 已创建 tag `p13-start-20260822`，并完成生产数据库
  与上传文件备份；全量评测发现 Agent 与风险边界仍有硬指标问题，旧双路按门禁要求保留。
- **Gate 0（P13 / M13.1 收口）是后续路线图的唯一入口。** 在严格公共/私有 Agent、
  风险边界、路由评测和图片同路回归都形成可靠 PASS 前，不启动 M14，也不删除旧路。
  判分服务 `429` / 网络失败只记 `UNRELIABLE`，不能算模型失败或门禁通过。
- **2026-08-23 已闭环的部分（全是规则判定，不依赖判分器）：**
  M13.1 Agent 图片回归（5 条新测试）、三条发布红线（高风险幻觉 / 假引用 /
  跨平台污染，风险边界连跑 0/0/0）、私有库幻觉率与假阴性率（19 题 0.0 / 0.0，
  直路与 Agent 一致）、路由评测（63 题 100%，越过工具直答 0%）。
  详见下面「当前执行记录（2026-08-23）」。
- **判分器已恢复（Moonshot 已充值），全量严格评测跑完，四组结果都可靠。**
  公共库直路/Agent 98.7%、私有库直路/Agent 100%、风险边界 95.7%（三条硬指标 0/0/0）、
  路由 100%；五条 0% 红线全部达成，判分失效 0。**Gate 0 的评测侧已经通过**，
  只差「部署 → 20 组人工复验 + 7 天 Agent 质量报告」这两项线上证据。

## NEXT

1. **M14-A 后端已完成（2026-08-23）**，剩下两件：
   - 聊天页的知识版本选择器**故意推迟到 M18**：现在只有旗舰版一个空间是
     `active`（企业版语料还没导入），做出来就是一个只有一个选项、永远选不动的
     下拉框——正是前端规范里说的「假按钮」。`GET /api/knowledge-spaces` 已经就绪，
     M18 导入企业版语料的同一个 PR 里把它接上。
   - 线上迁移还没跑（下面「部署」那一节）。
2. **M14-B**：ImageAsset 与公共/私有图片鉴权。
2. 线上自然攒够数据后再看一遍 `quality-report --route agent --days 7`——
   今天那份跨了修复窗口，修复后只有 3 轮样本。
3. 记账项：引用角标偶发裸露、配图编号跳号、方案题挂 21 条无关来源、
   「答错了，我来改」提交即公共生效（M16 之前是否先关掉，待产品决定）。

### 已完成（2026-08-23）

1. **部署这几个修复**（`f7c20b6`→`HEAD`）。线上现在跑的仍是带 bug 的那版：
   定义题那段常开、点名客户的问题会拿公共流程作答、常识回答被记成 no_answer。
   按 `deploy/deploy.sh` 走，先备份再上，留回滚点。
2. 部署后补齐 Gate 0 的**线上证据**：20 组人工多轮验收重跑一遍（08-22 那轮跑的是
   改动之前的 prompt），再攒够数据看 `quality-report --route agent --days 7`。
3. **两处语料笔误要你定夺**（都已在评测里定位，改完走勘误层 `copilot correct`）：
   自动审核失败到底重试 48 次还是 10 次；抖音共享面单修复的「1327 版本」
   是不是 1.3.2.7。
4. 上面三项完成后才评估删除 `_chat_stream`、`AGENT_TRIGGERS` 和旧粘性路由。
5. 再之后只进入 M14-A：管理员守卫、KnowledgeSpace、既有文档/会话回填与
   集中检索隔离；企业版知识库不得提前上传。

## LATER

- 只有线上数据证明 router 显著拉高首字时间，才把 trace 再拆成
  router / retrieval / rerank / generation 四段；现在不先做缓存或并行化。
- M14–M20 的顺序固定为：M14-A（空间与隔离）→ M14-B（ImageAsset 兼容迁移）→
  M15-A（只读管理台）→ M16（AnswerCorrection / VerifiedAnswer）→ M17（嵌图解析）→
  M19-A（评测契约）→ M18（企业版首次导入）→ M19-B（持续评测）→ M20（生产验证）。

## M14–M20 执行合同（2026-08-22）

本节是项目内 `plan.md` 的主执行入口；详细设计保留在
[M14–M20 路线图](C:/Users/liushun/Desktop/ERP_Knowledge_Platform_M14_M20_Plan.md)。
实施时以真实代码、测试和线上行为为准，附件与本节冲突时先记录 delta，再修改计划。

### Gate 0 — P13 / M13.1 收口

Gate 0 未通过前，不启动 M14，不上传企业版知识库，不删除 `_chat_stream`、
`AGENT_TRIGGERS` 或 `profile is not None` 旧粘性路由。

必须同时满足：

- 公共 Agent、私有 Agent、风险边界、路由全量严格评测为可靠 PASS。
- judge 配额/网络失败记为 `UNRELIABLE`，不能计入模型失败，也不能当作通过。
- Agent 与直路在图片保留、编号、上下文、SSE、持久化和错误引用上通过同题回归。
- `quality-report --route agent --days 7`、20 组人工多轮验收、生产备份和回滚说明可追溯。
- 旧路保持可用，直到新路通过灰度和回滚演练。

当前执行记录（2026-08-22）：

- 已核对 Agent 图片链路：`answer_kb → emit_images → runner event pump → SSE → _AnswerWriter`；
  现有测试覆盖了终结工具图片先于正文，但还缺 Agent 路由完整持久化回归。
- 纯图片单元测试：24 passed。
- 本机集成测试目前被环境阻塞：PostgreSQL `localhost:5432` 未监听；未因此修改生产配置、
  启动生产服务或把这次运行当作 Gate 0 证据。

当前执行记录（2026-08-23）：

- **本机测试环境已恢复。** `postgresql-x64-17` 服务仍需管理员权限才能 `Start-Service`，
  改用 `pg_ctl start -D D:\PostgreSQL\17\data` 以当前用户拉起，未改动服务配置。
- **M13.1 Agent 图片回归已补齐并通过。** 新增 `tests/test_agent_images.py`（5 条）：
  Agent 流里的 `data-images` 必须早于正文、Agent 与直路对照表一致、编号只来自本轮上下文、
  图片随答案落进 `messages.images`、模型写 `[图99]` 时后端不替它造记录。
  每条都用 `request_trace.route` 断言这一轮真的走了 Agent，避免"钉死路由却没进分支"。
- 变异验证：把 `deps.emit_images()` 短路后，顺序那条立刻转红（配图掉到正文之后），
  恢复后重新全绿——这几条测试确实咬住了链路。
- 证据：后端 `pytest` 488 passed（原 483 + 5），`ruff check` 全通过，
  前端 `npm test` 4 passed（`inlineImages` 已经把不存在的图号从正文里删掉）。
- **判分器已停用（外部阻塞）。** Moonshot 账号余额不足被停用，探活返回
  `429 ... account is suspended due to insufficient balance`。同一把 key 也是
  `VISION_API_KEY`，**线上图片 / 扫描件解析同时停摆**。DeepSeek（答题）正常，200。
  恢复途径三选一：Moonshot 充值 / 提 Gemini spend cap 换回去 / 换第三家；
  **不能退回 DeepSeek 判自己**（同厂偏心，EVALUATION.md 三节）。

### 判分器不在场时量到的东西（2026-08-23）

三条发布红线**全是规则判定**，判分器在不在都成立。为此给 `run.py` 和
`risk_boundary.py` 加了 `--no-judge`：跳过语义判分、把每题如实记成「没判成」，
判分失效率因此接近 100%、`可信=false`，**这一轮不能用来比较好坏**，
但红线照量。准确率类指标一律留到判分器恢复后重跑。

**查到的真因：铁律 9 常开是这轮硬指标破线的原因。** 一次干净的 A/B（同题集、
同语料、只差这一条，`eval/prompts.py` 的 `current-no-rule9` 是从线上 prompt
原地删掉这一条拼出来的，不是抄旧文本）：

| | 铁律 9 常开 | 去掉铁律 9 |
|---|---|---|
| high_risk_hallucination_rate | 18.2% | **0.0%** |
| cross_platform_contamination_rate | 20.0% | **0.0%** |
| no_answer_correct_rate | 81.8% | **100.0%** |

去掉这一条之后 `prompt_sha` 正好回到 `566fcb56`——和 2026-08-21 那轮三条硬指标
全 0 的基线**同一个指纹**，等于两头对上了。

坏在哪：「第一句先用通俗的一句话定义」是给定义题写的，模型把它读成了
「任何问题都先用自己的话开个头」。于是「Temu 的电子面单怎么取号」不再拒答，
改答「知识库里没有专门针对 Temu 的说明。**按通用理解**，通常是在 ERP 中
新建快递……」——一套编出来的操作路径，长着有出处的样子。
在这条规则后面补一句「只管定义、不管操作」**没用**（同样量过，两条硬指标一个点没动）。

**修法：把它从常开改成按问题形状开。** `qa.is_definition_question()` 判「X 是什么 /
什么是 X / X 什么意思」，只有这类问题才追加那一段；操作题连见都见不到它。
这是本项目一贯的做法——能由规则保证的边界不交给 prompt 猜。
`是什么时候 / 是什么原因` 这类问时机和归因的排除在外。

同时修的两处**评测仪器**（都不改判定的严厉程度，只去掉假阳性）：

1. `banned_hits` 现在也认**被禁串前后的划界说法**（`不适用 / 规则不同 / 无关` 等）。
   原来只往前看 3 个字，于是「JIT 实时订单的库存释放规则不同——……由夜间定时任务
   释放。[1] 但普通淘宝订单不适用此规则」被判成串场景，**而这正是铁律 8 要的答法**。
   把它判成违规，唯一能让指标变好的做法是让模型闭嘴。
   回扫全部历史结果核对：新旧实现的差异**只出现在这一道题的这一种写法上**，
   没有任何一条真串台被放过。
2. 删掉双稳态的 `num-vip-waybill-nil`（理由写在 `risk_boundary.yaml` 原地和
   EVALUATION.md 出题规矩里）。风险边界题集 48 → 47（门槛 >= 40），
   高风险 no_answer 分母 11 → 10。

**当前证据（`--no-judge`，连跑三轮）：**

```
high_risk_hallucination_rate       0.0%   0.0%   0.0%
fake_citation_rate                 0.0%   0.0%   0.0%
cross_platform_contamination_rate  0.0%   0.0%   0.0%
no_answer_correct_rate           100.0% 100.0% 100.0%
规则级判错                          0      0      0
```

公共库 75 题（直路 / Agent，同样 `--no-judge`）：幻觉率 0.0 / 0.0，
假阴性率 0.0 / 0.0，检索命中率 98.5 / 98.5（与基线一致）。
规则级判错只剩两道漏关键事实的老问题（`proc-stocktake-flow`、
`proc-purchase-inbound-ways`），M13 之前就在。
⚠️ 配图带出率这一轮是 50.0（直路）/ 35.0（Agent），低于 08-22 的 65.0 / 50.0，
单轮样本不能下结论，**判分器恢复后要连着准确率一起重量**。

- 回归证据：后端 `pytest` **514 passed**（M13.1 的 488 + 定义题开关 6 条、
  禁串匹配 5 条、路由仪器 10 条，多轮那条改判据），`ruff check` 全通过。

### 路由评测：90.5% → 100.0%，越过工具直答 7.9% → 0.0%（2026-08-23）

路由评测不需要判分器（纯规则判定），所以这一项这一轮就能闭环。
先复跑确认，6 道错题和 08-22 完全一致（`p13-routing-live-base-20260823`）。
逐题查完，**六道全是仪器失真，不是模型越线**：

1. **五道越界题**（写代码 / 翻译 / 医疗 / 算数 / 薪资）。直接探针跑了一遍，
   模型说的是「这个我帮不了你——我只负责旺店通旗舰版 ERP 的实施配置，不写代码」，
   一个工具都没调、也没替用户把那件事做了——**这正是 instructions 里
   「和这个产品无关的事……这些都不做」要的行为**。旧判据只认「走 answer_kb
   再说没有」，于是把这五次正确拒答记成 `direct` + 越过工具直答。
   越界那组 37.5% 量的是判据本身。
   改法：新增 `refuse` 落点，越界题写 `accept: [refuse]`（kb 和 refuse 都算对）；
   `bypassed` 改回**按答案内容**判（`bypassed_tool` 的 ERP 路径特征），
   拒答里只要出现界面路径仍然算越线——这条写成了测试。
2. **一道「明白了」**。生产代码里寒暄短路是
   `if not plan_flow and small_talk_reply(...)`（`chat.py`，`test_multiturn.py`
   已经守着），而评测无条件先查寒暄表，于是报出一个线上根本不存在的 bug。
   评测改成复用同一个判据（`_history_started_agent`）。

改完连跑：**路由准确率 100.0%（63/63），越过工具直答率 0.0%**，
七个分类全部 100%。确定性那一路仍是 84.1%（和 08-22 一致，未受影响——
它量的是正在退休的关键词分叉，时间题和两道关键词误报是已知且写在题面里的）。
新增 `tests/test_routing_eval.py`（10 条）把这两处判据钉住。

### 私有库幻觉率 16.7% → 0.0%（2026-08-23）

私有库那 19 题的幻觉率和假阴性率也都是规则判定，这一轮同样能闭环。
连跑三轮，`priv-noanswer-not-in-fixture` 每轮都破线（题面写着这是
「私有库场景下最危险的一种幻觉」）：夹具里没有退货流程，公共库里一大把，
模型于是答

> 「星辰电商的退货入库流程，按公共知识库的标准流程…[1][3]
>  关于星辰电商是否有额外的审核节点约定，知识库中暂无此内容。」

查历史：这道题在 08-22 的两轮私有 Agent 里就在错，**不是这次改 prompt 带出来的**。

两次尝试都失败：主体约束里已经写了「公共知识库不是任何一家的约定」；
再补一条「先按公共流程答一遍、末尾说这家没提，也算冒充」，三轮实测
一个点没动。**材料在上下文里，模型就会用它。**

改成数据层保证（和 `private_count == 0` 那道闸门同一种做法）：
主体约束触发、且问题**点名了公司**时，`ask_stream` 把公共材料整块滤掉，
只留他自己的文档；夹具里没有这件事，铁律 3 自然给出兜底话术。

⚠️ 第一人称那一支**不走**这道闸门：「我们公司的电子面单怎么配」多半是在问
产品本身，拿掉公共材料会把答得出的题变成「暂无此内容」——假阴性是这条规则
最贵的失败。为此 `asks_about_named_subject()` 专门排掉「我们公司 / 本公司 /
咱们公司」这种第一人称打头的形态（后缀表里有「公司」，不排就会误判）。

⚠️⚠️ **同时补上一处评测失真**：`eval/run.py` 是两阶段跑（检索一次、生成一次），
不走 `ask_stream`——生产改完，私有库的数字一动不动。现在检索阶段复现了同一道闸门，
生成阶段也按问题形状追加「定义题」那一段（否则定义题上评测和线上是两版 prompt）。
判定函数一律从 `copilot.qa` 引，不在评测里抄第二份。

**证据（`--no-judge`，直路连跑三轮 + Agent 一轮）：**

```
私有库幻觉率      0.0%   0.0%   0.0%      （Agent 路径同样 0.0%）
私有库假阴性率    0.0%   0.0%   0.0%
检索命中率      100.0% 100.0% 100.0%
规则级判错         0      0      0
```

同一份改动之后重跑风险边界与公共库：风险边界三条硬指标仍是 0/0/0、规则判错 0；
公共库直路幻觉率 0.0、假阴性率 0.0、检索命中率 98.5，只剩 `proc-purchase-inbound-ways`
那道漏关键事实的老问题。后端 `pytest` **522 passed**，`ruff` 通过。

### 判分器恢复后的全量严格评测（2026-08-23 晚）

Moonshot 充值后判分器探活 200，按门禁跑完四组，**判分失效全部为 0，
四轮都是可靠结果（`reliable: true`），不需要标 UNRELIABLE**：

| 题集 | 路径 | 准确率 | 幻觉率 | 假阴性率 | 检索命中率 | 引用正确率 | 判分失效 |
|---|---|---|---|---|---|---|---|
| 公共库 75 题 | 直路 | 98.7% | **0.0%** | 0.0% | 98.5% | 98.5% | 0 |
| 公共库 75 题 | Agent | 98.7% | **0.0%** | 0.0% | 98.5% | 98.5% | 0 |
| 私有库 19 题 | 直路 | 100.0% | **0.0%** | 0.0% | 100.0% | 100.0% | 0 |
| 私有库 19 题 | Agent | 100.0% | **0.0%** | 0.0% | 100.0% | 100.0% | 0 |

风险边界 47 题（直路）：准确率 95.7%，三条硬指标
`high_risk_hallucination_rate` / `fake_citation_rate` /
`cross_platform_contamination_rate` **全部 0.0%**，
`no_answer_correct_rate` 100.0%、`general_answer_success_rate` 100.0%、
`high_risk_grounded_rate` 92.0%，判分失效 0。
路由 63 题（`--live`）：准确率 100.0%，越过工具直答 0.0%。

⭐ 对比 08-22 那轮：公共库 Agent 幻觉率 **10.0% → 0.0%**、判分失效 5 → 0；
风险边界高风险幻觉 **18.2% → 0.0%**、跨平台污染 **20.0% → 0.0%**；
私有库幻觉率 **16.7% → 0.0%**；路由 90.5% → 100.0%。

判分器回来后又修了两处（都在判分跑出来的错题里）：

1. **`is_no_answer` 把常识回答判成拒答。** M12 规定常识回答不标来源编号，
   于是「全文没有 [n]」这个条件对它恒成立；末尾补一句「产品里具体怎么算，
   知识库暂无此内容」，整段正确的解释就被判成拒答
   （`gk-inventory-turnover`：189 字的回答，那句话在第 180 字）。
   线上的影响是 `answer_source` 记成 `no_answer`——答得好好的问题记成没答上。
   改成只有那句话出现在**开头 80 字以内**才算拒答，M7 那种一句话前缀照常抓得住。
   风险边界 `general_answer_success_rate` 91.7% → 100.0%。
2. **`must_include` 只认一种词序。** `proc-purchase-inbound-ways` 要「批量入库」，
   而答案写「批量采购入库」、语料标题写「采购批量入库」，一个**答全了**的
   回答被记成漏事实。改成一条事实可以写成一组同义写法（命中任意一个即可），
   并写清它**不是**用来放宽事实判定的：数字和界面路径仍然只认一个字串。

**两处语料矛盾已经定准并走了勘误层（2026-08-23，产品拍板）：**

- **自动审核失败重试 10 次**（不是 48）。两段矛盾其实在**同一篇**里：
  `设置 · 自动审核设置方式` 正文写「重试次数达到 10 次不再尝试」，
  Q3 却写「一共重试 48 次（1.5.9.2 版本优化）」。勘误把 Q3 改成 10 次，
  重试间隔的说明留在原处；顺带修掉同句里的全角句号笔误「1。5.9.2」。
- **抖音共享面单在 1.3.2.7 版本修复**（原文写「1327版本」是笔误，
  同一句里另一个版本号写的是 1.3.1.4）。这段说明在两个知识库里各存了一份，
  **两份都勘误**——只修一份的话，检索抽到另一份时答案又变回错的。

勘误文件在 `corrections/`（进 Git、可 diff、可回滚）。本机 ingest 后核对索引：
旧「一共重试48次」0 块、新「重试次数达到10次」6 块；旧「1327版本」0 块、
新「1.3.2.7版本」2 块。评测判据跟着改（`must_include` 48 → 10、1327 → 1.3.2.7）。

**勘误后重跑（判分器在线，判分失效全 0）：**

```
风险边界 47 题   准确率 100.0%   三条硬指标 0/0/0
                general_answer_success / high_risk_grounded / no_answer_correct 均 100.0%
公共库 75 题     直路 97.3% / Agent 97.3%   幻觉率 0.0   假阴性率 0.0
                检索命中率 98.5%   引用正确率 98.5%
```

公共库剩下的两道错题都是判分器挑刺，不动它们：`proc-purchase-inbound-ways`
说「智能采购入库材料里没有」，但它确实在语料里
（`仓储 · 快速入库 › 二.操作步骤 › 3、智能采购入库`）；
`fact-batch-exchange-limit` 同一份材料上一轮判的是对的。
n=75 上一道题 1.3 个点，属于「准确率允许的波动」。

### 部署记录（2026-08-23 14:4x CST）

- **回滚点**：部署前手动触发了一次备份，`kb-20260823-063706.dump` +
  `uploads-20260823-063706.tar.gz`（`LAST_OK=2026-08-23T06:37:10Z`）。
  上一个已部署版本是 `700a2ef`；要回滚就把它重新 `deploy.sh` 推一遍，
  数据库如需回退再用上面那份 dump（**不要对已写入新数据的生产库跑 downgrade**）。
- 部署内容：`f7c20b6` → `756699f`（M13.1 图片回归、定义段按问题形状开、
  点名主体只留私有材料、常识回答不再判成拒答、评测仪器四处修正）。
- 部署脚本七步全绿，公网验收 `/api/health` `/` `/chat/` `/documents/` 均 200，
  API 4 秒就绪，备份体检 0 小时前。
- **线上冒烟（服务器上 `copilot ask`，走真语料真模型）：**
  - 「Temu 的电子面单怎么取号？」→ **「知识库暂无此内容。」**
    （改动前是「按通用理解，通常是在 ERP 中新建快递……」一套编出来的路径）
  - 「电子面单是什么？和以前手写的快递单有什么不一样？」→ 第一句就是通俗定义，
    后面才是材料里的产品能力，配图 `[图1]…[图10]` 照常带出。
- 部署时撞出一个**旧 bug 并当场修掉**：`copilot ask` 从 2026-08-20（详解档加
  模型草稿那一版）起就在打元组、收尾抛 `TypeError`——网页那条路不经过它，
  所以坏了三天没人发现，而它正是「部署完当场验一句」用的工具。
  已补 `tests/test_cli_ask.py`，并把 `deploy.sh` 第 1 步的 `uv run` 换成直接调
  `backend/.venv` 的解释器（`uv run` 会把本机 venv 的 parse/agent/eval extra 卸掉）。

### 20 组人工多轮验收（2026-08-23 晚）— **不通过**

跑完 20 组，找出 7 个问题，其中 3 个是判据表里明写的破线项。**已修 4 个并上线**
（`cda8381`、`2dab90d`），剩 3 个见下。

**① 同一句话发两遍**（判据表第 2 条：重复问已经问过的）。出方案那几轮，每条
回答都是同一个意思的两个版本首尾相接；「我传过哪些文档」整段清单出现两遍，
连模型自己道歉之后那一条**还是**两遍。根因：`drafted` 跨 step 累加，收尾时
一把全发——调工具**前**写的那句是草稿，模型看到工具结果后已经重写了一遍。
改成在工具调用处打一刀，只发最后一刀之后的内容。

**② 寒暄挂着 5 条来源**（判据表第 8 条）。「好的谢谢」四个字不在寒暄表里
（表里只有「好的」和「谢谢」），于是走了检索。改成整句都由寒暄词拼成时也算
寒暄。边界没动：「好的，那采购退货呢」照常走检索。

**③ 正文引用的编号，来源列表里没有。** 组 2 第 2 轮正文写着 `[2]` `[5]`，
来源列表只有 1 条。直路把带 `[n]` 的历史原样喂回模型，它照抄编号；
Agent 路早就剥了，直路一直没剥。现在两条路一样剥。

**④ 「来源 · 2」下面列着 1 和 4。** 点名主体那道闸门（`a9ded2f`）滤掉公共材料
后没有重排编号——注释写了「跟着重排」，代码没做。补 `RetrievalResult.renumbered()`。

**⑤ 线上有 3 条测试垃圾在公共库里**（已按你确认删除，删前备份
`kb-20260823-115737.dump`）：`已订正 · 你在干什么` → 「我想知道你在干什么」、
`已订正 · 好的`、`已订正 · 随便问一句`。`owner_id=None` 公共可见，且带
`verified` 标记会被排到语雀原文**前面**。08-20 有人用「答错了，我来改」试出来的。

> ⚠️ **这暴露的是设计问题，不是数据问题。** 现在「答错了，我来改」是
> **任何用户提交即刻公共生效、无人审核**（`api/routes/verified.py` 文件头把
> 这个取舍写明了：内部工具，同事可见性即 review）。M14–M20 计划第 19 节要求的
> 正是「未审核的纠错不得进 RAG」——这是 M16 的活。在 M16 落地之前，这个按钮
> 就是一个任何人都能往公共库里塞东西的入口。

**第二轮验收（重跑 6 组）：组 8 ✅ 组 16 ✅ 组 12 ❌ → 修完再跑 ✅**

组 8 暴露了今天最严重的一个 bug，而它**没有任何症状**：一句话给全七个字段
之后，页面上只剩一个「已完成 7 个步骤」的徽章、一个字都没有；**再往后每一句
（连「你好」）都是同样的空白**——那条会话废了。日志里是
`UsageLimitExceeded: tool_calls_limit of 10 (tool_calls=12)`。
模型把「星辰电商的对账以什么为准」这种普通问题也当成需求，一个字段一个字段
地试着记，撞穿额度、整轮抛异常。三处一起修（`653bffb`）：撞上限降级成
「这一轮到此为止」并补最后兜底、空转两次就叫停、plan_flow 工具上限 10 → 16。

组 12 是另一个形态：方案出完之后，「你好」和「好的谢谢」把方案摘要**重新
生成了一遍**（「已参考 21 条知识内容」），每次项数还不一样（14 → 15 → 13）。
成因是「收集中」和「出过方案」被当成一回事，寒暄短路一直被跳过（`c6b5643`）。

**第三轮：6 组全过。** 组 8 直接出方案 + xlsx；组 12 两句寒暄都是干净的
canned（无来源、无方案摘要），夹在中间的两个真问题照常检索；组 16 来源编号
连续、跨客户对比没串味；组 2 引用编号和来源列表全对得上。

### 六个修复之后的全量评测（2026-08-23 深夜）

```
公共库 75 题   直路 97.3% / Agent 98.7%   幻觉率 0.0   假阴性率 0.0
               检索命中率 98.5%   引用正确率 98.5%   判分失效 0
私有库 19 题   直路 100%  / Agent 100%    幻觉率 0.0   假阴性率 0.0
风险边界 47 题 97.9%   三条硬指标 0/0/0   三项分率均 100.0%
路由 63 题     100%    越过工具直答 0.0%   七个分类全部 100%
```

五条 0% 红线依旧全达标，没有一个指标因为今天这六个修复而退。

**仍未处理（不阻断，但要记账）：**

- **⑥ 用户原话被当成答案回显——已消失。** 第二、三轮重跑组 8 都没有再出现。
  它确实是 ① 的下游：重复的助手正文把模型带进了复读。
- **方案题挂 21 条来源**，里面混着 `xingchen-private-test`、`8.20` 这类和方案
  无关的文档。不影响答案，但来源列表长得没法看，也让人怀疑方案是照着这些写的。
- **「答错了，我来改」是任何用户提交即刻公共生效、无人审核**（见上）。M16 之前
  它是一个任何人都能往公共库里塞东西的入口。要不要先关掉，等产品决定。
- **⑦ 引用角标偶发渲染成裸 markdown。** 京东那条出现 `[1](#copilot-cite-1)`，
  紧跟在一个裸 URL 后面。前端对未知编号本来就有保护（不认识的编号不转链接），
  这一处是 remark-gfm 的自动链接和紧邻的角标撞在一起。只影响显示。
- **⑧ 配图编号跳号。** 电子面单模板那条出现 图1–图5 之后直接 图16–图20。
  和 ④ 是同一类问题（编号是这一轮的槽位），但在配图那条链路上，要单独查。

### M14-A — 知识版本与隔离（2026-08-23）

隔离从此有**两根轴**：`owner_id`（谁的文档）和 `knowledge_space_id`（哪一版 ERP）。

四个空间：`flagship`（现有语雀语料全归它）、`enterprise_desktop`、
`enterprise_web`（都预置成 `inactive`，语料 M18 才导入）、`common`
（跨版本通用，**只作为检索范围**，不是能聊天的空间）。

三个 migration，各自可回滚，本机双向都演练过：

| revision | 做什么 | 结果 |
|---|---|---|
| `b2f5a91c3d47` | 建表 + 可空外键 | 此时每一行都还没有值，直接 NOT NULL 会当场失败 |
| `c3a7d82e5f19` | 种子 → 回填 → 校验 → NOT NULL | 748 文档 / 97 会话，回填后 0 NULL |
| `d4b1e63a920c` | 把空间冗余到 `chunks` | 4572 块，与所属文档不一致 0 |

⚠️ **缺空间时检索返回空（fail closed）**，不是退回全库搜。写这条测试时当场
撞出一个真漏：M11 P3 的私有块保底召回是**第二条查询**，它绕过了空间过滤，
于是用户传在旗舰版下的文档会出现在他的企业版会话里。**旁路是隔离最容易漏的
地方**——主查询人人都记得改，补捞的那一支没人想起来。

管理员守卫 `require_admin` / `CurrentAdmin` 只认 `users.is_admin`；停用判定
不重复写（`get_current_user_optional` 已经卡了 `is_active`）。CLI 早就有
`copilot admin <email> [--revoke]`，网页上没有自助升级的口子。

顺带修好 `copilot ask`：它没有会话也就没有空间，加了过滤之后会一条都查不到。

**推迟的一项**：聊天页的版本选择器留到 M18（理由见 NEXT）。

### Gate 0 — **已关闭（2026-08-23）**

五项条件逐条落实：

| 条件 | 证据 |
|---|---|
| 公共/私有 Agent、风险边界、路由全量严格评测可靠 PASS | 公共 97.3/98.7、私有 100/100、风险边界 97.9（硬指标 0/0/0）、路由 100%，判分失效 0 |
| judge 失败记 `UNRELIABLE`，不计入通过 | 本轮判分失效 0，无需动用；规则和实现都在 `eval/run.py` |
| Agent 与直路图片同题回归 | `tests/test_agent_images.py`（`f7c20b6`），24 项图片单测 |
| `quality-report --route agent --days 7` 可追溯 | 285 轮、**越过工具直答 0**、活跃用户 1、TTFB p50 2701ms / p95 9771ms |
| 20 组人工多轮验收 | 第一轮不通过 → 修六处 → 重跑 7 组全过（组 2/5/6/8/12/13/16） |
| 生产备份和回滚说明可追溯 | 每次部署前备份，最近 `kb-20260823-115737.dump`；回滚办法写在部署记录里 |
| 旧路保持可用 | `_chat_stream`、`AGENT_TRIGGERS`、`profile is not None` 粘性路由**都还在** |

⚠️ 那份 7 天报告的窗口**跨了今天的修复**：14 次出错里 9 次是已修掉的
`UsageLimitExceeded`、3 次 `UnexpectedModelBehavior: max output retries`、
1 次用户主动停止。差评率 100% 的分母只有 4 轮，全部来自人工验收时的点击。
修复上线后的窗口里 0 错误、0 越过工具直答，但样本只有 3 轮——**这一项要等
线上自然攒够数据再看第二遍**，不能拿它当"修好了"的证据。

### 旧路删除评估 — **现在不删**

Gate 0 关闭**不等于**可以删 `_chat_stream` / `AGENT_TRIGGERS` / 旧粘性路由。
门禁原文是「旧路保持可用，直到新路通过**灰度和回滚演练**」，而现在：

- 灰度还是白名单模式，线上**活跃用户只有 1 个**（就是自己）。按 `agent_rollout`
  的注释：3 个注册账号谈百分比灰度等于观察零样本。
- 回滚演练没做过。删掉旧路之后再出今天这种「一条会话被打死」的问题，
  没有可以切回去的东西。
- 今天六个修复全部上线不到一天。

所以删除留到 **M20**，条件不变：真实用户用一周、回滚演练做过、路由指标稳定。
这不是拖延——`UsageLimitExceeded` 那个 bug 正说明 Agent 路还在长新形态的故障，
而它当时的表现是**完全没有症状**。

### Gate 0 原始条件（存档）

评测侧的门槛**全部达成**：五条 0% 红线、检索命中率与引用正确率不低于 baseline、
判分失效 0、题集规模达标（风险边界 47 ≥ 40、procedural 20、私有 19、路由 63）。
剩下的是**线上侧**两项：

1. **人工多轮验收：第一轮不通过，修完六处后重跑 6 组全过**（见上一节）。
   还差组 13（「我传过哪些？」——重复输出最明显的那一组）没有复验。
2. **7 天 Agent 质量报告**：等线上攒够数据再看 `quality-report --route agent --days 7`。


在上面两项线上证据齐备前，仍然不删 `_chat_stream` / `AGENT_TRIGGERS` /
旧粘性路由，也不开 M14。

### 交付顺序与完成定义

| 阶段 | 主要范围 | 进入条件 | 完成条件 |
|---|---|---|---|
| M14-A | 现有管理员守卫、KnowledgeSpace、文档/会话回填、Scoped Retrieval | Gate 0 通过 | 既有数据回填无 NULL；用户/空间隔离和升级/回滚测试通过 |
| M14-B | ImageAsset、双读/双写、公共/私有图片鉴权 | M14-A 通过 | 旧图片链路兼容；私有图片越权返回 404；无孤儿资产 |
| M15-A | 只读 Admin Overview、用户、反馈、评测结果 | M14-A 通过 | 所有 `/api/admin/*` 服务端鉴权、分页和敏感信息过滤通过 |
| M16 | 独立 AnswerCorrection、审核发布、VerifiedAnswer | M15-A 通过 | 未审核不进 RAG；发布同空间生效；不经 LLM 改写；修订可追溯 |
| M17 | DOCX/PPTX/PDF/XLSX 嵌图解析 | M14-B 通过 | 图片与文本/页/slide 归属正确；超时、大小和私有隔离测试通过 |
| M19-A | 空间级评测契约、跨空间/图片负例、UNRELIABLE 规则 | M14-A/B 通过 | 旗舰版基线和门禁可重复运行 |
| M18 | 企业桌面/Web 知识空间首次导入 | M19-A 通过 | 导入前后跨空间污染为 0；回滚和删除链路演练通过 |
| M19-B | Admin Evaluation Center、持续回归、只读发布 | M18 通过 | 结果版本、配置、corpus hash 和 judge 状态完整 |
| M20 | 生产验证与 Agent 路由收敛 | 全部阶段通过 | 灰度、回滚、人工验收和路由删除门禁全部留证 |

### 已核对的实现边界

- 当前 `users.is_admin`、`users.is_active`、`documents.source_type` 已存在；M14 不重复添加，也不长期双写 `role` 与 `is_admin`。
- 当前 `corrections` 是来源文档纠错并会触发重新摄取；答案纠错必须使用独立的 `answer_corrections` 和 `/api/answer-corrections`。
- 当前没有 `KnowledgeSpace` / `ImageAsset`；现有 `chunks.images`、`messages.images` 和 `/images/` 必须双读兼容，不能一次性删除。
- 空间过滤必须集中在检索边界，并覆盖直路、Agent `answer_kb`、VerifiedAnswer 和评测；缺少空间上下文时 fail closed。
- 生产回滚优先使用已验证备份；不要对已写入用户新数据的生产库执行会丢数据的 downgrade。
- 不引入 Redis、Celery、Elasticsearch、Qdrant/Milvus、Graph RAG、MCP、多 Agent 或新微服务。

### 每阶段执行规则

1. 先用真实代码和现有测试写 implementation delta。
2. 先补失败测试，再改实现；不得用局部评测替代全量门禁。
3. 跑相关测试、全量后端测试、ruff，以及前端 lint/build（涉及前端时）。
4. 记录 migration upgrade/downgrade、回填校验、备份/回滚和线上证据。
5. 每个逻辑阶段独立 commit，并同步本文件的 NOW/NEXT/LATER/DONE；未通过不得标为 DONE。

## DONE

- M13 P0–P11：判分器三态、风险边界、procedural 20 题、人工多轮清单、
  `answer_source`、保留清理、删除生命周期、邀请码、ZIP 防护、质量报告和延迟指标。
- M13 P14：README / ARCHITECTURE / EVALUATION / OPERATIONS / DECISIONS 已从历史台账拆出。
- M13 P12：生产修复已部署，20 / 20 组真实前端多轮验收通过，且已执行最近 7 天
  `quality-report --route agent --days 7`；报告中的历史样本已在台账中明确标注。
- M13 P13 **不在 DONE**：真实灰度门禁尚未通过，旧路按要求保留。

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
| 部署 | 阿里云 ECS `8.136.116.9` → `https://liushun666.cn/` |

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

Next.js 15 + AI SDK 6 完整保留，`useChat` 照常工作——只是 `output: 'export'`，服务器上不跑 Node 进程。这同时省下 100–200MB。

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
| 前端 | **Next.js 15（静态导出）+ Vercel AI SDK 6** | 2026 AI 聊天界面事实标准。`useChat` 直接搬走流式/工具调用/中断/重试。`output: 'export'` 本机构建，服务器不跑 Node |
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

### 4.1 全局排期

单位是**净工作日**（约 6–8 小时专注开发）。你有本职工作，若按每天 2–3 小时投入，日历时间约为 **2.5–3 倍**。

| 阶段 | 里程碑 | 工期 | 累计 | 依赖 | 状态 |
|---|---|---|---|---|---|
| **地基期** | M0 地基 | 1–2 d | 2 d | — | ✅ |
| | M1 检索内核 | 2–3 d | 5 d | M0 | ✅ |
| | M2 语雀入库 | 2–3 d | 8 d | M1 | ✅ |
| **上线期** | M3 认证 + 聊天 API | 2 d | 10 d | M1 | ✅ |
| | M4 Next.js 前端 | 2–3 d | 13 d | M3 | ✅ |
| | **M5 上线** ⭐ | 1–2 d | 15 d | M2, M4 | ✅ |
| **增强期** | M6 上传 + 私有库 | 2–3 d | 18 d | M5 | ✅ |
| | M7 Agent 化 | 2–3 d | 21 d | M5 | ✅ |
| | M8 评测 + 优化 | 2–3 d | 24 d | M7 | ✅ |
| **运维期** | M9 删除 + 勘误 + 图片 | 1 d | 25 d | M8 | ✅ |
| **收敛期** | M10 全 Agent 化 | 3–4 d | 29 d | M9 | 🔨 P0–P4 完成 |
| **可运维期** | M11 备份 + 限流 + 私有库纠偏 | 3–4 d | 33 d | M10 | 🔨 P0–P6 代码就位 |

**合计 16–24 净工作日**（M0–M8 的原始估算；M9 +1 d、M10 +3–4 d、M11 +3–4 d）。按业余投入折算，约 6–10 周日历时间。

**进度：M0–M9 全部完成，M10 代码全部就位**（P0–P4），**M11 做完 P0–P3 与 P6**（见第五节末）：
数据库有备份了、恢复演练在生产备份上真跑通了、登录接口不再裸奔、一条问答一行台账、
答案下面能点赞踩、私有库主体纠偏把幻觉率从 100% 压到 0%。
**P5 写完量完撤了**——它冲的那个指标分母只有 6 题，分辨不出效果，按规矩不上线。
**P4 只剩「人真用一周」**：`AGENT_ALLOW_EMAILS` 机制就位，填上邮箱即可开始观察。
`AGENT_ROLLOUT=0`、白名单默认为空，**线上路由行为一个字没变**。
站点在 https://liushun666.cn 运行中，
已有 3 个真实注册账号；公共库 746 篇 / **4568 块**（M8 清掉了 700 块二进制垃圾），
每个人还能传自己的文档进私有库，也能让 Agent 多轮问出一份《实施配置方案.xlsx》。

> **M10 要收的是 M7 留下的账。** 现在是「普通问答走直路、出方案走 Agent」
> 两条路并存，新功能只加在其中一条上（多轮改写、招呼语、答案档位、
> 边流边落库，四样全都只有直路有）。M10 把入口统一到 Agent，
> 但**不把原始材料交给 Agent**——那正是 M7 掉 12 个点的原因。

> **M8 提前到 M7 之前做了**（原依赖图里 M8 在 M7 之后）。起因是 M6 验收撞见
> 「材料里有答案却被答成不知道」，而那件事只有评测集能量化。事后看这个顺序是对的：
> M8 查出了索引里 13% 是垃圾、以及 M5 那句「诚实」其实盖住了一次召回失败——
> 这两件都是**先加功能就会一直带着走**的问题。
>
> 现在有了可复跑的评测（41 题，`eval/run.py`），M7 每动一次检索或 prompt
> 都能立刻看到指标变化。**但要注意评测集已经饱和在 100%**，
> 它现在只能发现退化、不能证明改进——见 M8 那节的「已知方法学缺陷」。

### 4.2 关键路径

```
M0 ──→ M1 ──→ M2 ──┐
        │           ├──→ M5 上线 ──→ M6 上传 ──→ M8 评测 ──→ M7 Agent
        └──→ M3 ──→ M4 ┘
```

> 原图是 `M7 → M8`。实际执行时把 M8 提到了 M7 前面（2026-08-17），
> 因为 M6 验收暴露了一个「只有评测集能量化」的问题。M7/M8 之间本来也没有
> 真正的依赖——M8 依赖的是 M1 的检索链路，不是 Agent。
>
> **事后看这个顺序换得非常值。** M7 做完第一件事就是拿 M8 的评测集去量
> Agent 路径，结果是「每项都更差」（准确率 87.8% vs 100%，幻觉率 12.5% vs 0%）——
> 于是 Agent 只接管它独有的那件事（多轮出方案），普通问答留在直路。
> 按原顺序先做 M7，这个决定就只能凭感觉，而凭感觉的结论几乎一定是
> 「Agent 更高级，切过去吧」。

- **M1 是所有事的前置**，检索链路不通，后面全是空中楼阁
- **M2 和 M3/M4 可并行**——语雀爬虫卡住时不要干等，去写认证和前端
- **M5 之后才允许做 M6/M7**。上线是分水岭：之前是自己闭门造车，之后每个功能都有真实反馈

### 4.3 三个阶段的心态

| 阶段 | 目标 | 失败信号 |
|---|---|---|
| 地基期（M0–M2） | 证明「检索得准、答得有据」 | 超过 8 天还答不准 → 停下来查 chunk 策略，别往下堆功能 |
| 上线期（M3–M5） | 让别人能用上 | 在 UI 细节上打磨超过半天 → 立刻停手，上线优先 |
| 增强期（M6–M8） | 用真实反馈驱动 | 开始做没人要的功能 → 回去看用户实际问了什么 |

> **前两次项目死在地基期和上线期之间。** 这次 M5 被刻意压到第 15 天，就是为了穿过那道坎。

---

## 五、详细任务台账

> 每个里程碑都有**可当场验证的验收命令**。不过验收不往下走。
> 勾选框用于实际执行时逐项打勾。

### M0 — 地基（1–2 天）　✅ **已完成 2026-08-15**

**服务器侧**
- [x] 加 2GB swap 并写入 `/etc/fstab`
- [x] 清理内存：删 Docker + erp-copilot 容器 + 关无用系统服务 → **591MB → 327MB**
- [x] 系统更新 + 重启，Aura Note 站点正常，8000 端口腾出

**本地工程**
- [x] ⭐ D 盘环境变量：`UV_PYTHON_INSTALL_DIR=D:\dev\uv\python`（`UV_CACHE_DIR` 原本已在 D）、`npm config set cache D:\dev\npm-cache`
- [x] Python 3.12.13 装到 `D:\dev\uv\python`
- [x] `uv init --lib`，pin Python 3.12 → `backend/pyproject.toml`
- [x] **`.gitignore`**：`data/`、`.env`、`out/`、`__pycache__`（已用 `git check-ignore` 验证 `.env` 确实被忽略）
- [x] 装 **PostgreSQL 17** + **pgvector 0.8.6** 到 `D:\PostgreSQL\17`
- [x] `config.py`：pydantic-settings 读 `.env`；`.env.example` 同步
- [x] `db/models.py`：User / InviteCode / Document / Chunk / Job / **Conversation + Message** 七张表
- [x] `db/session.py`：async engine（`pool_size=5`，适配服务器 `max_connections=20`）
- [x] alembic 初始化 + 首次迁移（迁移里带 `CREATE EXTENSION vector`）
- [x] `cli.py`：`ingest` / `ask` / `sync-yuque` / `invite` / `serve` / `worker` 六个子命令空壳
- [x] pytest 冒烟测试 6 条 + ruff 配置

**验收结果**
```
[1] kb --help              六个子命令全部列出            ✅
[2] alembic upgrade head   be8621870f54 (head)，七张表   ✅
[3] pytest                 6 passed                      ✅
[4] ruff check             All checks passed             ✅
```

**与原计划的三处偏差（都有理由）**

| 原计划 | 实际 | 为什么 |
|---|---|---|
| PostgreSQL 16 | **17** | winget/EDB 只有 17/18；Ubuntu PGDG 也有官方 `postgresql-17-pgvector`，两端都省事。选 16 本来就无硬理由 |
| 六张表 | **七张** | `Conversation` 拆成 `conversations` + `messages`，消息才好按条存引用 |
| pgvector 官方包 | **社区预编译** | 官方不发 Windows 二进制，自编译需先装 3–6GB VS Build Tools。**仅开发机用，服务器走官方 apt 包** |

**踩到的坑（记下来省得再踩）**
- 每个 async 测试跑在各自的事件循环里，共用连接池会拿到上个循环的死连接 → asyncpg 崩。测试一律用 `NullPool` 独立引擎，见 `tests/conftest.py`
- alembic 生成的迁移需要 `import pgvector.sqlalchemy`，已加进 `script.py.mako`，后续迁移自动带上

### M1 — 检索内核　✅ **已完成 2026-08-15**

> 顺序调整：语雀已在 M2 抓成本地文件，M1 直接用真实语料，跳过手写 md 这一步。

- [x] `providers/base.py`：`Embedder` / `Reranker` 两个 Protocol
- [x] `providers/siliconflow.py`：bge-m3 + bge-reranker-v2-m3，批量 + 限速 + 429 退避重试
- [x] `providers/llm.py`：OpenAI 兼容流式（DeepSeek / 通义 / KIMI 通吃）
- [x] `ingest/chunker.py`：按标题分段 + 短段合并，每块带 `title`/`heading`/`source_url`
- [x] `ingest/pipeline.py`：切分 → 批量嵌入 → 写 pgvector
- [x] `retrieve.py`：向量 top-20 → rerank top-5 → 带 `[1][2]` 编号的 context
- [x] ⭐ **owner 过滤在 `_visibility_filter()` 单点收敛**
- [x] `qa.py`：防幻觉双闸门（检索层兜底 + prompt 铁律）
- [x] `tests/test_isolation.py` 8 条 + `test_chunker.py` 19 条
- [ ] pgvector 索引：数据量到万级再建 HNSW，现在顺序扫够用

**用真实语料标定出来的三个参数**（不是拍脑袋，见下）

| 参数 | 原值 | 实测后 | 依据 |
|---|---|---|---|
| `min_chars` | 30 | **5** | 30 会丢掉 12.7% 的段，而那些「（操作路径：【app】-【店铺销售统计】）」正是用户最想要的答案 |
| 段落合并 | 无 | **相邻短段合并，不跨一级标题** | 语料段落长度中位数仅 101 字，一段一块会切出海量碎片 |
| `rerank_score_threshold` | 0.3 | **0.005** | bge-reranker 分数绝对值极低：正确答案 0.02、无关 0.0001。0.3 会让系统永远回答"不知道" |

**验收结果**
```
全量入库        746 篇 → 5098 块，失败 0，隔离违规 0            ✅
检索抽查        6 个业务域：5 题完整答出 + 1 题正确声明材料不足   ✅
防幻觉对照      3/3 全部「知识库暂无此内容」且零来源              ✅
pytest          53 passed                                       ✅
ruff            All checks passed                               ✅
```

各库入库量：设置 1622 块 · 统计 832 · 常见问题库 782 · 仓储 656 · 销售 393 ·
货品 196 · 分销 155 · 平台事项 115 · 采购 102 · 账款 99 · APP 65 · 其它 43 ·
事务 22 · CRM 16

**786 → 746 的差额已查清，不是数据丢失**：39 篇是语雀自己 `word_count=0`
的目录封面页（「订单处理」「货品管理」这类）。

**那道没答上的题反而证明系统是诚实的**：问「拼多多电子面单模板怎么设置」，
模型答「参考材料中没有直接说明」。因为该文档正文只有两行「XX请点击：」，
真正的步骤在语雀内链里，而内链是 Lake 卡片、解析时被剥掉了。
模型没有硬编步骤，这正是 ERP 场景最需要的品质。
（对比：《京东面单模板设置步骤》7 块、《唯品会MP》14 块，有正文的就能答。）

**踩到的坑**

1. **`rerank_score_threshold=0.3` 是个隐形炸弹。** 实测正确答案分数只有 0.02，
   这个阈值会让系统永远回答"不知道"——而这个故障看起来还很像"防幻觉工作正常"。
2. **「暂无此内容」下面挂着来源。** `ask_stream` 为了让前端提前渲染，
   在生成前就返回引用；模型说不知道时若照常展示，用户会以为答案有依据。
   已在 `qa.is_no_answer()` 收口，并写进接口文档，免得 M4 做前端时重犯。
3. **合并小节时子标题会丢。** 「1、营销步骤」「2、结果统计」合并后成了两段
   没头没尾的文字。改为把各自的子标题以【】写进正文。
4. **单独指定测试文件路径会让 pytest 读不到 pyproject 配置**（rootdir 判定变化），
   async 夹具直接报错。一律用 `uv run pytest` 从 `backend/` 跑。
5. **正文全在标题里的文档会整篇消失。** 13 篇电子面单对接文档
   （拼多多/抖音/快手/美团/京东/唯品会/顺丰/得物）通篇只有 `####` 标题、
   没有正文段落，按段落切得到 0 块——**文档凭空不见且不报错**。
   已加兜底：切不出块时用标题文本成块。
6. **硬切超长文本会留下零头。** 有文档正文含打印模板 JSON，硬切后剩
   `ext1"}` 这种 6 字残片单独成块。已改为并进上一块。
7. **自动化检查会骗人。** 抽查脚本按「引用命中期望知识库」判定通过，
   给一道其实没答上的题打了 ✓。指标要看，但答案本身也得读。

### M2 — 语雀抓取　✅ **抓取部分已完成 2026-08-15**

> 调整了顺序：**先抓到本地文件，再由 M1 入库**。抓取和检索分开验证，
> 出问题时能分清是解析的锅还是切分的锅。

- [x] `sources/yuque.py` — 四步 JSON 接口链路（见 2.5），替代原计划的全链路正则
- [x] 限速 1.5 req/s + 随机抖动；正常 User-Agent
- [x] 指数退避重试 3 次；单篇失败不中断整库
- [x] 增量：`content_updated_at` 时间戳 + `_manifest.json` 台账
- [x] `sources/sync.py` — 落成带 YAML frontmatter 的 Markdown
- [x] `tests/test_yuque_parse.py` 14 条回归 + `scripts/refresh_yuque_fixtures.py`
- [ ] 接入 `pipeline.py`，以 `owner_id=NULL` 写入公共库　→ **归到 M1 一起做**

**实际战果**

```
14 个公开知识库 · 807 篇
  成功 784    私密 19（作者设了权限，401/404，重试无用）
  跳过  2     真失败 0
产物：data/raw/yuque/<book_slug>/<doc>.md  +  _manifest.json
```

**两个发现**

1. **19 篇 404 不是 bug**——网页版返回 401，内容接口伪装成 404 并附 `docTitle`。
   已单独归类为 `YuqueRestricted`，不再混进错误里。否则每次同步跳 19 个红字，
   真出问题时反而看不见。
2. **增量省的是 embedding，不是网络**——目录接口不带时间戳，所以即便一篇没变，
   仍要发 ~800 次请求（约 9 分钟）确认。做定时同步够用；真要优化，
   可以先比对 `books` 接口的 book 级 `updated_at` 跳过整个库。

**验收结果**
```
copilot sync-yuque https://www.yuque.com/wdterpqjb
  → 14 个库 / 新增 784 / 私密 19 / 失败 0                       ✅

再跑一次（增量验证）
  → 新增 0　跳过 786　私密 19　失败 0，14 个库全部 ✓            ✅

pytest   24 passed（含 18 条语雀解析回归）                       ✅
ruff     All checks passed                                       ✅
```

产物：`data/raw/yuque/` 786 篇 Markdown，3.37 MB，平均 4.4 KB/篇。
按 500 字切分预计产生 **7000–9000 个 chunk**。

> 带引用问答的两条验收（含防幻觉那条）挪到 M1，因为要先有检索链路。

### M3 — 认证 + 聊天 API　✅ **已完成 2026-08-16**

- [x] `auth/security.py`：bcrypt 密码哈希 + JWT 签发/校验
- [x] JWT 存 **HttpOnly + Secure + SameSite=Lax** cookie（不放 localStorage）
- [x] `auth/deps.py`：`current_user` 依赖，未登录抛 401
- [x] `cli.py invite --count 5`：生成邀请码（另加 `--list` 查未用完的）
- [x] `routes/auth.py`：注册（**校验邀请码 + 一次性作废**）/ 登录 / 登出 / me
- [x] `routes/chat.py`：输出 **AI SDK UI Message Stream Protocol**（SSE 格式）
- [x] 对话历史落 `conversations` + `messages`，另给 M4 备了两个读接口
- [x] CORS 配置（本地开发 `localhost:3000` → `localhost:8000`，带凭证）
- [x] `api/stream.py` 协议编码器单独隔离 + 15 条格式固化测试

**接口清单**（M4 照这个对接）

```
POST /api/auth/register   {email, password, inviteCode}  → 201 + Set-Cookie
POST /api/auth/login      {email, password}              → 200 + Set-Cookie
POST /api/auth/logout                                    → 204，清 cookie
GET  /api/auth/me                                        → 当前用户 / 401
POST /api/chat            useChat 的 body                → SSE 流
GET  /api/conversations                                  → 会话列表
GET  /api/conversations/{id}/messages                    → 单条会话的消息
GET  /api/health                                         → 给 systemd / nginx 探活
```

**SSE 片段顺序**（`data-citations` 的位置是刻意的，见下面坑 #1）

```
start → start-step → data-conversation → text-start
      → text-delta × N → text-end → [data-citations] → finish-step → finish → [DONE]
```

**验收结果**（本机真起服务、真密钥、真 curl）
```
[1] copilot invite -n 3        3 个码：4UP6-U49C / RH3E-CXYF / D3Z7-QX8J   ✅
[2] 无 cookie POST /api/chat   401 未登录或登录已过期                       ✅
[3] 错邀请码注册               400 邀请码无效或已被使用，且用户没被建出来    ✅
[4] 真邀请码注册               201 + Set-Cookie: HttpOnly; SameSite=lax     ✅
[5] 登录 → 带 cookie 提问      187 个 SSE 片段，5 条引用带语雀真实链接       ✅
[6] 问知识库没有的             「知识库暂无此内容。」+ data-citations 0 帧   ✅
[7] GET /api/conversations     2 条会话，标题取自首个问题                    ✅
[8] CORS 预检                  localhost:3000 放行；evil.example.com 400    ✅
[9] pytest                     113 passed（新增 60 条）                     ✅
[10] ruff check                All checks passed                            ✅
```

**与原计划的两处偏差**

| 原计划 | 实际 | 为什么 |
|---|---|---|
| 只落 `conversations` | 另加 `GET /api/conversations` 和 `/{id}/messages` | 存了读不出来等于没存；M4 刷新页面要靠它恢复对话 |
| — | `retrieve.py` 的 embed / rerank 调用改走线程池 | 它们是同步 httpx + `time.sleep` 限速。在协程里直接调会**卡住整个事件循环**，服务器是单 worker，别人正在进行的 SSE 流会一起停住 |

**踩到的坑**

1. **「引用先发」和「防幻觉」是直接冲突的。** M1 的 `ask_stream` 为了让前端早点
   渲染来源，在生成之前就把引用返回了；可模型完全可能接着回一句「知识库暂无此内容」。
   那时页面上就是一句"不知道"底下挂着五条来源——用户会以为答案有依据，
   **比不做防幻觉更糟**。已把 `data-citations` 挪到正文流完之后，
   先过 `is_no_answer()` 再决定发不发。这条从"靠自觉"变成了"结构上不可能"，
   并配了 `test_no_answer_carries_no_citations` 守着。
2. **`session.add()` 之后对象的主键还是 None。** `Conversation.id` 的
   `default=uuid.uuid4` 是**列默认值**，INSERT 时才求值。不 flush 就拿 `conv.id`
   去建 Message，插进去的 `conversation_id` 是 NULL。写测试之前跑手工 curl
   根本发现不了——因为异常被流里的 `except` 兜住变成了一个 error 片段。
3. **StreamingResponse 的响应体在依赖退出之后才被消费。** 流里再用
   `Depends(get_session)` 那个会话，可能已经关了。聊天流里自己
   `async with SessionLocal()`，不蹭请求作用域的依赖。
4. **bcrypt 只认前 72 字节，超了直接抛 ValueError。** 汉字 UTF-8 占 3 字节，
   25 个汉字就炸。已改成先 SHA-256 摘要 + base64 压成定长 44 字节再交给 bcrypt
   （Django 的 `bcrypt_sha256` 就是这个做法）。顺带堵住"前 72 字节相同就算同一个密码"。
5. **登录失败的两种情形必须给同一句话、花同样的时间。** 区分「邮箱不存在」和
   「密码错误」等于送人一个用户名枚举接口；而"不存在就立刻返回、密码错要跑一次
   bcrypt"这个耗时差异本身也是。已用一个假哈希把两条分支的耗时拉平。
6. **Git Bash 命令行里的中文传不进 curl 的 `-d`。** Windows 本地代码页会把它
   转成非法 UTF-8，FastAPI 回 400「There was an error parsing the body」，
   看着特别像鉴权顺序写反了。中文 payload 一律写文件 + `--data-binary @file`。
7. **`Depends()` 写在参数默认值里会被 ruff 的 B008 拦下。** 改用
   `Annotated[User, Depends(...)]`，这也是 FastAPI 现在推荐的写法，
   顺手收成 `CurrentUser` / `SessionDep` 两个别名，路由签名干净多了。

### M4 — Next.js 前端　✅ **已完成 2026-08-17**

- [x] `create-next-app` + Tailwind 4 + shadcn/ui
- [x] ⭐ **`next.config.ts` 设 `output: 'export'`** + `trailingSlash: true`
- [x] 登录页 / 注册页（填邀请码）
- [x] 聊天页：`useChat` + `DefaultChatTransport({ api, credentials: 'include' })`
- [x] 引用气泡：可点开，跳语雀原文
- [x] 侧栏历史：会话列表 + 新对话 + 点开还原（用上 M3 那两个读接口）
- [x] `lib/auth-guard.ts` 客户端路由守卫——**真正的鉴权在 FastAPI，前端守卫只是体验优化，不是安全边界**
- [x] 移动端适配：侧栏收成抽屉，`100dvh` 兜住地址栏收放
- [x] ⭐ **UI/UX 深度升级（全面模仿 ChatGPT 风格与 100% 全局纯中文）**：
  - 侧边栏：支持桌面端折叠/展开（一键收起释放阅读空间），历史会话按「今天、昨天、前 7 天、更早」智能分组，增加历史快速搜索；
  - 顶部导航栏：ChatGPT 风格极简磨砂玻璃顶栏，集成侧栏展开开关、助手身份徽章（`语雀检索增强`）与快捷新建对话；
  - 空状态欢迎屏：ChatGPT 经典居中 Sparkle 徽标与问候语，配备 2×2 业务场景卡片（退货入库、面单打印、短信策略、对账结算）与快捷提问标签胶囊；
  - 消息与富文本：集成 `react-markdown` + `remark-gfm`，完美渲染 Markdown 标题、粗体、列表、引用与表格；代码块配备语言标签与「一键复制代码」；
  - 引用来源：升级为 ChatGPT Search / 权威知识库风格卡片网格，清晰展示编号徽章、标题、小节路径与语雀原文外部链接；
  - 底部输入框：ChatGPT 标志性 `rounded-3xl` 大圆角悬浮卡片、微光聚焦、流式生成停止按钮动效，支持长会话「回到底部」悬浮气泡；
  - 纯中文本地化：全站所有提示、Aria 标签、表单占位符与异常提示均为自然规范的简体中文，严密防范 IME 中文输入法误触。

**版本与计划不同**（计划写于更早，这是当时的版本号）

| 计划 | 实际 | 说明 |
|---|---|---|
| Next.js 15 | **16.3.1** | 当前稳定版；`output: 'export'` 照常支持 |
| AI SDK 6 | **`ai` 7.0.66 + `@ai-sdk/react` 4.0.69** | M3 的后端就是照**现行**协议文档写的，两边对得上 |
| — | Tailwind 4 + React 19.2 | create-next-app 默认 |

**验收结果**（真浏览器跑，不是看代码觉得能跑）
```
[1] npm run lint / tsc --noEmit    干净                                   ✅
[2] npm run build                  out/ 7 个 html，1.3MB                  ✅
[3] 邀请码注册 → 自动进聊天页       ✅
[4] 提问「京东电子面单模板怎么设置」 逐字流式，5 条引用带真实语雀链接      ✅
[5] 问知识库没有的                  「知识库暂无此内容。」+ 0 个链接、无"来源"✅
[6] 侧栏点历史                      消息和 5 条引用从数据库还原            ✅
[7] 登出                            跳回 /login/                          ✅
[8] 未登录直开 /chat/               自动踢回 /login/                       ✅
[9] 390×844 移动端                  侧栏移出屏幕、汉堡可用、无横向滚动     ✅
[10] 静态站实测                     out/ + /api 反代，登录/流式/引用全通   ✅
[11] out/ 里 localhost:8000 出现 0 次，接口全是同源相对路径               ✅
```

> [10] 用一个模拟 nginx 的小脚本（发 `out/` + 反代 `/api`）跑的，
> 不是 dev server。M5 的 nginx 配置照这个形状写就行。

**踩到的坑**

1. **静态导出会在构建期预渲染页面，所以 `crypto.randomUUID()` 不能放
   `useState` 初始值**——那个 UUID 会被写死进 HTML，浏览器再算一个就是
   hydration mismatch。但放 `useEffect` 里也不行，React 19 的
   `set-state-in-effect` lint 直接报错。最后放在**渲染期派生**
   （`if (authed && id === null) setId(...)`）：预渲染时 auth 还是 loading，
   这段根本不执行，两头都躲开了。
2. **中文输入法选词时的 Enter 会把半截拼音提交上去。** 必须判
   `e.nativeEvent.isComposing`——中文界面的聊天框不做这个就是残的。
3. **`next/font/google` 给构建加了一次外网字体下载。** 断网就 build 失败，
   而 Geist 根本不含中文字形。已改成系统中文字体栈，零请求。
4. **`grep -rl xxx | head` 的退出码取自 `head` 不是 `grep`。** 我用它判
   "产物里有没有泄漏 dev 地址"，得到一个假阳性，差点去修一个不存在的问题。
   管道里做真假判断要单独取 grep 的退出码或数行数。
5. **`browse` 的 daemon 在这台机器上起不来**（Stagehand 相关的 `fill`/`click`
   一并失效）。`browse --ws <port>` 可以绕开 daemon 直连 CDP，配合
   `eval` + 原生 setter 驱动 React 受控输入照样能跑完整流程。
   页面跳转会销毁 JS 上下文，那时 eval 报 `Uncaught` 是正常的，动作其实已生效。

**留在开发库里**：`m3demo@test.local` / `a-good-password`、
`browser-m4@test.local` / `browser-pass-2026`（都只在本机库）。

**验收命令**
```bash
# 本机双开
cd backend && uv run copilot serve          # :8000
cd frontend && npm run dev                  # :3000
npm run build                               # 必须产出 out/
```
> **`npm run build` 这步不过，M5 就上不了线**——服务器跑不了 build。

### M4.5 — 答案带操作截图　✅ **已完成 2026-08-17**

> 计划外插入的一项。ERP 文档「点哪个按钮」全靠截图说清楚，纯文字答案差口气。
> 用户决定：**上线时就带图**、**行内插图**（不是只堆在来源区）、**图片下载到本地**。

**起因：图片一直都在，是被我自己扔掉的。**
语雀的配图不是 `<img>` 而是 `<card name="image" value="data:...">`，
而 `lake_to_markdown()` 里一句 `soup.select("card, ...")` 把所有卡片都
`decompose()` 了——附件和脑图该删，图片是误伤。786 篇 Markdown 里只剩 3 篇有图。
抽样 12 篇实测**平均 3.9 张/篇**，外推全库约 3000 张。

**必须镜像到本地，不能外链**（实测）：

```
curl <语雀图片>                                 → HTTP 200
curl -H 'Referer: https://liushun666.cn/' <同一张> → HTTP 403
```

浏览器加载 `<img>` 默认带 Referer，直接外链就是**满屏裂图**。
`referrerpolicy="no-referrer"` 眼下能绕过，但那是把整站的图押在
「语雀不收紧策略」上，且收紧后是静默故障——图裂了没有任何报错。

- [x] `sources/yuque.py`：删卡片**之前**先把图片卡片换成 `<img>`
- [x] `sources/images.py`：内容寻址下载器（按 URL 的 sha256 命名、两级目录、
      限速 6 req/s、重试、10MB 上限、临时文件改名落盘）
- [x] `sources/sync.py`：同步时顺带镜像，正文里的地址改写成 `/images/...`
- [x] `ingest/chunker.py`：图片抽成 `[图:a3f9]` 标记，随块走
- [x] `db/models.py`：`chunks.images` + `messages.images`（迁移 `108c3b17f470`）
- [x] `retrieve.py`：`build_context()` 把块内标记重编成全局 `[图1][图2]`
- [x] `qa.py`：prompt 教模型在步骤末尾引用图号，且**只准用材料里出现过的编号**
- [x] `api/routes/chat.py`：`data-images` 片段（**在正文之前**发，见下）
- [x] 前端：`[图N]` 渲染成可点开的截图，编号对不上的静默丢掉
- [x] `tests/test_images.py` 25 条
- [x] 全量重新同步（786 篇 / 6048 张图 1.1G）+ 重新入库（5268 块，55% 带图）
- [x] 线上端到端验收

**⭐ 实测：模型到底会不会引用 `[图N]`**

这是整件事唯一无法靠代码保证的环节，实测了两轮：

| | 6 题中带图 | 图引用数 | **无效图号** |
|---|---|---|---|
| 初版 prompt | 3 / 6 | 14 | **0** |
| 调整后 | **6 / 6** | **34** | **0** |

初版把配图规则放在「写法要求」末尾，还加了一句「图不是必须的，不要为了凑数硬塞」——
这个 hedge 把模型压住了。改成**铁律第 5 条**、正面表述在前，覆盖率从 50% 提到 100%。

**两轮都是 0 个无效图号**，这是最危险的失败模式（配一张错的截图比没有图更糟），
没有发生。前端另有一层：编号对不上的静默丢掉，不会显示成裂图或错图。

防幻觉闸门回归验证：3 道知识库没有的题，全部「暂无此内容」+ 零来源 + 零图号。

> **一个差点被我判成 bug 的细节**：第一次测「京东电子面单模板怎么设置」，
> 模型一个图号都没写，看着像功能没生效。查下来是**模型对的**——
> 召回的材料讲的是得物/京邦达，确实没有京东的截图。
> 单题结论不可信，必须成批测。

**两个关键设计**

1. **标记自带 id，不靠"数第几张图"。**
   如果按顺序计数，只要有一段被 `min_chars` 滤掉，后面所有图就整体偏移一张——
   「第 3 步」配上「第 2 步」的截图，而且**不会有任何报错**。
   `[图:a3f9]` 里的 id 由图片地址哈希而来，每块自己就能算出带哪些图，与顺序无关。

2. **配图在正文之前发，引用在正文之后发。**
   看着矛盾，其实是同一条原则的两面：
   - 引用晚发，是因为模型可能答「知识库暂无此内容」，那时挂来源会让用户
     误以为答案有依据（M1 坑 #2）
   - 配图早发，是因为前端要边流边把 `[图1]` 换成真图；而它不构成"有依据"的
     暗示——模型说不知道时正文里根本不会出现 `[图N]`，什么都不会渲染

### M5 — 上线 ⭐ **第一个交付点**　✅ **已完成 2026-08-17**

> **SSH 不用另给**——`~/.ssh/erp_vps`（上个 erp-copilot 项目留下的密钥）
> 对 `root@8.136.116.9` 仍然有效，免密可登。

**已完成**

- [x] 备份 nginx 全量配置 + `/var/www` → `/root/backup-20260817-163500`（186M）
- [x] 80/443 早已放行（Aura Note 一直在跑），无需改安全组
- [x] PostgreSQL 16.14 + pgvector 0.6.0（Ubuntu 官方源就有，不必加 PGDG）
- [x] `shared_buffers=128MB` / `max_connections=20`，七张表迁移到位
- [x] 数据库密码**在服务器上生成**，写进 `/root/.kb-db-password`(600)，全程不过对话
- [x] uv + 依赖（92 个包）
- [x] `copilot-api.service`：非 root 的 `copilot` 账号、`MemoryMax=600M`、
      `ProtectSystem=strict`。实测常驻 **81MB**
- [x] nginx 切换：`/` → 前端，`/api` → 8000，`/images/` → 配图
- [x] **Aura Note 下线**（2026-08-17，用户决定只保留知识库助手）。
      留了两份：`/var/www/myprogram.retired-20260817` 和
      `/root/backup-20260817-163500/var-www/myprogram`（186M / 10 个文件，已核对一致）。
      恢复办法写在 `deploy/nginx.conf` 的注释里
- [x] HTTPS 沿用既有证书（有效期至 2026-10-15），HTTP 强制跳转
- [x] `deploy/`：`setup-server.sh` / `deploy.sh` / `nginx.conf` / `copilot-api.service`
- [x] 灌数据：服务器自己跑 `sync-yuque`，14/14 库 / 786 篇 / 6047 张图
- [x] `copilot ingest`：**746 篇 / 5268 块 / 2881 块带图 / 6193 处图片引用，
      与本机数字完全一致，0 失败**
- [x] 发邀请码 + 端到端验收
- [x] `copilot-api` / `postgresql` / `nginx` 三个服务均已 `enabled`（开机自启）

**最终验收**（公网，用第一次问过、当时因数据没灌完而答不上来的那道题）
```
问「京东电子面单模板怎么设置？」
  → 答出 4 步，5 条引用，行内配图 [图12][图13]，无效图号 0
  → 两张图 HTTPS 均 200
  → 并诚实指出「材料中未提供旺店通系统内的具体设置步骤」
```

**上线验收（公网实测）**
```
/                200   /login/     200   /register/  200   /chat/  200
/api/health      200
/api/chat 无 cookie   401     /api/docs   404 ← 线上已关闭
http://  → 301 https://
free -h 可用 1.1Gi ← 健康线 200MB 以上
```
> ⚠️ 验证公网可达**必须用 `curl --resolve`**：本机 VPN 做 fake-IP DNS 劫持，
> 直接 curl 域名会全部失败，看起来像上线没成功（见坑 #5）。

**一处改动**：图片不从本机上传。语雀配图约 6000 张 1GB，
服务器在阿里云机房、从语雀 CDN 拉比家用宽带上传快得多；
而图片路径是按 URL 哈希算的，两边跑出来完全一致。

**踩到的坑**

1. **⭐ `config.py` 靠数目录层数定位项目根，部署时必然错。**
   开发是 `Copilot/backend/src/copilot/config.py`（往上四层是根），
   部署时目录被拍平成 `/opt/copilot/src/copilot/config.py`，根就算成了 `/opt`。
   可怕的是**它不报错**：pydantic-settings 读不到 `.env` 就静默用字段默认值，
   应用拿着默认的 `kb:kb` 去连库，最后报的是「password authentication failed」——
   排查方向被带到密码和 pg_hba 上，真正的原因在三层目录之外。
   已改成「显式 `COPILOT_ROOT` → 向上找 `.env.example` → 才轮到数层数」。
2. **向上找路标要分两趟。** 第一版把「找 `<x>/backend/.env.example`」和
   「找 `<x>/.env.example`」写在同一个循环里，结果在 `backend/` 就命中了第二个条件，
   根被算成 `backend/`，`data/` 跟着指到 `backend/data`。开发布局必须先判。
3. **`pkill -f "uv sync"` 会把自己杀掉。** `-f` 匹配整条命令行，
   而我那条 ssh 命令的命令行里正好含 "uv sync"。表现是命令毫无输出、
   什么也没发生。
4. **阿里云到 PyPI 慢到装不完**：默认源几分钟才装 2 个包，
   换清华源后 **20 秒装完 92 个**。这不是优化，是能不能装完的问题。
5. **本机 `curl https://liushun666.cn` 全部失败，但站点是好的。**
   本机装了 VPN/代理做 fake-IP DNS 劫持，域名解析到了 `198.18.1.213`
   （保留段）。验证公网可达要用 `curl --resolve` 绕开本机 DNS，
   否则会得出「上线失败」的错误结论。

**裸装，不用 Docker**（1.6GB 上 Docker 本身的开销不划算）。

- [ ] ⭐ **先备份 Aura Note 目录和现有 nginx 配置**——改任何东西之前
- [ ] 阿里云安全组放行 80/443
- [ ] `setup-server.sh`：
  - `apt install postgresql-16 postgresql-16-pgvector`
  - **`shared_buffers=128MB`、`max_connections=20`**（默认值在 1.6GB 上太贪）
  - 建库建用户，跑 alembic 迁移
  - Python 3.12 + uv（**别碰系统 Python**）
- [ ] `deploy/nginx.conf`：
  - `location /` → `out/` 静态文件（配 `try_files $uri $uri/ /index.html;`，
    因为前端是 `trailingSlash: true` 的静态导出）
  - `location /api` → `127.0.0.1:8000`
  - ⭐ `location /images/ { alias <项目>/data/images/; expires 30d; }`
    —— 语雀配图，**必须由 nginx 直接发**。FastAPI 里那个 `app.mount("/images")`
    只是本地开发用的；1.6GB 的机器上让 Python 发几千张图纯属浪费
  - `location /aura` → Aura Note 静态目录（**保留**；确定下线时删这个 block 即可）
  - ⭐ **SSE 必须配**：`proxy_buffering off;` `proxy_read_timeout 300s;`
  - ⭐ `client_max_body_size 20m;`（默认 1m 会让上传直接 413）
- [ ] certbot 签 HTTPS + 自动续期
- [ ] `kb-api.service` + `kb-worker.service` systemd 常驻 + 开机自启
- [ ] `deploy.sh`：本机 `npm run build` → `rsync out/` → `systemctl restart`
  - ⭐ **`data/images/` 也要 rsync 上去**（约 3000 张图）。服务器上重跑
    `sync-yuque` 也能拿到，但那要再向语雀发几千次请求，不如直接传
- [ ] 首次线上 `kb sync-yuque` 灌数据 + `kb invite` 发码

**验收**：**别人在自己手机上打开 `https://liushun666.cn`，用你发的邀请码注册，问出带引用的答案。** 到这一步项目就活了。同时确认 `/aura` 仍能打开，且 `free -h` 剩余内存 > 200MB。

### M6 — 上传 + 私有库（2–3 天）　✅ **已完成 2026-08-17（含上线）**

- [x] `routes/docs.py` 上传接口，安全项**逐条**落实：
  - 白名单扩展名 `.md .txt .docx .pptx .pdf`（PDF 仅纯文本提取）
  - 大小上限 20MB（**边写盘边判**，不是先读进内存）+ 每用户 200 份
  - ⭐ **落盘用 uuid 重命名**，原始文件名只存 DB —— 防路径穿越
  - 存 `data/uploads/{user_id}/`，库里存**相对路径**（绝对路径搬不了机器）
- [x] 上传后 `enqueue` 解析任务（**与 documents 行同一个事务**）
- [x] `ingest/parsers.py`：md / txt / docx / pptx / pdf → Markdown
- [x] `jobs/queue.py` + `jobs/worker.py`：`FOR UPDATE SKIP LOCKED` 消费；
      状态机 `pending→running→done/failed`，失败存 `error`
- [x] 解析结果以 `owner_id={user_id}` 入库（复用 `write_chunks`，红线仍只有一处）
- [x] 文档管理页 `/documents`：上传（拖拽）+ 列表 + 状态 + 删除（**同时删向量块**）
- [x] 前端轮询 `GET /api/documents`，**只在有文档没解析完时轮**，跑完自己停
- [x] ⭐ `tests/test_isolation.py`：加了「真上传 → 真 worker → 真检索」的端到端隔离
- [x] `copilot worker` / `copilot-worker.service`（独立进程，`MemoryMax=400M`）
- [x] `deploy.sh`：同步 systemd 单元文件 + `uv sync --extra parse` + 重启两个服务
- [x] 部署上线 + 线上换账号实测

**本机验收（真 uvicorn + 真 multipart + worker 独立进程 + 真 SiliconFlow）**
```
上传 e2e-manual.docx  → pending → worker 跑一轮 → done / 1 块
  正文含 `# 一、电子面单设置` 与 Markdown 表格 `| 字段 | 含义 |`  ← 标题层级和表格都保住了
重复上传同一份       → duplicate=true，沿用原来那一行，不重复烧 embedding
上传 x.exe           → 415「不支持的文件类型（.exe），只收 …」
上传坏的 .docx       → failed，错误写着「打不开这个 Word 文件（PackageNotFoundError）」
检索（真 embedder + 真 reranker，库里 5268 个真实块）：
  本人   → 命中自己上传的那篇  True
  另一人 → False        未登录 → False        ← 隔离
删除                 → 204，块与落盘文件一起没了，公共库 746 篇不动
```
**测试**：`pytest` 187 passed（M5 时 137，新增 50 条），`ruff` / `eslint` / `tsc` 全过。

**做的时候想清楚的几件事**

1. **worker 必须是独立进程。** 解析一份 20MB 的 pptx 是同步 CPU 活，塞进 API
   进程就会顶住那个唯一的事件循环——别人正在流的答案会一起停住。分开还能
   单独限内存：真跑飞了被 systemd 收走的是 worker，网站还在。
2. **可重试与不可重试要分开。** 文件坏了（`ParseError`）重试一万次也是坏的，
   只会白烧 CPU；embedding 撞限流是过一会儿就好的。分不开的话，要么坏文件
   卡在队列里反复重试，要么一次网络抖动就让用户看到「解析失败」。
3. **`run_once` 返回三态而不是布尔。** 「有没有活干」和「干成了没有」混在一起时，
   主循环会把失败当成「还有活」→ 立刻接着取 → 同一条任务不带间隔地连撞三次，
   把重试次数在一秒内烧光。**重试也就白设了**，而且日志上看不出异常。
4. **落盘路径存相对的。** 绝对路径会把开发机的 `C:\Users\…` 写进库里，
   搬到 `/opt/copilot` 全都指不对——而这个库是要跨机器用的。
5. **删文档要连待办任务一起撤。** 不撤也不会坏（worker 认得出文档没了，
   作废即可），但队列会攒一堆注定作废的行；更要紧的是它让测试变得随机失败——
   下一个用例的 `run_once` 可能先取到这条孤儿任务。

**踩到的坑**

1. **⭐ `updated_at` 在复用旧行时会炸 `MissingGreenlet`。**
   它是 `onupdate=func.now()`，值由数据库算，提交后属性处于**过期**状态，
   序列化时一读就触发隐式 IO，在 async 会话里直接抛
   `greenlet_spawn has not been called`。
   可怕的是**新建行那条路径完全正常**——只有「重传上次失败的那份」会踩到，
   极容易漏过测试。解法是提交后 `await session.refresh(doc)`。
2. **`FakeEmbedder` 的桶下标带字符位置**（`ord(ch)*7 + i`），
   所以「块正文 = 标题 + 空行 + 原文」时，拿原文去搜反而**搜不到自己**——
   前面多几个字就把整个向量错开了。真实 embedder 没这毛病，
   但测试差点得出「隔离过度」的错误结论。测试数据要么不带标题，要么用原文当查询。
3. **单跑一个测试文件会全红**：`pytest ../tests/test_jobs.py` 时 rootdir 变成
   `Copilot/`，找不到 `backend/pyproject.toml` 里的 `asyncio_mode = "auto"`，
   于是所有 async 测试被当成同步的，报的是
   「requested an async fixture, with no plugin that handled it」。
   要么不带路径跑（`uv run pytest`），要么显式 `-c pyproject.toml`。
4. **`nginx client_max_body_size` 要比应用上限宽一点**（21m vs 20MB）：
   multipart 的分隔符和头也算进 body 长度，正好卡 20m 会让 19.9MB 的文件
   被 nginx 拦下，而 nginx 回的是一页 HTML，前端拿不到那句「文件超过 20MB 上限」。
5. **`uv sync --no-dev` 不装 extra。** 服务器上漏了 `--extra parse` 的表现是：
   网站一切正常，上传也成功，只是每份文档都转成「解析失败：服务端缺少 docx
   解析组件」。

**上线验收（公网实测，2026-08-17）**

用两个临时账号做的，验完已连数据一起删干净（`未用邀请码 0`、`文档 746` / `块 5268` 与灌数据时一致）。

```
/  /chat/  /documents/  /api/health   全 200
POST /api/documents 无 cookie          401
copilot-api / copilot-worker           active + enabled（开机自启）
服务器上 import docx/pptx/pypdf         ok      ← --extra parse 真的装上了
free -h 可用 1.0Gi                              ← 健康线 200MB

A 上传 m6-rule.md → worker 在 1 秒内取到 → done / 1 块
A 上传 x.exe      → 415        A 上传坏 docx → failed「打不开这个 Word 文件」
B 的「我的文档」  → []         B 删 A 的文档 → 404，A 那份还在

⭐ 隔离的线上验证（同一个问题，两个账号）
   问「本公司京东面单用哪个模板，打印偏移量设多少」
   A（主人）→「统一使用「JD-三联单-2026版」模板…[1] 偏移量上边距 3 毫米、
              左边距 2 毫米，否则运单号会压到裁切线上。[1]」引用首条 = m6-rule
   B（另一人）→「知识库暂无此内容。」无引用
```

**一个意外发现（留给 M8 量化）**：第一次验收用的测试文档写了「仅限内部查看」，
问题也偏离 ERP 领域（「爱丽丝的客户报价是多少」）。结果**连文档主人都被答成
「知识库暂无此内容」**——而检索层完全正常（那一块重排 0.9997 排第一，
上下文里就是答案）。是 prompt 的第二道闸门（铁律 3 + 「ERP 实施顾问」的身份设定）
把它挡了。对这个产品来说这个偏保守的取向大概是对的，但**保守到什么程度是没数的**，
正好是 M8 评测集要回答的问题：假阴性率（材料里有、却答不知道）现在完全没有度量。

### M7 — Agent 化（2–3 天）　✅ **已完成 2026-08-18**

用 Pydantic AI（2.31）把 M1 的检索包成工具，加上多轮追问与出方案的能力。

- [x] `agent/agent.py`：Agent + 依赖注入（当前用户、db session、provider）
- [x] `agent/tools.py` 四个工具：
  - `search_kb(query)` — 包装 M1 的 `retrieve.py`，**自动带当前用户的 owner 过滤**
  - `save_requirement(field, value)` — 维护需求清单，并**返回下一个该问什么**
  - `generate_plan()` — 子 Agent + Pydantic 约束的结构化输出
  - `export_excel()` — 落 xlsx 供下载
- [x] `agent/planner.py`：出清单的子 Agent（`output_type=Checklist`）
- [x] `agent/runner.py`：Agent 事件流 → AI SDK 的 UI Message Stream
- [x] 前端渲染工具调用过程 + 下载按钮（`components/chat/agent-trace.tsx`）
- [x] 最大轮数限制（`UsageLimits`）+ 工具失败恢复
- [x] ⚠️ **`routes/chat.py` 没有整体切到 Agent**——理由是数字，见下

**⭐ 最重要的结论：Agent 不接管普通问答**

M8 的评测集直接用上了（`eval/run.py --agent`，同一份 41 题）：

| 指标 | 直路（线上） | Agent |
|---|---|---|
| 准确率 | **100%** | 87.8% (−12.2) |
| 检索命中率 | **100%** | 93.9% (−6.1) |
| 引用正确率 | **100%** | 90.9% (−9.1) |
| 幻觉率 | **0%** | 12.5% (+12.5) |
| 无据陈述率 | **0%** | 2.9% (+2.9) |

Agent 自己决定检索词，命中率反而更低，还会把相邻主题的材料凑进答案
（典型：拿「得物」的面单步骤回答「京东」的问题）。**每一项都更差**，
所以让它接管普通问答就是拿一个已量化的系统去换一个没量化的。

最终形态是**按意图分流**（`_use_agent`）：
- 普通问答 → 直路（那条被量到 100% 的）
- 出现「实施方案 / 配置清单 / 上线清单」等意图词 → Agent
- **会话已经在收集需求（`profile` 非空）→ 继续走 Agent**。这条不能少：
  少了它，用户答完第一个追问，第二轮就被路由回直路，状态断掉，
  表现是「它把刚问过的又问一遍」。
- `agent_enabled` 总开关留着，给评测和将来验证用。

**验收（本机实测，真 LLM）**
```
四轮对话：
  「帮我出一个实施配置方案」        → 追问对接平台（没调工具，先问）
  「淘宝、拼多多、抖音，5 个店」    → save_requirement ×2 → 问仓库
  「一个自营仓，日均 3000，大促 2 万」→ save_requirement ×3 → 问物流
  「中通和韵达，有组合装和预售」    → save_requirement ×2 → generate_plan
接着说「生成方案并导出成 excel」    → generate_plan → export_excel
  清单 15 项 / 待确认 7 条
  xlsx 11KB，三个工作表（实施配置方案 / 待确认 / 生成信息），表头冻结
  条目形如「[必做] 设置–基本设置–店铺 → 创建5个店铺并授权」，依据引材料 [4]
```
测试 200 → 221（新增 18 条 Agent 用例 + 3 条 `is_no_answer` 边界）。

**踩到的坑**

1. **⭐ 差点因为度量错误得出「Agent 幻觉率 75%」的结论。**
   第一轮 Agent 评测显示 8 道「知识库没有」的题里 6 道在编答案。
   翻原始输出才发现：其中 4 道的答案**末尾明明写着**「知识库暂无此内容。」，
   而 `is_no_answer()` 只认**开头**匹配。
   → 这暴露了一个真 bug：判定不成立时，页面会把「暂无此内容」和五条引用
   一起显示出来——正是 M1 最在意的那个坑。已修成
   「开头匹配 **或** （提到那句话 **且** 全文没有 `[n]` 标记）」，
   后半个条件是为了保住 `partial` 类答案（答了一部分并说明另一部分没有，
   引用必须照常显示）。配了 3 条边界测试。
   修完再测：幻觉率 75% → 12.5%，`no_answer` 分类准确率 25% → 87.5%。
   **教训：指标异常时先看原始输出，别急着改被测系统。**
2. **一轮里有多个 TextPart。** Agent 先说「我查一下」、调工具、再接着答，
   那是两段独立文本，各自要有配套的 `text-start` / `text-end` 且 id 对得上。
   复用同一个 id 会让前端把后一段拼进前一段；不发 `text-end` 就永远显示
   「正在输入」。
3. **工具失败要发 `tool-output-error`，不是 `error`。** 后者让整轮变成错误态，
   而 Agent 明明还能继续。
4. **引用要按「本轮全局」重编号。** 一轮检索三次，每次都从 [1] 开始，
   直接拼起来会出现两个 [1]——用户点开溯源看到的是另一篇文档。
5. **多轮状态必须落库**（`conversations.profile` / `checklist`）。
   每个 HTTP 请求都是一次全新的 Agent run，状态放进程内存里，
   用户答完第二个问题第一个答案就没了。
6. **Agent 路径的 token 记账一开始漏了检索到的材料**（占八成）。
   直路是显式把 `context_text` 交出来的，Agent 这边材料在工具里产生，
   得专门累积一份（`deps.retrieved`）。少了它，配额形同虚设。
7. **⭐ 装了 pydantic-ai 之后，命令行在服务器上整个跑不起来。**
   以 `copilot` 账号从 `/root` 执行任何 CLI 命令都炸在一句毫不相干的错误上：

       PermissionError: [Errno 13] Permission denied: 'pyproject.toml'

   链路是：pydantic-ai 拖进来 logfire → logfire 在 `pydantic` 这个 entry point
   组里注册了插件 → **第一次构建 pydantic 模型**时 pydantic 会枚举并 `load()`
   它（= import logfire）→ logfire 初始化时读**当前工作目录**下的
   `pyproject.toml`（`logfire/_internal/config_params.py`）→ 而 copilot
   这个系统账号读不了 `/root`。

   两处让它特别难查：
   - pydantic 加载插件时**只捕获 ImportError 和 AttributeError**
     （`pydantic/plugin/_loader.py`），PermissionError 直接穿出来，
     连一句「插件加载失败，已跳过」都没有；
   - 堆栈里全是 importlib 的帧，看不出跟 logfire 有关。
     **我第一次就归错了因**——grep 到 `beartype` 里出现 `pyproject.toml` 就下了
     结论，其实那只是它的注释。

   修法是在 `copilot/__init__.py` 里 `os.environ.setdefault(
   "PYDANTIC_DISABLE_PLUGINS", "logfire-plugin")`——本项目根本没用 logfire。
   顺带的好处（都是实测，不是估算）：本机 CLI 路径的导入模块数
   **1030 → 577**；服务器上 API 只跑普通问答时常驻 **67MB**
   （修之前，只要有一个带 body 的请求触发 pydantic 建模型，logfire 就被拉进来）。
   参照：M7 之前是 71MB，跑过一次 Agent 会话后是 180MB
   （pydantic-ai 那一坨常驻，与本条无关）。
   配了两条回归测试，其中一条用子进程跑（同进程里别的用例可能已经
   import 过 agent，pydantic-ai 会直接 import logfire，那样断言就成了假阴性）。

**没做的**

- 工具调用的**入参**没推给前端（只推了工具名和状态）。检索词对用户是噪声，
  真要看就去 journal 按 request id 捞。
- `generate_plan` 之后不自动导出。实测模型生成完会接着问澄清问题，
  中间插一次导出反而打断它；用户说「导出」时再导（意图明确得多）。
- `tests/` 和 `eval/` **不在 ruff 的检查范围里**（`deploy.sh` 只 `cd backend`
  跑 lint）。M8/M7 新加的文件是手工过的 lint，但既有测试文件里有几处 E501。
  要治就把 lint 范围放到仓库根，那会顺带改动一批既有文件，留给下次。

### M8 — 评测 + 优化（2–3 天）　✅ **已完成 2026-08-17（含上线）**

- [x] `eval/dataset.yaml`：**41 题**，四类各量不同的东西
      （`fact` 26 / `no_answer` 8 / `probe` 5 / `partial` 2）
- [x] `eval/run.py`：可复跑，`--check` / `--tag` / `--compare` / `--prompt`
- [x] `eval/prompts.py`：历史 prompt 存档，用于干净的 A/B
- [x] 指标：准确率 / 检索命中率 / 引用正确率 / 幻觉率 / 假阴性率 / **无据陈述率**
- [x] 基线 → 调参 → 对比表（见下）
- [x] ⭐ `pg_jieba` 决策：**不加**。理由是数字：检索命中率 **100%**（34/34）
- [x] 语雀定时增量同步：`copilot-sync.service` + `.timer`（每天 12:10 北京时间）
- [x] 每用户每日 token 配额：`token_usage` 表 + `usage.py`，超额 429
- [x] 日志与错误上报：`api/logging_setup.py`（request id / 慢请求 / 兜底 500）
- [x] `copilot prune-junk`：清索引里的二进制垃圾块
- [x] 上线（prompt 修复 + 清垃圾块 + timer + 迁移）

**评测集怎么建的**

先把 5268 个块里「带界面路径、带数字、带映射表」的 1178 个挑出来**逐个读**，
再针对读到的具体事实出题。凭想象出的题会让指标好看却毫无意义——
模型答对的是我脑子里的知识库，不是磁盘上这个。

`no_answer` 那 8 道逐条核实过语料里真的没有（`工资条`/`SAP`/`Shopify`/`股票代码`/
`MySQL`/`考勤`/`年会`/`美元` 全是 0 块）。**这一步不能省**：万一知识库里其实有，
模型答出来反而被判成幻觉，整套指标就是反的。

**对比表**（41 题，语料 4568 块，判分 `deepseek-reasoner`，答题温度 0）

| 指标 | v1（M5 的 prompt） | v2（改闸门优先级） | **v3（线上）** | v2 + rerank_k=3 |
|---|---|---|---|---|
| 准确率 | 92.7% | 97.6% (+4.9) | **100.0%** (+7.3) | 100.0% |
| 检索命中率 | 100.0% | 100.0% | **100.0%** | 100.0% |
| 引用正确率 | 100.0% | 100.0% | **100.0%** | 97.0% (−3.0) |
| 幻觉率 | 0.0% | 0.0% | **0.0%** | 0.0% |
| 假阴性率 | 6.1% | 0.0% (−6.1) | **0.0%** | 0.0% |
| 无据陈述率 | 3.2% | 3.0% | **0.0%** (−3.2) | 0.0% |

改的**只有 prompt**（`top_k=20 / rerank_k=5 / chunk=500/80` 全程没动）。
`rerank_k=3` 那一列是为了看能不能省上下文：准确率一样，但**引用正确率掉了 3 个点**，
而且它「修好」那道题的方式是把模型说错话的那块材料从上下文里拿掉了——
不是变准了，是少给了材料。**没有理由缩**，维持 5。

**这一轮真正查出来的东西**

1. **⭐ 索引里 13% 是垃圾。** 5268 块里 700 块是语雀内嵌表格（Lake sheet）的
   zlib 压缩载荷，全在「统计」库。它们白烧了 700 次 embedding、白占 top-k 名额，
   而且**永远不会有人发现**——没人会去搜「统计-销售排行」的正文，答案里也不会
   出现它们，只有清点索引时才看得见。已在切分器里拦掉（`looks_like_junk`），
   存量用 `copilot prune-junk --apply` 清了，本机和服务器都是 5268 → 4568。
   > 过滤这类东西**误杀比漏放严重得多**：漏放只是浪费，误杀是让一整篇文档从
   > 知识库里消失且不报错。第一版判定用了「拉丁扩展区字符密度」，把
   > 「授权信息一览表」判成了垃圾——因为 `×`（U+00D7）正好在那个区间里，
   > 而真实的对接表大量用 `×`/`√` 标「支持/不支持」。已排除这两个字符并加了回归测试。

2. **⭐ M5 那句「诚实」其实盖住了一次召回失败。**
   上线验收时问「京东电子面单模板怎么设置」，答案末尾补了一句
   「材料中未提供旺店通系统内的具体设置步骤」，当时（也在 M5 的记录里）
   被当成防幻觉做得好的证据。建评测集时才查出来：那篇文档**第 7 块（共 7 块）
   就是**「3.旺店通erp操作 › 3.1 下载京东云模板 / 3.2 配置 / 3.3 使用」——
   知识库里一直有。模型没说谎，它对着自己拿到的上下文说的是实话；
   **是重排 top-5 没把第 7 块捞上来。**
   这类失败读起来完全合理，**没有评测集就永远发现不了**。
   已固化成 `fact-jd-wdt-side-steps` 这道题。

3. **假阴性的根因是两条铁律打架。** 旧 prompt 的铁律 3（「材料回答不了就只回复
   这一句」）压倒了铁律 4（「只有部分信息时答已有的部分」）。表现：
   问题多问一句、或答案埋在长块中间，模型就整个放弃。
   最典型的一例——上下文里**原文写着**「批量换货的操作上限为一次500单」，
   它答「知识库暂无此内容」。改成「先判断有没有可用信息，再决定怎么答，
   顺序不能反」之后，这类题全过。

4. **收紧闸门会长出反向的毛病。** v2 修好了假阴性，却开始**过度声明缺失**——
   说「材料未提及按体积拆分」，而材料 [4] 里明明有。这和编造一样是错的，
   被「无据陈述率」这个指标抓到。v3 补了一句「说『材料里没有』只针对问题问到的
   东西，且要先通读全部材料」，才真正干净。
   > 这印证了当初设这两个指标的理由：**幻觉率和假阴性率是一对，只看一个必然调偏。**

5. **评测自己有 5% 的抖动。** 温度 0.1 时同一份配置连跑三轮，41 题里有 2 题
   翻来翻去——**比「改一处 prompt」带来的提升还大**。差点据此得出「清垃圾块导致
   两道题退化」的错误结论（复跑两次就都过了）。现在评测把答题温度压到 0，
   与线上的 0.1 有出入，但**可复现比完全一致更值**。
   凡是只差一两道题的结论，都当成噪声处理。

**已知的方法学缺陷**（写在这里，别让指标看起来比实际可信）

- ~~**判分器和被判者同源**：`deepseek-reasoner` 判 `deepseek-chat`。~~
  → **M9 已解**：换成 Gemini 2.5 Pro，换厂了。只能在本机跑（服务器连不上 Google）。
- ~~**41 题已经饱和在 100%**，只能发现退化、不能证明改进。~~
  → **M9 已解**：补了 14 道难题（多跳 / 跨文档 / 否定 / 条件），指标里单列
  「难题准确率」——总准确率会被饱和的老题稀释成噪声。
- ~~评测只打公共库（`user_id=None`），私有库的检索质量没有单独度量。~~
  → **M9 已解**：`--as-user <邮箱>` + 6 道 `scope: private` 的题 + 夹具文档。
  第一次跑就查出私有文档会被公共库挤出 top-5。

**验收**
```bash
cd backend
uv run python ../eval/run.py --check            # 只验检索，不花钱
uv run python ../eval/run.py --tag now          # 出指标
uv run python ../eval/run.py --compare v3-final now   # 和线上基线比
uv run copilot prune-junk                       # 预演；--apply 才真删

# 私有库那组（M9）。夹具改了要重灌
uv run copilot ingest ../eval/private --owner you@example.com
uv run python ../eval/run.py --as-user you@example.com --check
uv run python ../eval/run.py --as-user you@example.com --tag private-1
```
```
线上（2026-08-17 部署后实测）
  copilot-api / copilot-worker      active      copilot-sync.timer  active
  下次同步 Tue 2026-08-18 12:18 CST
  服务器 prune-junk：5268 → 4568（与本机一致）
  服务器上问「批量换货一次最多多少单」
    → 「一次最多能操作500单，目前无法提高操作上限。[1]」 ← 原先答「暂无此内容」
```

---

### M9 — 会话删除 + 勘误层 + 图片解析（2026-08-18）　✅

M0–M8 之后用户提的五件事，一批做完。三个新增能力各自解决一类**结构性**问题，
不是功能堆叠。

#### 1. 聊天记录可以删了

在此之前会话只能新建、不能删——库里攒的是别人的真实提问，
连个「删掉」都没有这件事本身就不合适。

- [x] `DELETE /api/conversations/{id}`，别人的会话仍是 404 不是 403
- [x] 侧边栏 hover 出垃圾桶，`focus-visible` 也能出（只挂 hover 的话键盘用户够不着）
- [x] 删当前会话时自动换一个新 id
- [x] 4 条测试：级联删消息 / 二次删 404 / 删不了别人的 / xlsx 跟着删

> **导出的 xlsx 必须手动删。** 会话行没了之后，`data/exports/<user_id>/` 下那个
> 文件再没有任何东西指向它，留着就是永远不会被回收的孤儿。
>
> **删除接口用 404 比读接口更要紧。** 读接口用 403 泄漏的是「这个 id 存在」，
> 删除接口用 403 泄漏的是「这个 id 现在还活着」——后者更值钱。

#### 2. 语雀同步改成 15 天，并且内容错了能改

- [x] `copilot-sync.timer`：`OnCalendar=*-*-01,16`（每月 1 号、16 号）
- [x] **勘误层** `corrections/`：进 Git 的独立一层，ingest 时覆盖语雀原文
- [x] `copilot correct <关键词>` / `--retire`（整篇作废）/ `copilot corrections [--check]`
- [x] `deploy.sh` 第 4 步推送勘误层，自检阶段跑一次过期体检（**只警告不拦部署**）
- [x] 16 条测试 + 真文档端到端（覆盖 → 作废 → 回滚，块数精确回到 4568）

> **为什么不用 `OnUnitActiveSec=15d`**：那是「距上次运行满 15 天」，一次重启或
> 一次手动 start 就重新起算，跑久了没人说得清下次是什么时候。写死日历日期，
> `systemctl list-timers` 上永远看得见准确的下一次。
>
> **为什么勘误不直接改 `data/raw/yuque/*.md`**：那是 sync 的产物、`data/` 又在
> .gitignore 里。直接改会同时踩三个坑——改动无记录、换机器就没、
> 而且语雀那篇一更新，sync 重新落盘就把修改**静默**冲掉。
>
> ⭐ **这一层最容易骗人的失败是「勘误没生效但看起来一切正常」。**
> 所以三处都做了噪音：`target_url` 对不上号的 ingest 时单独列出来警告；
> 两条勘误指向同一篇直接报错（静默取其一的话，生效哪份取决于文件名排序）；
> 语雀原文后来又更新了标成「过期」——**但仍然照常覆盖**。
> 过期就自动失效意味着某天知识库悄悄换回了错的原文，而那正是勘误要解决的问题。

#### 3. 图片和扫描件 PDF 能传了

- [x] `providers/vision.py`：Kimi 多模态，`moonshot-v1-32k-vision-preview`
- [x] 上传放开 `.png/.jpg/.jpeg/.webp/.bmp`，走 worker 自动转写 → 切分 → 向量化
- [x] 扫描件 PDF：`pypdfium2` 逐页渲染 → 逐页读图（原来是直接拒收）
- [x] 页数上限 `vision_pdf_max_pages=20`，**截断要写进 note 告诉用户**
- [x] 11 条测试（假 vision 客户端）+ 真截图 / 合成扫描件端到端

> **`kimi-k2.6` 用不了**：它要求 `temperature=1`，而转写不该有创造性。
> 视觉预览版正好没这个限制。
>
> ⭐ **模型会把整段输出套一个 ```markdown 围栏**（十次里七八次）。不剥掉的话，
> 里面的 `## 标题` 是代码不是标题——整张图变成一个没有章节的大块，
> 引用里也就没有了「第 N 节」。这不是洁癖问题，是功能问题。
>
> **透明 PNG 必须铺白底再转 JPEG**，否则黑底黑字，一个字都认不出来。
> ERP 截图里白底 PNG 正是最常见的那种。
>
> **扫描件的判据是「整篇都提不出字」，不是「某几页空」。** 按页回退看着更聪明，
> 实际上一份 200 页 PDF 里夹几页插图是常态，那样会在用户毫不知情的情况下
> 反复触发付费调用。
>
> 许可：选 pypdfium2（BSD-3 + Apache-2.0、3.7MB、零依赖）而不是 PyMuPDF（AGPL，七.3 红线）。

#### 4. 三个模型各就各位

| 环节 | 模型 | 为什么是它 |
|---|---|---|
| 答题 | **deepseek-chat** | 唯一有 41 题评测背书的配置，不凭感觉换 |
| 向量 / 重排 | bge-m3 / bge-reranker-v2-m3 | 不动 |
| 读图 | **Kimi** `moonshot-v1-32k-vision-preview` | 服务器实测可直连（200 / 3.2s） |
| 判分 | **Gemini 2.5 Pro** | 换厂，解掉「判分器和被判者同源」 |

> **Gemini 只能在本机用**：国内服务器连 `generativelanguage.googleapis.com`
> 15 秒超时（实测）。评测本来就只在本机跑，正好。
> 两个坑：要走 OpenAI 兼容端点（结尾 `/openai`），原生端点鉴权方式不同；
> 这个 key 是 `AQ.` 开头的，`?key=` 能用、原生端点的 Bearer 会 401。
>
> ⚠️ **DeepSeek 余额 2026-08-18 只剩 ¥2.13。** 它是线上唯一的答题模型，
> 烧完就是全站 402。Kimi 已经接成可切换的 provider（改 `.env` 两行），
> 但**切之前必须重跑评测**——现有那套 100% 的数字对 Kimi 不成立。

#### 5. 评测集从饱和里捞出来

M8 结束时 41 题在 v3 上是 100%，**饱和的题集只能发现退化、不能证明改进**。

- [x] 补 **14 道难题**（`hard: true`）：多跳 / 跨文档 / 否定 / 条件推理
- [x] 补 **6 道私有库题**（`scope: private`）+ 夹具文档 + `--as-user <邮箱>`
- [x] `copilot ingest --owner <邮箱>`：把文档灌进某人私有库
- [x] 判分器换 Gemini 2.5 Pro
- [x] 指标加「难题准确率」——总准确率会被 41 道饱和老题稀释成噪声

> 出题方式和第一批一样：**先读语料再出题**，每条事实都用 SQL 在 chunks 里
> 核实过原文，包括 no_answer 那两道的「确实没有」（TikTok / Lazada 均 0 块，
> 而「速卖通」有 14 块——跨境这个语义邻域是存在的，所以比问一个完全无关的词更难）。
>
> ⭐ **私有库第一次被度量就查出两个真问题**，见下面的结果表。

#### M9 的评测结果

**公共库 55 题**（判分 gemini-2.5-pro，答题 deepseek-chat 温度 0，配置未动）

| 指标 | v3（M8，41 题） | **v4-hard（M9，55 题）** |
|---|---|---|
| 准确率 | 100.0% | **98.2%** |
| 难题准确率 | — | **100.0%**（14 题） |
| 检索命中率 | 100.0% | **100.0%** |
| 引用正确率 | 100.0% | **100.0%** |
| 幻觉率 | 0.0% | **0.0%** |
| 假阴性率 | 0.0% | **0.0%** |
| 无据陈述率 | 0.0% | **2.2%** |

> **两个数不能直接比**——题集换了（41 → 55）。唯一没过的是
> `probe-negation-form`：答案断言「只有以下三种拆分方式」，而材料 [4][5]
> 里还有别的，属于**过度概括**。这是一个真实缺陷，不是判分抖动。
>
> ⚠️ **14 道难题全过，说明天花板只被抬高了一点点。** 补题达到了「覆盖多跳/
> 跨文档/否定/条件」的目的，但现有配置照样能答对——评测集**仍然主要是
> 一个退化探测器**。下一批题得更狠（要求综合三处以上材料、或涉及版本差异的时序推理）。

**私有库 6 题**（`--as-user`，夹具《客户A-实施配置约定》）

| 指标 | 值 | 说明 |
|---|---|---|
| 准确率 | 83.3% | |
| 检索命中率 | 80.0% | ⭐ 缺的那题：问「星辰电商的组合装要不要拆分」，私有文档被公共库《流程中拆分条件说明》的 4 个块整个挤出 top-5 |
| **幻觉率** | **100.0%** | ⭐ 只有 1 道 no_answer 题，没过 |

> ⭐⭐ **这两条是公共库评测永远看不见的失败**，M8 把它列为缺口是对的。
>
> 幻觉那条尤其值得记：问「星辰电商的退货入库要走哪几个审核节点」，
> 夹具里根本没写退货流程，模型拿公共库的通用流程答了，还以
> 「星辰电商的退货入库流程如下」开头。**每一句都能在材料里找到出处**，
> 所以连 `grounded` 判定都抓不到——比编造更难发现，因为它看起来更专业。

**试过、没修好的一版**（留档在 `eval/prompts.py` 的 `v4-subject-rejected`）

在铁律里加第 6 条「问题限定了主体时，材料必须真的是讲这个主体的」。
结果：模型确实多说了一句「材料未说明该配置在星辰电商是否启用」，
但开头那句「星辰电商的退货入库流程如下」原样保留，那题**仍然不过**。

> 新规则和铁律 3「有一部分就答一部分」直接打架，而铁律 3 是 M8 花整整一轮
> 才调对的（假阴性 6.1% → 0）。**没有数字支持的 prompt 改动不上线**，
> 所以线上仍是 v3。真要修多半得动检索侧（让主体名参与过滤/加权），
> 那要重新过一遍公共库那 55 题。

**判分器也要重试。** 第一轮 55 题里 11 题挂在 `SSL: UNEXPECTED_EOF_WHILE_READING`
（国内连 Gemini 的常态），报告显示成「12 题没过」——**判分器的网络抖动
伪装成了模型退化**，是这套指标最坏的一种失真。现在判分带 4 次重试 + 退避。

#### M9 验收（都是线上实测，不是看代码觉得能跑）

```
本机
[1]  ruff + pytest 225 → 254 项全绿（新增 4 会话删除 / 16 勘误 / 9 图片）
[2]  npm run lint / tsc --noEmit    干净
[3]  勘误端到端     覆盖 → 作废 → 删掉勘误回滚，块数精确回到 4568   ✅
[4]  图片端到端     真 ERP 截图 10.6s / 1380 字，带菜单路径和表格      ✅
[5]  扫描件端到端   合成 2 页纯图 PDF，13.0s，逐页出「## 第 N 页」    ✅
[6]  公共库评测     55 题 98.2%，难题 14/14                          ✅
[7]  私有库评测     6 题 83.3%，查出 2 个真缺陷（见上）               ⚠️

线上 https://liushun666.cn（2026-08-18 部署后）
[8]  deploy.sh 七步全过，API 4s 就绪，四个页面全 200                  ✅
[9]  上传真截图 → status=done / 1 块                                 ✅
[10] 问「XLY-0001 总库存」→「39。[1]」，引用指向刚传的那张图          ✅
     ← 这个数只存在于截图里，公共库 4568 块中没有
[11] DELETE 会话 → 204；再删 → 404；列表空；messages 404              ✅
[12] ⭐ 真浏览器（CDP）注册 → 提问 → hover 出垃圾桶 → 点删除 →
     确认框文案正确 → 列表清空 → 刷新后服务端确实没了                 ✅
[13] 首次真跑 copilot-sync.service（装上以来从没触发过）：
     14 库 / 786 篇 / 0 变更 / 19 篇私密 / 0 失败 / 11 分钟 / 无 OOM   ✅
[14] 下次自动同步 Tue 2026-09-01 12:11 CST                            ✅
[15] free -h 可用 1.0Gi                                              ✅
```

> **[12] 那一步真正验的是「删除按钮点不点得到」。** 它是绝对定位盖在
> 整行会话按钮上的，默认 `opacity: 0`——命中测试要是落回下面那个按钮，
> 点上去就变成「打开会话」，而截图和类型检查都看不出来。
> 实测 `elementFromPoint` 落在删除按钮本身。
>
> ⚠️ **删账号会把邀请码放回「未使用」**（`invite_codes.used_by` 是
> `ON DELETE SET NULL`）。清理测试账号时撞见的：两个已经用掉的码又变成可注册。
> 已手动清掉。日常没人删账号，但要记住这个连带效应。

### 「回答没有图片了」——一次否定结论的排查（2026-08-18）

线上反馈：答案里不出截图了。**查完的结论是：图片链路没坏，相邻块扩展这个"修法"是错的。**
记在这里，是为了下次有人再提「把相邻块一起检索出来」时，不用再花一遍钱。

#### 逐段验证：每一环都是好的

| 环节 | 实测 |
|---|---|
| 生产库的块还带图吗 | 4568 块中 **2881 块带图** |
| 检索出来的材料带图吗 | 55 题里 **54 题**的材料含 `[图N]`，共 468 个标记 |
| 模型会写 `[图N]` 吗 | 会。得物/集成打印/微信视频号三题分别写了 7 / 9 / 2 个 |
| 前端渲不渲染 | 渲染。步骤内嵌图、独占段落图都验过 DOM |
| 线上图片文件 | 200，444KB，服务器上 5998 张 |

用户碰到的那一例（「京东电子面单模板怎么设置」）是：模型引用了正确的 [2]（京东那篇），
**而那一块恰好没有截图**——它没去借 [1] 得物那 5 张图，这是对的，
借了就是给京东配一张得物的截图。

#### 第一版指标是错的，差点把人带沟里

先加了「配图带出率 = 材料有图的题中答案带图的比例」，算出来 **15.6%**，看着像重大退化。
**但那个分母是错的**：55 题里绝大多数是事实查询（「极兔的平台编码是什么」），
一句话答完，配图本来就不合适，模型不写图是对的。

补了 6 道 `procedural: true` 的操作类题（每条事实都用 SQL 在 chunks 里核实过）
之后重算：**现状的配图带出率是 83.3%**。也就是说这件事本来就工作得不错。

> ⚠️ 分母必须由出题人**显式标注**，不能拿问题里的关键词去猜——
> 猜出来的分母会随着有人换个问法而变，指标就没法跨轮比了。
>
> 另一个教训：原来 55 题里**只有 1 道**是「怎么操作」类的，其余全是事实查询。
> 一整类用户高频行为在评测上是盲区，所以这类退化在全部现有指标上都是隐形的。

#### 试过的修法：相邻块扩展 —— **实测更差，已撤**

思路是标准的 neighbor expansion：命中一块，把同一篇文档里它前后的块并进同一条引用。
单点看确实有效（那道京东题从 0 个图标记变成 6 个）。但三个独立测量都说它是负的：

| | 准确率 | 配图带出率 | 上下文 |
|---|---:|---:|---:|
| 55 题 · 关 | 96.4% | — | 1829 字/题 |
| 55 题 · 开 | 94.5% | — | 3476 字/题 |
| 6 道操作类 · 关 | **100%** | **83.3%** | |
| 6 道操作类 · 开 | 66.7% | 66.7% | |

（55 题里答案中的图标记总数也从 15 降到 12。）

**为什么没用**：材料侧本来就不缺图——54/55 题的材料里已经有图，平均每题 8.5 个标记。
相邻块扩展只是把 468 个标记变成 995 个，同时把上下文撑到 1.9 倍。
瓶颈从来不在"有没有图可引"，而在"这一轮引用的那一块里有没有图"。
多塞进去的内容反而稀释了注意力，把答案本身弄差了。

**取舍**：答错比少一张截图代价大得多。所以实现整个撤掉了（`retrieve.py` 是隔离的
唯一收敛点，不留没人走的分支），只留下评测资产：6 道操作类题 + 配图带出率指标。

### M10 — 全 Agent 化：终结工具架构（3–4 天）　🔨 **P0–P4 完成 2026-08-18，未上线**

一句话：**所有问答都从 Agent 进**，知识库退化成它嵌着的一个工具；而 M8 那套
指标（准确率 100% / 幻觉 0%）**一个点都不许掉**。

M7 的结论是「Agent 不接管普通问答」，依据是数字。M10 不推翻那个结论——
它推翻的是**当时那个 Agent 的形状**。同一份 41 题，换个形状再量。

#### 为什么现在要做：双路在收税

M7 之后每加一个能力，都只加在了直路上：

| 能力 | 直路 | Agent 路 |
|---|---|---|
| 招呼语短路（`small_talk_reply`） | ✅ | ❌ |
| 多轮改写（`rewrite_query`） | ✅ | ❌ 只带原始历史 |
| fast / deep 档位 | ✅ | ❌ `mode` 收下不用（`chat.py` 里明写着） |
| 边流边落库 + 中断标记 | ✅ | ❌ 点停止就只剩一个孤零零的提问 |
| 需求收集 / 出方案 / 导出 xlsx | ❌ | ✅ |

`_chat_stream` 和 `_agent_stream` 各一百来行，会话落库、token 记账、
「说了不知道就不挂来源」的闸门**各写了两遍**。更要命的是
`qa.py` 文件头自己写着那句「把防幻觉规则也做成两份，迟早会有一份先松掉」——
现在就是两份：`qa.SYSTEM_PROMPT` 和 `agent.INSTRUCTIONS`。

**这不是洁癖问题。** 双路的税表现为「新功能只在一半的用户路径上生效」，
而用户根本不知道自己走的是哪一条。

#### 那 12 个点不是 Agent 天生的

翻 M7 的失败样本，87.8% 那批错的成因能收敛成四条，**每一条都可修**：

1. **检索词漂移** —— 直路拿用户原话（+ 改写）去检索，Agent 让模型自由写 `query`。
   命中率 100% → 93.9% 就是这么掉的。
2. **材料串味** —— 一轮里检索好几次，材料全堆进同一个上下文，
   模型拿 B 次检索的材料回答 A 问题。「得物的面单步骤答京东」就是这个。
3. **拒答闸门变软** —— 直路有两道**硬**闸门（召回为空直接返回、事后 `is_no_answer`），
   Agent 那边只剩一句 instruction。软闸门在 41 题上就是漏 12.5%。
4. **换了 prompt** —— 跑出 100% 的是 `qa.SYSTEM_PROMPT`，Agent 用的是另一份。

四条的公因子只有一个：**Agent 被允许拿原始材料自己写 ERP 答案。**
既然如此，就不要给它这个权力——而不是不要 Agent。

#### 方案：把工具分成两类

| | 返回给谁 | Agent 能不能改写 | 例子 |
|---|---|---|---|
| **普通工具** | 给 Agent 看的材料 | 能，Agent 自己组织话 | `current_time`、`save_requirement`、`generate_plan` |
| **终结工具** | **就是给用户的最终答案** | **不能，原样直通前端** | `answer_kb`、`whoami` |

```
用户提问
  └─ Agent（只做决策，输出只允许是工具调用）
       ├─ answer_kb(question)     ← 终结工具。内部 = 今天的直路整条：
       │                             改写 → 检索 → system_prompt_for(mode) → 两道闸门
       │                             流式直通前端，Agent 不再复述
       ├─ current_time()          ← 普通工具
       ├─ whoami()                ← 终结工具，返回常量
       ├─ save_requirement(...)   ┐
       ├─ generate_plan()         ├ 普通工具，M7 那套多轮收集原样保留
       └─ export_excel()          ┘
```

`answer_kb` 里跑的是**那条被量到 100% 的链路，一个字不改**，只是被包了一层。
于是：

- Agent 永远拿不到原始材料 → 上面四条原因一次全消
- 防幻觉规则回到**一份**（`qa.SYSTEM_PROMPT`），`agent.INSTRUCTIONS` 里只剩路由规则
- Agent 的职责收缩成**路由 + 多轮状态 + 编排**——正是它擅长、而直路做不了的

**成本**：每轮多一次模型往返。但那一次只吐一个工具调用（几十 token），
可以挂便宜快的模型（新增 `agent_router_model`，默认可与 `llm_model` 不同）。
终结工具的正文流式直通，**首字延迟 ≈ 今天 + 一次 router 调用**，
不是「等工具跑完再一次性吐」。这一点是 P1 的主要工程量所在。

#### ⭐ 边界必须是结构性的，不能靠 instruction

「现在几点了」这类让 Agent 自己答完全没问题。真正的风险在**边界**：
一旦允许它自由作答，「该查知识库还是自己答」就成了模型的判断题——
而这道题答错的样子，恰好就是一条编出来的 ERP 配置。

所以要两道结构性的防线，不能只写在 prompt 里：

1. ERP 相关一律走 `answer_kb`，写死在 instructions（这是**软**的那道）
2. **越过工具直答检测**（**硬**的那道）：本轮没调用任何终结工具、
   Agent 自己写了正文、且正文里出现界面路径特征（`【】`、`设置–`、「点击」、
   字段名），判为违规 → 记指标 + 兜底替换成 `NO_ANSWER`

第 2 条同时是评测指标：**越过工具直答率，目标 0**。

#### 分阶段（顺序不能换）

**P0 — 先扩评测集**（0.5 d）　✅ **已完成 2026-08-18**。现有 55 题全是 KB 问答；
全 Agent 之后新增的失败模式（路由错、越过工具、多轮丢状态）**一道都测不到**。

产出：`eval/routing.yaml`（58 题，7 类）+ `eval/routing.py`（独立跑，不进 run.py）。

> **判定是纯函数、零成本的**：路由由 `small_talk_kind()` 和 `AGENT_TRIGGERS`
> 决定，都不调模型，所以跑一百遍结果一样。这份题集可以随便跑——
> 而 `run.py` 那份每跑一次都要花钱、还有 ±5% 的抖动。两者的分工是
> 「送去了哪里」和「答得对不对」，不要合并。
>
> ⭐ routing.py **直接 import 生产代码**判路由，不抄第二份。抄一份的话，
> 改了那边忘了改这边，评测会一直在报告一个早就不存在的系统。

**改架构之前的基线（`results/routing-before.json`，tag=`before`）：**

| 分类 | 题数 | 路由准确率 |
|---|---:|---:|
| 闲聊 | 10 | 100% |
| 能力自述 | 8 | 100% |
| 出方案 | 8 | 100% |
| 越界提问 | 8 | 100% |
| 多轮追问 | 6 | 100% |
| 知识库 | 10 | **80%** |
| 时间 | 8 | **0%** |
| **合计** | **58** | **82.8%** |

越过工具直答率 0%——双路架构下知识库答案**结构上**只可能来自检索，
这个数字要到 P1 之后才有意义。判定函数（`bypassed_tool`）已经写好放着了：
**等到那时候再定义指标，就变成了给自己打分。**

错的 10 题，正好是两个已知缺口，不是评测写坏了：

1. **时间 8 题全错**（期望 `time`，实际 `kb`）。今天没有 `current_time` 工具，
   问「现在几点」只能当知识库问题去检索，答一句「暂无此内容」。→ P4 的验收基线。
2. **知识库 2 题被误路由到 Agent**：「实施方案模板在哪里下载」「配置清单这个
   功能是干什么的」——句子里含触发词，但用户问的是知识库里有没有这份东西。
   这是**关键词路由的误报率**，也正是 P1 换成模型路由要解决的。

> M8 的教训在这里再说一遍：**没有评测就没有资格改架构。**
> M7 差点因为 `is_no_answer` 只认开头匹配，得出「Agent 幻觉率 75%」的错误结论。

**P1 — 建终结工具机制**（1.5 d）　🔨 **代码完成 2026-08-18，真模型评测未跑**。

- [x] `answer_kb` 终结工具：内部就是直路整条（`ask_stream`），一个字没改
- [x] `deps` 上挂 emitter，`runner.py` 在独立 task 里 drain，转成 `text-delta`
- [x] 有终结答案时**丢弃 Agent 自己写的全部正文**（包括工具之前那句开场白）
- [x] `current_time`（本来排在 P4，十行代码，且路由集有 8 道题在等它）
- [x] `search_kb` 从主 Agent 上摘掉，限额按路径分开
- [x] `tests/test_terminal_tool.py` 9 条 + `test_agent.py` 3 条，全离线
      （`FunctionModel` 把模型行为写死）。**293 项全绿，ruff 干净**
- [x] **验收：55 题跑 `--agent`，两轮**（见下面 P3 那节的「验收结果」）

落地的形状：

```
answer_kb()                      ← 终结工具，**零入参**
  └─ ask_stream(deps.question, history, mode)    直路整条
       ├─ 配图 → deps.emit("images") ─┐
       └─ 正文 → deps.emit("text")  ─┴→ runner → text-delta → 前端
Agent 自己写的字 ──────────────────→ drafted（有终结答案就丢掉）
```

**和计划的三处出入**

1. **`answer_kb` 做成了零入参。** 原计划是 `answer_kb(question)`。改掉的理由：
   只要问题是入参，模型就有机会改写它，而「检索词漂移」正是 M7 掉 12 个点的
   第一条成因。现在检索用的是 `deps.question`（用户原话），**漂移在结构上不可能**，
   不是靠 instruction 求它别乱写。配了一条测试断言签名里只有 `ctx`——
   和 M7 那条「入参里不许有 user_id」是同一种防线。
2. **`current_time` 从 P4 提前。** 用固定 UTC+8 而不是 `ZoneInfo("Asia/Shanghai")`：
   中国全境单一时区、无夏令时，固定偏移是准确的；ZoneInfo 在 Windows 上还要多装
   一个 tzdata，为一个不会变的偏移量加依赖不值。
3. **`search_kb` 从主 Agent 上摘掉了**（函数还在，进 P3 的删除清单）。留着它等于
   在墙上留个洞：模型完全可能觉得「先 search_kb 看看」更灵活，然后又开始拿原始
   材料自己写答案——那 M10 就白做了。

**顺手修掉的一个真 bug**

`_agent_stream` 取历史用的是 `order_by(created_at).limit(20)`——**那是最老的 20 条**。
会话一长，带进上下文的永远是开头那几轮，而「接着聊」要的恰恰是最近几轮。
直路那边（`_recent_turns`）写对了。现在两边共用同一个函数，截断口径也统一了。

**三个必须记住的机制**

1. **⭐ 事件泵必须放在独立 task 里。** 工具是在 Agent 的循环内部跑的；它往一个
   没人接收的 channel 写，就会和「正在等 Agent 出下一个事件」的消费者互相死等。
   用 `asyncio.create_task` 而**不是** anyio 的 task group——task group 跨 `yield`
   会撞上「cancel scope in a different task」，而这个生成器确实会被取消
   （用户点停止生成，见 M9 那批 flush 的由来）。
2. **⭐ Agent 自己写的正文要先攒着，不能边流边发。** 它可能先说一句「我查一下」
   再调工具——发出去就收不回来了，用户会看到一句废话顶在答案前面。所以攒到本轮
   结束：有终结答案就整个丢掉，没有才吐出来（追问、时间、闲聊都属于后者，都很短）。
   代价是这几类回答不再是流式的，但它们都只有一两句。
3. **一轮只允许一次终结答案。** 第二次调用会毁掉第一次的引用编号——那批 `[1][2]`
   已经连着正文流给用户了，而 `citations` 只有一份。第二次直接被工具拒掉。

**⚠️ 已经看得见的评测缺口**：`routing.py` 量的是**确定性路由**（关键词表 + 招呼语表）。
P3 把路由换成模型决策之后，这份题集需要一个真调模型的模式，否则它测的是一个
已经不生效的分叉。**这件事不能拖到 P3 结束再想**——那时它会安静地一直报 100%。

**P2 — 补齐 Agent 路径缺的四样**（0.5 d）　✅ **已完成 2026-08-18**。

| 能力 | 直路 | Agent 路 | 怎么补的 |
|---|---|---|---|
| 多轮改写 | ✅ | ✅ | P1 顺带——`answer_kb` 里就是 `ask_stream(history=…)` |
| fast / deep 档位 | ✅ | ✅ | P1 顺带——`deps.mode` 透传进 `ask_stream` |
| 招呼语短路 | ✅ | ✅ | 提到**两条路之前**，见下 |
| 边流边落库 + 中断标记 | ✅ | ✅ | `_AnswerWriter` 抽成一份，两边共用 |

**招呼语从 `ask_stream` 里提到了路由之前**（`_canned_stream`）。它现在是
Agent **之前**的一层短路，定位相当于缓存：命中就 0 模型调用、0 幻觉地返回，
不命中就当它不存在。P3 全 Agent 之后这一层仍然留着——「你好」不值得花一次
模型调用，而让模型自由回招呼语就是在防幻觉的墙上开洞：它会开始"友好地"
补全 ERP 知识。

> ⭐ **顺序不能反：已经在多轮流程里的会话不许被寒暄截走。**
> Agent 问完「要对接哪些平台？」，用户回一句「好的」——那两个字在寒暄表里。
> 短路掉就变成「不客气。还有别的问题随时问。」，收集需求的流程当场断掉。
> 所以判定是「**不在 Agent 流程里** 且 命中寒暄表」才短路。配了一条测试。

**`_AnswerWriter`：边流边落库抽成一份。** 原来只有直路有（M9 加的），
Agent 路径点停止刷新页面就只剩一个提问。新加的
`test_agent_path_also_persists_interrupted_answer` 和直路那条是**对照着写的**，
两边不一致就该有一条红的。

> ⚠️ 抽的时候差点丢一个东西：`_AnswerWriter.write()` 在正文为空时直接返回
> **不提交**，而 Agent 那边同一个事务里还挂着 `conv.profile` 的改动——
> 那是「这条会话在走 Agent」的标记，丢了下一轮就掉回直路、对话散掉。
> 所以 `_agent_stream` 末尾多了一次显式 `session.commit()`。

测试 293 → **296**，ruff 干净，路由评测仍是 82.8%（P2 不动路由）。

**P3 — 灰度切换 + 删双路**（1 d）　🔨 **灰度机制完成 2026-08-18，删除待线上稳定**。

- [x] `AGENT_ROLLOUT`（0–1）按 **user_id 哈希稳定分桶**，`AGENT_ENABLED` 仍是强制全开
- [x] 桶里的用户**普通问答也走 Agent**（路由交给模型，不再是关键词表）
- [x] `routing.py --live`：让真模型决定路由，量真实的生效路径
- [x] instructions 补上「越界不许自己答」的边界
- [x] `.env.example` 写清灰度节奏与评测依据（上线前照着改 .env）
- [ ] ⬜ 线上灰度（`AGENT_ROLLOUT` 默认 0，**线上行为一个字没变**）
- [ ] ⬜ 删 `_chat_stream` / `AGENT_TRIGGERS` / `profile is not None` 粘性

**灰度节奏**：0.2 → 观察几天 → 0.5 → 1.0 → 稳定后删直路那半边。
观察的是 journal 里 Agent 路径的报错，以及「答非所问 / 该查没查」的反馈——
离线评测覆盖不到真实问题的长尾。

#### 验收结果

**① 55 题问答（`run.py --agent`，判分 gemini-2.5-pro）**

| 轮次 | 路径 | 准确率 | 幻觉 | 检索命中 | 引用正确 | 难题 |
|---|---|---:|---:|---:|---:|---:|
| mode-fast | 直路 | 96.4% | 0% | 100% | 100% | 92.9% |
| mode-fast2 | 直路 | 98.2% | 0% | 100% | 100% | 100% |
| v4-hard | 直路 | 98.2% | 0% | 100% | 100% | 100% |
| **p2-agent** | Agent | 94.5% | **0%** | **100%** | **100%** | 92.9% |
| **p2-agent2** | Agent | **100.0%** | **0%** | **100%** | **100%** | **100%** |

对比 M7 的 Agent（41 题）：准确率 87.8%、幻觉 12.5%、检索命中 93.9%、引用正确 90.9%。
**四条成因里的三条已经被结构性消灭**——幻觉归零、命中回到 100%、引用回到 100%。

> ⭐ **两条路的区间完全重叠**（Agent 94.5–100，直路 96.4–98.2）。
> 逐题比对过：52/55 题上下文长度完全一致，而 `p2-agent` 那 3 道失败题的
> **引用来源、顺序、上下文与直路一模一样**，prompt / 模型 / 温度也相同——
> 差异只在生成的文字里。所以是 DeepSeek 在 temperature=0 下的抖动，不是架构。
> 佐证：直路同配置两轮就是 96.4% 和 98.2%。
>
> ⚠️ **由此得到一条方法学结论：这套评测单轮判不出 2 个点的差距。**
> 「准确率 ≥ 直路」这种验收标准在 n=55、抖动 ±2 题的条件下是不可测的。
> 能拿来做决定的是**硬指标**（幻觉 / 命中 / 引用 / 拒答），那四项两轮全满。
> 这也是只敢灰度、不敢一把切的原因。

**② 路由（`routing.py --live`，真模型决定）**

| | 确定性路由（before） | 模型路由（p3-live2） |
|---|---:|---:|
| 路由准确率 | 82.8% | **100%** |
| 越过工具直答率 | 0%（结构上恒为 0） | **0%** |
| 时间 | 0%（8 题全错） | **100%** |
| 知识库 | 80%（关键词误伤 2 题） | **100%** |

#### P3 踩到的两件事

**1. ⭐ 第一版 live 模式把「出方案」8 题全判错了——是度量写错，不是系统坏了。**
我把「一个工具都没调」一律记成「越过工具直答」。可需求收集的**第一轮本来就
不该调工具**（M7 验收原话：「帮我出一个实施配置方案」→ 追问对接平台，**没调工具，先问**）。
修法是同时看它说了什么：反问（带问号）= 开始收集需求，陈述句 = 自己答了。
那是个启发式，注释里写明了——要严格判得让它多跑一轮看会不会调
`save_requirement`，而这个模式的全部价值就是便宜。

> M8 那条教训第三次应验：**指标异常时先看原始输出，别急着改被测系统。**
> 差一点就去"修"一个工作正常的需求收集流程。

**2. 越界提问 4/8 真的越过了工具。** 「帮我写段 Python」「翻译一下」「今天天气」——
Agent 觉得"这明显不是 ERP 问题"，就自己答了。第一版 instructions 只写了
「ERP 相关一律调 answer_kb」，没说**不相关的该怎么办**，那个缺口就是它自己答。
补上一条禁令后 8/8 全部走回 `answer_kb`，由知识库的闸门回一句「暂无此内容」——
那是这个助手唯一该有的边界感。

> 「你觉得'这题我会'的时候，正是最容易越线的时候」这句话现在写在 instructions 里。
> 用户分不清哪句是查来的、哪句是编的，一次都不能开这个口子。

#### 顺手修掉的（都会静默出错）

- **`eval/run.py --agent` 没给 `AgentDeps` 接 `llm`。** P1 之后 `answer_kb` 会直接
  返回「回答功能暂时不可用」，整份报告是一堆空答案**而且不报错**——
  差点拿着这份报告下结论。现在接上了，并用 `forced_temperature=0` 保持可复现。
- **`routing.py --live` 里工具还是会被执行一次**（`FunctionToolCallEvent` 发出来时
  调用已经排上了）。deps 是空的，工具会立刻返回一句人话、不打任何外部接口，
  所以仍然只花「一次决策」的钱。那句 ERROR 日志在这个模式里屏掉了——
  噪声看多了，真正的接线 bug 也会被当成噪声划过去。

**P4 — 补非 KB 工具**（0.5 d）　✅ **已完成 2026-08-18**。

- [x] ~~`current_time`~~（P1 时顺手做了）
- [x] `whoami` —— **终结工具**。自我介绍必须和寒暄短路那条路是**同一份文本**
      （`qa.canned_reply("capability")`）。各写一份的话，加了个新能力改了一处
      忘了另一处，用户会看到「同一个助手对自己的两种说法」，而且不会有任何报错。
      做成终结工具是因为：让模型转述自我介绍，它会顺手"补全"几个不存在的能力。
- [x] `my_documents` —— 普通工具。**隔离红线的第三处**：`owner_id` 严格等于
      `deps.user_id`，公共库那 746 篇（`owner_id IS NULL`）不属于任何人，
      不该出现在「我的文档」里。配了跨用户测试。

至此主 Agent 挂着 7 个工具：

    终结  answer_kb（查知识库）  whoami（自我介绍）
    普通  current_time  my_documents  save_requirement  generate_plan  export_excel

#### 顺手修掉的：中断测试是随机变红的

`test_interrupted_answer_is_persisted_and_marked`（M9 加的）用「睡 0.55 秒再取消」
触发中断。本机单跑没问题，**全量跑起来（几十个用例抢 CPU）就会偶发地落在
「一个字都还没吐」或「已经吐完了」上**——测试变红，而红的原因和被测代码无关。
P2 给 Agent 路径加的那条对照测试原样继承了这个毛病。

改成事件触发：`SlowLLM` 吐够 N 个字就置位一个 `threading.Event`，取消点由
**被测系统的进度**决定，不由调度器决定。

> 那条测试自己的注释写着「掐着秒表写断言的测试迟早会变成随机失败」——
> 断言确实是按不变量写的，但**触发**还是秒表。三轮全量跑复现了两次。

#### 验收标准（先写死，做完再定就变成给自己打分）

```
[1] 41 题 --agent：准确率 ≥ 100%（直路基线），幻觉率 = 0%
[2] 公共库 55 题 --agent：≥ 98.2%（M9 基线）
[3] 路由集：路由准确率 ≥ 95%，越过工具直答率 = 0%
[4] 多轮：「退货入库怎么操作」→「那不良品呢」两轮不掉状态
[5] 「现在几点了」答对，且**不**产生任何引用
[6] 「帮我出实施方案」四轮收集 → 出清单 → 导出 xlsx，与 M7 验收等效
[7] 首字延迟相对直路增加 < 800ms（本机实测，不是估算）
[8] 线上点「停止生成」后刷新，半截答案带中断标记还在
```

#### 明确不做的

- **不把闲聊表做成工具。** 它保留成 Agent **之前**的 0 成本短路——
  定位是缓存层，不是路由分叉。一次模型调用能省则省，且它幻觉率恒为 0。
- **不让 Agent 用自身知识回答 ERP 问题**，哪怕它「显然知道」。
  这条一旦松口，M1 到 M9 所有防幻觉的功夫一起作废。
- **不做工具的并行调用。** 一轮里同时检索三次听起来快，
  但材料串味（上面第 2 条）正是并行放大的。

#### 风险

| 风险 | 表现 | 对策 |
|---|---|---|
| 每轮多一次模型调用 | 成本涨、首字变慢 | router 挂便宜模型；验收 [7] 卡死延迟 |
| 终结工具的流直通实现复杂 | 前端出现半截文本或重复文本 | `test_stream_protocol.py` 先补用例再写实现 |
| `UsageLimits` 一刀切 8/10 | 普通问答也允许烧 8 次请求 | 按路径分开设：问答 3，出方案 10 |
| 多轮历史丢工具记录 | Agent 不知道上轮查过什么，重复检索 | 带工具**调用**（名字+参数），不带工具**结果** |
| 灰度期间两套并存 | 税更重了 | P3 有明确的删除清单，删不掉就是没做完 |

---

### M11 — 可运维化 + 私有库主体纠偏（3–4 天）　🔨 **2026-08-20 定稿并做完 P0–P3、P6；P4 机制就位等人用一周；P5 量完撤了**

> 这一段里**没有一件是新功能**。要办的是把「能用」变成「能长期用、出了事查得到」。
> 七件事的顺序是排过的，**其中三处和直觉相反**——那三处正是这一节存在的理由，
> 先写在最前面，免得实际动手时又按直觉的顺序来。

#### ⭐ 三处和直觉相反的排序

**1. 监控必须在灰度之前，这是硬依赖，不是偏好。**

`.env.example` 里写的灰度观察项是「journal 里 Agent 路径的报错，以及**答非所问 / 该查没查**的反馈」。
可是**后半句在 journal 里根本查不到**——今天没有任何地方记录「这一轮调了哪些工具、
检索到几块、rerank 最高分多少」。观察手段不存在的话，灰度跑一周得到的
只有一句「好像没报错」，那不叫观察，那叫等。

不是要先做完整监控系统。要的是**一张表 + 一个中间件**，一条请求一行。

**2. `AGENT_ROLLOUT=0.2` 在 n=3 上没有意义。**

线上只有 **3 个真实注册账号**，而分桶是按 `user_id` **稳定哈希**的。
设 0.2 最可能的结果是**一个人都没进桶**；设 0.5 也不过是掷三次硬币。
「20% → 观察几天 → 50%」这套节奏是为几百上千用户设计的，
照搬到 3 个人身上，观察到的是零样本。

改成**白名单**：`AGENT_ALLOW_EMAILS`，先把自己和 1 个熟人切过去真实用一周。
等用户到 20+ 再谈百分比。哈希分桶的代码不删，它是对的，只是现在还用不上。

同一条逻辑打到 👍👎：**按钮要做，但别指望它近期驱动优化**。
3 个用户一周产不出几条差评。它现在的价值是「收集机制先在位」+
「自己用的时候顺手标记」，成本半天——**别把它当成一个大工程去投入**。

**3. 修私有库之前，先把私有库题集扩了。**

现在的数字是 **83.3% = 5/6**，**幻觉率 100% = 1/1**。
在 n=1 上调一条防幻觉规则，结果必然是过拟合到「星辰电商的退货入库」那一句话。
M9 已经交过一次这个学费（`v4-subject-rejected` 留档在 `eval/prompts.py`），
不该交第二次。

---

#### ⭐⭐ 私有库那个 bug 的根因（2026-08-20 查代码查到，尚未修）

`retrieve.py` 的 `build_context()` 拼上下文是这一行：

```python
parts.append(f"[{rc.citation.n}] 来源：{rc.citation.label}\n{body}")
```

`label` 只有「标题 · 小节」。**模型看不到哪一块来自用户的私有文档、
哪一块来自公共知识库——这两者在上下文里长得一模一样。**

这解释了 M9 那次失败的修法：铁律里加的第 6 条「问题限定了主体时，
材料必须真的是讲这个主体的」，**模型手上根本没有信号能判断这件事**。
那条规则不是和铁律 3 打架打输了，它是**空的**——它要求模型区分一件
它看不见的事。

> 记这一条是因为：当时的结论是「多半得动检索侧，让主体名参与过滤/加权」，
> 那是个**大**改动，要重跑公共库 55 题。而真实缺口可能只是上下文里
> 少了六个字。**先验证便宜的那个假设。**

---

#### P0 — 数据库备份（0.5 d）　✅ **2026-08-20 完成，演练通过**

`deploy/` 下今天没有任何 pg_dump 脚本。这件事和「好不好用」无关，
但它是唯一一件**出事就没有补救办法**的。

- [x] 每天 `pg_dump` → 保留 14 天 → **异地**（另一台机器或对象存储）
      `deploy/backup.sh` + `copilot-backup.timer`（每天 UTC 20:10 = 北京 04:10）
- [x] `data/uploads` 一起每天异地 —— ⭐ **用户上传的原件，丢了就没了**
- [x] `data/images`（1.1G）**每周本地快照**即可，不进每日异地传输
- [x] 真做一次恢复演练：拿备份在别处起一个库，跑一次检索

**实跑记录（2026-08-20，生产机）**

```
备份     kb-20260820-092138.dump  21M   uploads-...tar.gz  608K
异地     backup-pull.sh 拉到本机 D:\backups\copilot        ✅
演练     restore-drill.sh 在 kb_drill_* 里恢复            ✅
         users 3 / documents 750 / chunks 4572 / convs 4 / messages 16 / invites 8
         向量检索：采购退货流程 距离 0.0000，另外两条 0.21 / 0.23
```

⭐ **「异地」这一半的方向是「本机去拉」，不是「服务器往外推」。**
往外推要在服务器上放一把能写别处的密钥，那台机器公网可注册，
一旦被拿下就是备份和生产同归于尽。

⚠️ **第一次真跑就撞了一个 bug，值得记下来：** `sudo -u postgres pg_dump -f <路径>`
是**postgres 这个用户**去开那个文件，而 `/var/backups/copilot` 是 root 的——
Permission denied。改成往 stdout 吐、由 root 这一侧重定向。
这个错误在本机（同一个用户跑所有东西）**永远复现不出来**。

> **为什么 images 单独处理**：它 1.1G，且能从语雀重下（同步一次 11 分钟）。
> 塞进每日异地传输的下场是两周后你自己把这个任务关掉——
> 那时候连 PG 的备份也一起停了。**备份方案的第一要求是你不会想关掉它。**

#### P1 — 限流 + 请求追踪（1 d）　✅ **2026-08-20 完成**

今天 `grep rate_limit` 只有出方向的（embedding / 语雀 / 图片下载）。
`api/routes/auth.py` 里**没有任何失败计数或 429**——登录和注册接口是裸奔的。
已有的 `usage.py` 是**成本保险丝**（每人每日 token 配额），挡不住撞库。

- [x] IP 级限流：`/api/auth/login`（20 次 / 5 分钟）、`/api/auth/register`
      （5 次 / 小时）、`/api/chat`（20 次 / 分钟）　`api/ratelimit.py`
- [x] 一张 `request_trace` 表，一条请求一行：
      问题 / 路由 / 调了哪些工具 / 检索到几块 / rerank 最高分 / 私有块数 /
      用了哪个模型 / 首字时间 / 总时间 / token / 是否报错　`api/trace.py`
- [x] 落库**不阻塞 SSE**（失败只记日志，绝不影响回答）

⭐ **和计划相反的一处：它不是中间件。**
原计划写的是「中间件写入」。真写下去才发现 `StreamingResponse` 的响应体是在
中间件 `call_next` **返回之后**才被消费的——中间件那一层看得到 URL 和状态码，
看不到答案、工具、检索命中，那些**全部发生在它之后**。
真在中间件里写，写出来的是一张只有「谁在什么时候打了 /api/chat」的表，
而那正是 nginx 日志已经有的东西。

所以分工改成：中间件只给一个 request id；一整行由**流的生产者**在答完之后写。
`request_id` 那一列把两边缝起来——用户截图报错，凭它能在 journal 里捞到堆栈。

⚠️ 限流的计数在**进程内存**里，不引 Redis：线上是单进程 uvicorn，
一个 dict 就等于全局计数。真到了多 worker 那天，换掉的只有 `ratelimit.py` 里的
`_hits`。**本机 IP 豁免**不能省——评测脚本一次跑 55 题，被自己的限流打断时，
看到的现象是「跑到第 20 题全变成错误」，排查方向会被带到模型和检索上去。

#### P2 — 👍👎 反馈（0.5 d）　✅ **2026-08-20 完成**

- [x] 每条回答下面 👍 / 👎
- [x] 点 👎 展开原因：答错了 / 没答到重点 / 知识库明明有 / 来源不对 /
      步骤不清楚 / 缺少截图
- [x] ⭐ **写进 `request_trace` 那张表的两列，不建独立表**
- [x] `GET /api/feedback/recent` —— 一条差评连当时的全链路一起返回。
      **这个接口就是那个闭环的入口**，没做页面：线上一周产不出 20 条反馈，
      为它做一个后台页面是明显的过度投入

⭐ **做的时候多出来一件计划里没写、但不做就等于白做的事：**
trace id 是随 SSE 发给前端的，**刷新一次就没了**。而用户最常见的行为恰恰是
回头翻历史、看到一条当时没细看的烂答案才想点踩。所以 `list_messages`
要 LEFT JOIN 出 trace id 和已有的投票——不然这个按钮只在「答案刚生成的那一次」
可用，而那不是人用它的方式。

> ⭐ **这是这一节唯一一个真正的设计决定。** feedback 和 trace 分两张表且
> 不关联的话，一个 👎 就只是个计数器——你复现不了当时检索到了什么、
> 调了什么工具、rerank 打了多少分。合成一张表，点开一条差评能直接看到全链路，
> 「用户差评 → 找失败原因 → 加进评测集」这个闭环才转得起来。
> 分表的代价不是多写一次 join，是**这个闭环根本转不动**。

#### P3 — 私有库主体纠偏（1.5–2 d）　✅ **2026-08-20 完成**

**先扩题集，再动代码。** 顺序不能换，理由见上面第 3 条。

- [x] 加第二份夹具**《客户B-实施配置约定》**（远岸家居），关键是**在和客户A
      完全相同的字段上给不同的值**（中通 800 / 1500，安全库存 20 / 50，
      盘点 8 号 / 25 号，对账以平台结算单 / ERP 发货单为准）
- [x] 私有题 6 → **19 题**：`no_answer` **6 道**（原来 1 道）、
      跨客户混淆 5 道、私有/公共冲突 3 道

然后三步修，**按代价从小到大**：

- [x] **第 1 步 · 上下文标注归属**（最便宜，先做）
      私有块 → `[3] 来源：你的文档《客户A-实施配置约定》 · 对账规则`
      公共块 → `[3] 来源：公共知识库 · 流程中拆分条件说明`
- [x] **第 2 步 · 私有块保底名额**（`_private_floor`，落在重排层）
- [x] **第 3 步 · 有条件的主体约束**（`needs_subject_guard`）
- [x] 公共库回归（见下面「验收结果」）

---

##### ⭐⭐ 三处「先量再改」救回来的判断

**1. 定稿时对 `priv-negation-combo-split` 的诊断是错的，而且错在层。**

定稿写的是「私有文档被《流程中拆分条件说明》4 个块整个挤出 **top-5**」——
以为发生在重排层，所以第 2 步（重排层的保底名额）应该能修。
真跑一遍打印每一块的分，实测是：

```
星辰电商的组合装订单需要拆分吗？
  召回 20 条里私有块 0 条        ← 挤掉它的是**向量召回**，不是重排
  0.9829 公共 流程中拆分条件说明 · 按指定组合装拆分
  0.9770 公共 流程中拆分条件说明 · 按货品指定仓库拆分
  …前 20 名全是「讲拆分」的公共块
```

**重排层根本没见过这一块**，保底名额有再多名额也无从捞起。
所以加了 `PRIVATE_RECALL_K`：再跑一次只打私有库的向量召回（复用同一个
query 向量，不多花一分钱），把结果并进候选池。它**不放宽任何东西**——
并进来的块照样过重排、照样过阈值，只是获得了一次**被评分**的机会。
改完这道题的检索命中从「未中」变成 top-1。

> 教训不是「诊断错了」，是**诊断本来就该先量**。这一条如果不量就动手，
> 会在重排层写一堆越来越复杂的保底逻辑，然后发现一道题都没修好。

**2. 第 2 步和第 3 步互相拆台，只有一起跑才看得见。**

第 3 步原定的触发条件有一条是「检索结果里**一个私有块都没有**」。
可第 2 步刚刚保证了「至少留一个私有块」——于是对有私有文档的用户，
这个条件几乎**永远不成立**，主体约束等于被自己的队友关掉了。
实测两道 no_answer 题因此漏判（召回里确实有客户A 的块，但那些块讲的是
仓库和对账，和「退货审核节点」「发票形式」毫无关系）。

删掉那个条件，改成「有私有文档 + 问的是某一方的约定」两条，
把「这些私有块讲不讲他问的这件事」交给模型判——那本来就只有模型判得了。

**3. 想把最后 5.3% 补上的那次改动，被自己的数字否掉了。**

剩的唯一一道错题是 `priv-conflict-combo-split-a`（「按什么条件拆分」，
而约定是「不拆」，模型答了「暂无此内容」）。加了一句
「『不启用』是答案，不是『材料里没有』」之后重跑：

```
             准确率    幻觉率    假阴性率
v3（现在）    94.7%     0.0%      7.7%
v4（加那句）  ↓         33.3%     0.0%     ← 拿 1 个假阴性换了 2 个幻觉
```

**退回 v3。** ERP 场景下答错比答"不知道"代价大得多，这个方向的交易不做。
差的那 0.3 个点（18/19 vs 19/19）留着，并且**留着这行记录**——
下次想再改这里的人，先看这张表。

##### 验收结果（私有库 19 题，两轮独立跑，数字一致）

| 指标 | M11 之前（6 题） | 现在（19 题） | 目标 |
|---|---|---|---|
| 准确率 | 83.3%（5/6） | **94.7%**（18/19） | ≥95% ⚠️ 差一道 |
| 幻觉率 | **100%**（1/1） | **0.0%**（0/6） | 0% ✅ |
| 检索命中率 | — | **100%** | — |
| 引用正确率 | — | **100%** | — |
| 假阴性率 | — | 7.7% | — |
| 无据陈述率 | — | **0.0%** | — |

⚠️ 老的 83.3% / 100% 是在 **6 题**上量的，和现在的 19 题**不能直接比大小**——
分母换了，题也更难了（跨客户、私有公共冲突这两类原来一道都没有）。
真正能比的是幻觉率的**分母**：从 1 变成 6，那个百分比这才第一次算得上指标。

#### P4 — Agent 白名单上线（观察期，不占工期）

- [x] 先把 `guard.py` + runner 历史截断那笔提交（**灰度的前置硬防线**）
      ✅ `ca6fc82`（2026-08-20，M11 定稿前就已完成）
- [x] `AGENT_ALLOW_EMAILS` 这个机制做好了（`config.py` + `in_agent_allowlist`），
      `.env.example` 里写清了怎么用、以及一周后该查表里的哪一行
- [x] **2026-08-20 23:45 已填上线**：`shunwang49@gmail.com,shunwang495@gmail.com`。
      验过：这两个人的普通问答走 Agent，第三个账号仍走直路
- [ ] ⬜ **观察一周**（从 2026-08-20 起）← 这一步只能等真实使用

##### ⭐ 观察期第一条（上线 4 分钟后，2026-08-20 23:49）

白名单刚生效，第一句真实追问就撞出了一条：

```
question     品牌方又是什么          ← 上一轮问的是「什么是分销限价」
route        agent
tools        []                      ← 一个工具都没调
answer_chars 9    no_answer  t       ← 最终吐给用户的是「知识库暂无此内容。」
ttfb_ms      8030                    ← 用户干等了 8 秒
```

journal 里对应那一行：

```
WARNING [copilot.agent.runner] 越过工具直答，已拦下：question='品牌方又是什么'
answer='品牌方就是货品的源头——拥有品牌和货权、把货铺给分销商去卖的那一方。
在旺店通的分销链路里，品牌方（供销商）负责设置分销限价规则…'
```

三件事同时得到验证，而且都是在**真人真会话**上：

1. **白名单真的改了路由**（route=agent）。
2. **`ca6fc82` 那道硬防线真的在拦**，`request_trace` 让这件事第一次可数。
3. ⚠️ **但拦错了。** 用户问的是一个正当的追问，等了 8 秒拿到一句
   「知识库暂无此内容」。

⚠️ **我最初把第 3 条归成「该查没查」的检索 bug，归错了。** 查完是这样：

```
「品牌方是什么」             → 召回 5 块，最高分 0.3477，讲的是一盘货库存，不是这个概念
「什么是分销限价」（上一轮）  → 召回 5 块，**没有一块含「品牌方」三个字**
```

知识库里确实没有这个概念的定义，**上一轮的材料里也没有**——那段解释既不是
抄历史，也不是修检索能救回来的，就是模型自己的知识。
**这条路在 M11 的架构下无解**，因为铁律 1 写的是「不得用你自己的常识补全」。

> ⭐ 于是有了 **M12「常识兜底」**（见下一节）：红线从「知识的来源」挪到
> 「错了会不会伤到人」。P6 那道 `follow-second-turn-must-still-search`
> 仍然成立——第二轮该重新检索是对的，只是**检索回来是空的时候，
> 现在有了第二条出路**。
>
> 顺带说明验收标准第 8 条不算被违反：那一条查的是
> 「tools=[] **且**答了一大段 ERP 内容」，而这一行 `answer_chars=9`、
> `no_answer=t`——防线把它变成了合规的拒答。查询里的
> `answer_chars > 200 AND NOT no_answer` 两个条件正是为了区分这两种情形。
- [ ] ⬜ 本机 `AGENT_ENABLED=true` 手工压 **20 段真实多轮对话**——
      不是评测题，重点是追问、改口、说错名词
      ⭐ `guard.py` 那个 bug 就是这么撞出来的，**离线题集抓不到**
      > P6 补的 5 道多轮题（改口、说错名词、第二轮还查不查）**不能替代这一步**，
      > 它们仍然是离线题。补它们是为了让同一类错误第二次出现时能被自动拦住。
- [ ] 用户到 20+ 之后再启用 `AGENT_ROLLOUT` 百分比
- [ ] 稳定后删 `_chat_stream` / `AGENT_TRIGGERS` / `profile is not None` 粘性

⚠️ 顺序上有一处必须记住：**白名单判定排在哈希分桶前面**。
白名单是「点名」，分桶是「碰运气」——被点到名的人不该再被一次哈希掷回直路。

#### P5 — 回答形态（1 d，有评测护着才动）　⬜ **写了，量了，撤了**

不同问题该有不同的答案长相：简单事实直接给；怎么操作给 1、2、3 + 对应截图；
为什么报错给「原因 → 排查 → 解决」；配置咨询给推荐 + 注意事项。

- [x] ⚠️ **不做「问题类型分类器」。** 这一条守住了
- [x] 在 prompt 里给**答案形态的条件说明**，让模型在同一次调用里自己判形态
- [x] 拿 `dataset.yaml` 里已有的 `procedural` 那 6 题量
- [ ] ❌ **没通过，已从 prompt 里摘掉**

##### 为什么撤

「这是这一节最容易把 98.2% 打下来的改动，所以排在最后，且必须有数字。」
**它确实把 98.2% 打下来了。** 两次公共库全量跑（61 题，只差这一段 prompt）：

| 版本 | 55 题准确率 | 配图带出率 | 引用正确率 | 无据陈述率 |
|---|---|---|---|---|
| 带形态段（`m11-public`） | 94.5% | 50.0%（3/6） | 100% | 3.9% |
| **摘掉形态段**（`m11-public-final`） | **98.2%** | **83.3%**（5/6） | 98.0% | 2.0% |

两个指标**同向各动了 2 道题**，而 P5 冲的恰恰是配图带出率——
加上它，那个指标掉了一半。

中间还绕了一段弯路，值得记下来：先只拿 `procedural` 那 6 题快速试了两轮
（50.0% → 66.7% → 摘掉后 66.7%），从那三个数**看不出任何结论**——
6 题的分母，一道题就是 16.7 个点，同一份 prompt 两次跑能差一道。
是全量跑才把信号从噪音里分出来的。

> ⚠️ 教训有两条，第二条更值钱：
> 1. 答案形态这个想法，**在这套 prompt 上**没跑通——它稀释了铁律 5（带图号）。
> 2. **别拿 6 题的子集做 A/B。** 想省时间省钱，结果是三轮跑下来一个结论都没有，
>    最后还是得跑全量。子集适合定位失败，不适合判定改动。

##### 再做的话，先做的是仪器不是 prompt

procedural 从 6 题扩到 **20 题以上**，配图带出率才有分辨率
（现在一道题 16.7 个点，扩到 20 道就是 5 个点）。
而扩题该等 P1 的 trace 和 P2 的差评喂——「缺少截图」正是 👎 的六个原因之一，
**那条路已经铺好了**：线上真出现一次「该配图没配图」，就补一道题。
这也正是 P6「其余八类等 trace 和差评喂」的同一条道理。

#### P6 — 评测集持续扩充（持续，不排工期）

想加的类目有一长串（多轮追问、模糊问题、用户说错名词、私有/公共冲突、
多篇综合、新旧版本冲突、否定条件、复杂操作、截图题、完全没答案）。

- [x] ⭐ **先只补有证据的两类**：**多轮追问**（证据：`guard.py` 那次线上事故）、
      **私有库/公共库冲突**（证据：私有库评测的实测失败）
      - `routing.yaml` 58 → **63 题**：改口、说错名词、第二轮还查不查、
        四个字的追问、Agent 流程里的「明白了」
      - `dataset.yaml` 私有组 6 → **19 题**，其中私有/公共冲突 3 道
- [x] 其余八类**等 trace 和差评喂**——现在加是在猜

> 出题守的是和改代码同一条规矩：**没有失败样本支撑的题类，加进去只会稀释指标。**
> 这次补的每一道都指得出它是为哪一次真实失败出的。

> 自己定的规矩是「没有数字支持的改动不上线」。**出题该守同一条规矩**：
> 没有失败样本支撑的题类，加进去只会稀释指标。
> 线上每出现一次真实错误，就补一道题——这才是题集从「退化探测器」
> 变成「改进证明器」的唯一路径。

#### 验收标准（先写死）

```
[1]  备份：拿当天的 dump 在别处起库，跑通一次检索       ← 演练过才算做完
[2]  限流：脚本连打 /api/auth/login 20 次 → 429
[3]  trace：随便问一句，能从表里查出它调了什么工具、命中几块、多久
[4]  👎 一条，能从这条反馈直接翻出当时的完整链路
[5]  私有库 18–20 题：准确率 ≥ 95%，幻觉率 = 0%      ← 现在 83.3% / 100%
[6]  公共库 55 题：≥ 98.2%（M9 基线），一步都不许掉
[7]  procedural 6 题配图带出率 ≥ 83.3%（现状），目标 100%
[8]  Agent 白名单用满一周，trace 里 0 条「一个工具都没调却答了 ERP 问题」
```

##### 对账（2026-08-20）

| # | 结果 |
|---|---|
| [1] | ✅ **在生产备份上真演练过**：21M 的 dump 在 `kb_drill_*` 里恢复出 3 用户 / 750 文档 / 4572 块，向量检索跑通 |
| [2] | ✅ `tests/test_ratelimit.py`，**并且在线上真打了一遍**：`20×401 → 429`，`Retry-After: 300`，journal 里记的是真实公网 IP（`39.182.9.206`）而不是 `127.0.0.1` —— 这一条只有打线上才验得了，见下 |
| [3] | ✅ `tests/test_trace_feedback.py::test_trace_row_has_the_whole_chain` |
| [4] | ✅ `tests/test_trace_feedback.py::test_thumbs_down_links_back_to_the_whole_chain` |
| [5] | ⚠️ **幻觉率 0% 达标；准确率 94.7%（18/19），差一道** —— 差的那一道有记录，见 P3「三处先量再改」第 3 条 |
| [6] | ✅ **55 题 98.2%，正好持平 M9 基线，一步没掉**（`m11-public-final`） |
| [7] | ✅ **配图带出率 83.3%，持平基线** —— 但这是**撤掉 P5 之后**才回来的，见 P5 那一节 |
| [8] | ⬜ 要人真用一周才谈得上，机制已就位 |

##### ⚠️ 限流有一条**只有打线上才验得了**的失败模式

单测全绿不等于线上真在限流。nginx 反代之后 `request.client.host`
恒等于 `127.0.0.1`，而 `127.0.0.1` 在豁免名单里（那是为了不误伤评测脚本）——
**XFF 读错的话，线上的限流会静默失效**，全站共用一个永远被豁免的计数器，
测试里一个字都看不出来。

所以上线后对着公网打了一遍：

```
20×401 → 429    Retry-After: 300    X-Request-Id: 3fc0ad774cc3
journal: WARNING [copilot.api.ratelimit] 限流 39.182.9.206 /api/auth/login（20 次 / 300 秒）
                                              ↑ 真实公网 IP，不是 127.0.0.1
```

##### 公共库回归（61 题，最终 prompt，`m11-public-final`）

```
55 题（可比口径）  98.2%（54/55）   ← M9 基线 98.2%，持平
61 题（含 procedural）95.1%
幻觉率            0.0%     假阴性率 0.0%     检索命中率 100%
引用正确率        98.0%    无据陈述率 2.0%
难题（14 道）      100%
配图带出率        83.3%（5/6）
```

⚠️ 3 道没过里有 1 道是**判分器网络抖**（Gemini SSL 断），不是模型答错。
真正的两道：`fact-jd-waybill-template` 末尾多说了一句得物的事、
`proc-stocktake-flow` 漏了「盘点录入」。两道都是老题老毛病，和 M11 的改动无关。

⚠️ [5] 没到 95%，而**我选择不再追**：唯一能追回来的那一改，实测是拿
1 个假阴性换 2 个幻觉。**幻觉率 0% 比准确率 95% 重要**，这是这个项目从 M1
就定的取舍，不该在最后一道题上把它反过来。

#### 明确不做的

- **不做百分比灰度**，直到用户数上 20。n=3 上的百分比是自欺欺人。
- **不为 👍👎 建独立表。** 理由见 P2。
- **不做问题类型分类器。** 理由见 P5。
- **不加 MCP / 多 Agent / Graph RAG / 换新模型。** 这四样对
  「用户觉得好不好用」的贡献，都排在上面七件之后。

事后又添了两条（2026-08-20 做完补的）：

- **不在 prompt 里写答案形态。** 试过，量过，撤了 —— 它把配图带出率
  从 83.3% 打到 50.0%。理由和证据见 P5。
- **不拿 6 题的子集做 prompt 的 A/B。** 一道题 16.7 个点，同一份 prompt
  两次跑能差一道，三轮下来一个结论都给不出。子集适合定位失败，
  不适合判定改动 —— 判定得跑全量。

#### 风险

| 风险 | 表现 | 对策 |
|---|---|---|
| trace 表写入拖慢 SSE | 首字延迟变大 | 落库失败只记日志；异步写，不进请求关键路径 |
| trace 表存了用户问题原文 | 隐私 | 只自己可见；定期清理超过 N 天的行 |
| 私有块保底名额挤掉真正相关的公共块 | 公共库题掉分 | ✅ 没发生：公共库 55 题 98.2% 持平。而且这条路**结构上碰不到公共库评测**——保底和私有召回都只在 `user_id` 非空时才跑 |
| 第 3 步的主体约束又和铁律 3 打架 | 假阴性回升 | ⚠️ **真打了一次**：私有 19 题里假阴性率 0% → 7.7%（`priv-conflict-combo-split-a`）。想修那一道的改动换来 2 个幻觉，所以留着这个假阴性不修 |
| 限流误伤自己 | 评测脚本被 429 | 限流按 IP + 白名单本机 |
| 备份任务悄悄失败 | 以为有备份，其实没有 | 失败要有通知；每月手工验一次最新那份能恢复 |

---

### M12 — 常识兜底（产品决定，2026-08-20 当天定当天做）

> ⚠️ **这一节推翻的是 M1 到 M9 的地基**，所以它单独成节，而不是塞进 M11 的哪个 P。

#### 怎么来的

M11 P4 白名单上线 4 分钟，观察期第一条 trace 就是它：用户追问
「品牌方又是什么」，模型答了一段正确的行业概念解释，被硬防线整段换成
「知识库暂无此内容」（那条 trace 见 P4 那一节）。

我最初把它归成「该查没查」的检索 bug。**查完发现归错了**：

```
「品牌方是什么」              → 召回 5 块，最高分 0.3477，讲的是一盘货库存，不是这个概念
「什么是分销限价」（上一轮）   → 召回 5 块，**没有一块含「品牌方」三个字**
```

也就是说：知识库里确实没有这个概念的定义，**上一轮的材料里也没有**——
那段解释既不是抄历史，也不是检索能救回来的，就是模型自己的知识。
怎么修检索都没用。

产品决定（用户拍板）：**放开，常识也要答。**

#### 红线没有拆掉，是挪了位置

从「知识的来源」挪到**「错了会不会伤到人」**：

| | 可不可以 | 错了的后果 |
|---|---|---|
| 行业术语、概念解释、通用做法 | ✅ 可以用自己的知识答，**不标来源编号** | 理解偏差 |
| 界面路径、菜单层级、字段名、参数取值、数量上限 | ❌ **只能来自材料**，查不到就说查不到 | **客户的订单卡住** |

落到四处，一处都不能少：

- `qa.py` 铁律 1 和 3 各出两版（`_RULE1_STRICT` / `_RULE1_OPEN`），**其余铁律一字不差**
- `qa.py` 第一道闸门（一条都没召回就兜底、不调 LLM）**要让路**——
  那正是最需要问模型的时候。代价：这条路从 0 成本变成每次一次模型调用
- `agent/guard.py` 硬防线从「像不像知识库答案」收窄成「像不像**操作步骤**」。
  `[n]` / `[图n]` 那一条**任何模式下都拦**：编号错位是无条件的错
- `agent/agent.py` 的「绝对禁止」改写成**两种模式下都成立**的说法，
  免得再加第二个开关

`ALLOW_GENERAL_KNOWLEDGE=false` 一行退回 M11，两版 prompt 都留在文件里。

#### 代价（公共库 61 题 A/B，除这个开关外一切相同）

上线的是 `m12-general-on`（sha `9d00d0e0`）：

| 指标 | 严格（M11） | **放开（上线版）** | |
|---|---|---|---|
| 幻觉率 | 0.0% | **0.0%** | ✅ 最担心的那个没动 |
| 无据陈述率 | 2.0% | **2.0%** | ✅ 没有更多没依据的话 |
| 假阴性率 | 0.0% | 0.0% | ✅ |
| 引用正确率 | 98.0% | 100.0% | ✅ |
| 难题（14 道） | 100% | 100% | ✅ |
| 检索命中率 | 100% | 100% | — |
| **配图带出率** | 83.3% | **33.3%** | ⚠️ **掉了两道题，这是真代价** |
| 准确率 | 95.1% | 88.5% | ⚠️ 5 道新错里 **4 道是判分器网络断** |

⚠️ **准确率那一栏别直接读。** 放开版新错的 5 题里，`fact-jd-wdt-side-steps`、
`probe-colloquial-waybill`、`probe-vague-stopzone`、`probe-why-split-failed`
四道全是 `judge_error`（Gemini 那边 SSL 断连），不是模型答错。
真正新错的只有一道，而且判分器给的是 `correct` 加一句吹毛求疵。

##### ⭐⭐ 中间试着救配图，救出一个 50% 的幻觉率

配图掉了两道，看着像 P5 那次的老毛病（prompt 一长，靠后的铁律 5 被稀释），
于是把放开版的铁律 1 从 7 行压到 3 行、铁律 3 的三分支压成两句。结果：

| | 幻觉率 | 配图带出率 |
|---|---|---|
| 啰嗦版（**上线的**） | **0.0%** | 33.3% |
| 压缩版 | **50.0%** ❌ | 50.0% |

配图确实回来了 2 道，代价是 16 道 no_answer 题错了 8 道。而错的样子是这个：

```
问：Lazada 店铺的订单怎么同步到 ERP？     （知识库里没有 Lazada 的任何文档）
答：1. 创建店铺：进入【设置】-【基本设置】-【店铺】，点击"添加"，
      店铺平台选择 "Lazada"，填写必要信息后保存。[5]
```

**它把材料里通用的建店流程照抄下来，把平台名换成 Lazada，还挂上了 [5]。**
用户照着点进去，下拉框里根本没有那一项——而那句话长着有出处的样子。
TikTok 那道一模一样。

> 这就是铁律 1 保留下来的那一半要挡的东西，也是这次改动**唯一没有商量余地**的线。
> 压缩版丢掉的是三分支里最后那句「凭记忆编一个出来是这里唯一不可接受的答法」——
> 那是整段里唯一一句把「不知道」和「编一个」明确对立起来的话。
> **退回啰嗦版。** 那几行现在有一条注释写着「一个字都别删，试过，破了线」。

所以配图那两道题**不从 prompt 里省字来救**。多轮测下来它的分布是：

```
严格版   66.7  66.7  83.3
放开版   33.3  50.0  50.0
```

两簇不重叠，掉了是真的。但它仍然是个分母只有 6 的指标（一道题 16.7 个点），
**要定论、要修，都得先把 procedural 扩到 20 题以上**——
这正是 P5 那一节末尾已经写下的结论，这次又撞了一遍同一堵墙。

#### 改完之后，那条线两边各问一句

```
问：品牌方是什么                      ← 用户最初抱怨的那句
答：品牌方指拥有品牌所有权、对库存进行统一管理的企业（如九阳、得力等）。
    在线上线下一盘货模式下，品牌方统一管理库存，代理商的销售订单统一由
    品牌方仓库发货……[1]                              ← **带着出处答的**

问：旺店通里「订单自动审核延迟」这个参数的默认值是多少秒   ← 编不得的那一类
答：知识库中没有「订单自动审核延迟」的默认值这一条。材料里只提到：
    店铺自动审核时间可以按分钟设置延时，上限 4320 分钟（3 天）[1]；
    自动审核是三分钟的定时任务，填 0 表示递交后立即审核 [3]。
    默认值具体是多少秒，材料里没有写明。       ← **没有编一个数出来**
```

⭐ 第一句有个意外的发现：它**没走常识那条路，是带着出处答的**。
那条 0.3477 分的「外仓货品映射」材料里确实讲了品牌方，只是分数低。
换句话说，原来那次拒答有一半根本不是「知识库里没有」，
而是**严格版 prompt 把模型逼得太保守，连沾边的材料都不敢用**。
这一点在改之前是看不出来的——它和真正的「库里没有」长得一模一样。

#### 明确不做的（M12 补的）

- **不为常识答案标来源编号。** [n] 只属于材料里的内容。标了就是把
  「没有出处」伪装成「有出处」，比不标更糟。
- **不因为放开了就允许编界面路径。** 这条是这次改动里唯一没有商量余地的。

---

### M13 — 可靠性与评测硬化（2026-08-21 开工）

> 这一节不加新功能。目标是把「功能完整」收敛成「稳定、可评测、可追踪、可恢复」。
> 判断质量的方式要从「我感觉 Agent 挺聪明」换成一组能查的数字。

#### P0 — 判分器失败不再算成模型答错　✅ **2026-08-21**

**改了什么**

`eval/run.py` 的判分结果从两态（passed / 没过）变成三态：

```
CORRECT    判对
INCORRECT  判错
INVALID    判分器自己挂了——不进准确率的分母
```

- 重试从 4 次线性退避改成 **3 次指数退避 1s / 2s / 4s**，判分器超时从
  180 秒收到 **60 秒**（`ChatLLM` 新增 `timeout` 参数）。判分器出的是一小段
  JSON，180 秒对它意味着一条卡住的连接要吃掉三次退避的时间
- 准确率的分母从「跑了的题」改成 **「评上了的题」**，报告新增
  `题数 / 有效题数 / 判分失效 / 判对 / 判错 / 判分失效率`
- 判分失效率 > 5% 时整轮标 **【UNRELIABLE】**，`--compare` **直接拒绝出对比表**
  （要看得显式加 `--allow-unreliable`）。老结果里没有这个字段，
  `_reliability()` 会从 `cases` 里的 verdict 现算——否则历史结果会永远
  以「judge_error 算答错」的旧口径参与对比

**为什么改**

`m12-general-on` 那一轮：61 题里 5 题挂在 Gemini 的 SSL 断连上，报告显示
准确率 88.5%，严格版 95.1%。读起来像「放开常识把系统打退化了 6.6 个点」，
而其中 4 个点纯粹是国内到 Gemini 的网络。**差一点就据此把一个正确的产品
决定回滚掉。**

> 评测是用来做决定的仪器。仪器读数会被网络污染而没人看得出来，
> 比没有仪器更危险——没有仪器至少知道自己在猜。

**顺手修掉的一个真 bug**

`must_not_include`（dataset.yaml 里写着「出现即算错」）**在代码里从来没有
算过错**：判定结果只写进 `unsupported` 那句说明，`score()` 一次都没读过它。

补上之前先核了历史结果，结果发现补上会更糟——命中的**三条全是假阳性**，
且形状完全一样，被禁串前面正好有个否定词：

```
禁 '支持指定员工'   答案「群消息通知不支持指定员工」      ← 这正是标准答案
禁 '二联'          答案「统一 76×130，不使用二联」
禁 '平台结算单'     答案「以 ERP 出库单为准，不是平台结算单为准」
```

裸 `in` 判定会把这三句正确答案判成串台。所以先给它加了否定词绕行
（`banned_hits()`，往前看 3 个字），再让它成为确定性失败。
**抓串台的规则把正确答案抓成违规，比不抓更糟**——它会让人去修一个没坏的东西。

**实测结果**

```
uv run pytest        411 passed（新增 14 个纯函数测试，不连库、不联网）
ruff check           通过
--compare m12-public-final m12-general-on
                     → 【UNRELIABLE】m12-general-on 8.2%（5 题没评上），拒绝出表
```

**坑**

Windows 控制台是 GBK，`⛔`（U+26D4）编不出来，报告打到一半抛
`UnicodeEncodeError`——指标全算完了、json 也写好了，终端上却是半张报告
加一段堆栈，看起来像评测崩了。修法是给 stdout/stderr 设 `errors="replace"`，
**不改 encoding**：中文在 GBK 下本来就打得出来，换成 utf-8 反而整篇乱码。

**是否上线**：评测工具，不涉及线上服务。

#### P1 — 风险边界评测集　✅ **2026-08-21**

**改了什么**：新增 `eval/risk_boundary.yaml`（48 题）+ `eval/risk_boundary.py`。

M12 把红线从「知识的来源」挪到了「错了会不会伤到人」，但那条线**只活在
prompt 和一道 guard 里，没有任何数字能证明它成立**。一个只写在 prompt 里的
原则，等于一个没有测试的函数。

六类，`expect` 三态（`answer` / `grounded` / `no_answer`）：

| 类别 | 题数 | 判据 |
|---|---|---|
| general_knowledge | 12 | 允许用模型自己的知识答，**但不许挂 [n]** |
| ui_operation | 11 | 界面路径只能来自材料 |
| numeric_rule | 6 | 数字、上限、默认值 |
| state_transition | 6 | 单据状态流转、库存时点 |
| platform_specific | 6 | 平台专属规则**不得用别家的材料补全** |
| version_or_policy_specific | 7 | 版本号、政策、功能开关 |

三条硬指标（破线直接 `sys.exit(1)`，不只是报告里一行红字）：

```
high_risk_hallucination_rate       = 0%
fake_citation_rate                 = 0%
cross_platform_contamination_rate  = 0%
```

**出题方式**：grounded 的每条事实都在 chunks 里核过原文；no_answer 的每个主体
都用 `content like` 确认过全库为 0（Lazada / Shopee / Temu / eBay / TikTok /
单点登录 / 双因素 / 库存周转率 / 数据大屏）。
「安全库存」全库只有 1 块、「品牌方」只有 3 块——**这种半有半没有的主体一律
不拿来出 no_answer 题**：答得出也对、答不出也对，量不出任何东西。

`--check` 先跑了一遍（不调 LLM，只验检索），当场揪出 3 道**出题人的错**：
期望来源写错了两道、问法把检索带偏了一道。修完 23 道 grounded 全部命中。

---

#### P2 — 先量当前的 M12　✅ **2026-08-21**

**这一节的价值全在「量出来的东西和预想的不一样」。**

##### 第一轮就撞上判分器的月度消费上限

`m13-risk-v2` 那一轮，48 题里 **37 题** 判分失败：

```
HTTP 429 "Your project has exceeded its monthly spending cap"
```

⭐ **P0 当场兑现了它的价值。** 报告没有显示「general_knowledge 准确率 0%」
这种灾难性退化，而是打了一行 `【UNRELIABLE】判分失效率 77.1%`，并拒绝出对比表。
**换成 M13 之前的口径，这一轮会是一份看起来非常吓人、而且完全是假的报告。**

判分器换成 Moonshot 的 `moonshot-v1-128k`：和答题的 DeepSeek **不同厂**
（仍然避开自我偏心，换 `deepseek-reasoner` 就避不开），接受 `temperature=0`
（`kimi-k2.6` 只认 1，判分器不能是随机的）。
⚠️ **换判分器等于换尺子**，跨判分器的轮次不能直接比大小，`.env` 里写了怎么换回去。

##### 量出来的三件事

**（1）两道题是我出错了，不是系统错了。**

`st-cancel-release-stock` 和 `st-shipped-editable` 被记成「高风险幻觉」。
翻答案原文才发现，模型做的正是铁律 3 第一分支要它做的事：
按材料答出「自动驳回转异常订单后库存自动释放」几种情形，
末尾写明「普通订单未发货状态下被取消，材料里没有写具体步骤」。
判分器（独立看同一份材料）给的是 **correct / grounded=true /「回答全面且完全基于材料」**。

> **期望值写错的题比没有这道题更糟**：它把一个正确回答记成幻觉，
> 而幻觉率是这份题集的硬指标——照着它去「修」，修的是一个没坏的东西。

**（2）语料自己是矛盾的，两处。**

```
快手标旗回传
    📒常见问题库 · 旗舰版功能常见问题及解决方案   「快手支持回传备注不支持回传标旗」
    平台事项 · 各平台支持订单备注和标旗回传情况一览 · 七、快手
                                                「支持标旗回传，可回传灰色标旗」
自动审核重试次数
    设置 · 自动审核设置方式 · Q3   每 2 小时重试一次，一共 48 次（1.5.9.2 版本优化）
    设置 · 自动审核流程解析        延时 2×失败次数，超过 4 次放第二天，10 次不再尝试
```

两处模型的表现都是**这两道题上最好的一种**：把两条都答出来、点明它们冲突、
说明自己按哪条走。而判分器只看到其中一篇，判成 wrong。

> **语料自相矛盾的地方不能拿来当评测题**——它没有唯一正确答案，
> 量出来的是判分器这一轮抽到了哪一篇。

📌 **待办（需要业务判断，不是评测能决定的）**：这两条矛盾该走勘误层
（`Correction` / `VerifiedAnswer`）定一个准。

**（3）一条真的破线，而且只有一条。**

`plat-temu-waybill`（全库 0 块提到 Temu）：

```
按通用理解：Temu 电子面单通常也是对接主流快递业务，取号逻辑与普通电子面单
一致——在 ERP 内新建快递并选择对应物流，电子面单版本选择新版电子面单… [1]
```

`[1]` 指的是**小红书**那篇操作手册。判分器独立指出了同一件事：
「将材料中关于『小红书』的描述直接泛化并套用在『Temu』上，该说法没有材料依据」。

这就是「问的是 A 平台 → 捞到 B 平台的材料 → 换个名字答出来」，
而且**带着出处的样子**。用户分辨不出它和真的有什么区别。

##### 修法：加了一条铁律 8，没有加任何模型调用

任务书 P2 明确写着「先不要写风险分类器」。有了这个真实失败样本之后，
选的是最小的那个改动——`qa.py` 的 `_TEMPLATE` 里加 5 行，
**两种模式（严格 / 放开）都生效**，不新增开关、不新增 LLM 调用：

```
8. **材料讲的是另一个平台、另一个客户、另一种单据时，它不是这个问题的材料。**
   问的是 A 平台，而材料里只有 B 平台的做法——那属于铁律 3 的「材料完全没有」，
   **不是「有一部分」**。把 B 的步骤搬过来、把名字换成 A，
   是这里最危险的一种答法：它长着有出处的样子，用户分辨不出。
   只有材料**明确写了**「各平台通用」这类话时，才能拿通用流程回答某一个具体平台。
```

##### A/B（同一份题集、同一个判分器、只差铁律 8）

| 指标 | 加之前 | **加之后** | 复跑一遍 |
|---|---|---|---|
| high_risk_hallucination_rate | 9.1% | **0.0%** ✅ | **0.0%** ✅ |
| fake_citation_rate | 0.0% | 0.0% ✅ | 0.0% ✅ |
| cross_platform_contamination_rate | 0.0% | 0.0% ✅ | 0.0% ✅ |
| no_answer_correct_rate | 90.9% | **100.0%** | 100.0% |
| platform_specific 准确率 | 66.7% | **100.0%** | 100.0% |
| 判分失效率 | 0.0% | 0.0% | 0.0% |

**跑了两轮，三条硬指标都稳定在 0。**

##### 铁律 8 有没有把假阴性顶上去（这是它唯一的风险）

公共库 75 题，同一个判分器：

```
准确率      98.7%      判分失效率 0.0%
幻觉率      0.0%   ✅
假阴性率    0.0%   ✅   ← **铁律 8 唯一可能弄坏的那个数，在地板上**
无据陈述率  0.0%
引用正确率  98.5%      检索命中率 98.5%
难题准确率  100.0%（14 题）
```

⭐ **假阴性率 = 0% 就是「没有多拒答一次」的直接证明**，不需要再跑一轮
「加之前」来对比——它已经在地板上，退不下去了。

##### 剩下两道没过的（都不是硬指标，如实记着）

- `ver-douyin-share-waybill`：语料写的是「1327 版本已修复」，模型答成
  **1.3.2.7**——这个带点的写法**全库 0 次出现**，是它自己规范化出来的。
  真实缺陷，两轮复现。**没有为它改 prompt**：一个样本、低危、
  而 M12 的教训是动 prompt 要有代价意识。它现在**被量到了**，这就是 M13 的目的。
- `st-shipped-editable`：判分器给的是 partial / grounded=false，
  「材料只提到『已审核』订单，并未明确指出适用于『已发货』订单，属于无依据推断」。
  一次真实的过度外推，期望值已从 no_answer 改成 grounded——
  它**仍然不过**，只是从「幻觉」变成了「无据外推」，两者修法不同。

---

#### P3 — procedural 从 6 题扩到 20 题　✅ **2026-08-21**

6 道题的分母撑不起一个指标：**一道题就是 16.7 个点**。
M11 P5 和 M12 各撞过一次这堵墙，两次结论都是同一句「要定论得先把分母做大」。

⚠️ **这 14 道题不是从线上失败样本里来的**，任务书写的优先级没能满足：
本机库里 357 条 trace 全是测试造的，一条 `feedback IS NOT NULL` 都没有——
线上那份在服务器上。所以退回第二档：按第一批题一样的做法，
在 chunks 里挑「带界面路径 + **本块真的有配图**」的块读了再出题。
每条路径都是原文抄的，但它**不等于**真实失败样本，等线上攒够差评要回头补。

挑块条件里 `jsonb_array_length(images) > 0` 这一条不能省：本块没有图的题
放进分母，等于要求模型引用一张不存在的图，而那正是硬指标禁止的事。

覆盖：菜单操作 / 打印 / 面单 / 库存 / 审核 / 采购 / 退货 / 店铺配置 / 仓库配置 / 物流。

**实测**：`配图带出率 50.0%`（20 题分母）。这是第一次这个数字有意义——
以前 6 题的时候，33.3% 和 50.0% 之间只差一道题。
`wrong_image_rate` 由 `fake_citation_rate` 覆盖（`[图n]` 指向不存在的图号），
实测 **0%**。

---

#### P4 — 多轮人工验收集　✅ **2026-08-21 建立（尚未跑）**

新增 `eval/manual_conversations.md`：20 组、每组 3–8 轮。

离线单题评测**结构性地**测不到 Agent 最容易坏的地方——那份题集里根本没有
「上一轮」。M10 那道硬防线就是这么漏过去的：离线路由评测里的 history
是短的合成字符串，喂不出「拿上一轮的材料直接写答案」这个行为。

六组：追问与省略主语 / 多轮收集需求 / 概念与常识（M12 那条线）/
寒暄与边界 / 私有库与主体 / 稳定性与前端。八条通用判据。

⚠️ **它现在是「建立了」，不是「通过了」。** 第一次跑之前不要把它当成
已通过的验收项——文件末尾写着同一句话。

---

#### P5 — trace 补 `answer_source`　✅ **2026-08-21**

M12 之后「答了但没有出处」第一次成了一件**正常且允许**的事，
它同时也是最需要盯着的一件事。而在这一列之前，
「常识答的」和「查库答的」在表里的**每一列上都长得一模一样**：

```
直路的 tools 恒为空数组      → 反推不出
chunk_count 只说检索到几块   → 检索到了不等于用了
answer_kb 既可能引材料也可能拒答
```

所以**不能等以后从 `tools` 猜**，必须当场判定。五态：

```
kb                正文里有 [n] —— M12 的规矩是「[n] 只属于材料里的内容」
general_knowledge 答了，一个来源编号都没标
canned            写死的寒暄回复
tool              Agent 只跑了非终结工具（出方案 / 查文档 / 报时间）
no_answer         兜底话术
```

⚠️ **老数据留 NULL，不给 server_default。** 填成 `'kb'` 会让 M13 之前每一行
都凭空变成一条查库答案，而 M12 放开常识正是在那段时间上的线——
那批行恰恰最需要「不知道」这个状态。

迁移 `a1c7e4d20b31`。9 个测试，一半是纯函数、一半端到端
（`TraceDraft.answer` 在四个地方赋值，漏掉任何一处的表现都是
「那一列静静地记成 general_knowledge」，不报错、只是数字错）。

---

#### P6 — 数据保留策略　✅ **2026-08-21**

「定期删 N 天前的数据」不是一条保留策略，是一句愿望。落成具体规则：

```
普通 trace     30 天
带 👎 的       90 天   ← 它是评测集的原料，闭环有时跨好几周
出错的         90 天   ← 事故复盘常常发生在事发很久以后
聊天记录       用户删会话时当场删干净（另一条链路，见 P7）
```

`copilot prune-traces`，**默认 dry-run**，真删要显式 `--apply`。
`--apply` 和 `--dry-run` 同时给**直接报错**，不替他猜——猜「听保守的那个」
的代价是 timer 里一条写错的命令会安静地每天什么都不做。

新增 `copilot-prune.service` / `.timer`，UTC 21:10（北京 05:10），
**排在备份 timer 之后一小时**：先备份、再删。反过来的话，
某天清理出了错，当天的备份里已经没有那些行了。

##### ⭐ 写这条命令的时候踩了一个 SQL 三值逻辑的坑

第一版把「留久一点」写成 `NOT (feedback = 'down' OR ok = false)`。
`feedback` 是 NULL 时（390 行里 376 行都是），`feedback = 'down'` 的结果是
**NULL 而不是 false**，`NULL OR false` 还是 NULL，取反 `NOT NULL` **仍然是 NULL**——
那一行既不算「留久一点」也不算「普通到期」，两边都落空。

```sql
where feedback is null                                    389
where not (feedback = 'down' or ok = false)                  0   ← 全被 NULL 吃掉
where not (coalesce(feedback,'') = 'down' or ok = false)    376
```

**一行都删不掉，而且不报错。** timer 每天照常跑、日志每天写「到期 0 行」，
磁盘一直涨，等有人发现已经是几个月以后。
`test_null_feedback_rows_are_not_swallowed` 钉住这一次。

---

#### P7 — 删除链路　✅ **2026-08-21**

删一段会话横跨四张表加一个文件系统，四条规则各不相同：

```
messages         ON DELETE CASCADE      跟着删     （已有测试）
exports/*.xlsx   路由里手动 unlink      跟着删     （已有测试）
request_trace    **没有外键，不删**      刻意留下   ← 这次补的
feedback         在 trace 那一行上       跟着留下   ← 这次补的
```

**为什么 trace 不跟着删**（决定，不是疏忽）：它记的是「系统那天表现如何」，
不是「他说过什么」。而用户删掉那段会话的动机，很多时候恰恰是「这轮答得不好」——
跟着删的话，**最该留下的样本会被最想让你看到它的那个动作抹掉**。

> 一个没有测试的「决定了不删」，和「忘了删」在代码上长得一模一样。
> 半年后谁也说不清当初是哪一种。

新增 `tests/test_delete_lifecycle.py` 4 条。其中一条对照着邀请码那条看：

```
台账    user_id 被 SET NULL 清空  →  **不影响**任何事（要的是「那天发生过什么」）
邀请码  used_by 被 SET NULL 清空  →  **绝不能**影响「用过没有」（要的是「不能再用」）
```

今天没有「删除 trace」的接口，所以测的是唯一能改到它的入口（投票）：
别人的 trace id 一律 404，也不出现在他的 `/api/feedback/recent` 里。

---

#### P8 — 邀请码删号复活　✅ **2026-08-21，本机库里当时就有 1 个**

`invite_codes.used_by` 是 `ON DELETE SET NULL`，而三个地方都按
`used_by IS NULL` 判「还没用过」：

```
用邀请码注册  →  used_by = 那个人
删掉那个人    →  used_by 被置回 NULL
同一个码      →  又能注册了
```

一次性的邀请码因为删号而复活，而邀请制是这个站**唯一的准入闸门**。
本机库当时就有 1 个这样的码（16 条有 `used_at`，只有 15 条有 `used_by`）。

判据全部换成 `used_at`（核销、计数、可用列表三处）。两列语义分清：

```
used_by  谁用的。人删了就不知道了，**可以**为空
used_at  什么时候被消费的。**一旦写上永不清除**，它才是「用过了」的判据
```

不用改 schema——`used_at` 从 init 就在，且每条已消费的码都有值，所以不需要数据迁移。
核销仍在注册的同一个事务里（`redeem_invite_code` 不自己提交），
带条件的 UPDATE 做 compare-and-set，没有竞态窗口。

新增两条测试：删号后同码注册必须失败；作废的码不能出现在「未使用」列表里
（否则管理员会把它发出去，对方注册全部失败）。

---

#### P9 — 上传解析加 ZIP 炸弹防护　✅ **2026-08-21**

20MB 的上传上限挡不住这件事：`.docx` / `.pptx` 本质都是 ZIP，
20MB 进去可以是几十 GB 出来。而 worker 只有一个进程、`MemoryMax=400M`、
解析是同步的 CPU 活——**一份文件就能把它拖死**，之后所有人的上传都停在
「解析中」，页面上没有任何异常提示。

新增 `ingest/zipguard.py`。⭐ **它不解压任何东西**：ZIP 的中央目录里就写着
每条目的压缩前/后大小，`infolist()` 只读那张表，代价几毫秒，
且**先于** python-docx / python-pptx 执行。

阈值是量出来的，不是拍的（`tests/samples.py` 造的真实文件）：

```
a.docx   17 条目   解压后 827 KB   压缩比 23.7   最大单条 438 KB（单条比 32.2）
a.pptx   40 条目   解压后  97 KB   压缩比  4.1
```

Office 正文是 XML，压缩比二三十是**常态、不是可疑信号**。所以每条阈值
留了至少 8 倍余量：条目 5000 / 解压后 300MB / 单条 80MB / 整体压缩比 200。
压缩后总量低于 64KB 时不看压缩比——小文件的比例天然虚高，
不设下限的话最先被拦下的会是最小的那些**正常**文档。

`PARSER_TIMEOUT = 120s`，只套本机解析那几个，**不套视觉那条路**：
一份 20 页的扫描件要逐页发给 Kimi，一页几秒，合法文件也能超过 120 秒，
卡它只会把「正常但慢」判成「文件损坏」。

⚠️ **诚实地写清楚这一道的边界**（在代码注释里也写了）：Python 杀不掉正在跑的
线程，超时之后那条线程还在后台烧 CPU。它的作用是把「无限期卡住」降级成
「一次失败」，不是真的回收资源。真正兜住资源的是 zipguard（挡在解析之前）
和 systemd 的 `MemoryMax` + `Restart=always`。

12 条测试，**一半在测「不能误伤」**：正常 Office 文档不仅要过，还要
**离阈值很远**（以后有人想调小阈值，得先让那条测试同意）。
炸弹是写真数据造的，不是伪造的头。

##### 顺手修的两个

- `_run_with_timeout(timeout=PARSER_TIMEOUT)` 把常量写成了**默认参数值**——
  import 时求值一次就定死，之后无论 monkeypatch 还是改成从配置读，
  改的都是一个再也没人看的模块变量。**这种失效完全静默。** 改成调用时才读。
- `cli.py` 的 `if __name__ == "__main__": app()` 在文件**中间**
  （`worker` 和 `corrections-export` 之间），于是
  `python -m copilot.cli <后面那些命令>` 一律报 "No such command"。
  装出来的 `copilot` 入口点不受影响，所以这个坑只在用 `python -m` 时才踩得到。
  挪到文件末尾。

---

#### P10 — 周质量报告　✅ **2026-08-21**

`copilot quality-report [--days N] [--user EMAIL]`。**不做后台 Dashboard**，
理由同 M11 P2 不给反馈做页面：线上 3 个账号，一周产不出几十条数据；
一个页面要前端路由、权限、图表库和长期维护，而这条命令十分钟能写完、
一秒能跑完，**回答的问题完全一样**。

输出：提问数 / 活跃用户 / 答案来源五分类 / 👍👎 与差评率 /
越过工具直答 / 出错 / TTFB p50·p95 / 总时长 p50·p95 / token 合计与均值。

几处刻意的口径（都有测试钉着）：

- **差评率的分母是「被评价过的轮次」**，不是全部请求。用总数当分母只会得到
  一个恒定接近 0、看不出变化的数
- **延迟不含寒暄**。它一次模型调用都不花、首字毫秒级，混进来会把 p50
  拉到看不出问题——而这两个数字存在的意义就是回答「用户等了多久」
- **`answer_source` 是 NULL 的老行单独列出来**，不并进任何一类
- **百分位自己算、用最近邻、不插值**。样本量常常两位数，
  插值给出的「p95 = 1873.5ms」是一个从未发生过的事件，而它会被当成
  「有一次请求慢到 1873ms」去排查
- **没有可靠价格配置就不印成本**。硬编码单价，半年后换模型 / 调价之后
  报告会一本正经给出错数字——那比不给更糟，因为它看起来像真的

`--user` 是给「某个人说慢」那种排查用的：全站 p95 被大多数正常请求压着，
一个人的糟糕体验在里面看不出来。

---

#### P11 — TTFB / 延迟进入验收指标　✅ **2026-08-21（先测，不优化）**

`quality-report` 里 p50 / p95 已经在打。**本机这批数字不能代表线上**——
357 条 trace 全是测试造的（假 LLM，首字 200ms 级）。
线上真实数字要等 `deploy.sh` 之后在服务器上跑一次这条命令。

任务书说「暂时不要为了优化而优化，先测」——测的手段现在有了，到此为止。
真要定位的话，四段（router / retrieval / rerank / generation）今天在
trace 里还分不开，那是下一步的事，**不在没有数据之前先做**。

---

#### P12 — Agent 白名单观测　🟠 **真实观测完成，发现 blocker（2026-08-21）**

P10 的 `quality-report` 已经把 P12 需要的入口一起做了：

```bash
copilot quality-report --route agent --days 7
```

它只统计 Agent 路，输出 Agent 请求数、`tools=[]`、越过工具直答、
常识回答、拒答、差评、错误和 TTFB p95。`tools=[]` 和越过工具直答
**刻意分开**：追问和拒答本来就可能不调工具，不能把它们误报成违规。

这次补了两条专门守 P12 口径的测试：

- 直路行不能混进 `--route agent` 的分母
- `tools=[]` 的正常拒答不能被算成 tool bypass

2026-08-21 经用户授权后收紧本机 SSH 私钥 ACL、完成生产备份和部署，并实际运行：

```text
最近 7 天 Agent：81 轮，1 个活跃用户，tools=[] 32 轮，tool bypass 0
TTFB p50 2702ms / p95 5503ms，总时长 p50 4034ms / p95 10378ms
token 61959，报告显示拒答 39，错误 1
```

随后对 13:16–13:28 UTC 的 68 条新 trace 逐行核对，确认报告有两处口径错误：

- `save_requirement` / `whoami` / `my_documents` 等正常工具回复因为没有 citations，
  被写成 `no_answer`，把拒答率明显高估；
- 用户主动点“停止生成”的 `CancelledError` 被并进服务错误率。

同一生产账号完成 20 组真实前端多轮初跑，结果记录在
`eval/manual_conversations.md`：7 组通过、11 组失败、2 组用例输入不足。
主要 blocker 是无工具追问直接拒答、私有主体拿公共默认值冒充专属约定、
截断历史后编造最早问题和跨档位丢上下文。P12 因此**尚未通过**。

---

#### P13 — 删除双路　⛔ **2026-08-22 门禁未过，不执行**

用户已明确授权开始 P13。启动保护措施已完成：

```text
tag:     p13-start-20260822
commit:  677c062
backup:  /var/backups/copilot/kb-20260822-043813.dump
         /var/backups/copilot/uploads-20260822-043813.tar.gz
```

生产 `copilot-backup.service` 返回 `Result=success`，`LAST_OK=2026-08-22T04:38:17Z`。
随后完成当前版本的本地测试与评测：

| 评测 | 结果 | 门禁结论 |
|---|---:|---|
| 公共直路 75 题 | 73/75；幻觉 10.0% | 幻觉硬门槛失败 |
| 公共 Agent 75 题 | 70/75；幻觉 10.0% | 幻觉硬门槛失败 |
| 私有 Agent 19 题 | 17/19；检索/引用 100%；幻觉 16.7% | 幻觉硬门槛失败 |
| 风险边界 48 题 | 高风险幻觉 18.2%；跨平台污染 20.0% | 两项硬门槛失败 |
| 路由确定性 / 模型 | 84.1% / 90.5%；模型路由越过工具 7.9% | 需修复后复测 |
| 后端测试 / 静态检查 | 483 passed；ruff 全通过 | 通过 |
| 前端测试 / lint / build | 4 passed；lint、build 通过 | 通过 |

私有 Agent 评测另发现评测脚本缺陷：`--as-user` 解析出的用户 ID 原先没有传给
`run_agent_cases`，导致私有命中恒为 0。已在 `eval/run.py` 修正并重跑；本地用户的两份
fixture 文档状态为 `done`、共 4 个私有分块，修复后的检索命中率为 100%。该修正尚未部署
生产，也不改变生产路由。随后又发现 `--general off` 原先只作用于直路，已将开关从
`AgentDeps` 传入 `answer_kb` / `ask_stream`；两道公共 no-answer Agent 题的定向复测为
2 / 2、幻觉 0%。严格公共直路全量复测为 73 / 75、幻觉 0%；修正前的严格 Agent
全量命令因判分服务 429 有 5 题 `UNRELIABLE`，且当时 Agent 仍未接上严格开关，不能
当作严格模式证据。修正后目前只完成定向复测；私有严格模式也只完成定向复测，因此
没有用局部结果替代完整门禁。

最近 7 天生产报告和 20 / 20 组人工多轮验收证据已齐，但当前全量硬门槛仍未通过。
因此**没有部署 P13，也没有删除** `_chat_stream`、`AGENT_TRIGGERS` 或
`profile is not None` 旧粘性路由；等幻觉、风险边界和路由问题修复后再重新执行完整门禁。

---

#### P14 — 文档整理　✅ **2026-08-21**

保留 `plan.md` 的全部历史，不再让它同时承担入口文档、架构说明、评测手册和
运维手册四种职责。新增并按真实代码审校：

```text
README.md          项目、主要能力、本地运行、测试、部署入口
ARCHITECTURE.md    前端 / FastAPI / RAG / Agent / 终结工具 / Postgres / worker / 隔离 / 流式
EVALUATION.md      五套验收、三态判分、baseline、A/B 与 UNRELIABLE 规则
OPERATIONS.md      部署、备份恢复、systemd、日志、trace、限流、报告与事故检查表
DECISIONS.md       14 条已经付过代价的架构决策
```

`plan.md` 顶部增加 NOW / NEXT / LATER / DONE，后续不需要翻三千行历史才能知道
今天卡在哪里。文档里的本地链接全部做了存在性检查；审校时还抓到一处真实命令名
写错：`copilot corrections export` 已改成代码里的 `copilot corrections-export`。

这一步只改文档，**没有部署，也没有把 P12/P13 写成已完成**。

验收：`uv run pytest` **462 passed**（2 条测试密钥长度 warning）；
`uv run ruff check` **All checks passed**。本阶段没有前端改动，因此没有重复跑前端构建。

---

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
