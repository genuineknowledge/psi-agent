#include <windows.h>
#include <objbase.h>
#include <shlobj.h>
#include <wininet.h>
#include <urlmon.h>
#include <shellapi.h>
#include <string.h>
#include <stdio.h>

#define MAX_ENV 32767
#define PATH_BUF 32768
#define CMD_BUF 4096
/* Same folder name as Gateway DEFAULT_USER_WORKSPACE_NAME (haitun + 交付). */
#define DEFAULT_WS_NAME L"haitun\u4ea4\u4ed8"

static WCHAR g_dir[MAX_PATH];
static WCHAR g_env[MAX_ENV * 2];  /* double size: wide-char bytes */
static int   g_env_len;

/* Updater config is generated at package build time into the installed workspace. */
#define HAITUN_UPDATE_CONF L"haitun-update.conf"
#define MAX_UPDATE_URL 4096
#define UPDATE_VERSION_BUF 128

#ifndef HAITUN_UPDATE_INTERVAL_HOURS
#define HAITUN_UPDATE_INTERVAL_HOURS 24
#endif
#define HAITUN_UPDATE_INTERVAL_MS \
    ((DWORD)((HAITUN_UPDATE_INTERVAL_HOURS) * 60ULL * 60ULL * 1000ULL))

static WCHAR g_local_version[64];
static WCHAR g_version_url[MAX_UPDATE_URL];
static WCHAR g_installer_url[MAX_UPDATE_URL];
static DWORD g_update_interval_ms = HAITUN_UPDATE_INTERVAL_MS;

/* ---- helpers ---- */

static void set_env(const WCHAR *name, const WCHAR *val)
{
    int n = lstrlenW(name);
    int v = lstrlenW(val);
    if (g_env_len + n + 1 + v + 1 >= sizeof(g_env) / sizeof(WCHAR))
        return;
    lstrcpyW(g_env + g_env_len, name);
    g_env_len += n;
    g_env[g_env_len++] = L'=';
    lstrcpyW(g_env + g_env_len, val);
    g_env_len += v;
    g_env[g_env_len++] = L'\0';
}

static void append_env(const WCHAR *s)
{
    int len = lstrlenW(s);
    if (g_env_len + len + 1 >= sizeof(g_env) / sizeof(WCHAR))
        return;
    lstrcpyW(g_env + g_env_len, s);
    g_env_len += len;
    g_env[g_env_len++] = L'\0';
}

static WCHAR *find_env_var(const WCHAR *name)
{
    int nlen = lstrlenW(name);
    WCHAR *p = g_env;
    while (*p) {
        int i;
        for (i = 0; i < nlen && p[i] && p[i] == name[i]; i++)
            ;
        if (i == nlen && p[i] == L'=')
            return p;
        p += lstrlenW(p) + 1;
    }
    return NULL;
}

static void replace_env(const WCHAR *name, const WCHAR *val)
{
    WCHAR *pos = find_env_var(name);
    if (pos) {
        int old_size = lstrlenW(pos) + 1;   /* name=value + NUL */
        int new_size = lstrlenW(name) + 1 + lstrlenW(val) + 1; /* name=value + NUL */
        int diff = new_size - old_size;
        if (g_env_len + diff >= (int)(sizeof(g_env) / sizeof(WCHAR)))
            return;
        WCHAR *end = pos + old_size;
        if (diff) {
            MoveMemory(end + diff, end, (g_env + g_env_len - end) * sizeof(WCHAR));
        }
        lstrcpyW(pos, name);
        pos[lstrlenW(name)] = L'=';
        lstrcpyW(pos + lstrlenW(name) + 1, val);
        g_env_len += diff;
    } else {
        set_env(name, val);
    }
}

static const WCHAR *get_env_value(const WCHAR *name)
{
    WCHAR *pos = find_env_var(name);
    return pos ? pos + lstrlenW(name) + 1 : NULL;
}

