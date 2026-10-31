from __future__ import annotations

from collections import Counter
from datetime import datetime
from io import BytesIO
import re

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from xml.sax.saxutils import escape as xml_escape

from .models import Answer, Calibration, Exam, Participant, PersonMeasure, Question, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .services import item_definitions


def _excel_safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _safe_sheet_name(value: str) -> str:
    return re.sub(r"[\\/*?:\[\]]", "_", value)[:31] or "Sheet"


async def build_excel(session: AsyncSession, exam: Exam) -> BytesIO:
    qres = await session.execute(select(Question).where(Question.exam_id == exam.id).order_by(Question.number))
    questions = list(qres.scalars().all())
    pres = await session.execute(select(Participant).where(Participant.exam_id == exam.id).order_by(Participant.id))
    participants = list(pres.scalars().all())
    user_ids = [p.user_id for p in participants]
    users_by_id: dict[int, User] = {}
    if user_ids:
        ures = await session.execute(select(User).where(User.id.in_(user_ids)))
        users_by_id = {u.id: u for u in ures.scalars().all()}
    answers_res = await session.execute(
        select(Answer).join(Participant).where(Participant.exam_id == exam.id)
    )
    answers = list(answers_res.scalars().all())
    answer_map = {(a.participant_id, a.item_code): a for a in answers}
    cres = await session.execute(select(Calibration).where(Calibration.exam_id == exam.id))
    calibrations = {c.item_code: c for c in cres.scalars().all()}
    mres = await session.execute(select(PersonMeasure).where(PersonMeasure.exam_id == exam.id))
    measures = {m.participant_id: m for m in mres.scalars().all()}
    defs = item_definitions(questions)

    wb = Workbook()
    ws = wb.active
    ws.title = "Umumiy hisobot"
    navy = "173D64"
    teal = "DDF4EE"
    ws.append(["AI MATH — IMTIHON HISOBOTI"])
    ws["A1"].font = Font(size=16, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor=navy)
    ws.merge_cells("A1:D1")
    ws.append(["Imtihon", _excel_safe(exam.title)])
    ws.append(["Holat", exam.status])
    ws.append(["Yaratilgan sana", exam.created_at.isoformat() if exam.created_at else ""])
    ws.append(["Qatnashchilar", len(participants)])
    ws.append(["Savollar", 45])
    ws.append(["Rasch birliklari", len(defs)])
    scores = [measures[p.id].rasch_score for p in participants if p.id in measures]
    ws.append(["O‘rtacha Rasch ball", round(sum(scores) / len(scores), 2) if scores else "—"])
    grades = Counter(measures[p.id].grade for p in participants if p.id in measures)
    ws.append(["Kalibrlash holati", "DASTLABKI" if any(m.provisional for m in measures.values()) else "YAKUNIY / KALIBRLANGAN"])
    ws.append(["Model izohi", _excel_safe(exam.result_note or "")])
    ws.append([])
    ws.append(["Daraja", "Ishtirokchilar soni"])
    for grade in ["A+", "A", "B+", "B", "C+", "C", "Talabga yetmagan"]:
        ws.append([grade, grades.get(grade, 0)])
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 70

    results_ws = wb.create_sheet("Ishtirokchilar")
    headers = ["O‘rin", "Telegram ID", "Ism", "Username", "Xom ball", "Maks. ball", "Xom %", "Rasch %", "Theta", "SE", "Daraja", "Kalibrlash dastlabkimi?", "Javoblar"]
    results_ws.append(headers)
    result_rows = []
    for p in participants:
        u = users_by_id.get(p.user_id)
        m = measures.get(p.id)
        if not m:
            continue
        result_rows.append((p, u, m))
    result_rows.sort(key=lambda row: (row[2].rasch_score, row[2].raw_points), reverse=True)
    for rank, (p, u, m) in enumerate(result_rows, 1):
        results_ws.append([
            rank, u.telegram_id if u else "", _excel_safe(u.first_name if u else ""), _excel_safe(u.username if u else ""),
            m.raw_points, m.max_points, m.raw_percent, m.rasch_score, m.theta,
            m.standard_error, m.grade, "HA" if m.provisional else "YO‘Q", f"{m.answered_count}/55",
        ])

    matrix_ws = wb.create_sheet("55 javob matritsasi")
    item_codes = [d["code"] for d in defs]
    matrix_ws.append(["Telegram ID", "Ism"] + item_codes)
    for p, u, m in result_rows:
        row = [u.telegram_id if u else "", _excel_safe(u.first_name if u else "")]
        for code in item_codes:
            a = answer_map.get((p.id, code))
            row.append(1 if a and a.is_correct else 0)
        matrix_ws.append(row)

    item_ws = wb.create_sheet("Savollar tahlili")
    item_ws.append(["Birlik", "Savol", "Qism", "Mavzu", "Vazn", "To‘g‘ri %", "Beta", "Beta SE", "Infit", "Outfit", "Fit"])
    for d in defs:
        cal = calibrations.get(d["code"])
        response_values = [answer_map.get((p.id, d["code"])) for p in participants]
        correct_n = sum(1 for a in response_values if a and a.is_correct)
        item_ws.append([
            d["code"], d["question_number"], d["part"], _excel_safe(d["topic"]), d["weight"],
            round(100 * correct_n / max(len(participants), 1), 2),
            cal.beta if cal else None, cal.standard_error if cal else None,
            cal.infit if cal else None, cal.outfit if cal else None,
            cal.fit_status if cal else "N/A",
        ])

    written_ws = wb.create_sheet("Yozma javoblar")
    written_ws.append(["Telegram ID", "Ism", "Savol", "Qism", "Javob", "To‘g‘ri", "Ball", "Maks. ball"])
    for p, u, m in result_rows:
        for d in defs:
            if d["part"] not in ("A", "B"):
                continue
            a = answer_map.get((p.id, d["code"]))
            written_ws.append([
                u.telegram_id if u else "", _excel_safe(u.first_name if u else ""), d["question_number"], d["part"],
                _excel_safe(a.raw_answer if a else ""), bool(a and a.is_correct), a.earned_points if a else 0, d["weight"],
            ])

    for sheet in wb.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions if sheet.max_row > 1 else "A1"
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=navy)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.row_dimensions[1].height = 28
        for column in sheet.columns:
            values = [len(str(cell.value or "")) for cell in list(column)[:200]]
            width = min(max(max(values, default=10) + 2, 12), 34)
            sheet.column_dimensions[get_column_letter(column[0].column)].width = width
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="center", wrap_text=True)
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def _pdf_text(value: object) -> str:
    text = str(value if value is not None else "")
    # Built-in PDF fonts are not Unicode-complete; normalize Uzbek typographic apostrophes.
    text = (text.replace("‘", "'").replace("’", "'").replace("ʻ", "'")
            .replace("ʼ", "'").replace("–", "-").replace("—", "-")
            .replace("≤", "<=").replace("≥", ">=").replace("θ", "Theta").replace("β", "Beta"))
    return xml_escape(text)


