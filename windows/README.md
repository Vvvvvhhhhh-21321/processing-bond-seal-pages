# Windows desktop integration

This is a thin Windows host for the canonical Python package in `.agents/skills/processing-bond-seal-pages/scripts/bond_seal_pages`.

## User entry points

- In Windows 11 File Explorer, select one or more `.doc` or `.docx` files from one folder, right-click, and choose **生成签署页合集**. The native extension writes one UTF-8 request and opens one GUI process. It does not start Word or convert documents inside Explorer.
- The GUI starts collection immediately, shows progress and per-file errors, and opens the generated PDF or sidecar directory. Cancel signals the core and terminates its isolated Word worker.
- The packaged `bondseal.exe` alias is the automation entry. For a quick batch, call `bondseal.exe collect --request-file <absolute-json-path> --json`. Request v1 has a `version`, an explicit absolute `files` list, and optional `output_directory`.

Selected Word files must be direct children of one folder. Microsoft Word desktop must be installed. The package bundles Python and Python dependencies, not Microsoft Word.

## Build

Use Python 3.13 x64, uv, Visual Studio 2022 C++ Build Tools, and the Windows 10/11 SDK. From the repository root:

```powershell
uv sync --locked --group build
.\windows\scripts\build-package.ps1 -Version 0.1.0.0
```

The build creates GUI and CLI PyInstaller onedir bundles and one `BondSealWordWorker.exe` beside both hosts.

## Signing and distribution

The checked-in publisher `CN=BondSealPages-Development` is a placeholder. CI emits a **test-signed internal package** and the matching public `.cer` plus fingerprint; the private key is generated on the runner and is never uploaded. For an internal test, verify the `.cer` fingerprint, then import it into the target user's **Trusted People** and **Trusted Root Certification Authorities** stores before installing the MSIX. Remove the test certificate from both stores after testing. Never upload a test-signed package as a public release. Public distribution needs a permanent package publisher identity and a publicly trusted signing certificate, signing service, or Store distribution. No signing key or certificate belongs in this repository.

## Validation boundary

Windows CI compiles the x64 COM DLL, freezes Python hosts, and creates the MSIX. Explorer menu visibility, real multi-selection, package registration, upgrade/uninstall, and alias activation still need verification using a signed internal package on Windows 11.
