# Reset & Re-ingest Quick Steps

如果诊断 [5c] / [5d] 确认 attention 论文 chunk 自我检索 < 0.3，按以下步骤清空并重导。

## A. 仅清空数据（保留代码与模型）

```powershell
# 1) 停掉 backend（Ctrl+C）；embedding server 可以保留

# 2) 删 SQLite + graph
cd c:\project_pg\stream-kg
Remove-Item .\data\meta.db -ErrorAction SilentlyContinue
Remove-Item .\data\graph.pkl -ErrorAction SilentlyContinue

# 3) 清空 Qdrant collection（用 docker 重启更稳，不影响其它项目）
docker compose restart qdrant
# 或者：
# & "$env:USERPROFILE\.local\bin\uv.exe" run python -c "from qdrant_client import QdrantClient; c=QdrantClient(host='localhost', port=6333); c.delete_collection('chunks'); c.delete_collection('entities')"

# 4) 重启 backend（让它自动 ensure collection）
& "$env:USERPROFILE\.local\bin\uv.exe" run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# 5) 在前端重新上传：简历 + attention 论文
```

## B. 验证

重导完成后，重跑诊断：

```powershell
& "$env:USERPROFILE\.local\bin\uv.exe" run python scripts/diagnose_rag.py
```

应看到 [5] top_score ≥ 0.4，[5c] 自我检索 ≥ 0.95。
