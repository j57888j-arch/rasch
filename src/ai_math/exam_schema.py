from __future__ import annotations

from dataclasses import dataclass
from typing import Any


EXPECTED_TYPES = {
    **{n: "mcq4" for n in range(1, 33)},
    **{n: "mcq6" for n in range(33, 36)},
    **{n: "written2" for n in range(36, 46)},
}
DEFAULT_THRESHOLDS = {"A+": 70, "A": 65, "B+": 60, "B": 55, "C+": 50, "C": 46}


class ExamValidationError(ValueError):
    pass


def validate_exam_payload(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ExamValidationError("JSON ildizi obyekt bo‘lishi kerak.")
    title = str(data.get("title", "")).strip()
    if not title:
        raise ExamValidationError("Imtihon nomi kiritilmagan.")
    questions = data.get("questions")
    if not isinstance(questions, list) or len(questions) != 45:
        raise ExamValidationError("Aynan 45 ta savol bo‘lishi shart.")

    by_number: dict[int, dict[str, Any]] = {}
    for q in questions:
        if not isinstance(q, dict):
            raise ExamValidationError("Har bir savol JSON obyekt bo‘lishi kerak.")
        try:
            number = int(q.get("number"))
        except (TypeError, ValueError):
            raise ExamValidationError("Savol raqami noto‘g‘ri.") from None
        if number in by_number:
            raise ExamValidationError(f"{number}-savol takrorlangan.")
        if number not in EXPECTED_TYPES:
            raise ExamValidationError(f"Savol raqami 1 dan 45 gacha bo‘lishi kerak: {number}.")
        by_number[number] = q

    if sorted(by_number) != list(range(1, 46)):
        missing = sorted(set(range(1, 46)) - set(by_number))
        raise ExamValidationError(f"Savollar ketma-ketligi to‘liq emas. Yetishmayotganlar: {missing}")

    normalized: list[dict[str, Any]] = []
    for number in range(1, 46):
        q = by_number[number]
        qtype = EXPECTED_TYPES[number]
        if q.get("type") != qtype:
            raise ExamValidationError(
                f"{number}-savol turi {qtype} bo‘lishi kerak, sizda {q.get('type')} kiritilgan."
            )
        prompt = str(q.get("prompt", "")).strip()
        if not prompt:
            raise ExamValidationError(f"{number}-savol matni bo‘sh.")
        topic = str(q.get("topic", "Umumiy matematika")).strip() or "Umumiy matematika"

        if qtype in ("mcq4", "mcq6"):
            expected = list("ABCD") if qtype == "mcq4" else list("ABCDEF")
            options = q.get("options")
            if not isinstance(options, dict) or set(options.keys()) != set(expected):
                raise ExamValidationError(
                    f"{number}-savolda variantlar aynan {', '.join(expected)} bo‘lishi kerak."
                )
            if any(not str(options[k]).strip() for k in expected):
                raise ExamValidationError(f"{number}-savolda bo‘sh variant bor.")
            correct = str(q.get("correct", "")).strip().upper()
            if correct not in expected:
                raise ExamValidationError(f"{number}-savolning to‘g‘ri javobi {', '.join(expected)} dan biri bo‘lishi kerak.")
            weight = _validate_weight(q.get("weight", 1), 1, 3, f"{number}-savol")
            normalized.append({
                "number": number, "qtype": qtype, "topic": topic, "prompt": prompt,
                "options": {k: str(options[k]) for k in expected},
                "answer_key": {"MAIN": correct}, "weights": {"MAIN": weight},
            })
        else:
            parts = q.get("parts")
            if not isinstance(parts, dict) or set(parts.keys()) != {"A", "B"}:
                raise ExamValidationError(f"{number}-savolda parts ichida A va B bo‘lishi shart.")
            part_data: dict[str, dict[str, Any]] = {}
            weights: dict[str, int] = {}
            for part in ("A", "B"):
                p = parts[part]
                if not isinstance(p, dict) or not str(p.get("prompt", "")).strip():
                    raise ExamValidationError(f"{number}-savol {part} qismi matni bo‘sh.")
                answer = str(p.get("answer", "")).strip()
                if not answer:
                    raise ExamValidationError(f"{number}-savol {part} qismi javobi kiritilmagan.")
                answer_type = str(p.get("answer_type", "numeric")).lower()
                if answer_type not in {"numeric", "fraction", "expression", "text"}:
                    raise ExamValidationError(f"{number}-{part}: answer_type numeric/fraction/expression/text bo‘lishi kerak.")
                weight = _validate_weight(p.get("weight", 4), 4, 6, f"{number}-{part}")
                tolerance = float(p.get("tolerance", 0))
                if tolerance < 0:
                    raise ExamValidationError(f"{number}-{part}: tolerance manfiy bo‘lishi mumkin emas.")
                part_data[part] = {
                    "prompt": str(p["prompt"]).strip(), "answer": answer,
                    "answer_type": answer_type, "tolerance": tolerance,
                }
                weights[part] = weight
            normalized.append({
                "number": number, "qtype": qtype, "topic": topic, "prompt": prompt,
                "options": None, "answer_key": part_data, "weights": weights,
            })

    thresholds = data.get("grade_thresholds", DEFAULT_THRESHOLDS)
    if not isinstance(thresholds, dict):
        raise ExamValidationError("grade_thresholds JSON obyekt bo‘lishi kerak.")
    parsed_thresholds = {str(k): float(v) for k, v in thresholds.items()}
    for grade, default in DEFAULT_THRESHOLDS.items():
        parsed_thresholds.setdefault(grade, float(default))
    order = [parsed_thresholds[g] for g in ("A+", "A", "B+", "B", "C+", "C")]
    if any(not 0 <= x <= 100 for x in order) or order != sorted(order, reverse=True):
        raise ExamValidationError("Baholash chegaralari A+ dan C gacha kamayish tartibida 0–100 oralig‘ida bo‘lishi kerak.")

    return {
        "title": title[:200],
        "description": str(data.get("description", "")).strip(),
        "duration_minutes": max(1, min(int(data.get("duration_minutes", 120)), 600)),
        "grade_thresholds": parsed_thresholds,
        "questions": normalized,
    }


def _validate_weight(value: Any, low: int, high: int, label: str) -> int:
    try:
        weight = int(value)
    except (TypeError, ValueError):
        raise ExamValidationError(f"{label}: ball butun son bo‘lishi kerak.") from None
    if not low <= weight <= high:
        raise ExamValidationError(f"{label}: ball {low} dan {high} gacha bo‘lishi kerak.")
    return weight
