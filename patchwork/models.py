"""
Database and Pydantic models for Patchwork.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from sqlalchemy import String, Integer, Float, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pydantic import BaseModel, Field, ConfigDict


class Base(DeclarativeBase):
    pass


class JobStatus(str, Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    DIAGNOSING = "diagnosing"
    VERIFYING = "verifying"
    PR_OPENED = "pr_opened"
    FLAKY_RERUN = "flaky_rerun"
    FAILED = "failed"
    GUARDRAIL_REJECTED = "guardrail_rejected"


class HealingJobRecord(Base):
    __tablename__ = "healing_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repo_name: Mapped[str] = mapped_column(String(128), index=True)
    branch_name: Mapped[str] = mapped_column(String(128))
    commit_sha: Mapped[str] = mapped_column(String(64))
    workflow_name: Mapped[str] = mapped_column(String(128), default="CI")
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    installation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.QUEUED.value)
    
    # Context & Diagnosis
    failing_file: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    failing_line: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    root_cause: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    diff_patch: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Self-Correction Loop & Outcome
    attempts_count: Mapped[int] = mapped_column(Integer, default=0)
    pull_request_url: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    pull_request_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pr_merged: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Guardrails & Observability
    guardrail_violation: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    attempts: Mapped[List["PatchAttemptRecord"]] = relationship("PatchAttemptRecord", back_populates="job", cascade="all, delete-orphan")
    events: Mapped[List["JobEventRecord"]] = relationship("JobEventRecord", back_populates="job", cascade="all, delete-orphan", order_by="JobEventRecord.id")


class JobEventRecord(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("healing_jobs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    job: Mapped["HealingJobRecord"] = relationship("HealingJobRecord", back_populates="events")


class PatchAttemptRecord(Base):
    __tablename__ = "patch_attempts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("healing_jobs.id"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    patch_diff: Mapped[str] = mapped_column(Text)
    test_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    job: Mapped["HealingJobRecord"] = relationship("HealingJobRecord", back_populates="attempts")


class SettingRecord(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserSessionRecord(Base):
    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revealed_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["UserRecord"] = relationship("UserRecord")


# Pydantic Schemas for API / Dashboard
class PatchAttemptSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    attempt_number: int
    patch_diff: str
    test_output: Optional[str] = None
    passed: bool
    created_at: datetime


class JobEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    job_id: str
    stage: str
    level: str
    message: str
    details: Optional[str] = None
    created_at: datetime


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    repo_name: str
    branch_name: str
    commit_sha: str
    workflow_name: str
    run_id: int
    installation_id: Optional[int] = None
    status: str
    failing_file: Optional[str] = None
    failing_line: Optional[int] = None
    error_summary: Optional[str] = None
    root_cause: Optional[str] = None
    diff_patch: Optional[str] = None
    attempts_count: int
    pull_request_url: Optional[str] = None
    pull_request_number: Optional[int] = None
    pr_merged: bool
    guardrail_violation: Optional[str] = None
    tokens_used: int
    duration_seconds: float
    created_at: datetime
    attempts: List[PatchAttemptSchema] = []
    events: List[JobEventSchema] = []


class MetricsSummary(BaseModel):
    total_intercepted: int
    total_healed: int
    success_rate_percentage: float
    average_healing_time_seconds: float
    total_tokens_used: int
    flaky_runs_rerun: int
    guardrails_prevented: int
    recent_jobs: List[JobResponse] = []
