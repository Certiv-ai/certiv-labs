"""Pseudo-terminal bridge used to observe interactive Claude Code output."""

from __future__ import annotations

import errno
import fcntl
import os
import pty
import selectors
import signal
import sys
import termios
import time
import tty
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .limits import LimitDetector, LimitEvent


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    limit_event: LimitEvent | None = None
    restart_requested: bool = False


def run_interactive(
    command: Sequence[str],
    *,
    env: dict[str, str],
    detector: LimitDetector,
    on_limit: Callable[[LimitEvent], bool],
    on_output: Callable[[bytes], None] | None = None,
) -> RunResult:
    """Run a command under a PTY while preserving its normal terminal UI."""

    child_pid, master_fd = pty.fork()
    if child_pid == 0:
        try:
            os.execvpe(command[0], list(command), env)
        except OSError as exc:
            print(f"claude-pool: could not start {command[0]}: {exc}", file=sys.stderr)
            os._exit(127)

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    stdin_is_tty = os.isatty(stdin_fd)
    original_terminal = termios.tcgetattr(stdin_fd) if stdin_is_tty else None
    previous_winch = signal.getsignal(signal.SIGWINCH)
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_hup = signal.getsignal(signal.SIGHUP)
    selector = selectors.DefaultSelector()
    restart_requested = False
    limit_event: LimitEvent | None = None

    def resize_child(_signum: int | None = None, _frame: object = None) -> None:
        if not stdin_is_tty:
            return
        try:
            size = fcntl.ioctl(stdin_fd, termios.TIOCGWINSZ, b"\0" * 8)
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, size)
            os.kill(child_pid, signal.SIGWINCH)
        except OSError:
            pass

    def terminate_child(_signum: int, _frame: object = None) -> None:
        _signal_process_group(child_pid, signal.SIGTERM)

    try:
        if stdin_is_tty:
            tty.setraw(stdin_fd)
            selector.register(stdin_fd, selectors.EVENT_READ, "stdin")
        selector.register(master_fd, selectors.EVENT_READ, "child")
        signal.signal(signal.SIGWINCH, resize_child)
        signal.signal(signal.SIGTERM, terminate_child)
        signal.signal(signal.SIGHUP, terminate_child)
        resize_child()

        while True:
            for key, _ in selector.select(timeout=0.25):
                if key.data == "stdin":
                    try:
                        data = os.read(stdin_fd, 4096)
                    except OSError:
                        data = b""
                    if not data:
                        try:
                            selector.unregister(stdin_fd)
                        except (KeyError, ValueError):
                            pass
                    else:
                        _write_all(master_fd, data)
                    continue

                try:
                    data = os.read(master_fd, 65_536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        data = b""
                    else:
                        raise

                if not data:
                    try:
                        selector.unregister(master_fd)
                    except (KeyError, ValueError):
                        pass
                    break

                _write_all(stdout_fd, data)
                if on_output is not None:
                    on_output(data)
                event = detector.feed(data)
                if event is not None:
                    limit_event = event
                    restart_requested = on_limit(event)
                    if restart_requested:
                        # Give Claude a moment to finish rendering the limit card.
                        time.sleep(0.15)
                        _signal_process_group(child_pid, signal.SIGTERM)
                        break

            if restart_requested:
                break

            waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
            if waited_pid == child_pid:
                return RunResult(
                    exit_code=os.waitstatus_to_exitcode(status),
                    limit_event=limit_event,
                    restart_requested=False,
                )

            if not selector.get_map():
                break

        if restart_requested:
            status = _wait_for_child(child_pid, timeout=2.0)
            if status is None:
                _signal_process_group(child_pid, signal.SIGKILL)
                _, status = os.waitpid(child_pid, 0)
            return RunResult(
                exit_code=os.waitstatus_to_exitcode(status),
                limit_event=limit_event,
                restart_requested=True,
            )

        _, status = os.waitpid(child_pid, 0)
        return RunResult(exit_code=os.waitstatus_to_exitcode(status))
    finally:
        selector.close()
        try:
            os.close(master_fd)
        except OSError:
            pass
        signal.signal(signal.SIGWINCH, previous_winch)
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGHUP, previous_hup)
        if original_terminal is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, original_terminal)


def _wait_for_child(pid: int, *, timeout: float) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waited_pid, status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return status
        time.sleep(0.05)
    return None


def _signal_process_group(pid: int, signum: int) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        return
    except OSError:
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            pass


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]
