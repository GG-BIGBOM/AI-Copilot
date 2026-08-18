/**
 * 品牌符号 —— AI Spark + Diamond（UI_OPTIMIZATION_SPEC §21）。
 *
 * 两个状态，都很克制：
 *   idle     菱形描边 + 中性炭色的火花
 *   thinking 火花转成青铜色并轻轻呼吸（≤1.03 缩放，reduce-motion 下自动停）
 *
 * 空闲时**不做任何动画**——一个一直在动的 logo 会一直抢走本该给答案的注意力。
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
    <svg
      viewBox="0 0 24 24"
      fill="none"
      className={cn("size-5", className)}
      role={title ? "img" : "presentation"}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      <path
        d="M12 2.6 21.4 12 12 21.4 2.6 12Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
        opacity={thinking ? 0.5 : 0.72}
      />
      <path
        d="M12 7.4c.42 2.76 1.84 4.18 4.6 4.6-2.76.42-4.18 1.84-4.6 4.6-.42-2.76-1.84-4.18-4.6-4.6 2.76-.42 4.18-1.84 4.6-4.6Z"
        className={cn(
          thinking ? "fill-bronze trace-breathe" : "fill-current",
          "origin-center",
        )}
      />
    </svg>
  );
}
