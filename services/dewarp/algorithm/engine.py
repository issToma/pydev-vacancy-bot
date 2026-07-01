"""Движок деварпинга HybridDewarp (перенесён из DocumentResearch).

Чистая версия без matplotlib-визуализации: на вход BGR-изображение,
на выходе выпрямленное BGR-изображение (или None, если документ не найден).
Сам алгоритм (сегментация YOLO + построение деформационной сетки + ремаппинг)
не меняли — убрали только отрисовку отладочных панелей.
"""
from __future__ import annotations

import gc
import logging

import cv2
import numpy as np
from scipy.ndimage import uniform_filter1d

from services.dewarp.algorithm.config import DewarpConfig
from services.dewarp.algorithm.grid_construct import GridConstruct
from services.dewarp.algorithm.remap_doc import DocumentRemapping
from services.dewarp.algorithm.split_paper_sides import SplitPaperSides, find_polynomial_new
from services.dewarp.algorithm.text_curvature import TextCurvatureAnalyzer
from services.dewarp.algorithm.yolo_segmentation import YOLOSegmentation

logger = logging.getLogger(__name__)


def _arc_length(x: np.ndarray, y: np.ndarray) -> float:
    """Длина полилинии по дуге."""
    return float(np.sum(np.sqrt(np.diff(x) ** 2 + np.diff(y) ** 2)))


def _edge_sagitta_ratio(x: np.ndarray, y: np.ndarray) -> float:
    """Отношение макс. отклонения ребра от хорды к длине хорды (0=прямое, 0.2+=изогнутое)."""
    start = np.array([x[0], y[0]], dtype=float)
    end = np.array([x[-1], y[-1]], dtype=float)
    chord = end - start
    chord_len = np.linalg.norm(chord)
    if chord_len < 1e-6:
        return 0.0
    perp = np.array([-chord[1], chord[0]]) / chord_len
    pts_rel = np.column_stack([x - start[0], y - start[1]])
    return float(np.max(np.abs(pts_rel @ perp)) / chord_len)


def estimate_grid_lines(n_l_x, n_l_y, n_r_x, n_r_y, n_b_x, n_b_y, n_t_x, n_t_y,
                        min_lines: int, max_lines: int) -> int:
    """Оценивает число линий сетки по максимальному прогибу рёбер (sagitta ratio)."""
    max_sagitta = max(
        _edge_sagitta_ratio(n_l_x, n_l_y),
        _edge_sagitta_ratio(n_r_x, n_r_y),
        _edge_sagitta_ratio(n_b_x, n_b_y),
        _edge_sagitta_ratio(n_t_x, n_t_y),
    )
    ratio = float(np.clip(max_sagitta / 0.20, 0.0, 1.0))
    return min_lines + round((max_lines - min_lines) * ratio)


