"""Run the real replay loop through one busy lock and one completed report."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


with tempfile.TemporaryDirectory() as directory:
    base = Path(directory)
    commands = base / "bin"
    commands.mkdir()
    source = Path(__file__).with_name("run_early_warning_continuous.sh").read_text()
    original_base = "BASE=/home/ubuntu/token-meme-monitor"
    assert source.count(original_base) == 1
    runner = base / "runner.sh"
    runner.write_text(source.replace(original_base, "BASE=" + shlex.quote(str(base))))
    programs = {
        "flock": '''#!/bin/sh
if [ -f "$TMM_TEST_LOCK_ATTEMPT" ]; then exit 0; fi
: > "$TMM_TEST_LOCK_ATTEMPT"
exit 1
''',
        "date": '''#!/bin/sh
case "$*" in
  '-u +%Y%m%d-%H%M%S') echo 20261110-120000 ;;
  '+%s') echo 43200 ;;
  *) exit 2 ;;
esac
''',
        "sleep": '''#!/bin/sh
echo "$1" >> "$TMM_TEST_SLEEPS"
if [ "$1" = 30 ]; then exit 0; fi
kill -TERM "$PPID"
''',
    }
    for name, content in programs.items():
        path = commands / name
        path.write_text(content)
        path.chmod(0o755)
    replay = base / "tmm"
    replay.write_text("#!/bin/sh\necho '{\"completed\":true}'\n")
    replay.chmod(0o755)
    sleeps = base / "sleeps"
    env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"],
               TMM_TEST_LOCK_ATTEMPT=str(base / "attempt"), TMM_TEST_SLEEPS=str(sleeps))
    subprocess.run(["bash", str(runner)], env=env, capture_output=True, text=True, timeout=5)
    reports = list((base / "monitoring-runtime/continuous/early-warning").glob("*.json"))
    assert len(reports) == 1, "a busy lock must retry before skipping the entire three-hour report cycle"
    assert json.loads(reports[0].read_text()) == {"completed": True}
    assert sleeps.read_text().splitlines() == ["30", "10800"], "retry promptly, then resume the existing three-hour schedule"
