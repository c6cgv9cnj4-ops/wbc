# -*- coding: utf-8 -*-
"""状態ブランチの初期化と push 競合の検証（ローカルの bare リポジトリを使う。ネットワーク不要）。"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import os as _os  # noqa: E402
import sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import offline_guard  # noqa: E402,F401  (テスト中の実通信を遮断)
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ensure_state_branch.sh"
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@e")


def git(cwd, *args, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise AssertionError(f"git {args} failed: {r.stderr}")
    return r


class StateBranchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.bare = base / "remote.git"
        git(base, "init", "-q", "--bare", str(self.bare))
        self.work = base / "work"
        git(base, "init", "-q", "-b", "main", str(self.work))
        (self.work / "README.md").write_text("main\n", encoding="utf-8")
        git(self.work, "add", "README.md")
        git(self.work, "commit", "-q", "-m", "init main")
        git(self.work, "remote", "add", "origin", str(self.bare))
        git(self.work, "push", "-q", "origin", "main")
        self.main_sha = git(self.work, "rev-parse", "main").stdout.strip()

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self, cwd=None):
        return subprocess.run(["bash", str(SCRIPT)], cwd=cwd or self.work, env=ENV,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")

    def remote_sha(self, branch):
        r = git(self.work, "ls-remote", "--heads", "origin", branch, check=False)
        return r.stdout.split()[0] if r.stdout.strip() else None

    def test_creates_missing_branch_without_touching_main(self):
        self.assertIsNone(self.remote_sha("badminton-results-state"))
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        state_sha = self.remote_sha("badminton-results-state")
        self.assertIsNotNone(state_sha)
        self.assertEqual(self.remote_sha("main"), self.main_sha, "main は変更されない")
        parents = git(self.work, "rev-list", "--parents", "-n", "1", state_sha).stdout.split()
        self.assertEqual(len(parents), 1, "初期コミットは親を持たない（main と無関係）")
        self.assertEqual(git(self.work, "ls-tree", "-r", state_sha).stdout.strip(), "", "中身は空")

    def test_second_run_is_idempotent(self):
        self.run_script()
        first = self.remote_sha("badminton-results-state")
        r = self.run_script()
        self.assertEqual(r.returncode, 0)
        self.assertIn("既に存在", r.stdout)
        self.assertEqual(self.remote_sha("badminton-results-state"), first)

    def test_existing_branch_with_data_is_never_overwritten(self):
        other = Path(self._tmp.name) / "other"
        git(Path(self._tmp.name), "clone", "-q", str(self.bare), str(other))
        git(other, "checkout", "-q", "-b", "badminton-results-state")
        (other / "state").mkdir()
        (other / "state" / "badminton_results_state.json").write_text('{"matches": {"x": {}}}', encoding="utf-8")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "data")
        git(other, "push", "-q", "origin", "badminton-results-state")
        before = self.remote_sha("badminton-results-state")
        r = self.run_script()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.remote_sha("badminton-results-state"), before, "既存データを上書きしない")

    def test_concurrent_creation_second_writer_is_rejected_and_treated_as_success(self):
        # 別ジョブが先に作成した状態を再現: 存在チェックの後・push の前に作られた場合
        clone_dir = Path(self._tmp.name) / "racer"
        git(Path(self._tmp.name), "clone", "-q", str(self.bare), str(clone_dir))
        t = subprocess.run(["git", "mktree"], cwd=clone_dir, env=ENV, input="", capture_output=True,
                           text=True, encoding="utf-8").stdout.strip()
        c = subprocess.run(["git", "commit-tree", t, "-m", "racer"], cwd=clone_dir, env=ENV,
                           capture_output=True, text=True, encoding="utf-8").stdout.strip()
        git(clone_dir, "push", "-q", "origin", f"{c}:refs/heads/badminton-results-state")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.remote_sha("badminton-results-state"), c, "先に作られた方を残す")


class PushRaceTest(unittest.TestCase):
    """2つのworkflow（結果・月次ランキング）が状態ブランチへ同時に push する場合の retry 手順を検証する。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.bare = base / "remote.git"
        git(base, "init", "-q", "--bare", str(self.bare))
        seed = base / "seed"
        git(base, "init", "-q", "-b", "badminton-results-state", str(seed))
        (seed / "state").mkdir()
        (seed / "state" / ".keep").write_text("", encoding="utf-8")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "seed")
        git(seed, "remote", "add", "origin", str(self.bare))
        git(seed, "push", "-q", "origin", "badminton-results-state")
        self.a = base / "a"
        self.b = base / "b"
        git(base, "clone", "-q", "-b", "badminton-results-state", str(self.bare), str(self.a))
        git(base, "clone", "-q", "-b", "badminton-results-state", str(self.bare), str(self.b))

    def tearDown(self):
        self._tmp.cleanup()

    def _save_and_push(self, repo, relpath, text):
        """workflow の保存手順（対象ファイルだけを add → commit → push、失敗時は pull --rebase で再試行）。"""
        target = repo / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        git(repo, "add", "--", relpath)
        git(repo, "commit", "-q", "-m", f"update {relpath}")
        for _ in range(3):
            if git(repo, "push", "-q", "origin", "HEAD:badminton-results-state", check=False).returncode == 0:
                return True
            if git(repo, "pull", "-q", "--rebase", "origin", "badminton-results-state", check=False).returncode != 0:
                return False
        return False

    def test_second_pusher_rebases_and_both_files_survive(self):
        self.assertTrue(self._save_and_push(self.a, "state/badminton_results_state.json", '{"a":1}'))
        self.assertTrue(self._save_and_push(self.b, "state/badspi_ranking/2026-11_MS.json", '{"b":1}'))
        check = Path(self._tmp.name) / "check"
        git(Path(self._tmp.name), "clone", "-q", "-b", "badminton-results-state", str(self.bare), str(check))
        self.assertTrue((check / "state" / "badminton_results_state.json").exists())
        self.assertTrue((check / "state" / "badspi_ranking" / "2026-11_MS.json").exists())

    def test_no_force_push_is_used_in_workflows_or_scripts(self):
        for f in (ROOT / ".github" / "workflows" / "badminton_python_tests.yml", SCRIPT):
            text = f.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"push\s+(-f\b|--force)", text), f.name)
            self.assertIsNone(re.search(r"^\s*git add -A", text, re.M), f.name)


if __name__ == "__main__":
    unittest.main()
