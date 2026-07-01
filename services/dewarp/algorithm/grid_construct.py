import numpy as np
from matplotlib import pyplot as plt
from scipy.interpolate import interp1d, UnivariateSpline, Rbf

from scipy.signal import savgol_filter
from scipy.interpolate import CubicSpline


class GridConstruct:
    def __init__(self, image_rgb):
        self.image_rgb = image_rgb

    @staticmethod
    def interpolate(x, y, n_points):
        points = np.array(list(zip(x, y)))
        deltas = np.diff(points, axis=0)
        distances = np.linalg.norm(deltas, axis=1)
        arc = np.r_[0, np.cumsum(distances)]
        arc /= arc[-1]
        new_indices = np.linspace(0, 1, n_points)
        x1 = interp1d(arc, x, kind='cubic')(new_indices)
        y1 = interp1d(arc, y, kind='cubic')(new_indices)
        return x1, y1

    def calculate_opposite_interpolate(self, side_1, side_2, smooth=False, window=51, coef=5):
        if len(side_1) < 2 or len(side_2) < 2:
            raise ValueError(
                f"Ребро содержит слишком мало точек для интерполяции: "
                f"side_1={len(side_1)}, side_2={len(side_2)}. "
                "Возможно, маска документа вырожденная или find_edges не нашёл одно из рёбер."
            )

        side_1 = np.array(side_1)
        if side_1.ndim != 2 or side_1.shape[1] != 2:
            raise ValueError(f"Неверная форма side_1: {side_1.shape}, ожидается (N, 2).")
        side_1_x, side_1_y = side_1[:, 0], side_1[:, 1]

        side_2 = np.array(side_2)
        if side_2.ndim != 2 or side_2.shape[1] != 2:
            raise ValueError(f"Неверная форма side_2: {side_2.shape}, ожидается (N, 2).")
        side_2_x, side_2_y = side_2[:, 0], side_2[:, 1]

        pts_num = np.max((len(side_1_x), len(side_2_x)))

        new_side_1_x, new_side_1_y = self.interpolate(side_1_x, side_1_y, pts_num)
        new_side_2_x, new_side_2_y = self.interpolate(side_2_x, side_2_y, pts_num)

        if smooth:
            # Адаптируем window под длину данных
            window = min(window, pts_num)
            # window должен быть нечётным
            if window % 2 == 0:
                window -= 1
            # coef должен быть строго меньше window
            coef = min(coef, window - 1)

            new_side_1_x = savgol_filter(new_side_1_x, window, coef)
            new_side_1_y = savgol_filter(new_side_1_y, window, coef)
            new_side_2_x = savgol_filter(new_side_2_x, window, coef)
            new_side_2_y = savgol_filter(new_side_2_y, window, coef)
        return new_side_1_x, new_side_1_y, new_side_2_x, new_side_2_y

    def build_grid(self, n_lines, l_x, l_y, r_x, r_y, b_x, b_y, t_x, t_y):
        linfit_x_lr = interp1d([1, n_lines], np.vstack((l_x, r_x)), axis=0)
        linfit_y_lr = interp1d([1, n_lines], np.vstack((l_y, r_y)), axis=0)

        linfit_x_bt = interp1d([1, n_lines], np.vstack((b_x, t_x)), axis=0)
        linfit_y_bt = interp1d([1, n_lines], np.vstack((b_y, t_y)), axis=0)

        return linfit_x_bt, linfit_y_bt, linfit_x_lr, linfit_y_lr

    @staticmethod
    def _edge_curvature_profile(x, y, smooth_window=15):
        """
        Вычисляет профиль кривизны ребра по дискретным точкам.
        Возвращает (t, kappa): нормированный параметр и абс. значение кривизны.
        """
        pts = np.column_stack((x, y)).astype(float)
        diffs = np.diff(pts, axis=0)
        ds = np.sqrt((diffs ** 2).sum(axis=1))
        ds = np.maximum(ds, 1e-8)
        t = np.r_[0, np.cumsum(ds)]
        t = t / t[-1]  # нормируем к [0, 1]

        # Вторые производные через конечные разности
        if len(t) < 5:
            return t, np.zeros_like(t)

        dx = np.gradient(x, t)
        dy = np.gradient(y, t)
        ddx = np.gradient(dx, t)
        ddy = np.gradient(dy, t)

        # Кривизна: |x'y'' - y'x''| / (x'^2 + y'^2)^(3/2)
        num = np.abs(dx * ddy - dy * ddx)
        den = (dx ** 2 + dy ** 2) ** 1.5
        kappa = num / np.maximum(den, 1e-12)

        # Сглаживание Савицкого-Голея для подавления шума
        win = min(smooth_window, len(kappa))
        if win % 2 == 0:
            win -= 1
        if win >= 5:
            kappa = savgol_filter(kappa, win, min(3, win - 1))
            kappa = np.abs(kappa)

        return t, kappa

    def _nonuniform_positions(self, n_lines, edge1_x, edge1_y, edge2_x, edge2_y, alpha=0.5):
        """
        Вычисляет неравномерные позиции линий сетки на основе кривизны
        противолежащих рёбер.

        Возвращает массив из n_lines значений в [1, n_lines], которые можно
        подставить в linfit вместо равномерных range(1, n_lines+1).
        """
        # Средний профиль кривизны двух противолежащих рёбер
        t1, k1 = self._edge_curvature_profile(edge1_x, edge1_y)
        t2, k2 = self._edge_curvature_profile(edge2_x, edge2_y)

        # Интерполируем оба профиля на общую равномерную сетку
        n_samples = 200
        t_common = np.linspace(0, 1, n_samples)
        k1_interp = np.interp(t_common, t1, k1)
        k2_interp = np.interp(t_common, t2, k2)
        kappa_avg = (k1_interp + k2_interp) / 2

        # Весовая функция: w(t) = sqrt(kappa + eps)
        eps = alpha * np.max(kappa_avg) if np.max(kappa_avg) > 1e-8 else 1e-6
        w = np.sqrt(kappa_avg + eps)

        # Кумулятивный интеграл (трапеция)
        W = np.cumsum(w)
        W = W / W[-1]  # нормируем к [0, 1]

        # Квантили: позиции линий в пространстве t ∈ [0, 1]
        quantiles = np.linspace(0, 1, n_lines)
        t_positions = np.interp(quantiles, W, t_common)

        # Переводим из t ∈ [0, 1] в пространство linfit [1, n_lines]
        positions = 1 + t_positions * (n_lines - 1)
        # Гарантируем граничные значения
        positions[0] = 1.0
        positions[-1] = float(n_lines)

        return positions

    def build_grid_nonuniform(self, n_lines, l_x, l_y, r_x, r_y, b_x, b_y, t_x, t_y):
        """
        Строит сетку с неравномерным распределением линий по кривизне рёбер.
        Интерфейс идентичен build_grid(), возвращает те же 4 объекта linfit.
        """
        # Равномерные linfit — нужны как основа для интерполяции
        linfit_x_lr = interp1d([1, n_lines], np.vstack((l_x, r_x)), axis=0)
        linfit_y_lr = interp1d([1, n_lines], np.vstack((l_y, r_y)), axis=0)
        linfit_x_bt = interp1d([1, n_lines], np.vstack((b_x, t_x)), axis=0)
        linfit_y_bt = interp1d([1, n_lines], np.vstack((b_y, t_y)), axis=0)

        # Неравномерные позиции для каждого направления
        pos_bt = self._nonuniform_positions(n_lines, b_x, b_y, t_x, t_y)
        pos_lr = self._nonuniform_positions(n_lines, l_x, l_y, r_x, r_y)

        # Предвычисляем линии в неравномерных позициях
        bt_lines_x = np.array([linfit_x_bt(p) for p in pos_bt])
        bt_lines_y = np.array([linfit_y_bt(p) for p in pos_bt])
        lr_lines_x = np.array([linfit_x_lr(p) for p in pos_lr])
        lr_lines_y = np.array([linfit_y_lr(p) for p in pos_lr])

        # Переупаковываем как linfit с равномерными целочисленными ключами [1..n_lines]
        uniform_keys = np.arange(1, n_lines + 1, dtype=float)

        new_linfit_x_bt = interp1d(uniform_keys, bt_lines_x, axis=0,
                                   fill_value="extrapolate")
        new_linfit_y_bt = interp1d(uniform_keys, bt_lines_y, axis=0,
                                   fill_value="extrapolate")
        new_linfit_x_lr = interp1d(uniform_keys, lr_lines_x, axis=0,
                                   fill_value="extrapolate")
        new_linfit_y_lr = interp1d(uniform_keys, lr_lines_y, axis=0,
                                   fill_value="extrapolate")

        return new_linfit_x_bt, new_linfit_y_bt, new_linfit_x_lr, new_linfit_y_lr

    def get_interpolation_approximation(self, shape, num_ln, degree, num_p, linfit_x, linfit_y,
                                        show=True, method="bspline"):
        """
        Диспетчер: выбирает метод аппроксимации линий сетки.
        method: "bspline" | "polyfit" | "tps"
        """
        if method == "polyfit":
            return self._approx_polyfit(shape, num_ln, degree, num_p, linfit_x, linfit_y, show)
        elif method == "tps":
            return self._approx_tps(shape, num_ln, num_p, linfit_x, linfit_y, show)
        else:
            return self._approx_bspline(shape, num_ln, degree, num_p, linfit_x, linfit_y, show)

    def _approx_bspline(self, shape, num_ln, degree, num_p, linfit_x, linfit_y, show=True):
        """Сглаживающий кубический B-сплайн (UnivariateSpline)."""
        polys_bt = []
        is_vertical = (shape == self.image_rgb.shape[0])

        MAX_PTS = 30

        for i in range(1, num_ln + 1):
            x_raw = linfit_x(i)
            y_raw = linfit_y(i)

            if is_vertical:
                order = np.argsort(y_raw)
                t_raw, v_raw = y_raw[order], x_raw[order]
            else:
                order = np.argsort(x_raw)
                t_raw, v_raw = x_raw[order], y_raw[order]

            _, unique_idx = np.unique(t_raw, return_index=True)
            t_raw, v_raw = t_raw[unique_idx], v_raw[unique_idx]

            if len(t_raw) > MAX_PTS:
                idx = np.round(np.linspace(0, len(t_raw) - 1, MAX_PTS)).astype(int)
                t_raw, v_raw = t_raw[idx], v_raw[idx]

            margin = (t_raw.max() - t_raw.min()) * 0.08
            t_new = np.linspace(t_raw.min() - margin, t_raw.max() + margin, num_p)

            k = max(1, min(degree, len(t_raw) - 1))
            spl = UnivariateSpline(t_raw, v_raw, k=k, s=len(t_raw))
            v_new = spl(t_new)

            if is_vertical:
                x_new = v_new
                y_new = t_new
            else:
                x_new = t_new
                y_new = v_new

            poly_coor = np.column_stack((x_new, y_new))
            polys_bt.append(poly_coor)

            if show:
                plt.plot(x_new, y_new)

        return polys_bt

    def _approx_polyfit(self, shape, num_ln, degree, num_p, linfit_x, linfit_y, show=True):
        """Глобальный полином (np.polyfit) — метод из исходной ВКР."""
        polys_bt = []
        is_vertical = (shape == self.image_rgb.shape[0])
        t_new = np.linspace(0, shape, num_p)

        for i in range(1, num_ln + 1):
            x_raw = linfit_x(i)
            y_raw = linfit_y(i)

            if is_vertical:
                coeffs = np.polyfit(y_raw, x_raw, degree)
                x_new = np.polyval(coeffs, t_new)
                y_new = t_new
            else:
                coeffs = np.polyfit(x_raw, y_raw, degree)
                x_new = t_new
                y_new = np.polyval(coeffs, t_new)

            poly_coor = np.column_stack((x_new, y_new))
            polys_bt.append(poly_coor)

            if show:
                plt.plot(x_new, y_new)

        return polys_bt

    def _approx_tps(self, shape, num_ln, num_p, linfit_x, linfit_y, show=True):
        """Thin Plate Splines (Rbf) — экспериментальный метод."""
        polys_bt = []
        is_vertical = (shape == self.image_rgb.shape[0])

        for i in range(1, num_ln + 1):
            x_raw = linfit_x(i)
            y_raw = linfit_y(i)

            if is_vertical:
                t_min, t_max = y_raw.min(), y_raw.max()
                t_new = np.linspace(t_min, t_max, num_p)
                tps = Rbf(y_raw, x_raw, function='thin_plate', smooth=0.1)
                x_new = tps(t_new)
                y_new = t_new
            else:
                t_min, t_max = x_raw.min(), x_raw.max()
                t_new = np.linspace(t_min, t_max, num_p)
                tps = Rbf(x_raw, y_raw, function='thin_plate', smooth=0.1)
                x_new = t_new
                y_new = tps(t_new)

            poly_coor = np.column_stack((x_new, y_new))
            polys_bt.append(poly_coor)

            if show:
                plt.plot(x_new, y_new, linewidth=1)

        return polys_bt

