"""control_shell concern extracted from core/control.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/control."""

import itertools
import locale
import os
import re
import signal
import subprocess
import sys
import threading
import time
from .control_base import (_DATA_DIR, _is_windows, _routed_backend, _truncate, _input_blocked)


_ALWAYS_REFUSE_PATTERNS = [
    re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE),
    re.compile(r"\bformat\.com\b", re.IGNORECASE),
    re.compile(r"\bformat-volume\b", re.IGNORECASE),        # PowerShell format
    re.compile(r"\bclear-disk\b", re.IGNORECASE),           # PowerShell wipe
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bwipefs\b", re.IGNORECASE),
    re.compile(r"\bdiskpart\b", re.IGNORECASE),
    re.compile(r"\bdd\b[^\n]*\bof=/dev/", re.IGNORECASE),
    re.compile(r"\bcipher\s+/w", re.IGNORECASE),
]


_ENCODED_PAYLOAD_PATTERNS = [
    # PowerShell -EncodedCommand and EVERY unambiguous abbreviation
    # of it, ANCHORED to PowerShell on the same line (v5.45, H21d;
    # narrowed v5.46 programme item 6 / A4): PowerShell resolves
    # parameter names by unique prefix, so -e / -ec / -en / -enc /
    # … after powershell/pwsh all mean the same unreadable payload
    # as the full spelling. The anchor is required: a bare -e is
    # also grep/sed/echo's ordinary flag, so an unanchored -e rule
    # refused everyday commands the confirm tier cannot override.
    re.compile(r"\b(?:powershell|pwsh)(?:\.exe)?\b[^\n]*?(?<![\w])-(?:e|ec|en|enc|enco|encod|encode|encoded|encodedc|encodedco|encodedcom|encodedcomm|encodedcomma|encodedcomman|encodedcommand)\b", re.IGNORECASE),
    re.compile(r"\bfrombase64string\b", re.IGNORECASE),          # [Convert]::FromBase64String(...)
    re.compile(r"\b(?:iex|invoke-expression)\b[^\n]*[A-Za-z0-9+/]{40,}={0,2}", re.IGNORECASE),  # IEX + base64 blob
    re.compile(r"\bcertutil(?:\.exe)?\b[^\n]*?-decode(?:hex)?\b", re.IGNORECASE),  # certutil -decode/-decodehex
]


