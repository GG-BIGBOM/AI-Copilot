"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { api } from "@/lib/api";

/** 落地页只负责分流：登录了去聊天页，没登录去登录页。 */
export default function Home() {
  const router = useRouter();

  useEffect(() => {
    api
      .me()
      .then(() => router.replace("/chat"))
      .catch(() => router.replace("/login"));
  }, [router]);

  return (
    <main className="flex h-full items-center justify-center">
      <p className="text-muted-foreground text-sm">正在加载…</p>
    </main>
  );
}
