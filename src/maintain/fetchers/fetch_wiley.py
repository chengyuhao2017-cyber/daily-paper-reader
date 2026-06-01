#!/usr/bin/env python
"""Wiley Online Library 论文抓取器。

通过 CrossRef API 检索 Wiley 出版的学术文献。
Wiley 论文可通过 CrossRef DOI 检索接口获取（member ID 311）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
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
CRAWL_STATE_FILE = os.path.join(ROOT_DIR, "archive", "wiley_crawl_state.json")
SEEN_IDS_FILE = os.path.join(ROOT_DIR, "archive", "wiley_seen.json")

API_BASE = "https://api.crossref.org/works"
WILEY_MEMBER_ID = "311"
SOURCE_KEY = "wiley"


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


def _parse_date_parts(date_parts: list) -> str:
    """将 CrossRef date-parts 格式转换为 ISO 日期字符串。"""
    if not date_parts or not isinstance(date_parts, list):
        return ""
    parts = date_parts[0] if isinstance(date_parts[0], list) else date_parts
    if len(parts) >= 3:
        return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
    elif len(parts) >= 2:
        return f"{parts[0]:04d}-{parts[1]:02d}-01"
    elif len(parts) >= 1:
        return f"{parts[0]:04d}-01-01"
    return ""


def fetch_papers(query: str, days: int = 7, max_results: int = 50) -> List[Dict[str, Any]]:
    """通过 CrossRef API 检索 Wiley 出版的论文。"""
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    headers = {
        "User-Agent": "DailyPaperReader/1.0 (mailto:paper-reader@example.com)",
        "Accept": "application/json",
    }
    params = {
        "query": query,
        "filter": f"member:{WILEY_MEMBER_ID},from-pub-date:{start_date.strftime('%Y-%m-%d')},until-pub-date:{end_date.strftime('%Y-%m-%d')}",
        "rows": min(max_results, 50), "sort": "published", "order": "desc",
    }

    papers: List[Dict[str, Any]] = []
    data: dict = {}
    for attempt in range(3):
        try:
            resp = requests.get(API_BASE, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as exc:
            log(f"[WARN] CrossRef/Wiley API 请求失败 (第{attempt + 1}次)：{exc}")
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
            else:
                return []

    items = (data.get("message") or {}).get("items") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        doi = _norm(item.get("DOI") or "")
        if not doi:
            continue
        author_list = item.get("author") or []
        authors = []
        for author in author_list:
            if isinstance(author, dict):
                name = f"{_norm(author.get('given') or '')} {_norm(author.get('family') or '')}".strip()
                if name:
                    authors.append(name)
        title_list = item.get("title") or []
        title = title_list[0] if isinstance(title_list, list) and title_list else str(title_list)
        published_info = item.get("published-print") or item.get("published-online") or item.get("published") or {}
        pub_date = _parse_date_parts(published_info.get("date-parts") or [])
        abstract = re.sub(r"<[^>]+>", "", _norm(item.get("abstract") or ""))
        url = _norm(item.get("URL") or f"https://doi.org/{doi}")
        subjects = item.get("subject") or []
        categories = [_norm(s) for s in subjects if _norm(s)] or ["journal-article"]
        papers.append({
            "id": doi, "title": title, "abstract": abstract, "authors": authors,
            "published": pub_date, "updated_at": pub_date, "url": url, "pdf_url": "",
            "categories": categories, "source": SOURCE_KEY,
        })

    log(f"Wiley (CrossRef) 检索到 {len(papers)} 篇论文 (query={query!r})")
    return papers


def main() -> None:
    parser = argparse.ArgumentParser(description="Wiley Online Library 论文抓取")
    parser.add_argument("--query", default="monetary policy", help="搜索查询词")
    parser.add_argument("--days", type=int, default=7, help="抓取最近多少天的论文")
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
