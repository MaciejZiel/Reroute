"""Tiny dependency-free SVG charts that follow the reader's light or dark theme."""

from __future__ import annotations

from html import escape

STYLE = """<style>
svg{--ink:#1d2824;--muted:#5b6a64;--grid:#dfe6e2;--base:#7d8b85;--model:#0e8f68;--accent:#d9622e}
@media (prefers-color-scheme: dark){svg{--ink:#e8eee9;--muted:#9eaba4;--grid:#2c3a35;--base:#9eaba4;--model:#6fd9b6;--accent:#ff8d74}}
text{font:12px system-ui,-apple-system,"Segoe UI",sans-serif;fill:var(--ink)}
.m{fill:var(--muted)}.t{font-size:14px;font-weight:600}.g{stroke:var(--grid);stroke-width:1}
</style>"""


def _wrap(width: int, height: int, title: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
        f'role="img" aria-label="{escape(title)}">{STYLE}'
        f'<text class="t" x="16" y="24">{escape(title)}</text>{body}</svg>\n'
    )


def grouped_bars(
    title: str, groups: list[str], series: dict[str, list[float]], unit: str = "s"
) -> str:
    """Vertical bars: one cluster per group, one bar per series (first = baseline colour)."""
    width, height, left, bottom, top = 640, 300, 48, 54, 52
    peak = max(v for values in series.values() for v in values) * 1.12
    plot_h = height - top - bottom
    cluster = (width - left - 16) / len(groups)
    bar = min(34, (cluster - 18) / len(series))
    colours = ["var(--base)", "var(--model)", "var(--accent)"]
    body = []
    for tick in range(5):
        value = peak * tick / 4
        y = top + plot_h * (1 - tick / 4)
        body.append(f'<line class="g" x1="{left}" x2="{width - 16}" y1="{y:.1f}" y2="{y:.1f}"/>')
        body.append(
            f'<text class="m" x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{value:.0f}</text>'
        )
    for g, label in enumerate(groups):
        x0 = left + g * cluster + (cluster - bar * len(series)) / 2
        for s, (name, values) in enumerate(series.items()):
            h = plot_h * values[g] / peak
            x, y = x0 + s * bar, top + plot_h - h
            body.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar - 2:.1f}" height="{h:.1f}" rx="3" '
                f'fill="{colours[s % 3]}"><title>{escape(name)}, {escape(label)}: '
                f"{values[g]:.1f} {unit}</title></rect>"
            )
            body.append(
                f'<text x="{x + (bar - 2) / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle">'
                f"{values[g]:.0f}</text>"
            )
        body.append(
            f'<text class="m" x="{left + g * cluster + cluster / 2:.1f}" y="{height - bottom + 18}" '
            f'text-anchor="middle">{escape(label)}</text>'
        )
    for s, name in enumerate(series):
        x = left + s * 210
        body.append(f'<rect x="{x}" y="34" width="10" height="10" rx="2" fill="{colours[s % 3]}"/>')
        body.append(f'<text x="{x + 16}" y="43">{escape(name)}</text>')
    body.append(f'<text class="m" x="16" y="{height - 8}">MAE in {unit}, lower is better</text>')
    return _wrap(width, height, title, "".join(body))


def horizontal_bars(title: str, items: list[tuple[str, float]], unit: str) -> str:
    width, row, left, top = 640, 26, 250, 44
    peak = max(v for _, v in items) * 1.15 or 1
    height = top + row * len(items) + 30
    body = []
    for i, (label, value) in enumerate(items):
        y = top + i * row
        w = (width - left - 70) * value / peak
        body.append(f'<text x="{left - 8}" y="{y + 14}" text-anchor="end">{escape(label)}</text>')
        body.append(
            f'<rect x="{left}" y="{y + 2}" width="{w:.1f}" height="16" rx="3" fill="var(--model)"/>'
        )
        body.append(f'<text x="{left + w + 6:.1f}" y="{y + 14}">{value:.1f}</text>')
    body.append(f'<text class="m" x="16" y="{height - 8}">{escape(unit)}</text>')
    return _wrap(width, height, title, "".join(body))


