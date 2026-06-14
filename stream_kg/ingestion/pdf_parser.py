"""PDF 解析器（MinerU CLI 封装）。

为什么用 CLI：
- 与本地 CUDA 环境解耦，便于快速替换解析器；
- 失败时可直接拿 stderr 诊断问题；
- 与规范文档的部署流程一致。
"""

from __future__ import annotations

import string
import subprocess
from pathlib import Path

from pypdf import PdfReader


class PdfParser:
    """通过 MinerU 命令行解析 PDF。"""

    def __init__(
        self,
        *,
        mineru_cli: str = "mineru",
        output_dir: str = "./data/parsed",
        backend: str = "pipeline",
        device: str = "cpu",
        source: str = "modelscope",
        timeout_sec: int = 1800,
        parse_mode: str = "fast",
    ) -> None:
        self.mineru_cli = mineru_cli
        self.output_dir = Path(output_dir)
        self.backend = backend
        self.device = device
        self.source = source
        self.timeout_sec = timeout_sec
        self.parse_mode = parse_mode.strip().lower()

    def parse(self, pdf_path: str) -> tuple[str, int | None]:
        """执行 MinerU 并返回解析文本。

        Returns:
            tuple[text, page_count]
        """
        source = Path(pdf_path)
        if not source.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 先走轻量文本提取，适合大多数“可复制文本”的 PDF，速度快且稳定。
        # 若提取为空，再回退到 MinerU（OCR/版面能力更强，但初始化成本更高）。
        simple_text, simple_page_count = self._parse_with_pypdf(source)
        if simple_text.strip():
            return simple_text, simple_page_count
        if self.parse_mode == "fast":
            raise RuntimeError(
                "PDF 在 fast 模式下无法提取可用文本。"
                "若是扫描件，请将 PDF_PARSE_MODE=quality 后重试（会启用 MinerU，速度较慢）。"
            )

        command = [
            self.mineru_cli,
            "-p",
            str(source),
            "-o",
            str(self.output_dir),
            "-b",
            self.backend,
            "-d",
            self.device,
            "--source",
            self.source,
        ]
        # 超时可配置：避免解析任务长时间卡死占资源。
        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=self.timeout_sec,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"MinerU CLI not found: '{self.mineru_cli}'. "
                "Please install MinerU or set MINERU_CLI to the executable path."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"MinerU parsing timed out after {self.timeout_sec} seconds. "
                "Please retry with a smaller PDF or check MinerU runtime environment."
            ) from exc
        if process.returncode != 0:
            raise RuntimeError(
                f"MinerU failed (code={process.returncode}): {process.stderr or process.stdout}"
            )

        # MinerU 默认按文件名创建目录，例如 parsed/<stem>/content.md
        stem_dir = self.output_dir / source.stem
        content_path = stem_dir / "content.md"
        if not content_path.exists():
            raise RuntimeError(f"MinerU output missing content.md under {stem_dir}")

        text = content_path.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            raise RuntimeError("MinerU output content is empty")

        # Phase 1: page_count is optional; derive in later iterations.
        return text, None

    def _parse_with_pypdf(self, source: Path) -> tuple[str, int | None]:
        """使用 pypdf 进行快速文本提取。

        说明：
        - 仅适用于文本型 PDF；
        - 扫描件通常提取不到文本，返回空串后由 MinerU 继续处理。
        """
        try:
            reader = PdfReader(str(source))
            page_texts: list[str] = []
            for page in reader.pages:
                extracted = page.extract_text() or ""
                if extracted.strip():
                    page_texts.append(extracted.strip())
            merged = "\n\n".join(page_texts)
            if not self._is_usable_text(merged):
                # 避免把乱码直接写入库：交给 MinerU 再做一次高质量解析。
                return "", len(reader.pages)
            return merged, len(reader.pages)
        except Exception:
            # 轻量提取失败不阻断主流程，交给 MinerU 再尝试。
            return "", None

    def _is_usable_text(self, text: str) -> bool:
        content = text.strip()
        if not content:
            return False
        sample = content[:4000]
        length = max(len(sample), 1)
        replacement_ratio = sample.count("�") / length
        if replacement_ratio > 0.05:
            return False
        readable = 0
        allowed_punct = set("，。！？；：、（）《》【】“”‘’.,!?;:()[]{}<>-_/%")
        for ch in sample:
            if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff") or ch.isspace() or ch in allowed_punct:
                readable += 1
        readable_ratio = readable / length
        if readable_ratio < 0.55:
            return False
        noisy = sum(1 for ch in sample if ch not in string.printable and not ("\u4e00" <= ch <= "\u9fff") and not ch.isspace())
        return (noisy / length) < 0.25
