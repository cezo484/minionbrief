#!/usr/bin/env python3
"""MinionBrief: turn a topic and a set of sources into a Markdown research brief.

One command, one output file. The brief is produced by a real model call
through the OpenMinion 0.1 Python SDK. There is no fallback that writes a
brief without the model: if the model call fails, the program reports the
error and writes nothing.

Usage:
    python brief.py --topic "Why do we see lightning first?" \
        --sources notes-1.txt notes-2.txt --out brief.md

Sources can be local text files or http(s) URLs. The OpenMinion provider is
configured once with the SDK setup command, for example:

    openminion setup --provider groq --model openai/gpt-oss-20b --no-chat

The provider key is read from the environment by the SDK (GROQ_API_KEY for
Groq). This program never reads a key itself and never writes one anywhere.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.request
from pathlib import Path
from uuid import uuid4

REQUIRED_HEADINGS = (
    "Key findings",
    "Where the sources agree",
    "Where the sources disagree",
    "Open questions",
)

DEFAULT_MAX_SOURCE_CHARS = 6000
DEFAULT_TIMEOUT_SECONDS = 300

SYSTEM_PROMPT = (
    "You write short research briefs from supplied sources only. "
    "Use only what the sources state. Do not add facts from outside them. "
    "Write plain Markdown. Start directly with the first heading below, "
    "with no introduction before it and no closing paragraph after the "
    "last section."
)

BRIEF_TEMPLATE = """Write a research brief on this topic: {topic}

Sources (each source is quoted in full between its markers):

{source_blocks}

Structure the brief with exactly these four headings, in this order:

## Key findings
## Where the sources agree
## Where the sources disagree
## Open questions

Rules:
- Put the main claims from the sources under Key findings, one bullet per claim.
- Under Where the sources agree, list only points that at least two sources
  state in the same way.
- Under Where the sources disagree, list only points where the sources give
  different numbers, dates, or conclusions, and name the sources on each side.
- Under Open questions, list questions the sources leave unanswered.
- If the sources do not disagree on anything, write "No disagreements found
  in the sources." under that heading instead of inventing one.
- Keep every bullet to one or two sentences.
"""


def load_source(location: str, max_chars: int = DEFAULT_MAX_SOURCE_CHARS) -> str:
    """Read one source. URLs are fetched, anything else is read as a file."""
    if location.startswith(("http://", "https://")):
        text = fetch_url(location)
    else:
        path = Path(location)
        if not path.is_file():
            raise FileNotFoundError(f"source not found: {location}")
        text = path.read_text(encoding="utf-8")
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n[truncated]"
    return text


def fetch_url(url: str, timeout_seconds: int = 30) -> str:
    """Fetch a URL and return its text with markup stripped."""
    request = urllib.request.Request(url, headers={"User-Agent": "MinionBrief/0.1"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return strip_html(raw)


def strip_html(raw: str) -> str:
    """Drop tags, scripts, and styles from a page, keeping readable text."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def load_sources(locations: list[str], max_chars: int) -> list[tuple[str, str]]:
    """Load every source, keeping each location next to its text."""
    loaded = []
    for location in locations:
        loaded.append((location, load_source(location, max_chars)))
    return loaded


def build_prompt(topic: str, sources: list[tuple[str, str]]) -> str:
    """Assemble the model prompt: topic, quoted sources, fixed structure."""
    blocks = []
    for index, (location, text) in enumerate(sources, start=1):
        blocks.append(f"--- Source {index}: {location} ---\n{text}\n--- End source {index} ---")
    return BRIEF_TEMPLATE.format(topic=topic, source_blocks="\n\n".join(blocks))


def missing_headings(brief_text: str) -> list[str]:
    """Return the required headings that are absent from the brief text."""
    missing = []
    for heading in REQUIRED_HEADINGS:
        pattern = re.compile(r"^#{1,3}\s+" + re.escape(heading) + r"\s*$", re.IGNORECASE | re.MULTILINE)
        if not pattern.search(brief_text):
            missing.append(heading)
    return missing


def format_brief(topic: str, locations: list[str], model_text: str) -> str:
    """Wrap the model output with a title and a source list."""
    title = f"# Research brief: {topic}\n"
    source_lines = "\n".join(f"- {location}" for location in locations)
    return f"{title}\nSources used:\n{source_lines}\n\n{model_text.strip()}\n"


def run_model(prompt: str, model: str | None, session_id: str, timeout_seconds: int) -> str:
    """Send the prompt through the OpenMinion SDK and return the reply text.

    The SDK is imported here so that loading this module never requires it;
    argument parsing, source loading, and the tests all work without it.
    ``tools=[]`` is deliberate: it keeps the runtime from attaching its
    default tool catalog to the request, which keeps the request small
    enough for free-tier provider limits.
    """
    try:
        from openminion import Agent
    except ImportError as exc:
        raise RuntimeError(
            "The openminion package is not installed. Run: pip install -r requirements.txt"
        ) from exc

    kwargs: dict[str, object] = {
        "instructions": SYSTEM_PROMPT,
        "tools": [],
        "session_id": session_id,
    }
    if model:
        kwargs["model"] = model
    with Agent(**kwargs) as agent:  # type: ignore[arg-type]
        result = agent.run(prompt, timeout_seconds=timeout_seconds)
    text = result.text.strip()
    if text.startswith("openminion:"):
        text = text[len("openminion:"):].strip()
    return text


def session_id_for(topic: str) -> str:
    """Build a fresh, readable session id for one run."""
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40] or "brief"
    return f"minionbrief-{slug}-{uuid4().hex[:8]}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a Markdown research brief from sources, using the OpenMinion SDK."
    )
    parser.add_argument("--topic", required=True, help="Topic of the brief.")
    parser.add_argument(
        "--sources",
        required=True,
        nargs="+",
        help="Source files or URLs, at least one.",
    )
    parser.add_argument("--out", default="brief.md", help="Output file (default: brief.md).")
    parser.add_argument(
        "--model",
        default=None,
        help="Model id to use. When omitted, the configured default is used.",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="OpenMinion session id. When omitted, a fresh one is generated.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Model call timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_SOURCE_CHARS,
        help=f"Characters kept per source (default: {DEFAULT_MAX_SOURCE_CHARS}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        sources = load_sources(args.sources, args.max_chars)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: cannot load sources: {exc}", file=sys.stderr)
        return 2

    empty = [location for location, text in sources if not text]
    if empty:
        print(f"error: empty source(s): {', '.join(empty)}", file=sys.stderr)
        return 2

    prompt = build_prompt(args.topic, sources)
    session_id = args.session_id or session_id_for(args.topic)

    try:
        model_text = run_model(prompt, args.model, session_id, args.timeout)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - the SDK surfaces provider errors here
        print(f"error: model call failed: {exc}", file=sys.stderr)
        return 1

    if not model_text:
        print("error: model returned an empty reply, nothing written", file=sys.stderr)
        return 1

    missing = missing_headings(model_text)
    if missing:
        print(
            f"warning: brief is missing heading(s): {', '.join(missing)}",
            file=sys.stderr,
        )

    out_path = Path(args.out)
    out_path.write_text(
        format_brief(args.topic, args.sources, model_text), encoding="utf-8"
    )
    print(f"wrote {out_path} ({len(model_text)} characters of brief text)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
