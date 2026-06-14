# 02 — 硬件约束

## 目标设备

- GPU: RTX 3060 12GB
- OS: Windows 10/11
- RAM: ≥ 32GB 建议

## 本地 vs 远程

| 模块 | 部署 |
|------|------|
| MinerU | 本地 GPU |
| Embedding | 本地 GPU (:8081) |
| Reranker | 本地 GPU (:8082, P3 按需) |
| LLM | **DeepSeek-V3 API** |
| Qdrant | Docker CPU |
| NetworkX | 进程内 + pickle |

## 显存策略

1. MinerU 与 Embedding **串行**，不并行
2. P1-P2 不加载 Reranker
3. P3 Reranker 用完可卸载
