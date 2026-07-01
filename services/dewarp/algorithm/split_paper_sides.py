import cv2
import numpy as np
from scipy import spatial

from services.dewarp.algorithm.errors import HullNotFourPointsError, HullNotFoundError


def find_polynomial_new(masks, thres_coef, min_side_px=50):
    results = []

    for mask in masks:
        try:
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            big_contour = max(contours, key=cv2.contourArea)

            # Берём 4 угла напрямую через reduce_to_quad — работает при любом числе точек контура
            # (5 углов при окклюзии, 8 при загнутых углах и т.д.)
            pts = big_contour.reshape(-1, 2).astype("float32")
            quad = reduce_to_quad(pts)  # всегда (4, 2)

            # Проверка на вырожденный четырёхугольник
            sides = [np.linalg.norm(quad[i] - quad[(i + 1) % 4]) for i in range(4)]
            if min(sides) < min_side_px:
                raise HullNotFourPointsError(
                    f"Вырожденный четырёхугольник: мин. сторона {min(sides):.0f}px < {min_side_px}px"
                )

            # Формат (4,1,2) int32 — совместим с cv2 и diagonal_formation_new
            hull = quad.reshape(4, 1, 2).astype(np.int32)
            results.append((hull, big_contour, mask))

        except HullNotFourPointsError as e:
            print(f"Пропуск маски: {e}")
            continue

    if not results:
        raise HullNotFoundError("Не найдена оболочка документа")
    return results


def reduce_to_quad(points):
    pts = points.reshape(-1, 2).astype("float32")
    center = np.mean(pts, axis=0)

    bbox_w = pts[:, 0].max() - pts[:, 0].min()
    bbox_h = pts[:, 1].max() - pts[:, 1].min()
    aspect_skew = abs(bbox_w - bbox_h) / max(bbox_w, bbox_h)

    if aspect_skew < 0.3:
        # Метод квадрантов — надёжнее для повёрнутых документов
        def farthest(pts_list):
            if len(pts_list) == 0:
                return center
            return max(pts_list, key=lambda p: np.linalg.norm(p - center))

        tl = [p for p in pts if p[0] <= center[0] and p[1] <= center[1]]
        tr = [p for p in pts if p[0] > center[0] and p[1] <= center[1]]
        br = [p for p in pts if p[0] > center[0] and p[1] > center[1]]
        bl = [p for p in pts if p[0] <= center[0] and p[1] > center[1]]
        return np.array([farthest(tl), farthest(tr), farthest(br), farthest(bl)])
    else:
        # Метод суммы/разности — точнее для выровненных документов
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]   # top-left
        rect[2] = pts[np.argmax(s)]   # bottom-right
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)] # top-right
        rect[3] = pts[np.argmax(diff)] # bottom-left
        return rect


class SplitPaperSides:
    def __init__(self, image_rgb):
        self.image_rgb = image_rgb

    @staticmethod
    def sort_points(points):
        center = np.mean(points, axis=0)
        # arctan2(x, y) компенсирует перевёрнутую ось Y в системе координат OpenCV
        angles = np.arctan2(points[:, 0] - center[0], points[:, 1] - center[1])
        return points[np.argsort(angles)]

    def diagonal_formation_new(self, hull):
        pts = np.array(hull).reshape(-1, 2)

        s = pts.sum(axis=1)
        diff = np.diff(pts, axis=1)

        rect = np.zeros((4, 2), dtype="int32")
        rect[0] = pts[np.argmin(s)]    # Top-left
        rect[2] = pts[np.argmax(s)]    # Bottom-right
        rect[1] = pts[np.argmin(diff)] # Top-right
        rect[3] = pts[np.argmax(diff)] # Bottom-left

        corners = self.sort_points(rect)

        pt1 = tuple(map(int, corners[0]))
        pt2 = tuple(map(int, corners[2]))
        pt3 = tuple(map(int, corners[1]))
        pt4 = tuple(map(int, corners[3]))

        diag_lines = np.array([[pt1, pt2], [pt3, pt4]])
        return diag_lines

    @staticmethod
    def line_point_cross_product(line, point) -> np.ndarray:
        v1 = np.subtract(line[1], line[0])
        v2 = np.subtract(line[1], point)
        return np.cross(v1, v2)

    def find_edges(self, contour, diag_lines):
        edges = np.squeeze(np.array(contour), axis=1)

        left, right, top, bottom = [], [], [], []

        for edge in edges.tolist():
            cp0 = self.line_point_cross_product(diag_lines[0], edge)
            cp1 = self.line_point_cross_product(diag_lines[1], edge)

            if cp0 < 0 < cp1:
                left.append(edge)
            elif cp0 < 0 and cp1 < 0:
                bottom.append(edge)
            elif cp0 > 0 > cp1:
                right.append(edge)
            elif cp0 > 0 and cp1 > 0:
                top.append(edge)

        # right идёт сверху вниз — разворачиваем чтобы шёл снизу вверх как left
        # top сортируем по x для стабильной интерполяции
        top.sort()
        right = right[::-1]

        return left, right, top, bottom

    @staticmethod
    def get_dimensions(left, right, top, bottom, contour=None):
        if contour is not None:
            _, (w, h), _ = cv2.minAreaRect(contour)
            length = round(max(w, h))
            width  = round(min(w, h))
            return length, width

        left_length = round(spatial.distance.euclidean(left[0], left[-1]))
        right_length = round(spatial.distance.euclidean(right[0], right[-1]))
        top_length = round(spatial.distance.euclidean(top[0], top[-1]))
        bottom_length = round(spatial.distance.euclidean(bottom[0], bottom[-1]))

        length = round(np.mean([left_length, right_length]))
        width = round(np.mean([top_length, bottom_length]))

        return length, width