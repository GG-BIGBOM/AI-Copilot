"use client";

/**
 * 空状态里的场景入口（UI_OPTIMIZATION_SPEC §10.3）。
 *
 * 是「可点的内容行」，不是一排卡片：默认透明底、无边框，hover 才浮出一层
 * 极浅的暖灰和一个箭头。
 */

import { ArrowRight } from "lucide-react";

const STARTERS = [
  { title: "京东电子面单模板怎么设置？", desc: "面单开通、模板绑定与打印控件" },
  { title: "退货入库的操作流程是什么？", desc: "退货验货、良品与不良品入库" },
  { title: "怎么配置短信策略与规则？", desc: "发货通知、催付短信与触发规则" },
  { title: "对账单生成异常怎么排查？", desc: "结算费用差异与核销异常" },
];

export function PromptStarters({ onPick }: { onPick: (text: string) => void }) {
  return (
    <section className="w-full">
      <h2 className="px-2 text-[11px] font-medium text-muted-foreground/70">常见问题</h2>

      <div className="mt-1.5 grid gap-px sm:grid-cols-2">
        {STARTERS.map((s) => (
          <button
            key={s.title}
            type="button"
            onClick={() => onPick(s.title)}
            className="group flex items-start justify-between gap-3 rounded-md px-2 py-2.5 text-left transition-colors hover:bg-surface-subtle"
          >
            <span className="min-w-0">
              <span className="block truncate text-sm text-foreground">{s.title}</span>
              <span className="mt-0.5 block truncate text-[13px] text-muted-foreground">
                {s.desc}
              </span>
            </span>
            <ArrowRight className="mt-0.5 size-3.5 shrink-0 text-muted-foreground/0 transition-colors group-hover:text-muted-foreground" />
          </button>
        ))}
      </div>
    </section>
  );
}
