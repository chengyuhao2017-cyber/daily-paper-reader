#!/usr/bin/env python
"""
Elsevier ScienceDirect Search API fetcher.

Uses the ScienceDirect Search API v2 (https://api.elsevier.com/content/search/sciencedirect)
to search for recent economics papers matching the user's research keywords.

Requires: ELSEVIER_API_KEY environment variable (or --api-key argument).

Outputs JSON array in the same schema as fetch_arxiv.py / fetch_serp_scholar.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

try:
    from source_config import load_config_with_source_migration
except Exception:
    from src.source_config import load_config_with_source_migration

SCRIPT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
CONFIG_FILE = os.getenv("DPR_CONFIG_FILE") or os.path.join(ROOT_DIR, "config.yaml")
SEEN_IDS_FILE = os.path.join(ROOT_DIR, "archive", "sciencedirect_seen.json")
DATE_TOKEN_RE = re.compile(r"^\d{8}$")

SCIDIRECT_SEARCH_URL = "https://api.elsevier.com/content/search/sciencedirect"
DEFAULT_RESULTS_PER_QUERY = 25
DEFAULT_TIMEOUT = 30
DEFAULT_INTER_QUERY_SLEEP = 1.0   # Elsevier: 3 req/s for basic tier; 1s is safe
MAX_QUERIES_PER_RUN = 50


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
def log(message: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        print(f"[{ts}] {message}", flush=True)
    except BrokenPipeError:
        pass


def group_start(title: str) -> None:
    print(f"::group::{title}", flush=True)


def group_end() -> None:
    print("::endgroup::", flush=True)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _norm(value: Any) -> str:
    return str(value or "").strip()


def _make_paper_id(doi: str, title: str) -> str:
    if doi:
        clean = re.sub(r"[^a-zA-Z0-9/_.:@-]", "", doi)
        return f"doi:{clean}"
    fp = f"{title.lower().strip()}|sciencedirect"
    sha = hashlib.sha256(fp.encode("utf-8")).hexdigest()[:20]
    return f"scidir:{sha}"


# ---------------------------------------------------------------------------
# Seen-ID persistence
# ---------------------------------------------------------------------------
def load_seen_ids() -> set:
    if not os.path.exists(SEEN_IDS_FILE):
        return set()
    try:
        with open(SEEN_IDS_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
        ids = payload.get("ids") or []
        return set(ids) if isinstance(ids, list) else set()
    except Exception:
        return set()


def save_seen_ids(seen: set) -> None:
    os.makedirs(os.path.dirname(SEEN_IDS_FILE), exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "ids": sorted(seen),
    }
    with open(SEEN_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def load_config() -> Dict[str, Any]:
    try:
        return load_config_with_source_migration(CONFIG_FILE, write_back=False)
    except Exception as e:
        log(f"[WARN] Failed to load config.yaml: {e}")
        return {}


def extract_search_queries(config: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Extract search queries from config.yaml subscriptions.intent_profiles.
    Returns list of {"query": str, "tag": str, "profile": str} dicts.
    """
    subs = (config or {}).get("subscriptions") or {}
    profiles = subs.get("intent_profiles") or []
    queries: List[Dict[str, str]] = []

    for profile in profiles or []:
        if not isinstance(profile, dict):
            continue
        if not profile.get("enabled", True):
            continue
        tag = _norm(profile.get("tag") or profile.get("description") or "")

        for kw_entry in (profile.get("keywords") or []):
            if not isinstance(kw_entry, dict):
                kw_text = _norm(kw_entry)
            else:
                kw_text = _norm(kw_entry.get("keyword") or "")
            if kw_text:
                queries.append({
                    "query": kw_text,
                    "tag": tag,
                    "profile": tag,
                })

        for iq in (profile.get("intent_queries") or []):
            if not isinstance(iq, dict):
                continue
            if not iq.get("enabled", True):
                continue
            q = _norm(iq.get("query") or "")
            if q:
                queries.append({
                    "query": q,
                    "tag": tag,
                    "profile": tag,
                })

    seen_q: set = set()
    unique: List[Dict[str, str]] = []
    for item in queries:
        key = item["query"].lower()
        if key not in seen_q:
            seen_q.add(key)
            unique.append(item)
    return unique


