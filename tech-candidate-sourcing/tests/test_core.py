from __future__ import annotations

import csv
import importlib
import ipaddress
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import md_table
from candidate_schema import STANDARD_HEADERS

IMPORT_ERROR = None
try:
    import china_signals
    import draft_outreach
    import enrich_candidate_research
    import export_candidates_csv
    import filter_candidate_table
    import generate_paper_author_queries
    import import_platform_candidates
    import merge_candidate_tables
    import run_sourcing_pipeline
    import safe_http
    import send_outreach
    import shortlist_candidates
    import source_candidates
    import source_publication_emails
    import validate_candidate_table
    import validate_outreach_queue
except ModuleNotFoundError as exc:
    IMPORT_ERROR = exc


def candidate(email: str, name: str = "Test Person", confidence: str = "medium", extra_email: str = "") -> dict[str, str]:
    row = {header: "evidence" for header in STANDARD_HEADERS}
    row.update(
        {
            "姓名": name,
            "基础信息": "Public university profile",
            "电话": "暂无",
            "邮箱": "; ".join(value for value in [email, extra_email] if value),
            "渠道": "academic_web",
            "线索类型": "academic_profile",
            "置信度": confidence,
            "推荐级别": "可聊",
            "匹配分": "75/100",
            "来源": "https://example.edu/profile",
            "命中关键词": "database",
            "推荐点": "public evidence",
            "风险点/待确认": "manual review",
            "建议动作": "review",
            "研究方向": "database systems",
            "代表作/项目证据": "public paper",
            "中国相关公开信号": "未发现明确的中国高校/机构/中文公开来源信号",
        }
    )
    return row


def write_candidate_table(path: Path, rows: list[dict[str, str]]) -> None:
    path.write_text(md_table.render_rows(STANDARD_HEADERS, rows), encoding="utf-8")


