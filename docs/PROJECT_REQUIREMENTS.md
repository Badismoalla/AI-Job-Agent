# AI Job Search Assistant - Project Requirements

## 1. Project Objective

Build a local AI-powered job search assistant that helps automate the job application workflow.

The system should:

- Search relevant job offers based on the user's CV and profile.
- Analyze job descriptions.
- Score compatibility between the user profile and job requirements.
- Generate personalized cover letters and application answers.
- Track all applications and their status.
- Reduce repetitive manual work during job searching.

The first version should run locally on the user's computer.

---

# 2. User Profile Source

The main profile source is:

data/profile.json

The system must use this file as the main reference for:

- Skills
- Experience
- Target roles
- Countries
- Visa requirements
- CV selection
- Application preferences

CV files are stored in:

resumes/

---

# 3. Main Features

## 3.1 Job Discovery

The system should collect job opportunities from:

Initial targets:

- LinkedIn
- Indeed
- NoFluffJobs
- JustJoinIT
- Pracuj
- Company career pages

Each job should store:

- Job title
- Company
- Location
- Country
- URL
- Description
- Required skills
- Salary if available
- Date discovered

---

## 3.2 Job Matching

Each job receives a compatibility score.

The score should consider:

- Technical skill match
- Years of experience
- Role relevance
- Location preference
- Visa sponsorship availability
- Salary expectations

Example:

90-100:
Strong match

75-89:
Good match

Below 75:
Low priority

---

## 3.3 Application Generation

For selected jobs, the system should generate:

- Personalized cover letter
- Recruiter message
- Answers to common application questions

The generated content should consider:

- Company
- Job description
- User experience
- User achievements

---

## 3.4 AI Response Cache

The system should avoid asking AI the same questions repeatedly.

Examples:

Question:
"Why do you want to join our company?"

The answer should be stored locally.

Future similar questions should reuse or adapt previous answers.

Storage:

cache/

---

## 3.5 Application Tracking

The system should track:

- Company
- Position
- Country
- Date applied
- CV used
- Cover letter used
- Application URL
- Current status

Possible statuses:

- Interested
- Ready to Apply
- Applied
- Interview
- Offer
- Rejected
- Ghosted

Database:

SQLite

Export:

Excel

---

## 3.6 Email Tracking

Future feature:

Connect to Gmail API.

The system should detect:

- Rejection emails
- Interview invitations
- Offers
- Follow-up emails

and update application status.

---

## 3.7 Recruiter Outreach

Future feature:

Find recruiters related to target jobs.

Store:

- Recruiter name
- Company
- LinkedIn URL
- Suggested message
- Contact status

---

# 4. Technical Requirements

Programming language:

Python 3.12


Main technologies:

- Playwright
- SQLite
- Pandas
- OpenPyXL
- PyMuPDF
- Pydantic
- pytest


The application should:

- Have clean architecture.
- Use modular components.
- Have logging.
- Use configuration files.
- Be easy to extend.

---

# 5. Development Strategy

## Phase 1 - MVP

Priority:

1. Project structure
2. Profile loading
3. Database creation
4. Job storage
5. Job matching
6. Excel export


## Phase 2

1. Job scraping
2. Cover letter generation
3. AI cache
4. Browser automation


## Phase 3

1. Gmail integration
2. Recruiter search
3. Dashboard
4. Automation scheduling


---

# 6. Important Rules

The system should:

- Avoid duplicate applications.
- Keep all data locally.
- Never overwrite previous applications.
- Keep logs.
- Allow manual approval before final submission.
- Be maintainable and extendable.
