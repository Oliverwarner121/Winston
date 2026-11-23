import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessResult:
    return_code: int
    stdout: str
    stderr: str
    timed_out: bool


async def run_process(
    arguments: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    output_limit: int,
    environment: dict[str, str] | None = None,
) -> ProcessResult:
    process = await asyncio.create_subprocess_exec(
        *arguments,
        cwd=cwd,
        env=environment,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=(os.name == "posix"),
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_task = asyncio.create_task(_read_limited(process.stdout, output_limit))
    stderr_task = asyncio.create_task(_read_limited(process.stderr, output_limit))
    completion = asyncio.gather(process.wait(), stdout_task, stderr_task)
    timed_out = False

    try:
        _, stdout_result, stderr_result = await asyncio.wait_for(
            asyncio.shield(completion),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        timed_out = True
        await _terminate_process(process)
        _, stdout_result, stderr_result = await completion
    except asyncio.CancelledError:
        await _terminate_process(process)
        await completion
        raise

    stdout, stdout_truncated = stdout_result
    stderr, stderr_truncated = stderr_result
    return ProcessResult(
        return_code=process.returncode if process.returncode is not None else -1,
        stdout=_format_output(stdout, stdout_truncated),
        stderr=_format_output(stderr, stderr_truncated),
        timed_out=timed_out,
    )


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.returncode is None:
        process.kill()
    await process.wait()


async def _read_limited(
    stream: asyncio.StreamReader,
    output_limit: int,
) -> tuple[bytes, bool]:
    output = bytearray()
    truncated = False
    while chunk := await stream.read(4096):
        remaining = output_limit - len(output)
        if remaining > 0:
            output.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return bytes(output), truncated


def _format_output(output: bytes, truncated: bool) -> str:
    text = output.decode("utf-8", errors="replace")
    if truncated:
        return f"{text}\n[output truncated]"
    return text


def minimal_environment() -> dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    }
    return {key: value for key, value in environment.items() if value}
