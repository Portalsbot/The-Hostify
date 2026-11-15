from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LocalRuntimeArchitectureTests(unittest.TestCase):
    def read_source(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_runtime_modules_parse(self):
        modules = (
            "config.py",
            "database.py",
            "deployer.py",
            "manager.py",
            "watchdog.py",
            "plugins/deploy_flow.py",
            "plugins/dashboard.py",
            "plugins/owner.py",
        )
        for module in modules:
            with self.subTest(module=module):
                ast.parse(self.read_source(module), filename=module)

    def test_deploy_flow_uses_local_python_and_docker(self):
        source = self.read_source("plugins/deploy_flow.py")
        self.assertIn('callback_data="hostify:deploy:type:python"', source)
        self.assertIn('callback_data="hostify:deploy:type:docker"', source)
        self.assertIn("deploy_bot(", source)
        self.assertIn("runtime_type=runtime_type", source)
        self.assertNotIn("deploy_to_render", source)
        self.assertNotIn("render_deployer", source)

    def test_deployer_has_local_python_and_docker_paths(self):
        source = self.read_source("deployer.py")
        self.assertIn("async def deploy_bot(", source)
        self.assertIn('runtime_type: str = "python"', source)
        self.assertIn('if runtime_type == "docker":', source)
        self.assertIn('["build", "--tag", image_name', source)
        self.assertIn('"--restart", "unless-stopped"', source)
        self.assertIn('"Dockerfile"', source)

    def test_docker_engine_is_checked_before_build(self):
        source = self.read_source("deployer.py")
        self.assertIn("async def _docker_engine_status()", source)
        self.assertIn("await _docker_engine_status()", source)
        self.assertIn("Native Render Python services do not provide one.", source)

    def test_folder_recovery_uses_saved_github_source(self):
        source = self.read_source("deployer.py")
        self.assertIn("async def _restore_python_bot_folder", source)
        self.assertIn("async def _restore_docker_bot_folder", source)
        self.assertIn("await _restore_python_bot_folder(name, bot_dir, repo_url)", source)
        self.assertIn("await _restore_docker_bot_folder(name, bot_dir, repo_url)", source)

    def test_mongo_records_local_runtime_metadata(self):
        source = self.read_source("database.py")
        self.assertIn('runtime_type: str = "python"', source)
        self.assertIn('"runtime_type": runtime_type', source)
        self.assertIn('"deployment_provider": "local"', source)
        self.assertNotIn("add_render_bot", source)
        self.assertNotIn("update_render_deployment", source)

    def test_controller_no_longer_creates_render_services(self):
        checked = (
            "config.py",
            "plugins/deploy_flow.py",
            "deployer.py",
            "database.py",
            "manager.py",
            "watchdog.py",
        )
        for module in checked:
            source = self.read_source(module)
            with self.subTest(module=module):
                self.assertNotIn("RENDER_API_KEY", source)
                self.assertNotIn("RENDER_WORKSPACE_ID", source)
                self.assertNotIn("deploy_to_render", source)


if __name__ == "__main__":
    unittest.main()
