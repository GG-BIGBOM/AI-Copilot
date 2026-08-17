import type { Metadata, Viewport } from "next";

import { THEME_INIT_SCRIPT } from "@/lib/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: "旗舰版 ERP 知识库助手",
  description: "基于语雀公开知识库的带引用问答",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // 大概率有人用手机开，输入框聚焦时别让 iOS 自动放大页面
  maximumScale: 1,
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // 不用 next/font/google：那会给构建加一次外网字体下载（断网就 build 失败），
    // 而 Geist 只覆盖拉丁字符，对中文界面帮不上忙。直接用系统字体栈。
    <html lang="zh-CN" className="h-full antialiased">
      <body className="bg-background text-foreground min-h-full">
        {/* 必须是 body 的第一个子节点：同步执行，早于任何内容绘制。
            放在 Sidebar 的 useEffect 里的话，主题要等首屏画完才应用——
            深色用户每次进页面都会先闪一下白，而且登录页/注册页根本没有
            Sidebar，压根轮不到那段代码跑。 */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        {children}
      </body>
    </html>
  );
}
