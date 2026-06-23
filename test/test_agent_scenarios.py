import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNNER = PROJECT_ROOT / "test" / "run_agent_scenarios.py"


class AgentScenarioEvalTests(unittest.TestCase):
    def run_scenarios(self, *args: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "report.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--output-root",
                    str(root / "outputs"),
                    "--report-json",
                    str(report),
                    *args,
                ],
                cwd=str(PROJECT_ROOT),
                text=True,
                capture_output=True,
                check=False,
                timeout=90,
            )
            if completed.returncode != 0:
                self.fail(
                    "Agent scenario runner failed\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )
            return json.loads(report.read_text(encoding="utf-8"))

    def test_default_offline_agent_scenarios_pass(self) -> None:
        report = self.run_scenarios()
        counts = report["counts"]
        self.assertEqual(counts.get("fail", 0), 0)
        self.assertEqual(counts.get("xpass", 0), 0)
        self.assertGreater(counts.get("pass", 0), 0)

    def test_known_issue_agent_scenarios_are_tracked_as_xfail(self) -> None:
        report = self.run_scenarios("--profile", "known_issue")
        counts = report["counts"]
        self.assertEqual(counts.get("fail", 0), 0)
        self.assertEqual(counts.get("xpass", 0), 0)
        self.assertGreater(counts.get("xfail", 0), 0)


if __name__ == "__main__":
    unittest.main()
