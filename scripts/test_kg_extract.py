# -*- coding: utf-8 -*-
"""Phase A.0 PoC：验证 DeepSeek LLM KG 抽取通路。

不复用 rag_generator / 不动 ingest pipeline，目的：
  * 验证 prompt 质量（覆盖率 / 关系正确性）
  * 验证 JSON 输出稳定性（解析成功率）
  * 测量单 chunk 耗时与 token 消耗
  * 估算成本

用法：
    & "$env:USERPROFILE\\.local\\bin\\uv.exe" run python scripts/test_kg_extract.py
    & "$env:USERPROFILE\\.local\\bin\\uv.exe" run python scripts/test_kg_extract.py --runs 3
    & "$env:USERPROFILE\\.local\\bin\\uv.exe" run python scripts/test_kg_extract.py --model deepseek-v4-flash

参考：
  * prompts/kg_extraction.txt
  * docs/spec/13-kg-llm-redesign.md §2.2 / §2.3
  * docs/planning/14-kg-llm-execution-plan.md §1（PoC 验收标准）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Windows 默认 stdout 用系统 cp936/gbk，遇到中文 / 全角符号会 UnicodeEncodeError。
# 这里强制切到 UTF-8，确保跨平台一致。
if sys.stdout.encoding and sys.stdout.encoding.lower() not in {"utf-8", "utf8"}:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

from stream_kg.config import settings

PROMPT_PATH = ROOT / "prompts" / "kg_extraction.txt"

# 硬编码两段 attention 论文典型 chunk（手写复刻，非直接复制论文，避免版权问题）
SAMPLE_CHUNKS: dict[str, str] = {
    "abstract": (
        "The dominant sequence transduction models are based on complex recurrent or "
        "convolutional neural networks that include an encoder and a decoder. The best "
        "performing models also connect the encoder and decoder through an attention "
        "mechanism. We propose a new simple network architecture, the Transformer, based "
        "solely on attention mechanisms, dispensing with recurrence and convolutions entirely. "
        "Experiments on two machine translation tasks show these models to be superior in "
        "quality while being more parallelizable and requiring significantly less time to "
        "train. Our model achieves 28.4 BLEU on the WMT 2014 English-to-German translation "
        "task, improving over the existing best results, including ensembles, by over 2 BLEU. "
        "Authors include Ashish Vaswani, Noam Shazeer, and Niki Parmar from Google Brain and "
        "Google Research."
    ),
    "scaled_dot_product": (
        "We call our particular attention 'Scaled Dot-Product Attention'. The input consists "
        "of queries and keys of dimension d_k, and values of dimension d_v. We compute the "
        "dot products of the query with all keys, divide each by sqrt(d_k), and apply a "
        "softmax function to obtain the weights on the values. In practice, we compute the "
        "attention function on a set of queries simultaneously, packed together into a matrix "
        "Q. The keys and values are also packed together into matrices K and V. We compute "
        "the matrix of outputs as Attention(Q, K, V) = softmax(QK^T / sqrt(d_k)) V. Multi-Head "
        "Attention runs h=8 attention heads in parallel, each producing d_v-dimensional output "
        "values, which are concatenated and once again projected, resulting in the final values."
    ),
}

# 期望关键实体（模糊匹配，用于覆盖率评分）
EXPECTED_ENTITIES: dict[str, set[str]] = {
    "abstract": {
        "Transformer",
        "attention",
        "recurrent",
        "convolutional",
        "WMT",
        "BLEU",
        "machine translation",
        "Vaswani",
        "Google",
    },
    "scaled_dot_product": {
        "Scaled Dot-Product Attention",
        "Multi-Head Attention",
        "queries",
        "keys",
        "values",
        "softmax",
    },
}

# 期望关键关系（head 子串, relation, tail 候选子串列表）
EXPECTED_RELATIONS: dict[str, list[tuple[str, str, list[str]]]] = {
    "abstract": [
        ("Transformer", "improves", ["recurrent", "RNN", "convolutional"]),
    ],
    "scaled_dot_product": [
        ("Multi-Head Attention", "part_of", ["Transformer", "Scaled Dot-Product"]),
    ],
}

# DeepSeek 计价（参考 13 文档 §2.3；实际请以官方为准）
COST_PER_M_INPUT_CNY = 0.5
COST_PER_M_OUTPUT_CNY = 1.5


def load_prompt_template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def build_prompt(chunk_text: str) -> str:
    return load_prompt_template().replace("{{CHUNK_TEXT}}", chunk_text)


async def call_deepseek(
    *,
    prompt: str,
    model: str,
    api_key: str,
    api_base: str,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    timeout_sec: float = 60.0,
) -> dict:
    """直接调 DeepSeek /chat/completions，返回原始 response dict。

    使用 response_format=json_object 让 DeepSeek 服务端保证返回合法 JSON 字符串，
    比纯 text 模式更稳。注意 DeepSeek 要求 user prompt 含 'json' 字样才允许该参数；
    我们的 prompt 显式声明了"严格 JSON 对象"，满足条件。
    """
    url = f"{api_base.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict JSON extractor. Always return valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def fuzzy_contains(name: str, expected: str) -> bool:
    n = (name or "").lower().strip()
    e = expected.lower().strip()
    return bool(n) and bool(e) and (e in n or n in e)


def evaluate_coverage(
    extracted_entities: list[dict],
    extracted_relations: list[dict],
    chunk_key: str,
) -> dict:
    expected = EXPECTED_ENTITIES[chunk_key]
    extracted_names = [e.get("name", "") for e in extracted_entities]
    hits = sum(1 for exp in expected if any(fuzzy_contains(n, exp) for n in extracted_names))
    coverage = hits / max(1, len(expected))

    rel_results: list[tuple[str, bool]] = []
    for head_exp, rel_exp, tail_exps in EXPECTED_RELATIONS[chunk_key]:
        found = False
        for r in extracted_relations:
            h = (r.get("head") or "").lower()
            t = (r.get("tail") or "").lower()
            rt = r.get("relation", "")
            if rt == rel_exp and head_exp.lower() in h and any(te.lower() in t for te in tail_exps):
                found = True
                break
        rel_results.append((f"{head_exp} -{rel_exp}-> {tail_exps}", found))

    return {
        "entity_coverage": coverage,
        "ent_hits": hits,
        "ent_total": len(expected),
        "relation_hits": rel_results,
    }


async def run_one(
    *,
    chunk_key: str,
    chunk_text: str,
    run_idx: int,
    model: str,
    api_key: str,
    api_base: str,
) -> dict:
    prompt = build_prompt(chunk_text)
    t0 = time.perf_counter()
    parse_ok = False
    error_msg: str | None = None
    extracted: dict | None = None
    usage: dict | None = None
    try:
        resp = await call_deepseek(prompt=prompt, model=model, api_key=api_key, api_base=api_base)
        elapsed = time.perf_counter() - t0
        usage = resp.get("usage")
        content = resp["choices"][0]["message"]["content"]
        try:
            extracted = json.loads(content)
            parse_ok = True
        except json.JSONDecodeError as e:
            error_msg = f"JSON parse fail: {e}; head_of_content={content[:200]!r}"
    except httpx.HTTPStatusError as e:
        elapsed = time.perf_counter() - t0
        body_snippet = e.response.text[:300] if e.response is not None else ""
        error_msg = f"HTTPStatusError {e.response.status_code if e.response else '?'}: {body_snippet}"
    except httpx.HTTPError as e:
        elapsed = time.perf_counter() - t0
        error_msg = f"HTTP error: {e}"

    result: dict = {
        "chunk_key": chunk_key,
        "run_idx": run_idx,
        "elapsed_sec": elapsed,
        "parse_ok": parse_ok,
        "error": error_msg,
        "usage": usage,
        "extracted": extracted,
    }
    if extracted:
        ents = extracted.get("entities", []) or []
        rels = extracted.get("relations", []) or []
        result["coverage"] = evaluate_coverage(ents, rels, chunk_key)
        result["n_entities"] = len(ents)
        result["n_relations"] = len(rels)
    return result


def fmt_usage(usage: dict | None) -> str:
    if not usage:
        return "—"
    pt = usage.get("prompt_tokens", "?")
    ct = usage.get("completion_tokens", "?")
    tt = usage.get("total_tokens", "?")
    return f"in={pt} out={ct} total={tt}"


def print_result(result: dict) -> None:
    print(f"\n--- [{result['chunk_key']} #{result['run_idx']}] ---")
    print(f"  elapsed:  {result['elapsed_sec']:.2f}s")
    print(f"  parse_ok: {result['parse_ok']}")
    print(f"  usage:    {fmt_usage(result['usage'])}")
    if result.get("error"):
        print(f"  ERROR: {result['error']}")
    if result.get("extracted"):
        ents = result["extracted"].get("entities", []) or []
        rels = result["extracted"].get("relations", []) or []
        print(f"  entities ({len(ents)}):")
        for e in ents:
            print(
                f"    - [{(e.get('type') or '?'):>12}] "
                f"{(e.get('name') or '?'):<35} "
                f"salience={e.get('salience', '?')} "
                f"aliases={e.get('aliases', [])}"
            )
        print(f"  relations ({len(rels)}):")
        for r in rels:
            print(
                f"    - {(r.get('head') or '?'):<25} "
                f"--[{(r.get('relation') or '?'):>14}]--> "
                f"{(r.get('tail') or '?'):<30} "
                f"conf={r.get('confidence', '?')}"
            )
        cov = result.get("coverage", {})
        if cov:
            print(
                f"  entity coverage: {cov['ent_hits']}/{cov['ent_total']} "
                f"= {cov['entity_coverage'] * 100:.0f}%"
            )
            for desc, hit in cov["relation_hits"]:
                print(f"  relation check:  {'OK  ' if hit else 'MISS'} {desc}")


def compute_summary(results: list[dict]) -> dict:
    total_runs = len(results)
    parse_ok_count = sum(1 for r in results if r["parse_ok"])
    parse_rate = parse_ok_count / max(1, total_runs)
    elapsed_avg = sum(r["elapsed_sec"] for r in results) / max(1, total_runs)
    total_in = sum((r.get("usage") or {}).get("prompt_tokens", 0) for r in results)
    total_out = sum((r.get("usage") or {}).get("completion_tokens", 0) for r in results)
    cov_results = [r.get("coverage", {}).get("entity_coverage", 0.0) for r in results if r["parse_ok"]]
    avg_cov = sum(cov_results) / max(1, len(cov_results)) if cov_results else 0.0
    cost_yuan = (
        (total_in / 1_000_000) * COST_PER_M_INPUT_CNY
        + (total_out / 1_000_000) * COST_PER_M_OUTPUT_CNY
    )
    return {
        "total_runs": total_runs,
        "parse_ok_count": parse_ok_count,
        "parse_rate": parse_rate,
        "elapsed_avg": elapsed_avg,
        "total_in_tokens": total_in,
        "total_out_tokens": total_out,
        "avg_cov": avg_cov,
        "cost_yuan": cost_yuan,
        "pass_parse": parse_rate >= 0.9,
        "pass_cov": avg_cov >= 0.8,
        "pass_speed": elapsed_avg < 8.0,
    }


def print_summary(results: list[dict]) -> None:
    s = compute_summary(results)
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  runs:              {s['total_runs']}")
    print(f"  parse success:     {s['parse_ok_count']}/{s['total_runs']} = {s['parse_rate'] * 100:.0f}%")
    print(f"  avg elapsed:       {s['elapsed_avg']:.2f}s")
    print(f"  avg ent coverage:  {s['avg_cov'] * 100:.0f}%  (over parse-ok runs)")
    print(f"  total tokens:      in={s['total_in_tokens']} out={s['total_out_tokens']}")
    print(f"  est. cost (CNY):   {s['cost_yuan']:.4f} CNY")

    print("\n  Acceptance (14-doc section 1):")
    print(f"    JSON parse rate >= 90%   : {s['parse_rate'] * 100:.0f}%  {'PASS' if s['pass_parse'] else 'FAIL'}")
    print(f"    Entity coverage >= 80%   : {s['avg_cov'] * 100:.0f}%  {'PASS' if s['pass_cov'] else 'FAIL'}")
    print(f"    Avg chunk elapsed < 8s   : {s['elapsed_avg']:.2f}s  {'PASS' if s['pass_speed'] else 'FAIL'}")

    all_pass = s["pass_parse"] and s["pass_cov"] and s["pass_speed"]
    verdict = "PASS - proceed to Phase A" if all_pass else "FAIL - tune prompt or switch model"
    print(f"\n  >>> Overall: {verdict}")


def render_markdown_report(results: list[dict], *, model: str) -> str:
    """生成可直接贴到 14 文档 §10 PoC 报告区的 markdown。"""
    s = compute_summary(results)
    lines: list[str] = []
    lines.append(f"### PoC 运行结果（model=`{model}`，{s['total_runs']} 次调用）\n")
    lines.append("#### 汇总指标\n")
    lines.append("| 指标 | 数值 | 验收阈值 | 结果 |")
    lines.append("|------|------|----------|------|")
    lines.append(
        f"| JSON 解析成功率 | {s['parse_ok_count']}/{s['total_runs']} = "
        f"{s['parse_rate'] * 100:.0f}% | ≥ 90% | {'PASS' if s['pass_parse'] else 'FAIL'} |"
    )
    lines.append(
        f"| 平均实体覆盖率 | {s['avg_cov'] * 100:.0f}% | ≥ 80% | "
        f"{'PASS' if s['pass_cov'] else 'FAIL'} |"
    )
    lines.append(
        f"| 平均单 chunk 耗时 | {s['elapsed_avg']:.2f}s | < 8s | "
        f"{'PASS' if s['pass_speed'] else 'FAIL'} |"
    )
    lines.append(
        f"| token 消耗（累计） | in={s['total_in_tokens']} / out={s['total_out_tokens']} | — | — |"
    )
    lines.append(f"| 预估成本 | {s['cost_yuan']:.4f} CNY | — | — |")
    lines.append("")

    lines.append("#### 各次调用明细\n")
    lines.append("| chunk | run | parse | elapsed | tokens(in/out) | ent | rel | coverage |")
    lines.append("|-------|-----|-------|---------|----------------|-----|-----|----------|")
    for r in results:
        usage = r.get("usage") or {}
        cov = r.get("coverage") or {}
        cov_str = (
            f"{cov.get('ent_hits', 0)}/{cov.get('ent_total', 0)}"
            f" = {cov.get('entity_coverage', 0) * 100:.0f}%"
            if cov else "—"
        )
        lines.append(
            f"| {r['chunk_key']} | {r['run_idx']} | "
            f"{'OK' if r['parse_ok'] else 'FAIL'} | "
            f"{r['elapsed_sec']:.2f}s | "
            f"{usage.get('prompt_tokens', '?')}/{usage.get('completion_tokens', '?')} | "
            f"{r.get('n_entities', '—')} | {r.get('n_relations', '—')} | {cov_str} |"
        )
    lines.append("")

    lines.append("#### 示例抽取结果（首次调用）\n")
    first_ok = next((r for r in results if r["parse_ok"]), None)
    if first_ok and first_ok.get("extracted"):
        ents = first_ok["extracted"].get("entities", []) or []
        rels = first_ok["extracted"].get("relations", []) or []
        lines.append(f"**chunk = `{first_ok['chunk_key']}`**\n")
        lines.append("实体：\n")
        lines.append("| name | type | salience | aliases |")
        lines.append("|------|------|----------|---------|")
        for e in ents:
            aliases = e.get("aliases", []) or []
            lines.append(
                f"| {e.get('name', '?')} | {e.get('type', '?')} | "
                f"{e.get('salience', '?')} | {', '.join(aliases) if aliases else '—'} |"
            )
        lines.append("")
        lines.append("关系：\n")
        lines.append("| head | relation | tail | confidence | evidence |")
        lines.append("|------|----------|------|------------|----------|")
        for r in rels:
            ev = (r.get("evidence", "") or "")[:60]
            lines.append(
                f"| {r.get('head', '?')} | {r.get('relation', '?')} | {r.get('tail', '?')} | "
                f"{r.get('confidence', '?')} | {ev} |"
            )
        lines.append("")

    verdict = (
        "PASS — 可进 Phase A"
        if s["pass_parse"] and s["pass_cov"] and s["pass_speed"]
        else "FAIL — 需要调 prompt 或换模型"
    )
    lines.append(f"#### PoC 结论：**{verdict}**\n")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Phase A.0 PoC：DeepSeek LLM KG 抽取通路验证")
    parser.add_argument(
        "--model",
        default="deepseek-chat",
        help="LLM model name (default: deepseek-chat per execution decision E4)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="每个 chunk 重复跑几次（验收要 3 次重跑统计稳定性）",
    )
    parser.add_argument(
        "--only",
        choices=list(SAMPLE_CHUNKS.keys()),
        help="只跑指定的 chunk",
    )
    parser.add_argument(
        "--save-md",
        type=str,
        default=None,
        help="把 markdown 报告写入指定文件（便于贴到 14 文档 §10）",
    )
    args = parser.parse_args()

    api_key = settings.llm_api_key
    api_base = settings.llm_api_base
    if not api_key:
        print("ERROR: settings.llm_api_key is empty。请在 .env 设置 LLM_API_KEY", file=sys.stderr)
        return 1

    print("DeepSeek PoC")
    print(f"  model     = {args.model}")
    print(f"  api_base  = {api_base}")
    print(f"  runs/chunk= {args.runs}")
    print(f"  prompt    = {PROMPT_PATH.relative_to(ROOT)} ({PROMPT_PATH.stat().st_size} bytes)")

    chunks_to_run = SAMPLE_CHUNKS if not args.only else {args.only: SAMPLE_CHUNKS[args.only]}

    results: list[dict] = []
    for chunk_key, chunk_text in chunks_to_run.items():
        print(f"\n>>> running chunk [{chunk_key}] x {args.runs} ...")
        for run_idx in range(1, args.runs + 1):
            r = await run_one(
                chunk_key=chunk_key,
                chunk_text=chunk_text,
                run_idx=run_idx,
                model=args.model,
                api_key=api_key,
                api_base=api_base,
            )
            print_result(r)
            results.append(r)

    print_summary(results)

    if args.save_md:
        md = render_markdown_report(results, model=args.model)
        out_path = Path(args.save_md)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"\n[saved markdown report] {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
