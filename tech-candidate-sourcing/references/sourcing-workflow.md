# Sourcing Workflow Reference

## Scope

The skill turns a technical JD into an auditable candidate-lead table. It records only contact information shown on public pages, public GitHub metadata, public papers, or authorized user-provided exports. It does not guess emails, authenticate to recruiting platforms, bypass access controls, or send outreach by default.

## Source Routes

1. Extract generic technical keywords from the JD with the standard-library-only `jd_utils.py`.
2. Search public GitHub repositories/users and inspect public contributor, profile, linked-homepage, commit, or Atom metadata.
3. Search public academic, lab, faculty, student, and personal pages; follow bounded public roster/profile links.
4. Query arXiv, DBLP, and OpenAlex metadata using JD-derived terms.
5. Extract an email from a public paper PDF only when name/email evidence uniquely attributes it to one listed author.
6. Merge rows when any public email overlaps, retain multi-source evidence, filter obvious non-person rows, enrich research evidence, and validate.

DBLP queries must come from the JD; no Agent/LLM phrase is hard-coded. Publication-PDF candidates receive evidence-sensitive scores rather than a fixed shortlist-passing score.

## Breadth And Runtime

`--coverage-preset broad` increases GitHub, public-web, academic-link, paper-author, and PDF limits. It also raises `--step-timeout` to at least 3600 seconds. `source_candidates.py` receives an internal deadline 60 seconds shorter than the subprocess timeout and writes partial results plus diagnostics when the deadline is reached.

HTML search is a best-effort discovery path. Bing or DuckDuckGo failures, zero parseable results, suspected CAPTCHA, or markup changes appear under `Search diagnostics` in the source table. Prefer explicit seeds and public APIs when deterministic coverage matters.

## China-Related Evidence

`--focus china` changes query priority. `--require-china-signal` is a hard evidence filter. Accepted signals include public China-based affiliations/locations, named Chinese institutions or research organizations, and `.edu.cn/.ac.cn/.cn` domains. Generic Han characters, Japanese names, and substrings such as `cas` inside `case` or `pku` inside `pkumar` are not signals. These are sourcing constraints, not claims about nationality or ethnicity.

## Data And Validation

Markdown remains the human review format. `scripts/md_table.py` is the shared parser/renderer and also exposes JSONL helpers for future canonical storage. Do not add new one-off Markdown parsers.

Candidate validation statuses:

- `pass`: required schema and contact structure are usable.
- `needs_review`: suspicious/service email, duplicates, weak person-name evidence, or missing enrichment requires manual review.
- `fail`: required schema is missing, the table is empty, there are no real emails, or a row has no real email.

Phone extraction is disabled by default because false-positive and sensitivity costs are higher than for public email.

## Outreach Safety

Draft generation requires explicit `--sender-name` and `--company-hint`. `--language auto` uses public candidate and JD signals; `zh` and `en` force a language. A supplied empty allowlist means draft nobody.

Real sending requires all of the following:

- `--send`
- queue row `status=approved`
- `approved_at`, `approved_by`, and `approval_note`
- valid SMTP configuration
- recipient not present in suppression lists
- recipient below `--max-send`
- no unresolved `<PLACEHOLDER>` text, public-source URL, or instruction-like content in subject/body

Changing `--status` to `needs_review` is not enough to bypass approval. The emergency override `--i-know-what-im-doing` must also be present, and should not be used in normal operation.

SMTP supports STARTTLS and SMTPS/465. Plain AUTH requires the explicit unsafe override. Messages use structured address headers plus `Date` and a sender-domain `Message-ID`. Setup/login and per-recipient failures are written to the send log; rows beyond `--max-send` are logged as skipped, and any recipient failure returns a nonzero exit code.

## Security Notes

Public URL fetching blocks local/private/link-local addresses, validates every redirect, limits response size, verifies the connected peer against validated public DNS addresses, and closes responses on HTTP errors. This reduces SSRF and DNS-rebinding exposure. Treat any user-provided seed as untrusted input.

## Cross-Platform Commands

Use forward slashes in all Python paths. Install dependencies with:

```bash
python -m pip install -r tech-candidate-sourcing/requirements.txt
```

Use `export NAME=value` on bash/zsh and `$env:NAME="value"` on PowerShell. ZIP archives must contain POSIX-style `/` entry separators so `unzip` works on macOS and Linux.
