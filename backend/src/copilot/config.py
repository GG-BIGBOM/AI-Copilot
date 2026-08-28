"""全局配置。所有密钥只从 .env 读，绝不硬编码。"""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_root() -> Path:
    """定位项目根目录（`data/` 与 `.env` 都挂在这里）。

    ⚠️ **不能只靠数目录层数。** 开发时是
    `Copilot/backend/src/copilot/config.py`，往上四层正好是项目根；
    但部署到服务器时目录常被拍平成 `/opt/copilot/src/copilot/config.py`，
    层数少一层，根就算成了 `/opt`。

    这个错误的可怕之处在于**它不报错**：pydantic-settings 读不到 .env
    就静默使用字段默认值，于是应用拿着默认的 `kb:kb` 去连数据库，
    最后报出来的是「password authentication failed」——排查方向被带偏到
    密码和 pg_hba 上，真正的原因在三层目录之外。

    所以按可靠性排序：显式环境变量 → 向上找标志文件 → 才轮到数层数。
    """
    if explicit := os.getenv("COPILOT_ROOT"):
        return Path(explicit).resolve()

    here = Path(__file__).resolve()
    # `.env.example` 一定在仓库里，是比 `.env` 更可靠的路标（后者被 gitignore）。
    # **两趟分开找，顺序不能反**：开发布局里 `backend/` 自己也含 `.env.example`，
    # 混在一趟里会先命中它，把根算成 `backend/`，于是 data/ 指到 backend/data。
    for parent in here.parents:  # 开发布局：<根>/backend/.env.example
        if (parent / "backend" / ".env.example").exists():
            return parent
    for parent in here.parents:  # 部署拍平：<根>/.env.example
        if (parent / ".env.example").exists():
            return parent
    return here.parents[3]