_CONFIRM_REQUIRED_PATTERNS = [
    re.compile(r"\bdel\b", re.IGNORECASE),                 # del in ANY form (v4.4: a plain `del file` deletes too)
    re.compile(r"\berase\b", re.IGNORECASE),               # erase in ANY form
    re.compile(r"\bremove-item\b", re.IGNORECASE),         # Remove-Item in ANY form (was: only with -Recurse)
    re.compile(r"\brd\s+/s", re.IGNORECASE),               # rd /s/q c:\
    re.compile(r"\brmdir\s+/s", re.IGNORECASE),
    re.compile(r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", re.IGNORECASE),  # rm -rf / rm -fr
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\bstop-computer\b", re.IGNORECASE),       # PowerShell shutdown
    re.compile(r"\brestart-computer\b", re.IGNORECASE),    # PowerShell restart
    re.compile(r"\btaskkill\b", re.IGNORECASE),            # process kill (v4.4)
    re.compile(r"\bnet\s+user\b[^\n]*\s/add\b", re.IGNORECASE),  # create a user account (v4.4)
    re.compile(r"\bsc\s+(delete|stop)\b", re.IGNORECASE),  # delete/stop a service (v4.4)
    re.compile(r"\bremove-partition\b", re.IGNORECASE),    # disk partition removal (v4.4)
    re.compile(r"\breg\s+delete\b", re.IGNORECASE),
    # v5.45 (H21d): system-mutation verbs that used to sail through.
    re.compile(r"\brobocopy\b[^\n]*?/(?:mir|purge)\b", re.IGNORECASE),  # mirror/purge copies delete at the destination
    re.compile(r"\breg\s+add\b", re.IGNORECASE),           # writes the registry (reg delete was already gated)
    re.compile(r"\bschtasks\b[^\n]*?/create\b", re.IGNORECASE),  # persistence: a new scheduled task
    re.compile(r"\bicacls\b", re.IGNORECASE),              # rewrites ACLs
    re.compile(r"\btakeown\b", re.IGNORECASE),             # seizes file ownership
    re.compile(r"\bbcdedit\b", re.IGNORECASE),             # rewrites boot configuration
    re.compile(r"\bset-executionpolicy\b", re.IGNORECASE),  # weakens the script-execution policy
]


_CONFIRM_REQUIRED_RAW_PATTERNS = [
    # Download cradles (H21d): fetch piped/ handed straight into an
    # expression evaluator — no base64 blob required any more.
    re.compile(r"\b(?:invoke-webrequest|invoke-restmethod|iwr|irm|curl|wget)\b[^\n]*\|[^\n]*\b(?:iex|invoke-expression)\b", re.IGNORECASE),
    re.compile(r"\b(?:iex|invoke-expression)\b[^\n]*\b(?:invoke-webrequest|invoke-restmethod|iwr|irm|curl|wget)\b", re.IGNORECASE),
    # Script-file execution (H9): a script the human has not read is
    # confirm-required — never refused outright, because legitimate
    # scripts must stay approvable, and the confirm popup shows the
    # full command (script path included) verbatim.
    re.compile(r"\b(?:powershell|pwsh)(?:\.exe)?\b[^\n]*?\s-(?:file|f)\b", re.IGNORECASE),  # powershell -File x.ps1
    re.compile(r"\bcmd(?:\.exe)?\b[^\n]*?/c\b[^\n]*?\.(?:bat|cmd|ps1)\b", re.IGNORECASE),  # cmd /c x.bat
    re.compile(r"\b(?:wscript|cscript)(?:\.exe)?\b[^\n]*?\.(?:vbs|jse?|wsf|wsh|bat|cmd|ps1)\b", re.IGNORECASE),
    re.compile(r"(?:^|[&|;\n]\s*|&&\s*|\|\|\s*|\bcall\s+|\bstart\s+|&\s*)(?:\"[^\"]*?\.(?:bat|ps1|cmd)\"|'[^']*?\.(?:bat|ps1|cmd)'|[^\s\"']*?\.(?:bat|ps1|cmd)\b)", re.IGNORECASE | re.MULTILINE),  # direct x.bat / .\x.ps1 / & 'x.ps1' / "C:\my scripts\x.ps1"
]


_SHUTDOWN_ABORT = re.compile(r"^\s*shutdown(\.exe)?\s+(-|/)a\s*$", re.IGNORECASE)


_ALIAS_EXPANSIONS = [
    (re.compile(r"\biwr\b", re.IGNORECASE), "Invoke-WebRequest"),
    (re.compile(r"\birm\b", re.IGNORECASE), "Invoke-RestMethod"),
    (re.compile(r"\biex\b", re.IGNORECASE), "Invoke-Expression"),
    (re.compile(r"\bwget\b", re.IGNORECASE), "Invoke-WebRequest"),
    (re.compile(r"\bcurl\b", re.IGNORECASE), "Invoke-WebRequest"),
]


_QUOTE_CONCAT = re.compile(
    r"([\"'])((?:(?!\1).)*)\1\s*(?:\+\s*)?([\"'])((?:(?!\3).)*)\3",
    re.DOTALL)


def _normalize_command(command: str) -> str:
    """The screening form of a command (v5.45, H9): escape tricks
    removed, quoted concatenations merged into the single string
    the shell will see, common aliases expanded to the cmdlet
    names the pattern lists use. Quoting itself is PRESERVED —
    deciding what is code and what is text is _code_view's job."""
    s = str(command)
    # PowerShell backtick escapes: `x is just x to the shell.
    s = re.sub(r"`(.)", r"\1", s, flags=re.DOTALL).replace("`", "")
    # cmd caret escapes: ^x is just x.
    s = re.sub(r"\^(.)", r"\1", s, flags=re.DOTALL)
    for _ in range(8):  # chained concatenations, left to right
        merged = _QUOTE_CONCAT.sub(
            lambda m: m.group(1) + m.group(2) + m.group(4) + m.group(1),
            s)
        if merged == s:
            break
        s = merged
    for pattern, replacement in _ALIAS_EXPANSIONS:
        s = pattern.sub(replacement, s)
    # A quoted token in COMMAND position is executed, quotes and
    # all: "del" file deletes, and "powershell" is powershell.
    # Unwrap quotes that open a segment (start, or right after a
    # separator / call operator) so the verb is visible; quoted
    # ARGUMENTS stay quoted, and _code_view masks their contents —
    # echo "del" still cannot trip the del pattern, because there
    # the segment's first token is echo, not the quoted word.
    def _unwrap(m: "re.Match[str]") -> str:
        # A quoted span containing whitespace is a PATH, not a
        # verb — its quotes are load-bearing for the raw script
        # patterns, so it keeps them.
        if any(c.isspace() for c in m.group(3)):
            return m.group(0)
        return m.group(1) + m.group(3)

    s = re.sub(r"(^|[&|;\n]\s*|&&\s*|\|\|\s*|&\s+)([\"'])([^\n]*?)\2",
               _unwrap, s)
    return s


def _mask_quoted(text: str) -> str:
    """`text` with quoted-span CONTENTS blanked to spaces (the
    quote characters themselves stay). Basic quoting only: single
    and double quotes, with a doubled quote inside a span read as
    an escaped quote that does not end the span."""
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote is None:
            out.append(ch)
            if ch in ("'", '"'):
                quote = ch
            i += 1
        elif ch == quote:
            if i + 1 < len(text) and text[i + 1] == quote:
                out.append("  ")  # escaped quote: still inside
                i += 2
            else:
                quote = None
                out.append(ch)
                i += 1
        else:
            out.append("\n" if ch == "\n" else " ")
            i += 1
    return "".join(out)


_ECHO_HEAD = re.compile(
    r"\s*@?(echo|write-host|write-output)\b", re.IGNORECASE)


def _mask_echo_args(text: str) -> str:
    """Blank the argument text of echo-family commands, segment by
    segment (v5.45, H21e): `echo del` PRINTS the word del — it does
    not delete anything — while a `del` in the next segment still
    screens. Separators (& | ; newline) start a fresh segment."""
    parts = re.split(r"([&|;\n]+)", text)
    for idx in range(0, len(parts), 2):
        m = _ECHO_HEAD.match(parts[idx])
        if m:
            parts[idx] = (parts[idx][:m.end()]
                          + " " * (len(parts[idx]) - m.end()))
    return "".join(parts)


def _code_view(normalized: str) -> str:
    """The view the verb-class confirm patterns match against:
    normalized, with quoted text and echo arguments masked out —
    what remains is the command's executable skeleton."""
    return _mask_echo_args(_mask_quoted(normalized))


def _split_flag_destructive(code: str) -> bool:
    """`rm` with recurse AND force as SEPARATE flag tokens, in any
    order (v5.45, H21d): `rm -Recurse -Force x`, `rm -f -r x`. The
    combined-cluster regex in the pattern list cannot see flags
    split across tokens; this token scan can. Both halves are
    required, exactly like the combined form."""
    for segment in re.split(r"[&|;\n]+", code):
        tokens = segment.split()
        if not tokens:
            continue
        head = (tokens[0].strip("\"'").replace("\\", "/")
                .rsplit("/", 1)[-1].lower())
        if head not in ("rm", "rm.exe"):
            continue
        flags = [t for t in tokens[1:] if t.startswith("-")]
        letters = "".join(
            t.lstrip("-").lower() for t in flags
            if len(t.lstrip("-")) <= 3 and not t.startswith("--"))
        names = {t.lstrip("-").lower() for t in flags}
        recurse = "r" in letters or "recurse" in names
        force = "f" in letters or "force" in names
        if recurse and force:
            return True
    return False


_DOWNLOAD_VERB = re.compile(
    r"\b(?:invoke-webrequest|invoke-restmethod|start-bitstransfer)\b",
    re.IGNORECASE)


_TEMP_HINT = re.compile(
    r"(%temp%|\$env:temp|appdata[\\/]local[\\/]temp|/tmp/)", re.IGNORECASE)


_START_PROCESS = re.compile(r"\bstart-process\b", re.IGNORECASE)


def _download_then_start(normalized: str) -> bool:
    """Download-then-execute without a pipe (v5.45, H21d): fetch a
    file into a temp folder and Start-Process it."""
    return bool(_DOWNLOAD_VERB.search(normalized)
                and _TEMP_HINT.search(normalized)
                and _START_PROCESS.search(normalized))


def _confirm_required(normalized: str, code: str) -> bool:
    """Any confirm-required class hits (the caller has already
    handled refuse classes and the confirm flag itself)."""
    for pattern in _CONFIRM_REQUIRED_PATTERNS:
        if pattern.search(code):
            return True
    for pattern in _CONFIRM_REQUIRED_RAW_PATTERNS:
        if pattern.search(normalized):
            return True
    return (_split_flag_destructive(code)
            or _download_then_start(normalized))


_REFUSE_FORMAT_MSG = (
    "Refused: this command looks like a disk format / wipe operation. "
    "VYUK will not run disk formatting or wiping commands, even with confirmation."
)


_REFUSE_ENCODED_MSG = (
    "Refused: this command hides what it does behind an encoded "
    "payload (PowerShell -EncodedCommand / base64-style encoding), "
    "and encoded commands can't be approved because they can't be "
    "read — nobody can see what would actually run. Ask for the "
    "plain-text version of the command instead."
)


def _trust_decision(command: str, confirm: bool) -> tuple[str, str | None]:
    """The trust layer's verdict for a command (v5.45): ("run", None)
    or ("refuse" / "blocked", the exact user-facing message). Shared
    by the synchronous runner and the background-job launcher, so a
    background job is screened by precisely the same rules, at
    launch, before any job exists."""
    normalized = _normalize_command(command)
    for pattern in _ALWAYS_REFUSE_PATTERNS:
        if pattern.search(normalized):
            return "refuse", _REFUSE_FORMAT_MSG
    for pattern in _ENCODED_PAYLOAD_PATTERNS:
        if pattern.search(normalized):
            return "refuse", _REFUSE_ENCODED_MSG
    if not confirm and not _SHUTDOWN_ABORT.match(normalized.strip()):
        if _confirm_required(normalized, _code_view(normalized)):
            return "blocked", (
                "Blocked by VYUK's trust layer: this command looks destructive "
                f"('{command}'). If you really mean it, ask again with confirm=true."
            )
    return "run", None


def _decode_output(data: bytes | None) -> str:
    """Decode command output with errors="replace" (H21b). Strict
    locale decoding used to turn a SUCCESSFUL command whose output
    carried OEM-codepage bytes into "Could not run command" — the
    command ran; only the reading of its output failed. Replacement
    characters are honest; a phantom failure is not."""
    if not data:
        return ""
    try:
        encoding = locale.getpreferredencoding(False) or "utf-8"
    except Exception:  # noqa: BLE001 — exotic locale tables
        encoding = "utf-8"
    try:
        return data.decode(encoding, errors="replace")
    except (LookupError, ValueError):
        return data.decode("utf-8", errors="replace")


_live_procs: set = set()


_live_procs_lock = threading.Lock()


_kill_listener_registered = False


def _proc_running(proc) -> bool:
    """True when `proc` is still running (poll() says so). A
    proc whose state can't be read reads as not running."""
    try:
        return proc.poll() is None
    except Exception:  # noqa: BLE001 — unreadable = not killable here
        return False


def _register_live_proc(proc) -> None:
    """Register one spawned proc, pruning dead entries first,
    and (on the first registration) wire kill-on-trip."""
    with _live_procs_lock:
        for dead in [p for p in _live_procs if not _proc_running(p)]:
            _live_procs.discard(dead)
        _live_procs.add(proc)
    _ensure_kill_on_trip_registered()


def _unregister_live_proc(proc) -> None:
    with _live_procs_lock:
        _live_procs.discard(proc)


def _ensure_kill_on_trip_registered() -> None:
    """Register kill_all_live_processes as a failsafe trip
    listener, once (guarded lazy import — see the section
    header). A failed attempt leaves the flag clear so the
    next spawn retries."""
    global _kill_listener_registered
    with _live_procs_lock:
        if _kill_listener_registered:
            return
    try:
        from .. import failsafe  # Phase 3 D2b: failsafe stays flat in core/

        failsafe.add_trip_listener(kill_all_live_processes)
    except Exception:  # noqa: BLE001 — no failsafe, no wiring
        return
    with _live_procs_lock:
        _kill_listener_registered = True


def kill_all_live_processes(reason: str = "") -> int:
    """Kill every live process VYUK spawned (v5.46, item 5, A3)
    — the failsafe-trip listener. Running background jobs go
    through the existing job-cancel machinery FIRST, so their
    state reports cancelled/finished through the normal job
    surface instead of merely dying underneath it; every other
    registered proc still running (the synchronous runner's)
    is tree-killed directly. Returns how many processes/jobs
    were killed. Never raises: a trip must not be blockable by
    its own cleanup."""
    killed = 0
    # Jobs first, via cancel_background_job (defined below —
    # resolved at call time). Snapshot the ids under the jobs
    # lock, cancel WITHOUT holding it (cancel waits on the
    # job's reader thread, which needs the live-procs lock to
    # unregister — holding either lock across it could stall).
    try:
        with _jobs_lock:
            job_ids = [job.id for job in _jobs.values()
                       if not job.done]
    except Exception:  # noqa: BLE001 — no job registry readable
        job_ids = []
    for job_id in job_ids:
        try:
            cancel_background_job(job_id)
            killed += 1
        except Exception:  # noqa: BLE001 — best-effort per job
            pass
    with _live_procs_lock:
        snapshot = list(_live_procs)
    for proc in snapshot:
        try:
            if _proc_running(proc):
                _kill_process_tree(proc)
                killed += 1
        except Exception:  # noqa: BLE001 — best-effort per proc
            pass
    return killed


def _kill_process_tree(proc) -> None:
    """Kill a spawned process AND its children (v5.45, H21a).
    Killing only the shell leaves the command's real workers
    running orphaned — the timeout used to free the caller while
    the work kept going. psutil (a declared dependency) walks the
    tree where available; otherwise the platform branch runs:
    taskkill /T /F on Windows, the process group on POSIX (jobs
    spawn in a new session / group for exactly this). Never raises.
    """
    pid = getattr(proc, "pid", None)
    if pid is None:
        return
    try:
        import psutil

        try:
            parent = psutil.Process(pid)
            victims = parent.children(recursive=True) + [parent]
        except Exception:  # noqa: BLE001 — already gone: nothing to kill
            return
        for victim in victims:
            try:
                victim.kill()
            except Exception:  # noqa: BLE001 — raced exit is fine
                pass
        return
    except ImportError:
        pass  # no psutil on this host: platform branch below
    if sys.platform.startswith("win"):
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True, timeout=10)
            return
        except Exception:  # noqa: BLE001 — fall through to proc.kill()
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            return
        except Exception:  # noqa: BLE001 — no group: kill the one pid
            pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001 — best-effort by contract
        pass


