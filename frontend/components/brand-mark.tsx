/**
 * 品牌标识 —— 慧策 logo。
 *
 * 两个入口：
 *   BrandMark 方形图标（只有符号），给侧栏、消息头像这类正方形槽位用
 *   BrandLogo 完整横版（符号 + 慧策字样），给登录页这类有横向空间的地方用
 *
 * 空闲时**不做任何动画**——一个一直在动的 logo 会一直抢走本该给答案的注意力；
 * thinking 时才轻轻呼吸（trace-breathe 在 reduce-motion 下自动停）。
 *
 * 用原生 <img> 而不是 next/image：本项目是静态导出，没有图片优化服务器，
 * next/image 在这里只会多一层壳。
 */

import { cn } from "@/lib/utils";

export function BrandMark({
  className,
  thinking = false,
  title,
}: {
  className?: string;
  thinking?: boolean;
  /** 传了就当成有语义的图形（给读屏用），不传则是纯装饰 */
  title?: string;
}) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src="/brand/mark.png"
      alt={title ?? ""}
      role={title ? "img" : "presentation"}
      aria-hidden={title ? undefined : true}
      draggable={false}
      className={cn(
        "size-5 shrink-0 select-none object-contain",
        thinking && "trace-breathe origin-center",
        className,
      )}
    />
  );
}

export function BrandLogo({
  className,
  title = "慧策",
}: {
  className?: string;
  title?: string;
}) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src="/brand/logo.png"
      alt={title}
      draggable={false}
      className={cn("h-6 w-auto select-none object-contain", className)}
    />
  );
}
