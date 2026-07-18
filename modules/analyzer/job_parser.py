"""
modules/analyzer/job_parser.py
-------------------------------
Parses a raw job description text file into a JobListing model.

Supported file format (structured header + free-text body):
    Company: Bosch Engineering
    Role: Software Test & Validation Engineer
    Location: Wroclaw, Poland
    Market: Poland
    URL: https://jobs.bosch.com/job/...
    Visa Sponsorship: Yes

    ---

    [Free text job description follows]

The header fields are case-insensitive. The separator (---) is optional.
Everything after the separator (or after the last header line) is the
job description body fed into the matcher.

Why a dedicated parser module?
- Keeps all file I/O and text extraction out of main.py
- Independently testable (no CLI needed)
- Easy to extend: add new header fields without touching commands
- Single responsibility: text in → JobListing out

Usage:
    from modules.analyzer.job_parser import JobParser
    listing = JobParser.from_file(Path("jobs/bosch_test_engineer.txt"))
"""

import re
from pathlib import Path
from datetime import datetime

from slugify import slugify

from core.exceptions import JobSearchError
from core.logger import get_logger
from core.models import ApplicationSource, JobListing, Market

logger = get_logger(__name__)

# Market name → Market enum value mapping (case-insensitive)
_MARKET_MAP: dict[str, Market] = {
    "poland": Market.POLAND,
    "netherlands": Market.NETHERLANDS,
    "luxembourg": Market.LUXEMBOURG,
    "uae": Market.UAE,
    "united arab emirates": Market.UAE,
    "saudi arabia": Market.SAUDI_ARABIA,
    "ksa": Market.SAUDI_ARABIA,
    "qatar": Market.QATAR,
}

# Header field aliases — all map to a canonical key
_FIELD_ALIASES: dict[str, str] = {
    "company": "company",
    "role": "title",
    "title": "title",
    "job title": "title",
    "position": "title",
    "location": "location",
    "city": "location",
    "market": "market",
    "country": "market",
    "url": "url",
    "link": "url",
    "source url": "url",
    "visa sponsorship": "visa",
    "visa": "visa",
    "sponsorship": "visa",
    "salary": "salary",
    "salary range": "salary",
}


class ParseError(JobSearchError):
    """Raised when a JD file cannot be parsed into a JobListing."""
    pass