static void load_env_file(const WCHAR *path)
{
    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ,
                           NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;

    DWORD size = GetFileSize(h, NULL);
    if (size == INVALID_FILE_SIZE || size > 65536) { CloseHandle(h); return; }

    char *buf = HeapAlloc(GetProcessHeap(), 0, size + 1);
    if (!buf) { CloseHandle(h); return; }
    DWORD read = 0;
    ReadFile(h, buf, size, &read, NULL);
    buf[read] = '\0';
    CloseHandle(h);

    char *line = buf;
    while (*line) {
        char *nl = line;
        while (*nl && *nl != '\r' && *nl != '\n') nl++;
        char saved = *nl;
        *nl = '\0';

        char *p = line;
        while (*p == ' ' || *p == '\t') p++;
        if (*p && *p != '#') {
            char *eq = p;
            while (*eq && *eq != '=') eq++;
            if (*eq == '=' && eq > p) {
                *eq = '\0';
                char *key = p;
                /* strip trailing spaces from key (VBS Trim) */
                int klen = (int)strlen(key);
                while (klen > 0 && (key[klen - 1] == ' ' || key[klen - 1] == '\t'))
                    key[--klen] = '\0';
                char *val = eq + 1;
                while (*val == ' ' || *val == '\t') val++;
                int vlen = (int)strlen(val);
                while (vlen > 0 && (val[vlen - 1] == ' ' || val[vlen - 1] == '\t' || val[vlen - 1] == '\r')) {
                    val[--vlen] = '\0';
                }
                if (vlen >= 2 && (val[0] == '"' || val[0] == '\'') && val[vlen - 1] == val[0]) {
                    val[vlen - 1] = '\0';
                    val++;
                }
                if (*key) {
                    WCHAR wkey[256], wval[8192];
                    if (MultiByteToWideChar(CP_UTF8, 0, key, -1, wkey, 256) &&
                        MultiByteToWideChar(CP_UTF8, 0, val, -1, wval, 8192))
                        replace_env(wkey, wval);
                }
            }
        }

        *nl = saved;
        line = saved ? nl + (saved == '\r' ? (*(nl + 1) == '\n' ? 2 : 1) : 1) : nl;
    }
    HeapFree(GetProcessHeap(), 0, buf);
}

/* ---- update check ---- */

static void trim_whitespace(WCHAR *s)
{
    int n = lstrlenW(s);
    int a = 0;
    int b = n;
    while (a < b && (s[a] == L' ' || s[a] == L'\t' || s[a] == L'\r' || s[a] == L'\n'))
        a++;
    while (b > a && (s[b - 1] == L' ' || s[b - 1] == L'\t' || s[b - 1] == L'\r' || s[b - 1] == L'\n'))
        b--;
    if (a != 0 || b != n) {
        memmove(s, s + a, (size_t)(b - a) * sizeof(WCHAR));
        s[b - a] = L'\0';
    }
}

static int fetch_remote_text(const WCHAR *url, WCHAR *out, int out_cch)
{
    HINTERNET hNet;
    HINTERNET hUrl;
    char raw[4097];
    DWORD total = 0;
    DWORD read = 0;
    DWORD status = 0;
    DWORD status_len = sizeof(status);

    hNet = InternetOpenW(L"HaiTun Agent Updater", INTERNET_OPEN_TYPE_PRECONFIG,
                         NULL, NULL, 0);
    if (!hNet)
        return 0;
    hUrl = InternetOpenUrlW(hNet, url, NULL, 0,
                            INTERNET_FLAG_RELOAD | INTERNET_FLAG_NO_CACHE_WRITE |
                                INTERNET_FLAG_KEEP_CONNECTION,
                            0);
    if (!hUrl) {
        InternetCloseHandle(hNet);
        return 0;
    }

    if (HttpQueryInfoW(hUrl, HTTP_QUERY_STATUS_CODE | HTTP_QUERY_FLAG_NUMBER,
                       &status, &status_len, NULL) && status >= 400) {
        InternetCloseHandle(hUrl);
        InternetCloseHandle(hNet);
        return 0;
    }

    while (total < sizeof(raw) - 1 &&
           InternetReadFile(hUrl, raw + total, (DWORD)(sizeof(raw) - 1 - total), &read) &&
           read > 0) {
        total += read;
    }
    InternetCloseHandle(hUrl);
    InternetCloseHandle(hNet);

    if (!total)
        return 0;
    raw[total] = '\0';
    {
        int n = MultiByteToWideChar(CP_UTF8, 0, raw, (int)total, out, out_cch - 1);
        if (n <= 0)
            out[0] = L'\0';
        else
            out[n] = L'\0';
    }
    trim_whitespace(out);
    return 1;
}

