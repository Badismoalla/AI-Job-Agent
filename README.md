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

The goal is identifying positions where my background has a high probability of success and creating strong personalized applications.