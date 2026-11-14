<#
.SYNOPSIS
  mu WSL2 host adapter, Windows side: makes this Windows 11 PC a mu compute node. A WSL2 distro runs the mu toolchain,
  starts at boot with nobody signed in, and is stopped cleanly when Windows shuts down or restarts.

.DESCRIPTION
  Actions (the first argument):
    install    (default) set everything up. Rerunning it on a configured PC changes nothing and says so.
    status     check every setting and change nothing; exit code 1 if a check fails. -Json prints one JSON object.
    uninstall  remove the startup task and the shutdown hook, and restore the Windows settings this script changed
               (their previous values are recorded in <MuRoot>\state\wsl2-host.json). The distro is kept unless
               -RemoveDistro is given and confirmed.
    test-stop  stop WSL the way the shutdown hook does (as SYSTEM), start the distro again through the startup task
               and show its status: the clean-stop test without restarting Windows. Stops every running WSL distro.

  Run it from PowerShell. It asks for administrator rights when an action needs them (status does not):
    powershell -NoProfile -ExecutionPolicy Bypass -File .\mu-wsl2-host.ps1 install
  Run it as the Windows user who will own the distro (an administrator). The distro, %UserProfile%\.wslconfig and
  the startup task all belong to that user. README.md next to this script explains every setting and the tests.

  What install does, each step only when needed:
    1. WSL: installs it if missing (then asks for a restart), and updates it if older than 2.5.4.
    2. Distro: wsl --install Ubuntu-24.04 --name <Distro> --location <Location> --no-launch (no first-run prompt:
       the user is created as root inside).
    3. %UserProfile%\.wslconfig, merged (other keys are kept): [general] instanceIdleTimeout=-1 keeps the distro
       running with no client attached; [wsl2] memory, processors, swap, guiApplications=false.
    4. Inside the distro, as root: mu-wsl2-distro.sh (wsl.conf, the user without sudo, write-back timers, clean-stop
       marker, linger, the GitHub deploy key, the clone; the job layer's node: sshd on loopback, the gate, the relay
       tunnel, security updates, interop off). It gets the drive letter of the distro's disk (--host-drive) and the
       gate's source (mu-node-gate, next to this script).
    5. Windows power: never sleep or hibernate on AC power, Fast Startup off, automatic sign-in after updates off.
    6. The shutdown hook: the mu-wsl-shutdown service (LocalSystem), built here with the in-box C# compiler. On every
       orderly shutdown or restart it stops WSLService, which powers the distros off cleanly before Windows ends.
    7. The startup task \mu\mu-compute: at startup (+30 s), then every 5 minutes, runs "wsl.exe -d <Distro> --exec
       /bin/true" as this user with S4U (no stored password), so the distro runs with nobody signed in.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\mu-wsl2-host.ps1 install -Heartbeat on

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 status
#>
[CmdletBinding()]
param(
  # install | status | uninstall | test-stop
  [Parameter(Position = 0)]
  [ValidateSet('install', 'status', 'uninstall', 'test-stop')]
  [string]$Action = 'install',
  # the WSL distro's name
  [string]$Distro = 'mu-compute',
  # where its virtual disk lives (default <MuRoot>\wsl\<Distro>)
  [string]$Location = '',
  # the image installed when the distro does not exist
  [string]$Image = 'Ubuntu-24.04',
  # the Linux user mu runs as (created without sudo)
  [string]$User = 'mu',
  # WSL memory in GB (default: 75% of this PC's memory)
  [int]$MemoryGB = 0,
  # WSL processors (default: every logical processor)
  [int]$Processors = 0,
  # WSL swap in GB
  [int]$SwapGB = 8,
  # the mu repo the distro clones (read-only deploy key)
  [string]$Repo = 'git@github.com:martand-systems/mu.git',
  # branch or tag to clone (default: the repo's default branch)
  [string]$Branch = '',
  # don't clone the repo
  [switch]$NoClone,
  # also install mu in the distro (install.sh --system --user <User> as root, then ./install.sh as <User>)
  [switch]$InstallMu,
  # the heartbeat test service: on or off
  [ValidateSet('on', 'off')]
  [string]$Heartbeat,
  # leave sleep, hibernate, Fast Startup and automatic sign-in alone
  [switch]$NoPowerChanges,
  # don't install the shutdown hook
  [switch]$NoShutdownHook,
  # don't restart WSL or the distro to apply changes (they apply at the next restart)
  [switch]$NoRestart,
  # uninstall: also delete the distro and everything in it (asks to type its name)
  [switch]$RemoveDistro,
  # uninstall -RemoveDistro: don't ask
  [switch]$Force,
  # status: print one JSON object
  [switch]$Json,
  # status: skip the network check inside the distro (GitHub access)
  [switch]$Offline,
  # mu's folder on Windows (default <system drive>\Mu)
  [string]$MuRoot = '',
  # internal: set when the script restarted itself elevated
  [switch]$Elevated
)

$ErrorActionPreference = 'Stop'
$script:BoundParams = $PSBoundParameters
$script:SelfPath = $PSCommandPath
$script:Utf8 = New-Object System.Text.UTF8Encoding($false)
$script:WslExe = "$env:ProgramFiles\WSL\wsl.exe"
$script:TaskPath = '\mu\'
$script:TaskName = 'mu-compute'
$script:ServiceName = 'mu-wsl-shutdown'
$script:ServiceDisplayName = 'mu: stop WSL cleanly before shutdown'
$script:DistroScriptName = 'mu-wsl2-distro.sh'
$script:DistroScriptTarget = '/usr/local/sbin/mu-wsl2-distro'
$script:GateName = 'mu-node-gate'
$script:GateTarget = '/usr/local/lib/mu/mu-node-gate.in'
$script:Changes = New-Object System.Collections.ArrayList
$script:Checks = New-Object System.Collections.ArrayList
$script:State = $null

# ============================================================================ the shutdown hook (C# 5, built on the PC)
$script:ShutdownServiceSource = @'
// mu-wsl-shutdown: stops WSL cleanly before Windows shuts down or restarts (mu WSL2 host adapter, mu-wsl2-host.ps1).
//
// WSL's own service (WSLService) accepts neither SHUTDOWN nor PRESHUTDOWN, and its VM is powered off hard when the
// service process dies at the end of a Windows shutdown: Linux gets no signal, no sync and no unmount. Stopping
// WSLService runs WSL's clean path instead, for every distro of every user: systemctl poweroff (bounded by
// /etc/wsl.conf [boot] initTimeout), then sync, unmount and power-off of the VM.
//
// This service accepts PRESHUTDOWN, which the SCM sends on every orderly shutdown or restart, whoever is signed in,
// and waits for up to the service's preshutdown timeout. The control handler only reports STOP_PENDING and wakes the
// worker: a handler must return within 30 s, and the SCM handles one control at a time, so stopping WSLService from
// inside it could stall. The worker stops WSLService, waits up to 80 s while bumping the checkpoint, logs, and
// reports STOPPED. STOP (uninstall, reinstall, a manual stop) exits without touching WSL.
// At every start the service sets its own preshutdown timeout to 90 s (ChangeServiceConfig2: effective at once) and
// puts itself first in HKLM\SYSTEM\CurrentControlSet\Control\PreshutdownOrder (listed services stop before others).
//
//   mu-wsl-shutdown.exe [--log-dir DIR]             the service (installed by mu-wsl2-host.ps1)
//   mu-wsl-shutdown.exe --stop-wsl [--log-dir DIR]  stop WSLService the same way now (as an administrator or SYSTEM);
//                                                   exit code 0 once it has stopped
// Log: <the exe's folder>\..\logs\mu-wsl-shutdown.log (UTC times).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.ServiceProcess;
using System.Threading;
using Microsoft.Win32;

static class MuWslShutdown
{
    const string ServiceName = "mu-wsl-shutdown";
    const string WslServiceName = "WSLService";
    const int PreshutdownTimeoutMs = 90000;
    const int WslStopWaitMs = 80000;

    const int SERVICE_WIN32_OWN_PROCESS = 0x10;
    const int SERVICE_STOPPED = 1, SERVICE_START_PENDING = 2, SERVICE_STOP_PENDING = 3, SERVICE_RUNNING = 4;
    const int SERVICE_ACCEPT_STOP = 0x1, SERVICE_ACCEPT_PRESHUTDOWN = 0x100;
    const int SERVICE_CONTROL_STOP = 0x1, SERVICE_CONTROL_INTERROGATE = 0x4, SERVICE_CONTROL_PRESHUTDOWN = 0xF;
    const int NO_ERROR = 0, ERROR_CALL_NOT_IMPLEMENTED = 120, ERROR_FAILED_SERVICE_CONTROLLER_CONNECT = 1063;
    const int SC_MANAGER_CONNECT = 0x1, SERVICE_CHANGE_CONFIG = 0x2, SERVICE_CONFIG_PRESHUTDOWN_INFO = 7;

    [StructLayout(LayoutKind.Sequential)]
    struct SERVICE_STATUS
    {
        public int dwServiceType, dwCurrentState, dwControlsAccepted, dwWin32ExitCode, dwServiceSpecificExitCode,
            dwCheckPoint, dwWaitHint;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct SERVICE_PRESHUTDOWN_INFO { public int dwPreshutdownTimeout; }
    delegate void ServiceMainFn(int argc, IntPtr argv);
    delegate int HandlerExFn(int control, int eventType, IntPtr eventData, IntPtr context);
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct SERVICE_TABLE_ENTRY { public string lpServiceName; public ServiceMainFn lpServiceProc; }

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool StartServiceCtrlDispatcher(SERVICE_TABLE_ENTRY[] table);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr RegisterServiceCtrlHandlerEx(string serviceName, HandlerExFn handler, IntPtr context);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool SetServiceStatus(IntPtr handle, ref SERVICE_STATUS status);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr OpenSCManager(string machineName, string databaseName, int access);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr OpenService(IntPtr scm, string serviceName, int access);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool ChangeServiceConfig2(IntPtr service, int infoLevel, ref SERVICE_PRESHUTDOWN_INFO info);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool CloseServiceHandle(IntPtr handle);

    static readonly object Sync = new object();
    static readonly object LogSync = new object();
    static readonly ManualResetEvent Wake = new ManualResetEvent(false);
    static IntPtr statusHandle = IntPtr.Zero;
    static int checkPoint = 0;
    static int pendingControl = 0;
    static ServiceMainFn mainKeep;      // the delegates passed to native code must stay alive
    static HandlerExFn handlerKeep;
    static string logPath = "mu-wsl-shutdown.log";
    static bool toConsole = false;

    static int Main(string[] args)
    {
        string exe = System.Reflection.Assembly.GetExecutingAssembly().Location;
        string exeDir = Path.GetDirectoryName(exe);
        string root = Path.GetDirectoryName(exeDir);
        string logDir = Path.Combine(root == null ? exeDir : root, "logs");
        bool stopNow = false;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--log-dir" && i + 1 < args.Length) logDir = args[++i];
            else if (args[i] == "--stop-wsl") stopNow = true;
            else { Console.Error.WriteLine("usage: mu-wsl-shutdown.exe [--stop-wsl] [--log-dir DIR]"); return 2; }
        }
        logPath = Path.Combine(logDir, "mu-wsl-shutdown.log");
        if (stopNow)
        {
            toConsole = true;
            Log("--stop-wsl, run by " + Environment.UserDomainName + "\\" + Environment.UserName);
            return StopWsl(false) ? 0 : 1;
        }
        mainKeep = ServiceMain;
        SERVICE_TABLE_ENTRY[] table = new SERVICE_TABLE_ENTRY[2];
        table[0].lpServiceName = ServiceName;
        table[0].lpServiceProc = mainKeep;
        if (!StartServiceCtrlDispatcher(table))
        {
            int err = Marshal.GetLastWin32Error();
            if (err == ERROR_FAILED_SERVICE_CONTROLLER_CONNECT)
            {
                Console.Error.WriteLine("mu-wsl-shutdown is a Windows service (installed by mu-wsl2-host.ps1). " +
                    "To stop WSL the same way now: mu-wsl-shutdown.exe --stop-wsl");
                return 2;
            }
            Log("StartServiceCtrlDispatcher failed: error " + err);
            return 1;
        }
        return 0;
    }

    static void ServiceMain(int argc, IntPtr argv)
    {
        handlerKeep = Handler;
        statusHandle = RegisterServiceCtrlHandlerEx(ServiceName, handlerKeep, IntPtr.Zero);
        if (statusHandle == IntPtr.Zero)
        {
            Log("RegisterServiceCtrlHandlerEx failed: error " + Marshal.GetLastWin32Error());
            return;
        }
        Report(SERVICE_START_PENDING, 0, 10000);
        Configure();
        Report(SERVICE_RUNNING, SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_PRESHUTDOWN, 0);
        Log("running: stops " + WslServiceName + " when Windows shuts down or restarts");
        Wake.WaitOne();
        int control;
        lock (Sync) control = pendingControl;
        if (control == SERVICE_CONTROL_PRESHUTDOWN)
        {
            Log("preshutdown: Windows is shutting down or restarting");
            StopWsl(true);
        }
        else
        {
            Log("stop: exiting without touching WSL (uninstall, reinstall or a manual stop)");
        }
        Report(SERVICE_STOPPED, 0, 0);
    }

