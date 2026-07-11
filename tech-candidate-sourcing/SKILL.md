---
name: tech-candidate-sourcing
description: "Source public candidates for algorithm, AI research, and technical roles from a job description and produce reviewable Markdown/CSV tables with public emails, sources, research/project evidence, recommendation signals, risks, China-related public evidence, and optional approval-gated outreach drafts. Use for JD-to-candidate sourcing, public-email discovery, candidate lead generation, shortlist review, or explicitly approved SMTP outreach. Default behavior never sends email."
---

# Tech Candidate Sourcing

## Install

Install dependencies once from the directory containing this skill:

```bash
python -m pip install -r tech-candidate-sourcing/requirements.txt
```

Use forward slashes in commands; they work on macOS, Linux, and Windows Python.

## Candidate Workflow

Run a candidate-only broad pass:

```bash
python tech-candidate-sourcing/scripts/run_sourcing_pipeline.py --jd <JD_FILE> --out-dir <OUTPUT_DIR> --coverage-preset broad --no-outreach
```

Prioritize China-related public sources:

```bash
python tech-candidate-sourcing/scripts/run_sourcing_pipeline.py --jd <JD_FILE> --out-dir <OUTPUT_DIR> --coverage-preset broad --focus china --no-outreach
```

When the user requests a hard China-related constraint, require explicit public evidence:

```bash
python tech-candidate-sourcing/scripts/run_sourcing_pipeline.py --jd <JD_FILE> --out-dir <OUTPUT_DIR> --coverage-preset broad --focus china --require-china-signal --no-outreach
```

`--require-china-signal` keeps only rows with explicit public China affiliation, location, institution, or domain evidence. Chinese/Japanese characters alone are not evidence. Never infer nationality or ethnicity from names, appearance, or language.

For pasted JD text, use `--jd-text`. For a quick smoke test, use `--coverage-preset custom --depth quick`. The broad preset raises the per-step timeout to at least one hour and gives webpage sourcing an internal deadline so it writes partial output instead of losing all progress.

Pass known public pages with repeatable `--seed-url` or a one-URL-per-line `--seed-file`. Add `--no-search` to process seeds only. Phone collection is disabled by default; enable it only with `--collect-phones` when needed and appropriate.

The pipeline uses public GitHub metadata, public academic/personal pages, arXiv, DBLP, OpenAlex, and uniquely attributable emails in public paper PDFs. It does not guess emails, log in to platforms, bypass anti-scraping controls, or scrape private recruiting data. HTML search failures and possible markup/CAPTCHA issues are written to sourcing diagnostics.

Set `GITHUB_TOKEN` or `GH_TOKEN` for broad GitHub public reads. GitHub 403/429 rate limits are written to diagnostics rather than silently converted to zero results. Add `--skip-publication-pdfs` when PyMuPDF is unavailable or PDF extraction is not needed.

## Review Outputs

Read the final Markdown/CSV table and validation report. The pipeline also writes JSONL as the canonical machine-readable handoff. The table columns are:

- Name
- Basic information
- Phone
- Email
- Channel
- Lead type
- Confidence
- Recommendation level
- Match score
- Source
- Matched keywords
- Recommendation points
- Risks/to confirm
- Suggested action
- Research direction
- Representative paper/project evidence
- China-related public signal

Treat scores as lead-ranking signals, not hiring judgments. A blocked/service/placeholder email makes validation `needs_review`; structural failure, no candidates, or rows without a real email make it `fail`.

Create a review shortlist:

```bash
python tech-candidate-sourcing/scripts/shortlist_candidates.py --candidates <CANDIDATE_MD> --out <SHORTLIST_MD> --allowlist-out <ALLOWLIST_TXT>
```

For a strict China-related shortlist, add `--focus china --require-china-signal`.

## Outreach Drafts

Drafts require explicit sender and company/role context. An empty supplied allowlist intentionally produces zero drafts.

