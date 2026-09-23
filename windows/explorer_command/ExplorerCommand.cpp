#include <windows.h>
#include <knownfolders.h>
#include <shlobj.h>
#include <shellapi.h>
#include <shobjidl.h>
#include <cstring>
#include <cwchar>
#include <iterator>
#include <new>
#include <string>
#include <vector>
#include <filesystem>
#include <system_error>

namespace {

const CLSID CLSID_BondSealExplorerCommand =
    {0x62c7eb66, 0xa11d, 0x4f91, {0x93, 0x4c, 0x3d, 0xaa, 0x9a, 0x3e, 0xf8, 0x21}};

volatile LONG g_liveObjects = 0;
volatile LONG g_serverLocks = 0;
HMODULE g_module = nullptr;

std::wstring lower(std::wstring value) {
    for (wchar_t& ch : value) {
        if (ch >= L'A' && ch <= L'Z') ch = static_cast<wchar_t>(ch + (L'a' - L'A'));
    }
    return value;
}

bool supported_document(const std::filesystem::path& path) {
    const auto extension = lower(path.extension().wstring());
    return extension == L".doc" || extension == L".docx";
}

bool same_parent(const std::filesystem::path& left, const std::filesystem::path& right) {
    const auto a = left.parent_path().lexically_normal().wstring();
    const auto b = right.parent_path().lexically_normal().wstring();
    return CompareStringOrdinal(a.c_str(), static_cast<int>(a.size()),
                                b.c_str(), static_cast<int>(b.size()), TRUE) == CSTR_EQUAL;
}

bool read_selection(IShellItemArray* items, std::vector<std::filesystem::path>& paths) {
    paths.clear();
    if (!items) return false;
    DWORD count = 0;
    if (FAILED(items->GetCount(&count)) || count == 0) return false;

    for (DWORD index = 0; index < count; ++index) {
        IShellItem* item = nullptr;
        if (FAILED(items->GetItemAt(index, &item)) || !item) return false;
        PWSTR displayPath = nullptr;
        const HRESULT result = item->GetDisplayName(SIGDN_FILESYSPATH, &displayPath);
        item->Release();
        if (FAILED(result) || !displayPath) return false;
        std::filesystem::path path(displayPath);
        CoTaskMemFree(displayPath);
        if (!supported_document(path) || !path.is_absolute()) return false;
        if (!paths.empty() && !same_parent(paths.front(), path)) return false;
        paths.push_back(std::move(path));
    }
    return !paths.empty();
}

std::string utf8(const std::wstring& value) {
    if (value.empty()) return {};
    const int needed = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
        static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (needed <= 0) return {};
    std::string result(static_cast<size_t>(needed), '\0');
    if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
        static_cast<int>(value.size()), result.data(), needed, nullptr, nullptr) != needed) return {};
    return result;
}

std::string json_string(const std::wstring& value) {
    const std::string bytes = utf8(value);
    std::string result = "\"";
    static const char hex[] = "0123456789abcdef";
    for (unsigned char ch : bytes) {
        switch (ch) {
        case '"': result += "\\\""; break;
        case '\\': result += "\\\\"; break;
        case '\b': result += "\\b"; break;
        case '\f': result += "\\f"; break;
        case '\n': result += "\\n"; break;
        case '\r': result += "\\r"; break;
        case '\t': result += "\\t"; break;
        default:
            if (ch < 0x20) {
                result += "\\u00";
                result.push_back(hex[ch >> 4]);
                result.push_back(hex[ch & 0x0f]);
            } else {
                result.push_back(static_cast<char>(ch));
            }
        }
    }
    result += '"';
    return result;
}

std::wstring quote_argument(const std::wstring& value) {
    std::wstring result = L"\"";
    size_t slashes = 0;
    for (wchar_t ch : value) {
        if (ch == L'\\') {
            ++slashes;
        } else if (ch == L'"') {
            result.append(slashes * 2 + 1, L'\\');
            result.push_back(ch);
            slashes = 0;
        } else {
            result.append(slashes, L'\\');
            slashes = 0;
            result.push_back(ch);
        }
    }
    result.append(slashes * 2, L'\\');
    result.push_back(L'"');
    return result;
}