    // The control handler: never blocks. PRESHUTDOWN and STOP report STOP_PENDING and wake the worker.
    static int Handler(int control, int eventType, IntPtr eventData, IntPtr context)
    {
        if (control == SERVICE_CONTROL_PRESHUTDOWN || control == SERVICE_CONTROL_STOP)
        {
            lock (Sync)
            {
                if (pendingControl != 0) return NO_ERROR;
                pendingControl = control;
            }
            Report(SERVICE_STOP_PENDING, 0, 5000);
            Wake.Set();
            return NO_ERROR;
        }
        if (control == SERVICE_CONTROL_INTERROGATE) return NO_ERROR;
        return ERROR_CALL_NOT_IMPLEMENTED;
    }

    static void Report(int state, int accepted, int waitHint)
    {
        lock (Sync)
        {
            if (statusHandle == IntPtr.Zero) return;
            SERVICE_STATUS s = new SERVICE_STATUS();
            s.dwServiceType = SERVICE_WIN32_OWN_PROCESS;
            s.dwCurrentState = state;
            s.dwControlsAccepted = accepted;
            s.dwWin32ExitCode = NO_ERROR;
            s.dwWaitHint = waitHint;
            s.dwCheckPoint = (state == SERVICE_RUNNING || state == SERVICE_STOPPED) ? 0 : ++checkPoint;
            if (!SetServiceStatus(statusHandle, ref s)) Log("SetServiceStatus failed: error " + Marshal.GetLastWin32Error());
        }
    }

