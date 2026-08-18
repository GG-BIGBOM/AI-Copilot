"""读图转文字（OpenAI 兼容的多模态 chat completions，现用 Kimi）。

**为什么是 Kimi 而不是本地 OCR**：服务器 1.6GB，任何 OCR 模型
（PaddleOCR / tesseract 的中文包 / 更别说 Docling）都装不下，
这条在 plan.md「一、第 3 条硬约束」里已经封死。所以走 API。

**为什么不是 Gemini**：国内服务器连不上 `generativelanguage.googleapis.com`
（实测 15 秒超时）。Kimi 在服务器上实测 200 / 3.2s。
Gemini 留给本机的评测判分，那里连得上。

**为什么不是"OCR"而是"读图"**：需要的不是把像素转成字符，是把一张
ERP 界面截图变成**能被检索到的**文字——菜单路径、字段名、按钮上的字、
表格的行列关系。纯 OCR 出来的是一堆失去结构的词，切分器拿不到章节、
检索命中了也说不清答案在界面的哪个位置。
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx

from copilot.config import get_settings
from copilot.providers.siliconflow import ProviderError

# 转写提示词。三条约束按重要性排序，每条都对应一种实测见过的坏输出。
TRANSCRIBE_PROMPT = """你面前是一张旺店通 ERP 系统的截图或文档扫描件。
把它转写成 Markdown，供知识库检索使用。

要求：
1. **只写你真正看见的内容。** 看不清的地方写「[看不清]」，不要根据
   常识补全字段名或数值——这份内容会被当成事实检索出来回答别人的问题。
2. **保留结构**：界面区块和标题用 `##`，菜单路径写成「订单管理 › 批量换货」，
   表格转成 Markdown 表格，表单字段写成「字段名：值」。
3. **不要描述外观**（不要写"左上角有一个蓝色按钮"），要写这个界面
   **能做什么、字段叫什么、填什么**。纯装饰性的图标、logo 忽略。

如果整张图没有任何可读的文字内容（纯照片、纯装饰图），只回复一行：
NO_TEXT_CONTENT"""

NO_TEXT = "NO_TEXT_CONTENT"

# 送进模型前把长边压到这个尺寸。
# 再大对识别没有帮助（模型内部也会缩），但**会线性推高 token 花费**——
# 一张 4K 截图和一张 1600px 截图，认出来的字一样多，价钱差好几倍。
MAX_EDGE = 1600

MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
}


class VisionError(ProviderError):
    """读图失败。消息会进 `documents.error` 给用户看，写成人话。"""


def to_data_url(raw: bytes, mime: str = "image/png") -> str:
    """压到合理尺寸再转 base64 data URL。

    ⚠️ **必须重编码，不能原样 base64。** 手机拍的照片动辄 4000×3000、
    带一堆 EXIF，直接塞进请求体既慢又贵，还可能撞上服务端的体积上限——
    而那个错误回来时只是一个 400，看不出是图太大。
    """
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover - 环境缺件，装 parse extra 即可
        raise VisionError("服务端缺少图片处理组件（pip install '.[parse]'）") from e

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:  # noqa: BLE001 - Pillow 的异常类型很杂，一律当"打不开"
        raise VisionError(f"这张图片打不开或已损坏（{type(e).__name__}）") from e

    # 带透明通道的 PNG 转 JPEG 会得到黑底，先铺白底。
    # ERP 截图里最常见的正是白底 PNG，不铺的话文字变成黑底黑字，直接认不出来
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    if max(img.size) > MAX_EDGE:
        ratio = MAX_EDGE / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

    buf = io.BytesIO()
    # 质量 85：截图里的小字在 75 以下开始糊，95 以上纯属白花钱
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


class VisionLLM:
    """多模态 chat completions 客户端。接口和 `ChatLLM` 保持一致的形状。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        s = get_settings()
        key = api_key or s.vision_api_key or s.llm_api_key
        if not key:
            raise VisionError(
                "缺少 VISION_API_KEY，图片解析用不了。在 backend/.env 里填 Kimi 的密钥。"
            )
        self._model = model or s.vision_model
        self._client = httpx.Client(
            base_url=base_url or s.vision_base_url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            # 读一张图比生成一段文字慢得多，尤其是密集表格。180s 是给最坏情况留的
            timeout=httpx.Timeout(180.0, connect=15.0),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> VisionLLM:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def transcribe(self, raw: bytes, mime: str = "image/png", hint: str = "") -> str:
        """把一张图转成 Markdown。整张图没有可读文字时返回空串。"""
        prompt = TRANSCRIBE_PROMPT
        if hint:
            prompt += f"\n\n补充信息（可能有助于理解这张图）：{hint}"

        payload = {
            "model": self._model,
            "temperature": 0.1,  # 转写不是创作
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": to_data_url(raw, mime)}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
        try:
            resp = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as e:
            raise VisionError(f"读图服务连不上（{type(e).__name__}）") from e
        if resp.status_code != 200:
            raise VisionError(f"读图失败 HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            text = resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as e:
            raise VisionError("读图服务返回了看不懂的内容") from e

        text = strip_code_fence(text.strip())
        # 模型有时会把这句话包在一段解释里，所以不用 == 判
        return "" if NO_TEXT in text else text


def strip_code_fence(text: str) -> str:
    """剥掉模型习惯性套在整段输出外面的 ```markdown 围栏。

    ⚠️ **这不是洁癖，是功能问题。** 切分器靠 `#` 标题分段，而围栏里的
    `## 快速移位` 是代码不是标题——整张图会变成一个没有章节的大块，
    引用里也就没有了「第 N 节」。实测 Kimi 十次里有七八次会加这个围栏。

    只剥「整段恰好被一对围栏包住」的情况。正文中间真的有代码块时
    首尾不匹配，原样返回。
    """
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 2 or not lines[-1].strip().startswith("```"):
        return text
    # 中间还有围栏说明这是「多个代码块」而不是「整段被包住」，别动
    if any(ln.strip().startswith("```") for ln in lines[1:-1]):
        return text
    return "\n".join(lines[1:-1]).strip()


def mime_for(path: Path) -> str:
    return MIME.get(path.suffix.lower(), "image/png")
