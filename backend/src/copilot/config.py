"""全局配置。所有密钥只从 .env 读，绝不硬编码。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（Copilot/），data/ 与 .env 都挂在这里
ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / "backend" / ".env",
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
