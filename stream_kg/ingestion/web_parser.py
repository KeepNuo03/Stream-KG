"""网页解析器（Trafilatura + HTTP 回退 + 动态页回退）。"""

from __future__ import annotations

import html
import re
from urllib.parse import urlparse

import httpx
import trafilatura


class WebParser:
    """抓取网页并抽取正文。"""

    def __init__(self, *, timeout_sec: float = 20.0, cookie: str = "") -> None:
        self.timeout_sec = timeout_sec
        self.cookie = cookie.strip()

    def parse(self, url: str) -> str:
        """返回单个 URL 的纯文本正文。"""
        text, _ = self.parse_with_meta(url)
        return text

    def parse_with_meta(self, url: str) -> tuple[str, str | None]:
        """返回单个 URL 的正文与标题。"""
        # 第一优先级：Trafilatura 自带下载器（成功率高、实现简单）。
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            extracted = self._extract_with_trafilatura(downloaded)
            if extracted and not self._looks_like_placeholder_content(extracted):
                return extracted, self._extract_title_from_html(downloaded)

        # 第二优先级：自定义 HTTP 下载（可带浏览器头与 Cookie）。
        response = self._fetch_with_httpx(url)
        if response.status_code >= 400:
            raise RuntimeError(self._build_status_error(url, response.status_code))

        raw_html = response.text
        page_title = self._extract_title_from_html(raw_html)
        if self._looks_like_anti_bot_page(url, raw_html):
            # 动态壳/反爬场景下，尝试 reader 代理（对掘金类 JS 站点更稳定）。
            reader_text, reader_title = self._fetch_via_reader_proxy(url)
            if reader_text:
                return reader_text, reader_title or page_title
            raise RuntimeError(
                "目标网站返回安全验证页（疑似反爬拦截），无法直接抽取正文。"
                "若是知乎专栏，建议改为 PDF 上传；或在 .env 配置 WEB_FETCH_COOKIE 后重试。"
            )

        extracted = self._extract_with_trafilatura(raw_html)
        if extracted and not self._looks_like_placeholder_content(extracted):
            return extracted, page_title

        # 兜底：粗粒度去标签，至少给出可检索文本。
        fallback_text = self._strip_html_to_text(raw_html)
        if fallback_text:
            return fallback_text, page_title

        # 最后回退：reader 代理文本化抓取，处理掘金等前端渲染页面。
        reader_text, reader_title = self._fetch_via_reader_proxy(url)
        if reader_text:
            return reader_text, reader_title or page_title
        raise ValueError(f"Failed to extract content from URL: {url}")

    def _fetch_with_httpx(self, url: str) -> httpx.Response:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        with httpx.Client(timeout=self.timeout_sec, follow_redirects=True) as client:
            return client.get(url, headers=headers)

    def _extract_with_trafilatura(self, html_text: str) -> str | None:
        return trafilatura.extract(
            html_text,
            include_comments=False,
            include_tables=True,
        )

    def _fetch_via_reader_proxy(self, url: str) -> tuple[str, str | None]:
        """使用 reader 代理抓取动态网页正文。"""
        reader_url = f"https://r.jina.ai/{url}"
        try:
            with httpx.Client(timeout=max(self.timeout_sec, 30), follow_redirects=True) as client:
                response = client.get(
                    reader_url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36"
                        ),
                        "Accept": "text/plain,text/markdown;q=0.9,*/*;q=0.8",
                    },
                )
            if response.status_code >= 400:
                return "", None
            text = (response.text or "").strip()
            if not text:
                return "", None
            if self._looks_like_placeholder_content(text):
                return "", None
            title, body = self._split_reader_content(text)
            if len(body.strip()) < 120:
                return "", title
            return body, title
        except Exception:
            return "", None

    def _split_reader_content(self, text: str) -> tuple[str | None, str]:
        """拆分 reader 返回的 Title/Header 与正文。"""
        title: str | None = None
        body = text
        title_match = re.search(r"(?im)^Title:\s*(.+)$", text)
        if title_match:
            title = title_match.group(1).strip()

        markdown_anchor = re.search(r"(?im)^Markdown Content:\s*$", text)
        if markdown_anchor:
            body = text[markdown_anchor.end() :].strip()

        return title, body

    def _looks_like_anti_bot_page(self, url: str, html_text: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        lowered = html_text.lower()
        blocked_keywords = (
            "captcha",
            "verify you are human",
            "security check",
            "安全验证",
            "请完成验证",
            "访问受限",
            "zh-zse-ck",
            "please wait",
            "enable javascript",
        )
        if any(keyword in lowered for keyword in blocked_keywords):
            return True
        # 知乎专栏常见反爬场景：直接 403 + 验证页。
        if "zhihu.com" in host and ("登录后查看" in html_text or "安全验证" in html_text):
            return True
        # 掘金详情页在服务端常返回动态壳页面，正文需要二次抓取。
        if "juejin.cn" in host and ("Please wait" in html_text or len(html_text) < 3000):
            return True
        return False

    def _build_status_error(self, url: str, status_code: int) -> str:
        host = (urlparse(url).hostname or "").lower()
        if "zhihu.com" in host and status_code == 403:
            return (
                f"知乎页面返回 403（安全验证/反爬拦截）: {url}。"
                "可改用 PDF 上传；或在 .env 配置 WEB_FETCH_COOKIE 后重试。"
            )
        return f"Failed to download URL (status={status_code}): {url}"

    def _strip_html_to_text(self, html_text: str) -> str:
        no_script = re.sub(r"(?is)<script.*?>.*?</script>", " ", html_text)
        no_style = re.sub(r"(?is)<style.*?>.*?</style>", " ", no_script)
        no_tag = re.sub(r"(?is)<[^>]+>", " ", no_style)
        decoded = html.unescape(no_tag)
        normalized = re.sub(r"\s+", " ", decoded).strip()
        # 太短通常是导航/验证页噪声，不作为有效正文。
        if len(normalized) < 120:
            return ""
        if self._looks_like_placeholder_content(normalized):
            return ""
        return normalized

    def _extract_title_from_html(self, html_text: str) -> str | None:
        """从 HTML title 标签提取标题。"""
        match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html_text)
        if not match:
            return None
        title = html.unescape(match.group(1)).strip()
        title = re.sub(r"\s+", " ", title)
        return title or None

    def _looks_like_placeholder_content(self, text: str) -> bool:
        """识别“等待页/壳页面”文本。"""
        lowered = text.lower()
        placeholder_keywords = (
            "please wait",
            "enable javascript",
            "loading...",
            "just a moment",
            "security check",
            "验证码",
            "安全验证",
        )
        # 短文本且命中占位词时视为无效正文。
        if len(text.strip()) < 200 and any(keyword in lowered for keyword in placeholder_keywords):
            return True
        return False
