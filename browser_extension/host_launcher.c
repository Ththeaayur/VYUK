/* VYUK browser host launcher (click-anywhere Batch 1, Plan 9 §1.2, M-10/Q-16).
 *
 * Own code — nothing adopted (Parent-ruled (licence, 2026-11-14)).
 *
 * Chrome's native-messaging manifest has a single "path" field — one
 * executable, no slot for a script path (Chrome appends its own
 * arguments after it, so "python.exe script.py" cannot be expressed).
 * This stub is that one executable. It:
 *
 *   1. reads the interpreter path from HKCU Software\VYUK\BrowserHost
 *      value PythonPath (written by VYUK at install, re-written on
 *      every VYUK start). Absent key -> exits with the named error
 *      "host not registered".
 *   2. spawns "<PythonPath>" -m core.browser.bridge_host with working
 *      directory = the VYUK install root (the directory containing
 *      core/, derived from this exe's own path: it lives in
 *      <install>/browser_extension/), env PYTHONPATH=<install root>
 *      and PYTHONUNBUFFERED=1 (the stdio relay must not block on
 *      buffering — the 4-byte length-prefixed protocol).
 *   3. relays stdio both ways, reading the 4-byte length-prefixed
 *      framing and forwarding each message whole (never parsing the
 *      JSON — the Python host owns the protocol).
 *
 * Chrome's appended argv (origin + --parent-window) is passed through
 * to the Python host, which logs the origin and otherwise ignores it.
 * No signing is required for a locally-built host (signing is a
 * cost-gated follow-up, not a launch feature).
 *
 * Build: see host_launcher.README.md.
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

#define MAX_NATIVE_MESSAGE (1024 * 1024)  /* 1 MB browser->host cap (M-13) */

static volatile LONG g_failed = 0;

static void log_error(const wchar_t *what) {
    fwprintf(stderr, L"vyuk host_launcher: %s (error %lu)\n", what,
             (unsigned long)GetLastError());
}

/* Read exactly `count` bytes, or return FALSE on EOF / error. */
static BOOL read_exact(HANDLE h, BYTE *buf, DWORD count) {
    while (count > 0) {
        DWORD got = 0;
        if (!ReadFile(h, buf, count, &got, NULL) || got == 0) {
            return FALSE;
        }
        buf += got;
        count -= got;
    }
    return TRUE;
}

/* Write exactly `count` bytes, or return FALSE on error. */
static BOOL write_exact(HANDLE h, const BYTE *buf, DWORD count) {
    while (count > 0) {
        DWORD put = 0;
        if (!WriteFile(h, buf, count, &put, NULL) || put == 0) {
            return FALSE;
        }
        buf += put;
        count -= put;
    }
    return TRUE;
}

typedef struct {
    HANDLE from;  /* read end */
    HANDLE to;    /* write end */
} RelayArgs;

/* One direction of the relay: read a 4-byte little-endian length
 * prefix, validate it against the 1 MB cap, forward prefix + payload
 * whole. Returns on EOF or on a framing error. */
static DWORD WINAPI relay_thread(LPVOID param) {
    RelayArgs *args = (RelayArgs *)param;
    BYTE header[4];
    for (;;) {
        if (!read_exact(args->from, header, 4)) {
            break;  /* clean EOF: the other side closed */
        }
        DWORD length = (DWORD)header[0]
            | ((DWORD)header[1] << 8)
            | ((DWORD)header[2] << 16)
            | ((DWORD)header[3] << 24);
        if (length == 0 || length > MAX_NATIVE_MESSAGE) {
            fwprintf(stderr,
                     L"vyuk host_launcher: bad native-message length %lu\n",
                     (unsigned long)length);
            InterlockedExchange(&g_failed, 1);
            break;
        }
        BYTE *payload = (BYTE *)malloc(length);
        if (!payload) {
            InterlockedExchange(&g_failed, 1);
            break;
        }
        BOOL ok = read_exact(args->from, payload, length)
            && write_exact(args->to, header, 4)
            && write_exact(args->to, payload, length);
        free(payload);
        if (!ok) {
            break;
        }
    }
    return 0;
}

static BOOL read_python_path(wchar_t *out, DWORD out_chars) {
    HKEY key = NULL;
    if (RegOpenKeyExW(HKEY_CURRENT_USER, L"Software\\VYUK\\BrowserHost",
                      0, KEY_READ, &key) != ERROR_SUCCESS) {
        return FALSE;
    }
    DWORD type = 0;
    DWORD bytes = out_chars * sizeof(wchar_t);
    LONG rc = RegQueryValueExW(key, L"PythonPath", NULL, &type,
                               (LPBYTE)out, &bytes);
    RegCloseKey(key);
    return rc == ERROR_SUCCESS && type == REG_SZ;
}

