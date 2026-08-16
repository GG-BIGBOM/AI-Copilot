import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // ⭐ 静态导出。服务器只有 1.6GB 内存，跑不起 Node 进程，也跑不起 next build。
  // 流程固定：本机 npm run build → out/ → rsync 上去 → nginx 直接发静态文件。
  // 代价是没有 Server Actions / Route Handlers——本项目所有逻辑都在 FastAPI，零损失。
  output: "export",

  // 每个路由导出成 `out/<route>/index.html`（而不是 `out/<route>.html`）。
  // nginx 那边一句 `try_files $uri $uri/ /index.html;` 就够了，
  // 不必为每个页面写 .html 后缀的兜底规则。
  trailingSlash: true,

  // 静态导出下没有图片优化服务器。这里本来也不用 next/image，
  // 写上是防止将来谁引一个进来导致 build 直接失败。
  images: { unoptimized: true },
};

export default nextConfig;