def _spawn_shell(command: str, cwd: str | None = None,
                 merge_stderr: bool = False, stdin_pipe: bool = False):
    """Spawn a shell command in its OWN process group / session, so
    _kill_process_tree can take the whole tree down. cwd is passed
    to the child — the process-wide working directory is never
    touched (v5.45, H21c). stdin is DEVNULL by default (a prompting
    command must not hang); stdin_pipe=True leaves it a PIPE for
    callers that talk to the child (R17-4: a game server takes
    console input on stdin, including "stop")."""
    kwargs: dict = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        command, shell=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        stdin=(subprocess.PIPE if stdin_pipe else subprocess.DEVNULL),
        cwd=cwd, **kwargs)
    # v5.46 (programme item 5, A3): register EVERY spawned proc
    # in the live-process registry, so a failsafe trip can kill
    # it (see the registry section above _kill_process_tree).
    _register_live_proc(proc)
    return proc


def run_command(command: str, confirm: bool = False,
                tail: bool = False, max_lines: int = 0,
                cwd: str | None = None, timeout: float = 30,
                cancel_token=None) -> str:
    """Run a shell command (30s timeout by default). Destructive
    patterns are blocked by the trust layer. Output is head-truncated
    by default; tail=True keeps the END of the output instead (logs,
    build tails), and max_lines (when > 0) first trims the output to
    that many lines from the same end. cwd (v5.45) runs the child in
    that folder via the subprocess itself — no os.chdir, so other
    threads' relative paths are never raced.

    B20: ``cancel_token`` ends the command promptly on Stop —
    the child is polled, not blocked on in one
    communicate(timeout). A cancelled run returns the cancelled
    DICT, never a string (a string would wrap as success).
    Killing the CHILD TREE on cancel is the same
    _kill_process_tree the timeout path already uses — the
    step's own thread is never touched; the handler returns
    normally. Without a token the poll loop still applies (the
    deadline is the same timeout), so timeout behaviour is
    unchanged — only the wait is interruptible.
    """
    if not command or not str(command).strip():
        return "No command given."
    command = str(command)

    verdict, message = _trust_decision(command, confirm)
    if verdict != "run":
        return str(message)
    try:
        proc = _spawn_shell(command, cwd=cwd)
    except Exception as e:  # noqa: BLE001
        return f"Could not run command: {e}"
    try:
        _deadline = time.monotonic() + max(0.0, float(timeout or 0))
        while True:
            try:
                out_b, err_b = proc.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if cancel_token is not None and cancel_token.cancelled:
                    _kill_process_tree(proc)
                    try:
                        proc.communicate(timeout=5)
                    except Exception:  # noqa: BLE001 — the tree is down; move on
                        pass
                    # v5.46 (item 5, A3): communicate() has finished — the
                    # proc leaves the live-process registry on every path.
                    _unregister_live_proc(proc)
                    from ..funnel.tool_result import cancelled_result
                    return cancelled_result(
                        "Command cancelled at the user's request while "
                        f"it was still running: {command}")
                if time.monotonic() >= _deadline:
                    _kill_process_tree(proc)
                    try:
                        proc.communicate(timeout=5)
                    except Exception:  # noqa: BLE001 — the tree is down; move on
                        pass
                    # v5.46 (item 5, A3): communicate() has finished — the
                    # proc leaves the live-process registry on every path.
                    _unregister_live_proc(proc)
                    return f"Command timed out after {timeout:g} seconds: {command}"
    except Exception as e:  # noqa: BLE001
        _unregister_live_proc(proc)
        return f"Could not run command: {e}"
    _unregister_live_proc(proc)
    # GAPS-24 (K-V3): a child killed from outside (the failsafe
    # trip's proc-kill) lands here via communicate() returning,
    # not via the TimeoutExpired branch — the token check there
    # never runs. A cancelled token reports cancelled, never
    # "Exit code -9" as a success: the user stopped this command.
    if cancel_token is not None and cancel_token.cancelled:
        from ..funnel.tool_result import cancelled_result
        return cancelled_result(
            "Command cancelled at the user's request while "
            f"it was still running: {command}")
    output = (_decode_output(out_b) + _decode_output(err_b)).strip()
    if not output:
        output = "(no output)"
    try:
        keep_lines = max(0, int(max_lines or 0))
    except (TypeError, ValueError):
        keep_lines = 0
    total_lines = output.count("\n") + 1
    if keep_lines and total_lines > keep_lines:
        all_lines = output.split("\n")
        kept = all_lines[-keep_lines:] if tail else all_lines[:keep_lines]
        side = "last" if tail else "first"
        output = ("\n".join(kept)
                  + f"\n... [showing the {side} {keep_lines} of "
                  f"{total_lines} lines]")
    text = f"Exit code {proc.returncode}:\n{output}"
    if len(text) <= 1500:
        return text
    if tail:
        return "... [truncated — showing the end]\n" + text[-1450:]
    return _truncate(text, 1500)


