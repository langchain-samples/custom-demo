"""Assistant resources and file routes must be usable without loading the agent graph."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_resource_and_file_access_do_not_import_the_agent():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "class BlockAgent:\n"
            "    def find_spec(self, fullname, path=None, target=None):\n"
            "        if fullname == 'custom_demo.runtime.agent':\n"
            "            raise AssertionError('resource access loaded the agent graph')\n"
            "sys.meta_path.insert(0, BlockAgent())\n"
            "import custom_demo.resources.sandbox\n"
            "import custom_demo.web.sandbox\n",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
