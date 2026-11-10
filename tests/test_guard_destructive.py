"""scripts/guard_destructive.py (incident I1, 09.11.2026): no recursive deletion outside the session's scratchpad.

Fictional paths only; nothing is ever deleted (the hook only decides). A python script file named by a Bash command is
read from a temporary folder.

python -m unittest discover -s tests   (from the root of qa-assistant)
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "scripts")]

import guard_destructive as g

SID = "12c7189d-2fff-4671-8864-545dff532ad2"
OTHER = "aaaaaaaa-0000-0000-0000-000000000000"
PROJECT = "/private/tmp/claude-501/-Users-demo-proj"
SP = f"{PROJECT}/{SID}/scratchpad"


def bash(command, sid=SID):
    return g.decide({"tool_name": "Bash", "tool_input": {"command": command}, "session_id": sid})


def write(content, sid=SID, tool="Write"):
    key = {"Write": "content", "Edit": "new_string"}[tool]
    return g.decide({"tool_name": tool, "tool_input": {"file_path": f"{SP}/h.py", key: content}, "session_id": sid})


class Guard(unittest.TestCase):
    def test_a_recursive_rm_strictly_inside_the_scratchpad_is_allowed(self):
        for cmd in (f"rm -rf {SP}/a", f"rm -r {SP}/a/b {SP}/c", f"rm -fr {SP}/a", f"/bin/rm --recursive {SP}/a",
                    f"cd /x && rm -rf {SP}/a"):
            self.assertIsNone(bash(cmd), cmd)

    def test_the_incident_a_parent_folder_a_prefix_or_the_scratchpad_itself_is_refused(self):
        for cmd in (f"rm -rf {PROJECT}", f"rm -rf {SP}", f"rm -rf {SP}/", f"rm -rf {SP}/../..", "rm -rf /private/tmp/claude-501",
                    "rm -rf /", "rm -rf ~", f"rm -rf {PROJECT}/{OTHER}/scratchpad/x", "echo ok && rm -rf /Users/demo/x",
                    f"rm -rf {SP}/a; rm -rf {PROJECT}"):
            self.assertIn("only absolute paths strictly inside", bash(cmd) or "", cmd)

    def test_relative_paths_variables_globs_and_substitutions_are_refused(self):
        for cmd in ("rm -rf tmp/x", "rm -rf ./x", "rm -rf $DIR/x", f"rm -rf {SP}/*", "rm -rf $(pwd)/x", "rm -rf `pwd`/x", f"rm -rf {SP}/{{a,b}}"):
            self.assertIsNotNone(bash(cmd), cmd)

    def test_a_non_recursive_rm_and_ordinary_commands_are_not_affected(self):
        for cmd in (f"rm {PROJECT}/a.txt", "ls -la /private/tmp", "git status", "echo rm -rf is dangerous", "rmdir /x/empty"):
            self.assertIsNone(bash(cmd), cmd)

    def test_without_a_session_id_nothing_is_allowed_and_another_session_is_not_this_one(self):
        self.assertIsNotNone(bash(f"rm -rf {SP}/a", sid=None))
        self.assertIsNotNone(bash(f"rm -rf {SP}/a", sid="x"))
        self.assertIsNotNone(bash(f"rm -rf {SP}/a", sid=OTHER))

    def test_rmtree_in_the_command_text_needs_a_literal_path_inside_the_scratchpad(self):
        self.assertIsNone(bash(f"python3 -c \"import shutil; shutil.rmtree('{SP}/a')\""))
        self.assertIn("rmtree", bash("python3 -c \"import shutil; shutil.rmtree('/private/tmp/claude-501/-Users-demo')\"") or "")
        self.assertIn("not a literal", bash("python3 -c \"import shutil,sys; shutil.rmtree(sys.argv[1])\" /x") or "")
        self.assertIsNotNone(bash("python3 - <<'EOF'\nimport shutil\nshutil.rmtree(p)\nEOF"))

    def test_a_python_script_named_by_the_command_is_read_before_it_runs(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "htmlq.py")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("import os, shutil\nstray = '/private/tmp/claude-501/-Users-demo'\nif os.path.isdir(stray):\n"
                        "    shutil.rmtree('/private/tmp/claude-501/-Users-demo')\n")
            good = os.path.join(d, "ok.py")
            with open(good, "w", encoding="utf-8") as f:
                f.write(f"import shutil\nshutil.rmtree('{SP}/a')\n")
            self.assertIn("script", bash(f"cd /x && .venv/bin/python {bad}; ls /private/tmp | head -3") or "")
            self.assertIsNone(bash(f"python3 -I {good}"))

    def test_find_with_delete_and_git_clean_are_refused_outside_the_scratchpad(self):
        self.assertIsNone(bash(f"find {SP}/a -name '*.tmp' -delete"))
        self.assertIsNotNone(bash("find /tmp/demo -name '*.tmp' -delete"))
        self.assertIsNotNone(bash("find . -type d -exec rm -rf {} +"))
        self.assertIsNone(bash("find /tmp/demo -name '*.tmp' -print"))
        self.assertIsNotNone(bash("git clean -fd"))

    def test_a_file_that_is_being_written_is_checked_for_the_same_calls(self):
        incident = "import shutil\nstray = '/private/tmp/claude-501/-Users-demo'\nshutil.rmtree('/private/tmp/claude-501/-Users-demo')\n"
        self.assertIn("being written", write(incident) or "")
        self.assertIn("being written", write("import shutil\nshutil.rmtree(stray)\n", tool="Edit") or "")
        self.assertIsNone(write(f"import shutil\nshutil.rmtree('{SP}/a')\n"))
        self.assertIsNone(write("print('nothing is removed here')\n"))
        self.assertIn("being written", write("#!/bin/sh\nrm -rf /private/tmp/claude-501/-Users-demo\n") or "")

    def test_the_extra_scratchpad_of_the_environment_is_honoured_for_tests_only(self):
        with mock.patch.dict(os.environ, {"QA_SCRATCHPAD": "/var/fiction/pad"}):
            self.assertIsNone(bash("rm -rf /var/fiction/pad/x", sid=None))
            self.assertIsNotNone(bash("rm -rf /var/fiction/pad", sid=None))
            self.assertIsNotNone(bash("rm -rf /var/fiction/padx/x", sid=None))

    def test_as_a_hook_it_exits_2_with_the_reason_to_block_0_to_allow_and_never_blocks_unreadable_input(self):
        script = os.path.join(ROOT, "scripts", "guard_destructive.py")
        run = lambda payload: subprocess.run([sys.executable, script], input=payload, capture_output=True, text=True, check=False)
        blocked = run(json.dumps({"tool_name": "Bash", "tool_input": {"command": f"rm -rf {PROJECT}"}, "session_id": SID}))
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("BLOCKED by guard_destructive", blocked.stderr)
        self.assertEqual(run(json.dumps({"tool_name": "Bash", "tool_input": {"command": f"rm -rf {SP}/a"}, "session_id": SID})).returncode, 0)
        self.assertEqual(run(json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/x"}})).returncode, 0)
        self.assertEqual(run("not json").returncode, 0)


if __name__ == "__main__":
    unittest.main()
