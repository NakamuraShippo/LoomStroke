"""Shared Bezier fitting math used by the precise pen and editable text paths."""

import math

from .common import Anchor


class CurveFitting:
    def _fit_curve_to_points(
        self, points: list[tuple[float, float]], smoothness: float, closed: bool
    ) -> list[Anchor]:
        """
        Fit a series of Bezier curves to the points.
        Reduces the number of anchors based on `smoothness` (error tolerance).
        """
        if len(points) < 2:
            return [Anchor(pos=p, kind="corner") for p in points]

        # smoothness (0-5.0) を 許容誤差 (ピクセル単位の自乗誤差など) に変換
        # 例: 0.2 -> 1px, 1.0 -> 5px, 5.0 -> 100px 程度の許容度
        # 誤差許容値が大きいほど、より強い曲線近似が行われ、アンカーが減る
        error_threshold_sq = (smoothness * 4.0) ** 2

        # 1. 接線ベクトルの計算
        # 各点における接線を、隣接点から推定する
        tangents = self._compute_tangents(points, closed)

        # 2. 再帰的にカーブをフィットさせる
        # 始点と終点の接線方向を固定し、間の点を誤差内に収められるか試行する
        anchors = self._fit_recursive(points, tangents, 0, len(points) - 1, error_threshold_sq)

        # 閉じたパスの場合の端点処理
        if closed and len(anchors) > 1:
            # 始点と終点が同じ位置だが、ハンドルが独立している場合があるため、
            # 連続性を保つために平均化するなどの処理を入れることも可能だが、
            # ここでは単純に座標一致とKindの設定を行う
            anchors[-1] = Anchor(
                pos=anchors[0].pos,
                kind=anchors[0].kind,
                h_in=anchors[-1].h_in,
                h_out=anchors[
                    -1
                ].h_out,  # 通常終点のh_outは(0,0)だが、閉じるなら連続性を持たせる余地あり
            )
            # 始点のh_inを終点から持ってくる（完全な平滑化）  # noqa: RUF003
            anchors[0] = Anchor(
                pos=anchors[0].pos,
                kind=anchors[0].kind,
                h_in=anchors[-1].h_in,
                h_out=anchors[0].h_out,
            )

        return anchors

    def _fit_recursive(self, points, tangents, first, last, error_threshold_sq) -> list[Anchor]:
        """
        改良版: 常に [first ... last] の範囲のアンカーリストを返す。
        結合時に重複（split_idx）を除去する。
        """  # noqa: RUF002
        # ベースケース
        if last - first < 2:
            # 直線の始点と終点
            p_start = points[first]
            p_end = points[last]
            return [
                Anchor(pos=p_start, kind="corner", h_in=(0, 0), h_out=(0, 0)),
                Anchor(pos=p_end, kind="corner", h_in=(0, 0), h_out=(0, 0)),
            ]

        # フィット試行
        tan_start = tangents[first]
        tan_end = tangents[last]
        dist = math.hypot(points[last][0] - points[first][0], points[last][1] - points[first][1])
        handle_len = dist / 3.0

        h_out_start = (tan_start[0] * handle_len, tan_start[1] * handle_len)
        h_in_end = (-tan_end[0] * handle_len, -tan_end[1] * handle_len)

        p0 = points[first]
        p3 = points[last]
        p1 = (p0[0] + h_out_start[0], p0[1] + h_out_start[1])
        p2 = (p3[0] + h_in_end[0], p3[1] + h_in_end[1])

        max_err_sq = 0.0
        split_idx = -1
        count = last - first
        for i in range(1, count):
            idx = first + i
            t = i / count
            pt = points[idx]
            qt = self._eval_bezier(p0, p1, p2, p3, t)
            dx = qt[0] - pt[0]
            dy = qt[1] - pt[1]
            err_sq = dx * dx + dy * dy
            if err_sq > max_err_sq:
                max_err_sq = err_sq
                split_idx = idx

        if max_err_sq < error_threshold_sq:
            # フィット成功
            # 始点と終点のアンカーを返す
            # 始点のh_in、終点のh_outは、隣接セグメントとの結合時に調整されるべきだが
            # ここではこのセグメント内での形状を決定する
            start_anchor = Anchor(pos=p0, kind="smooth", h_in=(0, 0), h_out=h_out_start)
            end_anchor = Anchor(pos=p3, kind="smooth", h_in=h_in_end, h_out=(0, 0))
            return [start_anchor, end_anchor]

        # 分割
        left = self._fit_recursive(points, tangents, first, split_idx, error_threshold_sq)
        right = self._fit_recursive(points, tangents, split_idx, last, error_threshold_sq)

        # 結合: leftの最後とrightの最初は同じ点 (split_idx)
        # left[-1] の h_in (左からの入り) と right[0] の h_out (右への出) を合成して
        # 1つのアンカーにする
        mid_in = left[-1].h_in
        mid_out = right[0].h_out

        merged_anchor = Anchor(pos=points[split_idx], kind="smooth", h_in=mid_in, h_out=mid_out)

        return [*left[:-1], merged_anchor, *right[1:]]

    def _compute_tangents(
        self, points: list[tuple[float, float]], closed: bool
    ) -> list[tuple[float, float]]:
        """各点における単位接線ベクトルを計算する (Catmull-Rom like)"""
        if not points:
            return []
        n = len(points)
        tangents = [(0.0, 0.0)] * n

        for i in range(n):
            if i == 0:
                if closed:
                    prev = points[-2]
                    next_p = points[1]
                else:
                    prev = points[0]
                    next_p = points[1] if n > 1 else points[0]
            elif i == n - 1:
                if closed:
                    prev = points[-2]
                    next_p = points[1]
                else:
                    prev = points[-2]
                    next_p = points[-1]
            else:
                prev = points[i - 1]
                next_p = points[i + 1]

            # 隣接点間のベクトル
            dx = next_p[0] - prev[0]
            dy = next_p[1] - prev[1]
            length = math.hypot(dx, dy)
            if length > 1e-6:
                tangents[i] = (dx / length, dy / length)
            else:
                tangents[i] = (0.0, 0.0)
        return tangents

    def _eval_bezier(self, p0, p1, p2, p3, t):
        mt = 1.0 - t
        mt2 = mt * mt
        mt3 = mt2 * mt
        t2 = t * t
        t3 = t2 * t

        x = mt3 * p0[0] + 3 * mt2 * t * p1[0] + 3 * mt * t2 * p2[0] + t3 * p3[0]
        y = mt3 * p0[1] + 3 * mt2 * t * p1[1] + 3 * mt * t2 * p2[1] + t3 * p3[1]
        return (x, y)

    def _compute_smooth_handles(
        self,
        prev_pos: tuple[float, float],
        curr_pos: tuple[float, float],
        next_pos: tuple[float, float],
        *,
        strength: float,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        vx = float(next_pos[0] - prev_pos[0])
        vy = float(next_pos[1] - prev_pos[1])
        v_len = math.hypot(vx, vy)
        if v_len <= 1e-6:
            return (0.0, 0.0), (0.0, 0.0)
        tx = vx / v_len
        ty = vy / v_len
        d_prev = math.hypot(curr_pos[0] - prev_pos[0], curr_pos[1] - prev_pos[1])
        d_next = math.hypot(next_pos[0] - curr_pos[0], next_pos[1] - curr_pos[1])
        base = min(d_prev, d_next) / 3.0
        length = max(0.0, base * max(0.0, min(1.0, strength)))
        h_out = (tx * length, ty * length)
        h_in = (-h_out[0], -h_out[1])
        return h_in, h_out
