from __future__ import annotations

from decimal import Decimal, InvalidOperation
from fractions import Fraction
import ast
import re


def normalize_text(value: str) -> str:
    value = value.strip().replace("−", "-").replace("×", "*").replace("÷", "/")
    value = value.replace("٫", ".").replace(",", ".")
    value = re.sub(r"\s+", "", value)
    return value.casefold()


def _to_fraction(value: str) -> Fraction | None:
    normalized = normalize_text(value)
    is_percent = normalized.endswith("%")
    cleaned = normalized[:-1] if is_percent else normalized
    try:
        if "/" in cleaned:
            result = Fraction(cleaned)
        else:
            result = Fraction(Decimal(cleaned))
        return result / 100 if is_percent else result
    except (ValueError, ZeroDivisionError, InvalidOperation, OverflowError):
        return None


def check_answer(raw: str, expected: str, answer_type: str = "numeric", tolerance: float = 0.0) -> bool:
    raw = (raw or "").strip()
    expected = (expected or "").strip()
    if not raw or not expected:
        return False
    kind = answer_type.lower()
    if kind == "text":
        return normalize_text(raw) == normalize_text(expected)
    if kind in {"numeric", "fraction"}:
        got = _to_fraction(raw)
        want = _to_fraction(expected)
        if got is None or want is None:
            return False
        if tolerance > 0:
            return abs(float(got - want)) <= tolerance
        return got == want
    if kind == "expression":
        try:
            import sympy as sp

            def parse_safe(text: str):
                if len(text) > 120:
                    raise ValueError("Expression too long")
                expression = text.replace("^", "**")
                # Common school-math shorthand: 2x, 2(x+1), x(x+1).
                expression = re.sub(r"(?<=[0-9])(?=[a-zA-Z(])", "*", expression)
                expression = re.sub(r"(?<=[a-zA-Z)])(?=[a-zA-Z0-9(])", "*", expression)
                tree = ast.parse(expression, mode="eval")
                if sum(1 for _ in ast.walk(tree)) > 64:
                    raise ValueError("Expression too complex")
                def visit(node):
                    if isinstance(node, ast.Expression):
                        return visit(node.body)
                    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                        return sp.Rational(str(node.value))
                    if isinstance(node, ast.Name) and re.fullmatch(r"[a-zA-Z]", node.id):
                        return sp.Symbol(node.id)
                    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                        value = visit(node.operand)
                        return value if isinstance(node.op, ast.UAdd) else -value
                    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
                        left, right = visit(node.left), visit(node.right)
                        if isinstance(node.op, ast.Add): return left + right
                        if isinstance(node.op, ast.Sub): return left - right
                        if isinstance(node.op, ast.Mult): return left * right
                        if isinstance(node.op, ast.Div): return left / right
                        if isinstance(node.op, ast.Pow):
                            if not right.is_Integer or abs(int(right)) > 12:
                                raise ValueError("Exponent is out of allowed range")
                            return left ** right
                    raise ValueError("Unsupported mathematical syntax")
                return visit(tree)

            left = parse_safe(raw)
            right = parse_safe(expected)
            return sp.simplify(left - right) == 0
        except Exception:
            return False
    return False
