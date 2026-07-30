"""macOS Keychain access without placing secrets in command arguments."""

from __future__ import annotations

import errno
import os
import pty
import select
import shutil
import subprocess
import time
from dataclasses import dataclass

DEFAULT_SERVICE = "com.codex.claude-pool.oauth-token"


class KeychainError(RuntimeError):
    """Raised when a Keychain operation fails."""


@dataclass(frozen=True)
class MacOSKeychain:
    """Store one OAuth token per account alias in the login Keychain."""

    service: str = DEFAULT_SERVICE
    security_command: str = "/usr/bin/security"

    def validate(self) -> None:
        if not shutil.which(self.security_command):
            raise KeychainError(
                f"macOS Keychain command not found: {self.security_command}"
            )

    def exists(self, account: str) -> bool:
        result = subprocess.run(
            self._find_command(account, reveal=False),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def has_token(self, account: str) -> bool:
        """Return whether an account has a non-empty Keychain token."""

        try:
            self.get(account)
        except KeychainError:
            return False
        return True

    def get(self, account: str) -> str:
        result = subprocess.run(
            self._find_command(account, reveal=True),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "item not found"
            raise KeychainError(
                f'Could not read the OAuth token for "{account}": {detail}'
            )

        token = result.stdout.strip()
        if not token:
            raise KeychainError(f'The OAuth token for "{account}" is empty')
        return token

    def store_interactive(self, account: str) -> None:
        """Ask Keychain's own hidden prompt for the token.

        Passing ``-w`` without a value is intentional. The ``security`` command
        prompts on the terminal, which keeps the token out of argv and shell
        history.
        """

        result = subprocess.run(
            self._store_command(account),
            check=False,
        )
        if result.returncode != 0:
            raise KeychainError(
                f'Keychain did not store the OAuth token for "{account}"'
            )

    def store(self, account: str, token: str) -> None:
        """Store a captured token through a PTY so it never appears in argv."""

        token = token.strip()
        if not token:
            raise KeychainError(f'The OAuth token for "{account}" is empty')
        returncode = self._store_with_pty(self._store_command(account), token)
        if returncode != 0:
            raise KeychainError(
                f'Keychain did not store the OAuth token for "{account}"'
            )
        if self.get(account) != token:
            raise KeychainError(
                f'Keychain did not preserve the OAuth token for "{account}"'
            )

    def _store_with_pty(self, command: list[str], token: str) -> int:
        """Answer Keychain's two hidden password prompts on its controlling TTY."""

        child_pid, master_fd = pty.fork()
        if child_pid == 0:
            try:
                os.execv(command[0], command)
            except OSError:
                os._exit(127)

        prompts = (
            b"password data for new item:",
            b"retype password for new item:",
        )
        prompt_index = 0
        buffer = bytearray()
        deadline = time.monotonic() + 15
        status: int | None = None
        try:
            while status is None:
                if time.monotonic() >= deadline:
                    try:
                        os.kill(child_pid, 15)
                    except ProcessLookupError:
                        pass
                    _, status = os.waitpid(child_pid, 0)
                    break

                readable, _, _ = select.select([master_fd], [], [], 0.25)
                if readable:
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        data = b""
                    if data:
                        buffer.extend(data)
                        if (
                            prompt_index < len(prompts)
                            and prompts[prompt_index] in buffer
                        ):
                            os.write(master_fd, token.encode("utf-8") + b"\n")
                            prompt_index += 1
                            buffer.clear()

                waited_pid, child_status = os.waitpid(child_pid, os.WNOHANG)
                if waited_pid == child_pid:
                    status = child_status
        finally:
            os.close(master_fd)

        if prompt_index != len(prompts):
            return 1
        return os.waitstatus_to_exitcode(status)

    def remove(self, account: str) -> bool:
        if not self.exists(account):
            return False
        result = subprocess.run(
            [
                self.security_command,
                "delete-generic-password",
                "-a",
                account,
                "-s",
                self.service,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or "unknown Keychain error"
            raise KeychainError(
                f'Could not remove the OAuth token for "{account}": {detail}'
            )
        return True

    def _find_command(self, account: str, *, reveal: bool) -> list[str]:
        command = [
            self.security_command,
            "find-generic-password",
            "-a",
            account,
            "-s",
            self.service,
        ]
        if reveal:
            command.append("-w")
        return command

    def _store_command(self, account: str) -> list[str]:
        # Keep -w last. With no value, security prompts without echo.
        return [
            self.security_command,
            "add-generic-password",
            "-U",
            "-a",
            account,
            "-s",
            self.service,
            "-l",
            f"Claude Pool OAuth: {account}",
            "-w",
        ]
