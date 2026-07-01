import logging
import cv2
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from scipy.ndimage import uniform_filter1d
from scipy.signal import find_peaks as _find_peaks

logger = logging.getLogger(__name__)


@dataclass
class TextCurvatureResult:
    """
    median_sagitta: медианный sagitta ratio по всем найденным строкам.
                    None — недостаточно текста для надёжной оценки.
    baselines:      список (x_arr, y_arr) — полином каждой строки для визуализации.
    n_lines:        число строк, которые удалось выделить.
    """
    median_sagitta: Optional[float]
    baselines: list = field(default_factory=list)
    n_lines: int = 0


def _sagitta_ratio(x: np.ndarray, y: np.ndarray) -> float:
    """Отношение максимального отклонения кривой от хорды к длине хорды.
    Идентична _edge_sagitta_ratio из t_models.py — намеренно дублируем,
    чтобы модуль был независимым."""
    start = np.array([x[0], y[0]], dtype=float)
    end   = np.array([x[-1], y[-1]], dtype=float)
    chord = end - start
    chord_len = np.linalg.norm(chord)
    if chord_len < 1e-6:
        return 0.0
    perp = np.array([-chord[1], chord[0]]) / chord_len
    pts_rel = np.column_stack([x - start[0], y - start[1]])
    return float(np.max(np.abs(pts_rel @ perp)) / chord_len)


