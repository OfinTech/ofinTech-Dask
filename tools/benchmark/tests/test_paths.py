"""The corpus must not be able to land in a checkout, so that rule is tested before anything else."""

from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest

from ofinbench import paths


class RootResolution(unittest.TestCase):
    def test_missing_root_is_an_error_not_a_default(self) -> None:
        with self.assertRaises(paths.RootError) as caught:
            paths.data_root(None)
        self.assertIn("outside any git repository", str(caught.exception))

    def test_refuses_a_root_inside_a_git_work_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = pathlib.Path(temp) / "repo"
            (repo / "nested" / "deep").mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            for candidate in (repo, repo / "nested", repo / "nested" / "deep"):
                with self.subTest(candidate=candidate):
                    with self.assertRaises(paths.RootError) as caught:
                        paths.data_root(str(candidate))
                    self.assertIn("inside the git repository", str(caught.exception))

    def test_refuses_a_root_whose_git_is_a_file(self) -> None:
        """A linked worktree or a submodule has a .git file, not a directory, and must be refused the same way."""
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "worktree"
            root.mkdir()
            (root / ".git").write_text("gitdir: ../real/.git\n", encoding="utf-8")
            with self.assertRaises(paths.RootError):
                paths.data_root(str(root))

    def test_creates_the_layout_outside_a_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = paths.data_root(str(pathlib.Path(temp) / "corpus"), create=True)
            for expected in ("cases/bank-statement", "cases/monthly-close", "items", "runs", "keys"):
                self.assertTrue((root / expected).is_dir(), expected)

    def test_case_dir_rejects_a_bad_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = paths.data_root(temp)
            for bad in ("", "../escape", ".hidden", "a/b"):
                with self.subTest(bad=bad), self.assertRaises(paths.RootError):
                    paths.case_dir(root, "bank-statement", bad)
            with self.assertRaises(paths.RootError):
                paths.case_dir(root, "not-a-kind", "bs-001")

    def test_iter_cases_only_returns_folders_with_a_truth_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = paths.data_root(str(pathlib.Path(temp) / "corpus"), create=True)
            ready = paths.case_dir(root, "bank-statement", "bs-001")
            ready.mkdir(parents=True)
            (ready / "truth.json").write_text("{}", encoding="utf-8")
            paths.case_dir(root, "bank-statement", "bs-002").mkdir(parents=True)
            self.assertEqual([("bank-statement", ready)], paths.iter_cases(root))


if __name__ == "__main__":
    unittest.main()
