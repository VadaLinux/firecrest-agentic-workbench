import re
import datetime
from pathlib import Path
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TIMER_PATH = REPO_ROOT / "systemd" / "firecrest-nightly-report.timer"
SCRIPT_PATH = REPO_ROOT / "scripts" / "nightly_report.sh"
REINGEST_TIMER_PATH = REPO_ROOT / "systemd" / "firecrest-reingest.timer"


def parse_time_str(t_str: str) -> datetime.time:
    parts = [int(p) for p in t_str.strip().split(":")]
    if len(parts) == 2:
        return datetime.time(hour=parts[0], minute=parts[1])
    elif len(parts) == 3:
        return datetime.time(hour=parts[0], minute=parts[1], second=parts[2])
    raise ValueError(f"Cannot parse time: {t_str}")


def test_timer_and_script_window_constants_are_compatible():
    """
    VDLP-61: Verify that firecrest-nightly-report.timer and scripts/nightly_report.sh
    constants are in sync so the script does not skip the morning run.

    The timer fires at morning T (e.g. 07:00).
    The script's WINDOW_END for target date T must close *before* the timer fires
    (e.g. 06:00 < 07:00), otherwise NOW < WINDOW_END causes the script to exit 0
    without producing the report, delaying every report by 24h permanently.
    """
    assert TIMER_PATH.is_file(), f"Timer file not found at {TIMER_PATH}"
    assert SCRIPT_PATH.is_file(), f"Script file not found at {SCRIPT_PATH}"

    timer_content = TIMER_PATH.read_text()
    script_content = SCRIPT_PATH.read_text()

    # Extract OnCalendar from timer
    timer_match = re.search(r"OnCalendar=\*-\*-\*\s+([0-9]{2}:[0-9]{2}:[0-9]{2})", timer_content)
    assert timer_match, f"Could not find OnCalendar time in {TIMER_PATH}"
    timer_time_str = timer_match.group(1)
    timer_time = parse_time_str(timer_time_str)

    # Extract WINDOW_END_TIME from script
    window_end_match = re.search(r'WINDOW_END_TIME="\$\{WINDOW_END_TIME:-([0-9]{2}:[0-9]{2}:[0-9]{2})\}"', script_content)
    assert window_end_match, f"Could not find default WINDOW_END_TIME in {SCRIPT_PATH}"
    window_end_time_str = window_end_match.group(1)
    window_end_time = parse_time_str(window_end_time_str)

    # Extract WINDOW_START_TIME from script
    window_start_match = re.search(r'WINDOW_START_TIME="\$\{WINDOW_START_TIME:-([0-9]{2}:[0-9]{2}:[0-9]{2})\}"', script_content)
    assert window_start_match, f"Could not find default WINDOW_START_TIME in {SCRIPT_PATH}"
    window_start_time_str = window_start_match.group(1)
    window_start_time = parse_time_str(window_start_time_str)

    # 1. Window end must be earlier than the timer run time on the morning of target_date
    assert window_end_time < timer_time, (
        f"Incompatible schedule: script WINDOW_END ({window_end_time_str}) is not before "
        f"timer OnCalendar ({timer_time_str}). The script will skip execution on target morning!"
    )

    # 2. Window start must cover the evening before (e.g. 20:00 or 22:00)
    assert window_start_time.hour >= 18, (
        f"WINDOW_START_TIME ({window_start_time_str}) is expected to start in the evening (>= 18:00)"
    )

    # 3. Check reingest timer if present
    if REINGEST_TIMER_PATH.is_file():
        reingest_content = REINGEST_TIMER_PATH.read_text()
        reingest_match = re.search(r"OnCalendar=\*-\*-\*\s+([0-9]{2}:[0-9]{2}:[0-9]{2})", reingest_content)
        if reingest_match:
            reingest_time = parse_time_str(reingest_match.group(1))
            assert window_start_time <= reingest_time, (
                f"WINDOW_START_TIME ({window_start_time_str}) must be <= reingest timer time ({reingest_match.group(1)})"
            )


@pytest.mark.parametrize(
    "target_date_str, run_datetime_str, expected_closed",
    [
        # Same day, before 06:00 -> window not closed yet
        ("2026-09-17", "2026-09-17 05:59:00", False),
        # Same day at 06:00:00 -> window closed
        ("2026-09-17", "2026-09-17 06:00:00", True),
        # Same day at 07:00:00 (timer run time) -> window closed, report produced on time!
        ("2026-09-17", "2026-09-17 07:00:00", True),
        # Previous day at 07:00:00 -> window not closed yet
        ("2026-09-17", "2026-09-16 07:00:00", False),
    ],
)
def test_window_closure_logic(target_date_str, run_datetime_str, expected_closed):
    """
    Simulate the window closure condition used in nightly_report.sh:
    WINDOW_END = target_date 06:00:00 Europe/Zurich
    NOW = run_datetime
    """
    target_date = datetime.date.fromisoformat(target_date_str)
    window_end = datetime.datetime.combine(target_date, datetime.time(6, 0, 0))
    run_dt = datetime.datetime.fromisoformat(run_datetime_str)

    is_closed = run_dt >= window_end
    assert is_closed == expected_closed
