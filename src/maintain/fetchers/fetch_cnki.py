#!/usr/bin/env python
"""CNKI 知网论文抓取器。

通过 CNKI 知网检索学术文献。\n知网没有公开 API，使用 HTTP 请求模拟搜索。
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
CRAWL_STATE_FILE = os.path.join(ROOT_DIR, "archive", "cnki_crawl_state.json")
SEEN_IDS_FILE = os.path.join(ROOT_DIR, "archive", "cnki_seen.json")

API_BASE = "https://kns.cnki.net/kns8s/brief/grid"
SOURCE_KEY = "cnki"


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


def fetch_papers(query: str, days: int = 7, max_results: int = 50) -> List[Dict[str, Any]]:
    """通过 CNKI 知网检索论文。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://kns.cnki.net/kns8s/search",
    }
    query_json = json.dumps({
        "Platform": "", "DBCode": "CFLS",
        "KuaKuCode": "CJFQ,CDMD,CIPD,CCND,CISD,SNAD,BDZK,CCJD,CCVD,CJFN",
        "QNode": {"QGroup": [{"Key": "Subject", "Title": "", "Logic": 0,
            "Items": [{"Title": "主题", "Name": "SU", "Value": query, "Operate": "%=", "BlurType": ""}],
            "ChildItems": []}]},
    }, ensure_ascii=False)
    payload = {
        "isPagination": "true", "QueryJson": query_json,
        "pageNum": 1, "pageSize": min(max_results, 50),
        "sortField": "PT", "sortType": "desc",
    }
    papers: List[Dict[str, Any]] = []
    data: dict = {}
    for attempt in range(3):
        try:
            resp = requests.post(API_BASE, headers=headers, data=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as exc:
            log(f"[WARN] CNKI 请求失败 (第{attempt + 1}次)：{exc}")
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
            else:
                return []
    records = data.get("Result") or data.get("records") or []
    if isinstance(records, dict):
        records = records.get("Records") or []
    for record in records:
        if not isinstance(record, dict):
            continue
        filename = _norm(record.get("FileName") or "")
        db_code = _norm(record.get("DbCode") or "")
        paper_id = f"cnki:{db_code}:{filename}" if filename else ""
        if not paper_id:
            continue
        authors_raw = _norm(record.get("Authors") or "")
        authors = [a.strip() for a in authors_raw.split(";") if a.strip()] if authors_raw else []
        pub_date = _norm(record.get("PublishDate") or "")
        title = re.sub(r"<[^>]+>", "", _norm(record.get("Title") or ""))
        abstract = re.sub(r"<[^>]+>", "", _norm(record.get("Abstract") or ""))
        url = f"https://kns.cnki.net/kcms2/article/abstract?v=&filename={filename}&dbcode={db_code}"
        papers.append({
            "id": paper_id, "title": title, "abstract": abstract, "authors": authors,
            "published": pub_date, "updated_at": pub_date, "url": url, "pdf_url": "",
            "categories": [_norm(record.get("ClassifyName") or "")], "source": SOURCE_KEY,
        })
    log(f"CNKI 检索到 {len(papers)} 篇论文 (query={query!r})")
    return papers


def main() -> None:
    parser = argparse.ArgumentParser(description="CNKI 知网论文抓取")
    parser.add_argument("--query", default="货币政策", help="搜索查询词")
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
