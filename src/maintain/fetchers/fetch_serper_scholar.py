#!/usr/bin/env python
"""
Serper API 学术搜索抓取器

通过 Serper Google Scholar API 从以下学术数据库搜索论文：
- Elsevier ScienceDirect
- CNKI 知网
- Web of Science (WoS)
- JSTOR
- Scopus
- Google Scholar
- RePEc
- Wiley Online Library

用法示例：
    python fetch_serper_scholar.py --query "monetary policy" --days 30
    python fetch_serper_scholar.py --query "tail risk" --source scopus --num 20
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

try:
    from source_config import load_config_with_source_migration
except Exception:  # pragma: no cover
    from src.source_config import load_config_with_source_migration

SCRIPT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
CONFIG_FILE = os.getenv("DPR_CONFIG_FILE") or os.path.join(ROOT_DIR, "config.yaml")
CRAWL_STATE_FILE = os.path.join(ROOT_DIR, "archive", "serper_scholar_crawl_state.json")

SERPER_SCHOLAR_URL = "https://google.serper.dev/scholar"

# 数据源到 site: 搜索限定的映射
SOURCE_SITE_MAP: Dict[str, str] = {
    "sciencedirect": "site:sciencedirect.com",
    "cnki": "site:cnki.net",
    "wos": "site:webofscience.com",
    "jstor": "site:jstor.org",
    "scopus": "site:scopus.com",
    "google_scholar": "",
    "repec": "site:repec.org",
    "wiley": "site:onlinelibrary.wiley.com",
}

SUPPORTED_SOURCES = list(SOURCE_SITE_MAP.keys())


def log(message: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        print(f"[{ts}] {message}", flush=True)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _get_serper_api_key() -> str:
    key = _norm(os.getenv("SERPER_API_KEY"))
    if not key:
        raise RuntimeError(
            "未设置 SERPER_API_KEY 环境变量。"
            "请在 .env 文件或环境变量中设置您的 Serper API Key。"
        )
    return key


def build_search_query(query: str, source: str) -> str:
    """构建带有 site: 限定的搜索查询。"""
    site_filter = SOURCE_SITE_MAP.get(source, "")
    if site_filter:
        return f"{query} {site_filter}"
    return query


def _slugify(text: str) -> str:
    s = _norm(text).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "item"


def build_paper_id(source: str, title: str, link: str) -> str:
    """根据来源、标题和链接生成唯一的论文 ID。"""
    slug = _slugify(title)[:60]
    # 从 link 提取一些唯一标识
    link_hash = _slugify(link.split("//", 1)[-1] if "//" in link else link)[:40]
    return f"{source}-{slug}-{link_hash}"


def search_serper_scholar(
    query: str,
    *,
    api_key: str,
    source: str = "google_scholar",
    num: int = 10,
    year_from: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    调用 Serper Scholar API 搜索学术论文。

    返回论文列表，每篇论文包含 title, link, snippet, publication_info 等。
    """
    search_query = build_search_query(query, source)
    payload: Dict[str, Any] = {
        "q": search_query,
        "num": min(num, 40),
    }
    if year_from is not None:
        payload["yearFrom"] = year_from

    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }

    log(f"[Serper] 搜索: source={source}, query={search_query!r}, num={num}")

    try:
        resp = requests.post(
            SERPER_SCHOLAR_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        log(f"[Serper] 请求失败: {exc}")
        return []

    data = resp.json()
    organic = data.get("organic") or []

    papers: List[Dict[str, Any]] = []
    for item in organic:
        title = _norm(item.get("title"))
        link = _norm(item.get("link"))
        if not title or not link:
            continue

        snippet = _norm(item.get("snippet"))
        pub_info = item.get("publicationInfo") or {}
        citation_info = item.get("citationInfo") or {}

        paper = {
            "id": build_paper_id(source, title, link),
            "source": source,
            "title": title,
            "abstract": snippet,
            "url": link,
            "authors": _norm(pub_info.get("authors")),
            "publication": _norm(pub_info.get("summary")),
            "year": _norm(item.get("year")),
            "cited_by": citation_info.get("citedBy"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        papers.append(paper)

    log(f"[Serper] 来源 {source} 返回 {len(papers)} 篇论文")
    return papers


def fetch_papers_for_query(
    query: str,
    *,
    api_key: str,
    sources: Optional[List[str]] = None,
    num_per_source: int = 10,
    days: int = 365,
) -> List[Dict[str, Any]]:
    """
    对多个学术来源执行搜索，合并结果。
    """
    if sources is None:
        sources = SUPPORTED_SOURCES

    year_from = None
    if days and days < 365 * 5:
        year_from = (datetime.now(timezone.utc) - timedelta(days=days)).year

    all_papers: List[Dict[str, Any]] = []
    seen_ids: set = set()

    for source in sources:
        if source not in SOURCE_SITE_MAP:
            log(f"[WARN] 未知来源: {source}，跳过。")
            continue

        papers = search_serper_scholar(
            query,
            api_key=api_key,
            source=source,
            num=num_per_source,
            year_from=year_from,
        )

        for paper in papers:
            pid = paper["id"]
            if pid not in seen_ids:
                seen_ids.add(pid)
                all_papers.append(paper)

        # 避免速率限制
        if len(sources) > 1:
            time.sleep(0.5)

    return all_papers


def save_results(papers: List[Dict[str, Any]], output_path: str) -> None:
    """保存抓取结果到 JSON 文件。"""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "total": len(papers),
                "papers": papers,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    log(f"[Serper] 保存 {len(papers)} 篇论文到 {output_path}")


def save_crawl_state() -> None:
    """保存最近一次抓取时间。"""
    os.makedirs(os.path.dirname(CRAWL_STATE_FILE), exist_ok=True)
    payload = {"last_crawl_at": datetime.now(timezone.utc).isoformat()}
    with open(CRAWL_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serper Scholar 学术论文搜索抓取器")
    parser.add_argument("--query", type=str, required=True, help="搜索关键词")
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help=f"指定数据源（{', '.join(SUPPORTED_SOURCES)}），留空则搜索全部来源",
    )
    parser.add_argument("--num", type=int, default=10, help="每个来源返回的最大论文数量（默认 10）")
    parser.add_argument("--days", type=int, default=365, help="搜索最近 N 天的论文（默认 365）")
    parser.add_argument("--output", type=str, default=None, help="输出 JSON 文件路径")
    args = parser.parse_args()

    api_key = _get_serper_api_key()

    sources = None
    if args.source:
        sources = [s.strip() for s in args.source.split(",") if s.strip()]

    papers = fetch_papers_for_query(
        args.query,
        api_key=api_key,
        sources=sources,
        num_per_source=args.num,
        days=args.days,
    )

    if args.output:
        save_results(papers, args.output)
    else:
        run_date = datetime.now(timezone.utc).strftime("%Y%m%d")
        output_dir = os.path.join(ROOT_DIR, "archive", run_date, "raw")
        output_path = os.path.join(output_dir, "serper_scholar_results.json")
        save_results(papers, output_path)

    save_crawl_state()
    log(f"[Serper] 抓取完成，共 {len(papers)} 篇论文。")


if __name__ == "__main__":
    main()