bool write_request(const std::vector<std::filesystem::path>& paths, std::filesystem::path& requestPath) {
    PWSTR localAppData = nullptr;
    if (FAILED(SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_DEFAULT, nullptr, &localAppData))) return false;
    std::filesystem::path tempRoot(localAppData);
    CoTaskMemFree(localAppData);
    tempRoot /= L"Temp";

    GUID id{};
    if (FAILED(CoCreateGuid(&id))) return false;
    wchar_t guidText[40]{};
    if (!StringFromGUID2(id, guidText, static_cast<int>(std::size(guidText)))) return false;
    std::wstring fileName = guidText;
    for (wchar_t& ch : fileName) {
        if (ch == L'{' || ch == L'}' || ch == L'-') ch = L'_';
    }
    requestPath = tempRoot / (fileName + L".json");

    std::string body = "{\"version\":1,\"files\":[";
    for (size_t i = 0; i < paths.size(); ++i) {
        if (i) body += ',';
        body += json_string(paths[i].wstring());
    }
    body += "],\"output_directory\":" + json_string(paths.front().parent_path().wstring()) + "}";

    HANDLE file = CreateFileW(requestPath.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                              FILE_ATTRIBUTE_TEMPORARY, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    DWORD written = 0;
    const BOOL ok = WriteFile(file, body.data(), static_cast<DWORD>(body.size()), &written, nullptr);
    if (ok) FlushFileBuffers(file);
    CloseHandle(file);
    if (!ok || written != body.size()) {
        DeleteFileW(requestPath.c_str());
        return false;
    }
    return true;
}

HRESULT shell_open_gui(const std::filesystem::path& executable, const std::wstring& parameters) {
    SHELLEXECUTEINFOW info{};
    info.cbSize = sizeof(info);
    info.fMask = SEE_MASK_FLAG_NO_UI | SEE_MASK_NOASYNC;
    info.lpVerb = L"open";
    info.lpFile = executable.c_str();
    info.lpParameters = parameters.c_str();
    info.nShow = SW_SHOWNORMAL;
    if (!ShellExecuteExW(&info)) return HRESULT_FROM_WIN32(GetLastError());
    return S_OK;
}

HRESULT launch_gui(const std::filesystem::path& requestPath) {
    const std::wstring parameters = L"--request-file " + quote_argument(requestPath.wstring());

    // The DLL lives under the installed MSIX package root. Prefer its adjacent
    // packaged GUI executable so a user-disabled app alias cannot break the menu.
    std::wstring modulePath(32768, L'\0');
    const DWORD length = g_module ? GetModuleFileNameW(g_module, modulePath.data(),
        static_cast<DWORD>(modulePath.size())) : 0;
    if (length > 0 && length < modulePath.size()) {
        modulePath.resize(length);
        const auto packageRoot = std::filesystem::path(modulePath).parent_path().parent_path();
        const auto gui = packageRoot / L"GUI" / L"BondSealGUI.exe";
        std::error_code error;
        if (std::filesystem::is_regular_file(gui, error)) {
            const HRESULT result = shell_open_gui(gui, parameters);
            if (SUCCEEDED(result)) return result;
        }
    }

    // App execution aliases remain a fallback for package layouts or activation
    // policies where launching the package-relative executable is unavailable.
    PWSTR localAppData = nullptr;
    HRESULT result = SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_DEFAULT, nullptr, &localAppData);
    if (FAILED(result)) return result;
    std::filesystem::path alias(localAppData);
    CoTaskMemFree(localAppData);
    alias /= L"Microsoft";
    alias /= L"WindowsApps";
    alias /= L"bondseal-gui.exe";
    return shell_open_gui(alias, parameters);
}
HRESULT copy_title(PCWSTR value, LPWSTR* output) {
    if (!output) return E_POINTER;
    const size_t size = (wcslen(value) + 1) * sizeof(wchar_t);
    *output = static_cast<LPWSTR>(CoTaskMemAlloc(size));
    if (!*output) return E_OUTOFMEMORY;
    memcpy(*output, value, size);
    return S_OK;
}

class ExplorerCommand final : public IExplorerCommand {
public:
    ExplorerCommand() { InterlockedIncrement(&g_liveObjects); }
    ~ExplorerCommand() { InterlockedDecrement(&g_liveObjects); }

    IFACEMETHODIMP QueryInterface(REFIID iid, void** object) override {
        if (!object) return E_POINTER;
        *object = nullptr;
        if (iid == IID_IUnknown || iid == IID_IExplorerCommand) {
            *object = static_cast<IExplorerCommand*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&references_); }
    IFACEMETHODIMP_(ULONG) Release() override {
        const ULONG remaining = InterlockedDecrement(&references_);
        if (!remaining) delete this;
        return remaining;
    }

