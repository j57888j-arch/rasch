from __future__ import annotations

from io import BytesIO
from typing import Any

from openpyxl import load_workbook


class ExcelImportError(ValueError):
    pass


def exam_payload_from_xlsx(data: bytes) -> dict[str, Any]:
    try:
        wb = load_workbook(BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:
        raise ExcelImportError("Excel faylini o‘qib bo‘lmadi.") from exc
    if "Exam Info" not in wb.sheetnames or "Questions" not in wb.sheetnames:
        raise ExcelImportError("Excel faylida 'Exam Info' va 'Questions' varaqlari bo‘lishi kerak.")

    info_ws = wb["Exam Info"]
    info: dict[str, Any] = {}
    for row in info_ws.iter_rows(min_row=2, values_only=True):
        if row and row[0] is not None:
            info[str(row[0]).strip()] = row[1]

    qws = wb["Questions"]
    rows = qws.iter_rows(values_only=True)
    try:
        headers = [str(x).strip() if x is not None else "" for x in next(rows)]
    except StopIteration:
        raise ExcelImportError("Questions varag‘i bo‘sh.") from None
    index = {name: i for i, name in enumerate(headers) if name}

    def get(row: tuple, name: str, default: Any = "") -> Any:
        idx = index.get(name)
        if idx is None or idx >= len(row) or row[idx] is None:
            return default
        return row[idx]

    def as_float(value: Any, default: float) -> float:
        try:
            return float(value) if value not in (None, "") else default
        except (TypeError, ValueError):
            return default

    def as_int(value: Any, default: int) -> int:
        try:
            return int(value) if value not in (None, "") else default
        except (TypeError, ValueError):
            return default

    questions: list[dict[str, Any]] = []
    for row in rows:
        if not row or all(value is None for value in row):
            continue
        number_value = get(row, "number", None)
        if number_value is None:
            continue
        number = int(number_value)
        qtype = str(get(row, "type", "")).strip().lower()
        q: dict[str, Any] = {
            "number": number,
            "type": qtype,
            "topic": str(get(row, "topic", "Umumiy matematika") or "Umumiy matematika"),
            "prompt": str(get(row, "prompt", "") or ""),
        }
        if qtype in {"mcq4", "mcq6"}:
            letters = "ABCD" if qtype == "mcq4" else "ABCDEF"
            q["options"] = {letter: str(get(row, f"option_{letter}", "") or "") for letter in letters}
            q["correct"] = str(get(row, "correct", "") or "").strip().upper()
            q["weight"] = as_int(get(row, "weight", 1), 1)
        elif qtype == "written2":
            q["parts"] = {}
            for part, prefix in (("A", "part_a"), ("B", "part_b")):
                q["parts"][part] = {
                    "prompt": str(get(row, f"{prefix}_prompt", "") or ""),
                    "answer": str(get(row, f"{prefix}_answer", "") or ""),
                    "answer_type": str(get(row, f"{prefix}_type", "numeric") or "numeric").strip().lower(),
                    "tolerance": as_float(get(row, f"{prefix}_tolerance", 0), 0),
                    "weight": as_int(get(row, f"{prefix}_weight", 4), 4),
                }
        else:
            q["type"] = qtype
        questions.append(q)

    thresholds = {
        "A+": as_float(info.get("threshold_Aplus"), 70),
        "A": as_float(info.get("threshold_A"), 65),
        "B+": as_float(info.get("threshold_Bplus"), 60),
        "B": as_float(info.get("threshold_B"), 55),
        "C+": as_float(info.get("threshold_Cplus"), 50),
        "C": as_float(info.get("threshold_C"), 46),
    }
    wb.close()
    return {
        "title": str(info.get("title") or "").strip(),
        "description": str(info.get("description") or "").strip(),
        "duration_minutes": as_int(info.get("duration_minutes"), 120),
        "grade_thresholds": thresholds,
        "questions": questions,
    }