    // Preshutdown timeout (effective at once, unlike a registry write) and first place in PreshutdownOrder,
    // re-asserted at every start (a Windows feature update may rewrite the order)
    static void Configure()
    {
        IntPtr scm = OpenSCManager(null, null, SC_MANAGER_CONNECT);
        if (scm == IntPtr.Zero) Log("OpenSCManager failed: error " + Marshal.GetLastWin32Error());
        else
        {
            IntPtr svc = OpenService(scm, ServiceName, SERVICE_CHANGE_CONFIG);
            if (svc == IntPtr.Zero) Log("OpenService failed: error " + Marshal.GetLastWin32Error());
            else
            {
                SERVICE_PRESHUTDOWN_INFO info = new SERVICE_PRESHUTDOWN_INFO();
                info.dwPreshutdownTimeout = PreshutdownTimeoutMs;
                if (!ChangeServiceConfig2(svc, SERVICE_CONFIG_PRESHUTDOWN_INFO, ref info))
                    Log("could not set the preshutdown timeout: error " + Marshal.GetLastWin32Error());
                CloseServiceHandle(svc);
            }
            CloseServiceHandle(scm);
        }
        try
        {
            using (RegistryKey k = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Control", true))
            {
                string[] order = k.GetValue("PreshutdownOrder") as string[];
                if (order == null) order = new string[0];
                if (order.Length == 0 || !string.Equals(order[0], ServiceName, StringComparison.OrdinalIgnoreCase))
                {
                    List<string> list = new List<string>();
                    list.Add(ServiceName);
                    foreach (string s in order)
                        if (!string.IsNullOrEmpty(s) && !string.Equals(s, ServiceName, StringComparison.OrdinalIgnoreCase))
                            list.Add(s);
                    k.SetValue("PreshutdownOrder", list.ToArray(), RegistryValueKind.MultiString);
                    Log("PreshutdownOrder: " + string.Join(", ", list.ToArray()));
                }
            }
        }
        catch (Exception e) { Log("could not update PreshutdownOrder: " + Inner(e)); }
    }

    static bool StopWsl(bool asService)
    {
        Stopwatch sw = Stopwatch.StartNew();
        try
        {
            using (ServiceController sc = new ServiceController(WslServiceName))
            {
                ServiceControllerStatus st = sc.Status;   // throws if WSL is not installed
                if (st == ServiceControllerStatus.Stopped)
                {
                    Log(WslServiceName + " is not running: nothing to stop");
                    return true;
                }
                Log("stopping " + WslServiceName + " (" + st + "): WSL powers its distros off cleanly");
                // A service that is still starting refuses STOP (error 1061): ask again every 2 s until it accepts
                long lastStop = -2000;
                bool stopLogged = false;
                long lastReport = 0;
                while (true)
                {
                    if (st != ServiceControllerStatus.StopPending && sw.ElapsedMilliseconds - lastStop >= 2000)
                    {
                        lastStop = sw.ElapsedMilliseconds;
                        try { sc.Stop(); }
                        catch (InvalidOperationException e)
                        {
                            if (!stopLogged) Log("stop request refused (" + st + "), asking again every 2 s: " + Inner(e));
                            stopLogged = true;
                        }
                    }
                    if (sw.ElapsedMilliseconds >= WslStopWaitMs) break;
                    Thread.Sleep(500);
                    sc.Refresh();
                    st = sc.Status;
                    if (st == ServiceControllerStatus.Stopped)
                    {
                        Log(WslServiceName + " stopped after " + sw.ElapsedMilliseconds + " ms");
                        return true;
                    }
                    if (asService && sw.ElapsedMilliseconds - lastReport >= 2000)
                    {
                        Report(SERVICE_STOP_PENDING, 0, 5000);
                        lastReport = sw.ElapsedMilliseconds;
                    }
                }
                Log(WslServiceName + " still " + sc.Status + " after " + sw.ElapsedMilliseconds +
                    " ms: giving up (Windows will stop the VM hard)");
                return false;
            }
        }
        catch (Exception e)
        {
            Log("error stopping " + WslServiceName + ": " + Inner(e));
            return false;
        }
    }

    static string Inner(Exception e)
    {
        while (e.InnerException != null) e = e.InnerException;
        return e.Message;
    }

    static void Log(string message)
    {
        string line = DateTime.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", CultureInfo.InvariantCulture) + " " + message;
        if (toConsole) Console.WriteLine(line);
        lock (LogSync)
        {
            try
            {
                string dir = Path.GetDirectoryName(Path.GetFullPath(logPath));
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                FileInfo fi = new FileInfo(logPath);
                if (fi.Exists && fi.Length > 1048576)
                {
                    string old = logPath + ".1";
                    if (File.Exists(old)) File.Delete(old);
                    File.Move(logPath, old);
                }
                using (FileStream fs = new FileStream(logPath, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
                using (StreamWriter w = new StreamWriter(fs))
                {
                    w.WriteLine(line);
                    w.Flush();
                    fs.Flush(true);
                }
            }
            catch (Exception) { }
        }
    }
}
'@

# ============================================================================ pure helpers (tools/test-wsl2-host)

# One argument quoted for a Windows command line (CommandLineToArgvW rules)
# Get-HostDriveLetter PATH: the drive letter of a Windows path (C:\Mu\wsl\x, \\?\D:\x), upper case; '' for a UNC path
function Get-HostDriveLetter([string]$Path) {
  if ($Path -match '^(?:\\\\\?\\)?([A-Za-z]):') { return $Matches[1].ToUpperInvariant() }
  return ''
}

function ConvertTo-WinArgument([string]$Arg) {
  if ($Arg.Length -gt 0 -and $Arg -notmatch '[\s"]') { return $Arg }
  $sb = New-Object System.Text.StringBuilder
  [void]$sb.Append('"')
  $bs = 0
  foreach ($c in $Arg.ToCharArray()) {
    if ($c -eq [char]'\') { $bs++; continue }
    if ($c -eq [char]'"') { [void]$sb.Append('\' * (2 * $bs + 1)); [void]$sb.Append('"'); $bs = 0; continue }
    if ($bs -gt 0) { [void]$sb.Append('\' * $bs); $bs = 0 }
    [void]$sb.Append($c)
  }
  [void]$sb.Append('\' * (2 * $bs))
  [void]$sb.Append('"')
  return $sb.ToString()
}

# INI files (.wslconfig): a List[string] of lines, edited in place, everything else kept
function New-IniLines([string]$Text) {
  $list = New-Object System.Collections.Generic.List[string]
  if ([string]::IsNullOrEmpty($Text)) { return ,$list }
  $t = $Text -replace "`r`n", "`n"
  if ($t.Length -gt 0 -and $t[0] -eq [char]0xFEFF) { $t = $t.Substring(1) }   # (not StartsWith: culture rules ignore U+FEFF)
  if ($t.EndsWith("`n", [StringComparison]::Ordinal)) { $t = $t.Substring(0, $t.Length - 1) }
  foreach ($l in ($t -split "`n")) { $list.Add($l) }
  return ,$list
}
function ConvertTo-IniText([System.Collections.Generic.List[string]]$Lines) {
  if ($Lines.Count -eq 0) { return '' }
  return (($Lines.ToArray()) -join "`r`n") + "`r`n"
}
function Get-NormalizedIniValue([string]$Value) { return ($Value -replace '\s', '').ToLowerInvariant() }
# Get-IniValue: the value of Key in [Section] (names compare case-insensitively), or $null. A repeated key gives its
# first value: WSL uses the first one and warns about the others (configfile.cpp, ConfigKey::Parse)
function Get-IniValue([System.Collections.Generic.List[string]]$Lines, [string]$Section, [string]$Key) {
  $cur = $null
  foreach ($l in $Lines) {
    if ($l -match '^\s*\[([^\]]*)\]\s*$') { $cur = $Matches[1].Trim(); continue }
    if ($cur -ne $Section -or $l -match '^\s*[#;]') { continue }
    if ($l -match '^\s*([^=]+?)\s*=\s*(.*?)\s*$' -and $Matches[1] -eq $Key) { return $Matches[2] }
  }
  return $null
}
# Set-IniValue: sets Key=Value in [Section]; a key already holding this value (ignoring case and spaces) is left as
# written. A missing key goes after the last non-blank line of the section's first occurrence; a missing section is
# appended. Returns an object with Changed and Old (the previous value, or $null when the key was absent).
function Set-IniValue([System.Collections.Generic.List[string]]$Lines, [string]$Section, [string]$Key, [string]$Value) {
  $cur = $null; $firstEnd = -1; $inFirst = $false; $old = $null; $found = $false; $changed = $false
  for ($i = 0; $i -lt $Lines.Count; $i++) {
    $l = $Lines[$i]
    if ($l -match '^\s*\[([^\]]*)\]\s*$') {
      $cur = $Matches[1].Trim(); $inFirst = $false
      if ($cur -eq $Section -and $firstEnd -lt 0) { $firstEnd = $i; $inFirst = $true }
      continue
    }
    if ($cur -ne $Section -or $l -match '^\s*$') { continue }
    if ($inFirst) { $firstEnd = $i }
    if ($l -match '^\s*[#;]') { continue }
    if ($l -match '^\s*([^=]+?)\s*=\s*(.*?)\s*$' -and $Matches[1] -eq $Key) {
      if (-not $found) { $old = $Matches[2] }
      $found = $true
      if ((Get-NormalizedIniValue $Matches[2]) -ne (Get-NormalizedIniValue $Value)) { $Lines[$i] = "$Key=$Value"; $changed = $true }
    }
  }
  if (-not $found) {
    $changed = $true
    if ($firstEnd -ge 0) { $Lines.Insert($firstEnd + 1, "$Key=$Value") }
    else {
      if ($Lines.Count -gt 0 -and $Lines[$Lines.Count - 1].Trim() -ne '') { $Lines.Add('') }
      $Lines.Add("[$Section]"); $Lines.Add("$Key=$Value")
    }
  }
  return New-Object PSObject -Property @{ Changed = $changed; Old = $old }
}
# Remove-IniValue: removes Key from [Section]; a section left empty loses its header (and the blank line before it)
function Remove-IniValue([System.Collections.Generic.List[string]]$Lines, [string]$Section, [string]$Key) {
  $cur = $null; $drop = New-Object System.Collections.Generic.List[int]
  for ($i = 0; $i -lt $Lines.Count; $i++) {
    $l = $Lines[$i]
    if ($l -match '^\s*\[([^\]]*)\]\s*$') { $cur = $Matches[1].Trim(); continue }
    if ($cur -ne $Section -or $l -match '^\s*[#;]') { continue }
    if ($l -match '^\s*([^=]+?)\s*=\s*(.*?)\s*$' -and $Matches[1] -eq $Key) { $drop.Add($i) }
  }
  for ($j = $drop.Count - 1; $j -ge 0; $j--) { $Lines.RemoveAt($drop[$j]) }
  # empty sections of that name
  for ($i = $Lines.Count - 1; $i -ge 0; $i--) {
    if ($Lines[$i] -match '^\s*\[([^\]]*)\]\s*$' -and $Matches[1].Trim() -eq $Section) {
      $k = $i + 1; $empty = $true
      while ($k -lt $Lines.Count -and $Lines[$k] -notmatch '^\s*\[[^\]]*\]\s*$') { if ($Lines[$k].Trim() -ne '') { $empty = $false }; $k++ }
      if ($empty) {
        $n = $k - $i
        $Lines.RemoveRange($i, $n)
        if ($i -gt 0 -and $Lines[$i - 1].Trim() -eq '') { $Lines.RemoveAt($i - 1) }
      }
    }
  }
  return ($drop.Count -gt 0)
}

# The startup task, as Task Scheduler XML: at startup (+30 s), repeated every 5 minutes indefinitely, as the user
# with S4U (no stored password) and highest privileges; 5-minute limit; a run is skipped while one is running
function Get-TaskXml([string]$UserSid, [string]$WslExe, [string]$Distro) {
  $u = [System.Security.SecurityElement]::Escape($UserSid)
  $x = [System.Security.SecurityElement]::Escape($WslExe)
  $d = [System.Security.SecurityElement]::Escape($Distro)
  return @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>mu</Author>
    <Description>mu WSL2 host adapter (mu-wsl2-host.ps1): starts the WSL distro $d at startup with nobody signed in, and again every 5 minutes (a WSL update stops it). .wslconfig instanceIdleTimeout=-1 keeps it running.</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Repetition>
        <Interval>PT5M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
    </BootTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$u</UserId>
      <LogonType>S4U</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$x</Command>
      <Arguments>-d $d --exec /bin/true</Arguments>
    </Exec>
  </Actions>
</Task>
"@
}

function Resolve-Sid([string]$Name) {
  if ($Name -match '^S-1-') { return $Name }
  try { return (New-Object System.Security.Principal.NTAccount($Name)).Translate([System.Security.Principal.SecurityIdentifier]).Value }
  catch { return $Name }
}

# Compare-StartupTask: what differs between a registered task (Get-ScheduledTask) and the one Get-TaskXml describes
function Compare-StartupTask($Task, [string]$UserSid, [string]$WslExe, [string]$Distro) {
  $d = New-Object System.Collections.ArrayList
  $a = @($Task.Actions)
  if ($a.Count -ne 1) { [void]$d.Add("$($a.Count) actions") }
  else {
    if ([string]$a[0].Execute.Trim('"') -ne $WslExe) { [void]$d.Add("runs $($a[0].Execute)") }
    if ([string]$a[0].Arguments -ne "-d $Distro --exec /bin/true") { [void]$d.Add("arguments '$($a[0].Arguments)'") }
  }
  $t = @($Task.Triggers)
  if ($t.Count -ne 1 -or $t[0].CimClass.CimClassName -ne 'MSFT_TaskBootTrigger') { [void]$d.Add('not a single at-startup trigger') }
  else {
    if ($t[0].Delay -ne 'PT30S') { [void]$d.Add("startup delay '$($t[0].Delay)'") }
    if ($null -eq $t[0].Repetition -or $t[0].Repetition.Interval -ne 'PT5M') { [void]$d.Add('does not repeat every 5 minutes') }
    elseif ($t[0].Repetition.Duration) { [void]$d.Add("repeats only for $($t[0].Repetition.Duration)") }
    if (-not $t[0].Enabled) { [void]$d.Add('trigger disabled') }
  }
  $p = $Task.Principal
  if ([string]$p.LogonType -ne 'S4U') { [void]$d.Add("logon type $($p.LogonType)") }
  if ([string]$p.RunLevel -ne 'Highest') { [void]$d.Add("run level $($p.RunLevel)") }
  if ((Resolve-Sid ([string]$p.UserId)) -ne $UserSid) { [void]$d.Add("runs as $($p.UserId)") }
  $s = $Task.Settings
  if ($s.ExecutionTimeLimit -ne 'PT5M') { [void]$d.Add("time limit '$($s.ExecutionTimeLimit)'") }
  if ([string]$s.MultipleInstances -ne 'IgnoreNew') { [void]$d.Add("multiple instances: $($s.MultipleInstances)") }
  if (-not $s.Enabled) { [void]$d.Add('task disabled') }
  if ($s.DisallowStartIfOnBatteries -or $s.StopIfGoingOnBatteries) { [void]$d.Add('battery conditions') }
  return $d.ToArray()
}

# powercfg /query ... prints the minimum, maximum, increment, then the AC and DC values as hex: the AC one is second to
# last (the labels are translated, the hex values are not)
function Get-PowercfgAcValue([string[]]$Lines) {
  $hex = @($Lines | ForEach-Object { if ($_ -match '0x([0-9a-fA-F]{8})\s*$') { $Matches[1] } })
  if ($hex.Count -lt 2) { return $null }
  return [Convert]::ToInt32($hex[$hex.Count - 2], 16)
}

# Failure actions in the registry (FailureActions): reset period, then (type, delay) pairs after a 20-byte header.
# Wanted: reset after 1 day; restart after 10 s, 10 s, 60 s.
function Test-FailureActionsBytes([byte[]]$Bytes) {
  if ($null -eq $Bytes -or $Bytes.Length -lt 44) { return $false }
  if ([BitConverter]::ToUInt32($Bytes, 0) -ne 86400 -or [BitConverter]::ToUInt32($Bytes, 12) -ne 3) { return $false }
  $want = @(10000, 10000, 60000)
  for ($i = 0; $i -lt 3; $i++) {
    if ([BitConverter]::ToUInt32($Bytes, 20 + 8 * $i) -ne 1 -or [BitConverter]::ToUInt32($Bytes, 24 + 8 * $i) -ne $want[$i]) { return $false }
  }
  return $true
}

# Merge-PreshutdownOrder: Name first, then the others in their order (no duplicates, no empty entries)
function Merge-PreshutdownOrder([string[]]$Order, [string]$Name) {
  $out = New-Object System.Collections.Generic.List[string]
  $out.Add($Name)
  foreach ($s in @($Order)) { if ($s -and $s -ne $Name) { $out.Add($s) } }
  return ,$out.ToArray()
}
function Remove-FromPreshutdownOrder([string[]]$Order, [string]$Name) {
  $out = New-Object System.Collections.Generic.List[string]
  foreach ($s in @($Order)) { if ($s -and $s -ne $Name) { $out.Add($s) } }
  return ,$out.ToArray()
}

# WSL memory default: 75% of this PC's memory, in whole GB (at least 2)
function Get-DefaultMemoryGB([double]$TotalBytes) {
  $gb = [int][math]::Floor($TotalBytes / 1GB * 0.75)
  if ($gb -lt 2) { $gb = 2 }
  return $gb
}

# A mu-wsl-shutdown.log line's UTC time, or $null
function Get-LogTime([string]$Line) {
  if ($Line -notmatch '^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z) ') { return $null }
  return [datetime]::ParseExact($Matches[1], "yyyy-MM-dd'T'HH:mm:ss.fff'Z'", [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal)
}

# What the hook did at the last shutdown before BootUtc, from its log: @(result, detail)
function Get-LastShutdownResult([string[]]$Lines, [datetime]$BootUtc) {
  $before = @($Lines | Where-Object { $t = Get-LogTime $_; $null -ne $t -and $t -lt $BootUtc })
  if ($before.Count -eq 0) { return @('info', 'no shutdown logged since the hook was installed') }
  $last = $before[$before.Count - 1]
  $pre = @($before | Where-Object { $_ -match ' preshutdown: ' })
  if ($last -match ' stopped after (\d+) ms' -or $last -match ' is not running: nothing to stop') {
    if ($pre.Count -gt 0 -and (Get-LogTime $pre[$pre.Count - 1]) -le (Get-LogTime $last)) {
      return @('pass', "last shutdown: WSL stopped cleanly ($($last.Substring(25)))")
    }
  }
  if ($last -match ' giving up| error ') { return @('fail', "last shutdown: $($last.Substring(25))") }
  if ($last -match ' stop: ') { return @('warn', 'the hook was stopped by hand or by a reinstall before the last shutdown') }
  return @('warn', "last shutdown: the hook logged no preshutdown (last entry: $($last.Substring(25)))")
}

function ConvertTo-UtcDate($Value) {
  if ($Value -is [datetime]) { return $Value.ToUniversalTime() }
  return [datetime]::Parse([string]$Value, [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal)
}
function Format-Utc($Time) {
  if ($null -eq $Time) { return '?' }
  return ([datetime]$Time).ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
}

# ============================================================================ output
function Write-Step([string]$Text) { if (-not $Json) { Write-Host ''; Write-Host "== $Text" } }
function Write-Changed([string]$Text) { [void]$script:Changes.Add($Text); if (-not $Json) { Write-Host "  changed  $Text" -ForegroundColor Cyan } }
function Write-Same([string]$Text) { if (-not $Json) { Write-Host "  ok       $Text" } }
function Write-Note([string]$Text) { if (-not $Json) { Write-Host "  warning  $Text" -ForegroundColor Yellow } }
function Add-Check([string]$Result, [string]$Name, [string]$Detail) {
  [void]$script:Checks.Add([ordered]@{ result = $Result; name = $Name; detail = $Detail })
}

# ============================================================================ processes
# Invoke-Native EXE ARGS: runs a Windows program, returns ExitCode and Output (stdout and stderr lines)
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
  $ErrorActionPreference = 'Continue'   # Windows PowerShell turns a native program's stderr into errors
  $out = & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
  return New-Object PSObject -Property @{ ExitCode = $LASTEXITCODE; Output = @($out) }
}

# Invoke-Wsl ARGS [-InputBytes BYTES] [-Capture] [-Exe PATH]: runs wsl.exe with an exact command line (UTF-8 output).
# Without -Capture its output goes straight to this console. Returns ExitCode, Output, Error.
function Invoke-Wsl([string[]]$Arguments, [byte[]]$InputBytes = $null, [switch]$Capture, [string]$Exe = '') {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $script:WslExe
  if ($Exe) { $psi.FileName = $Exe }
  $psi.Arguments = (@($Arguments | ForEach-Object { ConvertTo-WinArgument $_ }) -join ' ')
  $psi.UseShellExecute = $false
  $psi.WorkingDirectory = $env:SystemRoot
  $psi.EnvironmentVariables['WSL_UTF8'] = '1'
  if ($null -ne $InputBytes) { $psi.RedirectStandardInput = $true }
  if ($Capture) {
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = $script:Utf8; $psi.StandardErrorEncoding = $script:Utf8
  }
  $p = [System.Diagnostics.Process]::Start($psi)
  $errTask = $null; $outTask = $null
  if ($Capture) { $outTask = $p.StandardOutput.ReadToEndAsync(); $errTask = $p.StandardError.ReadToEndAsync() }
  if ($null -ne $InputBytes) {
    $p.StandardInput.BaseStream.Write($InputBytes, 0, $InputBytes.Length)
    $p.StandardInput.BaseStream.Flush()
    $p.StandardInput.Close()
  }
  $p.WaitForExit()
  $out = ''; $err = ''
  if ($Capture) { $out = $outTask.Result -replace "`0", '' -replace "`r", ''; $err = $errTask.Result -replace "`0", '' -replace "`r", '' }
  return New-Object PSObject -Property @{ ExitCode = $p.ExitCode; Output = $out; Error = $err.Trim() }
}

function Get-WslVersion {
  try {
    $v = (Get-Item $script:WslExe).VersionInfo.ProductVersion -replace '[^0-9.].*$', ''
    if ($v -match '^\d+\.\d+') { return [version]$v }
  } catch { }
  $r = Invoke-Wsl -Arguments @('--version') -Capture
  if ($r.Output -match '(\d+\.\d+\.\d+(\.\d+)?)') { return [version]$Matches[1] }
  return [version]'0.0'
}

# Get-WslDistro NAME: the registration of this user's distro (HKCU Lxss), or $null
function Get-WslDistro([string]$Name) {
  $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
  if (-not (Test-Path $root)) { return $null }
  foreach ($k in @(Get-ChildItem $root)) {
    $p = Get-ItemProperty $k.PSPath
    if ($p.PSObject.Properties['DistributionName'] -and [string]$p.DistributionName -eq $Name) {
      $base = ''
      if ($p.PSObject.Properties['BasePath']) { $base = [string]$p.BasePath }
      if ($base.StartsWith('\\?\', [StringComparison]::Ordinal)) { $base = $base.Substring(4) }
      return New-Object PSObject -Property @{ Name = [string]$p.DistributionName; BasePath = $base; Key = $k.PSPath }
    }
  }
  return $null
}

function Test-DistroRunning {
  $r = Invoke-Wsl -Arguments @('--list', '--running', '--quiet') -Capture
  foreach ($l in ($r.Output -split "`n")) { if ($l.Trim() -eq $Distro) { return $true } }
  return $false
}

# WSL keeps one session per Windows user, created by the first wsl.exe call when none exists, and tied to the Windows
# session of that caller: signing out of that session ends it, and the distro with it. Only a session created from
# session 0 (the startup task) lasts until WSLService stops. So nothing here may call wsl.exe before the task has.
# Get-WslSessionSource SERVICESTATE SERVICESTARTUTC TASKLASTRUNUTC TASKRESULT:
#   'none'     WSLService is stopped: no distro runs (a wsl.exe call would start WSL tied to this sign-in)
#   'task'     the startup task ran successfully since WSLService started: the session exists and comes from session
#              0, and a wsl.exe call reuses it
#   'unknown'  anything else: a wsl.exe call could create the session from here
function Get-WslSessionSource([string]$ServiceState, $ServiceStartUtc, $TaskLastRunUtc, [int64]$TaskResult) {
  if ($ServiceState -eq '' -or $ServiceState -eq 'Stopped') { return 'none' }
  if ($ServiceState -ne 'Running' -or $null -eq $ServiceStartUtc -or $null -eq $TaskLastRunUtc -or $TaskResult -ne 0) { return 'unknown' }
  # the task's own wsl.exe may have started the service, a moment after its run began
  if (([datetime]$TaskLastRunUtc) -ge ([datetime]$ServiceStartUtc).AddSeconds(-10)) { return 'task' }
  return 'unknown'
}
function Get-WslSessionState {
  $state = ''; $start = $null; $last = $null; $res = -1
  $svc = Get-CimInstance Win32_Service -Filter "Name='WSLService'" -ErrorAction SilentlyContinue
  if ($svc) {
    $state = [string]$svc.State
    if ($svc.ProcessId) {
      # Win32_Process gives every process's start time, also to a user who is not elevated
      $p = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$svc.ProcessId)" -ErrorAction SilentlyContinue
      if ($p -and $p.CreationDate) { $start = ([datetime]$p.CreationDate).ToUniversalTime() }
    }
  }
  try {
    $info = Get-ScheduledTaskInfo -TaskPath $script:TaskPath -TaskName $script:TaskName -ErrorAction Stop
    if ($info.LastRunTime) { $last = ([datetime]$info.LastRunTime).ToUniversalTime() }
    $res = [int64]$info.LastTaskResult
  } catch { }
  return (Get-WslSessionSource $state $start $last $res)
}

# Start the distro through the startup task, so that its WSL session comes from session 0, then wait until it runs.
# Nothing asks WSL anything from here until the task's run has finished.
function Start-DistroViaTask([int]$TimeoutSeconds = 120) {
  if (-not (Get-ScheduledTask -TaskPath $script:TaskPath -TaskName $script:TaskName -ErrorAction SilentlyContinue)) { return $false }
  $busy = ([string](Get-ScheduledTask -TaskPath $script:TaskPath -TaskName $script:TaskName).State -eq 'Running')   # that run counts
  $t0 = (Get-Date).AddSeconds(-2)
  Start-ScheduledTask -TaskPath $script:TaskPath -TaskName $script:TaskName
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  do {
    Start-Sleep -Seconds 2
    $info = Get-ScheduledTaskInfo -TaskPath $script:TaskPath -TaskName $script:TaskName
    $state = [string](Get-ScheduledTask -TaskPath $script:TaskPath -TaskName $script:TaskName).State
    # 0x41301 (SCHED_S_TASK_RUNNING) and 0x41325 (SCHED_S_TASK_QUEUED) are what LastTaskResult reads while a run has
    # not finished; State can already say Ready for a moment after Start-ScheduledTask (seen on the office PC)
    $res = [int64]$info.LastTaskResult
    $done = ($state -ne 'Running') -and ($busy -or $info.LastRunTime -ge $t0) -and ($res -ne 0x41301) -and ($res -ne 0x41325)
  } while (-not $done -and (Get-Date) -lt $deadline)
  if (-not $done) { Write-Note "the startup task's run did not finish within $TimeoutSeconds s"; return $false }
  if ([int64]$info.LastTaskResult -ne 0) { Write-Note ("the startup task's run ended with result 0x{0:X}" -f [int64]$info.LastTaskResult); return $false }
  while ($true) {
    if (Test-DistroRunning) { return $true }
    if ((Get-Date) -ge $deadline) { return $false }
    Start-Sleep -Seconds 3
  }
}

# ============================================================================ state (what to restore on uninstall)
function Get-StatePath { return (Join-Path (Join-Path $MuRoot 'state') 'wsl2-host.json') }
function Read-State {
  $s = [ordered]@{ version = 1; installed = $null; settings = [ordered]@{}; originals = [ordered]@{} }
  $path = Get-StatePath
  if (Test-Path $path) {
    $o = [IO.File]::ReadAllText($path) | ConvertFrom-Json
    if ($o.PSObject.Properties['installed']) { $s.installed = $o.installed }
    if ($o.PSObject.Properties['settings'] -and $o.settings) { foreach ($p in $o.settings.PSObject.Properties) { $s.settings[$p.Name] = $p.Value } }
    if ($o.PSObject.Properties['originals'] -and $o.originals) {
      foreach ($p in $o.originals.PSObject.Properties) { $s.originals[$p.Name] = [ordered]@{ present = [bool]$p.Value.present; value = $p.Value.value } }
    }
  }
  return $s
}
function Save-State {
  $path = Get-StatePath
  $dir = Split-Path $path -Parent
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  [IO.File]::WriteAllText($path, ($script:State | ConvertTo-Json -Depth 6), $script:Utf8)
}
# Save-Original NAME PRESENT VALUE: records a setting's value before this script first changes it (kept on reruns)
function Save-Original([string]$Name, [bool]$Present, $Value) {
  if ($script:State.originals.Contains($Name)) { return }
  $script:State.originals[$Name] = [ordered]@{ present = $Present; value = $Value }
  Save-State
}

# ============================================================================ Windows settings
function Get-RegDword([string]$Path, [string]$Name) {
  if (-not (Test-Path $Path)) { return $null }
  $p = Get-ItemProperty -Path $Path -Name $Name -ErrorAction SilentlyContinue
  if ($null -eq $p) { return $null }
  return [int]$p.$Name
}
function Set-RegDword([string]$Path, [string]$Name, [int]$Value, [string]$Why) {
  $cur = Get-RegDword $Path $Name
  if ($null -ne $cur -and $cur -eq $Value) { Write-Same "$Why ($Name=$Value)"; return }
  Save-Original "reg|$Path|$Name" ($null -ne $cur) $cur
  if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
  New-ItemProperty -Path $Path -Name $Name -PropertyType DWord -Value $Value -Force | Out-Null
  $was = 'absent'; if ($null -ne $cur) { $was = "$cur" }
  Write-Changed "$Why ($Name=$Value, was $was)"
}

function Get-ActiveSchemeGuid {
  $r = Invoke-Native 'powercfg.exe' @('/getactivescheme')
  if (($r.Output -join ' ') -match '([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})') { return $Matches[1] }
  return $null
}
function Get-PowerAcSeconds([string]$Setting, [string]$Scheme = 'SCHEME_CURRENT') {
  $r = Invoke-Native 'powercfg.exe' @('/query', $Scheme, 'SUB_SLEEP', $Setting)
  if ($r.ExitCode -ne 0) { return $null }
  return Get-PowercfgAcValue $r.Output
}
function Set-PowerAcSeconds([string]$Setting, [int]$Seconds, [string]$Why) {
  $cur = Get-PowerAcSeconds $Setting
  if ($null -ne $cur -and $cur -eq $Seconds) { Write-Same $Why; return }
  $scheme = Get-ActiveSchemeGuid
  Save-Original "power|$Setting" ($null -ne $cur) ([ordered]@{ scheme = $scheme; seconds = $cur })
  $r = Invoke-Native 'powercfg.exe' @('/setacvalueindex', 'SCHEME_CURRENT', 'SUB_SLEEP', $Setting, "$Seconds")
  if ($r.ExitCode -ne 0) { throw "powercfg could not set $Setting`: $($r.Output -join ' ')" }
  [void](Invoke-Native 'powercfg.exe' @('/setactive', 'SCHEME_CURRENT'))
  Write-Changed "$Why (was $cur s)"
}

$script:PowerKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'
$script:PolicyKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
function Set-PowerSettings {
  Set-PowerAcSeconds 'STANDBYIDLE' 0 'sleep on AC power: never (a sleeping PC pauses the VM)'
  Set-PowerAcSeconds 'HIBERNATEIDLE' 0 'hibernate on AC power: never'
  Set-RegDword $script:PowerKey 'HiberbootEnabled' 0 'Fast Startup off (shut down = a real shutdown; startup tasks run after power-on)'
  Set-RegDword $script:PolicyKey 'DisableAutomaticRestartSignOn' 1 'automatic sign-in after updates off (no interactive session may own the WSL session)'
}

# ============================================================================ folders (the service exe runs as SYSTEM)
# The drive root's default ACL lets every signed-in user create folders in it and modify what they contain, and a
# folder's creator owns it (and so can change its ACL). <MuRoot> and its folders therefore inherit nothing: SYSTEM and
# Administrators have full control, Users may read (a LocalSystem service runs the exe in <MuRoot>\bin). <MuRoot>\wsl,
# which holds the distro's virtual disk (the deploy key, every job file), is not readable by other users at all: only
# SYSTEM, Administrators and the distro's owner, who needs full control for WSL to grant its VM access to the disk.
# Each folder's owner must be SYSTEM or Administrators; one created by another user is refused, not taken over.
$script:SidSystem = 'S-1-5-18'
$script:SidAdmins = 'S-1-5-32-544'
$script:SidUsers = 'S-1-5-32-545'
$script:WriteMask = [int]([System.Security.AccessControl.FileSystemRights]'Write, Delete, DeleteSubdirectoriesAndFiles, ChangePermissions, TakeOwnership')

# The managed folders: Path, Name (for status), Full (SIDs with full control), Read (SIDs that may only read),
# Recurse (reset the ACLs of everything inside too; never for <MuRoot> itself or wsl, where WSL's own entries on the
# disk file must stay), Why
function Get-MuDirSpecs {
  $admin = @($script:SidSystem, $script:SidAdmins)
  $specs = New-Object System.Collections.ArrayList
  [void]$specs.Add((New-Object PSObject -Property @{ Path = $MuRoot; Name = 'root'; Full = $admin; Read = @($script:SidUsers); Recurse = $false; Why = 'only administrators and SYSTEM can write' }))
  foreach ($sub in @('bin', 'host', 'state', 'logs')) {
    [void]$specs.Add((New-Object PSObject -Property @{ Path = (Join-Path $MuRoot $sub); Name = $sub; Full = $admin; Read = @($script:SidUsers); Recurse = $true; Why = 'only administrators and SYSTEM can write' }))
  }
  [void]$specs.Add((New-Object PSObject -Property @{ Path = (Join-Path $MuRoot 'wsl'); Name = 'wsl'; Full = ($admin + @($script:Me.User.Value)); Read = @(); Recurse = $false
      Why = "only $($script:Me.Name), administrators and SYSTEM can open it (the distro's disk)" }))
  return ,$specs.ToArray()
}

function Get-SidName([string]$Sid) {
  try { return (New-Object System.Security.Principal.SecurityIdentifier($Sid)).Translate([System.Security.Principal.NTAccount]).Value } catch { return $Sid }
}
function Get-DirOwner([string]$Dir) { return (Get-Acl -LiteralPath $Dir).GetOwner([System.Security.Principal.SecurityIdentifier]).Value }

# Test-DirAcl: the folder inherits nothing, is owned by SYSTEM or Administrators, every SID in Full has an entry, SIDs
# in Read have no write rights, and nobody else has an entry
function Test-DirAcl($Spec) {
  $acl = Get-Acl -LiteralPath $Spec.Path
  if (-not $acl.AreAccessRulesProtected) { return $false }
  $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
  if ($owner -ne $script:SidSystem -and $owner -ne $script:SidAdmins) { return $false }
  $seen = @{}
  foreach ($r in @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))) {
    $sid = $r.IdentityReference.Value
    if ([string]$r.AccessControlType -ne 'Allow') { return $false }
    if ($Spec.Full -contains $sid) { $seen[$sid] = $true }
    elseif ($Spec.Read -contains $sid) { if (([int]$r.FileSystemRights -band $script:WriteMask) -ne 0) { return $false } }
    else { return $false }
  }
  foreach ($sid in $Spec.Full) { if (-not $seen.ContainsKey($sid)) { return $false } }
  return $true
}
function Set-DirAcl($Spec) {
  # /reset drops explicit entries; /inheritance:r the inherited ones, then the grants (by SID: any language). Without
  # /T, the entries the files inside inherit are recomputed and their own explicit entries are kept.
  $t = @(); if ($Spec.Recurse) { $t = @('/T') }
  $grants = @($Spec.Full | ForEach-Object { "*$($_):(OI)(CI)F" }) + @($Spec.Read | ForEach-Object { "*$($_):(OI)(CI)RX" })
  $r = Invoke-Native 'icacls.exe' (@($Spec.Path, '/reset') + $t + @('/Q'))
  if ($r.ExitCode -eq 0) { $r = Invoke-Native 'icacls.exe' (@($Spec.Path, '/inheritance:r', '/grant:r') + $grants + @('/Q')) }
  if ($r.ExitCode -eq 0) { $r = Invoke-Native 'icacls.exe' (@($Spec.Path, '/setowner', "*$($script:SidAdmins)") + $t + @('/Q')) }
  if ($r.ExitCode -ne 0) { throw "icacls $($Spec.Path) failed: $($r.Output -join ' ')" }
}
# Assert-DirOwner: a folder owned by someone other than SYSTEM, Administrators or the user running this was made by
# another user, who may have put files in it: refuse
function Assert-DirOwner([string]$Dir) {
  $owner = Get-DirOwner $Dir
  if (@($script:SidSystem, $script:SidAdmins, $script:Me.User.Value) -contains $owner) { return }
  throw "$Dir belongs to $(Get-SidName $owner): another user created it and may have put files in it. Check it and delete it (or use -MuRoot for another folder), then run install again"
}
function Initialize-MuDirs {
  foreach ($spec in (Get-MuDirSpecs)) {
    if (-not (Test-Path -LiteralPath $spec.Path)) { New-Item -ItemType Directory -Force -Path $spec.Path | Out-Null }
    Assert-DirOwner $spec.Path
    if (-not (Test-DirAcl $spec)) { Set-DirAcl $spec; Write-Changed "$($spec.Path) ($($spec.Why))" }
  }
}
# Get-DiskAclProblem PATH USERSID: who else may read the distro's virtual disk, or $null when only SYSTEM,
# Administrators, the user and Hyper-V's per-VM accounts (NT VIRTUAL MACHINE, S-1-5-83-...) can
function Get-DiskAclProblem([string]$Path, [string]$UserSid) {
  $others = New-Object System.Collections.ArrayList
  $readMask = [int][System.Security.AccessControl.FileSystemRights]'ReadData, Write, Delete'
  foreach ($r in @((Get-Acl -LiteralPath $Path).GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))) {
    $sid = $r.IdentityReference.Value
    if ([string]$r.AccessControlType -ne 'Allow' -or ([int]$r.FileSystemRights -band $readMask) -eq 0) { continue }
    # S-1-5-83-*: Hyper-V VM identities (HcsGrantVmAccess). S-1-15-3-*: capability SIDs, which only count for sandboxed
    # (AppContainer) processes and only on top of a normal user or group grant, so they let no other user read the disk
    if (@($script:SidSystem, $script:SidAdmins, $UserSid) -contains $sid -or $sid.StartsWith('S-1-5-83-', [StringComparison]::Ordinal) -or
        $sid.StartsWith('S-1-15-3-', [StringComparison]::Ordinal)) { continue }
    $n = Get-SidName $sid
    if (-not $others.Contains($n)) { [void]$others.Add($n) }
  }
  if ($others.Count -eq 0) { return $null }
  return ($others.ToArray() -join ', ')
}

# a copy of both scripts in <MuRoot>\host, to run status and uninstall from later
function Copy-HostScripts {
  $dst = Join-Path $MuRoot 'host'
  foreach ($f in @($script:SelfPath, $script:DistroScriptPath, $script:GatePath)) {
    $target = Join-Path $dst (Split-Path $f -Leaf)
    if ((Test-Path $target) -and ((Get-FileHash $target).Hash -eq (Get-FileHash $f).Hash)) { continue }
    Copy-Item -Force -Path $f -Destination $target
    Write-Changed "$target (a copy for status and uninstall)"
  }
}

# ============================================================================ install steps
function Confirm-Wsl {
  if (-not (Test-Path $script:WslExe)) {
    Write-Host '  WSL is not installed: installing it (wsl --install --no-distribution)'
    # straight to this console: Windows' own wsl.exe writes UTF-16 into a pipe
    $r = Invoke-Wsl -Exe "$env:SystemRoot\System32\wsl.exe" -Arguments @('--install', '--no-distribution')
    Write-Host ''
    Write-Host "wsl --install --no-distribution: exit code $($r.ExitCode). Restart Windows, then run this script again." -ForegroundColor Yellow
    return $false
  }
  $v = Get-WslVersion
  if ($v -lt [version]'2.5.4') {
    Write-Host "  WSL $v is older than 2.5.4 (needed for instanceIdleTimeout): updating"
    $r = Invoke-Wsl -Arguments @('--update')
    $v = Get-WslVersion
    if ($v -lt [version]'2.5.4') { throw "WSL is still $v after wsl --update (exit $($r.ExitCode)); update it from the Microsoft Store or github.com/microsoft/WSL/releases" }
    Write-Changed "WSL updated to $v"
  } else { Write-Same "WSL $v" }
  return $true
}

function Confirm-Distro {
  $d = Get-WslDistro $Distro
  if ($d) {
    if ($d.BasePath -and ($d.BasePath.TrimEnd('\') -ne $Location.TrimEnd('\'))) { Write-Note "$Distro is at $($d.BasePath), not $Location (left where it is)" }
    Write-Same "$Distro is installed ($($d.BasePath))"
    $disk = Join-Path $d.BasePath 'ext4.vhdx'
    if ($d.BasePath -and (Test-Path -LiteralPath $disk)) {
      $who = Get-DiskAclProblem $disk $script:Me.User.Value
      if ($null -ne $who) { Write-Note "$disk can be read by $who, and it holds the deploy key: move the distro into $(Join-Path $MuRoot 'wsl') (wsl --manage $Distro --move <folder>) or fix its access rules" }
    }
    return $false
  }
  Write-Host "  installing $Image as $Distro in $Location (a download of a few hundred MB)"
  $parent = Split-Path $Location -Parent
  if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
  $r = Invoke-Wsl -Arguments @('--install', $Image, '--name', $Distro, '--location', $Location, '--no-launch')
  if ($r.ExitCode -ne 0 -or -not (Get-WslDistro $Distro)) { throw "wsl --install $Image failed (exit $($r.ExitCode))" }
  Write-Changed "$Distro installed ($Image, $Location)"
  return $true
}

function Get-DesiredWslConfig {
  $l = @()
  $l += ,@('general', 'instanceIdleTimeout', '-1', 'the distro keeps running with no client attached')
  $l += ,@('wsl2', 'memory', "$($script:Mem)GB", 'memory for the VM')
  $l += ,@('wsl2', 'processors', "$($script:Cpus)", 'processors for the VM')
  $l += ,@('wsl2', 'swap', "$($script:Swap)GB", 'swap')
  $l += ,@('wsl2', 'guiApplications', 'false', 'headless: no WSLg')
  return ,$l
}
function Get-WslConfigPath { return (Join-Path $env:USERPROFILE '.wslconfig') }
function Read-WslConfig {
  $path = Get-WslConfigPath
  $text = ''; if (Test-Path $path) { $text = [IO.File]::ReadAllText($path) }
  return ,(New-IniLines $text)
}
function Set-WslConfig {
  $path = Get-WslConfigPath
  $lines = Read-WslConfig
  $changed = $false
  foreach ($kv in (Get-DesiredWslConfig)) {
    $r = Set-IniValue $lines $kv[0] $kv[1] $kv[2]
    $label = "[$($kv[0])] $($kv[1])=$($kv[2])"
    if ($r.Changed) {
      Save-Original "wslconfig|$($kv[0])|$($kv[1])" ($null -ne $r.Old) $r.Old
      $was = 'absent'; if ($null -ne $r.Old) { $was = $r.Old }
      Write-Changed ".wslconfig $label (was $was): $($kv[3])"
      $changed = $true
    } else { Write-Same ".wslconfig $label" }
  }
  if ($changed) {
    [IO.File]::WriteAllText($path, (ConvertTo-IniText $lines), $script:Utf8)
    Write-Note "$path applies to every WSL distro of this Windows user, and only after 'wsl --shutdown'"
  }
  return $changed
}

# Copy-IntoDistro TEXT TARGET MODE: writes TEXT (LF line endings) to TARGET in the distro as root, through stdin, only if
# it differs
function Copy-IntoDistro([string]$Text, [string]$Target, [string]$Mode) {
  $bytes = $script:Utf8.GetBytes($Text)
  $dir = $Target.Substring(0, $Target.LastIndexOf('/'))
  $copy = "umask 022; mkdir -p $dir && cat > $Target.new && chmod $Mode $Target.new && if cmp -s $Target.new $Target; then rm -f $Target.new; echo same; else mv -f $Target.new $Target; echo changed; fi"
  $r = Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--exec', '/bin/sh', '-c', $copy) -InputBytes $bytes -Capture
  if ($r.ExitCode -ne 0) { throw "could not copy $Target into $Distro (exit $($r.ExitCode)): $($r.Error) $($r.Output)" }
  if ($r.Output.Trim() -eq 'changed') { Write-Changed "$Target (in $Distro)" } else { Write-Same "$Target (in $Distro)" }
}

function Invoke-DistroSetup {
  # copy the Linux script and the gate's source in through stdin (LF line endings), then run the script as root
  $t = $script:DistroScriptTarget
  Copy-IntoDistro $script:DistroScriptText $t '755'
  Copy-IntoDistro $script:GateText $script:GateTarget '644'
  $a = @('-d', $Distro, '-u', 'root', '--exec', $t, 'install', '--user', $User, '--repo', $Repo, '--from-host')
  # the drive that holds the distro's disk: the distro records it for mu's disk check, which cannot ask Windows once
  # interop is off (docs/design/job-layer.md 13.4)
  $base = $Location
  $reg = Get-WslDistro $Distro
  if ($reg -and $reg.BasePath) { $base = $reg.BasePath }
  $letter = Get-HostDriveLetter $base
  if ($letter) { $a += @('--host-drive', $letter) } else { Write-Note "$base has no drive letter: mu's disk check will see only the distro's virtual disk" }
  if ($Branch) { $a += @('--branch', $Branch) }
  if (-not $NoClone) { $a += '--clone' }
  if ($InstallMu) { $a += '--install-mu' }
  if ($Heartbeat) { $a += @('--heartbeat', $Heartbeat) }
  $rc = (Invoke-Wsl -Arguments $a).ExitCode
  if ($rc -ne 0) { throw "the Linux side failed (exit $rc): see its output above" }
  $res = @{ changed = 0; restart = 0; access = 'unknown' }
  $r = Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--exec', '/bin/cat', '/run/mu-wsl2/install-result') -Capture
  foreach ($l in ($r.Output -split "`n")) { if ($l -match '^(\w+)=(.*)$') { $res[$Matches[1]] = $Matches[2].Trim() } }
  return $res
}

# Ubuntu's first-run setup asks for a user name on the first interactive start. The user exists already (created as
# root above), so mark it as done; WSL keeps this flag in the distro's registration.
function Clear-RunOobe {
  $d = Get-WslDistro $Distro
  if (-not $d) { return }
  $p = Get-ItemProperty $d.Key
  if ($p.PSObject.Properties['RunOOBE'] -and [int]$p.RunOOBE -ne 0) {
    Set-ItemProperty -Path $d.Key -Name 'RunOOBE' -Value 0 -Type DWord
    Write-Changed "Ubuntu's first-run user prompt skipped (the user $User exists)"
  }
}

# Stop-HookService: stops the hook (STOP never touches WSL) and waits until its process has exited. The SCM reports
# STOPPED before the process ends, and its exe can't be replaced or deleted while the process still maps it.
function Stop-HookService {
  $wmi = Get-CimInstance Win32_Service -Filter "Name='$($script:ServiceName)'"
  if (-not $wmi) { return }
  $procId = [int]$wmi.ProcessId
  if ($wmi.State -ne 'Stopped') {
    Stop-Service -Name $script:ServiceName
    (Get-Service -Name $script:ServiceName).WaitForStatus('Stopped', (New-TimeSpan -Seconds 30))
  }
  if ($procId -gt 0) { try { Wait-Process -Id $procId -Timeout 30 -ErrorAction SilentlyContinue } catch { } }
}

function Install-ShutdownHook {
  $bin = Join-Path $MuRoot 'bin'
  $exe = Join-Path $bin 'mu-wsl-shutdown.exe'
  $src = Join-Path $bin 'mu-wsl-shutdown.cs'
  $svc = Get-Service -Name $script:ServiceName -ErrorAction SilentlyContinue
  $current = (Test-Path $exe) -and (Test-Path $src) -and ([IO.File]::ReadAllText($src) -ceq $script:ShutdownServiceSource)
  if ($current) { Write-Same "$exe is built from the current source" }
  else {
    $csc = "$env:SystemRoot\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    if (-not (Test-Path $csc)) { $csc = "$env:SystemRoot\Microsoft.NET\Framework\v4.0.30319\csc.exe" }
    if (-not (Test-Path $csc)) { throw "the .NET Framework 4 C# compiler is missing ($csc)" }
    $newExe = Join-Path $bin 'mu-wsl-shutdown.new.exe'
    [IO.File]::WriteAllText("$src.new", $script:ShutdownServiceSource, $script:Utf8)
    $r = Invoke-Native $csc @('/nologo', '/optimize+', '/target:exe', '/platform:anycpu', '/r:System.ServiceProcess.dll', "/out:$newExe", "$src.new")
    if ($r.ExitCode -ne 0 -or -not (Test-Path $newExe)) { throw "building the shutdown hook failed:`n$($r.Output -join "`n")" }
    if ($svc) { Stop-HookService }
    Move-Item -Force -Path $newExe -Destination $exe
    Move-Item -Force -Path "$src.new" -Destination $src
    Write-Changed "$exe built (the in-box C# compiler)"
  }
  $wantPath = '"' + $exe + '"'
  $wmi = Get-CimInstance Win32_Service -Filter "Name='$($script:ServiceName)'"
  if ($wmi -and ($wmi.PathName -ne $wantPath -or $wmi.StartName -ne 'LocalSystem')) {
    Write-Host "  re-creating the service (it runs $($wmi.PathName) as $($wmi.StartName))"
    Stop-HookService
    [void](Invoke-Native 'sc.exe' @('delete', $script:ServiceName))
    Start-Sleep -Seconds 2
    $wmi = $null
  }
  if (-not $wmi) {
    New-Service -Name $script:ServiceName -BinaryPathName $wantPath -DisplayName $script:ServiceDisplayName `
      -Description 'Stops WSLService (a clean poweroff of every WSL distro) when Windows shuts down or restarts. mu WSL2 host adapter (mu-wsl2-host.ps1); log in <MuRoot>\logs\mu-wsl-shutdown.log.' `
      -StartupType Automatic | Out-Null
    Write-Changed "service $($script:ServiceName) created (LocalSystem, automatic)"
  } elseif ($wmi.StartMode -ne 'Auto') {
    Set-Service -Name $script:ServiceName -StartupType Automatic
    Write-Changed "service $($script:ServiceName) starts automatically"
  } else { Write-Same "service $($script:ServiceName) (LocalSystem, automatic)" }
  # the preshutdown timeout: the service sets it through ChangeServiceConfig2 at every start; the registry value is
  # the same setting, read at boot
  $svcKey = "HKLM:\SYSTEM\CurrentControlSet\Services\$($script:ServiceName)"
  if ((Get-RegDword $svcKey 'PreshutdownTimeout') -ne 90000) {
    New-ItemProperty -Path $svcKey -Name 'PreshutdownTimeout' -PropertyType DWord -Value 90000 -Force | Out-Null
    Write-Changed 'preshutdown timeout 90 s (Windows waits up to that long for WSL to stop)'
  } else { Write-Same 'preshutdown timeout 90 s' }
  $fa = (Get-ItemProperty -Path $svcKey -Name 'FailureActions' -ErrorAction SilentlyContinue)
  $faBytes = $null; if ($fa) { $faBytes = [byte[]]$fa.FailureActions }
  if (-not (Test-FailureActionsBytes $faBytes)) {
    $r = Invoke-Native 'sc.exe' @('failure', $script:ServiceName, 'reset=', '86400', 'actions=', 'restart/10000/restart/10000/restart/60000')
    if ($r.ExitCode -ne 0) { throw "sc.exe failure: $($r.Output -join ' ')" }
    Write-Changed 'restarts after a failure (10 s, 10 s, 60 s)'
  } else { Write-Same 'restarts after a failure' }
  $order = @((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control' -Name 'PreshutdownOrder' -ErrorAction SilentlyContinue).PreshutdownOrder)
  if ($order.Count -eq 0 -or $order[0] -ne $script:ServiceName) {
    $new = Merge-PreshutdownOrder $order $script:ServiceName
    New-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control' -Name 'PreshutdownOrder' -PropertyType MultiString -Value $new -Force | Out-Null
    Write-Changed "first in PreshutdownOrder ($($new -join ', '))"
  } else { Write-Same 'first in PreshutdownOrder' }
  if ((Get-Service -Name $script:ServiceName).Status -ne 'Running') {
    Start-Service -Name $script:ServiceName
    Write-Changed "service $($script:ServiceName) started"
  } else { Write-Same "service $($script:ServiceName) is running" }
}

function Register-StartupTask {
  $sid = $script:Me.User.Value
  $t = Get-ScheduledTask -TaskPath $script:TaskPath -TaskName $script:TaskName -ErrorAction SilentlyContinue
  if ($t) {
    $diff = @(Compare-StartupTask $t $sid $script:WslExe $Distro)
    if ($diff.Count -eq 0) { Write-Same "task $($script:TaskPath)$($script:TaskName): at startup +30 s, every 5 min, S4U as $($script:Me.Name)"; return }
    Write-Host "  updating the task: $($diff -join '; ')"
  }
  Register-ScheduledTask -TaskPath $script:TaskPath -TaskName $script:TaskName -Xml (Get-TaskXml $sid $script:WslExe $Distro) -Force | Out-Null
  Write-Changed "task $($script:TaskPath)$($script:TaskName): at startup +30 s, every 5 min, as $($script:Me.Name) (S4U: runs with nobody signed in, no stored password)"
}

# ============================================================================ actions
function Resolve-Settings {
  # explicit parameters, else what install recorded, else computed from this PC
  $s = $script:State.settings
  $cs = Get-CimInstance Win32_ComputerSystem
  $script:Mem = $MemoryGB; $script:Cpus = $Processors; $script:Swap = $SwapGB
  if (-not $script:BoundParams.ContainsKey('MemoryGB')) { if ($s.Contains('memoryGB')) { $script:Mem = [int]$s.memoryGB } else { $script:Mem = Get-DefaultMemoryGB $cs.TotalPhysicalMemory } }
  if (-not $script:BoundParams.ContainsKey('Processors')) { if ($s.Contains('processors')) { $script:Cpus = [int]$s.processors } else { $script:Cpus = [int]$cs.NumberOfLogicalProcessors } }
  if (-not $script:BoundParams.ContainsKey('SwapGB') -and $s.Contains('swapGB')) { $script:Swap = [int]$s.swapGB }
  $script:NoPower = [bool]$NoPowerChanges; $script:NoHook = [bool]$NoShutdownHook
  if (-not $script:BoundParams.ContainsKey('NoPowerChanges') -and $s.Contains('noPowerChanges')) { $script:NoPower = [bool]$s.noPowerChanges }
  if (-not $script:BoundParams.ContainsKey('NoShutdownHook') -and $s.Contains('noShutdownHook')) { $script:NoHook = [bool]$s.noShutdownHook }
  $script:TotalGB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
}

function Invoke-Install {
  $script:DistroScriptPath = Join-Path $PSScriptRoot $script:DistroScriptName
  if (-not (Test-Path $script:DistroScriptPath)) { throw "$($script:DistroScriptName) is missing next to this script ($PSScriptRoot): copy the whole install\hosts\wsl2 folder" }
  $script:DistroScriptText = [IO.File]::ReadAllText($script:DistroScriptPath) -replace "`r`n", "`n"
  $script:GatePath = Join-Path $PSScriptRoot $script:GateName
  if (-not (Test-Path $script:GatePath)) { throw "$($script:GateName) is missing next to this script ($PSScriptRoot): copy the whole install\hosts\wsl2 folder" }
  $script:GateText = [IO.File]::ReadAllText($script:GatePath) -replace "`r`n", "`n"
  Write-Host "mu WSL2 host: install on $env:COMPUTERNAME for $($script:Me.Name)"
  Write-Host "  distro $Distro ($Location), user $User; WSL VM: $($script:Mem) GB of $($script:TotalGB) GB, $($script:Cpus) processors, $($script:Swap) GB swap"
  $console = (Get-CimInstance Win32_ComputerSystem).UserName
  if ($console -and $console -ne $script:Me.Name) {
    Write-Note "running as $($script:Me.Name) while $console is signed in: the distro, .wslconfig and the task belong to $($script:Me.Name)"
  }

  Write-Step 'Folders'
  Initialize-MuDirs
  Copy-HostScripts
  $script:State.settings = [ordered]@{ distro = $Distro; location = $Location; user = $User; memoryGB = $script:Mem; processors = $script:Cpus; swapGB = $script:Swap; noPowerChanges = $script:NoPower; noShutdownHook = $script:NoHook }
  if (-not $script:State.installed) { $script:State.installed = Format-Utc (Get-Date) }
  Save-State

  # who owns this user's WSL session before this run's own wsl.exe calls (see Get-WslSessionSource)
  $sessionBefore = Get-WslSessionState
  Write-Step 'WSL'
  if (-not (Confirm-Wsl)) { return 0 }
  $wasRunning = $false
  Write-Step "Distro $Distro"
  $newDistro = Confirm-Distro
  if (-not $newDistro) { $wasRunning = Test-DistroRunning }

  Write-Step "WSL settings ($(Get-WslConfigPath))"
  $wslconfigChanged = Set-WslConfig

  Write-Step "Inside $Distro (as root)"
  $res = Invoke-DistroSetup
  Clear-RunOobe

  Write-Step 'Windows power'
  if ($script:NoPower) { Write-Host '  left alone (-NoPowerChanges)' } else { Set-PowerSettings }

  Write-Step 'Shutdown hook'
  if ($script:NoHook) {
    if (Get-Service -Name $script:ServiceName -ErrorAction SilentlyContinue) { Write-Note "-NoShutdownHook, but $($script:ServiceName) is installed (uninstall removes it)" }
    else { Write-Host '  not installed (-NoShutdownHook): a Windows restart stops the distro without a Linux shutdown' }
  } else { Install-ShutdownHook }

  Write-Step 'Startup task'
  Register-StartupTask

  Write-Step 'Start'
  $restartVm = $wslconfigChanged
  $restartDistro = ($res.restart -eq '1') -or $newDistro
  if ($NoRestart -and ($restartVm -or $restartDistro)) {
    Write-Note "not restarting (-NoRestart): apply the changes with a clean stop and start: $(Join-Path (Join-Path $MuRoot 'host') 'mu-wsl2-host.ps1') test-stop"
  } elseif ($restartVm) {
    Write-Note 'wsl --shutdown, so that .wslconfig applies: this stops every running WSL distro of this user'
    [void](Invoke-Wsl -Arguments @('--shutdown'))
    Write-Changed 'WSL restarted'
  } elseif ($restartDistro) {
    [void](Invoke-Wsl -Arguments @('--terminate', $Distro))
    Write-Changed "$Distro restarted"
  }
  if (Test-DistroRunning) { Write-Same "$Distro is running" }
  elseif (Start-DistroViaTask) { if ($wasRunning) { Write-Same "$Distro is running (started by the task)" } else { Write-Host "  $Distro started by the task" } }
  else { Write-Note "$Distro did not start through the task within 2 minutes: check the task (status)" }

  if ($sessionBefore -ne 'task') {
    Write-Note "WSL may have been started for $($script:Me.Name) from this sign-in (by this install) rather than by the startup task: then signing out stops $Distro until the task starts it again (within 5 minutes). To hand it to the task now: restart Windows, or run test-stop"
  }

  Write-Host ''
  $n = $script:Changes.Count + [int]$res.changed
  if ($n -eq 0) { Write-Host 'Nothing to change: this PC is already set up as a mu compute node.' -ForegroundColor Green }
  else { Write-Host "Done: $n change(s) ($($script:Changes.Count) on Windows, $($res.changed) in $Distro)." -ForegroundColor Green }
  if ($res.access -ne 'ok') {
    Write-Host "Still to do: add the deploy key printed above on GitHub, then run this install again (it clones then)." -ForegroundColor Yellow
  }
  Write-Host "Check everything:  powershell -NoProfile -ExecutionPolicy Bypass -File $(Join-Path (Join-Path $MuRoot 'host') 'mu-wsl2-host.ps1') status"
  return 0
}

function Invoke-Status {
  $os = Get-CimInstance Win32_OperatingSystem
  $bootUtc = ([datetime]$os.LastBootUpTime).ToUniversalTime()
  Add-Check 'info' 'windows' "$($os.Caption) build $($os.BuildNumber), started $(Format-Utc $os.LastBootUpTime)"
  $ex = @(Get-Process -Name explorer -ErrorAction SilentlyContinue | Where-Object { $null -ne $_.StartTime } | Sort-Object StartTime)
  if ($ex.Count -gt 0) {
    $mins = [int](($ex[0].StartTime.ToUniversalTime() - $bootUtc).TotalMinutes)
    Add-Check 'info' 'sign-in' "first sign-in since startup $(Format-Utc $ex[0].StartTime) ($mins min after startup)"
  } else { Add-Check 'info' 'sign-in' 'nobody is signed in' }

  # WSL and the distro. wsl.exe is called only when the startup task has run since WSL started: otherwise the call
  # could create this user's WSL session from this sign-in, and signing out would then stop the distro.
  $running = $false; $d = $null
  if (Test-Path $script:WslExe) {
    $v = Get-WslVersion   # the file's version, else wsl --version (which does not start WSL)
    if ($v -ge [version]'2.5.4') { Add-Check 'pass' 'wsl' "WSL $v" } else { Add-Check 'fail' 'wsl' "WSL $v is older than 2.5.4 (wsl --update)" }
    $d = Get-WslDistro $Distro
    if ($d) {
      Add-Check 'pass' 'distro' "$Distro at $($d.BasePath)"
      switch (Get-WslSessionState) {
        'none' { Add-Check 'fail' 'distro.running' "$Distro is not running: WSLService is stopped (the startup task starts it within 5 minutes)" }
        'task' {
          $running = Test-DistroRunning
          if ($running) { Add-Check 'pass' 'distro.running' "$Distro is running" } else { Add-Check 'fail' 'distro.running' "$Distro is not running (the startup task starts it within 5 minutes)" }
        }
        default { Add-Check 'warn' 'distro.running' "unknown: the startup task has not run successfully since WSL started, and asking WSL from here could tie it to this sign-in. It runs every 5 minutes; or now: Start-ScheduledTask -TaskPath \mu\ -TaskName mu-compute" }
      }
    } else { Add-Check 'fail' 'distro' "no distro named $Distro for $($script:Me.Name)" }
  } else { Add-Check 'fail' 'wsl' "WSL is not installed ($($script:WslExe))" }

  # .wslconfig
  $lines = Read-WslConfig
  foreach ($kv in (Get-DesiredWslConfig)) {
    $got = Get-IniValue $lines $kv[0] $kv[1]
    $name = ".wslconfig.$($kv[0]).$($kv[1])"
    if ($null -ne $got -and (Get-NormalizedIniValue $got) -eq (Get-NormalizedIniValue $kv[2])) { Add-Check 'pass' $name $got }
    else { Add-Check 'fail' $name "is '$got', want '$($kv[2])' ($($kv[3]))" }
  }

  # the startup task
  try {
  $t = Get-ScheduledTask -TaskPath $script:TaskPath -TaskName $script:TaskName -ErrorAction SilentlyContinue
  if ($t) {
    $diff = @(Compare-StartupTask $t $script:Me.User.Value $script:WslExe $Distro)
    if ($diff.Count -eq 0) { Add-Check 'pass' 'task' "$($script:TaskPath)$($script:TaskName): at startup +30 s, every 5 min, S4U" }
    else { Add-Check 'fail' 'task' ($diff -join '; ') }
    $info = Get-ScheduledTaskInfo -TaskPath $script:TaskPath -TaskName $script:TaskName
    $res = [int64]$info.LastTaskResult
    $hex = '0x{0:X}' -f $res
    if ($res -eq 0 -or $res -eq 0x41301) { Add-Check 'pass' 'task.last-run' "$(Format-Utc $info.LastRunTime): result $hex" }
    elseif ($res -eq 0x41303) { Add-Check 'warn' 'task.last-run' 'has not run yet (it runs at the next startup, or: Start-ScheduledTask -TaskPath \mu\ -TaskName mu-compute)' }
    else { Add-Check 'fail' 'task.last-run' "$(Format-Utc $info.LastRunTime): result $hex" }
  } else { Add-Check 'fail' 'task' "no task $($script:TaskPath)$($script:TaskName)" }
  } catch { Add-Check 'fail' 'task' "could not read the task: $($_.Exception.Message)" }

  # power
  if ($script:NoPower) { Add-Check 'info' 'power' 'not managed (-NoPowerChanges)' }
  else {
    $sb = Get-PowerAcSeconds 'STANDBYIDLE'
    if ($sb -eq 0) { Add-Check 'pass' 'power.sleep' 'never on AC power' } else { Add-Check 'fail' 'power.sleep' "after $sb s on AC power" }
    $hb = Get-PowerAcSeconds 'HIBERNATEIDLE'
    if ($hb -eq 0) { Add-Check 'pass' 'power.hibernate' 'never on AC power' } else { Add-Check 'fail' 'power.hibernate' "after $hb s on AC power" }
    $fs = Get-RegDword $script:PowerKey 'HiberbootEnabled'
    if ($fs -eq 0) { Add-Check 'pass' 'power.fast-startup' 'off' } else { Add-Check 'fail' 'power.fast-startup' "HiberbootEnabled=$fs" }
    $ar = Get-RegDword $script:PolicyKey 'DisableAutomaticRestartSignOn'
    if ($ar -eq 1) { Add-Check 'pass' 'power.auto-sign-in' 'off after updates' } else { Add-Check 'fail' 'power.auto-sign-in' "DisableAutomaticRestartSignOn=$ar" }
  }

  # the shutdown hook
  try {
  if ($script:NoHook) { Add-Check 'warn' 'hook' 'not installed (-NoShutdownHook): a Windows restart stops the distro without a Linux shutdown' }
  else {
    $wmi = Get-CimInstance Win32_Service -Filter "Name='$($script:ServiceName)'"
    if (-not $wmi) { Add-Check 'fail' 'hook' "service $($script:ServiceName) is not installed" }
    else {
      if ($wmi.State -eq 'Running') { Add-Check 'pass' 'hook.running' 'running' } else { Add-Check 'fail' 'hook.running' "$($wmi.State) (Start-Service $($script:ServiceName))" }
      if ($wmi.StartMode -eq 'Auto' -and $wmi.StartName -eq 'LocalSystem') { Add-Check 'pass' 'hook.config' 'automatic, LocalSystem' } else { Add-Check 'fail' 'hook.config' "$($wmi.StartMode), $($wmi.StartName)" }
      $q = Invoke-Native 'sc.exe' @('query', $script:ServiceName)
      if (($q.Output -join ' ') -match 'PRESHUTDOWN') { Add-Check 'pass' 'hook.preshutdown' 'accepts PRESHUTDOWN' }
      elseif ($wmi.State -eq 'Running') { Add-Check 'fail' 'hook.preshutdown' 'does not accept PRESHUTDOWN' }
      $to = Get-RegDword "HKLM:\SYSTEM\CurrentControlSet\Services\$($script:ServiceName)" 'PreshutdownTimeout'
      if ($to -ge 90000) { Add-Check 'pass' 'hook.timeout' "$to ms" } else { Add-Check 'fail' 'hook.timeout' "preshutdown timeout $to ms (want 90000)" }
      $order = @((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control' -Name 'PreshutdownOrder' -ErrorAction SilentlyContinue).PreshutdownOrder)
      if ($order.Count -gt 0 -and $order[0] -eq $script:ServiceName) { Add-Check 'pass' 'hook.order' "first in PreshutdownOrder ($($order -join ', '))" }
      else { Add-Check 'fail' 'hook.order' "PreshutdownOrder is $($order -join ', ')" }
      $src = Join-Path (Join-Path $MuRoot 'bin') 'mu-wsl-shutdown.cs'
      if ((Test-Path $src) -and ([IO.File]::ReadAllText($src) -ceq $script:ShutdownServiceSource)) { Add-Check 'pass' 'hook.build' 'built from the current source' }
      else { Add-Check 'warn' 'hook.build' 'built from another version of this script (run install)' }
      $log = Join-Path (Join-Path $MuRoot 'logs') 'mu-wsl-shutdown.log'
      if (Test-Path $log) {
        $r = Get-LastShutdownResult @(Get-Content -Path $log -Tail 400) $bootUtc
        Add-Check $r[0] 'hook.last-shutdown' $r[1]
      } else { Add-Check 'info' 'hook.last-shutdown' "no log yet ($log)" }
      try {
        $since = (Get-Date).AddDays(-30)
        if ($script:State.installed) { $since = (ConvertTo-UtcDate $script:State.installed).ToLocalTime() }
        $ev = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Service Control Manager'; Id = 7043; StartTime = $since } -ErrorAction SilentlyContinue |
          Where-Object { $vals = @($_.Properties | ForEach-Object { [string]$_.Value }); ($vals -contains $script:ServiceName) -or ($vals -contains $script:ServiceDisplayName) })
        if ($ev.Count -gt 0) { Add-Check 'fail' 'hook.event-7043' "$($ev.Count) time(s) the hook did not finish within its preshutdown timeout, last $(Format-Utc $ev[0].TimeCreated)" }
        else { Add-Check 'pass' 'hook.event-7043' 'never timed out at shutdown (no event 7043)' }
      } catch { Add-Check 'info' 'hook.event-7043' "could not read the System log: $($_.Exception.Message)" }
    }
  }
  } catch { Add-Check 'fail' 'hook' "could not check the hook: $($_.Exception.Message)" }

  # folders: who can write <MuRoot>\bin (run by a SYSTEM service) and read <MuRoot>\wsl (the distro's disk)
  foreach ($spec in (Get-MuDirSpecs)) {
    if (-not (Test-Path -LiteralPath $spec.Path)) { continue }
    try {
      if (Test-DirAcl $spec) { Add-Check 'pass' "folder.$($spec.Name)" "$($spec.Path) ($($spec.Why))" }
      else { Add-Check 'fail' "folder.$($spec.Name)" "$($spec.Path): other users can change it, or it inherits the drive's access rules (run install)" }
    } catch { Add-Check 'fail' "folder.$($spec.Name)" "could not read the access rules of $($spec.Path): $($_.Exception.Message)" }
  }
  if ($d -and $d.BasePath) {
    $disk = Join-Path $d.BasePath 'ext4.vhdx'
    if (Test-Path -LiteralPath $disk) {
      try {
        $who = Get-DiskAclProblem $disk $script:Me.User.Value
        if ($null -eq $who) { Add-Check 'pass' 'distro.disk' "$disk (only $($script:Me.Name), administrators and SYSTEM can read it)" }
        else { Add-Check 'fail' 'distro.disk' "$disk can be read by $who (it holds the deploy key: move the distro into $(Join-Path $MuRoot 'wsl') or fix its access rules)" }
      } catch { Add-Check 'info' 'distro.disk' "could not read the access rules of ${disk}: $($_.Exception.Message)" }
    }
  }

  # inside the distro (only when it runs: starting it from here would tie its WSL session to this sign-in)
  if ($running) {
    $a = @('-d', $Distro, '-u', 'root', '--exec', $script:DistroScriptTarget, 'status', '--json', '--user', $User)
    if ($Offline) { $a += '--offline' }
    $r = Invoke-Wsl -Arguments $a -Capture
    $j = $null
    $jl = @($r.Output -split "`n" | Where-Object { $_.TrimStart().StartsWith('{', [StringComparison]::Ordinal) })
    if ($jl.Count -gt 0) { try { $j = $jl[$jl.Count - 1] | ConvertFrom-Json } catch { } }
    if ($null -eq $j) { Add-Check 'fail' 'linux' "mu-wsl2-distro status did not answer (exit $($r.ExitCode)): $($r.Error) $($r.Output)".Trim() }
    else {
      foreach ($c in @($j.checks)) { Add-Check ([string]$c.result) ("linux.$($c.name)") ([string]$c.detail) }
      if ([int]$j.cpus -eq $script:Cpus) { Add-Check 'pass' 'vm.processors' "$($j.cpus)" } else { Add-Check 'fail' 'vm.processors' "$($j.cpus), want $($script:Cpus) (wsl --shutdown applies .wslconfig)" }
      $wantMiB = $script:Mem * 1024
      if ([int]$j.mem_mib -le $wantMiB -and [int]$j.mem_mib -ge [int]($wantMiB * 0.85)) { Add-Check 'pass' 'vm.memory' "$($j.mem_mib) MiB" }
      else { Add-Check 'fail' 'vm.memory' "$($j.mem_mib) MiB, want about $wantMiB (wsl --shutdown applies .wslconfig)" }
      if ($j.started) {
        try {
          $st = ConvertTo-UtcDate $j.started
          Add-Check 'info' 'vm.started' "$(Format-Utc $st), $([int]($st - $bootUtc).TotalSeconds) s after Windows started"
        } catch { }
      }
    }
  }

  $fails = @($script:Checks | Where-Object { $_.result -eq 'fail' }).Count
  if ($Json) {
    $o = [ordered]@{ ok = ($fails -eq 0); computer = $env:COMPUTERNAME; user = $script:Me.Name; distro = $Distro; checks = $script:Checks.ToArray() }
    [Console]::Out.WriteLine(($o | ConvertTo-Json -Depth 6))
  } else {
    foreach ($c in $script:Checks) {
      $color = 'Gray'
      switch ($c.result) { 'pass' { $color = 'Green' } 'fail' { $color = 'Red' } 'warn' { $color = 'Yellow' } }
      Write-Host ('  {0,-5} {1,-26} {2}' -f $c.result.ToUpper(), $c.name, $c.detail) -ForegroundColor $color
    }
    Write-Host ''
    if ($fails -eq 0) { Write-Host 'mu WSL2 host status: all checks pass' -ForegroundColor Green } else { Write-Host "mu WSL2 host status: $fails check(s) failed" -ForegroundColor Red }
  }
  if ($fails -eq 0) { return 0 } else { return 1 }
}

function Restore-Original([string]$Name, $Entry) {
  $parts = $Name.Split('|')
  switch ($parts[0]) {
    'reg' {
      $path = $parts[1]; $n = $parts[2]
      if ($Entry.present) { New-ItemProperty -Path $path -Name $n -PropertyType DWord -Value ([int]$Entry.value) -Force | Out-Null; Write-Changed "$n restored to $($Entry.value)" }
      elseif ($null -ne (Get-RegDword $path $n)) { Remove-ItemProperty -Path $path -Name $n; Write-Changed "$n removed (it was not set)" }
    }
    'power' {
      if ($Entry.present -and $Entry.value -and $null -ne $Entry.value.seconds) {
        $scheme = 'SCHEME_CURRENT'; if ($Entry.value.scheme) { $scheme = [string]$Entry.value.scheme }
        $r = Invoke-Native 'powercfg.exe' @('/setacvalueindex', $scheme, 'SUB_SLEEP', $parts[1], "$($Entry.value.seconds)")
        if ($r.ExitCode -eq 0) { [void](Invoke-Native 'powercfg.exe' @('/setactive', 'SCHEME_CURRENT')); Write-Changed "$($parts[1]) on AC power restored to $($Entry.value.seconds) s" }
        else { Write-Note "could not restore $($parts[1]): $($r.Output -join ' ')" }
      }
    }
    'wslconfig' {
      $path = Get-WslConfigPath
      $lines = Read-WslConfig
      if ($Entry.present) { [void](Set-IniValue $lines $parts[1] $parts[2] ([string]$Entry.value)); Write-Changed ".wslconfig [$($parts[1])] $($parts[2]) restored to $($Entry.value)" }
      elseif (Remove-IniValue $lines $parts[1] $parts[2]) { Write-Changed ".wslconfig [$($parts[1])] $($parts[2]) removed (it was not set)" }
      if ($lines.Count -eq 0) { if (Test-Path $path) { Remove-Item -Force $path } } else { [IO.File]::WriteAllText($path, (ConvertTo-IniText $lines), $script:Utf8) }
    }
  }
}

function Invoke-Uninstall {
  Write-Host "mu WSL2 host: uninstall on $env:COMPUTERNAME for $($script:Me.Name)"
  $statePath = Get-StatePath
  if (-not (Test-Path $statePath)) { Write-Note "no $statePath (removing the task and the hook, but the earlier values of the settings are unknown and left as they are)" }

  Write-Step 'Startup task'
  if (Get-ScheduledTask -TaskPath $script:TaskPath -TaskName $script:TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskPath $script:TaskPath -TaskName $script:TaskName -Confirm:$false
    Write-Changed "task $($script:TaskPath)$($script:TaskName) removed"
  } else { Write-Same 'no startup task' }

  Write-Step 'Shutdown hook'
  if (Get-Service -Name $script:ServiceName -ErrorAction SilentlyContinue) {
    Stop-HookService
    $r = Invoke-Native 'sc.exe' @('delete', $script:ServiceName)
    if ($r.ExitCode -ne 0) { Write-Note "sc.exe delete: $($r.Output -join ' ')" } else { Write-Changed "service $($script:ServiceName) removed" }
  } else { Write-Same 'no shutdown hook service' }
  $order = @((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control' -Name 'PreshutdownOrder' -ErrorAction SilentlyContinue).PreshutdownOrder)
  if ($order -contains $script:ServiceName) {
    New-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control' -Name 'PreshutdownOrder' -PropertyType MultiString -Value (Remove-FromPreshutdownOrder $order $script:ServiceName) -Force | Out-Null
    Write-Changed 'removed from PreshutdownOrder'
  }
  foreach ($f in @('mu-wsl-shutdown.exe', 'mu-wsl-shutdown.cs')) {
    $p = Join-Path (Join-Path $MuRoot 'bin') $f
    if (Test-Path $p) { Remove-Item -Force $p; Write-Changed "$p removed" }
  }

  Write-Step 'Windows settings'
  $names = @($script:State.originals.Keys)
  if ($names.Count -eq 0) { Write-Same 'nothing recorded to restore' }
  foreach ($n in $names) {
    Restore-Original $n $script:State.originals[$n]
    $script:State.originals.Remove($n)
    Save-State
  }
  if (@($names | Where-Object { $_ -like 'wslconfig|*' }).Count -gt 0) { Write-Note ".wslconfig applies after 'wsl --shutdown' (or a restart)" }

  Write-Step "Distro $Distro"
  $d = Get-WslDistro $Distro
  if (-not $d) { Write-Same "no distro named $Distro" }
  elseif ($RemoveDistro) {
    $go = [bool]$Force
    if (-not $go) {
      Write-Host "  This deletes $Distro and everything in it ($($d.BasePath)): the toolchain, clones, job data, the ssh key." -ForegroundColor Yellow
      $answer = Read-Host "  Type the distro name ($Distro) to delete it"
      $go = ($answer -ceq $Distro)
    }
    if ($go) {
      $r = Invoke-Wsl -Arguments @('--unregister', $Distro)
      if ($r.ExitCode -ne 0) { throw "wsl --unregister $Distro failed (exit $($r.ExitCode))" }
      Write-Changed "$Distro deleted"
    } else { Write-Host '  not deleted' }
  } else {
    $r = Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--exec', '/bin/sh', '-c', "if [ -x $($script:DistroScriptTarget) ]; then $($script:DistroScriptTarget) uninstall --user $User && rm -f $($script:DistroScriptTarget) $($script:GateTarget) && { rmdir /usr/local/lib/mu 2>/dev/null || true; }; fi")
    if ($r.ExitCode -ne 0) { Write-Note "the Linux side did not uninstall cleanly (exit $($r.ExitCode))" }
    Write-Host "  kept: the distro $Distro and its user $User, with their files (uninstall -RemoveDistro deletes it)"
  }

  Write-Step 'Folders'
  foreach ($f in @('mu-wsl2-host.ps1', $script:DistroScriptName, $script:GateName)) {
    $p = Join-Path (Join-Path $MuRoot 'host') $f
    if ((Test-Path $p) -and ($p -ne $script:SelfPath)) { Remove-Item -Force $p; Write-Changed "$p removed" }
  }
  if ($script:State.originals.Count -eq 0 -and (Test-Path $statePath)) { Remove-Item -Force $statePath; Write-Changed "$statePath removed" }
  Write-Host "  kept: $(Join-Path $MuRoot 'logs')"
  Write-Host ''
  Write-Host "Done: $($script:Changes.Count) change(s)." -ForegroundColor Green
  return 0
}

function Invoke-TestStop {
  $exe = Join-Path (Join-Path $MuRoot 'bin') 'mu-wsl-shutdown.exe'
  if (-not (Test-Path $exe)) { throw "the shutdown hook is not installed ($exe): run install first" }
  $name = 'mu-wsl-stop-test'
  Write-Host 'mu WSL2 host: test-stop. This stops WSL as SYSTEM, the way the shutdown hook does at a restart.'
  Write-Note 'every running WSL distro of every user stops now'
  $action = New-ScheduledTaskAction -Execute $exe -Argument '--stop-wsl'
  $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
  $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  Register-ScheduledTask -TaskPath $script:TaskPath -TaskName $name -Action $action -Principal $principal -Settings $settings -Force | Out-Null
  try {
    $t0 = (Get-Date).AddSeconds(-2)
    Start-ScheduledTask -TaskPath $script:TaskPath -TaskName $name
    $deadline = (Get-Date).AddSeconds(150)
    do {
      Start-Sleep -Seconds 2
      $info = Get-ScheduledTaskInfo -TaskPath $script:TaskPath -TaskName $name
      $state = (Get-ScheduledTask -TaskPath $script:TaskPath -TaskName $name).State
    } while (([string]$state -eq 'Running' -or $info.LastRunTime -lt $t0) -and (Get-Date) -lt $deadline)
    Write-Host "  mu-wsl-shutdown.exe --stop-wsl as SYSTEM: exit code $($info.LastTaskResult)"
  } finally {
    Unregister-ScheduledTask -TaskPath $script:TaskPath -TaskName $name -Confirm:$false
  }
  $log = Join-Path (Join-Path $MuRoot 'logs') 'mu-wsl-shutdown.log'
  if (Test-Path $log) { Get-Content -Path $log -Tail 4 | ForEach-Object { Write-Host "    $_" } }
  Write-Host "  starting $Distro again through the startup task"
  if (-not (Start-DistroViaTask)) { throw "$Distro was not started again through the startup task (see the note above)" }
  Write-Host ''
  Write-Host "Inside $Distro (expect last-stop: a clean stop, and no NUL bytes in the heartbeat log):"
  $r = Invoke-Wsl -Arguments @('-d', $Distro, '-u', 'root', '--exec', $script:DistroScriptTarget, 'status', '--offline', '--user', $User)
  if ([int]$info.LastTaskResult -ne 0) { return 1 }
  return $r.ExitCode
}

# ============================================================================ main
if ($MyInvocation.InvocationName -eq '.') { return }   # dot-sourced (tools/test-wsl2-host): the functions only

function Test-Admin {
  $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Get-RelaunchArguments SCRIPT: powershell.exe arguments that run SCRIPT with this run's action and parameters
function Get-RelaunchArguments([string]$ScriptPath) {
  $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ScriptPath, '-Action', $Action)
  foreach ($k in @($script:BoundParams.Keys)) {
    if ($k -eq 'Action' -or $k -eq 'Elevated') { continue }
    $v = $script:BoundParams[$k]
    if ($v -is [System.Management.Automation.SwitchParameter]) { if ($v.IsPresent) { $argv += "-$k" } }
    else { $argv += "-$k"; $argv += [string]$v }
  }
  return ,$argv
}

# 32-bit Windows PowerShell on 64-bit Windows sees another Program Files, System32 and registry: run this script again
# in the 64-bit one (in this window)
function Invoke-As64Bit {
  $ps = "$env:SystemRoot\sysnative\WindowsPowerShell\v1.0\powershell.exe"
  if (-not (Test-Path $ps)) { throw "this is 32-bit PowerShell and the 64-bit one was not found ($ps): run the script from the 64-bit Windows PowerShell" }
  $argv = Get-RelaunchArguments $script:SelfPath
  if ($Elevated) { $argv += '-Elevated' }
  Write-Host 'mu-wsl2-host: continuing in 64-bit Windows PowerShell'
  $p = Start-Process -FilePath $ps -ArgumentList (@($argv | ForEach-Object { ConvertTo-WinArgument $_ }) -join ' ') -NoNewWindow -Wait -PassThru
  return $p.ExitCode
}

# restart this script elevated (UAC), with the same parameters; from a UNC path (\\wsl.localhost\...), a local copy
function Invoke-Elevated {
  $src = $script:SelfPath
  if ($src.StartsWith('\\', [StringComparison]::Ordinal)) {
    $tmp = Join-Path $env:TEMP ('mu-wsl2-host-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    Copy-Item -Path $src -Destination $tmp
    foreach ($n in @($script:DistroScriptName, $script:GateName)) {
      $sib = Join-Path (Split-Path $src -Parent) $n
      if (Test-Path $sib) { Copy-Item -Path $sib -Destination $tmp }
    }
    $src = Join-Path $tmp (Split-Path $src -Leaf)
  }
  $argv = Get-RelaunchArguments $src
  $argv += '-Elevated'
  $line = (@($argv | ForEach-Object { ConvertTo-WinArgument $_ }) -join ' ')
  $exe = (Get-Process -Id $PID).Path
  Write-Host "mu-wsl2-host: '$Action' needs administrator rights: continuing in an elevated window"
  try { $p = Start-Process -FilePath $exe -ArgumentList $line -Verb RunAs -Wait -PassThru }
  catch { Write-Host "mu-wsl2-host: not elevated ($($_.Exception.Message))" -ForegroundColor Red; return 1 }
  return $p.ExitCode
}

if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT -and [Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
  $rc = 1
  try { $rc = [int](Invoke-As64Bit) } catch { Write-Host "mu-wsl2-host: $($_.Exception.Message)" -ForegroundColor Red }
  exit $rc
}

$rc = 1
$transcript = $false
$prevEncoding = $null
try {
  if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'this script runs on Windows' }
  if (-not $MuRoot) { $MuRoot = "$env:SystemDrive\Mu" }
  if (-not $Location) { $Location = Join-Path (Join-Path $MuRoot 'wsl') $Distro }
  Set-Location -Path $env:SystemRoot   # not a UNC path: wsl.exe and csc.exe start here
  [Environment]::CurrentDirectory = $env:SystemRoot
  try { $prevEncoding = [Console]::OutputEncoding; [Console]::OutputEncoding = $script:Utf8 } catch { }   # Linux output is UTF-8
  $script:Me = [Security.Principal.WindowsIdentity]::GetCurrent()
  if ($Action -ne 'status' -and -not (Test-Admin)) {
    $out = @(Invoke-Elevated); $rc = [int]$out[$out.Count - 1]
  } else {
    $script:State = Read-State
    Resolve-Settings
    if ($Action -ne 'status') {
      $logs = Join-Path $MuRoot 'logs'
      if (-not (Test-Path $logs)) { New-Item -ItemType Directory -Force -Path $logs | Out-Null }
      try { Start-Transcript -Path (Join-Path $logs ("wsl2-host-$Action-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')) | Out-Null; $transcript = $true } catch { }
    }
    switch ($Action) {
      'install' { $out = @(Invoke-Install) }
      'status' { $out = @(Invoke-Status) }
      'uninstall' { $out = @(Invoke-Uninstall) }
      'test-stop' { $out = @(Invoke-TestStop) }
    }
    $rc = 1
    if ($out.Count -gt 0) { $rc = [int]$out[$out.Count - 1] }
  }
} catch {
  Write-Host ''
  Write-Host "mu-wsl2-host $Action failed: $($_.Exception.Message)" -ForegroundColor Red
  if ($_.InvocationInfo) { Write-Host "  at line $($_.InvocationInfo.ScriptLineNumber): $($_.InvocationInfo.Line.Trim())" -ForegroundColor DarkGray }
  $rc = 1
} finally {
  if ($transcript) { try { Stop-Transcript | Out-Null } catch { } }
  if ($null -ne $prevEncoding) { try { [Console]::OutputEncoding = $prevEncoding } catch { } }
  if ($Elevated) { Write-Host ''; [void](Read-Host 'Press Enter to close this window') }
}
exit $rc
