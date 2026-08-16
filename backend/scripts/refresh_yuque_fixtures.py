"""重新抓取语雀测试样本。

什么时候跑：`test_yuque_parse.py` 挂了，怀疑是语雀改版。
先跑这个刷新样本，再看测试是否恢复——恢复了说明结构变了要改解析代码，
没恢复说明是解析逻辑本身的 bug。

    uv run python scripts/refresh_yuque_fixtures.py

样本取最小的库（CRM，4 篇），别给人家服务器添麻烦。
"""

from __future__ import annotations

import json
from pathlib import Path

from copilot.sources.yuque import YUQUE_HOST, YuqueClient

LOGIN = "wdterpqjb"
SAMPLE_BOOK = "crm"
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "yuque"


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)

    with YuqueClient() as client:
        group_id, group_name = client.fetch_group_id(LOGIN)
        print(f"空间: {group_name} (id={group_id})")

        # 主页 HTML —— 唯一依赖 HTML 结构的一步，样本要留
        home = client._get(f"{YUQUE_HOST}/{LOGIN}")
        (FIXTURES / "home.html").write_text(home.text, encoding="utf-8")
        print(f"  home.html            {len(home.text) // 1024} KB")

        raw_books = client._get_json(f"{YUQUE_HOST}/api/groups/{group_id}/books")
        (FIXTURES / "books.json").write_text(
            json.dumps(raw_books, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  books.json           {len(raw_books.get('data', []))} 个知识库")

        book = next(b for b in client.list_books(group_id) if b.slug == SAMPLE_BOOK)

        raw_toc = client._get_json(f"{YUQUE_HOST}/api/catalog_nodes?book_id={book.id}")
        (FIXTURES / "catalog_nodes.json").write_text(
            json.dumps(raw_toc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  catalog_nodes.json   {len(raw_toc.get('data', []))} 个节点")

        node = next(n for n in client.fetch_toc(book.id) if n.type == "DOC" and n.slug)
        raw_doc = client._get_json(
            f"{YUQUE_HOST}/api/docs/{node.slug}?book_id={book.id}&merge_dynamic_data=false"
        )
        (FIXTURES / "doc.json").write_text(
            json.dumps(raw_doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        content = raw_doc.get("data", {}).get("content", "")
        (FIXTURES / "doc_content.html").write_text(content, encoding="utf-8")
        print(f"  doc.json             {node.title}")
        print(f"  doc_content.html     {len(content) // 1024} KB")

    print(f"\n样本已更新到 {FIXTURES}")


if __name__ == "__main__":
    main()