static void join_url(WCHAR *out, int out_cch, const WCHAR *base, const WCHAR *suffix)
{
    int n = lstrlenW(base);
    int m = lstrlenW(suffix);
    if (n <= 0 || n + 1 + m + 1 > out_cch)
        return;
    lstrcpyW(out, base);
    if (out[n - 1] != L'/') {
        out[n++] = L'/';
        out[n] = L'\0';
    }
    lstrcatW(out, suffix);
}

static int starts_with_https(const WCHAR *s)
{
    static const WCHAR prefix[] = L"https://";
    int i;
    for (i = 0; i < 8; i++) {
        if (s[i] != prefix[i])
            return 0;
    }
    return s[8] != L'\0';
}

static void configure_updater(void)
{
    const WCHAR *v = get_env_value(L"HAITUN_VERSION");
    const WCHAR *base = get_env_value(L"HAITUN_UPDATE_BASE_URL");
    const WCHAR *interval = get_env_value(L"HAITUN_UPDATE_INTERVAL_HOURS");
    const WCHAR *installer = get_env_value(L"HAITUN_UPDATE_INSTALLER_NAME");

    g_local_version[0] = L'\0';
    g_version_url[0] = L'\0';
    g_installer_url[0] = L'\0';

    if (v && v[0])
        lstrcpynW(g_local_version, v, 64);
    if (interval && interval[0]) {
        int hours = 0;
        const WCHAR *p;
        for (p = interval; *p >= L'0' && *p <= L'9'; p++)
            hours = hours * 10 + (*p - L'0');
        if (hours > 0 && hours <= 24 * 30)
            g_update_interval_ms = (DWORD)((DWORD)hours * 60u * 60u * 1000u);
    }
    if (base && base[0] && starts_with_https(base)) {
        const WCHAR *installer_name =
            (installer && installer[0]) ? installer : L"HaiTun_Agent_Setup.exe";
        join_url(g_version_url, MAX_UPDATE_URL, base, L"version.txt");
        join_url(g_installer_url, MAX_UPDATE_URL, base, installer_name);
    }
}

static void launch_installer_file(const WCHAR *path)
{
    WCHAR cmd[1024];
    STARTUPINFOW si = {sizeof(si)};
    PROCESS_INFORMATION pi = {0};

    if (!path || !path[0])
        return;
    wsprintfW(cmd, L"\"%s\"", path);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_SHOWNORMAL;
    if (CreateProcessW(NULL, cmd, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
        if (pi.hThread) CloseHandle(pi.hThread);
        if (pi.hProcess) CloseHandle(pi.hProcess);
        return;
    }
    ShellExecuteW(NULL, L"open", g_installer_url, NULL, NULL, SW_SHOWNORMAL);
}

/* ---- download progress window ---- */

static HWND g_progress_hwnd = NULL;
static WCHAR g_progress_version[64];

static LRESULT CALLBACK progress_wnd_proc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam)
{
    switch (msg) {
    case WM_CREATE:
    {
        HFONT font = (HFONT)GetStockObject(DEFAULT_GUI_FONT);
        WCHAR text[256];
        wsprintfW(text, L"正在下载新版本 %s 安装包，请稍候……", g_progress_version);
        HWND label = CreateWindowExW(0, L"STATIC", text,
                                     WS_CHILD | WS_VISIBLE | SS_CENTER,
                                     18, 22, 324, 36,
                                     hwnd, NULL, GetModuleHandleW(NULL), NULL);
        if (label)
            SendMessageW(label, WM_SETFONT, (WPARAM)font, TRUE);
        return 0;
    }
    case WM_CLOSE:
        DestroyWindow(hwnd);
        g_progress_hwnd = NULL;
        return 0;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wParam, lParam);
}

