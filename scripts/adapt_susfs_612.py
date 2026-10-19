#!/usr/bin/env python3
"""Safely adapt the locked SUSFS Android 16/6.12 filesystem patch.

The adaptor is intentionally not a generic patch-repairer.  It accepts only the
known OnePlus 6.12 source layouts recorded below.  Every SUSFS task_mmu addition
is retained; only the three stale context hunks are re-anchored to exact source
lines.  The exec.c diff is accepted only when its additions are the audited
KSU/tiann lifecycle block, and is then removed because ZSU owns that lifecycle.
"""
from __future__ import annotations

import argparse
from collections import Counter
import difflib
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile

PROFILES = {
    # repo/ref: OnePlusOSS/android_kernel_oneplus_mt6993,
    # oneplus/mt6993_b_16.0_ace_6_ultra
    "2e3b6d890bd2c7bd5779bef6df6e16e2c972b87c": {
        "name": "OP-ACE-6-ULTRA",
        "exec": "2c0f1edc537abe68111e36a3e4ffb1b8fc57607ed8f216acfa1f621784817dd5",
        "task": "4f1a6936a32f104f2d685b190a60ac14cb27d1e6709b758110022baef8ac8773",
    },
    # repo/ref: OnePlusOSS/android_kernel_common_oneplus_sm8845,
    # oneplus/sm8845_b_16.0.0_ace_6t
    "c1102892bf2eb5806e135f5169b3dcfd72f96c47": {
        "name": "OP-ACE-6T",
        "exec": "2c8828653f872f7925d962bfc41042e9133d87638f45eb9859702cc43dcd61bd",
        "task": "38f94fbe5113b6af0cdd31ebb9a8896fd9b756a8a64ddaac487936601c1ee053",
    },
}
SOURCE_COMMIT = next(iter(PROFILES))
# This is the SHA-256 of the complete filtered task_mmu section (headers and
# hunks, excluding the following file).  It pins the approved archive input.
APPROVED_TASK_SECTION_SHA256 = "24af15d218614b870190123e29231e720012ad9f6642582b84e96b65ce965841"
APPROVED_TASK_HUNKS_SHA256 = "268d4f5352efd87a76f3e28796e6f30cb6992ebb662182fb9cfd61de6d4812c6"
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
REQUIRED_HUNKS = {"27", "531", "544", "1549", "1618", "1630", "2449", "2458"}

# Exact additions permitted in the audited exec.c lifecycle diff.  No source
# fingerprint is used for exec.c: the addition content itself is the guard.
EXEC_ALLOWED_EXACT = {
    "#ifdef CONFIG_KSU_SUSFS\n",
    "#include <linux/susfs_def.h>\n",
    "#endif\n",
    "#endif // #ifdef CONFIG_KSU_SUSFS\n",
    "extern struct static_key_true ksu_su_compat_enabled;\n",
    "extern struct static_key_true susfs_is_sdcard_android_data_not_decrypted;\n",
    "extern int ksu_handle_execveat(int *fd, struct filename **filename_ptr, void *argv,\n",
    "\t\t\tvoid *envp, int *flags);\n",
    "extern int ksu_handle_execveat_sucompat(int *fd, struct filename **filename_ptr, void *argv,\n",
    "\t\t\t\tvoid *envp, int *flags);\n",
    "extern int ksu_handle_post_execveat_sucompat(int *fd, struct filename **filename_ptr, void *argv,\n",
    "\t\t\t\tvoid *envp, int *flags, int *retval);\n",
    "\tbool is_su_session = false;\n",
    "\tif (likely(susfs_is_current_proc_no_su()))\n",
    "\t\tgoto orig_flow;\n",
    "\tif (static_branch_likely(&ksu_su_compat_enabled)) {\n",
    "\t\tif (static_branch_unlikely(&susfs_is_sdcard_android_data_not_decrypted)) {\n",
    "\t\t\tis_su_session = !ksu_handle_execveat(&fd, &filename, &argv, &envp, &flags);\n",
    "\t\t} else {\n",
    "\t\t\tis_su_session = !ksu_handle_execveat_sucompat(&fd, &filename, &argv, &envp, &flags);\n",
    "\t}\n",
    "orig_flow:\n",
    "\tif (unlikely(is_su_session))\n",
    "\t\t(void)ksu_handle_post_execveat_sucompat(&fd, &filename, &argv, &envp, &flags, &retval);\n",
}


def _section_bounds(lines: list[str], filename: str) -> tuple[int, int] | None:
    marker = f"diff --git a/{filename} b/{filename}"
    starts = [i for i, line in enumerate(lines) if line.rstrip("\n") == marker]
    if not starts:
        return None
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("diff --git ")), len(lines))
    return start, end


