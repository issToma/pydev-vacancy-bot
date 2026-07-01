import itertools
import logging

import cv2
import numpy as np
from matplotlib import pyplot as plt
from scipy import spatial
from scipy.interpolate import griddata

logger = logging.getLogger(__name__)


class DocumentRemapping:
    def __init__(self, image_rgb):
        self.image_rgb = image_rgb

    @staticmethod
    def find_polyline_intersection(curve_1: np.ndarray, curve_2: np.ndarray):
        """
        Точное геометрическое пересечение двух полилиний через segment-segment intersection.

        Для каждой пары сегментов (из curve_1 и curve_2) вычисляет параметры t и u:
            P(t) = p + t * r,  t ∈ [0,1]
            Q(u) = q + u * s,  u ∈ [0,1]
        Пересечение существует, если оба параметра в [0,1].
        Все найденные точки усредняются (для хорошо построенных линий сетки
        реальное пересечение одно; среднее — страховка от численного шума).

        Возвращает точку пересечения (2,) или None если пересечений нет.
        """
        if len(curve_1) < 2 or len(curve_2) < 2:
            return None

        p = curve_1[:-1]           # (N-1, 2) начала сегментов curve_1
        r = curve_1[1:] - p        # (N-1, 2) направления
        q = curve_2[:-1]           # (M-1, 2) начала сегментов curve_2
        s = curve_2[1:] - q        # (M-1, 2) направления

        # Broadcasting: (N-1, 1, 2) и (1, M-1, 2) → операции дают (N-1, M-1)
        p_b = p[:, np.newaxis, :]
        r_b = r[:, np.newaxis, :]
        q_b = q[np.newaxis, :, :]
        s_b = s[np.newaxis, :, :]

        def cross2d(a, b):
            return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]

        rxs = cross2d(r_b, s_b)     # (N-1, M-1) знаменатель
        qmp = q_b - p_b             # (N-1, M-1, 2)

        with np.errstate(divide='ignore', invalid='ignore'):
            t = np.where(np.abs(rxs) > 1e-10, cross2d(qmp, s_b) / rxs, np.inf)
            u = np.where(np.abs(rxs) > 1e-10, cross2d(qmp, r_b) / rxs, np.inf)

        valid = (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1)

        if not np.any(valid):
            return None

        i_idx, j_idx = np.where(valid)
        pts = p[i_idx] + t[i_idx, j_idx, np.newaxis] * r[i_idx]
        return pts.mean(axis=0)

    def get_intersection_interpolation_approximation(self, polys_bt, polys_lr):
        """
        Находит точки пересечения линий сетки.

        Стратегия (два прохода):
        1. Segment-segment intersection — точное геометрическое пересечение.
           Работает для хорошо параметризованных кривых (горизонталь × вертикаль).
        2. Nearest-point fallback — если segment-segment не нашёл пересечения.
           Причина сбоя: savgol_filter нарушает монотонность x/y на краях данных,
           bt-линии перестают быть строго горизонтальными → геометрического пересечения нет.
           Nearest-point midpoint даёт приемлемое приближение (ранее работало с tolerance).

        max_dist вычисляется по длинам кривых — аналогично исходному max_tolerance = span * 0.20.
        """
        points_set = []
        fallback_used = 0

        # Максимальное допустимое расстояние для fallback: 30% длины наибольшей кривой.
        # Было 20%, но для сильно деформированных документов (sagitta > 0.3) этого не хватает:
        # SG-артефакты на краях данных пропорционально больше → nearest-point dist растёт.
        # При 30% 12_5.jpg (dist=704, max было 693) проходит без изменений качества.
        bt_arc = max(
            (float(np.sum(np.linalg.norm(np.diff(c, axis=0), axis=1))) for c in polys_bt),
            default=0.0,
        )
        lr_arc = max(
            (float(np.sum(np.linalg.norm(np.diff(c, axis=0), axis=1))) for c in polys_lr),
            default=0.0,
        )
        max_dist = max(max(bt_arc, lr_arc) * 0.30, 20.0)

        for line_bt, line_lr in itertools.product(polys_bt, polys_lr):
            pt = self.find_polyline_intersection(line_bt, line_lr)

            if pt is None:
                # Fallback: midpoint ближайшей пары точек
                tree = spatial.cKDTree(line_bt)
                dists, idx1 = tree.query(line_lr, k=1)
                min_i = int(np.argmin(dists))
                dist = float(dists[min_i])
                if dist > max_dist:
                    raise ValueError(
                        f"Пересечение не найдено (nearest-point dist={dist:.0f}px > max={max_dist:.0f}px). "
                        "Проверьте корректность линий сетки."
                    )
                pt = np.mean([line_bt[idx1[min_i]], line_lr[min_i]], axis=0)
                fallback_used += 1

            points_set.append([pt[0], pt[1]])

        if fallback_used > 0:
            logger.info(f'Nearest-point fallback: {fallback_used}/{len(points_set)} пар линий сетки')

        return points_set

    def get_fix_grid_coordinates(self, length, width, num_of_line):
        x = np.linspace(0, width, num_of_line)
        y = np.linspace(0, length, num_of_line)
        xv, yv = np.meshgrid(x, y)
        return np.dstack([xv, yv]).reshape(-1, 2)

    @staticmethod
    def crop_image(img):
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_OTSU + cv2.THRESH_BINARY)[1]
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            raise ValueError("Контуры не найдены — возможно изображение полностью чёрное")
        contour = sorted(contours, key=cv2.contourArea, reverse=True)
        x, y, w, h = cv2.boundingRect(contour[0])
        return img[y:y + h, x:x + w]

    def remap_image(self, height, width, points_set, xy_pairs):
        img = self.image_rgb.copy()
        grid_x, grid_y = np.mgrid[0:(height - 1):height * 1j, 0:(width - 1):width * 1j]
        grid_z = griddata(np.fliplr(xy_pairs), np.fliplr(points_set), (grid_x, grid_y), method='cubic')

        map_x = grid_z[:, :, 1].astype('float32')
        map_y = grid_z[:, :, 0].astype('float32')

        warped_image = cv2.remap(img, map_x, map_y, cv2.INTER_LANCZOS4)
        warped_image = np.fliplr(np.rot90(warped_image, 2))
        crop_warped_image = self.crop_image(warped_image)
        return cv2.cvtColor(crop_warped_image, cv2.COLOR_RGB2BGR)

    @staticmethod
    def plot_remap_diff(img, crop_warped_image):
        """Визуализация до/после для ручной отладки."""
        plt.subplot(121, title='before')
        plt.imshow(img)
        plt.axis('off')
        plt.subplot(122, title='after')
        plt.imshow(crop_warped_image)
        plt.axis('off')
        plt.tight_layout()
        plt.show()