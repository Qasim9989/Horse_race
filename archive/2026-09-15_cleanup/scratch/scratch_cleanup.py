"""Tidy the project folder - MOVE ONLY, nothing is deleted.

Everything moved lands under archive/2026-09-15_cleanup/ with a manifest CSV,
so the whole thing can be reverted by reading the manifest.

  python scratch_cleanup.py           # dry run - prints what would move
  python scratch_cleanup.py --go      # do it
"""
import csv
import os
import shutil
import sys

ROOT = r"E:\Test\racing-form-system"
ARCHIVE = os.path.join(ROOT, "archive", "2026-09-15_cleanup")
TESTS = os.path.join(ROOT, "tests")
GO = "--go" in sys.argv

# --- the two regression tests are worth keeping, under clearer names --------
KEEP_AS_TESTS = {
    "scratch_test_compare.py": "test_book_odds.py",
    "scratch_test_dashboard.py": "test_dashboard.py",
}

# --- generated data that nothing reads any more (your own FRESH_NOTE
#     deprecates the old dashboard's generated JSON) ------------------------
DATA_JSON = ["historical-races.generated.json", "historical-races.sample.json",
             "prodb-flat-handicaps-recent.json", "prodb-flat-handicaps-small.json",
             "prodb-flat-handicaps.json"]
DATA_DIRS = ["excel_raw"]

# --- one-off root scripts / tool history ----------------------------------
ROOT_LEGACY = ["dump_cols.py", "gpt_coder.py", "rdb_cols.txt",
               ".aider.chat.history.md", ".aider.input.history"]

moves = []          # (src, dst)


def add(src, dst):
    if os.path.exists(src):
        moves.append((src, dst))


def build():
    # 1. scratch_*.py at root (except the two tests)
    for name in sorted(os.listdir(ROOT)):
        p = os.path.join(ROOT, name)
        if not os.path.isfile(p) or not name.startswith("scratch_"):
            continue
        if name in KEEP_AS_TESTS:
            dst = os.path.join(TESTS, KEEP_AS_TESTS[name])
        else:
            dst = os.path.join(ARCHIVE, "scratch", name)
        if os.path.abspath(p) != os.path.abspath(dst):
            add(p, dst)

    # 2. probe logs / captured API dumps in reports/
    rep = os.path.join(ROOT, "reports")
    if os.path.isdir(rep):
        for name in sorted(os.listdir(rep)):
            p = os.path.join(rep, name)
            if name.startswith("_cleanup"):
                continue                      # our own log, may be locked
            if os.path.isdir(p) and name.startswith("_api"):
                add(p, os.path.join(ARCHIVE, "probe_logs", name))
            elif os.path.isfile(p) and name.startswith("_"):
                add(p, os.path.join(ARCHIVE, "probe_logs", name))

    # 3. root legacy tooling
    for name in ROOT_LEGACY:
        add(os.path.join(ROOT, name),
            os.path.join(ARCHIVE, "root_legacy", name))

    # 4. generated JSON / excel dumps
    for name in DATA_JSON:
        add(os.path.join(ROOT, "data", name),
            os.path.join(ARCHIVE, "data_generated", name))
    for name in DATA_DIRS:
        add(os.path.join(ROOT, "data", name),
            os.path.join(ARCHIVE, "data_generated", name))

    # 5. launchers/ only holds dead .bat files for archived scanners
    add(os.path.join(ROOT, "launchers"),
        os.path.join(ARCHIVE, "launchers"))


def size_of(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for dp, _dn, fn in os.walk(path):
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def main():
    build()
    total = sum(size_of(s) for s, _ in moves)
    print(f"{'MOVING' if GO else 'WOULD MOVE'} {len(moves)} items "
          f"({total / 1024 / 1024:.1f} MB)\n")
    for s, d in moves:
        kind = "dir " if os.path.isdir(s) else "file"
        print(f"  [{kind}] {os.path.relpath(s, ROOT):52s} -> "
              f"{os.path.relpath(d, ROOT)}")
    if not GO:
        print("\nDry run only. Re-run with --go to perform the moves.")
        return

    os.makedirs(ARCHIVE, exist_ok=True)
    os.makedirs(TESTS, exist_ok=True)
    mpath = os.path.join(ARCHIVE, "MANIFEST.csv")
    previous = []
    if os.path.exists(mpath):                       # append, never overwrite
        with open(mpath, newline="", encoding="utf-8") as f:
            previous = list(csv.reader(f))[1:]
    manifest = []
    for s, d in moves:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if os.path.exists(d):
            print(f"  ! target exists, skipped: {d}")
            continue
        shutil.move(s, d)
        manifest.append([os.path.relpath(s, ROOT), os.path.relpath(d, ROOT),
                         size_of(d)])
    with open(mpath, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["moved_from", "moved_to", "bytes"])
        w.writerows(previous + manifest)

    # root __pycache__ only held bytecode of already-archived modules
    pc = os.path.join(ROOT, "__pycache__")
    if os.path.isdir(pc):
        shutil.rmtree(pc)
        print("  removed regenerable __pycache__/")

    print(f"\nDone. {len(manifest)} items moved; manifest: "
          f"{os.path.relpath(mpath, ROOT)}")


main()
