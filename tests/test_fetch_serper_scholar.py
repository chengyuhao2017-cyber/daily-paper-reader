import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src" / "maintain" / "fetchers"))

from src.maintain.fetchers.fetch_serper_scholar import (
    SUPPORTED_SOURCES,
    SOURCE_SITE_MAP,
    build_paper_id,
    build_search_query,
    fetch_papers_for_query,
    save_results,
    search_serper_scholar,
)


class TestBuildSearchQuery(unittest.TestCase):
    def test_google_scholar_no_site_filter(self):
        q = build_search_query("monetary policy", "google_scholar")
        self.assertEqual(q, "monetary policy")

    def test_sciencedirect_site_filter(self):
        q = build_search_query("tail risk", "sciencedirect")
        self.assertIn("site:sciencedirect.com", q)
        self.assertIn("tail risk", q)

    def test_cnki_site_filter(self):
        q = build_search_query("生产网络", "cnki")
        self.assertIn("site:cnki.net", q)

    def test_wos_site_filter(self):
        q = build_search_query("policy effect", "wos")
        self.assertIn("site:webofscience.com", q)

    def test_jstor_site_filter(self):
        q = build_search_query("production networks", "jstor")
        self.assertIn("site:jstor.org", q)

    def test_scopus_site_filter(self):
        q = build_search_query("monetary", "scopus")
        self.assertIn("site:scopus.com", q)

    def test_repec_site_filter(self):
        q = build_search_query("fiscal policy", "repec")
        self.assertIn("site:repec.org", q)

    def test_wiley_site_filter(self):
        q = build_search_query("causal inference", "wiley")
        self.assertIn("site:onlinelibrary.wiley.com", q)

    def test_unknown_source_no_filter(self):
        q = build_search_query("test query", "unknown_source")
        self.assertEqual(q, "test query")


class TestBuildPaperId(unittest.TestCase):
    def test_basic_paper_id(self):
        pid = build_paper_id("scopus", "A Great Paper", "https://example.com/paper/123")
        self.assertTrue(pid.startswith("scopus-"))
        self.assertIn("a-great-paper", pid)

    def test_paper_id_deterministic(self):
        pid1 = build_paper_id("wos", "Title A", "https://x.com/1")
        pid2 = build_paper_id("wos", "Title A", "https://x.com/1")
        self.assertEqual(pid1, pid2)

    def test_different_links_different_ids(self):
        pid1 = build_paper_id("jstor", "Title A", "https://jstor.org/1")
        pid2 = build_paper_id("jstor", "Title A", "https://jstor.org/2")
        self.assertNotEqual(pid1, pid2)


class TestSearchSerperScholar(unittest.TestCase):
    @patch("src.maintain.fetchers.fetch_serper_scholar.requests.post")
    def test_successful_search(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "organic": [
                {
                    "title": "Monetary Policy and Inflation",
                    "link": "https://sciencedirect.com/paper/123",
                    "snippet": "This paper studies monetary policy...",
                    "publicationInfo": {
                        "authors": "Smith, J.",
                        "summary": "Journal of Economics, 2024",
                    },
                    "year": "2024",
                    "citationInfo": {"citedBy": 42},
                },
                {
                    "title": "Interest Rate Effects",
                    "link": "https://sciencedirect.com/paper/456",
                    "snippet": "We analyze interest rate...",
                    "publicationInfo": {},
                    "year": "2023",
                },
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        papers = search_serper_scholar(
            "monetary policy",
            api_key="test-key",
            source="sciencedirect",
            num=10,
        )

        self.assertEqual(len(papers), 2)
        self.assertEqual(papers[0]["title"], "Monetary Policy and Inflation")
        self.assertEqual(papers[0]["source"], "sciencedirect")
        self.assertEqual(papers[0]["year"], "2024")
        self.assertEqual(papers[0]["cited_by"], 42)
        self.assertIn("sciencedirect.com", mock_post.call_args[1]["json"]["q"])

    @patch("src.maintain.fetchers.fetch_serper_scholar.requests.post")
    def test_empty_response(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"organic": []}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        papers = search_serper_scholar(
            "obscure query",
            api_key="test-key",
            source="google_scholar",
        )
        self.assertEqual(papers, [])

    @patch("src.maintain.fetchers.fetch_serper_scholar.requests.post")
    def test_request_failure_returns_empty(self, mock_post):
        mock_post.side_effect = __import__("requests").RequestException("Network error")

        papers = search_serper_scholar(
            "test",
            api_key="test-key",
            source="scopus",
        )
        self.assertEqual(papers, [])

    @patch("src.maintain.fetchers.fetch_serper_scholar.requests.post")
    def test_skips_items_without_title_or_link(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "organic": [
                {"title": "", "link": "https://example.com"},
                {"title": "Valid Title", "link": ""},
                {"title": "Good Paper", "link": "https://example.com/good"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        papers = search_serper_scholar(
            "test",
            api_key="test-key",
            source="google_scholar",
        )
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["title"], "Good Paper")


class TestFetchPapersForQuery(unittest.TestCase):
    @patch("src.maintain.fetchers.fetch_serper_scholar.search_serper_scholar")
    def test_fetches_from_multiple_sources(self, mock_search):
        mock_search.return_value = [
            {
                "id": "test-id",
                "source": "google_scholar",
                "title": "Test",
                "url": "https://example.com",
            }
        ]

        papers = fetch_papers_for_query(
            "monetary policy",
            api_key="test-key",
            sources=["google_scholar", "scopus"],
            num_per_source=5,
        )
        self.assertEqual(mock_search.call_count, 2)

    @patch("src.maintain.fetchers.fetch_serper_scholar.search_serper_scholar")
    def test_deduplicates_papers(self, mock_search):
        mock_search.return_value = [
            {
                "id": "same-id",
                "source": "google_scholar",
                "title": "Test",
                "url": "https://example.com",
            }
        ]

        papers = fetch_papers_for_query(
            "test",
            api_key="test-key",
            sources=["google_scholar", "scopus"],
            num_per_source=5,
        )
        self.assertEqual(len(papers), 1)

    @patch("src.maintain.fetchers.fetch_serper_scholar.search_serper_scholar")
    def test_skips_unknown_source(self, mock_search):
        mock_search.return_value = []

        papers = fetch_papers_for_query(
            "test",
            api_key="test-key",
            sources=["unknown_source"],
            num_per_source=5,
        )
        mock_search.assert_not_called()


class TestSaveResults(unittest.TestCase):
    def test_save_results_creates_file(self):
        import tempfile

        papers = [
            {"id": "test-1", "title": "Paper 1", "source": "scopus"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, "results.json")
            save_results(papers, output_path)

            self.assertTrue(os.path.exists(output_path))
            with open(output_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["total"], 1)
            self.assertEqual(len(data["papers"]), 1)
            self.assertEqual(data["papers"][0]["title"], "Paper 1")


class TestSupportedSources(unittest.TestCase):
    def test_all_required_sources_present(self):
        required = [
            "sciencedirect", "cnki", "wos", "jstor",
            "scopus", "google_scholar", "repec", "wiley",
        ]
        for src in required:
            self.assertIn(src, SUPPORTED_SOURCES)
            self.assertIn(src, SOURCE_SITE_MAP)


if __name__ == "__main__":
    unittest.main()
