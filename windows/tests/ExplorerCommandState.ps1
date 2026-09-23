$ErrorActionPreference = 'Stop'
$definition = @(
'using System;',
'using System.Runtime.InteropServices;',
'public static class BondSealExplorerCommandProbe {',
'    [DllImport("ole32.dll", ExactSpelling=true, PreserveSig=true)]',
'    public static extern int CoCreateInstance(ref Guid clsid, IntPtr outer, uint context, ref Guid iid, out IntPtr instance);',
'    [UnmanagedFunctionPointer(CallingConvention.StdCall)]',
'    public delegate int GetTitleFn(IntPtr self, IntPtr items, out IntPtr title);',
'    [UnmanagedFunctionPointer(CallingConvention.StdCall)]',
'    public delegate int GetStateFn(IntPtr self, IntPtr items, int slow, out uint state);',
'}'
) -join [Environment]::NewLine
Add-Type -TypeDefinition $definition
$clsid = [Guid]'{62C7EB66-A11D-4F91-934C-3DAA9A3EF821}'
$iid = [Guid]'a08ce4d0-fa25-44ab-b57c-c7b1c323e0b9'
$command = [IntPtr]::Zero
$hr = [BondSealExplorerCommandProbe]::CoCreateInstance([ref]$clsid,[IntPtr]::Zero,1,[ref]$iid,[ref]$command)
if ($hr -ne 0) { throw ('IExplorerCommand activation failed: 0x{0:X8}' -f ($hr -band 0xffffffff)) }
try {
    $vtable = [Runtime.InteropServices.Marshal]::ReadIntPtr($command)
    $titleMethod = [Runtime.InteropServices.Marshal]::ReadIntPtr($vtable,[IntPtr]::Size * 3)
    $stateMethod = [Runtime.InteropServices.Marshal]::ReadIntPtr($vtable,[IntPtr]::Size * 7)
    $getTitle = [Runtime.InteropServices.Marshal]::GetDelegateForFunctionPointer($titleMethod,[type][BondSealExplorerCommandProbe+GetTitleFn])
    $getState = [Runtime.InteropServices.Marshal]::GetDelegateForFunctionPointer($stateMethod,[type][BondSealExplorerCommandProbe+GetStateFn])
    $titlePointer = [IntPtr]::Zero
    $hr = $getTitle.Invoke($command,[IntPtr]::Zero,[ref]$titlePointer)
    if ($hr -ne 0 -or $titlePointer -eq [IntPtr]::Zero) { throw 'Explorer command title unavailable.' }
    try { $title = [Runtime.InteropServices.Marshal]::PtrToStringUni($titlePointer) }
    finally { [Runtime.InteropServices.Marshal]::FreeCoTaskMem($titlePointer) }
    if ($title -ne '生成签署页合集') { throw "Unexpected Explorer command title: $title" }
    $state = [uint32]2
    $hr = $getState.Invoke($command,[IntPtr]::Zero,0,[ref]$state)
    if ($hr -ne 0 -or $state -ne 0) { throw "Explorer command hidden for an incomplete selection: hr=$hr state=$state" }
    Write-Host 'Explorer command COM title and visibility: OK'
}
finally { [Runtime.InteropServices.Marshal]::Release($command) | Out-Null }
