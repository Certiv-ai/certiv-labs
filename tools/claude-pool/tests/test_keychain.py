from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from claude_pool.keychain import KeychainError, MacOSKeychain


class KeychainTests(unittest.TestCase):
    def test_store_command_prompts_instead_of_putting_secret_in_argv(self) -> None:
        keychain = MacOSKeychain(
            service="test.claude-pool",
            security_command="/usr/bin/security",
        )

        command = keychain._store_command("primary")

        self.assertEqual(command[-1], "-w")
        self.assertNotIn("token-value", command)
        self.assertEqual(command[command.index("-a") + 1], "primary")

    def test_find_command_only_reveals_with_explicit_flag(self) -> None:
        keychain = MacOSKeychain()

        hidden = keychain._find_command("primary", reveal=False)
        revealed = keychain._find_command("primary", reveal=True)

        self.assertNotIn("-w", hidden)
        self.assertEqual(revealed[-1], "-w")

    @patch.object(MacOSKeychain, "get", return_value="token")
    def test_has_token_rejects_only_missing_or_empty_values(self, _get: object) -> None:
        self.assertTrue(MacOSKeychain().has_token("primary"))

    @patch.object(MacOSKeychain, "get", side_effect=KeychainError("empty"))
    def test_has_token_rejects_empty_values(self, _get: object) -> None:
        self.assertFalse(MacOSKeychain().has_token("backup"))

    @patch.object(MacOSKeychain, "get", return_value="secret-token")
    @patch.object(MacOSKeychain, "_store_with_pty", return_value=0)
    def test_store_sends_token_through_pty_not_argv(
        self,
        store_with_pty: Mock,
        _get: Mock,
    ) -> None:
        keychain = MacOSKeychain()

        keychain.store("primary", "secret-token")

        command, token = store_with_pty.call_args.args
        self.assertNotIn("secret-token", command)
        self.assertEqual(token, "secret-token")


if __name__ == "__main__":
    unittest.main()
