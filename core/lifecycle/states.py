"""
core/lifecycle/states.py
------------------------
Job Lifecycle State Machine: 13 states and their semantics.

States represent the journey of a job from discovery to resolution,
independent from matching decisions.
"""

from enum import Enum
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class JobLifecycleState(str, Enum):
    """13 lifecycle states for job applications."""
    
    # Discovery & evaluation phase
    DISCOVERED = "discovered"              # Initial: job found by scraper
    EVALUATED = "evaluated"                # Matcher ran, MatchDecision assigned
    
    # Application phase
    SHORTLISTED = "shortlisted"            # Selected for application
    APPLICATION_PREPARED = "application_prepared"  # Messages generated, ready to submit
    APPLIED = "applied"                    # Application submitted
    SKIPPED = "skipped"                    # Rejected (soft terminal)
    
    # Pipeline phase
    SCREENING = "screening"                # Company reviewing/scheduling screening
    INTERVIEW = "interview"                # Interview stage(s)
    OFFER = "offer"                        # Offer received
    
    # Terminal states
    REJECTED = "rejected"                  # Company rejected (hard terminal)
    WITHDRAWN = "withdrawn"                # Candidate withdrew (hard terminal)
    EXPIRED = "expired"                    # Deadline passed (hard terminal)
    CLOSED = "closed"                      # Job accepted (hard terminal)


class JobLifecycle(BaseModel):
    """Represents the lifecycle state of a job."""
    
    job_id: str = Field(description="JobListing.id")
    current_state: JobLifecycleState = Field(description="Current state")
    previous_state: Optional[JobLifecycleState] = Field(
        default=None,
        description="Previous state for debugging"
    )
    discovered_at: datetime = Field(description="When first discovered")
    state_changed_at: datetime = Field(description="When state last changed")
    last_event: Optional[str] = Field(
        default=None,
        description="Last event that triggered transition"
    )
    application_id: Optional[str] = Field(
        default=None,
        description="Link to Application.id if created"
    )
    
    class Config:
        use_enum_values = False  # Keep enum objects, not strings
