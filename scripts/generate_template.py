"""Print the bundled 45-question JSON template path."""
from pathlib import Path

print(Path(__file__).resolve().parents[1] / "data" / "exam_template.json")
