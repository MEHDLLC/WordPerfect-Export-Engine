#!/usr/bin/env python3
r"""
wpd_convert.py — Batch WPD -> DOCX conversion via WordPerfect 2021 COM automation.

Step 1 of the firm's conversion pipeline:
  * SQLite job queue (resumable, auditable)
  * Drives WordPerfect 2021 through its PerfectScript COM interface
  * Watchdog kills a hung WP process and continues the batch
  * Structured per-file logging: hash, timing, status, error text

Requirements (on the Windows processing machine):
    pip install pywin32 psutil
    WordPerfect 2021 installed and licensed. Run this script on the console
    session (not a disconnected RDP session) the first few times so you can
    see whether WP raises any dialogs.

Typical use:
    python wpd_convert.py init --db firmconvert.db
    python wpd_convert.py add  --db firmconvert.db --src "C:\\WPFiles" --recursive
    python wpd_convert.py run  --db firmconvert.db --outdir "C:\\Converted" --timeout 60
    python wpd_convert.py status --db firmconvert.db
    python wpd_convert.py retry-failed --db firmconvert.db

First-run format check:
    python wpd_convert.py list-constants --filter word
  This prints the PerfectScript enum constants WP 2021 exposes so you can
  confirm the exact name of the MS Word .docx export enum on your install.
  The script tries sensible names automatically (see FORMAT_CONST_CANDIDATES);
  if none match, pass the confirmed name via:  run --format-const <Name>
  or a raw integer via:                        run --format-int <N>

Design notes:
  * Output tree mirrors the source tree relative to the --src root recorded
    at 'add' time, so C:\WPFiles\Smith\letter.wpd -> C:\Converted\Smith\letter.docx
  * Existing outputs are deleted before FileSave so WP never shows an
    "overwrite?" prompt.
  * WP is kept visible but minimized: fully hidden instances hang silently
    if WP throws a modal dialog (font substitution, crash recovery).
  * On timeout or COM failure the wpwin process is force-killed, the file is
    marked failed with the reason, WP is relaunched, and the batch continues.
  * Everything lands in the SQLite db — this becomes the audit trail that the
    later verification / exception-report stages build on.
"""

import argparse
import hashlib
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# --- Windows-only imports are deferred so 'init/add/status' also work elsewhere ---
def _import_com():
    global win32com, pythoncom, psutil
    import win32com.client as win32com  # type: ignore
    import pythoncom  # type: ignore
    import psutil  # type: ignore
    return win32com, pythoncom, psutil


WP_PROGID = "WordPerfect.PerfectScript"

# Candidate enum names for the MS Word .docx export format. Confirmed on
# WP 2021: Word2007_FileSave_ExportType = 70 (the .docx container).
FORMAT_CONST_CANDIDATES = [
    "Word2007_FileSave_ExportType",
    "MSWord2007_FileSave_ExportType",
    "MSWord2007",
]

