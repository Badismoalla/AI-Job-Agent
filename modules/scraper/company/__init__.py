"""
modules/scraper/company/
-------------------------
Scrapers for company career pages hosted on major ATS (Applicant Tracking
System) platforms — Greenhouse, Lever, SmartRecruiters, Workday. Each
platform gets its own module; `base.py` holds the shared abstraction
(`CompanyJobScraper`, extending `BaseJobScraper`) and the
`config/company_sources.json` loader every platform module uses.

Unlike the job-board scrapers in modules/scraper/ (NoFluffJobs, Pracuj.pl,
JustJoin.it), each of these targets exactly one company at a time — that's
the natural unit each ATS's own public API exposes. See base.py for why.
"""
