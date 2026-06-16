"""粗略测量文档列表 / 删除接口耗时（本地排查用）。

用法：
    uv run python scripts/profile_doc_api.py
"""

from __future__ import annotations

import asyncio
import time

import httpx

API = "http://localhost:8000/api/v1"


async def main() -> None:
    async with httpx.AsyncClient(timeout=120.0) as client:
        t0 = time.perf_counter()
        res = await client.get(f"{API}/documents")
        elapsed = time.perf_counter() - t0
        data = res.json()
        total = data.get("total", 0)
        print(f"GET /documents: {elapsed:.3f}s (total={total}, status={res.status_code})")

        docs = data.get("documents") or []
        if not docs:
            print("无文档，跳过删除测试")
            return
        if not __import__("os").environ.get("PROFILE_DOC_DELETE"):
            print("跳过 DELETE 测试（设置 PROFILE_DOC_DELETE=1 可测删除耗时）")
            return
        doc_id = docs[0]["doc_id"]
        title = docs[0].get("title", doc_id)
        print(f"将测试删除（请确认可删）: {title} ({doc_id})")
        t1 = time.perf_counter()
        del_res = await client.delete(f"{API}/documents/{doc_id}")
        elapsed_del = time.perf_counter() - t1
        print(f"DELETE /documents/{{id}}: {elapsed_del:.3f}s (status={del_res.status_code})")


if __name__ == "__main__":
    asyncio.run(main())
