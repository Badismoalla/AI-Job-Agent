"""
modules/application/
-----------------------
Application *preparation* adapters — one per submission channel (email) or
ATS platform (Greenhouse, Lever, SmartRecruiters, Workday).

Every adapter turns a JobListing + the GeneratedMessage objects already
produced for it (by modules.ai.claude_generator.ClaudeGenerator, via
core.pipeline) into a structured ApplicationResult describing exactly what
would be sent/submitted — subject/body/attachments for email, form-field
payloads for ATS platforms. No adapter in this package ever performs a
real send, a real HTTP submission, or any browser automation. That is a
deliberate scope boundary, not a placeholder: this package answers "what
would we submit, and does the data support it", not "submit it".

Naming note: modules/scraper/company/{greenhouse,lever,smartrecruiters,
workday}.py already exist and do something entirely different — they
*discover* job listings from each platform's public search API. The
modules here, despite sharing platform names, *prepare an application
submission payload* for a job the candidate has already decided to apply
to. Different concern, different direction of data flow — don't confuse
the two when reading either package.
"""