ROOT_DIR = _resolve_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # 两种布局都认：开发时在 backend/ 下，部署拍平后在根下。
        # 靠后的优先级更高，所以开发布局能盖住部署布局
        env_file=(ROOT_DIR / ".env", ROOT_DIR / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ===== 数据库 =====
    database_url: str = "postgresql+asyncpg://kb:kb@localhost:5432/kb"

    # ===== SiliconFlow：embedding + rerank =====
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024  # bge-m3 输出维度，改模型必须同步改迁移
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    # 免费额度限速较严。批量小一点、慢一点，也好过跑到一半被限流中断
    embedding_batch_size: int = 16
    embedding_rate_limit_per_sec: float = 3.0
    embedding_max_retries: int = 4

    # ===== LLM：生成答案（简答档）=====
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"

    # ===== LLM：详解档 =====
    # 同一个问题答得更细：前置条件、注意事项、常见错误都展开。
    # **防幻觉的铁律两档完全一样**，不一样的只有写法要求（见 qa.py）。
    #
    # 留空 key 就复用 VISION_API_KEY——那本来就是同一家 Moonshot 的密钥，
    # 让人为同一个账号在 .env 里填两遍是没必要的。两个都没配时，
    # 详解档不可用，接口会回一句人话（而不是 500）。
    llm_deep_api_key: str = ""
    llm_deep_base_url: str = "https://api.moonshot.cn/v1"
    llm_deep_model: str = "kimi-k2.6"
    # ⚠️ **kimi-k2.5 / k2.6 / k3 只接受 temperature=1**，传别的直接 HTTP 400
    # （`invalid temperature: only 1 is allowed for this model`）。
    # 而我们全局默认 0.1。换成 `moonshot-v1-*` 系列时可以设回 0.1，它们不挑。
    llm_deep_temperature: float = 1.0

    # ===== 视觉：读图转文字（图片上传 + 扫描件 PDF）=====
    # ⚠️ **不能复用 LLM_API_KEY**。答题走 DeepSeek，它没有视觉能力；
    # 这里走 Kimi（服务器实测可直连，200 / 3.2s）。留空会退回 llm_api_key，
    # 那时表现是一个 401——所以 .env 里请显式填。
    vision_api_key: str = ""
    vision_base_url: str = "https://api.moonshot.cn/v1"
    # moonshot-v1-32k-vision-preview 是专做视觉的那个，32k 上下文够放一页密集表格。
    # kimi-k2.6 也支持读图且更新，但贵一档；换模型只改这一行
    vision_model: str = "moonshot-v1-32k-vision-preview"

    # 扫描件 PDF 逐页读图的页数上限。**这是一道花钱的闸门**：
    # 一页约 ¥0.01–0.03，没有上限的话一份 300 页的扫描手册能一次烧掉几十块，
    # 而用户完全不知道自己触发了什么
    vision_pdf_max_pages: int = 20
    # 渲染扫描页的 DPI。150 够认清宋体小五号；再高只是把图变大、把钱烧多
    vision_pdf_dpi: int = 150

    # ===== 认证 =====
    jwt_secret: str = "CHANGE-ME-IN-DOTENV"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 天

    # JWT 放 HttpOnly cookie，不放 localStorage——localStorage 任何 XSS 都能读走
    cookie_name: str = "copilot_token"
    # 本地开发走 http，必须为 false，否则浏览器根本不存这个 cookie。线上 HTTPS 置 true
    cookie_secure: bool = False
    password_min_length: int = 8

    # ===== API =====
    # 逗号分隔。本地开发前端在 3000、后端在 8000，是跨源的，必须放行且允许带凭证。
    # 线上前后端同源（都在 liushun666.cn），这条其实用不上。
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # ===== 切分 =====
    chunk_size: int = 500  # 目标字数（中文按字算）
    chunk_overlap: int = 80

    # ===== 常识兜底（M12）=====
    # 知识库里没有的时候，允不允许模型用自己的通用知识回答。
    #
    # ⚠️ **这一行推翻的是 M1 到 M9 的地基。** 铁律 1 原文是「不得用你自己的
    # 常识补全或推测」，整套防幻觉都建在它上面：M7 让 Agent 自由发挥那次，
    # 41 题准确率掉 12 个点、幻觉率 12.5%，掉的不是常识题——是它「想起来」的
    # 界面路径和数值，而那种答案和真的长得一模一样。
    #
    # 打开它的理由（2026-08-20 产品决定）：一个连「品牌方是什么」都要回
    # 「知识库暂无此内容」的助手，用起来像坏的。实测那句追问，知识库里
    # 确实一条都没有（最高分 0.35 那条讲的是一盘货，不是这个概念）——
    # 换句话说**怎么修检索都救不回来**，只能靠常识答。
    #
    # ⭐ **做成开关是因为它必须能一行退回去。** 评测掉得难看时，改 .env 重启，
    # 不用重新发版。（同 agent_enabled / agent_rollout 的规矩。）
    allow_general_knowledge: bool = True

    # ===== 检索 =====
    retrieve_top_k: int = 20  # 向量召回数量
    rerank_top_k: int = 5  # 重排后送进 LLM 的数量

    # 低于此分视为「没检索到」，触发防幻觉兜底。
    # ⚠️ bge-reranker-v2-m3 的分数绝对值很低：实测正确答案 0.02、无关内容 0.0001，
    # 靠的是相对差距（200 倍）而非绝对值。这里只做「滤掉明显垃圾」的下限，
    # 真正的防幻觉闸门在 prompt 里（检索不到就明说不知道）。
    # 这个值要在 M8 用评测集标定，别凭感觉调。
    rerank_score_threshold: float = 0.005

    # ===== 可观测性：一次请求的 span 树（W1.1）=====
    # ⚠️ **默认关，而且是可选依赖**（`uv sync --extra obs`）。
    # 生产那台机器只有 1.6GB 内存，OTel SDK 的常驻占用要实测过才谈开不开；
    # 没装 SDK 时打开它只会在日志里留一句 warning，服务照常起（见 obs.py）。
    tracing_enabled: bool = False
    tracing_service_name: str = "copilot"
    tracing_environment: str = "dev"
    # 采样率。本机和评测用 1.0（全采），生产降下来——
    # span 是按 trace_id 采的，采到就是一整棵树，不会出现"半棵树"
    tracing_sample_ratio: float = 1.0
    # 把 span 打到控制台。**本机调试用**：不配任何后端也能看见树的形状，
    # 这样"埋点对不对"和"导出通不通"是两件可以分开排查的事
    tracing_console: bool = False

    # OTLP 导出。Langfuse 的入口形如
    # `https://cloud.langfuse.com/api/public/otel/v1/traces`
    otlp_endpoint: str = ""
    # 额外头，`k=v,k2=v2`。OTel 官方环境变量就是这个格式
    otlp_headers: str = ""
    # Langfuse 收 HTTP Basic。**两个都填才会加这个头**——只填一个的表现是
    # 401，而导出在后台线程里，401 只会刷一行日志，看板上永远是空的
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # ===== 混合检索：BM25 + RRF（W1.2）=====
    # ⭐ **默认开——因为量过了。** 2026-08-28，`eval/keyword.yaml` 45 题：
    #
    #     检索命中 top-5   35/45  →  44/45
    #     裸粘贴那 15 条   6/15   →  15/15    MRR@5 0.367 → 0.933
    #     完整问句那 30 条 29/30  →  29/30    一道题的名次都没变
    #
    # 结论很干净：**bge-m3 + 重排在完整问句上已经饱和，hybrid 一分没加也一分没减；
    # 真正断掉的是"用户什么都不说、直接把一个编码/字段名贴进来"那条路**——
    # `JTSD`、`23381383`、`ownerCode`、`POSTB` 这些 dense 一条都找不到。
    # 完整的 A/B 和取舍见 DECISIONS.md 的 ADR-8。
    #
    # ⚠️ **关掉它随时可以，而且是安全的**：`retrieve.search()` 里所有 BM25
    # 相关的代码一行都不会执行，行为退回 W1.2 之前逐字节一致。
    #
    # ⚠️ 两种"开着但没生效"的情形都是**静默退回纯向量**，不是报错：
    # 服务器没装 jieba（`uv sync --extra hybrid`），或者 `content_tsv`
    # 还没回填（`copilot backfill-tsv`）。这是刻意的——一个检索增强
    # 不该有能力把整个站点变成 500。前者会在日志里留一句 warning。
    #
    # ⛔⛔ **2026-08-29：默认值从 True 翻回 False。付费那一轮把它打下来了。**
    #
    #                          08-24 基线    hybrid      题
    #     公共库·直路 幻觉率        0.0%   →   10.0%    none-sap-connector
    #     公共库·Agent 幻觉率       0.0%   →   10.0%    同上（两条路径都复现）
    #     风险边界 高风险幻觉率      0.0%   →   10.0%    ui-dashboard
    #     检索命中率              98.5%   →   98.5%    ← 一点没动
    #
    # ⭐ **机理是同一个，而且很反直觉**：纯向量池里 top-5 的第 5 位往往是个
    # **重复块**（等于一个空位），而那个空位正是模型愿意拒答的原因；
    # hybrid 把它填上一块"话题相邻但不是这件事"的材料，就足以把它劝离拒答。
    #     「数据大屏在哪个菜单」→ 捞进讲"顶部数据看板"的块 → 当成同一个功能答了
    #     「旺店通和 SAP 的对接方案」→ 捞进"外部ERP分销商对接" → 「按通用理解…」
    # 词法召回带进新内容，在裸粘贴上是救命的，在 no_answer 题上是致命的——
    # **是同一个动作的两面。**
    #
    # ⚠️⚠️ **上面那行「检索命中率一点没动」是这件事最该记住的部分。**
    # W1.2 当初把默认值定成 True，依据是免费的 `--check`（75 题两边一模一样）。
    # 而那个指标**结构上看不见这种失败**：no_answer 题没有期望来源，
    # 根本不进 `source_hit` 的分母。「没有理由退步」不等于「量过了」。
    #
    # ⭐ 怎么把它开回来（有路，但要先做）：`eval/keyword.yaml` 的 A/B 说得很清楚——
    # **完整问句 30 题 hybrid 一分没加**（29/30 → 29/30），全部收益都在
    # 裸粘贴那 15 条（6/15 → 15/15）。而上面两道回退**都是完整问句**。
    # 所以按查询形状开关词法这一路（短、无疑问词、含编码/ID 才走 BM25），
    # 能保住 100% 的收益、去掉整个伤害面。做完要重跑这四轮。
    hybrid_enabled: bool = False
    # 词法召回数量。和向量那一路的 `retrieve_top_k` 各取各的，
    # 融合之后再一起进重排
    hybrid_lexical_k: int = 20
    # RRF 的常数。60 是原论文（Cormack 2009）的取值，也是各家默认。
    # ⚠️ 它决定"排名差一位"值多少分：k 越小，头部名次的权重越大。
    # 调它之前先想清楚是想让谁赢——没有理由就别动
    hybrid_rrf_k: int = 60
    # ⭐ 词法查询只保留**文档频率低于这个比例**的词（见 `retrieve._rare_terms`）。
    # 0.02 = 出现在 2% 以上的块里就算废话。这不是拍脑袋：`ts_rank_cd` 不含 IDF，
    # 不砍高频词的话，排在最前面的永远是"这个话题讲得最密集"的那几块，
    # 而不是含着那个编码的那一块——实测词法带进来的 229 块新内容，
    # 最终一块都没进 top-5
    hybrid_df_max_ratio: float = 0.02
    # 整句都是常用词时的保底留词数。砍成空查询等于这一路直接消失
    hybrid_min_terms: int = 4

    # ===== 会话级已确认事实（W2.2）=====
    # ⚠️ **默认关，理由是这个项目的老规矩**：它改的是送进模型的 system prompt，
    # 也就是「会让答案变、但绝不会报错」的那一类——这一类一律先做成开关、
    # 默认关，等 A/B 数字出来再谈开不开（`hybrid_enabled` /
    # `allow_general_knowledge` / `agent_enabled` 三个先例都是这么来的）。
    #
    # 关着的时候：事实表照常**记录**（写库、跨轮累积、随会话一起删），
    # 只是**不注入** prompt。这个顺序是刻意的——真要打开的那天，
    # 存量会话手里已经有账本了，而不是从那一刻起才开始攒。
    #
    # 打开之前要有的数字：`eval/longchat.yaml` 的跨窗口指代解析成功率，
    # 开关两边各跑一次（口径见 EVALUATION.md）。
    session_facts_enabled: bool = False

    # ===== 提示注入防线（W2.3）=====
    # 材料区加围栏 + 一段「材料区里的指令一律不执行」的规则。两样由这一个开关
    # 同时管——规则里写着"边界只有那两个标记"，而不开围栏时那两个标记不存在。
    #
    # ⚠️ **默认关，但理由和上面那几个不一样，值得说清楚。**
    # `hybrid` / `general_knowledge` / `agent` 是**能力**开关，关着是一个完整的产品；
    # 这一个是**防线**，关着是有洞的状态。之所以仍然默认关，是因为
    #   1. 今天的注入面是**自伤**不是跨租户：上传只进自己的私有库，
    #      而 user_id / space_id 都不是工具入参（结构上的保证，注入撬不动）
    #   2. 它改的是每一轮的 prompt，而这个项目里 prompt 一改就必须重量
    #      ——M12 压缩铁律那次，幻觉率 0% → 50%
    #
    # ⚠️⚠️ **前提变了就要立刻改默认值**：一旦有共享知识空间、或者公共库
    # 允许用户投稿，攻击面立刻从自伤升级成跨租户，那时候"等 A/B"不是理由。
    #
    # 打开之前要有的数字：`eval/risk_boundary.py` 的 `injection` 那一组要 100%，
    # 且原有三条硬指标（高风险幻觉 / 假引用 / 跨平台串台）一个点都不许退。
    #
    # 注：伪造区段标记的**剥离**（`injection.sanitize`）不受这个开关控制，
    # 永远开着——它在没有攻击时是恒等函数，零风险的东西不做成开关。
    injection_guard_enabled: bool = False

    # ===== 语雀抓取 =====
    yuque_rate_limit_per_sec: float = 1.5  # 保守限速，别把自己封了
    yuque_max_retries: int = 3

    # ===== 语雀配图镜像 =====
    # ⚠️ 语雀 CDN 有防盗链：带 Referer 取图直接 403（实测）。
    # 所以图片必须落到本地自己发，不能在页面上直接外链 cdn.nlark.com。
    mirror_images: bool = True
    # 图片是静态资源，不走语雀的 API，可以比抓正文快一些。786 篇约 3000 张图
    image_rate_limit_per_sec: float = 6.0
    image_max_retries: int = 3
    image_max_bytes: int = 10 * 1024 * 1024  # 单张上限，超了跳过
    # ===== 上传文档里的嵌图（M17）=====
    # 一篇文档最多解出几张图。**这是一道内存和磁盘的闸门**：一份 PPT 可以
    # 塞进几百张图，而 worker 的 MemoryMax=400M，磁盘只有 40G
    upload_max_images_per_doc: int = 30
    # 太小的多半是图标、分隔线、logo。收进来只会让答案挂上一堆装饰性小图，
    # 而每一张都占一行 image_assets
    upload_image_min_bytes: int = 4 * 1024
    # ===== 纠错里贴的截图（M17.1）=====
    # 比文档嵌图小一档：这是人手动截的一张图，不是一份 PPT 里的原图。
    # 上限本身不是安全边界（真正的边界是魔数白名单和私有目录），
    # 它挡的是"一个注册用户慢慢把 40G 磁盘填满"
    correction_image_max_bytes: int = 5 * 1024 * 1024
    # 每人**还没提交**的悬空图上限。传了不提交的图没有任何行指向它，
    # 只能靠时间清理——没有这道闸，清理之前的那段时间是敞开的
    correction_images_pending_max: int = 20
    image_allowed_suffixes: tuple[str, ...] = (
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
    )
    yuque_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    # ===== 评测（M8，只在本机跑）=====
    # ⚠️ **判分模型要和答题模型不一样。** 同一个模型判自己的答案会偏心
    # （self-preference bias），指标会虚高。留空则退回 llm_* 那组配置，
    # 但那时报告里必须写明「判分器和被判者同源」这个缺陷。
    eval_judge_model: str = "deepseek-reasoner"
    eval_judge_api_key: str = ""  # 留空复用 llm_api_key
    eval_judge_base_url: str = ""  # 留空复用 llm_base_url

    # ===== Agent（M7）=====
    # ⚠️ 默认关。M8 的评测证明「直接检索 + 回答」这条路已经 100%（41 题），
    # 把所有问答都改成 Agent 循环是拿一个**已量化**的系统去换一个没量化的。
    # 打开之前必须用 `eval/run.py --agent` 跑一遍，证明不退化。
    agent_enabled: bool = False
    # ⭐ **白名单先于百分比（M11 P4）。** 逗号分隔的邮箱，这些人一律走 Agent。
    #
    # 为什么不用 `AGENT_ROLLOUT=0.2`：线上只有 **3 个真实注册账号**，
    # 而分桶是按 user_id **稳定哈希**的。设 0.2 最可能的结果是**一个人都没进桶**，
    # 设 0.5 也不过是掷三次硬币。「20% → 观察几天 → 50%」这套节奏是为
    # 几百上千用户设计的，照搬到 3 个人身上，观察到的是零样本。
    #
    # 白名单则是确定的：把自己和 1 个熟人切过去，真实用一周。
    # 等用户到 20+ 再谈百分比——`agent_rollout` 那套代码不删，它是对的，
    # 只是现在还用不上。
    agent_allow_emails: str = ""

    @property
    def agent_allow_email_set(self) -> set[str]:
        """统一小写。注册时邮箱就是小写存的（见 schemas.py），
        白名单不跟着小写的话，`.env` 里写了大写字母就静默地一个人都匹配不上——
        而那种失败没有任何症状：灰度「开了」，但所有人还走直路。"""
        return {e.strip().lower() for e in self.agent_allow_emails.split(",") if e.strip()}

    # 灰度比例（M10 P3）。0 = 全部走直路，1.0 = 全部走 Agent。
    # ⚠️ **按用户分桶，不是按请求。** 同一个人在两条路之间跳，
    # 多轮收集需求的状态就断了；而且线上出问题时也归不了因——
    # 你不知道他这一句走的是哪条路。`agent_enabled` 仍是强制全开的总开关。
    agent_rollout: float = 0.0
    # 最大模型请求数与工具调用数。Agent 跑飞时的硬闸门——
    # 没有它，一个循环调用工具的模型能把额度和时间都烧光。
    # 这一组是**出方案**那条路的额度（多轮收集需求要留余量）
    agent_max_requests: int = 8
    # ⚠️ 这个数**不能小于「一次给全」那条路的开销**：七个需求字段各一次
    # `save_requirement`，加 `generate_plan`、`export_excel`，正好 9 次。
    # 上限 10 意味着任何一次工具重试都会炸掉整轮——2026-08-23 线上组 8 就是
    # 这么炸的，而且那条会话从此每一句都在同一个位置炸。留到 16。
    agent_max_tool_calls: int = 16
    # 普通问答（M10）。正常形态就是「决策 → answer_kb → 结束」，
    # 留一次给工具失败后的重试。给多了等于允许它多烧几次才被拦住
    agent_max_requests_qa: int = 3
    agent_max_tool_calls_qa: int = 3

    # ===== 上传限制 =====
    upload_max_bytes: int = 20 * 1024 * 1024  # 20MB
    upload_max_docs_per_user: int = 200
    upload_allowed_suffixes: tuple[str, ...] = (
        ".md",
        ".txt",
        ".docx",
        ".pptx",
        # M17：Excel 走 openpyxl，一个工作表一节。嵌图是「有限支持」
        # （openpyxl 的 `ws._images` 是私有属性，见 `parsers.parse_xlsx`）
        ".xlsx",
        ".pdf",
        # 图片走视觉模型转写。**能不能真的解析取决于 VISION_API_KEY 配没配**，
        # 没配时上传会成功、解析会失败并给出一句人话——比在这里默默不列出来好：
        # 后者用户看到的是「不支持的文件类型」，而它明明是支持的
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".bmp",
    )

    # ===== 路径 =====
    @property
    def data_dir(self) -> Path:
        return ROOT_DIR / "data"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def corrections_dir(self) -> Path:
        """人工勘误层。**挂在仓库根、不在 `data/` 下**——它要进 Git。

        `data/` 整个在 .gitignore 里，勘误放进去就等于「改动无记录、换机器就没」，
        而勘误恰恰是最需要能 diff、能回滚、能看出是谁为什么改的那类东西。
        """
        return ROOT_DIR / "corrections"

    @property
    def image_dir(self) -> Path:
        """镜像下来的语雀配图。线上由 nginx 直接发，不经过 Python。"""
        return self.data_dir / "images"

    @property
    def private_image_dir(self) -> Path:
        """用户上传文档里解出来的图（M17）。**和公共图物理分开。**

        ⚠️⚠️ **这个目录绝不能被 nginx alias 出去。** `/images/` 那个目录是
        静态直发的，谁猜中文件名谁就能取；把别人 Word 里的截图放进去，
        等于把私有内容挂在公网上，而且没有任何症状。
        私有图只有一条出口：`GET /api/images/{id}`，后端逐次校验 owner。

        分目录不是"再加一道保险"，是**让写错的那种代码写不出来**：
        路径由 `owner_id` 决定（见 `assets.absolute_path`），
        一张私有图在物理上就落不进公共目录。
        """
        return self.data_dir / "private-images"

    @property
    def export_dir(self) -> Path:
        """M7 Agent 导出的 xlsx。一个会话一份，按 user_id 分目录。"""
        return self.data_dir / "exports"

    def export_path(self, rel: str) -> Path:
        """同 `upload_path` 的规矩：存相对路径 + 越界检查。"""
        base = self.export_dir.resolve()
        p = (base / rel).resolve()
        if p != base and base not in p.parents:
            raise ValueError(f"导出路径越界：{rel!r}")
        return p

    def upload_path(self, stored_path: str) -> Path:
        """把 `documents.stored_path` 还原成绝对路径。

        **库里存的是相对 `upload_dir` 的路径**，不是绝对路径：绝对路径会把
        开发机的目录（`C:\\Users\\...`）写进数据库，搬到服务器上
        （`/opt/copilot`）全都指不对，而这个库是要跨机器用的。

        顺手挡一道路径穿越：`stored_path` 正常情况下是我们自己生成的 uuid，
        但万一哪天有别的写入路径把 `../../etc/passwd` 塞了进来，
        这里直接拒绝，而不是老老实实去读。
        """
        base = self.upload_dir.resolve()
        p = (base / stored_path).resolve()
        if p != base and base not in p.parents:
            raise ValueError(f"上传路径越界：{stored_path!r}")
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()