class JobParser:
    """
    Parses structured job description text files into JobListing objects.
    Use as a namespace — no instance state needed.
    """

    @classmethod
    def from_file(cls, path: Path) -> JobListing:
        """
        Load and parse a job description file.

        Args:
            path: Path to the .txt file containing the JD

        Returns:
            A JobListing ready to be evaluated by JobMatcher

        Raises:
            ParseError: if the file cannot be read or lacks required fields
        """
        if not path.exists():
            raise ParseError(
                f"Job description file not found: {path}\n"
                f"Create a .txt file with Company, Role, Location, and URL headers."
            )

        if path.suffix.lower() not in {".txt", ".md", ""}:
            raise ParseError(
                f"Unsupported file type: {path.suffix}. Use .txt or .md files."
            )

        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ParseError(f"Cannot read file {path}: {e}") from e

        if not raw.strip():
            raise ParseError(f"File is empty: {path}")

        logger.info("Parsing JD file | path={path}", path=str(path))
        return cls._parse(raw, source_path=path)

    @classmethod
    def from_text(cls, text: str, source_hint: str = "manual") -> JobListing:
        """
        Parse a job description from a raw string.
        Useful for testing and future web paste support.
        """
        return cls._parse(text, source_path=Path(source_hint))

    @classmethod
    def _parse(cls, raw: str, source_path: Path) -> JobListing:
        """Core parsing logic — shared by from_file and from_text."""
        header, body = cls._split_header_body(raw)
        fields = cls._extract_header_fields(header)

        # Validate required fields
        company = cls._require(fields, "company", source_path)
        title = cls._require(fields, "title", source_path)
        location = fields.get("location", "Unknown")
        url = fields.get("url", f"file://{source_path.resolve()}")
        market = cls._parse_market(fields.get("market", ""), location, source_path)
        visa = cls._parse_visa(fields.get("visa", "no"))
        salary = fields.get("salary")

        # Build the city from location (first part before comma)
        city = location.split(",")[0].strip() if "," in location else location.strip()

        # Generate deterministic ID from company + title + city
        job_id = slugify(f"{company}-{title}-{city}", separator="-")

        listing = JobListing(
            id=job_id,
            title=title.strip(),
            company=company.strip(),
            city=city,
            market=market,
            url=url.strip(),
            source=ApplicationSource.MANUAL,
            description=body.strip() if body.strip() else None,
            salary_range=salary,
            visa_sponsorship=visa,
        )

        logger.info(
            "JD parsed | company={company} | title={title} | market={market} | visa={visa}",
            company=listing.company,
            title=listing.title,
            market=listing.market,
            visa=listing.visa_sponsorship,
        )

        return listing

    @staticmethod
    def _split_header_body(raw: str) -> tuple[str, str]:
        """
        Split file into header section and description body.
        Separator is '---' on its own line. If no separator, use heuristic split.
        """
        # Try explicit separator first
        if "\n---" in raw or raw.startswith("---"):
            parts = re.split(r"\n---+\n?", raw, maxsplit=1)
            if len(parts) == 2:
                return parts[0], parts[1]

        # Heuristic: header ends at first blank line after structured fields
        lines = raw.splitlines()
        header_lines = []
        body_lines = []
        in_body = False

        for i, line in enumerate(lines):
            if in_body:
                body_lines.append(line)
                continue

            # A structured header line has a colon near the start
            is_header_line = bool(re.match(r"^[A-Za-z ]{1,25}:\s*.+", line))

            if not is_header_line and line.strip() == "" and header_lines:
                # First blank line after header content → switch to body
                in_body = True
                continue

            if not is_header_line and not line.strip() == "" and header_lines and not any(
                re.match(r"^[A-Za-z ]{1,25}:\s*.+", l) for l in lines[i:][:5]
            ):
                # Non-header content with no more headers coming → body
                in_body = True
                body_lines.append(line)
                continue

            header_lines.append(line)

        return "\n".join(header_lines), "\n".join(body_lines)

    @staticmethod
    def _extract_header_fields(header: str) -> dict[str, str]:
        """
        Extract key: value pairs from the header section.
        Normalises keys using _FIELD_ALIASES.
        """
        fields: dict[str, str] = {}
        for line in header.splitlines():
            match = re.match(r"^([A-Za-z ]{1,25}):\s*(.+)$", line)
            if not match:
                continue
            raw_key = match.group(1).strip().lower()
            value = match.group(2).strip()
            canonical = _FIELD_ALIASES.get(raw_key)
            if canonical:
                fields[canonical] = value

        return fields

    @staticmethod
    def _require(fields: dict, key: str, path: Path) -> str:
        """Return field value or raise ParseError with a helpful message."""
        value = fields.get(key, "").strip()
        if not value:
            raise ParseError(
                f"Missing required field '{key}' in {path.name}.\n"
                f"Add a line like '{key.capitalize()}: Your Value' at the top of the file."
            )
        return value

    @staticmethod
    def _parse_market(market_str: str, location: str, path: Path) -> Market:
        """
        Resolve market from the Market: field or infer from Location:.
        Raises ParseError if neither resolves to a known market.
        """
        # Try explicit market field first
        candidate = market_str.lower().strip()
        if candidate in _MARKET_MAP:
            return _MARKET_MAP[candidate]

        # Try inferring from location string
        location_lower = location.lower()
        for key, value in _MARKET_MAP.items():
            if key in location_lower:
                logger.info(
                    "Market inferred from location | location={loc} | market={mkt}",
                    loc=location,
                    mkt=value.value,
                )
                return value

        # GCC city heuristic
        gcc_cities = {"dubai", "abu dhabi", "riyadh", "doha", "manama", "muscat"}
        for city in gcc_cities:
            if city in location_lower:
                return Market.UAE

        raise ParseError(
            f"Cannot determine market from '{market_str}' or location '{location}' in {path.name}.\n"
            f"Add a line: Market: Poland  (or Netherlands, Luxembourg, UAE, Saudi Arabia, Qatar)"
        )

    @staticmethod
    def _parse_visa(visa_str: str) -> bool:
        """Parse 'Yes'/'No'/'True'/'False' to bool."""
        return visa_str.lower().strip() in {"yes", "true", "1", "y"}
