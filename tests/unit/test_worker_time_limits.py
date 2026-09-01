from math import isinf

from apps.worker.tasks import (
    NO_TIME_LIMIT,
    run_history_sync,
    run_initialization,
    run_memory_polish,
    run_mirror_sync,
)


def test_long_running_jobs_have_no_total_time_limit() -> None:
    assert isinf(NO_TIME_LIMIT)
    assert isinf(run_initialization.options["time_limit"])
    assert isinf(run_memory_polish.options["time_limit"])
    assert isinf(run_mirror_sync.options["time_limit"])
    assert isinf(run_history_sync.options["time_limit"])
