from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import asyncio

from .exam_schema import validate_exam_payload
from .models import Answer, Calibration, Exam, Participant, PersonMeasure, Question, User, utcnow
from .rasch import calibrate_1pl, grade_for_score, scaled_expected_score
from .checker import check_answer


def item_definitions(questions: list[Question]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for q in sorted(questions, key=lambda x: x.number):
        if q.qtype in ("mcq4", "mcq6"):
            items.append({
                "code": f"Q{q.number:02d}", "weight": int(q.weights.get("MAIN", 1)),
                "expected": q.answer_key.get("MAIN", ""), "answer_type": "option", "tolerance": 0,
                "question_number": q.number, "part": "MAIN", "topic": q.topic,
            })
        else:
            for part in ("A", "B"):
                p = q.answer_key[part]
                items.append({
                    "code": f"Q{q.number:02d}_{part}", "weight": int(q.weights.get(part, 4)),
                    "expected": p["answer"], "answer_type": p.get("answer_type", "numeric"),
                    "tolerance": float(p.get("tolerance", 0)),
                    "question_number": q.number, "part": part, "topic": q.topic,
                })
    return items


async def create_exam_from_payload(
    session: AsyncSession, raw: dict[str, Any], admin_id: int
) -> Exam:
    payload = validate_exam_payload(raw)
    exam = Exam(
        id=str(uuid.uuid4()), title=payload["title"], description=payload["description"],
        status="DRAFT", duration_minutes=payload["duration_minutes"], created_by=admin_id,
        grade_thresholds=payload["grade_thresholds"],
    )
    session.add(exam)
    await session.flush()
    for q in payload["questions"]:
        session.add(Question(
            exam_id=exam.id, number=q["number"], qtype=q["qtype"], topic=q["topic"],
            prompt=q["prompt"], options=q["options"], answer_key=q["answer_key"], weights=q["weights"],
        ))
    await session.commit()
    await session.refresh(exam)
    return exam


async def ensure_user(
    session: AsyncSession, telegram_id: int, username: str | None, first_name: str
) -> User:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_id=telegram_id, username=username, first_name=first_name or "Foydalanuvchi")
        session.add(user)
    else:
        user.username = username
        user.first_name = first_name or user.first_name
        user.last_active_at = utcnow()
    await session.commit()
    await session.refresh(user)
    return user


async def get_open_exam(session: AsyncSession) -> Exam | None:
    result = await session.execute(
        select(Exam).where(Exam.status == "OPEN").order_by(Exam.opened_at.desc())
    )
    return result.scalars().first()


async def start_participant(session: AsyncSession, exam_id: str, user_id: int) -> Participant:
    result = await session.execute(
        select(Participant).where(Participant.exam_id == exam_id, Participant.user_id == user_id)
    )
    participant = result.scalar_one_or_none()
    if participant is not None:
        if participant.status in {"SUBMITTED", "AUTO_SUBMITTED"}:
            return participant
        participant.last_activity_at = utcnow()
    else:
        participant = Participant(exam_id=exam_id, user_id=user_id)
        session.add(participant)
    await session.commit()
    await session.refresh(participant)
    return participant


