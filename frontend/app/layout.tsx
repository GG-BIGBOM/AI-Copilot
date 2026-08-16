import type { Metadata, Viewport } from "next";

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
      <body className="bg-background text-foreground min-h-full">{children}</body>
    </html>
  );
}
