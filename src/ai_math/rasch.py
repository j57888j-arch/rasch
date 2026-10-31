"""Lightweight Rasch 1PL marginal maximum-likelihood calibration.

The latent population distribution is fixed to N(0, 1) for identification.
Person measures are EAP estimates. This implementation is intended for a
single 45-question / 55-item exam on low-memory hosting, not as a substitute
for a full psychometric validation process.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-min(x, 40.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(x, -40.0))
    return z / (1.0 + z)


def _log_sigmoid(x: float) -> float:
    if x > 0:
        return -math.log1p(math.exp(-min(x, 40.0)))
    return x - math.log1p(math.exp(max(x, -40.0)))


def _softmax(log_values: list[float]) -> list[float]:
    top = max(log_values)
    exps = [math.exp(max(-700.0, v - top)) for v in log_values]
    total = sum(exps)
    if total == 0 or not math.isfinite(total):
        return [1.0 / len(log_values)] * len(log_values)
    return [v / total for v in exps]


@dataclass
class RaschResult:
    item_codes: list[str]
    betas: list[float]
    item_se: list[float]
    infit: list[float]
    outfit: list[float]
    fit_status: list[str]
    theta: list[float]
    person_se: list[float]
    iterations: int
    converged: bool
    warnings: list[str]


def calibrate_1pl(
    matrix: list[list[int]],
    item_codes: list[str],
    max_iter: int = 80,
    tolerance: float = 1e-4,
    grid_min: float = -4.0,
    grid_max: float = 4.0,
    grid_size: int = 41,
) -> RaschResult:
    if not matrix:
        raise ValueError("Rasch hisoblash uchun javoblar topilmadi.")
    n_persons = len(matrix)
    n_items = len(item_codes)
    if n_items == 0 or any(len(row) != n_items for row in matrix):
        raise ValueError("Javoblar matritsasi o‘lchami noto‘g‘ri.")
    if any(value not in (0, 1) for row in matrix for value in row):
        raise ValueError("Rasch matritsasida faqat 0 va 1 bo‘lishi kerak.")

    step = (grid_max - grid_min) / (grid_size - 1)
    theta_grid = [grid_min + i * step for i in range(grid_size)]
    prior = [math.exp(-0.5 * theta * theta) for theta in theta_grid]
    prior_total = sum(prior)
    prior = [x / prior_total for x in prior]
    log_prior = [math.log(max(x, 1e-300)) for x in prior]

    betas = [0.0] * n_items
    posteriors: list[list[float]] = []
    converged = False
    warnings: list[str] = []

    for iteration in range(1, max_iter + 1):
        posteriors = []
        for row in matrix:
            logs = []
            for q, theta in enumerate(theta_grid):
                ll = log_prior[q]
                for j, answer in enumerate(row):
                    p = theta - betas[j]
                    ll += _log_sigmoid(p if answer else -p)
                logs.append(ll)
            posteriors.append(_softmax(logs))

        max_change = 0.0
        new_betas = betas.copy()
        for j in range(n_items):
            numerator = 0.0
            denominator = 0.0
            for person_index, row in enumerate(matrix):
                answer = row[j]
                posterior = posteriors[person_index]
                for q, theta in enumerate(theta_grid):
                    p = _sigmoid(theta - betas[j])
                    probability = posterior[q]
                    numerator += probability * (p - answer)
                    denominator += probability * p * (1.0 - p)
            if denominator < 1e-9:
                candidate = betas[j]
            else:
                delta = numerator / denominator
                candidate = max(-4.5, min(4.5, betas[j] + delta))
            new_betas[j] = candidate
            max_change = max(max_change, abs(candidate - betas[j]))
        betas = new_betas
        if max_change < tolerance:
            converged = True
            break

    # Recalculate posterior probabilities with the final item parameters.
    posteriors = []
    for row in matrix:
        logs = []
        for q, theta in enumerate(theta_grid):
            ll = log_prior[q]
            for j, answer in enumerate(row):
                p = theta - betas[j]
                ll += _log_sigmoid(p if answer else -p)
            logs.append(ll)
        posteriors.append(_softmax(logs))

    person_theta: list[float] = []
    person_se: list[float] = []
    for posterior in posteriors:
        mean = sum(p * theta for p, theta in zip(posterior, theta_grid))
        variance = sum(p * (theta - mean) ** 2 for p, theta in zip(posterior, theta_grid))
        person_theta.append(mean)
        person_se.append(math.sqrt(max(variance, 0.0)))

    item_se: list[float] = []
    infit: list[float] = []
    outfit: list[float] = []
    fit_status: list[str] = []
    for j in range(n_items):
        information = 0.0
        weighted_residual = 0.0
        variance_sum = 0.0
        outfit_sum = 0.0
        outfit_count = 0
        correct_count = sum(row[j] for row in matrix)
        for person_index, row in enumerate(matrix):
            p = _sigmoid(person_theta[person_index] - betas[j])
            var = max(p * (1.0 - p), 1e-5)
            residual2 = (row[j] - p) ** 2
            information += var
            weighted_residual += residual2
            variance_sum += var
            outfit_sum += residual2 / var
            outfit_count += 1
        se = 1.0 / math.sqrt(max(information, 1e-8))
        outfit_value = outfit_sum / max(outfit_count, 1)
        infit_value = weighted_residual / max(variance_sum, 1e-8)
        item_se.append(se)
        infit.append(infit_value)
        outfit.append(outfit_value)
        status = "FIT" if 0.7 <= infit_value <= 1.3 and 0.7 <= outfit_value <= 1.3 else "REVIEW"
        if correct_count in (0, n_persons):
            status = "EXTREME"
            warnings.append(f"{item_codes[j]} savolda barcha javoblar bir xil; Beta cheklangan baho.")
        fit_status.append(status)

    if not converged:
        warnings.append("Rasch EM algoritmi belgilangan iteratsiyada to‘liq yaqinlashmadi.")
    if n_persons < 30:
        warnings.append(f"Namuna {n_persons} kishi: kalibrlash dastlabki/provisional hisoblanadi.")
    if n_persons < 10:
        warnings.append("10 kishidan kam qatnashchi bilan Rasch darajasini ishonchli talqin qilib bo‘lmaydi.")
    if n_persons < 2:
        warnings.append("Kamida 2 qatnashchi kerak; Rasch kalibrlashining amaliy ma'nosi cheklangan.")

    return RaschResult(
        item_codes=item_codes, betas=betas, item_se=item_se, infit=infit,
        outfit=outfit, fit_status=fit_status, theta=person_theta,
        person_se=person_se, iterations=iteration, converged=converged,
        warnings=warnings,
    )


def scaled_expected_score(theta: float, betas: list[float], weights: list[int]) -> float:
    if not betas or len(betas) != len(weights):
        return 0.0
    max_score = sum(weights)
    if max_score <= 0:
        return 0.0
    expected = sum(w * _sigmoid(theta - beta) for beta, w in zip(betas, weights))
    return round(100.0 * expected / max_score, 2)


def grade_for_score(score: float, thresholds: dict[str, float]) -> str:
    for grade in ("A+", "A", "B+", "B", "C+", "C"):
        if score >= float(thresholds.get(grade, 0)):
            return grade
    return "Talabga yetmagan"
