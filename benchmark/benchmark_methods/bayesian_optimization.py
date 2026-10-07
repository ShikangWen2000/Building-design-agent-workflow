"""Lightweight Gaussian-process Bayesian optimization over the shared genome space.

The envelope benchmark represents every candidate as a fixed-length vector in
``[0, 1]`` (see :mod:`envelope_search_space`). This module fits a Gaussian
process surrogate to the valid evaluations collected so far and proposes the
next genome vector by maximizing the Expected Improvement (EI) acquisition
function. It depends only on NumPy and SciPy so it can run alongside the other
benchmark methods without any extra services.

The objective is the selected total EUI (lower is better); EI is therefore
written for minimization.
"""

from __future__ import annotations

import math
import random

import numpy as np
from scipy.special import erf


def _rbf_kernel(left: np.ndarray, right: np.ndarray, lengthscale: float, signal_var: float) -> np.ndarray:
    left_sq = np.sum(left * left, axis=1)[:, None]
    right_sq = np.sum(right * right, axis=1)[None, :]
    squared_distance = np.maximum(left_sq + right_sq - 2.0 * left @ right.T, 0.0)
    return signal_var * np.exp(-0.5 * squared_distance / (lengthscale * lengthscale))


def _log_marginal_likelihood(
    inputs: np.ndarray,
    targets: np.ndarray,
    lengthscale: float,
    signal_var: float,
    noise: float,
) -> tuple[float, np.ndarray | None, np.ndarray | None]:
    count = inputs.shape[0]
    gram = _rbf_kernel(inputs, inputs, lengthscale, signal_var) + noise * np.eye(count)
    try:
        lower = np.linalg.cholesky(gram)
    except np.linalg.LinAlgError:
        return -math.inf, None, None
    alpha = np.linalg.solve(lower.T, np.linalg.solve(lower, targets))
    log_likelihood = (
        -0.5 * float(targets @ alpha)
        - float(np.sum(np.log(np.diag(lower))))
        - 0.5 * count * math.log(2.0 * math.pi)
    )
    return log_likelihood, lower, alpha


def _fit_gaussian_process(
    inputs: np.ndarray, targets: np.ndarray
) -> tuple[float, float, float, np.ndarray, np.ndarray]:
    """Select GP hyper-parameters by a small marginal-likelihood grid search."""
    upper = np.triu_indices(inputs.shape[0], k=1)
    if upper[0].size:
        pairwise = np.sqrt(
            np.maximum(np.sum((inputs[upper[0]] - inputs[upper[1]]) ** 2, axis=1), 0.0)
        )
        median_distance = float(np.median(pairwise)) or 1.0
    else:
        median_distance = 1.0
    lengthscale_grid = [median_distance * factor for factor in (0.25, 0.5, 1.0, 2.0, 4.0)]
    signal_grid = (0.5, 1.0, 2.0)
    noise_grid = (1e-3, 1e-2, 1e-1)
    best = (-math.inf, lengthscale_grid[2], 1.0, 1e-2, None, None)
    for lengthscale in lengthscale_grid:
        for signal_var in signal_grid:
            for noise in noise_grid:
                score, lower, alpha = _log_marginal_likelihood(
                    inputs, targets, lengthscale, signal_var, noise
                )
                if score > best[0] and lower is not None:
                    best = (score, lengthscale, signal_var, noise, lower, alpha)
    _, lengthscale, signal_var, noise, lower, alpha = best
    if lower is None:  # numerically degenerate fallback
        _, lower, alpha = _log_marginal_likelihood(inputs, targets, median_distance, 1.0, 1e-1)
    return lengthscale, signal_var, noise, lower, alpha


def _predict(
    candidates: np.ndarray,
    inputs: np.ndarray,
    lower: np.ndarray,
    alpha: np.ndarray,
    lengthscale: float,
    signal_var: float,
) -> tuple[np.ndarray, np.ndarray]:
    cross = _rbf_kernel(candidates, inputs, lengthscale, signal_var)
    mean = cross @ alpha
    solved = np.linalg.solve(lower, cross.T)
    variance = signal_var - np.sum(solved * solved, axis=0)
    return mean, np.maximum(variance, 1e-12)


def _expected_improvement(
    mean: np.ndarray, variance: np.ndarray, incumbent: float, xi: float = 0.01
) -> np.ndarray:
    sigma = np.sqrt(variance)
    improvement = incumbent - mean - xi
    z = improvement / sigma
    cdf = 0.5 * (1.0 + erf(z / math.sqrt(2.0)))
    pdf = np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    ei = improvement * cdf + sigma * pdf
    return np.where(sigma > 0.0, ei, 0.0)


def propose_next(
    vectors: list[list[float]],
    scores: list[float],
    rng: random.Random,
    *,
    candidate_pool: int = 2048,
    local_pool: int = 512,
    local_sigma: float = 0.08,
) -> list[float]:
    """Return the next genome vector maximizing Expected Improvement.

    ``vectors`` and ``scores`` are the flattened genomes and EUI values of the
    valid evaluations gathered so far. The acquisition function is optimized by
    scoring a large pool of uniform random candidates plus local perturbations
    around the best incumbents, which keeps the search derivative-free and fully
    compatible with the mixed-variable decoder.
    """
    inputs = np.asarray(vectors, dtype=float)
    raw_targets = np.asarray(scores, dtype=float)
    dimension = inputs.shape[1]
    target_mean = float(np.mean(raw_targets))
    target_std = float(np.std(raw_targets)) or 1.0
    targets = (raw_targets - target_mean) / target_std

    lengthscale, signal_var, _noise, lower, alpha = _fit_gaussian_process(inputs, targets)
    incumbent = float(np.min(targets))

    generator = np.random.default_rng(rng.randrange(2**32))
    uniform = generator.random((candidate_pool, dimension))
    ranked = inputs[np.argsort(raw_targets)]
    seeds = ranked[: max(1, min(8, ranked.shape[0]))]
    repeats = int(np.ceil(local_pool / seeds.shape[0]))
    local_base = np.repeat(seeds, repeats, axis=0)[:local_pool]
    local = np.clip(local_base + generator.normal(0.0, local_sigma, local_base.shape), 0.0, 1.0)
    candidates = np.vstack([uniform, local])

    mean, variance = _predict(candidates, inputs, lower, alpha, lengthscale, signal_var)
    acquisition = _expected_improvement(mean, variance, incumbent)
    best_index = int(np.argmax(acquisition))
    return candidates[best_index].tolist()
