import unittest
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.source_config import (
    ARXIV_SOURCE_KEY,
    get_source_backend,
    get_supabase_shared_config,
    migrate_source_config_inplace,
    resolve_source_backends,
)


class SourceConfigMigrationTest(unittest.TestCase):
    def test_migrate_fills_missing_paper_sources_and_source_backends(self):
        cfg = {
            "supabase": {
                "enabled": True,
                "url": "https://example.supabase.co",
                "anon_key": "anon",
                "papers_table": "arxiv_papers",
                "use_bm25_rpc": True,
                "use_vector_rpc": True,
            },
            "subscriptions": {
                "intent_profiles": [
                    {
                        "tag": "AHD",
                        "enabled": True,
                        "keywords": [{"keyword": "test", "query": "test"}],
                    }
                ]
            },
        }
        changed, notes = migrate_source_config_inplace(cfg)
        self.assertTrue(changed)
        self.assertTrue(notes)
        self.assertEqual(cfg["subscriptions"]["intent_profiles"][0]["paper_sources"], [ARXIV_SOURCE_KEY])
        self.assertIn("source_backends", cfg)
        self.assertIn(ARXIV_SOURCE_KEY, cfg["source_backends"])

    def test_migrate_rejects_empty_paper_sources(self):
        cfg = {
            "subscriptions": {
                "intent_profiles": [
                    {
                        "tag": "BAD",
                        "enabled": True,
                        "paper_sources": [],
                        "keywords": [{"keyword": "test", "query": "test"}],
                    }
                ]
            }
        }
        with self.assertRaises(ValueError):
            migrate_source_config_inplace(cfg)

    def test_resolve_source_backends_prefers_new_shape(self):
        cfg = {
            "supabase_shared": {
                "enabled": True,
                "url": "https://shared.supabase.co",
                "anon_key": "shared-key",
                "schema": "public",
            },
            "source_backends": {
                "arxiv": {
                    "url": "https://new.supabase.co",
                    "papers_table": "papers",
                }
            },
            "supabase": {
                "enabled": True,
                "url": "https://legacy.supabase.co",
                "anon_key": "legacy-key",
            },
        }
        backends = resolve_source_backends(cfg)
        self.assertEqual(backends["arxiv"]["url"], "https://new.supabase.co")
        self.assertEqual(get_source_backend(cfg, "arxiv")["anon_key"], "shared-key")

    def test_resolve_source_backends_merges_supabase_shared(self):
        cfg = {
            "supabase_shared": {
                "enabled": True,
                "url": "https://shared.supabase.co",
                "anon_key": "shared-key",
                "schema": "public",
            },
            "source_backends": {
                "scopus": {
                    "enabled": False,
                    "papers_table": "scopus_papers",
                    "vector_rpc_exact": "match_scopus_papers_exact",
                    "bm25_rpc": "match_scopus_papers_bm25",
                }
            },
        }
        shared = get_supabase_shared_config(cfg)
        self.assertEqual(shared["url"], "https://shared.supabase.co")
        backend = get_source_backend(cfg, "scopus")
        self.assertEqual(backend["url"], "https://shared.supabase.co")
        self.assertEqual(backend["anon_key"], "shared-key")
        self.assertEqual(backend["papers_table"], "scopus_papers")
        self.assertFalse(backend["enabled"])

    def test_resolve_source_backends_supports_env_sciencedirect_backend(self):
        cfg = {
            "supabase_shared": {
                "url": "https://shared.supabase.co",
                "anon_key": "shared-key",
                "schema": "public",
            }
        }
        with patch.dict(
            "os.environ",
            {
                "DPR_ENABLE_SCIENCEDIRECT_BACKEND": "1",
                "DPR_SCIENCEDIRECT_ENABLED": "1",
                "DPR_SCIENCEDIRECT_PAPERS_TABLE": "sciencedirect_papers",
                "DPR_SCIENCEDIRECT_VECTOR_RPC_EXACT": "match_sciencedirect_papers_exact",
                "DPR_SCIENCEDIRECT_BM25_RPC": "match_sciencedirect_papers_bm25",
            },
            clear=False,
        ):
            backend = get_source_backend(cfg, "sciencedirect")
        self.assertEqual(backend["url"], "https://shared.supabase.co")
        self.assertEqual(backend["papers_table"], "sciencedirect_papers")
        self.assertEqual(backend["vector_rpc_exact"], "match_sciencedirect_papers_exact")

    def test_resolve_source_backends_supports_env_cnki_backend(self):
        cfg = {
            "supabase_shared": {
                "url": "https://shared.supabase.co",
                "anon_key": "shared-key",
                "schema": "public",
            }
        }
        with patch.dict(
            "os.environ",
            {
                "DPR_ENABLE_CNKI_BACKEND": "1",
                "DPR_CNKI_ENABLED": "1",
                "DPR_CNKI_PAPERS_TABLE": "cnki_papers",
                "DPR_CNKI_VECTOR_RPC_EXACT": "match_cnki_papers_exact",
                "DPR_CNKI_BM25_RPC": "match_cnki_papers_bm25",
            },
            clear=False,
        ):
            backend = get_source_backend(cfg, "cnki")
        self.assertEqual(backend["url"], "https://shared.supabase.co")
        self.assertEqual(backend["papers_table"], "cnki_papers")
        self.assertEqual(backend["vector_rpc_exact"], "match_cnki_papers_exact")

    def test_resolve_source_backends_supports_env_wos_backend(self):
        cfg = {
            "supabase_shared": {
                "url": "https://shared.supabase.co",
                "anon_key": "shared-key",
                "schema": "public",
            }
        }
        with patch.dict(
            "os.environ",
            {
                "DPR_ENABLE_WOS_BACKEND": "1",
                "DPR_WOS_ENABLED": "1",
                "DPR_WOS_PAPERS_TABLE": "wos_papers",
                "DPR_WOS_VECTOR_RPC_EXACT": "match_wos_papers_exact",
                "DPR_WOS_BM25_RPC": "match_wos_papers_bm25",
            },
            clear=False,
        ):
            backend = get_source_backend(cfg, "wos")
        self.assertEqual(backend["url"], "https://shared.supabase.co")
        self.assertEqual(backend["papers_table"], "wos_papers")
        self.assertEqual(backend["vector_rpc_exact"], "match_wos_papers_exact")

    def test_resolve_source_backends_supports_env_jstor_backend(self):
        cfg = {
            "supabase_shared": {
                "url": "https://shared.supabase.co",
                "anon_key": "shared-key",
                "schema": "public",
            }
        }
        with patch.dict(
            "os.environ",
            {
                "DPR_ENABLE_JSTOR_BACKEND": "1",
                "DPR_JSTOR_ENABLED": "1",
                "DPR_JSTOR_PAPERS_TABLE": "jstor_papers",
                "DPR_JSTOR_VECTOR_RPC_EXACT": "match_jstor_papers_exact",
                "DPR_JSTOR_BM25_RPC": "match_jstor_papers_bm25",
            },
            clear=False,
        ):
            backend = get_source_backend(cfg, "jstor")
        self.assertEqual(backend["url"], "https://shared.supabase.co")
        self.assertEqual(backend["papers_table"], "jstor_papers")
        self.assertEqual(backend["vector_rpc_exact"], "match_jstor_papers_exact")

    def test_resolve_source_backends_supports_env_scopus_backend(self):
        cfg = {
            "supabase_shared": {
                "url": "https://shared.supabase.co",
                "anon_key": "shared-key",
                "schema": "public",
            }
        }
        with patch.dict(
            "os.environ",
            {
                "DPR_ENABLE_SCOPUS_BACKEND": "1",
                "DPR_SCOPUS_ENABLED": "1",
                "DPR_SCOPUS_PAPERS_TABLE": "scopus_papers",
                "DPR_SCOPUS_VECTOR_RPC_EXACT": "match_scopus_papers_exact",
                "DPR_SCOPUS_BM25_RPC": "match_scopus_papers_bm25",
            },
            clear=False,
        ):
            backend = get_source_backend(cfg, "scopus")
        self.assertEqual(backend["url"], "https://shared.supabase.co")
        self.assertEqual(backend["papers_table"], "scopus_papers")
        self.assertEqual(backend["vector_rpc_exact"], "match_scopus_papers_exact")

    def test_resolve_source_backends_supports_env_google_scholar_backend(self):
        cfg = {
            "supabase_shared": {
                "url": "https://shared.supabase.co",
                "anon_key": "shared-key",
                "schema": "public",
            }
        }
        with patch.dict(
            "os.environ",
            {
                "DPR_ENABLE_GOOGLE_SCHOLAR_BACKEND": "1",
                "DPR_GOOGLE_SCHOLAR_ENABLED": "1",
                "DPR_GOOGLE_SCHOLAR_PAPERS_TABLE": "google_scholar_papers",
                "DPR_GOOGLE_SCHOLAR_VECTOR_RPC_EXACT": "match_google_scholar_papers_exact",
                "DPR_GOOGLE_SCHOLAR_BM25_RPC": "match_google_scholar_papers_bm25",
            },
            clear=False,
        ):
            backend = get_source_backend(cfg, "google_scholar")
        self.assertEqual(backend["url"], "https://shared.supabase.co")
        self.assertEqual(backend["papers_table"], "google_scholar_papers")
        self.assertEqual(backend["vector_rpc_exact"], "match_google_scholar_papers_exact")

    def test_resolve_source_backends_supports_env_repec_backend(self):
        cfg = {
            "supabase_shared": {
                "url": "https://shared.supabase.co",
                "anon_key": "shared-key",
                "schema": "public",
            }
        }
        with patch.dict(
            "os.environ",
            {
                "DPR_ENABLE_REPEC_BACKEND": "1",
                "DPR_REPEC_ENABLED": "1",
                "DPR_REPEC_PAPERS_TABLE": "repec_papers",
                "DPR_REPEC_VECTOR_RPC_EXACT": "match_repec_papers_exact",
                "DPR_REPEC_BM25_RPC": "match_repec_papers_bm25",
            },
            clear=False,
        ):
            backend = get_source_backend(cfg, "repec")
        self.assertEqual(backend["url"], "https://shared.supabase.co")
        self.assertEqual(backend["papers_table"], "repec_papers")
        self.assertEqual(backend["vector_rpc_exact"], "match_repec_papers_exact")

    def test_resolve_source_backends_supports_env_wiley_backend(self):
        cfg = {
            "supabase_shared": {
                "url": "https://shared.supabase.co",
                "anon_key": "shared-key",
                "schema": "public",
            }
        }
        with patch.dict(
            "os.environ",
            {
                "DPR_ENABLE_WILEY_BACKEND": "1",
                "DPR_WILEY_ENABLED": "1",
                "DPR_WILEY_PAPERS_TABLE": "wiley_papers",
                "DPR_WILEY_VECTOR_RPC_EXACT": "match_wiley_papers_exact",
                "DPR_WILEY_BM25_RPC": "match_wiley_papers_bm25",
            },
            clear=False,
        ):
            backend = get_source_backend(cfg, "wiley")
        self.assertEqual(backend["url"], "https://shared.supabase.co")
        self.assertEqual(backend["papers_table"], "wiley_papers")
        self.assertEqual(backend["vector_rpc_exact"], "match_wiley_papers_exact")


if __name__ == "__main__":
    unittest.main()
