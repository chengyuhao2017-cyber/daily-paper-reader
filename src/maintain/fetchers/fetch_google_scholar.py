#!/usr/bin/env python
"""Google Scholar 论文抓取器。

通过 SerpAPI 的 Google Scholar 接口检索学术文献。
需要设置环境变量 SERPAPI_KEY。
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
CRAWL_STATE_FILE = os.path.join(ROOT_DIR, "archive", "google_scholar_crawl_state.json")
SEEN_IDS_FILE = os.path.join(ROOT_DIR, "archive", "google_scholar_seen.json")

SERPAPI_URL = "https://serpapi.com/search.json"
SOURCE_KEY = "google_scholar"


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


def fetch_papers(query: str, days: int = 7, max_results: int = 20) -> List[Dict[str, Any]]:
    """通过 SerpAPI Google Scholar 接口检索论文。"""
    api_key = os.getenv("SERPAPI_KEY", "")
    if not api_key:
        log("[WARN] 未设置 SERPAPI_KEY，无法检索 Google Scholar。")
        return []

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    params = {
        "engine": "google_scholar", "q": query, "api_key": api_key,
        "num": min(max_results, 20), "as_ylo": start_date.year, "as_yhi": end_date.year, "hl": "en",
    }

    papers: List[Dict[str, Any]] = []
    data: dict = {}
    for attempt in range(3):
        try:
            resp = requests.get(SERPAPI_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as exc:
            log(f"[WARN] Google Scholar API 请求失败 (第{attempt + 1}次)：{exc}")
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
            else:
                return []

    organic_results = data.get("organic_results") or []
    for item in organic_results:
        if not isinstance(item, dict):
            continue
        result_id = _norm(item.get("result_id") or "")
        if not result_id:
            continue
        pub_info = item.get("publication_info") or {}
        authors_raw = pub_info.get("authors") or []
        if isinstance(authors_raw, list):
            authors = [_norm(a.get("name") or "") for a in authors_raw if isinstance(a, dict)]
        else:
            summary = _norm(pub_info.get("summary") or "")
            authors = [summary.split("-")[0].strip()] if summary else []
        year = ""
        summary = _norm(pub_info.get("summary") or "")
        year_match = re.search(r"\b(19|20)\d{2}\b", summary)
        if year_match:
            year = year_match.group(0)
        url = _norm(item.get("link") or "")
        resources = item.get("resources") or []
        pdf_url = _norm(resources[0].get("link") or "") if resources and isinstance(resources[0], dict) else ""
        papers.append({
            "id": f"gs:{result_id}", "title": _norm(item.get("title") or ""),
            "abstract": _norm(item.get("snippet") or ""), "authors": authors,
            "published": year, "updated_at": year, "url": url, "pdf_url": pdf_url,
            "categories": ["economics"], "source": SOURCE_KEY,
        })

    log(f"Google Scholar 检索到 {len(papers)} 篇论文 (query={query!r})")
    return papers


def main() -> None:
    parser = argparse.ArgumentParser(description="Google Scholar 论文抓取")
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
