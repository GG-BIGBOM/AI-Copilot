export type NumberedImage = { n: number; url: string };

const IMAGE_REF_RE = /\[图\s*(\d{1,2})\]/g;

/** 把正文里真实存在的图号替换成 Markdown 图片；不存在的图号直接移除。 */
export function inlineImages(content: string, images: readonly NumberedImage[]): string {
  const byNumber = new Map(images.map((image) => [image.n, image.url]));
  return content.replace(IMAGE_REF_RE, (_, rawNumber: string) => {
    const n = Number(rawNumber);
    const url = byNumber.get(n);
    return url ? `![图${n}](${url})` : "";
  });
}

/**
 * 模型完全漏写有效 `[图N]` 时，把后端已经确认存在的图片作为收起的参考图区展示。
 *
 * 只要正文已经引用过一张真实图片，就不追加剩余图片：未被模型选择的图可能与
 * 当前步骤无关，自动补齐会提高错图风险。
 */
export function fallbackImages(
  content: string,
  images: readonly NumberedImage[],
): readonly NumberedImage[] {
  if (images.length === 0) return [];
  const known = new Set(images.map((image) => image.n));
  const hasValidReference = Array.from(content.matchAll(IMAGE_REF_RE)).some((match) =>
    known.has(Number(match[1])),
  );
  return hasValidReference ? [] : images;
}
