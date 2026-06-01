#!/usr/bin/env python
"""RePEc / IDEAS 论文抓取器。

通过 IDEAS/RePEc 检索经济学工作论文和期刊文章。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List

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
CONFIG_FILE = os.path.join(ROOT_DIR, "config.yaml")
CRAWL_STATE_FILE = os.path.join(ROOT_DIR, "archive", "repec_crawl_state.json")
SEEN_IDS_FILE = os.path.join(ROOT_DIR, "archive", "repec_seen.json")

SEARCH_URL = "https://ideas.repec.org/cgi-bin/htsearch"
SOURCE_KEY = "repec"


class _HTMLStripper(HTMLParser):
    """简单的 HTML 标签移除工具。"""
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts).strip()


def _strip_html(html: str) -> str:
    s = _HTMLStripper()
    try:
        s.feed(html)
    except Exception:
        return html
    return s.get_text()


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


def load_config() -> dict:
    try:
        return load_config_with_source_migration(CONFIG_FILE, write_back=False)
    except Exception as exc:
        log(f"[WARN] 读取 config.yaml 失败：{exc}")
        return {}


def load_last_crawl_at() -> datetime | None:
    if not os.path.exists(CRAWL_STATE_FILE):
        return None
    try:
        with open(CRAWL_STATE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
    except Exception:
        return None
    raw = _norm(payload.get("last_crawl_at"))
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def save_last_crawl_at(at_time: datetime) -> None:
    os.makedirs(os.path.dirname(CRAWL_STATE_FILE), exist_ok=True)
    payload = {"last_crawl_at": at_time.astimezone(timezone.utc).isoformat()}
    with open(CRAWL_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_seen_state() -> tuple[set[str], datetime | None]:
    if not os.path.exists(SEEN_IDS_FILE):
        return set(), None
    try:
        with open(SEEN_IDS_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
    except Exception:
        return set(), None
    raw_ids = payload.get("ids") or []
    if not isinstance(raw_ids, list):
        raw_ids = []
    seen_ids = {str(item).strip() for item in raw_ids if str(item).strip()}
    raw_latest = _norm(payload.get("latest_published_at"))
    latest_dt = None
    if raw_latest:
        try:
            latest_dt = datetime.fromisoformat(raw_latest.replace("Z", "+00:00"))
            if latest_dt.tzinfo is None:
                latest_dt = latest_dt.replace(tzinfo=timezone.utc)
            latest_dt = latest_dt.astimezone(timezone.utc)
        except Exception:
            latest_dt = None
    return seen_ids, latest_dt


def save_seen_state(seen_ids: set[str], latest_published_at: datetime | None) -> None:
    os.makedirs(os.path.dirname(SEEN_IDS_FILE), exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "latest_published_at": latest_published_at.astimezone(timezone.utc).isoformat()
        if latest_published_at else "",
        "ids": sorted(seen_ids),
    }
    with open(SEEN_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def fetch_papers(query: str, days: int = 30, max_results: int = 50) -> List[Dict[str, Any]]:
    """通过 IDEAS/RePEc 搜索检索论文。"""
    headers = {"User-Agent": "DailyPaperReader/1.0", "Accept": "text/html"}
    params = {"q": query, "cmd": "search", "dt": "paper", "s": "0"}

    papers: List[Dict[str, Any]] = []
    html_text = ""
    for attempt in range(3):
        try:
            resp = requests.get(SEARCH_URL, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            html_text = resp.text
            break
        except Exception as exc:
            log(f"[WARN] RePEc/IDEAS 请求失败 (第{attempt + 1}次)：{exc}")
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
            else:
                return []

    paper_blocks = re.findall(
        r'<li[^>]*class="list-group-item"[^>]*>(.*?)</li>', html_text, re.DOTALL,
    )
    for block in paper_blocks[:max_results]:
        title_match = re.search(r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not title_match:
            continue
        url = title_match.group(1)
        title = _strip_html(title_match.group(2))
        if not title:
            continue
        if url.startswith("/"):
            url = f"https://ideas.repec.org{url}"
        handle_match = re.search(r"/([a-z]+/[a-z]+/\w+)\.html", url)
        paper_id = f"repec:{handle_match.group(1)}" if handle_match else f"repec:{abs(hash(url))}"
        author_match = re.search(r'by\s+(.*?)(?:<br|<div|\n)', block, re.DOTALL)
        authors_text = _strip_html(author_match.group(1)) if author_match else ""
        authors = [a.strip() for a in authors_text.split("&") if a.strip()] if authors_text else []
        abstract_match = re.search(r'<div[^>]*class="abstract"[^>]*>(.*?)</div>', block, re.DOTALL)
        abstract = _strip_html(abstract_match.group(1)) if abstract_match else ""
        year_match = re.search(r"\b(19|20)\d{2}\b", block)
        year = year_match.group(0) if year_match else ""
        papers.append({
            "id": paper_id, "title": title, "abstract": abstract, "authors": authors,
            "published": year, "updated_at": year, "url": url, "pdf_url": "",
            "categories": ["economics"], "source": SOURCE_KEY,
        })

    log(f"RePEc/IDEAS 检索到 {len(papers)} 篇论文 (query={query!r})")
    return papers


def main() -> None:
    parser = argparse.ArgumentParser(description="RePEc/IDEAS 论文抓取")
    parser.add_argument("--query", default="monetary policy", help="搜索查询词")
    parser.add_argument("--days", type=int, default=30, help="抓取最近多少天的论文")
    parser.add_argument("--output", default="", help="输出 JSON 文件路径")
    args = parser.parse_args()
    papers = fetch_papers(args.query, days=args.days)
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(papers, f, ensure_ascii=False, indent=2)
        log(f"已写入 {len(papers)} 篇论文到 {args.output}")
    else:
        print(json.dumps(papers, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
