# terminal_ui.py - Clean terminal formatting helpers

from log_utils import terminal_time


def section(title: str) -> None:
    line = "=" * 72
    print()
    print(line)
    print(f"{title} | {terminal_time()}")
    print(line)


def table(title: str, rows: list[tuple[str, object]]) -> None:
    print()
    print(f"{title}")
    print("-" * 72)
    label_width = max((len(str(label)) for label, _ in rows), default=12)

    for label, value in rows:
        print(f"{str(label):<{label_width}} : {value}")


def note(message: str) -> None:
    print(f"[{terminal_time()}] {message}")