# ---------------------------------------------------------------------------
# Elsevier ScienceDirect Search API
# ---------------------------------------------------------------------------
def _search_sciencedirect(
    query: str,
    api_key: str,
    count: int = DEFAULT_RESULTS_PER_QUERY,
    start: int = 0,
) -> Dict[str, Any]:
    """
    Call Elsevier ScienceDirect Search API and return parsed JSON response.
    """
    params: Dict[str, Any] = {
        "query": query,
        "count": count,
        "start": start,
        "view": "COMPLETE",
    }
    headers = {
        "X-ELS-APIKey": api_key,
        "Accept": "application/json",
    }

    try:
        resp = requests.get(
            SCIDIRECT_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.HTTPError as e:
        status = getattr(resp, "status_code", 0)
        if status == 401 or status == 403:
            raise RuntimeError(
                "Elsevier API authentication failed. Check your ELSEVIER_API_KEY."
            ) from e
        if status == 429:
            log("[WARN] Elsevier API rate-limited. Waiting 60s before retry...")
            time.sleep(60)
            return {}
        log(f"[WARN] Elsevier API HTTP {status}: {e}")
        return {}
    except Exception as e:
        log(f"[WARN] Elsevier API request failed: {e}")
        return {}

    return data


def _normalize_sciencedirect_entry(
    entry: Dict[str, Any],
    tag: str,
) -> Optional[Dict[str, Any]]:
    """
    Normalize a single ScienceDirect search result to the project's paper schema.
    """
    title = _norm(entry.get("dc:title") or "")
    if not title:
        return None

    doi = _norm(entry.get("prism:doi") or "")

    # Link: prefer scidir link, fall back to prism:url or DOI link
    links = entry.get("link") or []
    link = ""
    if isinstance(links, list):
        for l in links:
            if isinstance(l, dict) and l.get("@ref") in ("scidir", "all"):
                link = _norm(l.get("@href") or "")
                break
    if not link:
        link = _norm(entry.get("prism:url") or "")
    if not link and doi:
        link = f"https://doi.org/{doi}"

    # Abstract: dc:description or teaser text
    abstract = _norm(entry.get("dc:description") or "")
    if not abstract:
        teaser = entry.get("teaser-text")
        if isinstance(teaser, list) and teaser:
            abstract = _norm(teaser[0] if isinstance(teaser[0], str) else "")
        elif isinstance(teaser, str):
            abstract = _norm(teaser)

    # Authors
    authors: List[str] = []
    author_data = entry.get("authors") or entry.get("author") or []
    if isinstance(author_data, dict) and "author" in author_data:
        author_data = author_data["author"]
    if isinstance(author_data, list):
        for a in author_data:
            if isinstance(a, dict):
                name = _norm(a.get("$") or a.get("name") or "")
                if name:
                    authors.append(name)
            elif isinstance(a, str):
                name = _norm(a)
                if name:
                    authors.append(name)
    # Fallback to dc:creator
    if not authors:
        creator = _norm(entry.get("dc:creator") or "")
        if creator:
            authors = [a.strip() for a in re.split(r",\s*|;\s*", creator) if a.strip()]

    # Publication date
    published_str = ""
    cover_date = _norm(entry.get("prism:coverDate") or "")
    if cover_date:
        # Parse various date formats: "2023-01-15", "2023-01", "January 2023", etc.
        date_match = re.match(r"(\d{4})-(\d{2})-(\d{2})", cover_date)
        if date_match:
            published_str = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}T00:00:00+00:00"
        else:
            year_match = re.search(r"(\d{4})", cover_date)
            if year_match:
                published_str = f"{year_match.group(1)}-01-01T00:00:00+00:00"
    if not published_str:
        pub_year = _norm(entry.get("prism:coverDisplayDate") or entry.get("prism:coverDate") or "")
        year_match = re.search(r"(\d{4})", pub_year)
        if year_match:
            published_str = f"{year_match.group(1)}-01-01T00:00:00+00:00"

    # Journal / publication name
    journal = _norm(entry.get("prism:publicationName") or "")

    paper_id = _make_paper_id(doi, title)

    return {
        "id": paper_id,
        "source": "sciencedirect",
        "source_paper_id": doi or paper_id,
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "primary_category": journal or tag,
        "categories": [tag, journal] if journal else [tag],
        "published": published_str,
        "link": link,
    }