_JOB_LIMIT = 8


_JOB_BUFFER_LIMIT = 100_000  # chars of output retained per job


_jobs_lock = threading.Lock()


_jobs: dict[str, "_CommandJob"] = {}


_jobs_order: list[str] = []


_job_seq = itertools.count(1)


class _CommandJob:
    """One background command: process, bounded output buffer and
    the poll cursor. `base` counts characters dropped from the
    buffer's head, so logical offsets stay meaningful after a trim.
    """

    def __init__(self, job_id: str, command: str, proc):
        self.id = job_id
        self.command = command
        self.proc = proc
        self.lock = threading.Lock()
        self.finished = threading.Event()
        self.text = ""
        self.base = 0
        self.read_pos = 0
        self.exit_code: int | None = None

    @property
    def done(self) -> bool:
        return self.finished.is_set()


def _job_reader(job: _CommandJob) -> None:
    """Drain a job's (merged) output into its bounded buffer, then
    record the exit code. Runs on a daemon thread per job."""
    stream = job.proc.stdout
    read = getattr(stream, "read1", None) or stream.read
    try:
        while True:
            chunk = read(4096)
            if not chunk:
                break
            text = _decode_output(chunk)
            with job.lock:
                job.text += text
                overflow = len(job.text) - _JOB_BUFFER_LIMIT
                if overflow > 0:
                    job.text = job.text[overflow:]
                    job.base += overflow
    except Exception:  # noqa: BLE001 — a broken pipe reads as EOF
        pass
    finally:
        try:
            job.exit_code = job.proc.wait()
        except Exception:  # noqa: BLE001
            job.exit_code = job.proc.returncode
        # v5.46 (item 5, A3): the reader is done with this proc —
        # it leaves the live-process registry before the job is
        # marked finished, so a finished job is never still
        # registered as live.
        _unregister_live_proc(job.proc)
        job.finished.set()


