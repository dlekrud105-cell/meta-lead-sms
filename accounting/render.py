"""Plain-text rendering helpers shared by the CLI."""
from __future__ import annotations

from .money import fmt

RULE = '-'


def heading(text: str, width: int = 78) -> str:
    return f'\n{text}\n{"=" * min(width, max(len(text), 20))}'


def table(headers, rows, align=None, indent: str = '  ') -> str:
    """Render rows as an aligned text table. `align` is a string of l/r chars."""
    rows = [[('' if cell is None else str(cell)) for cell in row] for row in rows]
    if not rows:
        return f'{indent}(nothing to show)'
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    align = align or 'l' * len(headers)

    def line(cells):
        parts = []
        for index, cell in enumerate(cells):
            parts.append(str(cell).rjust(widths[index]) if align[index] == 'r'
                         else str(cell).ljust(widths[index]))
        return indent + '  '.join(parts).rstrip()

    out = [line(headers), indent + '  '.join(RULE * w for w in widths)]
    out.extend(line(row) for row in rows)
    return '\n'.join(out)


def amounts(*values):
    return [fmt(v) for v in values]


def money_rows(pairs):
    """[(label, amount)] -> table rows with the amount formatted."""
    return [[label, fmt(amount)] for label, amount in pairs]
