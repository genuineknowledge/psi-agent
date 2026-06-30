#include <windows.h>
#include <stdio.h>

#define MAX_ENV 32767

static WCHAR g_dir[MAX_PATH];
static WCHAR g_env[MAX_ENV * 2];  /* double size: wide-char bytes */
static int   g_env_len;

/* ---- helpers ---- */

static void set_env(const WCHAR *name, const WCHAR *val)
{
    int n = lstrlenW(name);
    int v = lstrlenW(val);
    if (g_env_len + n + 1 + v + 1 > sizeof(g_env) / sizeof(WCHAR))
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
    if (g_env_len + len + 2 > sizeof(g_env) / sizeof(WCHAR))
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
        WCHAR *end = pos + lstrlenW(pos) + 1;
        WCHAR *tail = end + lstrlenW(end);
        int tail_len = (int)(g_env + g_env_len - tail);
        int old_len = (int)(end - pos);
        int new_len = lstrlenW(name) + 1 + lstrlenW(val);
        int diff = new_len - old_len;
        if (g_env_len + diff + 1 > (int)(sizeof(g_env) / sizeof(WCHAR)))
            return;
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

static void load_env_file(const WCHAR *path)
{
    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ,
                           NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;

    DWORD size = GetFileSize(h, NULL);
    if (size == INVALID_FILE_SIZE || size > 65536) { CloseHandle(h); return; }

    char *buf = HeapAlloc(GetProcessHeap(), 0, size + 1);
    DWORD read;
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
                    WCHAR wkey[256], wval[4096];
                    MultiByteToWideChar(CP_UTF8, 0, key, -1, wkey, 256);
                    MultiByteToWideChar(CP_UTF8, 0, val, -1, wval, 4096);
                    replace_env(wkey, wval);
                }
            }
        }

        *nl = saved;
        line = saved ? nl + (*nl == '\r' ? (*nl + 1 == '\n' ? 2 : 1) : 1) : nl;
    }
    HeapFree(GetProcessHeap(), 0, buf);
}

/* ---- entry ---- */

int WINAPI WinMain(HINSTANCE hInst, HINSTANCE hPrev, LPSTR cmdLine, int nShow)
{
    /* 1. get our own directory */
    GetModuleFileNameW(NULL, g_dir, MAX_PATH);
    WCHAR *bs = g_dir + lstrlenW(g_dir);
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
        WCHAR env_path[MAX_PATH];
        lstrcpyW(env_path, g_dir);
        lstrcatW(env_path, L"\\.env");
        load_env_file(env_path);
    }

    /* 4. prepend MSYS2 to PATH */
    {
        WCHAR usr[MAX_PATH], ucrt[MAX_PATH], old_path[8192];
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

        WCHAR new_path[16384];
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

    /* 7. launch psi-agent.exe with --verbose and log files */
    {
        WCHAR cmd[1024];
        WCHAR out_path[MAX_PATH], err_path[MAX_PATH];
        WCHAR stamp[32];
        SYSTEMTIME st;
        GetLocalTime(&st);
        wsprintfW(stamp, L"\\%04d%02d%02d-%02d%02d%02d",
                  st.wYear, st.wMonth, st.wDay,
                  st.wHour, st.wMinute, st.wSecond);

        lstrcpyW(out_path, g_dir);
        lstrcatW(out_path, stamp);
        lstrcatW(out_path, L".out.log");
        lstrcpyW(err_path, g_dir);
        lstrcatW(err_path, stamp);
        lstrcatW(err_path, L".err.log");

        HANDLE hOut = CreateFileW(out_path, GENERIC_WRITE, FILE_SHARE_READ,
                                  NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        HANDLE hErr = CreateFileW(err_path, GENERIC_WRITE, FILE_SHARE_READ,
                                  NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);

        lstrcpyW(cmd, g_dir);
        lstrcatW(cmd, L"\\psi-agent.exe gateway --tray haitun.ico --verbose");

        PROCESS_INFORMATION pi = {0};
        STARTUPINFOW si = {sizeof(si)};
        si.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
        si.wShowWindow = SW_HIDE;
        si.hStdOutput = hOut;
        si.hStdError  = hErr;
        si.hStdInput  = GetStdHandle(STD_INPUT_HANDLE);

        CreateProcessW(NULL, cmd, NULL, NULL, TRUE,
                       CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
                       g_env, g_dir, &si, &pi);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        CloseHandle(hOut);
        CloseHandle(hErr);
    }

    return 0;
}
