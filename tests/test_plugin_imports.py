from __future__ import annotations

import importlib
import os
import unittest

from cryptography.fernet import Fernet

# Plugin modules import the controller configuration and database client.
# Supply safe test-only values so imports validate registration/circular imports
# without making a network connection or starting Telegram.
os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "test_api_hash")
os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN")
os.environ.setdefault("OWNER_ID", "8043083450")
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017/hostify_test")
os.environ.setdefault("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())


class PluginImportTests(unittest.TestCase):
    def test_all_plugins_import(self):
        modules = [
            "plugins.deploy_flow",
            "plugins.admins",
            "plugins.dashboard",
            "plugins.owner",
            "plugins.start",
            "plugins.controller",
            "plugins.diagnostics",
            "plugins.github",
            "plugins.message_editor",
            "plugins.channel",
            "plugins.ui",
            "command_menu",
        ]
        imported = {}
        for module in modules:
            with self.subTest(module=module):
                imported[module] = importlib.import_module(module)

        menu = imported["command_menu"]
        self.assertEqual(
            [item["command"] for item in menu.PUBLIC_COMMANDS],
            ["start", "command", "admins", "ping"],
        )
        self.assertNotIn("help", [item["command"] for item in menu.PUBLIC_COMMANDS])
        self.assertIn("deploy", [item["command"] for item in menu.ADMIN_COMMANDS])
        self.assertIn("commands", [item["command"] for item in menu.OWNER_COMMANDS])


if __name__ == "__main__":
    unittest.main()
