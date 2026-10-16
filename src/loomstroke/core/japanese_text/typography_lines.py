"""Bounded greedy line fitting with JLReq punctuation spaces and adjustment."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .custom_rules import custom_policy, spacing_rules
from .typography_layout_types import LayoutAtom


@dataclass(frozen=True, slots=True)
class AtomPosition:
    atom: LayoutAtom
    offset: float
    advance: float
    draw_offset: float


@dataclass(frozen=True, slots=True)
class BrokenLine:
    end: int
    positions: tuple[AtomPosition, ...]
    extent: float
    hanging: bool = False
    overflow: bool = False


class LineComposer:
    """Prefix sums avoid repeatedly measuring growing lines or long paragraphs."""

    def __init__(self, atoms: list[LayoutAtom], options: Mapping[str, Any], em: float) -> None:
        self.atoms, self.options, self.em = atoms, options, em
        self.policy = custom_policy(options)
        self.rules = spacing_rules(options)
        self.custom_gaps = {}
        self.before, self.after, self.gaps = (
            [0.0] * len(atoms),
            [0.0] * len(atoms),
            [0.0] * len(atoms),
        )
        self._spaces()
        for i in range(1, len(atoms)):
            rule = self.rules.get((atoms[i - 1].category, atoms[i].category))
            if rule is not None:
                unit = min(atoms[i - 1].em or em, atoms[i].em or em)
                self.custom_gaps[i] = {**rule, "unit": unit}
                self.gaps[i] = rule["optimum"] * unit
        self.advances = [
            max(0.01, atom.advance + atom.tracking - self.before[i] - self.after[i])
            for i, atom in enumerate(atoms)
        ]
        self.capacities = [self._compressible(i) for i in range(len(atoms))]
        self.widths, self.cap_sums = [0.0], [0.0]
        for i, advance in enumerate(self.advances):
            self.widths.append(self.widths[-1] + advance + self.gaps[i])
            self.cap_sums.append(self.cap_sums[-1] + self.capacities[i])
        self.allowed = [i for i in range(1, len(atoms) + 1) if self._can_break(i)]

    def _spaces(self) -> None:
        mode = self.options["punctuation_spacing"]
        for i, atom in enumerate(self.atoms):
            half = (atom.em or self.em) * 0.5
            kind = atom.category if atom.kind == "base" else "other"
            if mode == "half":
                if kind == "opening":
                    self.before[i] = half
                elif kind in {"closing", "stop"}:
                    self.after[i] = half
                elif kind == "middle":
                    self.before[i] = self.after[i] = half * 0.5
            if i == 0:
                continue
            previous = self.atoms[i - 1]
            left = previous.category if previous.kind == "base" else "other"
            # Adjacent half-body punctuation shares the existing half-em gap;
            # full-width glyphs remain unchanged, only their empty sides trim.
            if mode == "standard":
                if left in {"closing", "stop"} and kind in {"closing", "stop"}:
                    self.after[i - 1] = (previous.em or self.em) * 0.5
                elif kind == "opening" and left in {"opening", "closing", "stop"}:
                    self.before[i] = half
                elif left == "middle" and kind == "middle":
                    self.after[i - 1] = (previous.em or self.em) * 0.25
                    self.before[i] = half * 0.5
            if {previous.category, atom.category} == {"japanese", "latin"}:
                self.gaps[i] = float(self.options["latin_spacing_em"]) * min(
                    atom.em or self.em, previous.em or self.em
                )

    def _compressible(self, index: int) -> float:
        if index in self.custom_gaps:
            rule = self.custom_gaps[index]
            return (rule["optimum"] - rule["minimum"]) * rule["unit"]
        atom = self.atoms[index]
        em = atom.em or self.em
        cap = float(self.options["compression_limit_em"]) * em
        if atom.kind == "base" and atom.category in {"opening", "closing", "stop", "middle"}:
            space = max(0.0, em * 0.5 - self.before[index] - self.after[index])
            return min(cap, space)
        if atom.category == "space" and atom.text != "\u3000":
            return min(cap, atom.advance * 0.5)
        return min(cap, self.gaps[index])

    def _can_break(self, end: int) -> bool:
        if end >= len(self.atoms):
            return True
        left, right = self.atoms[end - 1].text[-1], self.atoms[end].text[0]
        if (
            self.options.get("_wrap_mode") != "char"
            and self.atoms[end - 1].category == self.atoms[end].category == "latin"
        ):
            return False
        return (
            self.policy.can_end_line(left)
            and self.policy.can_start_line(right)
            and not self.policy.is_inseparable_pair(left, right)
        )

    def _edge_trim(self, start: int, end: int) -> tuple[float, float]:
        first, last = self.atoms[start], self.atoms[end - 1]
        leading = trailing = 0.0
        if first.kind == "base" and first.category == "opening":
            desired = (
                (first.em or self.em) * 0.5 if self.options["line_start_opening"] == "half" else 0.0
            )
            leading = desired - self.before[start]
        if last.kind == "base" and last.category in {"closing", "stop"}:
            desired = (
                (last.em or self.em) * 0.5 if self.options["line_end_closing"] == "half" else 0.0
            )
            trailing = desired - self.after[end - 1]
        return leading, trailing

    def extent(self, start: int, end: int, *, compressed: bool = False) -> float:
        leading, trailing = self._edge_trim(start, end)
        width = self.widths[end] - self.widths[start] - self.gaps[start] - leading - trailing
        width += sum(self._ruby_edges(start, end))
        if compressed and self.options["line_adjustment"] != "none":
            capacity = self.cap_sums[end] - self.cap_sums[start]
            if start in self.custom_gaps:
                capacity -= self.capacities[start]
            capacity -= max(0.0, leading) + max(0.0, trailing)
            width -= max(0.0, capacity)
        return max(0.01, width)

    def _ruby_edges(self, start: int, end: int) -> tuple[float, float]:
        first, last = self.atoms[start], self.atoms[end - 1]
        before = max(0.0, (first.line_start_advance or first.advance) - first.advance)
        after = max(0.0, (last.line_end_advance or last.advance) - last.advance)
        return (max(before, after), 0.0) if start + 1 == end else (before, after)

    def take(self, start: int, limit: float, *, wrap: bool = True) -> BrokenLine:
        count = len(self.atoms)
        if start >= count:
            return BrokenLine(start, (), 0.0)
        stop, hanging = count, False
        if wrap:
            candidate = start + 1
            while candidate <= count and self.extent(start, candidate, compressed=True) <= limit:
                candidate += 1
            if candidate <= count:
                punctuation = self.atoms[candidate - 1]
                if (
                    candidate > start + 1
                    and self.policy.can_hang(punctuation.text)
                    and self._can_break(candidate)
                ):
                    stop, hanging = candidate, True
                else:
                    allowed_index = bisect_right(self.allowed, candidate - 1) - 1
                    stop = self.allowed[allowed_index] if allowed_index >= 0 else start
                    if stop <= start:
                        stop = self.allowed[bisect_right(self.allowed, start)]
        return self._position(start, stop, limit, hanging=hanging)

    def _position(self, start: int, end: int, limit: float, *, hanging: bool) -> BrokenLine:
        leading, trailing = self._edge_trim(start, end)
        advances = self.advances[start:end].copy()
        gaps = self.gaps[start:end].copy()
        shifts = [-value for value in self.before[start:end]]
        gaps[0] = 0.0
        advances[0] -= leading
        shifts[0] -= leading
        advances[-1] -= trailing
        ruby_before, ruby_after = self._ruby_edges(start, end)
        advances[0] += ruby_before
        advances[-1] += ruby_after
        extent = sum(advances) + sum(gaps)
        delta = max(0.0, extent - limit) if not hanging else 0.0
        self._compress(start, advances, gaps, shifts, leading, trailing, delta)
        extent = sum(advances) + sum(gaps)
        if (
            self.options["line_adjustment"] == "justify"
            and end < len(self.atoms)
            and extent < limit
        ):
            eligible = [
                i
                for i in range(1, len(advances))
                if self.atoms[start + i - 1].category != "opening"
                and self.atoms[start + i].category not in {"closing", "stop"}
                and not self.policy.is_inseparable_pair(
                    self.atoms[start + i - 1].text[-1], self.atoms[start + i].text[0]
                )
            ]
            if eligible:
                amount = min(
                    (limit - extent) / len(eligible),
                    float(self.options["expansion_limit_em"]) * self.em,
                )
                for i in eligible:
                    rule = self.custom_gaps.get(start + i)
                    growth = amount
                    if rule is not None:
                        growth = min(growth, rule["maximum"] * rule["unit"] - gaps[i])
                    gaps[i] += max(0.0, growth)
        positions, cursor = [], 0.0
        for i, atom in enumerate(self.atoms[start:end]):
            cursor += gaps[i]
            positions.append(AtomPosition(atom, cursor, advances[i], shifts[i]))
            cursor += advances[i]
        return BrokenLine(
            end, tuple(positions), cursor, hanging, cursor > limit + 1e-6 and not hanging
        )

    def _compress(self, start, advances, gaps, shifts, leading, trailing, delta):
        if self.options["line_adjustment"] != "none" and delta:
            order = sorted(
                range(len(advances)),
                key=lambda i: (self.custom_gaps.get(start + i, {}).get("priority", 0), -i),
            )
            for i in order:
                atom = self.atoms[start + i]
                capacity = self.capacities[start + i]
                if start + i in self.custom_gaps and i == 0:
                    capacity = 0.0
                if i == 0:
                    capacity -= max(0.0, leading)
                if i == len(advances) - 1:
                    capacity -= max(0.0, trailing)
                amount = min(delta, max(0.0, capacity))
                if (
                    i == len(advances) - 1
                    and atom.category in {"closing", "stop", "middle"}
                    and amount
                ):
                    amount = max(0.0, capacity)
                if atom.category == "opening":
                    shifts[i] -= amount
                if gaps[i] > 0:
                    taken = min(gaps[i], amount)
                    gaps[i] -= taken
                    amount -= taken
                    delta -= taken
                advances[i] -= amount
                delta -= amount
                if delta <= 1e-8:
                    break
