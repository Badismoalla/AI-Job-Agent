"""
modules/ai/prompts.py
---------------------
All AI prompt templates in one place.

Why centralise prompts?
- Change positioning once, all message types update automatically
- Easy to A/B test different framings
- Version controlled — you can see exactly what changed
- The hiring persona is defined ONCE and imported everywhere

CARDINAL RULES baked into every prompt:
1. Always position as Test & Validation Engineer first
2. Never claim Data Analyst as primary identity
3. Never mention SSIS, Azure, CANoe as strengths
4. Always translate CANoe gap to DLT/Wireshark equivalent
5. BMW Group supply chain must appear (credibility anchor)
6. Metrics must appear: 40% / 25% / 100+ defects
7. Every message is personalised to company + role — never generic
"""

from core.profile import profile

# ── Hiring persona — used in ALL generated messages ──────────────────────────

HIRING_PERSONA = """
You are writing on behalf of Badis Moalla, an Automotive Software Test and Validation Engineer.

CANDIDATE IDENTITY (never deviate from this):
- Primary: Software Test & Validation Engineer specialising in Automotive Embedded Systems
- Background: 3+ years at KPIT Engineering within the BMW Group automotive supply chain
- Current title: Problem Manager - Process Improvement and Quality (KPIT Engineering)
- Core domain: AUTOSAR Adaptive ECU validation, ECU security testing, diagnostic protocols

CORE TECHNICAL STRENGTHS (always lead with these for testing roles):
- AUTOSAR Adaptive ECU validation
- ECU security domain: certificate installation, token verification, ECU access modes, secure boot
- Diagnostic protocols: UDS (ISO 14229), DoIP (ISO 13400), TCP/IP, SOME/IP
- DLT log analysis and Wireshark packet capture for embedded ECU debugging
- BMW Zedis diagnostic database
- Test Guide (TG) for test management
- ASPICE-aligned V-model environment, SWE.6 software qualification testing
- ISO 26262 functional safety (aware, worked within ISO 26262 environment)

PROCESS / AUTOMATION STRENGTHS (secondary — support the testing narrative):
- Python scripting for automation, deviation tracking, reporting
- Jira dashboards (built from scratch for BMW project teams)
- Power BI for QA metrics and defect trend analysis

PROVEN METRICS (use these — they are real and verified):
- Reduced manual reporting effort by 40% via Python automation
- Reduced incident resolution time by 25% via Power BI dashboards
- Detected 100+ diagnostic defects across multiple BMW ECU variants
- Reduced repeat incident rate by 30% over two quarters

LANGUAGES: Arabic (native), English (C1), French (C1)
RELOCATION: Open to immediate relocation — Poland, Netherlands, Luxembourg, UAE, Saudi Arabia, Qatar
VISA: Requires work permit sponsorship

DO NOT MENTION:
- SSIS (internship only — not a strength)
- Azure (not a real skill)
- CANoe or CANalyzer as a skill gap — instead say: "My diagnostic toolset for AUTOSAR Adaptive platforms is DLT Viewer and Wireshark, covering the same UDS/DoIP protocol analysis use cases"
- "Data Analyst" as the primary identity for testing roles
- Machine learning, AI, cloud architecture

TONE:
- Direct and confident — not humble, not boastful
- Technical and specific — no buzzwords
- Concise — every sentence must earn its place
- Personalised — reference the company's specific work, not generic praise
""".strip()


# ── System prompt for all message generation ──────────────────────────────────

SYSTEM_PROMPT = f"""
You are a professional job application writer specialising in automotive embedded systems engineering careers.
You write on behalf of a specific candidate. Never break character or add disclaimers.
Output only the requested message — no preamble, no "here is your cover letter", no markdown formatting unless requested.

{HIRING_PERSONA}
""".strip()


# ── Message prompt templates ──────────────────────────────────────────────────