# ---------------------------------------------------------------------------
# Main fetch loop
# ---------------------------------------------------------------------------
def fetch_sciencedirect(
    api_key: str,
    queries: List[Dict[str, str]],
    seen_ids: set,
    ignore_seen: bool = False,
    num_per_query: int = DEFAULT_RESULTS_PER_QUERY,
    max_queries: int = MAX_QUERIES_PER_RUN,
) -> List[Dict[str, Any]]:
    papers: Dict[str, Dict[str, Any]] = {}
    total_queries = min(len(queries), max_queries)
    if len(queries) > max_queries:
        log(f"[WARN] {len(queries)} queries found, capping at {max_queries} to control API cost.")

    for i, q_info in enumerate(queries[:total_queries], start=1):
        query = q_info["query"]
        tag = q_info.get("tag", "")
        group_start(f"ScienceDirect query {i}/{total_queries}: {query[:60]}")
        log(f"🔍 [{i}/{total_queries}] tag={tag!r} | query={query!r}")

        raw_data = _search_sciencedirect(
            query=query,
            api_key=api_key,
            count=num_per_query,
        )

        entries = []
        search_results = raw_data.get("search-results") or {}
        if isinstance(search_results, dict):
            raw_entries = search_results.get("entry") or []
            if isinstance(raw_entries, dict):
                raw_entries = [raw_entries]
            entries = raw_entries if isinstance(raw_entries, list) else []

        total_results = search_results.get("opensearch:totalResults", "0")
        log(f"   Got {len(entries)} entries from ScienceDirect (total results: {total_results}).")
        new_in_query = 0

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            paper = _normalize_sciencedirect_entry(entry, tag)
            if not paper:
                continue
            paper_id = paper["id"]
            if not ignore_seen and paper_id in seen_ids:
                continue
            if paper_id in papers:
                continue
            papers[paper_id] = paper
            seen_ids.add(paper_id)
            new_in_query += 1

        log(f"   ✅ {new_in_query} new papers from this query.")
        group_end()

        if i < total_queries:
            time.sleep(DEFAULT_INTER_QUERY_SLEEP)

    log(f"[INFO] ScienceDirect fetch complete: {len(papers)} unique papers collected.")
    return list(papers.values())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search Elsevier ScienceDirect using config.yaml keywords."
    )
    parser.add_argument(
        "--api-key", type=str,
        default=os.getenv("ELSEVIER_API_KEY", ""),
        help="Elsevier API key (default: ELSEVIER_API_KEY env var).",
    )
    parser.add_argument(
        "--output", type=str, default="",
        help="Output JSON file path.",
    )
    parser.add_argument(
        "--num-per-query", type=int, default=DEFAULT_RESULTS_PER_QUERY,
        help=f"Number of results per query (default: {DEFAULT_RESULTS_PER_QUERY}).",
    )
    parser.add_argument(
        "--max-queries", type=int, default=MAX_QUERIES_PER_RUN,
        help=f"Maximum number of queries to run (default: {MAX_QUERIES_PER_RUN}).",
    )
    parser.add_argument(
        "--ignore-seen", action="store_true", default=False,
    )
    parser.add_argument(
        "--skip-config-queries", action="store_true", default=False,
        help="Do not read queries from config.yaml; use --query instead.",
    )
    parser.add_argument(
        "--query", type=str, default="",
        help="Single ad-hoc search query (overrides config-based queries).",
    )
    args = parser.parse_args()

    api_key = _norm(args.api_key)
    if not api_key:
        log("[ERROR] No Elsevier API key provided. Set ELSEVIER_API_KEY env var or use --api-key.")
        sys.exit(1)

    now_utc = datetime.now(timezone.utc)
    token = str(os.getenv("DPR_RUN_DATE") or "").strip()
    if not DATE_TOKEN_RE.match(token):
        token = now_utc.strftime("%Y%m%d")

    output_path = _norm(args.output)
    if not output_path:
        raw_dir = os.path.join(ROOT_DIR, "archive", token, "raw")
        output_path = os.path.join(raw_dir, f"sciencedirect_papers_{token}.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if args.query:
        queries = [{"query": args.query, "tag": "adhoc", "profile": "adhoc"}]
    elif not args.skip_config_queries:
        config = load_config()
        queries = extract_search_queries(config)
        if not queries:
            log("[WARN] No queries found in config.yaml subscriptions.intent_profiles.")
    else:
        log("[ERROR] No queries specified.")
        sys.exit(1)

    log(f"[INFO] Running {len(queries)} queries via Elsevier ScienceDirect Search API.")

    seen_ids = load_seen_ids() if not args.ignore_seen else set()

    papers = fetch_sciencedirect(
        api_key=api_key,
        queries=queries,
        seen_ids=seen_ids,
        ignore_seen=args.ignore_seen,
        num_per_query=args.num_per_query,
        max_queries=args.max_queries,
    )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)
    log(f"[INFO] Wrote {len(papers)} papers to: {output_path}")

    if papers and not args.ignore_seen:
        save_seen_ids(seen_ids)
        log("[INFO] Updated seen IDs.")


if __name__ == "__main__":
    main()
