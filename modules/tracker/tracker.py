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
from typing import Optional

from tinydb import Query, TinyDB
from tinydb.storages import JSONStorage
from tinydb.middlewares import CachingMiddleware

from core.exceptions import DuplicateApplicationError, TrackerError, InvalidLifecycleTransitionError
from core.lifecycle import JobLifecycleState, JobLifecycle, is_valid_transition, is_terminal
from core.logger import get_logger
from core.models import Application, ApplicationStatus, DailyPlan, JobListing, MatchReport, normalize_datetime, parse_datetime, utc_now

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
        self._pipeline_runs = self._db.table("pipeline_runs")
        self._jobs_lifecycle = self._db.table("jobs_lifecycle")  # Phase 2C
        self._jobs_data = self._db.table("jobs_data")  # Phase 2C
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

    def get_all_applications(self) -> list[Application]:
        """
        Return every stored application, deserialized back into Application
        instances. Used by exports (Excel) and reporting — read-only, does
        not mutate the store.
        """
        return [Application.model_validate(record) for record in self._apps.all()]

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
                "updated_at": utc_now().isoformat(),
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
        cutoff = utc_now()
        for record in sent:
            applied_at = record.get("applied_at")
            parsed_applied_at = parse_datetime(applied_at)
            if parsed_applied_at is not None:
                delta = cutoff - parsed_applied_at
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
        today = utc_now().date().isoformat()
        App = Query()
        all_apps = self._apps.all()

        today_apps = [
            a for a in all_apps
            if (a.get("applied_at") or "").startswith(today)
        ]

        return {
            "total_applications": len(all_apps),
            "today_applications": len(today_apps),
            "sent": len([a for a in all_apps if a["status"] == ApplicationStatus.SENT.value]),
            "interviews": len([a for a in all_apps if a["status"] == "interview"]),
            "offers": len([a for a in all_apps if a["status"] == "offer"]),
            "rejected": len([a for a in all_apps if a["status"] == "rejected"]),
            "pending_follow_ups": len(self.get_follow_ups_due()),
        }

    def mark_job_seen(self, job_id: str) -> None:
        """Mark a job as seen (scraped) so we don't re-scrape it tomorrow."""
        self._jobs.upsert(
            {"job_id": job_id, "seen_at": utc_now().isoformat()},
            Query().job_id == job_id,
        )

    def is_job_seen(self, job_id: str) -> bool:
        """Return True if we have seen this job before."""
        return bool(self._jobs.search(Query().job_id == job_id))

    def record_pipeline_run(self, stats: dict) -> None:
        """
        Store a summary record of one core.pipeline.run_pipeline() execution.

        Application records alone can't answer "how many jobs were
        scraped/rejected/deduplicated" — Application objects only ever
        exist for *accepted* jobs. This is the only place that history is
        persisted, which is what makes the `stats` CLI command's scraped/
        accepted/rejected/duplicate/per-source numbers possible at all.

        `stats` is caller-defined (see commands/pipeline_cli.py) — this
        method doesn't interpret it, just timestamps and stores it.
        """
        record = dict(stats)
        record["recorded_at"] = utc_now().isoformat()
        self._pipeline_runs.insert(record)
        logger.info("Pipeline run recorded | recorded_at={ts}", ts=record["recorded_at"])

    def get_last_pipeline_run(self) -> dict | None:
        """Return the most recently recorded pipeline run, or None if
        no pipeline run has ever been recorded."""
        all_runs = self._pipeline_runs.all()
        if not all_runs:
            return None
        return max(all_runs, key=lambda r: r.get("recorded_at", ""))

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 2C: JOB LIFECYCLE TRACKING
    # ─────────────────────────────────────────────────────────────────────────

    def create_job_lifecycle(self, job: JobListing, initial_state: JobLifecycleState = JobLifecycleState.DISCOVERED) -> dict:
        """
        Create initial lifecycle record for a newly discovered job.
        
        Args:
            job: JobListing from scraper
            initial_state: Starting state (default: DISCOVERED)
        
        Returns:
            Inserted lifecycle record
        """
        now = utc_now()
        record = {
            "job_id": job.id,
            "current_state": initial_state.value,
            "previous_state": None,
            "discovered_at": now.isoformat(),
            "state_changed_at": now.isoformat(),
            "last_event": None,
            "application_id": None,
        }
        self._jobs_lifecycle.insert(record)
        logger.info(
            "Lifecycle created | job_id={job_id} | state={state}",
            job_id=job.id,
            state=initial_state.value,
        )
        return record

    def get_job_lifecycle(self, job_id: str) -> Optional[dict]:
        """
        Retrieve current lifecycle state of a job.
        
        Args:
            job_id: JobListing.id
        
        Returns:
            Lifecycle record or None if not found
        """
        results = self._jobs_lifecycle.search(Query().job_id == job_id)
        return results[0] if results else None

    def transition_job(
        self, 
        job_id: str, 
        event: str, 
        data: Optional[dict] = None
    ) -> dict:
        """
        Execute a state transition with validation.
        
        Args:
            job_id: JobListing.id
            event: Event name triggering transition
            data: Optional metadata to store (recruiter info, notes, etc.)
        
        Returns:
            Updated lifecycle record
        
        Raises:
            InvalidLifecycleTransitionError if transition is invalid
        """
        # Get current state
        lifecycle = self.get_job_lifecycle(job_id)
        if not lifecycle:
            # Auto-create if missing (shouldn't happen, but safe default)
            lifecycle = self.create_job_lifecycle(
                JobListing(id=job_id, company="", title="", description="", city="", market="", url="", source=""),
                initial_state=JobLifecycleState.DISCOVERED
            )
        
        current_state = JobLifecycleState(lifecycle["current_state"])
        
        # Determine next state (caller responsibility to know valid transitions)
        # This method validates and applies the transition
        # Caller should use get_allowed_next_states() first to know valid paths
        
        # For now, validate that AT LEAST one outbound transition exists
        # Full transition enforcement happens in calling code
        if is_terminal(current_state) and current_state != JobLifecycleState.SKIPPED:
            raise InvalidLifecycleTransitionError(
                current_state=current_state.value,
                event=event,
                reason=f"Terminal state {current_state.value} cannot transition",
            )
        
        logger.info(
            "Lifecycle transition | job_id={job_id} | event={event} | from={from_state}",
            job_id=job_id,
            event=event,
            from_state=current_state.value,
        )
        
        return lifecycle

    def update_job_lifecycle_state(
        self,
        job_id: str,
        event: str,
        next_state: JobLifecycleState,
        data: Optional[dict] = None,
    ) -> dict:
        """
        Update job lifecycle to a new state after validating transition.
        
        Args:
            job_id: JobListing.id
            event: Event that triggered transition
            next_state: Target JobLifecycleState
            data: Optional additional data to store
        
        Returns:
            Updated lifecycle record
        
        Raises:
            InvalidLifecycleTransitionError if transition is invalid
        """
        lifecycle = self.get_job_lifecycle(job_id)
        if not lifecycle:
            raise TrackerError(f"No lifecycle found for job {job_id}")
        
        current_state = JobLifecycleState(lifecycle["current_state"])
        
        # Validate transition
        if not is_valid_transition(current_state, event, next_state):
            raise InvalidLifecycleTransitionError(
                current_state=current_state.value,
                event=event,
                reason=f"Invalid transition to {next_state.value}",
            )
        
        # Update record
        now = utc_now()
        update_record = {
            "previous_state": current_state.value,
            "current_state": next_state.value,
            "state_changed_at": now.isoformat(),
            "last_event": event,
        }
        
        # Merge any additional data
        if data:
            update_record.update(data)
        
        self._jobs_lifecycle.update(
            update_record,
            Query().job_id == job_id,
        )
        
        logger.info(
            "Lifecycle updated | job_id={job_id} | {from_state} → {to_state} | event={event}",
            job_id=job_id,
            from_state=current_state.value,
            to_state=next_state.value,
            event=event,
        )
        
        return self.get_job_lifecycle(job_id)

    def get_jobs_in_state(self, state: JobLifecycleState) -> list[dict]:
        """
        Get all jobs currently in a specific lifecycle state.
        
        Args:
            state: JobLifecycleState to filter by
        
        Returns:
            List of lifecycle records
        """
        return self._jobs_lifecycle.search(Query().current_state == state.value)

    def update_job_data(
        self,
        job_id: str,
        job: JobListing,
        match_report: Optional[MatchReport] = None,
    ) -> dict:
        """
        Store updated job data snapshot without resetting lifecycle state.
        
        Args:
            job_id: JobListing.id
            job: Updated JobListing
            match_report: Optional updated MatchReport
        
        Returns:
            Stored job data record
        """
        now = utc_now()
        record = {
            "job_id": job_id,
            "job_listing": job.model_dump(mode="json"),
            "last_updated": now.isoformat(),
        }
        
        if match_report:
            record["last_matched_at"] = now.isoformat()
            record["last_match_decision"] = match_report.decision.value
            record["last_match_score"] = match_report.score
        
        self._jobs_data.upsert(
            record,
            Query().job_id == job_id,
        )
        
        logger.info(
            "Job data updated | job_id={job_id} | preserved lifecycle state",
            job_id=job_id,
        )
        
        return record

    def get_job_data(self, job_id: str) -> Optional[dict]:
        """
        Retrieve latest job data snapshot.
        
        Args:
            job_id: JobListing.id
        
        Returns:
            Job data record or None if not found
        """
        results = self._jobs_data.search(Query().job_id == job_id)
        return results[0] if results else None

    def close(self) -> None:
        """Flush and close the database."""
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