def _get_job(job_id: str) -> _CommandJob | None:
    with _jobs_lock:
        return _jobs.get(str(job_id or "").strip())


def start_background_command(command: str, confirm: bool = False,
                             cwd: str | None = None) -> str:
    """Launch a command as a managed background job. The trust
    decision happens HERE, before the job exists — its refusal /
    blocked messages are returned verbatim and nothing is spawned.
    """
    if not command or not str(command).strip():
        return "No command given."
    command = str(command)
    verdict, message = _trust_decision(command, confirm)
    if verdict != "run":
        return str(message)
    job_id = f"job-{next(_job_seq)}"
    with _jobs_lock:
        if len(_jobs) >= _JOB_LIMIT:
            evictable = [j for j in _jobs_order if _jobs[j].done]
            if not evictable:
                return (
                    f"Too many background jobs: {_JOB_LIMIT} are "
                    "already running and none has finished to make "
                    "room. Wait for one to finish or cancel one "
                    "before starting another."
                )
            oldest = evictable[0]
            del _jobs[oldest]
            _jobs_order.remove(oldest)
    try:
        proc = _spawn_shell(command, cwd=cwd, merge_stderr=True)
    except Exception as e:  # noqa: BLE001
        return f"Could not run command: {e}"
    job = _CommandJob(job_id, command, proc)
    with _jobs_lock:
        _jobs[job_id] = job
        _jobs_order.append(job_id)
    threading.Thread(target=_job_reader, args=(job,),
                     daemon=True).start()
    return (
        f"Started background job {job_id}: {command}\n"
        f"Poll for new output with run_command action='poll' "
        f"job_id='{job_id}'; finish with action='wait'; stop it "
        f"with action='cancel'."
    )


