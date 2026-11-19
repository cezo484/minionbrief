# MinionBrief

MinionBrief turns a topic and a set of sources into a structured Markdown
research brief. Give it a topic and two or more source files or URLs, and it
writes one file with key findings, points where the sources agree, points
where they disagree, and open questions.

The brief text comes from a real model call through the OpenMinion 0.1
Python SDK. If the model call fails, the program reports the error and
writes nothing. There is no local fallback that invents a brief.

This project was built for Cortensor Hackathon #5 on DoraHacks.

## Setup

Python 3.11 or newer is required. The OpenMinion SDK needs 3.11, so 3.10
will not work.

```sh
git clone https://github.com/cezo484/minionbrief.git
cd minionbrief
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The only third-party dependency is `openminion==0.1.22`. Everything else
is Python standard library.

Configure a provider once with the SDK setup command. The example below
uses Groq with a model that is available on its free tier:

```sh
openminion setup --provider groq --model openai/gpt-oss-20b --no-chat
```

The provider key is read from the environment by the SDK. For Groq, set
`GROQ_API_KEY` before running. MinionBrief never reads a key itself and
never writes one to a file. Any other provider from `openminion setup
--list-providers` works the same way, with no code change.

## Usage

```sh
python brief.py --topic "Why do we see lightning before we hear thunder?" \
  --sources examples/sources/lightning-physics.txt \
            examples/sources/thunder-distance.txt \
            examples/sources/lightning-safety.txt \
  --out brief.md
```

Options:

- `--topic`: the topic of the brief (required).
- `--sources`: one or more source files or http(s) URLs (required). URL
  pages are fetched and their markup is stripped to readable text.
- `--out`: output file, default `brief.md`.
- `--model`: model id to use. When omitted, the configured default is used.
- `--session-id`: OpenMinion session id. When omitted, a fresh one is
  generated per run.
- `--timeout`: model call timeout in seconds, default 300.
- `--max-chars`: characters kept per source, default 6000. Longer sources
  are cut with a `[truncated]` marker so a large input stays inside
  provider limits.

A run takes roughly one to two minutes. Most of that is runtime startup,
not the model call.

## Example output

`examples/sample-brief.md` is a real run of the command above. It starts
like this:

```markdown
# Research brief: Why do we see lightning before we hear thunder?

Sources used:
- examples/sources/lightning-physics.txt
- examples/sources/thunder-distance.txt
- examples/sources/lightning-safety.txt

## Key findings
- Lightning is an electrical discharge that heats air to ~30,000 K,
  producing a visible flash. (Source 1)
- The flash appears almost instantly because light travels ~300,000 km/s,
  while sound travels ~343 m/s at 20 °C. (Source 1)
- Thunder is the shock wave produced when the heated air expands faster
  than the speed of sound. (Source 2)

## Where the sources agree
- Light reaches observers almost instantly while sound takes several
  seconds due to their vastly different speeds. (Sources 1 & 2)

## Where the sources disagree
- Distance estimation: Source 2 uses "one kilometre in three seconds",
  whereas Source 3 uses "five seconds per mile". (Sources 2 and 3)

## Open questions
- How does the rapid heating of air produce the specific sound
  characteristics of thunder?
```

The full file is in `examples/sample-brief.md`. The three short source
notes it was built from are in `examples/sources/`.

## How it works

`brief.py` has four steps:

1. Load the sources. Files are read as text, URLs are fetched with
   `urllib` and stripped of markup.
2. Build one prompt: the topic, each source quoted in full, and a fixed
   four-heading structure with rules for each section.
3. Send the prompt through the SDK. `brief.py` creates an `Agent` with a
   short system instruction, an explicit session id, and `tools=[]`. The
   empty tool list is deliberate. Without it the runtime attaches its
   default tool catalog to the request, which pushed the request past the
   free-tier token limit on Groq. See What We Learned below.
4. Check the reply for the four required headings, warn if any is missing,
   and write the brief with a title and a source list.

## Tests

```sh
python -m unittest discover -v
```

21 tests, all passing. They cover source loading and truncation, URL
fetching and markup stripping, prompt construction, brief structure
validation, output formatting, session id generation, argument parsing,
and the error paths for missing or empty sources and a missing SDK. The
tests never call a provider, so they run offline.

## Building Blocks Tested

What this project used from OpenMinion 0.1.22, and how each part behaved:

- Model execution through the `Agent` class (`Agent.run`). Worked. The
  reply came back as `AgentRunResult` with text, run id, and run state.
- Sessions. Each run gets a fresh explicit session id. Worked, no
  special handling needed.
- Instructions override. The system instruction is passed per run through
  the `Agent` constructor. Worked.
- Model override. The model id is passed per run. Worked, and it is how
  the run moved from the stale default model to a working one.
- Provider setup. `openminion setup --provider groq` wrote a config that
  keeps the key out of the file and reads it from `GROQ_API_KEY`. Worked.
  The recommended default model in the preset did not work, see below.
- Turn timeout. `Agent.run` accepts `timeout_seconds`. Worked.
- Provider errors. Failures surfaced as `ProviderError` with the
  provider status and request id. Worked, and the messages were specific
  enough to act on.

Not used, on purpose: run-scoped tools, delegation, memory, skills,
scheduled tasks. This project needs one model call per run, so those
parts were left out rather than added for show.

## What We Learned

Four findings from building and running this project. The first three
cost real debugging time.

1. The Groq preset recommends a model that no longer answers. Setup
   suggests `llama-3.3-70b-versatile`, but Groq returns HTTP 404 for it:
   the model does not exist or the account has no access to it. The fix
   was to use `openai/gpt-oss-20b` instead. Suggested improvement: check
   the recommended model of each preset against the provider before a
   release, or have setup verify the model with one cheap call.
2. A plain `Agent` run sends more than the prompt. Without an explicit
   tool list, the runtime attaches its default tool catalog, and the
   request passed the free-tier token-per-minute limit on Groq
   (HTTP 413). Passing `tools=[]` fixed it at once. Suggested
   improvement: say this in the SDK docs, or skip the default catalog
   when a run declares no tools.
3. Bracketed IPv6 entries in `NO_PROXY` break provider calls. On a
   machine where `NO_PROXY` contained `[::1]`, every provider call
   failed inside httpx with `InvalidURL: Invalid port`. Setting
   `NO_PROXY=localhost,127.0.0.1` for the run fixed it. Suggested
   improvement: parse the proxy environment defensively, or report the
   bad entry in the error instead of a bare URL parse failure.
4. Startup dominates a short run. A one-turn run took about 50 seconds
   end to end, and the full brief run took about 100 seconds, with most
   of that in runtime bootstrap rather than the model call. That is fine
   for this project, but it is worth knowing before building a CLI tool
   on the SDK.

What worked well: the `Agent` facade itself. Install, setup, and a few
lines of code produced a working run, and the config file keeps secrets
out by default. The provider error messages carried enough detail to fix
each failure without guessing.

## License

MIT. See `LICENSE`.