def _trim_edge_outliers(points: list, primary_axis: int, sigma: float = 2.0) -> list:
    """Удаляет точки диагональных срезов углов из рёбер (скользящий тренд + sigma)."""
    if len(points) < 10:
        return points
    pts = np.array(points, dtype=float)
    perp = pts[:, 1 - primary_axis]
    window = max(5, len(perp) // 5)
    trend = uniform_filter1d(perp, size=window, mode="nearest")
    residuals = perp - trend
    std = np.std(residuals)
    if std < 1e-6:
        return points
    inliers = np.abs(residuals) <= sigma * std
    filtered = pts[inliers].tolist()
    return filtered if len(filtered) >= 6 else points


class DewarpEngine:
    """Оркестратор деварпинга. Модель YOLO грузится один раз при создании."""

    def __init__(self, config: DewarpConfig) -> None:
        self.config = config
        self.yolo_model = YOLOSegmentation(weight_path=config.weights_path)
        self.text_analyzer = TextCurvatureAnalyzer()

    def _dewarp_mask(self, image_rgb, mask_data, context) -> np.ndarray | None:
        """Обработка одной маски документа → выпрямленное изображение (BGR) или None."""
        hull, big_contour, doc_mask = mask_data
        h_img, w_img, num_pts = context

        # изолируем документ от фона по маске YOLO
        masked_rgb = (image_rgb * (doc_mask[:, :, np.newaxis] > 0)).astype(image_rgb.dtype)

        # 1. Поиск сторон
        sides_model = SplitPaperSides(image_rgb=image_rgb)
        diag_lines = sides_model.diagonal_formation_new(hull=hull)
        left, right, top, bottom = sides_model.find_edges(contour=big_contour, diag_lines=diag_lines)

        # убираем точки диагональных срезов углов
        left = _trim_edge_outliers(left, primary_axis=1)
        right = _trim_edge_outliers(right, primary_axis=1)
        top = _trim_edge_outliers(top, primary_axis=0)
        bottom = _trim_edge_outliers(bottom, primary_axis=0)
        bottom = sorted(bottom, key=lambda p: p[0])

        # 2. Построение сетки (интерполяция рёбер)
        grid_module = GridConstruct(image_rgb=image_rgb)
        n_l_x, n_l_y, n_r_x, n_r_y = grid_module.calculate_opposite_interpolate(left, right, smooth=True)
        n_b_x, n_b_y, n_t_x, n_t_y = grid_module.calculate_opposite_interpolate(bottom, top, smooth=True)

        # ориентация документа и реальные размеры из minAreaRect
        arc_bt = (_arc_length(n_b_x, n_b_y) + _arc_length(n_t_x, n_t_y)) / 2
        arc_lr = (_arc_length(n_l_x, n_l_y) + _arc_length(n_r_x, n_r_y)) / 2
        _, (rect_w, rect_h), _ = cv2.minAreaRect(big_contour)
        if arc_bt >= arc_lr:
            doc_width, doc_height = float(max(rect_w, rect_h)), float(min(rect_w, rect_h))
        else:
            doc_width, doc_height = float(min(rect_w, rect_h)), float(max(rect_w, rect_h))

        # анализ кривизны текста — для адаптивного выбора числа линий
        text_result = self.text_analyzer.analyze(image_rgb, doc_mask)
        TEXT_FLAT_THRESHOLD = 0.010

        if self.config.adaptive_grid:
            geo_grid_lines = estimate_grid_lines(
                n_l_x, n_l_y, n_r_x, n_r_y, n_b_x, n_b_y, n_t_x, n_t_y,
                self.config.min_grid_lines, self.config.max_grid_lines,
            )
            if (text_result.median_sagitta is not None
                    and text_result.median_sagitta < TEXT_FLAT_THRESHOLD):
                current_grid_lines = self.config.min_grid_lines
            else:
                current_grid_lines = geo_grid_lines
        else:
            current_grid_lines = self.config.num_grid_lines

        # 3. построение линий сетки
        if self.config.nonuniform_grid:
            l_bt_x, l_bt_y, l_lr_x, l_lr_y = grid_module.build_grid_nonuniform(
                n_lines=current_grid_lines,
                l_x=n_l_x, l_y=n_l_y, r_x=n_r_x, r_y=n_r_y,
                b_x=n_b_x, b_y=n_b_y, t_x=n_t_x, t_y=n_t_y,
            )
        else:
            l_bt_x, l_bt_y, l_lr_x, l_lr_y = grid_module.build_grid(
                n_lines=current_grid_lines,
                l_x=n_l_x, l_y=n_l_y, r_x=n_r_x, r_y=n_r_y,
                b_x=n_b_x, b_y=n_b_y, t_x=n_t_x, t_y=n_t_y,
            )

        # аппроксимация линий сетки (show=False → без matplotlib)
        polys_bt = grid_module.get_interpolation_approximation(
            w_img, current_grid_lines, self.config.poly_degree, num_pts, l_bt_x, l_bt_y,
            show=False, method=self.config.approx_method)
        polys_lr = grid_module.get_interpolation_approximation(
            h_img, current_grid_lines, self.config.poly_degree, num_pts, l_lr_x, l_lr_y,
            show=False, method=self.config.approx_method)

        # 4. ремаппинг на изолированном изображении
        remap_model = DocumentRemapping(image_rgb=masked_rgb)
        points_set = remap_model.get_intersection_interpolation_approximation(polys_bt, polys_lr)
        x_y_pairs = remap_model.get_fix_grid_coordinates(doc_height, doc_width, current_grid_lines)

        if len(points_set) != len(x_y_pairs):
            logger.error("Mismatch точек сетки: %s vs %s", len(points_set), len(x_y_pairs))
            return None
        return remap_model.remap_image(h_img, w_img, points_set, x_y_pairs)

    def dewarp(self, image_bgr: np.ndarray) -> np.ndarray | None:
        """Главный вход: BGR-изображение → выпрямленный документ (BGR) или None."""
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        num_points = int(w * self.config.point_percent)

        masks = self.yolo_model.build_masks_array(image_rgb=rgb, confidence=self.config.yolo_confidence)
        polynomials = find_polynomial_new(masks=masks, thres_coef=self.config.threshold_coef)
        if not polynomials:
            return None

        # берём первый найденный документ
        hull, contour, doc_mask = polynomials[0]
        result = self._dewarp_mask(rgb, (hull, contour, doc_mask), (h, w, num_points))
        gc.collect()
        return result
