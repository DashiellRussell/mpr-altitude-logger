# TUI Results Browser — Implementation Plan

## Problem

After tests complete, results dump as plain scrollable text and the user is
dropped back to the raw "Press a key" menu. Arrow keys send escape sequences
(`\x1b[A`) which `_getch()` reads as 3 separate chars — `\x1b`, `[`, `A` —
triggering unwanted actions (e.g. `A` matches "Pressure Spike at Apogee").

The user wants to **stay in the two-column layout** after tests finish and
**browse results interactively**.

## Design

### Post-test interactive browser

After pytest completes, enter a **results browser mode** — a Rich `Live`
display with two columns and keyboard navigation:

```
╭──────── Test List ────────╮╭──────────── Detail ─────────────╮
│   TestConfigDefaults       ││ PASSED                          │
│ ▸   test_default_config    ││                                 │
│   TestConfigValidation     ││ TestConfigDefaults               │
│     test_negative_q_alt    ││   test_default_config_passes     │
│     test_negative_q_vel    ││                                 │
│     ...                    ││ Duration: 0.01s                  │
│                            ││                                 │
│                            ││                                 │
╰────────────────────────────╯╰─────────────────────────────────╯
  102 passed  │  W/S or ↑/↓: navigate  │  B: back  │  Q: quit
```

### Left panel: Test list

- All tests grouped by class (class names as cyan headers)
- Currently selected test has a `▸` marker and is highlighted/bold
- Scrolls to keep the selected test visible (window follows cursor)
- PASS tests show green, FAIL tests show red

### Right panel: Detail view

Shows detail for the **currently selected** test:

- **For passed tests**:
  - Class name, test name
  - PASSED badge
  - (Simple — just confirms it passed)

- **For failed tests**:
  - Class name, test name
  - FAILED badge
  - Full traceback with syntax highlighting:
    - `E ` lines (assertion errors) in red
    - `>` lines (failing code) in yellow
    - Other traceback lines dimmed
  - Right panel scrolls independently if traceback is long

### Bottom bar

Fixed status line (not a progress bar):
```
  102 passed, 0 failed  │  W/S or ↑/↓: navigate  │  B: back  │  Q: quit
```

### Keyboard

| Key | Action |
|-----|--------|
| `w` or `↑` | Move selection up |
| `s` or `↓` | Move selection down |
| `b` | Back to main menu |
| `q` | Quit the TUI entirely |
| `e` | Export results (future: write CSV/JSON for dashboard) |

### `_getch()` rewrite

Must properly handle escape sequences and return named actions:

```python
def _getch():
    """Read a single keypress. Returns str for normal keys, named strings for special keys."""
    if not sys.stdin.isatty():
        ch = sys.stdin.read(1)
        return ch if ch else 'q'
    import tty, termios, select
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == '\x1b':
            # Read the rest of the escape sequence
            if select.select([fd], [], [], 0.1)[0]:
                ch2 = sys.stdin.read(1)
                if ch2 == '[':
                    if select.select([fd], [], [], 0.1)[0]:
                        ch3 = sys.stdin.read(1)
                        if ch3 == 'A': return 'up'
                        if ch3 == 'B': return 'down'
                        if ch3 == 'C': return 'right'
                        if ch3 == 'D': return 'left'
                        # Consume any remaining bytes
                        while select.select([fd], [], [], 0.05)[0]:
                            sys.stdin.read(1)
            return None  # bare escape
        if ch == '\x03':  # Ctrl+C
            return 'q'
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch
```

### Results browser function

```python
def browse_results(all_results, passed_count, failed_count):
    """Interactive two-column results browser.

    all_results: list of (test_id, passed, longrepr)
    """
    # Build flat list of selectable items (tests only, not class headers)
    # Each item: (display_line, test_id, passed, longrepr, class_name)
    items = []
    for test_id, passed, longrepr in all_results:
        pieces = test_id.split("::")
        cls = pieces[1] if len(pieces) > 2 else ""
        test = pieces[-1]
        items.append((test, test_id, passed, longrepr, cls))

    cursor = 0  # index into items

    with Live(...) as live:
        while True:
            # Build left panel: test list with cursor
            # Build right panel: detail for items[cursor]
            # Build bottom bar: summary + key hints
            live.update(layout)

            key = _getch()
            if key in ('w', 'up'):
                cursor = max(0, cursor - 1)
            elif key in ('s', 'down'):
                cursor = min(len(items) - 1, cursor + 1)
            elif key == 'b':
                return 'back'  # back to menu
            elif key == 'q':
                return 'quit'  # exit TUI
            elif key == 'e':
                # Future: export
                pass
            elif key is None:
                continue  # ignore
```

### Left panel rendering

```python
def _render_test_list(items, cursor, max_lines):
    """Render the test list with cursor and class headers."""
    lines = []
    current_cls = None
    item_to_line = {}  # map item index → line index

    for i, (test, test_id, passed, longrepr, cls) in enumerate(items):
        if cls and cls != current_cls:
            current_cls = cls
            lines.append(f"  [bold cyan]{cls}[/bold cyan]")

        item_to_line[i] = len(lines)
        marker = "▸" if i == cursor else " "
        color = "green" if passed else "red"
        status = "PASS" if passed else "FAIL"
        if i == cursor:
            lines.append(f"  [bold]{marker} [{color}]{status}[/{color}]  {test}[/bold]")
        else:
            lines.append(f"  {marker} [{color}]{status}[/{color}]  [dim]{test}[/dim]")

    # Scroll window to keep cursor visible
    cursor_line = item_to_line.get(cursor, 0)
    # ... calculate visible window around cursor_line ...

    return "\n".join(visible_lines)
```

### Integration with run_pytest()

```python
def run_pytest():
    # ... existing pytest run with live progress ...

    # After tests complete, enter results browser
    action = browse_results(plugin.all_results, plugin.passed, plugin.failed)

    if action == 'quit':
        sys.exit(0)
    # 'back' returns to menu normally
```

### Main loop changes

```python
def main():
    console.print(render_menu())
    while True:
        # ... existing key handling ...
        elif choice == 't':
            run_pytest()
            console.print(render_menu())  # only reached if 'back'
```

## What changes

| File | Change |
|------|--------|
| `tools/simulator_tui.py` | Rewrite `_getch()` to return `'up'`/`'down'` for arrow keys |
| `tools/simulator_tui.py` | Add `browse_results()` function — interactive two-column browser |
| `tools/simulator_tui.py` | Remove post-test text dump (the `for test_id, passed, longrepr` loop) |
| `tools/simulator_tui.py` | `run_pytest()` calls `browse_results()` after pytest finishes |
| `tools/simulator_tui.py` | Main loop: handle `run_pytest()` return for quit vs back |

## What stays the same

- The live two-column progress view during test execution (unchanged)
- The `_TUIProgressPlugin` class and `_build_detail_lines()` helper
- The scenario menu and individual scenario runner
- All test code in `tests/`
