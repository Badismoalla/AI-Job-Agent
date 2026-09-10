"""
core/executor
--------------
Application execution (browser automation + user-assisted submission).

Supported platforms: Greenhouse, Lever, SmartRecruiters (browser
automation), and Email (user-assisted, no browser). Workday remains
out of scope — its adapter explicitly documents a multi-step wizard
that requires human guidance and was never a candidate for automation.
"""

from core.executor.base import ApplicationExecutor
from core.executor.email_executor import EmailExecutor
from core.executor.greenhouse_executor import GreenhouseExecutor
from core.executor.lever_executor import LeverExecutor
from core.executor.smartrecruiters_executor import SmartRecruitersExecutor
from core.executor.states import (
    ExecutionMode,
    ExecutionResult,
    ExecutionStatus,
    ValidationResult,
)
from core.executor.validator import PackageValidator

__all__ = [
    "ApplicationExecutor",
    "EmailExecutor",
    "GreenhouseExecutor",
    "LeverExecutor",
    "SmartRecruitersExecutor",
    "ExecutionMode",
    "ExecutionResult",
    "ExecutionStatus",
    "ValidationResult",
    "PackageValidator",
]