static DWORD WINAPI progress_window_thread(LPVOID unused)
{
    (void)unused;
    HINSTANCE hInst = GetModuleHandleW(NULL);
    HWND hwnd;
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = progress_wnd_proc;
    wc.hInstance = hInst;
    wc.hCursor = LoadCursorW(NULL, IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    wc.lpszClassName = L"HaiTunDownloadProgress";
    if (!RegisterClassW(&wc) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS)
        return 0;

    hwnd = CreateWindowExW(WS_EX_TOPMOST | WS_EX_DLGMODALFRAME,
                           L"HaiTunDownloadProgress", L"正在更新",
                           WS_POPUP | WS_CAPTION,
                           CW_USEDEFAULT, CW_USEDEFAULT, 360, 100,
                           NULL, NULL, hInst, NULL);
    if (!hwnd)
        return 0;
    g_progress_hwnd = hwnd;

    {
        RECT r;
        GetWindowRect(hwnd, &r);
        SetWindowPos(hwnd, NULL,
                     (GetSystemMetrics(SM_CXSCREEN) - (r.right - r.left)) / 2,
                     (GetSystemMetrics(SM_CYSCREEN) - (r.bottom - r.top)) / 2,
                     0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
    }
    ShowWindow(hwnd, SW_SHOW);
    UpdateWindow(hwnd);

    {
        MSG msg;
        while (GetMessageW(&msg, NULL, 0, 0) > 0) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    return 0;
}

static HWND show_download_progress(const WCHAR *version)
{
    int i;
    HANDLE hThread;
    g_progress_hwnd = NULL;
    g_progress_version[0] = L'\0';
    if (version && version[0])
        lstrcpynW(g_progress_version, version, 64);
    hThread = CreateThread(NULL, 0, progress_window_thread, NULL, 0, NULL);
    if (hThread)
        CloseHandle(hThread);
    for (i = 0; i < 50 && !g_progress_hwnd; i++)
        Sleep(20);
    return g_progress_hwnd;
}

static void hide_download_progress(HWND hwnd)
{
    if (hwnd && IsWindow(hwnd))
        PostMessageW(hwnd, WM_CLOSE, 0, 0);
}

static DWORD WINAPI update_check_thread(LPVOID unused)
{
    (void)unused;
    if (!g_local_version[0] || !g_version_url[0] || !g_installer_url[0])
        return 0;

    for (;;) {
        WCHAR latest[UPDATE_VERSION_BUF];
        latest[0] = L'\0';
        if (fetch_remote_text(g_version_url, latest, UPDATE_VERSION_BUF) &&
            latest[0] && lstrcmpW(latest, g_local_version) != 0) {
            WCHAR msg[512];
            wsprintfW(msg,
                      L"HaiTun Agent 发现新版本 %s，当前版本为 %s。\n\n"
                      L"是否现在下载并更新？",
                      latest, g_local_version);
            if (MessageBoxW(NULL, msg, L"发现新版本",
                            MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST) == IDYES) {
                WCHAR temp_dir[MAX_PATH];
                WCHAR temp_path[MAX_PATH];
                HRESULT hr;
                HWND progress;
                if (GetTempPathW(MAX_PATH, temp_dir) && temp_dir[0]) {
                    wsprintfW(temp_path, L"%sHaiTun-Agent-Setup-%s.exe", temp_dir, latest);
                    progress = show_download_progress(latest);
                    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
                    hr = URLDownloadToFileW(NULL, g_installer_url, temp_path, 0, NULL);
                    CoUninitialize();
                    hide_download_progress(progress);
                    if (hr == S_OK) {
                        launch_installer_file(temp_path);
                    } else {
                        MessageBoxW(NULL, L"自动下载失败，将打开浏览器下载页面。", L"更新失败",
                                    MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST);
                        ShellExecuteW(NULL, L"open", g_installer_url, NULL, NULL, SW_SHOWNORMAL);
                    }
                } else {
                    MessageBoxW(NULL, L"自动下载失败，将打开浏览器下载页面。", L"更新失败",
                                MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST);
                    ShellExecuteW(NULL, L"open", g_installer_url, NULL, NULL, SW_SHOWNORMAL);
                }
            }
        }
        Sleep(g_update_interval_ms);
    }
    return 0;
}

/* ---- entry ---- */

int WINAPI WinMain(HINSTANCE hInst, HINSTANCE hPrev, LPSTR cmdLine, int nShow)
{
    HANDLE hAppProcess = NULL;

    /* 1. get our own directory */
    DWORD dlen = GetModuleFileNameW(NULL, g_dir, MAX_PATH);
    if (!dlen || dlen >= MAX_PATH) return 1;
    WCHAR *bs = g_dir + dlen;
    while (bs > g_dir && *bs != L'\\' && *bs != L'/') bs--;
    *bs = L'\0';

    /* 2. copy current environ */
    {
        WCHAR *env = GetEnvironmentStringsW();
        WCHAR *p = env;
        while (*p) {
            append_env(p);
            p += lstrlenW(p) + 1;
        }
        FreeEnvironmentStringsW(env);
    }

    /* 3. load .env from app dir */
    {
        WCHAR env_path[512];
        lstrcpyW(env_path, g_dir);
        lstrcatW(env_path, L"\\.env");
        load_env_file(env_path);
    }

    /* 3.5. load generated update config (version + download base URL) */
    {
        WCHAR conf_path[512];
        lstrcpyW(conf_path, g_dir);
        lstrcatW(conf_path, L"\\" HAITUN_UPDATE_CONF);
        load_env_file(conf_path);
    }

    /* 4. prepend MSYS2 to PATH */
    {
        WCHAR usr[512], ucrt[512], old_path[PATH_BUF];
        lstrcpyW(usr, g_dir);
        lstrcatW(usr, L"\\msys64\\usr\\bin");
        lstrcpyW(ucrt, g_dir);
        lstrcatW(ucrt, L"\\msys64\\ucrt64\\bin");

        WCHAR *existing = find_env_var(L"PATH");
        if (existing) {
            lstrcpyW(old_path, existing + lstrlenW(L"PATH") + 1);
        } else {
            old_path[0] = L'\0';
        }

        WCHAR new_path[PATH_BUF];
        lstrcpyW(new_path, usr);
        lstrcatW(new_path, L";");
        lstrcatW(new_path, ucrt);
        if (old_path[0]) {
            lstrcatW(new_path, L";");
            lstrcatW(new_path, old_path);
        }
        replace_env(L"PATH", new_path);
    }

    /* 5. CHERE_INVOKING */
    replace_env(L"CHERE_INVOKING", L"1");

    /* 6. set working directory */
    SetCurrentDirectoryW(g_dir);

    /* 7. launch psi-agent.exe with Gateway path defaults + log files.
     *
     * Install layout: {app} IS the haitun-workspace (tools/skills/systems).
     * Soft-default in Python only finds examples/haitun-workspace under a
     * repo root — so the launcher must pass --default-agent explicitly.
     * Workspace soft-default is Desktop/haitun交付; pass it too so install
     * and CLI stay aligned. Paths are resolved at runtime (g_dir + SHGetFolderPath),
     * never hardcoded machine paths.
     */
    {
        WCHAR cmd[CMD_BUF];
        WCHAR out_path[512], err_path[512];
        WCHAR desktop[MAX_PATH];
        WCHAR stamp[32];
        SYSTEMTIME st;
        GetLocalTime(&st);
        wsprintfW(stamp, L"\\%04d%02d%02d-%02d%02d%02d",
                  st.wYear, st.wMonth, st.wDay,
                  st.wHour, st.wMinute, st.wSecond);

        WCHAR log_dir[512];
        lstrcpyW(log_dir, g_dir);
        lstrcatW(log_dir, L"\\logs");
        CreateDirectoryW(log_dir, NULL);

        lstrcpyW(out_path, log_dir);
        lstrcatW(out_path, stamp);
        lstrcatW(out_path, L".out.log");
        lstrcpyW(err_path, log_dir);
        lstrcatW(err_path, stamp);
        lstrcatW(err_path, L".err.log");

        SECURITY_ATTRIBUTES sa = {sizeof(sa), NULL, TRUE};
        HANDLE hIn = GetStdHandle(STD_INPUT_HANDLE);
        if (!hIn || hIn == INVALID_HANDLE_VALUE)
            hIn = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ,
                              &sa, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        HANDLE hOut = CreateFileW(out_path, GENERIC_WRITE, FILE_SHARE_READ,
                                  &sa, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        HANDLE hErr = CreateFileW(err_path, GENERIC_WRITE, FILE_SHARE_READ,
                                  &sa, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);

        /* Quote paths: install dir may contain spaces ("HaiTun Agent"). */
        lstrcpyW(cmd, L"\"");
        lstrcatW(cmd, g_dir);
        lstrcatW(cmd, L"\\psi-agent.exe\" gateway --tray --browser --icon haitun.ico --verbose");
        lstrcatW(cmd, L" --default-agent \"");
        lstrcatW(cmd, g_dir);
        lstrcatW(cmd, L"\"");

        if (SUCCEEDED(SHGetFolderPathW(NULL, CSIDL_DESKTOPDIRECTORY, NULL,
                                        SHGFP_TYPE_CURRENT, desktop))) {
            lstrcatW(cmd, L" --default-workspace \"");
            lstrcatW(cmd, desktop);
            lstrcatW(cmd, L"\\");
            lstrcatW(cmd, DEFAULT_WS_NAME);
            lstrcatW(cmd, L"\"");
        }

        PROCESS_INFORMATION pi = {0};
        STARTUPINFOW si = {sizeof(si)};
        si.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
        si.wShowWindow = SW_HIDE;
        si.hStdInput  = hIn;
        si.hStdOutput = hOut;
        si.hStdError  = hErr;

        CreateProcessW(NULL, cmd, NULL, NULL, TRUE,
                       CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
                       g_env, g_dir, &si, &pi);
        if (pi.hThread) CloseHandle(pi.hThread);
        hAppProcess = pi.hProcess;
        if (hOut != INVALID_HANDLE_VALUE) CloseHandle(hOut);
        if (hErr != INVALID_HANDLE_VALUE) CloseHandle(hErr);
        if (hIn != INVALID_HANDLE_VALUE && hIn != GetStdHandle(STD_INPUT_HANDLE))
            CloseHandle(hIn);
    }

    /* 8. background update checker (checks download server version.txt) */
    configure_updater();
    if (g_local_version[0] && g_version_url[0] && g_installer_url[0]) {
        HANDLE hThread = CreateThread(NULL, 0, update_check_thread, NULL, 0, NULL);
        if (hThread)
            CloseHandle(hThread);
    }

    /* Keep the launcher alive while the app runs so the updater thread stays up. */
    if (hAppProcess) {
        WaitForSingleObject(hAppProcess, INFINITE);
        CloseHandle(hAppProcess);
    }

    return 0;
}