async def build_pdf(session: AsyncSession, exam: Exam) -> BytesIO:
    pres = await session.execute(select(Participant).where(Participant.exam_id == exam.id))
    participants = list(pres.scalars().all())
    uids = [p.user_id for p in participants]
    users: dict[int, User] = {}
    if uids:
        ures = await session.execute(select(User).where(User.id.in_(uids)))
        users = {u.id: u for u in ures.scalars().all()}
    mres = await session.execute(select(PersonMeasure).where(PersonMeasure.exam_id == exam.id))
    measures = list(mres.scalars().all())
    by_pid = {m.participant_id: m for m in measures}
    grade_counts = Counter(m.grade for m in measures)
    average = sum(m.rasch_score for m in measures) / len(measures) if measures else 0

    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4), rightMargin=14 * mm, leftMargin=14 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TitleBlue", parent=styles["Title"], textColor=colors.HexColor("#173D64"), alignment=TA_CENTER))
    story = [
        Paragraph("AI MATH - IMTIHON HISOBOTI", styles["TitleBlue"]),
        Paragraph(_pdf_text(exam.title), styles["Heading2"]),
        Paragraph(f"Holat: {_pdf_text(exam.status)} | Qatnashchilar: {len(participants)} | Savollar: 45 | Rasch birliklari: 55", styles["Normal"]),
        Spacer(1, 5 * mm),
        Paragraph("Umumiy statistika", styles["Heading2"]),
    ]
    provisional_count = sum(1 for m in measures if m.provisional)
    summary = [["Ko'rsatkich", "Qiymat"], ["Rasch ball o'rtachasi", f"{average:.2f}/100"],
               ["Baholangan ishtirokchilar", str(len(measures))],
               ["Dastlabki kalibrlashdagi ishtirokchilar", str(provisional_count)],
               ["Model izohi", _pdf_text(exam.result_note or "Yo'q")]]
    table = Table(summary, colWidths=[55 * mm, 190 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173D64")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C8D5DE")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [table, Spacer(1, 5 * mm), Paragraph("Darajalar taqsimoti", styles["Heading2"])]
    grade_rows = [["Daraja", "Ishtirokchilar"]]
    for grade in ["A+", "A", "B+", "B", "C+", "C", "Talabga yetmagan"]:
        grade_rows.append([grade, str(grade_counts.get(grade, 0))])
    grade_table = Table(grade_rows, colWidths=[65 * mm, 65 * mm])
    grade_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173D64")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C8D5DE")),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [grade_table, Spacer(1, 5 * mm), Paragraph("Eng yuqori natijalar", styles["Heading2"])]
    sorted_rows = []
    for p in participants:
        m = by_pid.get(p.id)
        if m:
            u = users.get(p.user_id)
            sorted_rows.append((p, u, m))
    sorted_rows.sort(key=lambda x: x[2].rasch_score, reverse=True)
    rank_data = [["O'rin", "Ism", "Telegram ID", "Xom ball", "Rasch ball", "Theta", "Daraja"]]
    for rank, (p, u, m) in enumerate(sorted_rows[:30], 1):
        rank_data.append([str(rank), _pdf_text(u.first_name if u else ""), str(u.telegram_id if u else ""),
                          f"{m.raw_points}/{m.max_points}", f"{m.rasch_score:.2f}", f"{m.theta:.3f}", m.grade])
    rank_table = Table(rank_data, repeatRows=1, colWidths=[14 * mm, 55 * mm, 38 * mm, 28 * mm, 28 * mm, 23 * mm, 23 * mm])
    rank_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173D64")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#C8D5DE")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("PADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(rank_table)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Eslatma: Rasch natijalari model mosligi va namuna hajmi bilan birga talqin qilinishi kerak. Ushbu hisobot yakuniy imtihon ma'lumotlari asosida yaratildi.", styles["Italic"]))
    doc.build(story)
    output.seek(0)
    return output
