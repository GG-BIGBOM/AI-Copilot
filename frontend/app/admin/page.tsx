"use client";

/**
 * 管理台 · 概览（M15-A）。
 *
 * ⚠️ **这一页没有一句问题原文，是刻意的。** 一个人问过的问题连起来，就是他
 * 在处理哪个客户、哪个故障。仪表盘是一眼扫过去的东西，不该顺带把这些摊开；
 * 要看具体内容，点进「用户」或「反馈」——那是一次明确的动作。
 * 后端也不给（见 `metrics.Summary` 的注释），不是靠前端不显示。
 *
 * 指标口径全部来自后端的 `copilot.metrics`，和 `copilot quality-report`
 * 是同一套函数。两边各算一次的话，同一天会在两个地方给出两个数。
 */

import { useCallback, useEffect, useState } from "react";

import { AdminShell, RangeTabs, Stat } from "@/components/admin/admin-shell";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  ANSWER_SOURCE_LABEL,
  api,
  ApiError,
  type AdminOverview,
  type AdminRange,
} from "@/lib/api";
import { useRequireAdmin } from "@/lib/auth-guard";

function latency(v: { p50: number | null; p95: number | null; count: number }): string {
  if (v.p50 === null) return "—";
  return `${v.p50} / ${v.p95}`;
}

export default function AdminOverviewPage() {
  const auth = useRequireAdmin();
  const [range, setRange] = useState<AdminRange>("7d");
  const [data, setData] = useState<AdminOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((r: AdminRange) => {
    api
      .adminOverview(r)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "拉取失败"));
  }, []);

  useEffect(() => {
    if (auth.status !== "authed") return;
    load(range);
  }, [auth.status, range, load]);

  if (auth.status !== "authed") {
    return (
      <main className="flex h-full items-center justify-center bg-background">
        <span className="text-[13px] text-muted-foreground">正在载入…</span>
      </main>
    );
  }

  return (
    <AdminShell
      title="概览"
      subtitle="全站使用情况。口径与 copilot quality-report 完全一致。"
      actions={<RangeTabs value={range} onChange={setRange} />}
    >
      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription className="text-[13px]">{error}</AlertDescription>
        </Alert>
      )}

      {!data ? (
        <p className="py-16 text-center text-[13px] text-muted-foreground">正在载入…</p>
      ) : (
        <div className="space-y-8">
          <section>
            <h2 className="mb-2 text-[13px] font-medium text-foreground">使用</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label="提问数" value={data.questions} />
              <Stat label="活跃用户" value={data.active_users} hint={`共 ${data.users_total} 个账号`} />
              <Stat label="上传文档" value={data.uploaded_documents} hint={`库里共 ${data.documents_total} 篇`} />
              <Stat
                label="token"
                value={data.tokens.toLocaleString("zh-CN")}
                hint="估算值，不分进出——按字符估的数拆不出 input/output"
              />
            </div>
          </section>

          <section>
            <h2 className="mb-2 text-[13px] font-medium text-foreground">答案来源</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {Object.entries(data.by_source).length === 0 ? (
                <p className="text-[13px] text-muted-foreground">这段时间一条请求都没有。</p>
              ) : (
                Object.entries(data.by_source).map(([key, n]) => (
                  <Stat
                    key={key}
                    label={ANSWER_SOURCE_LABEL[key] ?? key}
                    value={n}
                    hint={
                      data.questions
                        ? `${((100 * n) / data.questions).toFixed(1)}%`
                        : undefined
                    }
                  />
                ))
              )}
            </div>
          </section>

          <section>
            <h2 className="mb-2 text-[13px] font-medium text-foreground">质量</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label="👍" value={data.thumbs_up} />
              <Stat label="👎" value={data.thumbs_down} tone={data.thumbs_down ? "warn" : "default"} />
              <Stat
                label="差评率"
                value={data.feedback_rate}
                hint={`分母 = 被评价过的 ${data.thumbs_up + data.thumbs_down} 轮，不是全部请求`}
              />
              <Stat
                label="人工订正"
                value={data.verified_answers}
                hint="这段时间新写的条数；不是「有多少回答用上了订正」——那个今天量不出来"
              />
            </div>
          </section>

          <section>
            <h2 className="mb-2 text-[13px] font-medium text-foreground">Agent 与错误</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat label="Agent 轮次" value={data.agent_requests} />
              <Stat
                label="其中 tools 为空"
                value={data.agent_without_tools}
                hint="追问 / 寒暄本来就不调工具，这一项不等于违规"
              />
              <Stat
                label="越过工具直答"
                value={data.tool_bypass}
                tone={data.tool_bypass ? "bad" : "default"}
                hint="一个工具都没调却写出了有出处样子的答案。这是红线"
              />
              <Stat
                label="出错"
                value={data.errors}
                tone={data.errors ? "warn" : "default"}
                hint={`用户主动中断 ${data.interrupted} 次，不算在里面`}
              />
            </div>
          </section>

          <section>
            <h2 className="mb-2 text-[13px] font-medium text-foreground">延迟（不含寒暄）</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Stat
                label="首字 p50 / p95"
                value={latency(data.ttfb)}
                hint={`毫秒 · ${data.ttfb.count} 次`}
              />
              <Stat
                label="总时长 p50 / p95"
                value={latency(data.duration)}
                hint={`毫秒 · ${data.duration.count} 次`}
              />
              <Stat
                label="解析失败任务"
                value={data.failed_jobs}
                tone={data.failed_jobs ? "warn" : "default"}
              />
            </div>
            <p className="mt-3 text-[12px] text-muted-foreground">
              寒暄那条路一次模型调用都不花、首字是毫秒级的，混进来会把 p50 拉到看不出问题，
              所以延迟统计把它排除在外。
            </p>
          </section>
        </div>
      )}
    </AdminShell>
  );
}
