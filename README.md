# AI Job Search Assistant

An AI-powered personal job search platform designed for Badis Moalla.

The objective is not mass application automation.

The objective is to create a professional job-search intelligence system that:

- Finds relevant opportunities
- Evaluates compatibility with my technical background
- Prioritizes high-probability applications
- Generates personalized communication
- Tracks application performance
- Improves job search strategy over time


# Candidate Profile

## Professional Identity

Software Test & Validation Engineer specialized in:

- Embedded systems validation
- Automotive ECU testing
- Diagnostic protocols
- AUTOSAR Adaptive platforms
- Software quality improvement


## Current Experience

Problem Manager - Process Improvement and Quality

KPIT Engineering  
Automotive supplier within BMW Group supply chain


## Main Technical Domains

### Automotive Testing

- ECU validation
- UDS (ISO 14229)
- DoIP (ISO 13400)
- DLT analysis
- Wireshark
- BMW Zedis
- AUTOSAR Adaptive
- ASPICE V-model
- SWE.6 qualification testing


### Test Automation

- Python automation scripts
- Jira API automation
- Test reporting
- Regression testing
- Quality dashboards


### Data & BI (Supporting Skill)

Used for improving software quality processes:

- Power BI
- DAX
- Power Query
- SQL
- Python Pandas


# Career Targeting Strategy

## Tier 1 — Main Targets

Highest priority:

- Embedded Test Engineer
- Software Validation Engineer
- Automotive QA Engineer
- ECU Test Engineer
- System Test Engineer
- Integration Test Engineer
- Test Automation Engineer

These roles directly match:

- BMW experience
- ECU validation
- Diagnostic protocols
- Testing methodology


## Tier 2 — Secondary Targets

Consider:

- QA Automation Engineer
- Software Quality Engineer
- Verification Engineer

The role should involve:

- Python
- Testing frameworks
- CI/CD
- Software quality
- Automation


## Tier 3 — Selective Data Roles

Only consider when:

- Junior/Mid level
- Power BI focused
- SQL required
- Python useful
- Data quality/testing involved

Examples:

- Junior Data Analyst
- BI Developer
- Data Quality Analyst
- Power BI Developer

Avoid:

- Data Scientist
- Machine Learning Engineer
- Senior Data Engineer


# AI Rules

The AI assistant must:

## Never invent experience

Do not claim:

- CANoe experience
- Vector tools experience
- Azure expertise
- AUTOSAR Classic expertise
- Machine Learning experience


## Use accurate positioning

For missing skills:

Example:

Job requires CANoe.

Wrong:

"I have experience with CANoe."

Correct:

"My automotive validation experience includes DLT analysis, Wireshark debugging, UDS/DoIP diagnostics, and ECU validation workflows. These skills are directly transferable to CAN-based validation environments."


# Job Scoring System

Every job receives a score from 0–100.

## Automotive Testing Score

Criteria:

- +30 Automotive domain match
- +25 Embedded testing match
- +15 Diagnostic protocols
- +15 Python automation
- +10 ASPICE/testing process
- +5 Location/sponsorship compatibility


## Data Role Score

Criteria:

- +30 Power BI
- +25 SQL
- +20 Python
- +15 Business reporting
- +10 Domain relevance


# Application Workflow

Daily workflow:

1. Collect jobs

   Sources:

   - LinkedIn Jobs
   - Pracuj.pl
   - NoFluffJobs
   - JustJoinIT
   - Company career pages

2. Analyze jobs

   For each job, extract:

   - Company
   - Role
   - Location
   - Salary
   - Required skills
   - Match score
   - Missing skills

3. Generate application package

   For selected jobs, create:

   - Tailored cover letter
   - Recruiter message
   - HR email
   - Application answers

4. Track

   Store:

   - Company
   - Position
   - Date applied
   - Source
   - Status
   - Follow-up date


# Discovery Providers

Job discovery is provider-based (`core/discovery.py`): the pipeline runs
whichever providers are listed in `PIPELINE_ENABLED_SCRAPERS` (comma-separated
env var), collects `JobListing`s from each, and continues even if some
providers fail — one bad source never blocks the others.

Provider keys and what they need:

