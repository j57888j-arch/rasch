from __future__ import annotations

import json
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
source = json.loads((ROOT / "data" / "exam_template.json").read_text(encoding="utf-8"))

wb = Workbook()
info = wb.active
info.title = "Exam Info"
info.append(["key", "value", "Izoh"])
rows = [
    ("title", source["title"], "Imtihon nomi — o‘zingizga moslab o‘zgartiring"),
    ("description", source["description"], "Imtihon haqida qisqa ma'lumot"),
    ("duration_minutes", source["duration_minutes"], "Har bir ishtirokchiga ajratiladigan vaqt"),
    ("threshold_Aplus", 70, "A+ minimal chegara (0–100)"),
    ("threshold_A", 65, "A minimal chegara"),
    ("threshold_Bplus", 60, "B+ minimal chegara"),
    ("threshold_B", 55, "B minimal chegara"),
    ("threshold_Cplus", 50, "C+ minimal chegara"),
    ("threshold_C", 46, "C minimal chegara"),
]
for row in rows:
    info.append(list(row))

qs = wb.create_sheet("Questions")
headers = ["number", "type", "topic", "prompt", "option_A", "option_B", "option_C", "option_D", "option_E", "option_F", "correct", "weight", "part_a_prompt", "part_a_answer", "part_a_type", "part_a_tolerance", "part_a_weight", "part_b_prompt", "part_b_answer", "part_b_type", "part_b_tolerance", "part_b_weight"]
qs.append(headers)
for q in source["questions"]:
    row = {h: "" for h in headers}
    row.update({"number": q["number"], "type": q["type"], "topic": q["topic"], "prompt": q["prompt"]})
    if q["type"].startswith("mcq"):
        for letter, value in q["options"].items():
            row[f"option_{letter}"] = value
        row["correct"] = q["correct"]
        row["weight"] = q["weight"]
    else:
        for part, prefix in (("A", "part_a"), ("B", "part_b")):
            p = q["parts"][part]
            row[f"{prefix}_prompt"] = p["prompt"]
            row[f"{prefix}_answer"] = p["answer"]
            row[f"{prefix}_type"] = p["answer_type"]
            row[f"{prefix}_tolerance"] = p["tolerance"]
            row[f"{prefix}_weight"] = p["weight"]
    qs.append([row[h] for h in headers])

notes = wb.create_sheet("Read me")
notes.append(["AI MATH — EXCEL IMTIHON SHABLONI"])
notes.append(["DIQQAT: Bu fayl ichida faqat NAMUNA savollar bor. Haqiqiy imtihon uchun barcha savol va javoblarni almashtiring."])
notes.append(["1–32: type=mcq4, A-D variantlaridan bittasi; weight 1-3."])
notes.append(["33–35: type=mcq6, A-F variantlaridan bittasi; weight 1-3."])
notes.append(["36–45: type=written2; part_a va part_b javoblari; har bir weight 4-6."])
notes.append(["Javob turlari: numeric, fraction, expression, text. Formula yozishda x*y kabi * belgisidan foydalaning."])
notes.append(["number ustunidagi 1–45 raqamlarni o‘zgartirmang. Barcha 45 qator bo‘lishi shart."])
notes.append(["Tayyor faylni botda ➕ Imtihon yuklash tugmasi orqali yuboring. XLSX ham qabul qilinadi."])

navy = "173D64"
mint = "DDF4EE"
white = "FFFFFF"
thin = Side(style="thin", color="D5E1E8")
for sheet in (info, qs, notes):
    sheet.freeze_panes = "A2"
    for cell in sheet[1]:
        cell.font = Font(bold=True, color=white)
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 30
    for row in sheet.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = Border(bottom=thin)
            if cell.row > 1 and cell.row % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F5FBF9")
    for column in sheet.columns:
        letter = get_column_letter(column[0].column)
        lengths = [len(str(c.value or "")) for c in list(column)[:100]]
        sheet.column_dimensions[letter].width = min(max(max(lengths, default=12) + 2, 14), 42)

qs.auto_filter.ref = qs.dimensions
notes.column_dimensions["A"].width = 115
info.column_dimensions["A"].width = 28
info.column_dimensions["B"].width = 90
info.column_dimensions["C"].width = 52

out = ROOT / "data" / "exam_template.xlsx"
wb.save(out)
print(out)
