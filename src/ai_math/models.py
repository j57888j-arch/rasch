from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    participants: Mapped[list["Participant"]] = relationship(back_populates="user")


class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=120)
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grade_thresholds: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    calibration_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    result_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    questions: Mapped[list["Question"]] = relationship(back_populates="exam", cascade="all, delete-orphan")
    participants: Mapped[list["Participant"]] = relationship(back_populates="exam", cascade="all, delete-orphan")


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("exam_id", "number", name="uq_exam_question_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    qtype: Mapped[str] = mapped_column(String(16))  # mcq4, mcq6, written2
    topic: Mapped[str] = mapped_column(String(120), default="Umumiy matematika")
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    answer_key: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    weights: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    exam: Mapped[Exam] = relationship(back_populates="questions")


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (UniqueConstraint("exam_id", "user_id", name="uq_exam_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="IN_PROGRESS")
    current_question: Mapped[int] = mapped_column(Integer, default=1)
    current_part: Mapped[str | None] = mapped_column(String(1), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="participants")
    exam: Mapped[Exam] = relationship(back_populates="participants")
    answers: Mapped[list["Answer"]] = relationship(back_populates="participant", cascade="all, delete-orphan")


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("participant_id", "item_code", name="uq_participant_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    item_code: Mapped[str] = mapped_column(String(12))  # Q01, Q36_A, Q36_B
    raw_answer: Mapped[str] = mapped_column(Text, default="")
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    weight: Mapped[int] = mapped_column(Integer, default=1)
    earned_points: Mapped[int] = mapped_column(Integer, default=0)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    participant: Mapped[Participant] = relationship(back_populates="answers")


class Calibration(Base):
    __tablename__ = "calibrations"
    __table_args__ = (UniqueConstraint("exam_id", "item_code", name="uq_exam_item_calibration"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    item_code: Mapped[str] = mapped_column(String(12))
    beta: Mapped[float] = mapped_column(Float)
    standard_error: Mapped[float] = mapped_column(Float)
    infit: Mapped[float] = mapped_column(Float)
    outfit: Mapped[float] = mapped_column(Float)
    correct_rate: Mapped[float] = mapped_column(Float)
    fit_status: Mapped[str] = mapped_column(String(24), default="REVIEW")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PersonMeasure(Base):
    __tablename__ = "person_measures"
    __table_args__ = (UniqueConstraint("exam_id", "participant_id", name="uq_exam_person_measure"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    theta: Mapped[float] = mapped_column(Float)
    standard_error: Mapped[float] = mapped_column(Float)
    rasch_score: Mapped[float] = mapped_column(Float)
    raw_points: Mapped[int] = mapped_column(Integer)
    max_points: Mapped[int] = mapped_column(Integer)
    raw_percent: Mapped[float] = mapped_column(Float)
    grade: Mapped[str] = mapped_column(String(32))
    provisional: Mapped[bool] = mapped_column(Boolean, default=True)
    answered_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReportExport(Base):
    __tablename__ = "report_exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_id: Mapped[str] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"), index=True)
    file_type: Mapped[str] = mapped_column(String(8))
    filename: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