def cover_letter_prompt(
    job_title: str,
    company: str,
    city: str,
    market: str,
    recruiter_name: str | None,
    company_detail: str | None,
    gap_mitigations: dict[str, str],
    matched_keywords: list[str],
    matched_protocols: list[str],
) -> str:
    """Generate the user-turn prompt for cover letter generation."""

    visa_phrase = {
        "Poland": "I require a work permit to relocate to Poland and am prepared to support all documentation.",
        "Netherlands": "I require a Kennismigrant work permit and understand this is a standard process for recognised sponsors.",
        "Luxembourg": "I require a work permit and am available to relocate immediately upon approval.",
        "UAE": "I am open to visa sponsorship as part of the employment package.",
        "Saudi Arabia": "I am open to visa sponsorship as part of the employment package.",
        "Qatar": "I am open to visa sponsorship as part of the employment package.",
    }.get(market, "I require work permit sponsorship and am available to relocate immediately.")

    gap_section = ""
    if gap_mitigations:
        mitigations_text = "\n".join(
            f"- If '{gap}' appears as a requirement, use this mitigation: {text}"
            for gap, text in gap_mitigations.items()
        )
        gap_section = f"\nSKILL GAP HANDLING:\n{mitigations_text}\n"

    return f"""
Write a professional cover letter for this application.

ROLE: {job_title}
COMPANY: {company}
CITY: {city}
MARKET: {market}
RECRUITER NAME: {recruiter_name or 'Hiring Team'}
COMPANY DETAIL: {company_detail or 'Not provided — do not invent specific details, use general technical alignment'}

MATCHING CONTEXT:
- JD matched these keywords from my background: {', '.join(matched_keywords[:6]) or 'general testing/validation match'}
- Protocol overlap found: {', '.join(matched_protocols[:4]) or 'none specific'}

{gap_section}

VISA STATEMENT TO USE: {visa_phrase}

FORMAT:
- Subject line: Application – {job_title} | Badis Moalla
- Salutation: Dear {recruiter_name or 'Hiring Team'},
- 3 paragraphs: (1) who I am + why this role, (2) specific technical match, (3) relocation + call to action
- Closing: Best regards, Badis Moalla / +216 24 960 735 / BadisMoalla@gmail.com / linkedin.com/in/badismoalla
- Length: 250-320 words maximum
- No bullet points in the letter body
- Do not start with "I am writing to" — use a stronger opening
""".strip()


def recruiter_inmail_prompt(
    job_title: str,
    company: str,
    city: str,
    recruiter_name: str,
    gap_mitigations: dict[str, str],
) -> str:
    """Generate the user-turn prompt for a LinkedIn InMail."""
    return f"""
Write a LinkedIn InMail message to a recruiter.

RECRUITER NAME: {recruiter_name}
COMPANY: {company}
CITY: {city}
ROLE I AM INTERESTED IN: {job_title}

RULES:
- Maximum 120 words — LinkedIn InMail must be short
- First sentence: reference {company}'s work specifically (not generic)
- Second paragraph: 2-3 sentences on my most relevant technical skills for this role
- End with: "Would you be open to a quick call to see if there's a fit?"
- Sign off: Badis Moalla / linkedin.com/in/badismoalla
- Do NOT mention visa sponsorship in the InMail — save for the call
- Do NOT list certifications
- Sound like a person, not an HR document
""".strip()


def hr_email_prompt(
    job_title: str,
    company: str,
    city: str,
    market: str,
    recruiter_name: str | None,
    company_detail: str | None,
    gap_mitigations: dict[str, str],
) -> str:
    """Generate the user-turn prompt for a direct HR email."""
    gap_section = ""
    if gap_mitigations:
        mitigations_text = "\n".join(
            f"- {gap}: {text}" for gap, text in gap_mitigations.items()
        )
        gap_section = f"\nHANDLE THESE GAPS AS FOLLOWS:\n{mitigations_text}\n"

    return f"""
Write a direct HR email applying for a job.

SUBJECT LINE: Application – {job_title} | Badis Moalla
TO: {recruiter_name or 'HR Team'}
COMPANY: {company}
CITY: {city}
MARKET: {market}
COMPANY DETAIL: {company_detail or 'Not provided'}

{gap_section}

FORMAT:
- Subject line first, then email body
- 200-270 words
- Paragraph 1: Role + why this company specifically
- Paragraph 2: Core technical background (AUTOSAR Adaptive, UDS, DoIP, DLT, Test Guide, ASPICE)
- Paragraph 3: Process/automation skills (Python, Jira, Power BI) as complement to testing
- Paragraph 4 (2 sentences): Relocation + visa statement + call to action
- Sign off: Best regards, Badis Moalla | +216 24 960 735 | BadisMoalla@gmail.com
- Attach note: "I have attached my CV."
- No bullet points
""".strip()


