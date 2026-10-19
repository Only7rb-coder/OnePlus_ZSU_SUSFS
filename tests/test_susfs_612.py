#!/usr/bin/env python3
"""Standalone validation for the locked Android 16/6.12 SUSFS adaptation."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = Path("/home/ubuntu/work/612-validation")
FILTER = ROOT / "scripts/filter_susfs_common_patch.py"
ADAPT = ROOT / "scripts/adapt_susfs_612.py"
TARGETS = ("ace6ultra", "ace6t")


def run(cmd, *, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=check)


class Susfs612Validation(unittest.TestCase):
    def test_real_filter_adapt_dryrun_apply_both_fixtures(self):
        for target in TARGETS:
            with self.subTest(target=target):
                d = VALIDATION / target
                source = d / "source"
                inp = d / "input/50_add_susfs_in_gki-android16-6.12.patch"
                filtered = d / "filtered.patch"
                adapted = d / "adapted.patch"
                filter_log = d / "filter.log"
                adapt_log = d / "adapt.log"
                dry_log = d / "dry-run.log"
                apply_log = d / "apply.log"
                d.mkdir(parents=True, exist_ok=True)

                with filter_log.open("w") as log:
                    p = subprocess.run(["python3", str(FILTER), str(inp), str(filtered)],
                                       text=True, stdout=log, stderr=subprocess.STDOUT)
                self.assertEqual(p.returncode, 0)
                with adapt_log.open("w") as log:
                    p = subprocess.run(["python3", str(ADAPT), "--source-root", str(source),
                                        "--input", str(filtered), "--output", str(adapted)],
                                       text=True, stdout=log, stderr=subprocess.STDOUT)
                self.assertEqual(p.returncode, 0)
                self.assertNotIn("diff --git a/fs/exec.c b/fs/exec.c", adapted.read_text())
                self.assertGreater(adapted.read_text().count("SUSFS_IS_INODE_SUS_MAP"), 3)

                apply_tree = d / "test-apply"
                if apply_tree.exists():
                    shutil.rmtree(apply_tree)
                shutil.copytree(source, apply_tree)
                with dry_log.open("w") as log:
                    p = subprocess.run(["patch", "--batch", "--dry-run", "-p1"],
                                       cwd=apply_tree, input=adapted.read_text(),
                                       text=True, stdout=log, stderr=subprocess.STDOUT)
                self.assertEqual(p.returncode, 0, dry_log.read_text())
                with apply_log.open("w") as log:
                    p = subprocess.run(["patch", "--batch", "-p1"], cwd=apply_tree,
                                       input=adapted.read_text(), text=True,
                                       stdout=log, stderr=subprocess.STDOUT)
                self.assertEqual(p.returncode, 0, apply_log.read_text())
                self.assertEqual(list(apply_tree.rglob("*.rej")), [])
                self.assertNotIn("FAILED", dry_log.read_text())
                self.assertNotIn("FAILED", apply_log.read_text())

    def test_archive_extracted_source_requires_no_git_metadata(self):
        d = VALIDATION / "ace6t"
        with tempfile.TemporaryDirectory(prefix="susfs612-archive-") as td:
            tree = Path(td) / "source"
            shutil.copytree(d / "source", tree, ignore=shutil.ignore_patterns(".git"))
            adapted = self._adapt_text((d / "filtered.patch").read_text(), tree)
            self.assertGreater(adapted.count("SUSFS_IS_INODE_SUS_MAP"), 3)

    def test_negative_unexpected_task_hunk_rejected(self):
        d = VALIDATION / "ace6ultra"
        bad = (d / "filtered.patch").read_text().replace(
            "@@ -1549,6 +1591,13 @@", "@@ -1549,6 +1591,14 @@", 1)
        with self.assertRaises(ValueError):
            self._adapt_text(bad, d / "source")

    def test_negative_source_fingerprint_rejected(self):
        d = VALIDATION / "ace6t"
        with tempfile.TemporaryDirectory(prefix="susfs612-negative-source-") as td:
            tree = Path(td) / "source"
            shutil.copytree(d / "source", tree)
            with (tree / "fs/proc/task_mmu.c").open("a") as f:
                f.write("/* unexpected source layout */\n")
            with self.assertRaises(ValueError):
                self._adapt_text((d / "filtered.patch").read_text(), tree)

    def test_negative_unexpected_exec_addition_rejected(self):
        d = VALIDATION / "ace6ultra"
        text = (d / "filtered.patch").read_text()
        marker = "diff --git a/fs/exec.c b/fs/exec.c"
        start = text.index(marker)
        end = text.find("diff --git ", start + len(marker))
        if end < 0:
            end = len(text)
        section = text[start:end]
        section = section.replace("+#endif\n", "+int unexpected_exec_feature;\n+#endif\n", 1)
        bad = text[:start] + section + text[end:]
        with self.assertRaises(ValueError):
            self._adapt_text(bad, d / "source")

    @staticmethod
    def _adapt_text(text: str, source: Path) -> str:
        import importlib.util
        spec = importlib.util.spec_from_file_location("adapt_susfs_612", ADAPT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module.adapt_patch(text, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
