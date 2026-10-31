"""Small dependency-free Markdown renderer for terminal output."""

import re
import shutil
import textwrap


_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [part.replace(r"\|", "|").strip()
            for part in re.split(r"(?<!\\)\|", line)]


def _plain(text: str) -> str:
    return re.sub(r"(\*\*|__|`)(.*?)(\1)", r"\2", text)


def _table(lines: list[str], width: int) -> str | None:
    if len(lines) < 2:
        return None
    rows = [_cells(line) for line in lines]
    columns = len(rows[0])
    if columns == 0 or any(len(row) != columns for row in rows):
        return None
    if not all(_SEPARATOR_CELL.match(cell.replace(" ", ""))
               for cell in rows[1]):
        return None

    alignments = []
    for cell in rows[1]:
        cell = cell.replace(" ", "")
        if cell.startswith(":") and cell.endswith(":"):
            alignments.append("center")
        elif cell.endswith(":"):
            alignments.append("right")
        else:
            alignments.append("left")

    data = [[_plain(cell) for cell in row] for row in [rows[0], *rows[2:]]]
    natural = [max(1, max(len(row[i]) for row in data)) for i in range(columns)]
    available = max(columns, width - (3 * columns + 1))
    minimum = 3 if available >= columns * 3 else 1
    sizes = [min(value, minimum) for value in natural]
    budget = available - sum(sizes)
    while budget > 0 and any(sizes[i] < natural[i] for i in range(columns)):
        for i in range(columns):
            if budget <= 0:
                break
            if sizes[i] < natural[i]:
                sizes[i] += 1
                budget -= 1

    def border(left, middle, right, fill="─"):
        return left + middle.join(fill * (size + 2) for size in sizes) + right

    def format_row(row):
        wrapped = [textwrap.wrap(cell, size, break_long_words=True,
                                 break_on_hyphens=False) or [""]
                   for cell, size in zip(row, sizes)]
        height = max(len(parts) for parts in wrapped)
        output = []
        for line_no in range(height):
            rendered = []
            for i, parts in enumerate(wrapped):
                value = parts[line_no] if line_no < len(parts) else ""
                if alignments[i] == "right":
                    value = value.rjust(sizes[i])
                elif alignments[i] == "center":
                    value = value.center(sizes[i])
                else:
                    value = value.ljust(sizes[i])
                rendered.append(f" {value} ")
            output.append("│" + "│".join(rendered) + "│")
        return output

    output = [border("┌", "┬", "┐")]
    output.extend(format_row(data[0]))
    output.append(border("├", "┼", "┤"))
    for row in data[1:]:
        output.extend(format_row(row))
    output.append(border("└", "┴", "┘"))
    return "\n".join(output)


def render_terminal_markdown(text: str, width: int | None = None) -> str:
    """Render Markdown tables, wrapping cells to the terminal width."""
    width = max(20, width or shutil.get_terminal_size((100, 24)).columns)
    lines = text.splitlines()
    output = []
    table_lines = []
    in_fence = False

    def flush():
        if not table_lines:
            return
        rendered = _table(table_lines, width)
        output.extend(rendered.splitlines() if rendered else table_lines)
        table_lines.clear()

    for line in lines:
        if line.strip().startswith("```"):
            flush()
            in_fence = not in_fence
            output.append(line)
        elif not in_fence and line.strip().startswith("|"):
            table_lines.append(line)
        else:
            flush()
            output.append(line)
    flush()
    result = "\n".join(output)
    if text.endswith("\n"):
        result += "\n"
    return result


class TerminalMarkdownStream:
    """Buffer complete lines so streamed Markdown tables can be rendered."""

    def __init__(self, emit, width: int | None = None):
        self.emit = emit
        self.width = width
        self.buffer = ""
        self.table_lines = []
        self.in_fence = False

    def feed(self, text: str):
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self._line(line)

    def _line(self, line: str):
        if line.strip().startswith("```"):
            self._flush_table()
            self.in_fence = not self.in_fence
            self.emit(line + "\n")
        elif not self.in_fence and line.strip().startswith("|"):
            self.table_lines.append(line)
        else:
            self._flush_table()
            self.emit(line + "\n")

    def _flush_table(self):
        if self.table_lines:
            rendered = render_terminal_markdown(
                "\n".join(self.table_lines), self.width,
            )
            self.emit(rendered + "\n")
            self.table_lines.clear()

    def finish(self):
        if self.buffer:
            self._line(self.buffer)
            self.buffer = ""
        self._flush_table()
