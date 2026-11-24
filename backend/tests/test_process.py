import asyncio
import shutil

import pytest

from app.tools.implementations.process import run_process


def test_process_output_is_bounded(tmp_path) -> None:
    printf = shutil.which("printf")
    if printf is None:
        pytest.skip("printf is not available")

    result = asyncio.run(
        run_process(
            [printf, "123456789"],
            cwd=tmp_path,
            timeout_seconds=2,
            output_limit=4,
            environment={},
        )
    )

    assert result.return_code == 0
    assert result.stdout == "1234\n[output truncated]"


def test_process_timeout_terminates_command_tree(tmp_path) -> None:
    sleep = shutil.which("sleep")
    if sleep is None:
        pytest.skip("sleep is not available")

    result = asyncio.run(
        run_process(
            [sleep, "5"],
            cwd=tmp_path,
            timeout_seconds=0.05,
            output_limit=100,
            environment={},
        )
    )

    assert result.timed_out is True
    assert result.return_code != 0


def test_process_cancellation_terminates_command_tree(tmp_path) -> None:
    sleep = shutil.which("sleep")
    if sleep is None:
        pytest.skip("sleep is not available")

    async def cancel_process() -> None:
        task = asyncio.create_task(
            run_process(
                [sleep, "5"],
                cwd=tmp_path,
                timeout_seconds=10,
                output_limit=100,
                environment={},
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(cancel_process())