async def get_participant(
    session: AsyncSession, exam_id: str, user_id: int
) -> Participant | None:
    result = await session.execute(
        select(Participant).where(Participant.exam_id == exam_id, Participant.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_question(session: AsyncSession, exam_id: str, number: int) -> Question | None:
    result = await session.execute(
        select(Question).where(Question.exam_id == exam_id, Question.number == number)
    )
    return result.scalar_one_or_none()


async def get_answer(
    session: AsyncSession, participant_id: int, item_code: str
) -> Answer | None:
    result = await session.execute(
        select(Answer).where(Answer.participant_id == participant_id, Answer.item_code == item_code)
    )
    return result.scalar_one_or_none()


async def save_answer(
    session: AsyncSession, participant: Participant, item_code: str, raw_answer: str, weight: int
) -> Answer:
    answer = await get_answer(session, participant.id, item_code)
    if answer is None:
        answer = Answer(
            participant_id=participant.id, item_code=item_code, raw_answer=raw_answer,
            weight=weight, is_correct=None, earned_points=0,
        )
        session.add(answer)
    else:
        answer.raw_answer = raw_answer
        answer.weight = weight
        answer.is_correct = None
        answer.earned_points = 0
        answer.submitted_at = utcnow()
    participant.last_activity_at = utcnow()
    await session.commit()
    await session.refresh(answer)
    return answer


async def list_exams(session: AsyncSession, limit: int = 10) -> list[Exam]:
    result = await session.execute(select(Exam).order_by(Exam.created_at.desc()).limit(limit))
    return list(result.scalars().all())


async def exam_participant_count(session: AsyncSession, exam_id: str) -> tuple[int, int]:
    total = await session.scalar(select(func.count(Participant.id)).where(Participant.exam_id == exam_id)) or 0
    submitted = await session.scalar(
        select(func.count(Participant.id)).where(
            Participant.exam_id == exam_id,
            Participant.status.in_(["SUBMITTED", "AUTO_SUBMITTED"]),
        )
    ) or 0
    return int(total), int(submitted)


async def open_exam(session: AsyncSession, exam_id: str) -> Exam:
    other = await session.execute(select(Exam).where(Exam.status == "OPEN", Exam.id != exam_id))
    if other.scalars().first() is not None:
        raise ValueError("Avval ochiq turgan imtihonni yoping.")
    result = await session.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise ValueError("Imtihon topilmadi.")
    if exam.status not in {"DRAFT", "PAUSED"}:
        raise ValueError("Faqat tayyor yoki vaqtincha to‘xtatilgan imtihonni ochish mumkin.")
    qcount = await session.scalar(select(func.count(Question.id)).where(Question.exam_id == exam.id)) or 0
    if int(qcount) != 45:
        raise ValueError("Imtihonda 45 ta savol bo‘lishi shart.")
    exam.status = "OPEN"
    if exam.opened_at is None:
        exam.opened_at = utcnow()
    await session.commit()
    await session.refresh(exam)
    return exam


async def close_exam(session: AsyncSession, exam_id: str) -> Exam:
    result = await session.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise ValueError("Imtihon topilmadi.")
    if exam.status != "OPEN":
        raise ValueError("Faqat ochiq imtihonni yopish mumkin.")
    exam.status = "CLOSED"
    exam.closed_at = utcnow()
    parts = await session.execute(select(Participant).where(Participant.exam_id == exam_id))
    for participant in parts.scalars().all():
        if participant.status == "IN_PROGRESS":
            participant.status = "AUTO_SUBMITTED"
            participant.submitted_at = utcnow()
    await session.commit()
    await session.refresh(exam)
    return exam


async def grade_exam(session: AsyncSession, exam_id: str, min_sample: int = 30) -> dict[str, Any]:
    result = await session.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise ValueError("Imtihon topilmadi.")
    if exam.status not in {"CLOSED", "GRADING", "REVIEW"}:
        raise ValueError("Imtihon avval yopilishi kerak.")
    exam.status = "GRADING"
    await session.commit()

    qres = await session.execute(select(Question).where(Question.exam_id == exam_id).order_by(Question.number))
    questions = list(qres.scalars().all())
    item_defs = item_definitions(questions)
    pres = await session.execute(
        select(Participant).options(selectinload(Participant.user)).where(Participant.exam_id == exam_id)
    )
    participants = list(pres.scalars().all())
    if not participants:
        exam.status = "REVIEW"
        exam.result_note = "Imtihonda qatnashchilar yo‘q."
        await session.commit()
        return {"participants": 0, "warnings": ["Qatnashchilar topilmadi."], "results": []}

    answers_res = await session.execute(
        select(Answer).join(Participant).where(Participant.exam_id == exam_id)
    )
    answer_map = {(a.participant_id, a.item_code): a for a in answers_res.scalars().all()}

    # Materialize every response unit. Missing answers from started participants count as incorrect.
    matrix: list[list[int]] = []
    raw_points_by_person: list[int] = []
    answered_count_by_person: list[int] = []
    max_points = sum(item["weight"] for item in item_defs)
    for participant in participants:
        row: list[int] = []
        raw_points = 0
        answered_count = 0
        for item in item_defs:
            answer = answer_map.get((participant.id, item["code"]))
            raw = answer.raw_answer if answer else ""
            if raw.strip():
                answered_count += 1
            if item["answer_type"] == "option":
                correct = raw.strip().upper() == str(item["expected"]).strip().upper()
            else:
                correct = check_answer(
                    raw, str(item["expected"]), item["answer_type"], float(item["tolerance"])
                )
            if answer is None:
                answer = Answer(
                    participant_id=participant.id, item_code=item["code"], raw_answer="",
                    weight=item["weight"], is_correct=False, earned_points=0,
                )
                session.add(answer)
                answer_map[(participant.id, item["code"])] = answer
            else:
                answer.weight = item["weight"]
                answer.is_correct = correct
                answer.earned_points = item["weight"] if correct else 0
            row.append(1 if correct else 0)
            if correct:
                raw_points += item["weight"]
        matrix.append(row)
        raw_points_by_person.append(raw_points)
        answered_count_by_person.append(answered_count)
    await session.flush()

    from .rasch import calibrate_1pl
    calibration = await asyncio.to_thread(
        calibrate_1pl, matrix, [item["code"] for item in item_defs], 50, 1e-4, -4.0, 4.0, 31
    )

    # Replace prior results for safe, repeatable re-grading.
    old_cal = await session.execute(select(Calibration).where(Calibration.exam_id == exam_id))
    for old in old_cal.scalars().all():
        await session.delete(old)
    old_measures = await session.execute(select(PersonMeasure).where(PersonMeasure.exam_id == exam_id))
    for old in old_measures.scalars().all():
        await session.delete(old)
    await session.flush()

    for i, item in enumerate(item_defs):
        correct_rate = sum(row[i] for row in matrix) / len(matrix)
        session.add(Calibration(
            exam_id=exam_id, item_code=item["code"], beta=calibration.betas[i],
            standard_error=calibration.item_se[i], infit=calibration.infit[i],
            outfit=calibration.outfit[i], correct_rate=correct_rate,
            fit_status=calibration.fit_status[i],
        ))

    results: list[dict[str, Any]] = []
    provisional = len(participants) < min_sample or not calibration.converged
    for i, participant in enumerate(participants):
        theta = calibration.theta[i]
        rasch_score = scaled_expected_score(theta, calibration.betas, [x["weight"] for x in item_defs])
        raw_points = raw_points_by_person[i]
        raw_percent = 100.0 * raw_points / max_points if max_points else 0.0
        grade = grade_for_score(rasch_score, exam.grade_thresholds or {})
        existing = await session.execute(
            select(PersonMeasure).where(
                PersonMeasure.exam_id == exam_id, PersonMeasure.participant_id == participant.id
            )
        )
        measure = existing.scalar_one_or_none()
        if measure is None:
            measure = PersonMeasure(exam_id=exam_id, participant_id=participant.id)
            session.add(measure)
        measure.theta = theta
        measure.standard_error = calibration.person_se[i]
        measure.rasch_score = rasch_score
        measure.raw_points = raw_points
        measure.max_points = max_points
        measure.raw_percent = raw_percent
        measure.grade = grade
        measure.provisional = provisional
        measure.answered_count = answered_count_by_person[i]
        results.append({
            "participant_id": participant.id,
            "telegram_id": participant.user.telegram_id if participant.user else None,
            "name": participant.user.first_name if participant.user else "Foydalanuvchi",
            "username": participant.user.username if participant.user else None,
            "theta": theta,
            "se": calibration.person_se[i],
            "rasch_score": rasch_score,
            "raw_points": raw_points,
            "max_points": max_points,
            "raw_percent": raw_percent,
            "grade": grade,
            "answered_count": answered_count_by_person[i],
            "provisional": provisional,
        })

    exam.status = "REVIEW"
    exam.calibration_id = str(uuid.uuid4())
    exam.result_note = " | ".join(calibration.warnings) if calibration.warnings else "Kalibrlash yakunlandi."
    await session.commit()
    results.sort(key=lambda x: (x["rasch_score"], x["raw_points"]), reverse=True)
    for rank, item in enumerate(results, 1):
        item["rank"] = rank
    return {
        "participants": len(participants), "items": len(item_defs), "max_points": max_points,
        "provisional": provisional, "warnings": calibration.warnings,
        "iterations": calibration.iterations, "converged": calibration.converged,
        "results": results, "item_defs": item_defs,
        "betas": calibration.betas, "item_se": calibration.item_se,
        "infit": calibration.infit, "outfit": calibration.outfit,
        "fit_status": calibration.fit_status, "matrix": matrix,
    }


async def publish_exam(session: AsyncSession, exam_id: str) -> Exam:
    result = await session.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None or exam.status != "REVIEW":
        raise ValueError("Natijalarni e'lon qilishdan oldin hisoblash yakunlanishi kerak.")
    exam.status = "PUBLISHED"
    exam.published_at = utcnow()
    await session.commit()
    await session.refresh(exam)
    return exam


async def get_user_result(session: AsyncSession, exam_id: str, user_id: int) -> tuple[Exam, PersonMeasure] | None:
    result = await session.execute(
        select(Exam, PersonMeasure)
        .join(PersonMeasure, PersonMeasure.exam_id == Exam.id)
        .join(Participant, Participant.id == PersonMeasure.participant_id)
        .where(Exam.id == exam_id, Exam.status == "PUBLISHED", Participant.user_id == user_id)
    )
    row = result.first()
    return (row[0], row[1]) if row else None


async def get_latest_published_result(session: AsyncSession, user_id: int) -> list[tuple[Exam, PersonMeasure]]:
    result = await session.execute(
        select(Exam, PersonMeasure)
        .join(PersonMeasure, PersonMeasure.exam_id == Exam.id)
        .join(Participant, Participant.id == PersonMeasure.participant_id)
        .where(Exam.status == "PUBLISHED", Participant.user_id == user_id)
        .order_by(Exam.published_at.desc())
        .limit(10)
    )
    return [(row[0], row[1]) for row in result.all()]