def _job_state_line(job: _CommandJob) -> str:
    if job.done:
        return f"exited with code {job.exit_code}"
    return "running"


def poll_background_job(job_id: str) -> str:
    """Output since the last poll + the job's state (H3). An
    unknown id (never existed, or evicted) says so plainly."""
    job = _get_job(job_id)
    if job is None:
        return (f"No background job '{job_id}' — it never existed "
                "or has been evicted. action='list' shows the "
                "jobs VYUK is holding.")
    with job.lock:
        start = max(job.read_pos, job.base)
        fresh = job.text[start - job.base:]
        job.read_pos = job.base + len(job.text)
    header = (f"Background job {job.id} ({job.command}): "
              f"{_job_state_line(job)}.")
    if fresh.strip():
        return f"{header}\n{fresh.rstrip()}"
    return f"{header}\n(no new output)"


def wait_background_job(job_id: str, timeout: float = 30) -> str:
    """Block up to `timeout` seconds for a job, then report its
    final tail + exit code (or that it is still running, with the
    output so far). Marks all output as read, like a final poll."""
    job = _get_job(job_id)
    if job is None:
        return (f"No background job '{job_id}' — it never existed "
                "or has been evicted. action='list' shows the "
                "jobs VYUK is holding.")
    try:
        wait_s = max(0.0, float(timeout))
    except (TypeError, ValueError):
        wait_s = 30.0
    finished = job.finished.wait(wait_s)
    with job.lock:
        tail_text = job.text[-4000:]
        job.read_pos = job.base + len(job.text)
    if not finished:
        return (
            f"Background job {job.id} ({job.command}) is still "
            f"running after waiting {wait_s:g} seconds. Output so "
            f"far:\n{tail_text.rstrip() or '(no output yet)'}"
        )
    return (
        f"Background job {job.id} ({job.command}) finished with "
        f"exit code {job.exit_code}.\n{tail_text.rstrip() or '(no output)'}"
    )


def cancel_background_job(job_id: str) -> str:
    """Cancel a running job, killing its whole process tree; a
    finished job reports its exit code instead (nothing to kill)."""
    job = _get_job(job_id)
    if job is None:
        return (f"No background job '{job_id}' — it never existed "
                "or has been evicted. action='list' shows the "
                "jobs VYUK is holding.")
    if job.done:
        return (f"Background job {job.id} already finished with "
                f"exit code {job.exit_code} — nothing to cancel.")
    _kill_process_tree(job.proc)
    job.finished.wait(5)
    return (f"Cancelled background job {job.id} ({job.command}) — "
            "its process tree was killed.")


def list_background_jobs() -> str:
    """Every retained job with its state, oldest first."""
    with _jobs_lock:
        jobs = [_jobs[j] for j in _jobs_order if j in _jobs]
    if not jobs:
        return "No background jobs are being held."
    lines = [f"{job.id}: {_job_state_line(job)} — {job.command}"
             for job in jobs]
    return "Background jobs:\n" + "\n".join(lines)