| Key | Type | Needs |
|---|---|---|
| `nofluffjobs`, `pracuj`, `justjoinit` | Public job-board scraper | Nothing — enabled by default |
| `greenhouse`, `lever`, `smartrecruiters`, `workday` | Company ATS | Entries in `config/company_sources.json` for that platform |
| `gmail_linkedin` | Gmail/LinkedIn Job Alert ingestion | `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` |

Default: `PIPELINE_ENABLED_SCRAPERS=nofluffjobs,pracuj,justjoinit` — the
three job-board scrapers only. Company ATS and Gmail are both **opt-in**:
add their keys to the comma-separated list to enable them (e.g.
`nofluffjobs,pracuj,justjoinit,gmail_linkedin,greenhouse`).

**Gmail/LinkedIn alerts**: this never scrapes linkedin.com directly — it
reads LinkedIn "Job Alert" emails already sitting in your own Gmail inbox
(via the read-only Gmail API) and parses job postings out of them. If
`gmail_linkedin` is enabled but the three `GMAIL_*` credentials above
aren't configured, it fails softly per run (shows up as a normal failed
source in the `jobs`/`run` output) — it does not crash the CLI, and no
Gmail credentials are required just to run `python main.py --help` or use
the other providers.

**Company ATS**: `config/company_sources.json` is configuration-driven —
add a company under the right platform key and it's scraped automatically,
no code changes needed. The shipped file contains sample/illustrative
entries only (see the `_note` field in that file) — verify or replace them
before relying on this for a real job search.

**Known limitations of the public job-board scrapers** (`nofluffjobs`,
`pracuj`, `justjoinit`): these three sites' endpoints/markup have changed
more than once already and are not guaranteed to keep working — each
scraper module (`modules/scraper/{nofluffjobs,pracuj,justjoinit}.py`)
documents its current best-effort fix and exactly what to check if it
breaks again. Treat them as "may work" sources, not guaranteed-reliable
ones — this is why company ATS and Gmail/LinkedIn-alert discovery exist as
independent, more durable alternatives.

Unknown keys in `PIPELINE_ENABLED_SCRAPERS` are reported clearly (not
silently ignored) — `python main.py plan` lists them under "Unknown
scraper keys (ignored)" so a typo doesn't fail silently.


# Project Architecture

```text
job_search_assistant/

├── data/
│   └── profile.json

├── modules/
│   ├── scraper/
│   │   └── job collectors
│   ├── matching/
│   │   └── job scoring engine
│   ├── ai/
│   │   └── message generation
│   ├── tracker/
│   │   └── application database
│   └── analytics/
│       └── job market insights

├── main.py
└── docs/
```


# Future Improvements

Possible future features:

- CV keyword optimization
- Market trend analysis
- Salary comparison
- Company sponsorship database
- Interview preparation assistant
- Skill gap recommendations


# Philosophy

Quality over quantity.

The goal is not applying to hundreds of jobs.


Sources:

- LinkedIn Jobs
- Pracuj.pl
- NoFluffJobs
- JustJoinIT
- Company career pages


2. Analyze jobs

For each job:

Extract:

- Company
- Role
- Location
- Salary
- Required skills
- Match score
- Missing skills


3. Generate application package


For selected jobs create:

- Tailored cover letter
- Recruiter message
- HR email
- Application answers


4. Track


Store:

- Company
- Position
- Date applied
- Source
- Status
- Follow-up date


# Project Architecture


```
job_search_assistant/

├── data/
│   └── profile.json

├── modules/

│   ├── scraper/
│   │   ├── job collectors
│   │
│   ├── matching/
│   │   ├── job scoring engine
│   │
│   ├── ai/
│   │   ├── message generation
│   │
│   ├── tracker/
│   │   ├── application database
│   │
│   └── analytics/
│       └── job market insights


├── main.py

└── docs/
```


# Future Improvements

Possible future features:

- CV keyword optimization
- Market trend analysis
- Salary comparison
- Company sponsorship database
- Interview preparation assistant
- Skill gap recommendations


# Philosophy

Quality over quantity.

The goal is not applying to hundreds of jobs.

The goal is identifying positions where my background has a high probability of success and creating strong personalized applications.a