from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DewarpConfig:
    """Параметры алгоритма деварпинга (перенесено из DocumentResearch)."""

    weights_path: str
    adaptive_grid: bool = True
    min_grid_lines: int = 4
    max_grid_lines: int = 15
    nonuniform_grid: bool = True
    num_grid_lines: int = 10
    approx_method: str = "bspline"      # "bspline" | "polyfit" | "tps"
    poly_degree: int = 2
    point_percent: float = 0.1
    threshold_coef: float = 0.01
    yolo_confidence: float = 0.7