    IFACEMETHODIMP GetTitle(IShellItemArray*, LPWSTR* title) override {
        return copy_title(L"生成签署页合集", title);
    }
    IFACEMETHODIMP GetIcon(IShellItemArray*, LPWSTR* icon) override {
        if (icon) *icon = nullptr;
        return E_NOTIMPL;
    }
    IFACEMETHODIMP GetToolTip(IShellItemArray*, LPWSTR* tip) override {
        if (tip) *tip = nullptr;
        return E_NOTIMPL;
    }
    IFACEMETHODIMP GetCanonicalName(GUID* name) override {
        if (!name) return E_POINTER;
        *name = GUID_NULL;
        return S_OK;
    }
    IFACEMETHODIMP GetState(IShellItemArray* items, BOOL, EXPCMDSTATE* state) override {
        if (!state) return E_POINTER;
        std::vector<std::filesystem::path> paths;
        *state = read_selection(items, paths) ? ECS_ENABLED : ECS_HIDDEN;
        return S_OK;
    }
    IFACEMETHODIMP Invoke(IShellItemArray* items, IBindCtx*) override {
        std::vector<std::filesystem::path> paths;
        if (!read_selection(items, paths)) {
            MessageBoxW(nullptr, L"请选择同一文件夹内的 .doc 或 .docx 文件。",
                        L"签署页合集", MB_OK | MB_ICONINFORMATION);
            return E_INVALIDARG;
        }
        std::filesystem::path requestPath;
        if (!write_request(paths, requestPath)) {
            MessageBoxW(nullptr, L"无法创建临时请求文件。请检查用户临时目录权限。",
                        L"签署页合集", MB_OK | MB_ICONERROR);
            return HRESULT_FROM_WIN32(GetLastError());
        }
        const HRESULT result = launch_gui(requestPath);
        if (FAILED(result)) {
            DeleteFileW(requestPath.c_str());
            MessageBoxW(nullptr,
                L"未能启动签署页合集应用。请先安装应用包，或从开始菜单启动后重试。",
                L"签署页合集", MB_OK | MB_ICONERROR);
        }
        return result;
    }
    IFACEMETHODIMP GetFlags(EXPCMDFLAGS* flags) override {
        if (!flags) return E_POINTER;
        *flags = ECF_DEFAULT;
        return S_OK;
    }
    IFACEMETHODIMP EnumSubCommands(IEnumExplorerCommand** commands) override {
        if (commands) *commands = nullptr;
        return E_NOTIMPL;
    }

private:
    volatile LONG references_ = 1;
};

class ClassFactory final : public IClassFactory {
public:
    ClassFactory() { InterlockedIncrement(&g_liveObjects); }
    ~ClassFactory() { InterlockedDecrement(&g_liveObjects); }

    IFACEMETHODIMP QueryInterface(REFIID iid, void** object) override {
        if (!object) return E_POINTER;
        *object = nullptr;
        if (iid == IID_IUnknown || iid == IID_IClassFactory) {
            *object = static_cast<IClassFactory*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&references_); }
    IFACEMETHODIMP_(ULONG) Release() override {
        const ULONG remaining = InterlockedDecrement(&references_);
        if (!remaining) delete this;
        return remaining;
    }
    IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID iid, void** object) override {
        if (outer) return CLASS_E_NOAGGREGATION;
        auto* command = new (std::nothrow) ExplorerCommand();
        if (!command) return E_OUTOFMEMORY;
        const HRESULT result = command->QueryInterface(iid, object);
        command->Release();
        return result;
    }
    IFACEMETHODIMP LockServer(BOOL lock) override {
        if (lock) InterlockedIncrement(&g_serverLocks);
        else InterlockedDecrement(&g_serverLocks);
        return S_OK;
    }

private:
    volatile LONG references_ = 1;
};

} // namespace

STDAPI DllGetClassObject(
    REFCLSID classId, REFIID interfaceId, LPVOID* object) {
    if (!IsEqualCLSID(classId, CLSID_BondSealExplorerCommand)) return CLASS_E_CLASSNOTAVAILABLE;
    auto* factory = new (std::nothrow) ClassFactory();
    if (!factory) return E_OUTOFMEMORY;
    const HRESULT result = factory->QueryInterface(interfaceId, object);
    factory->Release();
    return result;
}

STDAPI DllCanUnloadNow(void) {
    return (InterlockedCompareExchange(&g_liveObjects, 0, 0) == 0 &&
            InterlockedCompareExchange(&g_serverLocks, 0, 0) == 0) ? S_OK : S_FALSE;
}

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}
