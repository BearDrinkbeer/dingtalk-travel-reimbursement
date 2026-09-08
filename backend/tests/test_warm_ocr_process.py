import os
import time

import pytest

from app.services.process_jobs import (
    KillableProcessRunner,
    ProcessJobResourceLimit,
    ProcessJobTimeout,
)
from app.services.warm_ocr_process import WarmOcrProcess


@pytest.mark.asyncio
async def test_reuses_process_and_rebuilds_after_timeout():
    warm = WarmOcrProcess(allowed=(os.getpid, time.sleep), idle_seconds=30)
    runner = KillableProcessRunner(warm)
    try:
        first = await runner.run(os.getpid, timeout_seconds=5)
        assert await runner.run(os.getpid, timeout_seconds=5) == first
        with pytest.raises(ProcessJobTimeout):
            await runner.run(time.sleep, 10, timeout_seconds=0.1)
        assert await runner.run(os.getpid, timeout_seconds=5) != first
    finally:
        await runner.close()
        await warm.close()


@pytest.mark.asyncio
async def test_idle_and_job_limit_recycle_and_non_ocr_stays_isolated():
    warm = WarmOcrProcess(allowed=(os.getpid,), idle_seconds=0.1, max_jobs=2)
    runner = KillableProcessRunner(warm)
    try:
        first = await runner.run(os.getpid, timeout_seconds=5)
        assert await runner.run(os.getpid, timeout_seconds=5) == first
        second = await runner.run(os.getpid, timeout_seconds=5)
        assert second != first
        await runner.run(time.sleep, 0.2, timeout_seconds=5)
        assert await runner.run(os.getpid, timeout_seconds=5) != second
    finally:
        await runner.close()
        await warm.close()


@pytest.mark.asyncio
async def test_crashed_worker_is_discarded_without_replaying_job():
    warm = WarmOcrProcess(allowed=(os.getpid, os._exit))
    runner = KillableProcessRunner(warm)
    try:
        first = await runner.run(os.getpid, timeout_seconds=5)
        with pytest.raises(ProcessJobResourceLimit):
            await runner.run(os._exit, 1, timeout_seconds=5)
        assert await runner.run(os.getpid, timeout_seconds=5) != first
    finally:
        await runner.close()
        await warm.close()