def follow_up_prompt(
    job_title: str,
    company: str,
    recruiter_name: str | None,
    days_since_applied: int,
) -> str:
    """Generate the user-turn prompt for a follow-up email."""
    return f"""
Write a follow-up email for a job application with no response.

ORIGINAL ROLE: {job_title}
COMPANY: {company}
CONTACT: {recruiter_name or 'Hiring Team'}
DAYS SINCE APPLIED: {days_since_applied}

RULES:
- Maximum 100 words
- Polite, not desperate, not pushy
- Confirm receipt of application
- Restate one concrete reason for interest (technical match — not generic)
- One clear call to action: "Would you be available for a brief call?"
- Subject line: Follow-up: {job_title} Application | Badis Moalla
- Do not apologise for following up
""".strip()


def hiring_manager_prompt(
    job_title: str,
    company: str,
    manager_name: str,
    team_context: str | None,
) -> str:
    """Generate the user-turn prompt for a direct hiring manager message."""
    return f"""
Write a direct LinkedIn message to a hiring manager (not HR).

MANAGER NAME: {manager_name}
COMPANY: {company}
TEAM/CONTEXT: {team_context or 'engineering/validation team'}
ROLE: {job_title}

RULES:
- Maximum 100 words
- Lead with a specific observation about their team or product (not generic)
- One sentence on my most relevant technical credential for their domain
- Ask for 15 minutes, not "consideration for a role"
- Do NOT sound like a cover letter
- Tone: peer-to-peer, not candidate-to-gatekeeper
""".strip()


def application_answer_prompt(
    question: str,
    job_title: str,
    company: str,
    max_words: int,
) -> str:
    """Generate the user-turn prompt to answer a specific application question."""
    return f"""
Answer this job application question on behalf of the candidate.

QUESTION: {question}
ROLE APPLYING FOR: {job_title}
COMPANY: {company}
MAXIMUM WORDS: {max_words}

RULES:
- Answer specifically and technically — no generic platitudes
- If the question is about automotive/testing experience, lead with AUTOSAR Adaptive, UDS, DoIP, DLT, ASPICE
- If the question is about process/improvement, lead with Python automation (40% effort reduction) and Jira dashboards
- If the question is about motivation, reference the BMW Group supply chain background and desire to apply it in {company}'s domain
- Do not mention skills the candidate does not have (no Azure, no CANoe, no machine learning)
- Under {max_words} words, no bullet points unless the question specifically asks for a list
""".strip()


def job_score_explanation_prompt(
    job_title: str,
    company: str,
    score: int,
    decision: str,
    tier: str,
    matched_keywords: list[str],
    gaps: list[str],
    mitigations: dict[str, str],
) -> str:
    """Generate the user-turn prompt for a human-readable match explanation."""
    return f"""
Write a brief match analysis for this job application decision.

JOB: {job_title} at {company}
MATCH SCORE: {score}/100
DECISION: {decision}
TIER: {tier}
MATCHED KEYWORDS: {', '.join(matched_keywords) or 'none'}
SKILL GAPS: {', '.join(gaps) or 'none'}
MITIGATIONS: {'; '.join(f"{k}: {v[:80]}..." for k, v in mitigations.items()) or 'none'}

Write 3 bullet points:
1. WHY THIS MATCHES: (specific technical reasons)
2. GAPS TO ADDRESS: (honest gaps and how to handle them in the message)
3. RECOMMENDED ACTION: (what to do and how to frame the application)

Keep each bullet to 1-2 sentences. Be specific, not generic.
""".strip()
