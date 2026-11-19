"""Tests for brief.py. Standard library unittest only, no network calls."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import brief


class TestLoadSource(unittest.TestCase):
    def test_loads_plain_text_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("  hello world  \n", encoding="utf-8")
            self.assertEqual(brief.load_source(str(path)), "hello world")

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            brief.load_source("/nonexistent-dir/nope.txt")

    def test_long_source_is_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long.txt"
            path.write_text("x" * 5000, encoding="utf-8")
            text = brief.load_source(str(path), max_chars=100)
            self.assertTrue(text.endswith("[truncated]"))
            self.assertLess(len(text), 200)

    def test_load_sources_keeps_location_with_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.txt"
            second = Path(tmp) / "b.txt"
            first.write_text("first text", encoding="utf-8")
            second.write_text("second text", encoding="utf-8")
            loaded = brief.load_sources([str(first), str(second)], 6000)
            self.assertEqual(loaded, [(str(first), "first text"), (str(second), "second text")])


class TestFetchUrl(unittest.TestCase):
    def test_data_url_is_fetched(self):
        # urllib handles data: URLs, so this tests the fetch path offline.
        text = brief.fetch_url("data:text/plain,hello%20from%20a%20url")
        self.assertEqual(text, "hello from a url")

    def test_strip_html_removes_tags_scripts_and_styles(self):
        raw = (
            "<html><head><style>p {color: red}</style></head>"
            "<body><script>alert(1)</script><h1>Title</h1>"
            "<p>First para.</p><p>Second para.</p></body></html>"
        )
        text = brief.strip_html(raw)
        self.assertIn("Title", text)
        self.assertIn("First para.", text)
        self.assertIn("Second para.", text)
        self.assertNotIn("alert", text)
        self.assertNotIn("color", text)
        self.assertNotIn("<", text)

    def test_strip_html_unescapes_entities(self):
        self.assertEqual(brief.strip_html("<p>fish &amp; chips</p>"), "fish & chips")


class TestBuildPrompt(unittest.TestCase):
    def test_prompt_contains_topic_and_every_source(self):
        sources = [("a.txt", "alpha text"), ("b.txt", "beta text")]
        prompt = brief.build_prompt("Test topic", sources)
        self.assertIn("Test topic", prompt)
        self.assertIn("alpha text", prompt)
        self.assertIn("beta text", prompt)
        self.assertIn("a.txt", prompt)
        self.assertIn("b.txt", prompt)

    def test_prompt_contains_all_required_headings(self):
        prompt = brief.build_prompt("T", [("a.txt", "x")])
        for heading in brief.REQUIRED_HEADINGS:
            self.assertIn(heading, prompt)


class TestMissingHeadings(unittest.TestCase):
    GOOD_BRIEF = """## Key findings
- one
## Where the sources agree
- two
## Where the sources disagree
- three
## Open questions
- four
"""

    def test_complete_brief_has_no_missing_headings(self):
        self.assertEqual(brief.missing_headings(self.GOOD_BRIEF), [])

    def test_missing_heading_is_reported(self):
        text = self.GOOD_BRIEF.replace("## Open questions\n- four\n", "")
        self.assertEqual(brief.missing_headings(text), ["Open questions"])

    def test_empty_text_misses_everything(self):
        self.assertEqual(brief.missing_headings(""), list(brief.REQUIRED_HEADINGS))

    def test_heading_match_is_case_insensitive(self):
        text = self.GOOD_BRIEF.replace("## Key findings", "## KEY FINDINGS")
        self.assertEqual(brief.missing_headings(text), [])


class TestFormatBrief(unittest.TestCase):
    def test_output_has_title_sources_and_model_text(self):
        out = brief.format_brief("My topic", ["a.txt", "b.txt"], "## Key findings\n- x\n")
        self.assertTrue(out.startswith("# Research brief: My topic\n"))
        self.assertIn("- a.txt\n- b.txt", out)
        self.assertIn("## Key findings\n- x", out)
        self.assertTrue(out.endswith("\n"))


class TestSessionId(unittest.TestCase):
    def test_session_id_is_suffixed_and_unique(self):
        first = brief.session_id_for("Lightning and Thunder!")
        second = brief.session_id_for("Lightning and Thunder!")
        self.assertTrue(first.startswith("minionbrief-lightning-and-thunder-"))
        self.assertNotEqual(first, second)

    def test_empty_topic_falls_back_to_brief(self):
        self.assertTrue(brief.session_id_for("!!!").startswith("minionbrief-brief-"))


class TestParseArgs(unittest.TestCase):
    def test_full_command_line(self):
        args = brief.parse_args(
            ["--topic", "T", "--sources", "a.txt", "b.txt", "--out", "o.md",
             "--model", "m", "--timeout", "60"]
        )
        self.assertEqual(args.topic, "T")
        self.assertEqual(args.sources, ["a.txt", "b.txt"])
        self.assertEqual(args.out, "o.md")
        self.assertEqual(args.model, "m")
        self.assertEqual(args.timeout, 60)

    def test_defaults(self):
        args = brief.parse_args(["--topic", "T", "--sources", "a.txt"])
        self.assertEqual(args.out, "brief.md")
        self.assertIsNone(args.model)
        self.assertEqual(args.timeout, brief.DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(args.max_chars, brief.DEFAULT_MAX_SOURCE_CHARS)


class TestRunModelWithoutSdk(unittest.TestCase):
    def test_missing_sdk_gives_a_clear_error(self):
        # Simulate an environment where openminion is not installed.
        with mock.patch.dict(sys.modules, {"openminion": None}):
            with self.assertRaises(RuntimeError) as ctx:
                brief.run_model("prompt", None, "s", 10)
        self.assertIn("pip install -r requirements.txt", str(ctx.exception))


class TestMainErrors(unittest.TestCase):
    def test_missing_source_file_exits_2(self):
        code = brief.main(["--topic", "T", "--sources", "/nope/missing.txt"])
        self.assertEqual(code, 2)

    def test_empty_source_file_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.txt"
            path.write_text("   \n", encoding="utf-8")
            code = brief.main(["--topic", "T", "--sources", str(path)])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