Replace every angle-bracket placeholder in command examples. Queue validation blocks unresolved placeholders, URLs, or instruction-like text copied from public pages.

```bash
python tech-candidate-sourcing/scripts/draft_outreach.py --candidates <CANDIDATE_MD> --jd <JD_FILE> --out <DRAFTS_MD> --queue-csv <QUEUE_CSV> --allowlist <ALLOWLIST_TXT> --sender-name <SENDER_NAME> --company-hint <COMPANY_HINT> --language auto
```

Use `--language auto`, `zh`, or `en`. Auto selects from public candidate/JD signals. Every queue row starts as `needs_review`; no message is sent by this command.

Approve only reviewed recipients:

```bash
python tech-candidate-sourcing/scripts/approve_outreach_queue.py --queue <QUEUE_CSV> --out <APPROVED_QUEUE_CSV> --allowlist <APPROVED_EMAILS_TXT> --approved-by <REVIEWER> --approval-note <NOTE>
```

Validate and dry-run before real sending:

```bash
python tech-candidate-sourcing/scripts/validate_outreach_queue.py --queue <APPROVED_QUEUE_CSV> --out <QUEUE_VALIDATION_MD>
python tech-candidate-sourcing/scripts/send_outreach.py --queue <APPROVED_QUEUE_CSV> --log <SEND_LOG_CSV>
```

Real sending requires `--send`, `status=approved`, complete approval metadata, SMTP credentials, and an explicit sender. `--status needs_review` cannot send unless the operator also supplies the conspicuous emergency override `--i-know-what-im-doing`.

Bash/zsh environment variables:

```bash
export SMTP_HOST="<SMTP_HOST>"
export SMTP_PORT="587"
export SMTP_FROM="<SENDER_EMAIL>"
export SMTP_USER="<SMTP_USER>"
export SMTP_PASSWORD="<SMTP_PASSWORD>"
python tech-candidate-sourcing/scripts/send_outreach.py --queue <APPROVED_QUEUE_CSV> --log <SEND_LOG_CSV> --send --max-send 20 --from-name "<SENDER_NAME>"
```

PowerShell environment variables:

```powershell
$env:SMTP_HOST="<SMTP_HOST>"
$env:SMTP_PORT="587"
$env:SMTP_FROM="<SENDER_EMAIL>"
$env:SMTP_USER="<SMTP_USER>"
$env:SMTP_PASSWORD="<SMTP_PASSWORD>"
python tech-candidate-sourcing/scripts/send_outreach.py --queue <APPROVED_QUEUE_CSV> --log <SEND_LOG_CSV> --send --max-send 20 --from-name "<SENDER_NAME>"
```

Use `--smtp-security auto` by default; it selects SMTPS for port 465 and STARTTLS otherwise. Plain SMTP AUTH is rejected unless the operator explicitly passes `--allow-insecure-plain-smtp`. Pass repeatable `--suppression-list` files as final no-contact guards; a missing suppression-list path is a hard error.

## Additional Tools

Generate query checklists without importing network dependencies:

```bash
python tech-candidate-sourcing/scripts/generate_channel_queries.py --jd <JD_FILE> --out <QUERY_MD>
```

Import authorized platform exports, then merge by any overlapping public email:

```bash
python tech-candidate-sourcing/scripts/import_platform_candidates.py --input <CSV_TSV_OR_TEXT> --out <OUTPUT_MD> --platform <boss|liepin|maimai|linkedin|other>
python tech-candidate-sourcing/scripts/merge_candidate_tables.py --input <PUBLIC_MD> --input <PLATFORM_MD> --out <MERGED_MD>
```

Read `references/sourcing-workflow.md` when tuning source breadth, understanding confidence, or extending the workflow.

Run regression tests after changes:

```bash
python -m unittest discover -s tech-candidate-sourcing/tests -v
python -S -m unittest discover -s tech-candidate-sourcing/tests -v
```