def _hunk_blocks(section: list[str]) -> list[tuple[int, int, re.Match[str]]]:
    starts = [i for i, line in enumerate(section) if line.startswith("@@ ")]
    result = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(section)
        match = HUNK_RE.match(section[start].rstrip("\n"))
        if not match:
            raise ValueError("invalid unified-diff hunk header in task_mmu.c")
        result.append((start, end, match))
    return result


def _task_section_sha(section: list[str]) -> str:
    # Section ends before the next diff.  Normalize no bytes: this is a strict
    # fingerprint of the approved filtered input, including newline layout.
    return hashlib.sha256("".join(section).encode()).hexdigest()


def _task_hunks_sha(section: list[str]) -> str:
    blocks = _hunk_blocks(section)
    hunks = "".join("".join(section[start:end]) for start, end, _ in blocks)
    return hashlib.sha256(hunks.encode()).hexdigest()


def _validate_exec_section(section: list[str]) -> None:
    additions = [line[1:] for line in section if line.startswith("+") and not line.startswith("+++")]
    if not additions:
        # filter_susfs_common_patch.py deliberately removes the lifecycle
        # hunks but leaves the file header; that empty section is harmless and
        # is removed by adapt_patch below.
        return
    unexpected = [line for line in additions if line not in EXEC_ALLOWED_EXACT]
    if unexpected:
        raise ValueError("exec.c contains an unexpected non-lifecycle addition; refusing to drop it")
    if all(line in {"#ifdef CONFIG_KSU_SUSFS\n", "#include <linux/susfs_def.h>\n", "#endif\n"} for line in additions):
        return
    if not any("ksu_handle_execveat" in line or "is_su_session" in line for line in additions):
        raise ValueError("exec.c lifecycle hunk lacks the audited tiann symbols")


def _extract_additions(block: list[str]) -> list[str]:
    return [line[1:] for line in block if line.startswith("+") and not line.startswith("+++")]