static BOOL install_root_from_exe_path(wchar_t *out, DWORD out_chars) {
    wchar_t exe[MAX_PATH];
    if (GetModuleFileNameW(NULL, exe, MAX_PATH) == 0) {
        return FALSE;
    }
    /* exe = <install>\browser_extension\host_launcher.exe — strip the
     * file name, then strip browser_extension\. */
    wchar_t *slash = wcsrchr(exe, L'\\');
    if (!slash) {
        return FALSE;
    }
    *slash = L'\0';
    slash = wcsrchr(exe, L'\\');
    if (!slash) {
        return FALSE;
    }
    *slash = L'\0';
    if (wcslen(exe) + 1 > out_chars) {
        return FALSE;
    }
    wcscpy(out, exe);
    return TRUE;
}

int wmain(int argc, wchar_t **argv) {
    wchar_t python_path[MAX_PATH];
    if (!read_python_path(python_path, MAX_PATH)) {
        /* Named error: the extension route reports "host not registered". */
        fwprintf(stderr,
                 L"vyuk host_launcher: host not registered "
                 L"(HKCU\\Software\\VYUK\\BrowserHost\\PythonPath is absent — "
                 L"run VYUK once so it registers the browser host)\n");
        return 2;
    }

    wchar_t install_root[MAX_PATH];
    if (!install_root_from_exe_path(install_root, MAX_PATH)) {
        log_error(L"could not derive the VYUK install root from the exe path");
        return 2;
    }

    /* Environment for the child (inherited by CreateProcess): the
     * install root on PYTHONPATH so `core.browser.bridge_host` is
     * importable, and unbuffered stdio so the relay never blocks. */
    SetEnvironmentVariableW(L"PYTHONPATH", install_root);
    SetEnvironmentVariableW(L"PYTHONUNBUFFERED", L"1");

    /* Command line: "<python>" -m core.browser.bridge_host <chrome argv...> */
    size_t cmd_chars = wcslen(python_path) + 64;
    for (int i = 1; i < argc; i++) {
        cmd_chars += wcslen(argv[i]) + 3;
    }
    wchar_t *cmdline = (wchar_t *)malloc(cmd_chars * sizeof(wchar_t));
    if (!cmdline) {
        return 2;
    }
    wcscpy(cmdline, L"\"");
    wcscat(cmdline, python_path);
    wcscat(cmdline, L"\" -m core.browser.bridge_host");
    for (int i = 1; i < argc; i++) {
        wcscat(cmdline, L" \"");
        wcscat(cmdline, argv[i]);
        wcscat(cmdline, L"\"");
    }

    HANDLE child_stdin_read = NULL, child_stdin_write = NULL;
    HANDLE child_stdout_read = NULL, child_stdout_write = NULL;
    SECURITY_ATTRIBUTES sa;
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    sa.lpSecurityDescriptor = NULL;
    if (!CreatePipe(&child_stdin_read, &child_stdin_write, &sa, 0)
        || !CreatePipe(&child_stdout_read, &child_stdout_write, &sa, 0)) {
        log_error(L"could not create stdio pipes");
        return 2;
    }
    /* The parent ends must not be inherited by the child. */
    SetHandleInformation(child_stdin_write, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(child_stdout_read, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW si;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = child_stdin_read;
    si.hStdOutput = child_stdout_write;
    si.hStdError = GetStdHandle(STD_ERROR_HANDLE);  /* tracebacks surface */
    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(NULL, cmdline, NULL, NULL, TRUE, 0, NULL,
                        install_root, &si, &pi)) {
        log_error(L"could not spawn the Python browser host");
        return 2;
    }
    free(cmdline);
    CloseHandle(child_stdin_read);
    CloseHandle(child_stdout_write);

    RelayArgs to_child = { GetStdHandle(STD_INPUT_HANDLE), child_stdin_write };
    RelayArgs from_child = { child_stdout_read, GetStdHandle(STD_OUTPUT_HANDLE) };
    HANDLE t1 = CreateThread(NULL, 0, relay_thread, &to_child, 0, NULL);
    HANDLE t2 = CreateThread(NULL, 0, relay_thread, &from_child, 0, NULL);

    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD exit_code = 0;
    GetExitCodeProcess(pi.hProcess, &exit_code);

    /* The child is gone: close the pipes so the relay threads finish. */
    CloseHandle(child_stdin_write);
    CloseHandle(child_stdout_read);
    if (t1) WaitForSingleObject(t1, 5000);
    if (t2) WaitForSingleObject(t2, 5000);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    if (t1) CloseHandle(t1);
    if (t2) CloseHandle(t2);
    if (g_failed) {
        return 2;
    }
    return (int)exit_code;
}
