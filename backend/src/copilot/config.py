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

    # ===== LLM：生成答案 =====
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"

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

    # ===== 检索 =====
    retrieve_top_k: int = 20  # 向量召回数量
    rerank_top_k: int = 5  # 重排后送进 LLM 的数量

    # 低于此分视为「没检索到」，触发防幻觉兜底。
    # ⚠️ bge-reranker-v2-m3 的分数绝对值很低：实测正确答案 0.02、无关内容 0.0001，
    # 靠的是相对差距（200 倍）而非绝对值。这里只做「滤掉明显垃圾」的下限，
    # 真正的防幻觉闸门在 prompt 里（检索不到就明说不知道）。
    # 这个值要在 M8 用评测集标定，别凭感觉调。
    rerank_score_threshold: float = 0.005

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

    # ===== 上传限制 =====
    upload_max_bytes: int = 20 * 1024 * 1024  # 20MB
    upload_max_docs_per_user: int = 200
    upload_allowed_suffixes: tuple[str, ...] = (".md", ".txt", ".docx", ".pptx", ".pdf")

    # ===== 路径 =====
    @property
    def data_dir(self) -> Path:
        return ROOT_DIR / "data"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def image_dir(self) -> Path:
        """镜像下来的语雀配图。线上由 nginx 直接发，不经过 Python。"""
        return self.data_dir / "images"

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
