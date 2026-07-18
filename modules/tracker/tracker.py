"""
modules/tracker/tracker.py
--------------------------
Local application tracker using TinyDB (JSON file database).

Why TinyDB?
- Zero server setup — just a JSON file in data/
- Full query API (filter by status, date, company)
- Human-readable — you can open data/applications.db.json and read it
- Portable — copy the file, back it up, open in any text editor

Responsibilities:
- Store every application, never overwrite
- Detect duplicates before applying
- Track status changes (sent → interview → offer)
- Return applications due for follow-up
- Generate daily statistics
"""

from datetime import datetime
from pathlib import Path

from tinydb import Query, TinyDB
from tinydb.storages import JSONStorage
from tinydb.middlewares import CachingMiddleware

from core.exceptions import DuplicateApplicationError, TrackerError
from core.logger import get_logger
from core.models import Application, ApplicationStatus, DailyPlan

logger = get_logger(__name__)

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "applications.db.json"


class ApplicationTracker:
    """
    Persistent local tracker for all job applications.
    Thread-safe via TinyDB's caching middleware.
    """

    def __init__(self, db_path: Path = _DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = TinyDB(db_path, storage=CachingMiddleware(JSONStorage))
        self._apps = self._db.table("applications")
        self._jobs = self._db.table("jobs_seen")
        logger.info("Tracker initialised | db={path}", path=str(db_path))

    def add_application(self, application: Application) -> None:
        """
        Store a new application.
        Raises DuplicateApplicationError if already applied.
        """
        if self.already_applied(application.job.id):
            raise DuplicateApplicationError(
                job_id=application.job.id,
                company=application.job.company,
            )

        self._apps.insert(application.model_dump(mode="json"))
        logger.info(
            "Application logged | company={company} | role={role} | id={id}",
            company=application.job.company,
            role=application.job.title,
            id=application.id,
        )

    def already_applied(self, job_id: str) -> bool:
        """Return True if we have already applied to this job."""
        App = Query()
        result = self._apps.search(App.job.id == job_id)
        return len(result) > 0

    def update_status(self, application_id: str, status: ApplicationStatus) -> None:
        """Update the status of an existing application."""
        App = Query()
        self._apps.update(
            {
                "status": status.value,
                "updated_at": datetime.utcnow().isoformat(),
            },
            App.id == application_id,
        )
        logger.info(
            "Status updated | id={id} | status={status}",
            id=application_id,
            status=status.value,
        )

    def get_follow_ups_due(self, after_days: int = 7) -> list[dict]:
        """Return all sent applications older than N days with no response."""
        App = Query()
        sent = self._apps.search(App.status == ApplicationStatus.SENT.value)
        due = []
        cutoff = datetime.utcnow()
        for record in sent:
            applied_at = record.get("applied_at")
            if applied_at:
                delta = cutoff - datetime.fromisoformat(applied_at)
                if delta.days >= after_days:
                    due.append(record)
        logger.info(
            "Follow-ups due | count={count} | after_days={days}",
            count=len(due),
            days=after_days,
        )
        return due

    def daily_stats(self) -> dict:
        """Return stats for today's session and all-time totals."""
        today = datetime.utcnow().date().isoformat()
        App = Query()
        all_apps = self._apps.all()

        today_apps = [
            a for a in all_apps
            if a.get("applied_at", "").startswith(today)
        ]

        return {
            "total_applications": len(all_apps),
            "today_applications": len(today_apps),
            "interviews": len([a for a in all_apps if a["status"] == "interview"]),
            "offers": len([a for a in all_apps if a["status"] == "offer"]),
            "rejected": len([a for a in all_apps if a["status"] == "rejected"]),
            "pending_follow_ups": len(self.get_follow_ups_due()),
        }

    def mark_job_seen(self, job_id: str) -> None:
        """Mark a job as seen (scraped) so we don't re-scrape it tomorrow."""
        self._jobs.upsert(
            {"job_id": job_id, "seen_at": datetime.utcnow().isoformat()},
            Query().job_id == job_id,
        )

    def is_job_seen(self, job_id: str) -> bool:
        """Return True if we have seen this job before."""
        return bool(self._jobs.search(Query().job_id == job_id))

    def close(self) -> None:
        """Flush and close the database."""
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