def line_panels(title: str, panels: list[tuple[str, list[float], list[float]]], unit: str) -> str:
    """A grid of small line charts sharing one y scale: (feature, x grid, effect in seconds)."""
    cols, cw, ch, top, left = 2, 300, 150, 62, 44
    rows = (len(panels) + cols - 1) // cols
    width, height = cols * (cw + 20) + 20, top + rows * (ch + 46) + 24
    lo = min(min(ys) for _, _, ys in panels)
    hi = max(max(ys) for _, _, ys in panels)
    pad = (hi - lo) * 0.1 or 1
    lo, hi = lo - pad, hi + pad
    body = []
    for i, (name, xs, ys) in enumerate(panels):
        ox, oy = 20 + (i % cols) * (cw + 20) + left - 20, top + (i // cols) * (ch + 46)
        pw = cw - left + 20
        body.append(f'<text x="{ox}" y="{oy - 6}">{escape(name)}</text>')
        zero = oy + ch * (hi - 0) / (hi - lo)
        body.append(f'<line class="g" x1="{ox}" x2="{ox + pw}" y1="{zero:.1f}" y2="{zero:.1f}"/>')
        x_lo, x_hi = min(xs), max(xs)
        span = (x_hi - x_lo) or 1
        points = " ".join(
            f"{ox + pw * (x - x_lo) / span:.1f},{oy + ch * (hi - y) / (hi - lo):.1f}"
            for x, y in zip(xs, ys, strict=True)
        )
        body.append(
            f'<polyline points="{points}" fill="none" stroke="var(--model)" stroke-width="2" '
            'stroke-linejoin="round"/>'
        )
        body.append(f'<text class="m" x="{ox}" y="{oy + ch + 16}">{x_lo:.4g}</text>')
        body.append(
            f'<text class="m" x="{ox + pw}" y="{oy + ch + 16}" text-anchor="end">{x_hi:.4g}</text>'
        )
        body.append(f'<text class="m" x="{ox - 6}" y="{oy + 8}" text-anchor="end">{hi:.0f}</text>')
        body.append(f'<text class="m" x="{ox - 6}" y="{oy + ch}" text-anchor="end">{lo:.0f}</text>')
    body.append(f'<text class="m" x="16" y="{height - 8}">{escape(unit)}</text>')
    return _wrap(width, height, title, "".join(body))


def coverage_dots(
    title: str,
    rows: list[tuple[str, float, float, float, int, str, bool]],
    nominal: float,
    note: str,
) -> str:
    """Observed coverage per group as a dot with its bootstrap interval, against the nominal level.

    ``rows`` are (label, coverage, low, high, n, window, held_out) with fractions; a hollow dot is
    a window the interval was tuned on, a filled one a held-out window. The dashed line is the
    level the interval promises."""
    width, height, left, bottom, top = 640, 312, 56, 74, 52
    plot_h = height - top - bottom
    lo, hi = 0.6, 1.0
    cluster = (width - left - 16) / max(len(rows), 1)  # no trusted bin: an empty chart

    def y_of(value: float) -> float:
        return top + plot_h * (1 - (min(max(value, lo), hi) - lo) / (hi - lo))

    body = []
    for tick in range(5):
        value = lo + (hi - lo) * tick / 4
        y = y_of(value)
        body.append(f'<line class="g" x1="{left}" x2="{width - 16}" y1="{y:.1f}" y2="{y:.1f}"/>')
        body.append(
            f'<text class="m" x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{value:.0%}</text>'
        )
    y = y_of(nominal)
    body.append(
        f'<line x1="{left}" x2="{width - 16}" y1="{y:.1f}" y2="{y:.1f}" stroke="var(--accent)" '
        'stroke-width="1.5" stroke-dasharray="5 4"/>'
    )
    body.append(
        f'<text x="{width - 16}" y="{y - 6:.1f}" text-anchor="end" fill="var(--accent)" '
        f'style="fill:var(--accent)">nominal {nominal:.0%}</text>'
    )
    for i, (label, value, low, high, n, window, held_out) in enumerate(rows):
        x = left + i * cluster + cluster / 2
        body.append(
            f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y_of(low):.1f}" y2="{y_of(high):.1f}" '
            'stroke="var(--model)" stroke-width="2"/>'
        )
        fill = "var(--model)" if held_out else "var(--bg, transparent)"
        body.append(
            f'<circle cx="{x:.1f}" cy="{y_of(value):.1f}" r="5" fill="{fill}" '
            f'stroke="var(--model)" stroke-width="2">'
            f"<title>{escape(label)} ({escape(window)}): {value:.1%} ({low:.1%} to {high:.1%}), "
            f"{n:,} predictions</title></circle>"
        )
        body.append(f'<text x="{x + 9:.1f}" y="{y_of(value) + 4:.1f}">{value:.1%}</text>')
        for line, text in enumerate((label, window, f"n = {n:,}")):
            body.append(
                f'<text class="{"" if line == 0 else "m"}" x="{x:.1f}" '
                f'y="{height - bottom + 18 + 15 * line}" text-anchor="middle">{escape(text)}</text>'
            )
    body.append(f'<text class="m" x="16" y="{height - 8}">{escape(note)}</text>')
    return _wrap(width, height, title, "".join(body))
