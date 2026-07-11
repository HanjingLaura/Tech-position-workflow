# Tech Candidate Sourcing Skill

[English](README.md) | [简体中文](README.zh-CN.md)

This repository contains a local Codex skill for sourcing public candidates for algorithm, AI research, and broader technical roles from a job description.

The skill turns a JD into reviewable Markdown/CSV candidate tables with public emails, source URLs, evidence, fit signals, risks, and suggested next actions. It is intentionally generic: it does not include a fixed candidate list, hard-coded Agent-role presets, guessed emails, or private recruiting data.

## What It Does

- Extracts JD-derived technical keywords.
- Searches public GitHub, academic/personal pages, arXiv, DBLP, OpenAlex, and public paper PDFs.
- Extracts only public emails that appear directly or in common public obfuscation formats.
- Merges candidates by overlapping public emails.
- Produces Markdown, CSV, JSONL, validation reports, and optional outreach drafts.
- Keeps outreach disabled by default; real sending requires explicit approval metadata and SMTP configuration.

## Install

Install Python dependencies from the repository root:

```bash
python -m pip install -r tech-candidate-sourcing/requirements.txt
```

To make the skill available to Codex, copy the skill folder into your Codex skills directory:

```bash
cp -R tech-candidate-sourcing ~/.codex/skills/
```

On Windows PowerShell:

```powershell
Copy-Item -Recurse -Force .\tech-candidate-sourcing $env:USERPROFILE\.codex\skills\
```

## Basic Usage

Run a candidate-only broad pass:

```bash
python tech-candidate-sourcing/scripts/run_sourcing_pipeline.py --jd path/to/jd.md --out-dir outputs --coverage-preset broad --no-outreach
```

Use pasted JD text:

```bash
python tech-candidate-sourcing/scripts/run_sourcing_pipeline.py --jd-text "Algorithm Researcher..." --out-dir outputs --coverage-preset custom --depth quick --no-outreach
```

Prioritize China-related public sources without making nationality assumptions:

```bash
python tech-candidate-sourcing/scripts/run_sourcing_pipeline.py --jd path/to/jd.md --out-dir outputs --coverage-preset broad --focus china --require-china-signal --no-outreach
```

Seed known public pages:

```bash
python tech-candidate-sourcing/scripts/run_sourcing_pipeline.py --jd path/to/jd.md --out-dir outputs --seed-url https://example.edu/person --no-outreach
```

## Output Columns

The candidate table uses the standard schema in `tech-candidate-sourcing/scripts/candidate_schema.py`, including:

- name/page name
- basic information
- phone
- email
- channel
- lead type
- confidence
- recommendation level
- match score
- source
- matched keywords
- recommendation points
- risks/to confirm
- suggested action
- research direction
- representative paper/project evidence
- China-related public signal

Treat scores as first-pass ranking signals only. Manually review ownership, role fit, location, compensation, availability, and outreach permission before any contact.

## Safety Boundaries

- Collect only public contact information.
- Do not guess or synthesize emails.
- Do not log in to platforms or bypass anti-scraping controls.
- Do not infer nationality or ethnicity from names, language, or appearance.
- Do not send email by default.
- Real email sending requires approved queue rows, approval metadata, suppression-list checks, SMTP config, and explicit `--send`.

## Validation

Run the skill validator:

```bash
python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py tech-candidate-sourcing
```

Run regression tests:

```bash
python -m unittest discover -s tech-candidate-sourcing/tests -v
python -S -m unittest discover -s tech-candidate-sourcing/tests -v
```

Run syntax checks:

```bash
python -m py_compile tech-candidate-sourcing/scripts/*.py
```

## Repository Layout

```text
tech-candidate-sourcing/
  SKILL.md
  agents/openai.yaml
  references/
  scripts/
  tests/
  requirements.txt
```

Generated outputs, local JD files, caches, bytecode, archives, and secrets are intentionally ignored by `.gitignore`.