def _apply_safe_task_hunks(source: Path, section: list[str], skip: set[str]) -> list[str]:
    """Apply unchanged hunks exactly, then return the resulting source lines."""
    blocks = _hunk_blocks(section)
    safe: list[str] = []
    for n, (start, end, match) in enumerate(blocks):
        if match.group(1) in skip:
            continue
        safe.extend(section[start:end])
    # The patch command needs file headers, but no index line is required.
    task_patch = "".join([
        "diff --git a/fs/proc/task_mmu.c b/fs/proc/task_mmu.c\n",
        "--- a/fs/proc/task_mmu.c\n", "+++ b/fs/proc/task_mmu.c\n", *safe,
    ])
    with tempfile.TemporaryDirectory(prefix="susfs612-safe-") as td:
        root = Path(td)
        (root / "fs/proc").mkdir(parents=True)
        work = root / "fs/proc/task_mmu.c"
        work.write_bytes(source.read_bytes())
        proc = subprocess.run(
            ["patch", "--batch", "--fuzz=0", "-p1"], cwd=root,
            input=task_patch.encode(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if proc.returncode:
            raise ValueError("approved task_mmu context hunk failed exact application:\n" + proc.stdout.decode())
        return work.read_text().splitlines(keepends=True)


def _insert_once(lines: list[str], predicate, additions: list[str], label: str) -> None:
    positions = [i for i, line in enumerate(lines) if predicate(i, line)]
    if len(positions) != 1:
        raise ValueError(f"{label} source anchor count is {len(positions)}, expected exactly one")
    i = positions[0]
    lines[i + 1:i + 1] = additions


def _adapt_task_mmu(source: Path, section: list[str]) -> list[str]:
    blocks = _hunk_blocks(section)
    by_old = {m.group(1): section[start:end] for start, end, m in blocks}
    if set(by_old) != REQUIRED_HUNKS:
        raise ValueError(f"unexpected task_mmu hunk set: {sorted(by_old)}")

    # Apply all ordinary SUSFS hunks with exact context.  The three known stale
    # anchors are inserted below, using source content rather than fake context.
    modified = _apply_safe_task_hunks(source, section, {"1549", "1618", "1630"})

    # Hunk 1549: preserve the complete SUSFS map check after mss initialization.
    add_smap = _extract_additions(by_old["1549"])
    show_start = next((i for i, x in enumerate(modified) if x.startswith("static int show_smap(")), None)
    if show_start is None:
        raise ValueError("show_smap function missing")
    show_end = next((i for i in range(show_start + 1, len(modified)) if modified[i].startswith("static ")), len(modified))
    _insert_once(
        modified,
        lambda i, x: show_start < i < show_end and x == "\tstruct mem_size_stats mss = {};\n",
        add_smap,
        "show_smap",
    )

    # Hunk 1618: its old out_unlock context moved in both approved trees.  The
    # semantic location is immediately after the null-VMA break in the loop.
    add_rollup = _extract_additions(by_old["1618"])
    roll_start = next((i for i, x in enumerate(modified) if x.startswith("static int show_smaps_rollup(")), None)
    if roll_start is None:
        raise ValueError("show_smaps_rollup function missing")
    _insert_once(
        modified,
        lambda i, x: roll_start < i < roll_start + 180 and x == "\t\t\t\tbreak;\n" and
        i > 0 and "if (!vma)" in modified[i - 1],
        add_rollup,
        "show_smaps_rollup skip",
    )

    # Hunk 1630: insert the audited label immediately before the VMA iterator's
    # closing line; this is identical semantic control flow in both layouts.
    add_label = _extract_additions(by_old["1630"])
    _insert_once(
        modified,
        lambda i, x: roll_start < i < roll_start + 220 and x == "\t} for_each_vma(vmi, vma);\n",
        add_label,
        "show_smaps_rollup label",
    )

    # Every plus-line in the approved task section must survive.  This catches
    # accidental feature loss while allowing source context to be regenerated.
    expected = Counter(_extract_additions(sum((by_old[k] for k in REQUIRED_HUNKS), [])))
    generated = list(difflib.unified_diff(
        source.read_text().splitlines(keepends=True), modified,
        fromfile="a/fs/proc/task_mmu.c", tofile="b/fs/proc/task_mmu.c",
    ))
    actual = Counter(line[1:] for line in generated if line.startswith("+") and not line.startswith("+++"))
    if expected != actual:
        missing = expected - actual
        extra = actual - expected
        raise ValueError(f"task_mmu SUSFS additions changed; missing={dict(missing)} extra={dict(extra)}")
    return generated


def adapt_patch(text: str, source_root: Path | None = None) -> str:
    """Return an adapted patch only for an approved source/profile pair."""
    if source_root is None:
        raise ValueError("source_root is required for fail-closed 6.12 adaptation")
    source_root = source_root.resolve(strict=True)
    profile = _identify_profile(source_root)
    lines = text.splitlines(keepends=True)
    task_bounds = _section_bounds(lines, "fs/proc/task_mmu.c")
    if task_bounds is None:
        raise ValueError("filtered SUSFS patch is missing fs/proc/task_mmu.c")
    ts, te = task_bounds
    task_section = lines[ts:te]
    if APPROVED_TASK_SECTION_SHA256 != "__SET_BY_TEST_FIXTURE__" and _task_section_sha(task_section) != APPROVED_TASK_SECTION_SHA256:
        raise ValueError("task_mmu patch fingerprint is not the approved filtered archive")
    if _task_hunks_sha(task_section) != APPROVED_TASK_HUNKS_SHA256:
        raise ValueError("task_mmu hunk fingerprint is not the approved 6.12 layout")

    exec_bounds = _section_bounds(lines, "fs/exec.c")
    if exec_bounds is not None:
        es, ee = exec_bounds
        _validate_exec_section(lines[es:ee])
        lines = lines[:es] + lines[ee:]
        if es < ts:
            ts, te = _section_bounds(lines, "fs/proc/task_mmu.c") or (0, 0)
            task_section = lines[ts:te]

    task_section = _adapt_task_mmu(source_root / "fs/proc/task_mmu.c", task_section)
    # Replace the complete task section with a source-derived diff.  The
    # original archive's index is intentionally not copied: it is stale after
    # adaptation, while patch(1) validates the exact generated context.
    replacement = ["diff --git a/fs/proc/task_mmu.c b/fs/proc/task_mmu.c\n", *task_section]
    lines = lines[:ts] + replacement + lines[te:]
    result = "".join(lines)
    if "diff --git a/fs/exec.c b/fs/exec.c" in result:
        raise ValueError("tiann lifecycle exec.c diff was not removed")
    return result


def _identify_profile(source_root: Path) -> dict:
    try:
        commit = subprocess.check_output(["git", "-C", str(source_root), "rev-parse", "HEAD"], text=True, stderr=subprocess.STDOUT).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("source tree must be a git checkout at an approved 6.12 commit") from exc
    profile = PROFILES.get(commit)
    if profile is None:
        raise ValueError(f"source commit/layout is not approved: {commit}")
    for relative, digest in (("fs/exec.c", profile["exec"]), ("fs/proc/task_mmu.c", profile["task"])):
        path = source_root / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            raise ValueError(f"source layout fingerprint mismatch for {relative}: {actual} != {digest}")
    return profile


def verify_source(source_root: Path, expected_commit: str | None = None) -> None:
    profile = _identify_profile(source_root.resolve(strict=True))
    if expected_commit is not None and expected_commit != next(k for k, v in PROFILES.items() if v is profile):
        raise ValueError(f"source commit mismatch: {expected_commit}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-commit", default=None)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        raise SystemExit("input and output must differ; do not overwrite an unvalidated patch")
    verify_source(args.source_root, args.source_commit)
    output = adapt_patch(args.input.read_text(), args.source_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output)


if __name__ == "__main__":
    main()