@unittest.skipIf(IMPORT_ERROR is not None, f"optional runtime dependencies unavailable: {IMPORT_ERROR}")
class CoreTests(unittest.TestCase):
    def test_markdown_parser_unescapes_pipe(self) -> None:
        text = "| 姓名 | 推荐点 |\n| --- | --- |\n| A | x\\|y |\n"
        headers, rows = md_table.parse_markdown(text)
        self.assertEqual(headers, ["姓名", "推荐点"])
        self.assertEqual(rows[0]["推荐点"], "x|y")

    def test_markdown_parser_accepts_compact_separator_and_fails_loud(self) -> None:
        headers, rows = md_table.parse_markdown("|姓名|邮箱|\n|---|---|\n|A|a@example.edu|\n")
        self.assertEqual(headers, ["姓名", "邮箱"])
        self.assertEqual(rows[0]["姓名"], "A")
        with self.assertRaisesRegex(ValueError, "Malformed Markdown table row"):
            md_table.parse_markdown("| A | B |\n|---|---|\n| only-one |\n")

    def test_platform_pipe_roundtrip_does_not_drop_candidate(self) -> None:
        rows = import_platform_candidates.normalize_rows(
            [{"name": "Zhang | San", "email": "zhang@example.edu", "company": "Example"}],
            "other",
            "authorized export",
        )
        rendered = import_platform_candidates.render_table(rows, Path("input.csv"), "other")
        _headers, parsed = md_table.parse_markdown(rendered)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["姓名"], "Zhang | San")

    def test_empty_allowlist_means_draft_nobody(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "allow.txt"
            path.write_text("", encoding="utf-8")
            allowed = draft_outreach.load_allowlist(path)
            self.assertEqual(allowed, [])
            self.assertEqual(draft_outreach.filter_by_allowlist([candidate("a@example.edu")], allowed), [])

    def test_auto_language_uses_public_candidate_signal(self) -> None:
        china = candidate("person@tsinghua.edu.cn")
        china["中国相关公开信号"] = "清华大学公开关联"
        international = candidate("person@princeton.edu")
        self.assertEqual(draft_outreach.candidate_language(china, "auto", "Database Engineer"), "zh")
        self.assertEqual(draft_outreach.candidate_language(international, "auto", "Database Engineer"), "en")
        cnrs = candidate("bob@cnrs.fr")
        japanese = candidate("person@example.jp", name="山田太郎")
        self.assertEqual(draft_outreach.candidate_language(cnrs, "auto", "Database Engineer"), "en")
        self.assertEqual(draft_outreach.candidate_language(japanese, "auto", "Database Engineer"), "en")

    def test_english_draft_never_leaks_chinese_placeholders(self) -> None:
        row = candidate("person@example.edu", name="你好")
        row["研究方向"] = "需进一步确认"
        row["代表作/项目证据"] = "论文主题需复核"
        _subject, body = draft_outreach.draft_email(row, "Database Engineer", "Recruiter", "Database systems team", "en")
        self.assertIn("Hi there", body)
        self.assertNotRegex(body, r"需进一步确认|需复核|你好")

    def test_merge_uses_any_overlapping_email_and_sorts_high_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first.md"
            second = Path(temp) / "second.md"
            third = Path(temp) / "third.md"
            write_candidate_table(first, [candidate("a@one.edu", "Same Person", "low", "b@two.edu")])
            write_candidate_table(second, [candidate("b@two.edu", "Same Person", "medium", "a@one.edu")])
            write_candidate_table(third, [candidate("high@three.edu", "High Person", "high")])
            rows = merge_candidate_tables.merge_tables([first, second, third])
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["置信度"], "high")
            merged = next(row for row in rows if row["姓名"] == "Same Person")
            self.assertIn("a@one.edu", merged["邮箱"])
            self.assertIn("b@two.edu", merged["邮箱"])

    def test_in_memory_merge_uses_email_set_overlap(self) -> None:
        first = source_candidates.Candidate(name="Same", emails={"a@one.edu", "b@two.edu"})
        second = source_candidates.Candidate(name="Same", emails={"b@two.edu", "c@three.edu"})
        merged = source_candidates.merge_candidates([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].emails, {"a@one.edu", "b@two.edu", "c@three.edu"})

    def test_in_memory_merge_keeps_name_groups_after_email_union(self) -> None:
        first = source_candidates.Candidate(name="Same", emails={"a@one.edu"})
        second = source_candidates.Candidate(name="Same", emails={"a@one.edu", "b@two.edu"})
        third = source_candidates.Candidate(name="Same", emails=set(), source_urls={"https://example.edu/same"})
        merged = source_candidates.merge_candidates([first, second, third])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].emails, {"a@one.edu", "b@two.edu"})
        self.assertIn("https://example.edu/same", merged[0].source_urls)

    def test_bad_email_is_needs_review_not_global_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "table.md"
            write_candidate_table(path, [candidate("noreply@valid.edu")])
            result = validate_candidate_table.validate(path)
            self.assertEqual(result["status"], "needs_review")

    def test_malformed_candidate_table_reports_fail_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.md"
            path.write_text("| name | email |\n| --- | --- |\n| only-one |\n", encoding="utf-8")
            result = validate_candidate_table.validate(path)
            self.assertEqual(result["status"], "fail")
            self.assertIn("Malformed Markdown table row", result["parse_error"])

    def test_china_hard_filter_requires_public_signal(self) -> None:
        no_signal = candidate("person@example.edu")
        china = candidate("person@tsinghua.edu.cn")
        china["中国相关公开信号"] = "中国高校/科研机构域名"
        self.assertFalse(filter_candidate_table.has_china_public_signal(no_signal))
        self.assertTrue(filter_candidate_table.has_china_public_signal(china))
        self.assertFalse(china_signals.has_china_public_signal("case studies in systems"))
        self.assertFalse(china_signals.has_china_public_signal("pkumar@iitb.ac.in"))
        self.assertFalse(china_signals.has_china_public_signal("bob@cnrs.fr"))
        self.assertFalse(china_signals.has_china_public_signal("CAS compare-and-swap for lock-free systems"))
        self.assertFalse(china_signals.has_china_public_signal("Toronto (CN) public profile"))
        self.assertTrue(china_signals.has_china_public_signal("person@company.cn"))

    def test_broad_preset_raises_timeout(self) -> None:
        namespace = type("Args", (), {})()
        namespace.coverage_preset = "broad"
        for name, value in {
            "depth": "quick", "search_limit": 1, "max_urls": 1, "github_user_limit": 1,
            "github_contributor_limit": 1, "github_commit_limit": 1, "academic_link_limit": 1,
            "paper_max_papers": 1, "paper_max_conference_papers": 1, "dblp_author_profile_limit": 1,
            "publication_pdf_limit": 1, "paper_max_authors": 1, "paper_seed_limit": 1,
            "paper_seeds_per_query": 1, "paper_max_openalex_papers": 1, "publication_pdf_workers": 1,
            "publication_pdf_max_pages": 1, "step_timeout": 240,
        }.items():
            setattr(namespace, name, value)
        run_sourcing_pipeline.apply_coverage_preset(namespace)
        self.assertGreaterEqual(namespace.step_timeout, 3600)

    def test_dblp_query_comes_from_jd_keywords(self) -> None:
        database_url = generate_paper_author_queries.dblp_query_urls(["database kernel", "storage engine"], 20)[0]
        frontend_url = generate_paper_author_queries.dblp_query_urls(["frontend", "typescript"], 20)[0]
        self.assertNotEqual(database_url, frontend_url)
        self.assertIn("database", database_url)
        self.assertNotIn("language+model+agent", database_url)

    def test_keyword_extraction_is_term_based_and_english_for_papers(self) -> None:
        from jd_utils import jd_keywords

        chinese = jd_keywords("负责事务系统与分布式一致性研发，负责存储引擎，熟悉 RocksDB、TiKV、LSM-tree", 10)
        english = jd_keywords("Database Kernel Engineer. Build a storage engine with RocksDB and distributed consensus.", 10)
        self.assertIn("transaction processing", chinese)
        self.assertIn("distributed consensus", chinese)
        self.assertIn("storage engine", chinese)
        self.assertFalse(any("负责" in term or "研发" in term for term in chinese))
        self.assertFalse(any("engineer." in term or "build" in term for term in english))

    def test_generate_queries_runs_without_site_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            jd = Path(temp) / "jd.md"
            out = Path(temp) / "queries.md"
            jd.write_text("Database Kernel Engineer\nStorage engine and distributed systems", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-S", str(SCRIPTS / "generate_channel_queries.py"), "--jd", str(jd), "--out", str(out)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(out.exists())

    def test_gbk_page_decoding(self) -> None:
        response = importlib.import_module("requests").Response()
        response.status_code = 200
        response._content = "清华大学 张三".encode("gb18030")
        response.headers = {}
        with mock.patch.object(source_candidates, "safe_get_public", return_value=response):
            text = source_candidates.fetch("https://example.edu.cn")
        self.assertIn("清华大学", text)

    def test_email_deobfuscation_does_not_invent_addresses(self) -> None:
        self.assertNotIn("student@stanford.edu", source_candidates.extract_emails("PhD student at stanford dot edu"))
        self.assertNotIn("available@github.com", source_candidates.extract_emails("available at github dot com"))
        self.assertIn(
            "john@cs.stanford.edu",
            source_candidates.extract_emails("john [at] cs [dot] stanford [dot] edu"),
        )
        self.assertEqual(source_candidates.extract_emails("中文foo@bar.com"), {"foo@bar.com"})

    def test_github_rate_limit_is_diagnostic_and_not_negative_cached(self) -> None:
        response = mock.Mock(status_code=403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "123"})
        source_candidates.SEARCH_NOTES.clear()
        with mock.patch.object(source_candidates.requests, "get", return_value=response), mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(source_candidates.GitHubRateLimitError):
                source_candidates.github_api_json("https://api.github.com/users/test")
        self.assertTrue(any("GITHUB_TOKEN" in note for note in source_candidates.SEARCH_NOTES))
        cache: dict[str, dict] = {}
        with mock.patch.object(source_candidates, "github_api_json", side_effect=source_candidates.GitHubRateLimitError("limited")):
            self.assertEqual(source_candidates.fetch_github_user("test", cache), {})
        self.assertNotIn("test", cache)

    def test_quick_mode_seed_does_not_suppress_web_search(self) -> None:
        source_candidates.ACTIVE_DEADLINE = 0.0
        with mock.patch.object(source_candidates, "search_github_repositories", return_value=[]), mock.patch.object(
            source_candidates, "search_web", return_value=["https://cs.example.edu/people/a"]
        ):
            urls = source_candidates.discover_urls(
                "Database engineer", 1, ["https://seed.example.edu/person"], [], False, 10, "quick", True
            )
        self.assertIn("https://cs.example.edu/people/a", urls)

    def test_academic_host_requires_real_suffix(self) -> None:
        self.assertTrue(source_candidates.is_academic_host("cs.stanford.edu"))
        self.assertTrue(source_candidates.is_academic_host("unsw.edu.au"))
        self.assertTrue(source_candidates.is_academic_host("nus.edu.sg"))
        self.assertTrue(source_candidates.is_academic_host("example.edu.hk"))
        self.assertTrue(source_candidates.is_academic_host("example.edu.tw"))
        self.assertTrue(source_candidates.is_academic_host("iitb.ac.in"))
        self.assertFalse(source_candidates.is_academic_host("evil.edu.attacker.com"))

    def test_send_gate_rejects_needs_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = Path(temp) / "queue.csv"
            log = Path(temp) / "log.csv"
            with queue.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["status", "to", "candidate_name", "subject", "body"])
                writer.writeheader()
                writer.writerow({"status": "needs_review", "to": "person@example.edu", "candidate_name": "P", "subject": "S", "body": "B"})
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "send_outreach.py"), "--queue", str(queue), "--log", str(log), "--send", "--status", "needs_review"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("requires --status approved", completed.stderr)

    def test_missing_suppression_list_fails_loud(self) -> None:
        with self.assertRaises(FileNotFoundError):
            send_outreach.load_suppression_lists(["does-not-exist.txt"])
        with self.assertRaises(FileNotFoundError):
            validate_outreach_queue.load_suppression_lists(["does-not-exist.txt"])

    def test_queue_validator_blocks_placeholders_and_injected_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = Path(temp) / "queue.csv"
            fields = [
                "status", "to", "candidate_name", "subject", "body", "channel", "confidence", "source",
                "matched_keywords", "risk_to_confirm", "personalization_note", "review_note",
            ]
            with queue.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow({
                    "status": "needs_review", "to": "person@valid.edu", "candidate_name": "P",
                    "subject": "Role at &lt;COMPANY_HINT&gt;", "body": "Click here https://bad.example",
                    "channel": "academic_web", "confidence": "medium", "source": "public profile",
                    "matched_keywords": "database", "risk_to_confirm": "review", "personalization_note": "public",
                    "review_note": "review",
                })
            result = validate_outreach_queue.validate(queue, "approved")
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["placeholder_rows"], [1])
            self.assertEqual(result["unsafe_content_rows"], [1])

    def test_queue_validator_blocks_unsafe_subject(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = Path(temp) / "queue.csv"
            fields = [
                "status", "to", "candidate_name", "subject", "body", "channel", "confidence", "source",
                "matched_keywords", "risk_to_confirm", "personalization_note", "review_note",
            ]
            with queue.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow({
                    "status": "needs_review", "to": "person@valid.edu", "candidate_name": "P",
                    "subject": "Click here https://evil.example/", "body": "Safe body",
                    "channel": "academic_web", "confidence": "medium", "source": "public profile",
                    "matched_keywords": "database", "risk_to_confirm": "review", "personalization_note": "public",
                    "review_note": "review",
                })
            result = validate_outreach_queue.validate(queue, "approved")
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["unsafe_content_rows"], [1])

    def test_send_gate_blocks_unsafe_subject(self) -> None:
        rows = [{
            "status": "approved",
            "to": "person@valid.edu",
            "subject": "Click here https://evil.example/",
            "body": "Safe body",
            "approved_at": "t",
            "approved_by": "r",
            "approval_note": "n",
        }]
        eligible, skipped = send_outreach.eligible_rows(rows, "approved", 20)
        self.assertEqual(eligible, [])
        self.assertEqual(skipped[0]["_reason"], "URL or instruction-like public-source content in outbound copy")

    def test_pipeline_requires_explicit_sender_for_outreach(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "run_sourcing_pipeline.py"), "--jd-text", "Database Engineer"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("requires explicit --sender-name and --company-hint", completed.stderr)

    def test_pipeline_rejects_jd_and_jd_text_together(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            jd = Path(temp) / "jd.md"
            jd.write_text("Database Engineer", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "run_sourcing_pipeline.py"), "--jd", str(jd), "--jd-text", "Other", "--no-outreach"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Pass only one", completed.stderr)

    def test_optional_path_cli_defaults_and_nonempty_pipeline_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jd = root / "database_jd.md"
            jd.write_text("Database Kernel Engineer\nRocksDB storage engine", encoding="utf-8")
            table = root / "candidates.md"
            write_candidate_table(table, [candidate("person@valid.edu")])
            commands = [
                [sys.executable, str(SCRIPTS / "export_candidates_csv.py"), "--candidates", str(table), "--out", str(root / "out.csv")],
                [sys.executable, str(SCRIPTS / "shortlist_candidates.py"), "--candidates", str(table), "--out", str(root / "short.md")],
                [sys.executable, str(SCRIPTS / "enrich_candidate_research.py"), "--input", str(table), "--out", str(root / "enriched.md")],
                [sys.executable, str(SCRIPTS / "draft_outreach.py"), "--candidates", str(table), "--jd", str(jd), "--out", str(root / "drafts.md"), "--sender-name", "Recruiter", "--company-hint", "Database team"],
            ]
            for command in commands:
                completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
                self.assertEqual(completed.returncode, 0, completed.stderr)

            platform = root / "platform.csv"
            platform.write_text("name,email,company\nCandidate One,person@valid.edu,Example Lab\n", encoding="utf-8")
            pipeline = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / "run_sourcing_pipeline.py"), "--jd", str(jd),
                    "--platform-input", f"other={platform}", "--no-search", "--no-github-search",
                    "--no-academic-expand", "--no-paper-author-resolve", "--no-arxiv", "--no-dblp",
                    "--no-openalex", "--skip-publication-pdfs", "--no-outreach", "--step-timeout", "60",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(pipeline.returncode, 0, pipeline.stderr)
            summaries = list((root / "outputs").glob("*/database_jd_pipeline_summary.md"))
            self.assertEqual(len(summaries), 1)
            self.assertIn("Candidate rows: 1", summaries[0].read_text(encoding="utf-8"))

    def test_max_send_overflow_is_logged_as_skipped(self) -> None:
        rows = [
            {"status": "approved", "to": f"p{i}@example.edu", "subject": "s", "body": "b", "approved_at": "t", "approved_by": "r", "approval_note": "n"}
            for i in range(3)
        ]
        eligible, skipped = send_outreach.eligible_rows(rows, "approved", 1)
        self.assertEqual(len(eligible), 1)
        self.assertEqual(len(skipped), 2)
        self.assertTrue(all(row["_reason"] == "over --max-send limit" for row in skipped))

    def test_shortlist_rewards_multichannel_and_strong_recommendation(self) -> None:
        row = candidate("p@valid.edu")
        row["渠道"] = "academic_web; paper_author"
        row["推荐级别"] = "强推荐"
        score = shortlist_candidates.shortlist_score(row)
        self.assertGreaterEqual(score, 75 + 12 + 8 + 15)

    def test_partial_send_failure_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = Path(temp) / "queue.csv"
            log = Path(temp) / "log.csv"
            fields = ["status", "to", "candidate_name", "subject", "body", "approved_at", "approved_by", "approval_note"]
            with queue.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow({"status": "approved", "to": "p@valid.edu", "candidate_name": "P", "subject": "S", "body": "B", "approved_at": "t", "approved_by": "r", "approval_note": "n"})
            error_entry = send_outreach.log_entry("send", "error", {"to": "p@valid.edu"}, "failed")
            argv = ["send_outreach.py", "--queue", str(queue), "--log", str(log), "--send"]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(send_outreach, "send_messages", return_value=[error_entry]):
                self.assertEqual(send_outreach.main(), 5)

    def test_messages_have_delivery_headers(self) -> None:
        message = send_outreach.build_message(
            {"to": "person@example.edu", "subject": "Subject", "body": "Body"},
            "sender@example.com",
            "Sender",
            "",
        )
        self.assertTrue(message["Date"])
        self.assertTrue(message["Message-ID"])
        self.assertTrue(str(message["Message-ID"]).endswith("@example.com>"))
        comma_name = send_outreach.build_message(
            {"to": "person@example.edu", "subject": "Subject", "body": "Body"},
            "sender@example.com",
            "Doe, Jane",
            "",
        )
        self.assertIn('"Doe, Jane"', str(comma_name["From"]))

    def test_publication_email_attribution_is_strict_and_supports_chinese_names(self) -> None:
        authors = {"Wei Li": "", "Ming Zhou": ""}
        self.assertEqual(source_publication_emails.unique_author_for_email("publisher@x.com", authors), "")
        self.assertEqual(source_publication_emails.unique_author_for_email("flamingo@x.com", authors), "")
        self.assertNotIn("publisher@x.com", source_publication_emails.extract_emails("publisher@x.com"))
        if source_publication_emails.lazy_pinyin is not None:
            self.assertEqual(source_publication_emails.unique_author_for_email("zhangwei@x.com", {"张伟": ""}), "张伟")

    def test_empty_arxiv_fixture_is_exercised(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            jd = Path(temp) / "jd.md"
            out = Path(temp) / "papers.md"
            jd.write_text("Database Kernel Engineer", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "generate_paper_author_queries.py"), "--jd", str(jd), "--out", str(out), "--offline-xml", str(ROOT / "references" / "empty-arxiv-feed.xml"), "--no-dblp", "--no-openalex"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(out.exists())
            self.assertIn("zero relevant papers", out.read_text(encoding="utf-8"))

    def test_plain_smtp_requires_explicit_insecure_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = Path(temp) / "queue.csv"
            with queue.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["status", "to", "subject", "body", "approved_at", "approved_by", "approval_note"])
                writer.writeheader()
                writer.writerow({"status": "approved", "to": "p@valid.edu", "subject": "S", "body": "B", "approved_at": "t", "approved_by": "r", "approval_note": "n"})
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "send_outreach.py"), "--queue", str(queue), "--send", "--smtp-security", "plain"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Plain SMTP would expose AUTH credentials", completed.stderr)

    def test_safe_http_closes_on_http_error(self) -> None:
        response = importlib.import_module("requests").Response()
        response.status_code = 500
        response.url = "https://example.com"
        response.reason = "error"
        sock = mock.Mock()
        sock.getpeername.return_value = ("8.8.8.8", 443)
        response.raw = mock.Mock()
        response.raw._connection = mock.Mock(sock=sock)
        response.close = mock.Mock()
        addresses = {ipaddress.ip_address("8.8.8.8")}
        with mock.patch.object(safe_http, "validate_public_url", return_value="https://example.com"), mock.patch.object(
            safe_http, "resolved_addresses", return_value=addresses
        ), mock.patch.object(safe_http.requests, "get", return_value=response):
            with self.assertRaises(Exception):
                safe_http.safe_get_public("https://example.com")
        response.close.assert_called()


if __name__ == "__main__":
    unittest.main()