# Process names to kill on hang. WP 2021's main exe is wpwin21.exe, but we
# match loosely to survive minor naming differences and helper processes.
WP_PROCESS_HINTS = ("wpwin", "perfectscript")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    src_path    TEXT NOT NULL UNIQUE,
    src_root    TEXT NOT NULL,
    rel_path    TEXT NOT NULL,
    sha256      TEXT,
    size_bytes  INTEGER,
    status      TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|failed|skipped
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    out_path    TEXT,
    duration_ms INTEGER,
    queued_at   TEXT,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS run_log (
    id       INTEGER PRIMARY KEY,
    at       TEXT NOT NULL,
    level    TEXT NOT NULL,
    message  TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def open_db(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL;")
    con.executescript(SCHEMA)
    return con


def log(con, level, message, echo=True):
    con.execute("INSERT INTO run_log(at, level, message) VALUES (?,?,?)",
                (now(), level, message))
    con.commit()
    if echo:
        print(f"[{level}] {message}")


def sha256_file(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(bufsize)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _load_wp_typelib_constants(verbose=False):
    """WP's PerfectScript COM object doesn't implement GetTypeInfo, so
    EnsureDispatch fails. Instead, find WordPerfect/Corel type libraries in
    the registry, generate makepy wrappers from them, and import those so
    win32com.client.constants gets populated with the enum values."""
    from win32com.client import selecttlb, gencache
    import win32com.client.makepy as makepy
    loaded = []
    for spec in selecttlb.EnumTlbs():
        desc = (spec.desc or "").lower()
        if not any(k in desc for k in ("wordperfect", "perfectscript", "corel")):
            continue
        try:
            makepy.GenerateFromTypeLibSpec(spec)
            try:
                gencache.EnsureModule(spec.clsid, int(spec.lcid or 0),
                                      int(spec.major, 16) if isinstance(spec.major, str) else int(spec.major or 1),
                                      int(spec.minor, 16) if isinstance(spec.minor, str) else int(spec.minor or 0))
            except Exception:
                pass  # GenerateFromTypeLibSpec usually merged constants already
            loaded.append(spec.desc)
            if verbose:
                print(f"  loaded type library: {spec.desc}")
        except Exception as e:
            if verbose:
                print(f"  (skipped type library '{spec.desc}': {e})")
    return loaded


def _all_constants():
    """Return {name: value} for every enum constant win32com has loaded."""
    from win32com.client import constants
    out = {}
    for d in getattr(constants, "__dicts__", []):
        out.update(d)
    return out


# ----------------------------------------------------------------------------
# Subcommands: init / add / status / retry-failed / list-constants / run
# ----------------------------------------------------------------------------

def cmd_init(args):
    open_db(args.db).close()
    print(f"Initialized job database: {args.db}")


def cmd_add(args):
    con = open_db(args.db)
    src_root = str(Path(args.src).resolve())
    if not os.path.isdir(src_root) and not os.path.isfile(src_root):
        sys.exit(f"Source not found: {src_root}")

    if os.path.isfile(src_root):
        files = [Path(src_root)]
        src_root = str(Path(src_root).parent)
    else:
        pattern = "**/*" if args.recursive else "*"
        files = [p for p in Path(src_root).glob(pattern)
                 if p.is_file() and p.suffix.lower() in (".wpd", ".wp", ".wp5", ".wp6", ".frm", ".wpt")]

    added, dup = 0, 0
    for p in sorted(files):
        rel = str(p.resolve().relative_to(src_root))
        try:
            con.execute(
                "INSERT INTO jobs(src_path, src_root, rel_path, sha256, size_bytes, queued_at) "
                "VALUES (?,?,?,?,?,?)",
                (str(p.resolve()), src_root, rel, sha256_file(p), p.stat().st_size, now()),
            )
            added += 1
        except sqlite3.IntegrityError:
            dup += 1
    con.commit()
    log(con, "INFO", f"add: queued {added} file(s), skipped {dup} duplicate(s) from {src_root}")


def cmd_status(args):
    con = open_db(args.db)
    rows = con.execute(
        "SELECT status, COUNT(*), COALESCE(SUM(size_bytes),0) FROM jobs GROUP BY status"
    ).fetchall()
    total = sum(r[1] for r in rows)
    print(f"Jobs: {total}")
    for status, n, size in rows:
        print(f"  {status:8s} {n:8d}   {size/1_048_576:,.1f} MB")
    fails = con.execute(
        "SELECT src_path, error FROM jobs WHERE status='failed' ORDER BY finished_at DESC LIMIT ?",
        (args.show_failures,)
    ).fetchall()
    if fails:
        print(f"\nMost recent failures (up to {args.show_failures}):")
        for path, err in fails:
            print(f"  {path}\n      -> {err}")


def cmd_retry_failed(args):
    con = open_db(args.db)
    n = con.execute(
        "UPDATE jobs SET status='queued', error=NULL WHERE status='failed'"
    ).rowcount
    con.commit()
    log(con, "INFO", f"retry-failed: re-queued {n} file(s)")


def cmd_list_constants(args):
    win32com, pythoncom, _ = _import_com()
    pythoncom.CoInitialize()
    print("Scanning registered WordPerfect/Corel type libraries...")
    loaded = _load_wp_typelib_constants(verbose=True)
    if not loaded:
        print("\nNo WordPerfect type libraries found in the registry.\n"
              "Fallback: record a Save As macro in WP (Tools > Macro > Record,\n"
              "File > Save As > MS Word 2007, stop recording, then Edit the\n"
              "macro) to see the ExportType enum name, and pass --format-int\n"
              "with its numeric value.")
        return
    consts = _all_constants()
    flt = (args.filter or "").lower()
    matched = sorted(n for n in consts if flt in n.lower())
    if not matched:
        print(f"\nNo constants matched filter '{args.filter}'. "
              f"({len(consts)} constants loaded — try --filter FileSave or --filter MSWord)")
    else:
        print(f"\nConstants matching '{args.filter}':")
        for n in matched:
            print(f"  {n} = {consts[n]}")


# ----------------------------------------------------------------------------
# Conversion engine
# ----------------------------------------------------------------------------

# PerfectScript exposes no type info, so pywin32 cannot tell methods from
# properties: merely ACCESSING app.FileOpen invokes it with zero arguments
# (which is why FileOpen failed with error 65441 before any filename was
# sent). _FlagAsMethod marks these names as methods so access returns a
# callable and arguments are delivered. Every command used must be listed.
WP_METHODS = (
    "WPActivate", "AppMinimize", "AppMaximize", "FileOpen", "FileSave",
    "Close", "CloseNoSave", "ExitWordPerfect",
)


class WPSession:
    """Owns one WordPerfect COM instance; recreated after any kill/failure."""

    def __init__(self, format_const=None, format_int=None):
        self.win32com, self.pythoncom, self.psutil = _import_com()
        self.format_const = format_const
        self.format_int = format_int
        self.app = None
        self.fmt = None

    def start(self):
        self.pythoncom.CoInitialize()
        # Force LATE-BOUND dispatch. Plain Dispatch() would return the makepy
        # wrapper generated from WP's type libraries (created by
        # list-constants for enum lookup), and that wrapper's pre-computed
        # dispatch IDs do not match PerfectScript's actual interface — calls
        # silently resolve wrong (WPActivate -> None) or reach WP mangled
        # (FileOpen -> error 65441). dynamic.Dispatch resolves each command
        # name at call time, which is how PerfectScript is meant to be driven.
        from win32com.client import dynamic
        self.app = dynamic.Dispatch(WP_PROGID)
        self.app._FlagAsMethod(*WP_METHODS)
        # Dispatch connects to the PerfectScript server but does not launch
        # the WordPerfect application itself; WPActivate does.
        try:
            self.app.WPActivate()
        except Exception:
            pass
        # Visible-but-minimized: hidden WP hangs silently on modal dialogs.
        try:
            self.app.AppMinimize()
        except Exception:
            pass
        self.fmt = self._resolve_format()

    def _open(self, src):
        """FileOpen with fallbacks: some interfaces require the second
        (format) argument, some reject it. 0 = auto-detect format."""
        try:
            self.app.FileOpen(src)
            return "FileOpen(path)"
        except Exception as e1:
            try:
                self.app.FileOpen(src, 0)
                return "FileOpen(path, 0)"
            except Exception:
                raise e1

    def _resolve_format(self):
        if self.format_int is not None:
            return self.format_int
        _load_wp_typelib_constants()
        consts = _all_constants()
        if self.format_const:
            if self.format_const in consts:
                return consts[self.format_const]
            raise RuntimeError(f"--format-const '{self.format_const}' not found in "
                               "loaded type libraries; check list-constants output")
        # Auto-detect: find the MS Word .docx FileSave ExportType constant.
        # WP 2021 names it Word2007_FileSave_ExportType (= 70).
        export_types = {n: v for n, v in consts.items()
                        if "filesave_exporttype" in n.lower() or
                        n in FORMAT_CONST_CANDIDATES}
        for want in ("msword2007", "word2007", "msworddocx"):
            hits = sorted(n for n in export_types if want in n.lower())
            if hits:
                name = hits[-1]  # highest-sorted = newest variant
                print(f"Auto-selected export format: {name} = {export_types[name]}")
                return export_types[name]
        raise RuntimeError(
            "Could not find an MS Word export enum in WP's type libraries. Run "
            "'wpd_convert.py list-constants --filter FileSave' to inspect what "
            "is available, then pass --format-const <Name> or --format-int <N>."
        )

    def convert(self, src, dst):
        """Open src, save as docx to dst, close without saving changes."""
        if os.path.exists(dst):
            os.remove(dst)  # pre-delete: WP must never see an overwrite prompt
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        self._open(src)
        try:
            # Overwrite param: 1 == Yes! (numeric enums required over OLE).
            self.app.FileSave(dst, self.fmt, 1)
        finally:
            # Close the document window without saving (source stays pristine).
            try:
                self.app.Close()
            except Exception:
                try:
                    self.app.CloseNoSave(0)
                except Exception:
                    pass
        if not os.path.exists(dst) or os.path.getsize(dst) == 0:
            raise RuntimeError("WP reported no error but output is missing/empty")

    def kill(self):
        """Hard-kill every WP-related process; used by the watchdog."""
        for proc in self.psutil.process_iter(["name"]):
            try:
                nm = (proc.info["name"] or "").lower()
                if any(h in nm for h in WP_PROCESS_HINTS):
                    proc.kill()
            except Exception:
                pass
        self.app = None

    def shutdown(self):
        try:
            if self.app is not None:
                self.app.ExitWordPerfect()
        except Exception:
            self.kill()
        self.app = None


def cmd_test_open(args):
    """Diagnostic: step through the COM calls one at a time on a single file,
    reporting each result. Leaves WP open and visible for inspection."""
    src = str(Path(args.file).resolve())
    if not os.path.exists(src):
        sys.exit(f"File not found: {src}")
    win32com, pythoncom, _ = _import_com()
    pythoncom.CoInitialize()

    def step(label, fn):
        try:
            result = fn()
            print(f"  OK    {label}" + (f" -> {result}" if result is not None else ""))
            return True
        except Exception as e:
            print(f"  FAIL  {label}: {e}")
            return False

    print("1. Dispatch PerfectScript COM server (late-bound)")
    from win32com.client import dynamic
    app = dynamic.Dispatch(WP_PROGID)
    app._FlagAsMethod(*WP_METHODS)
    print("  OK    dynamic.Dispatch + _FlagAsMethod")

    print("2. Launch/attach the WordPerfect application")
    step("WPActivate()", lambda: app.WPActivate())
    print("   >>> Check now: is there a WordPerfect window on screen/taskbar?")

    print("3. Open the test document")
    opened = step("FileOpen(path)", lambda: app.FileOpen(src))
    if not opened:
        opened = step("FileOpen(path, 0)", lambda: app.FileOpen(src, 0))

    if opened and args.save_to:
        dst = str(Path(args.save_to).resolve())
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            os.remove(dst)
        print("4. Save as .docx (format 70)")
        step(f"FileSave(dst, 70, 1)", lambda: app.FileSave(dst, 70, 1))
        if os.path.exists(dst):
            print(f"  Output written: {dst} ({os.path.getsize(dst)} bytes)")

    print("\nWP left open for inspection — close it manually when done.")


def cmd_run(args):
    con = open_db(args.db)
    outdir = Path(args.outdir).resolve()
    session = WPSession(format_const=args.format_const, format_int=args.format_int)
    session.start()
    log(con, "INFO", f"run: WP session started; export format enum = {session.fmt}")

    converted = failed = 0
    try:
        while True:
            row = con.execute(
                "SELECT id, src_path, rel_path FROM jobs WHERE status='queued' "
                "ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                break
            job_id, src, rel = row
            dst = str(outdir / Path(rel).with_suffix(".docx"))
            con.execute("UPDATE jobs SET status='running', attempts=attempts+1, "
                        "started_at=? WHERE id=?", (now(), job_id))
            con.commit()

            if not os.path.exists(src):
                con.execute("UPDATE jobs SET status='failed', error=?, finished_at=? "
                            "WHERE id=?", ("source file missing", now(), job_id))
                con.commit()
                failed += 1
                continue

            # --- watchdog: kill WP if the conversion exceeds the timeout ---
            timed_out = threading.Event()
            def watchdog():
                if not done_evt.wait(args.timeout):
                    timed_out.set()
                    session.kill()
            done_evt = threading.Event()
            t = threading.Thread(target=watchdog, daemon=True)
            t.start()

            t0 = time.monotonic()
            err = None
            try:
                session.convert(src, dst)
            except Exception as e:
                err = f"timeout after {args.timeout}s" if timed_out.is_set() else repr(e)
            finally:
                done_evt.set()
                t.join(timeout=5)
            dur_ms = int((time.monotonic() - t0) * 1000)

            if err is None:
                con.execute(
                    "UPDATE jobs SET status='done', out_path=?, duration_ms=?, "
                    "finished_at=?, error=NULL WHERE id=?",
                    (dst, dur_ms, now(), job_id))
                converted += 1
                print(f"  ok   ({dur_ms:>6d} ms)  {rel}")
            else:
                con.execute(
                    "UPDATE jobs SET status='failed', error=?, duration_ms=?, "
                    "finished_at=? WHERE id=?",
                    (err, dur_ms, now(), job_id))
                failed += 1
                log(con, "WARN", f"failed: {rel} -> {err}", echo=True)
                # WP may be dead (watchdog) or wedged (COM error): restart it.
                session.kill()
                time.sleep(2)
                session.start()
            con.commit()

            if args.limit and (converted + failed) >= args.limit:
                log(con, "INFO", f"run: --limit {args.limit} reached, stopping")
                break
    finally:
        session.shutdown()
    log(con, "INFO", f"run complete: {converted} converted, {failed} failed")


# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Batch WPD -> DOCX via WordPerfect 2021 COM")
    ap.add_argument("--db", default="firmconvert.db", help="SQLite job database")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the job database")

    p = sub.add_parser("add", help="enqueue a file or folder of WP files")
    p.add_argument("--src", required=True)
    p.add_argument("--recursive", action="store_true")

    p = sub.add_parser("status", help="show queue status and recent failures")
    p.add_argument("--show-failures", type=int, default=10)

    sub.add_parser("retry-failed", help="re-queue all failed jobs")

    p = sub.add_parser("list-constants", help="print WP type library enum constants")
    p.add_argument("--filter", default="FileSave")

    p = sub.add_parser("test-open", help="diagnostic: try COM calls on one file")
    p.add_argument("--file", required=True)
    p.add_argument("--save-to", help="optionally also attempt a .docx save to this path")

    p = sub.add_parser("run", help="convert queued files")
    p.add_argument("--outdir", required=True)
    p.add_argument("--timeout", type=int, default=60, help="seconds per file")
    p.add_argument("--limit", type=int, default=0, help="stop after N files (0 = all)")
    p.add_argument("--format-const", help="PerfectScript enum name for docx export")
    p.add_argument("--format-int", type=int, help="raw enum value for docx export")

    args = ap.parse_args()
    {
        "init": cmd_init,
        "add": cmd_add,
        "status": cmd_status,
        "retry-failed": cmd_retry_failed,
        "list-constants": cmd_list_constants,
        "test-open": cmd_test_open,
        "run": cmd_run,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