class TextCurvatureAnalyzer:
    """
    Оценивает кривизну текстовых строк классическими методами CV.

    Пайплайн:
        grayscale → адаптивная бинаризация (устойчива к тени от загнутого угла)
        → connected components → фильтрация по размеру и положению
        → Y-кластеризация (порог = 1.5 × медианная высота CC)
        → np.polyfit baseline → sagitta ratio для каждой строки
        → возвращаем медиану

    Работает для печатного текста и рукописи.
    Картинки/декорации отсекаются фильтром max_area_ratio.
    Если текста нет или его мало — возвращает median_sagitta = None (fallback на геометрию).
    """

    def __init__(
        self,
        min_area: int = 30,
        max_area_ratio: float = 0.005,      # макс. площадь CC как доля площади документа
        min_char_height_ratio: float = 0.004,  # мин. высота CC = 0.4% высоты изображения
        edge_margin_ratio: float = 0.05,    # отступ от края маски (зона артефактов загиба)
        min_cc_per_line: int = 4,           # минимум CC в строке
        min_lines: int = 3,                 # минимум строк для вынесения вердикта
        poly_degree: int = 2,               # степень полинома baseline
        adaptive_block_ratio: float = 0.033,  # размер блока адаптивного порога (~h/30)
        adaptive_C: int = 10,
    ):
        self.min_area = min_area
        self.max_area_ratio = max_area_ratio
        self.min_char_height_ratio = min_char_height_ratio
        self.edge_margin_ratio = edge_margin_ratio
        self.min_cc_per_line = min_cc_per_line
        self.min_lines = min_lines
        self.poly_degree = poly_degree
        self.adaptive_block_ratio = adaptive_block_ratio
        self.adaptive_C = adaptive_C

    def analyze(self, image_rgb: np.ndarray, doc_mask: np.ndarray) -> TextCurvatureResult:
        h, w = image_rgb.shape[:2]
        doc_area = float(np.sum(doc_mask > 0))
        if doc_area < 1:
            return TextCurvatureResult(None)

        # 1. Grayscale → адаптивный порог → маска
        # ВАЖНО: маску применяем ПОСЛЕ порога, не до.
        # Если занулить фон до порога, адаптивный алгоритм увидит 0 вместо фона
        # и после THRESH_BINARY_INV пометит весь фон как foreground (255).
        # RETR_EXTERNAL тогда находит один огромный контур-фон,
        # а все символы текста оказываются "дырками" внутри него и не видны.
        gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)

        block_size = int(h * self.adaptive_block_ratio)
        if block_size % 2 == 0:
            block_size += 1
        block_size = max(11, block_size)

        binary = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            block_size, self.adaptive_C,
        )
        # Обнуляем фон уже в бинарном изображении
        binary = cv2.bitwise_and(binary, binary, mask=doc_mask)

        # 2. Connected components
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        logger.debug(f"[TextCurvature] total contours: {len(contours)}")

        # 3. Фильтрация CC
        max_area = doc_area * self.max_area_ratio
        # Минимальная высота символа масштабируется с разрешением изображения:
        # при 4032px → ~16px; при 800px → ~4px. Отсекает шум,
        # который при фиксированном min_area=30 портил медианную высоту и y_gap_thresh.
        min_ch = max(4, int(h * self.min_char_height_ratio))
        margin_x = int(w * self.edge_margin_ratio)
        margin_y = int(h * self.edge_margin_ratio)

        n_small, n_large, n_short, n_margin = 0, 0, 0, 0

        # (cx, cy, ch) — центроид и высота bounding box
        filtered: list[tuple[int, int, int]] = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < self.min_area:
                n_small += 1
                continue
            if area > max_area:
                n_large += 1
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)
            if ch < min_ch:
                n_short += 1
                continue
            cx, cy = x + cw // 2, y + ch // 2
            if cx < margin_x or cx > w - margin_x or cy < margin_y or cy > h - margin_y:
                n_margin += 1
                continue
            filtered.append((cx, cy, ch))

        logger.debug(
            f"[TextCurvature] filtered: {len(filtered)} passed | "
            f"removed: small={n_small}, large={n_large}, short(h<{min_ch}px)={n_short}, margin={n_margin}"
        )

        if len(filtered) < self.min_cc_per_line * self.min_lines:
            logger.debug(
                f"[TextCurvature] n/a: too few CC ({len(filtered)} < {self.min_cc_per_line * self.min_lines})"
            )
            return TextCurvatureResult(None)

        # 4. Row histogram кластеризация
        #
        # Проблема gap-подхода: разброс y-центроидов ВНУТРИ одной строки
        # (~0.6 × char_height) перекрывается с расстоянием МЕЖДУ строками
        # (~0.5 × char_height для типичных документов) → соседние строки сливаются.
        #
        # Решение: строим гистограмму плотности y-координат CC,
        # сглаживаем окном ≈ 0.6 × char_height (объединяет символы одной строки),
        # находим пики (scipy.signal.find_peaks) — каждый пик = одна текстовая строка.
        # Затем назначаем CC ближайшему пику в радиусе ≈ char_height.

        median_ch = float(np.median([p[2] for p in filtered]))

        # Гистограмма y-координат центроидов
        y_hist = np.zeros(h, dtype=np.float32)
        for _, cy, _ in filtered:
            y_hist[min(int(cy), h - 1)] += 1.0

        # Ширина сглаживающего окна: объединяем символы одной строки
        smooth_win = max(3, int(median_ch * 0.6))
        if smooth_win % 2 == 0:
            smooth_win += 1
        y_hist_smooth = uniform_filter1d(y_hist, size=smooth_win)

        # Минимальное расстояние между пиками ≈ char_height (строки не ближе)
        min_peak_dist = max(int(median_ch * 0.8), 5)
        peaks, _ = _find_peaks(y_hist_smooth, height=1.0, distance=min_peak_dist)

        logger.debug(
            f"[TextCurvature] row histogram: median_ch={median_ch:.1f}px, "
            f"smooth_win={smooth_win}, min_peak_dist={min_peak_dist}, peaks={len(peaks)}"
        )

        if len(peaks) < self.min_lines:
            logger.debug(f"[TextCurvature] n/a: too few peaks ({len(peaks)})")
            return TextCurvatureResult(None, [], len(peaks))

        # Назначаем каждый CC ближайшему пику в радиусе assign_radius
        assign_radius = int(median_ch * 1.0)
        lines_raw: list[list] = [[] for _ in peaks]
        for cc in filtered:
            cy = cc[1]
            dists = np.abs(peaks - cy)
            nearest = int(np.argmin(dists))
            if dists[nearest] <= assign_radius:
                lines_raw[nearest].append(cc)

        lines_raw = [ln for ln in lines_raw if len(ln) >= self.min_cc_per_line]

        logger.debug(
            f"[TextCurvature] clusters with >= {self.min_cc_per_line} CC: {len(lines_raw)} "
            f"(need >= {self.min_lines})"
        )

        if len(lines_raw) < self.min_lines:
            logger.debug(f"[TextCurvature] n/a: too few valid lines ({len(lines_raw)})")
            return TextCurvatureResult(None, [], len(lines_raw))

        # 5. Polyfit baseline + sagitta для каждой строки
        baselines: list = []
        sagittas: list = []

        for line_pts in lines_raw:
            xs = np.array([p[0] for p in line_pts], dtype=float)
            ys = np.array([p[1] for p in line_pts], dtype=float)

            if len(xs) < 3:
                continue

            try:
                coeffs = np.polyfit(xs, ys, self.poly_degree)
            except np.linalg.LinAlgError:
                continue

            x_new = np.linspace(xs.min(), xs.max(), 120)
            y_new = np.polyval(coeffs, x_new)
            baselines.append((x_new, y_new))
            sagittas.append(_sagitta_ratio(x_new, y_new))

        if not sagittas:
            return TextCurvatureResult(None, [], 0)

        return TextCurvatureResult(
            median_sagitta=float(np.median(sagittas)),
            baselines=baselines,
            n_lines=len(baselines),
        )