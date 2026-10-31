import json
import math
from pathlib import Path

from ai_math.checker import check_answer
from ai_math.exam_schema import validate_exam_payload
from ai_math.rasch import calibrate_1pl, grade_for_score, scaled_expected_score


DATA = Path(__file__).resolve().parents[1] / "data" / "exam_template.json"


def test_template_has_45_questions_and_55_units():
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    clean = validate_exam_payload(payload)
    assert len(clean["questions"]) == 45
    units = sum(1 if q["qtype"].startswith("mcq") else 2 for q in clean["questions"])
    assert units == 55


def test_checker_numeric_fraction_percent():
    assert check_answer("0.5", "1/2", "numeric")
    assert check_answer("50%", "0.5", "numeric")
    assert check_answer("2,50", "5/2", "numeric")
    assert not check_answer("0.4", "1/2", "numeric")


def test_checker_safe_expression():
    assert check_answer("2*(x+3)", "2*x+6", "expression")
    assert check_answer("2(x+3)", "2*x+6", "expression")
    assert check_answer("2x+6", "2*(x+3)", "expression")
    assert not check_answer("__import__('os').system('whoami')", "0", "expression")


def test_grade_boundaries():
    thresholds = {"A+": 70, "A": 65, "B+": 60, "B": 55, "C+": 50, "C": 46}
    assert grade_for_score(70, thresholds) == "A+"
    assert grade_for_score(69.99, thresholds) == "A"
    assert grade_for_score(45.99, thresholds) == "Talabga yetmagan"


def test_rasch_estimation_returns_finite_parameters():
    import random
    random.seed(123)
    true_betas = [-1.0, -0.2, 0.6, 1.2]
    matrix = []
    for _ in range(180):
        theta = random.gauss(0, 1)
        row = []
        for beta in true_betas:
            p = 1 / (1 + math.exp(-(theta - beta)))
            row.append(1 if random.random() < p else 0)
        matrix.append(row)
    result = calibrate_1pl(matrix, ["Q1", "Q2", "Q3", "Q4"], max_iter=60, grid_size=31)
    assert len(result.theta) == 180
    assert all(math.isfinite(v) for v in result.theta)
    assert all(math.isfinite(v) for v in result.betas)
    assert result.betas[0] < result.betas[-1]
    weights = [1, 1, 4, 5]
    score = scaled_expected_score(result.theta[0], result.betas, weights)
    assert 0 <= score <= 100


def test_excel_template_import():
    from ai_math.excel_importer import exam_payload_from_xlsx
    excel_path = Path(__file__).resolve().parents[1] / "data" / "exam_template.xlsx"
    payload = exam_payload_from_xlsx(excel_path.read_bytes())
    clean = validate_exam_payload(payload)
    assert len(clean["questions"]) == 45
    assert clean["questions"][0]["qtype"] == "mcq4"
    assert clean["questions"][32]["qtype"] == "mcq6"
    assert clean["questions"][35]["qtype"] == "written2"


def test_excel_import_blank_optional_values_use_defaults():
    from io import BytesIO
    from openpyxl import load_workbook
    from ai_math.excel_importer import exam_payload_from_xlsx

    excel_path = Path(__file__).resolve().parents[1] / "data" / "exam_template.xlsx"
    wb = load_workbook(BytesIO(excel_path.read_bytes()))
    info = wb["Exam Info"]
    for row in range(2, info.max_row + 1):
        if info.cell(row, 1).value == "duration_minutes":
            info.cell(row, 2).value = None
        if info.cell(row, 1).value == "threshold_C":
            info.cell(row, 2).value = None
    out = BytesIO()
    wb.save(out)
    payload = exam_payload_from_xlsx(out.getvalue())
    assert payload["duration_minutes"] == 120
    assert payload["grade_thresholds"]["C"] == 46
