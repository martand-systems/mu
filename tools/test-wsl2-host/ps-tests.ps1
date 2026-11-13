# Tests for install/hosts/wsl2/mu-wsl2-host.ps1, run by tools/test-wsl2-host/run.sh with pwsh (any OS):
#   1. it parses, and uses no syntax or cmdlet that Windows PowerShell 5.1 lacks (pwsh accepts more than 5.1 does)
#   2. its pure functions (dot-sourced: the main part does not run): .wslconfig merge, argument quoting, task XML and
#      comparison, powercfg parsing, failure actions, PreshutdownOrder, the hook log, default memory
#   3. the embedded C# of the shutdown hook compiles as C# 5 (the in-box .NET Framework compiler's language version)
# Prints "ok NAME" / "FAIL NAME"; exits 1 on any failure.
param([Parameter(Mandatory = $true)][string]$Script)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$script:fails = 0
function ok([string]$Name) { Write-Output "ok    $Name" }
function bad([string]$Name, [string]$Why) { $script:fails++; Write-Output "FAIL  $Name"; if ($Why) { Write-Output "      $Why" } }
function eq([string]$Name, $Want, $Got) {
  $w = ($Want | Out-String).TrimEnd(); $g = ($Got | Out-String).TrimEnd()
  if ($w -ceq $g) { ok $Name } else { bad $Name ("want [" + $w + "] got [" + $g + "]") }
}

# ---- 1. syntax
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$tokens, [ref]$errors)
if ($errors.Count -eq 0) { ok 'parses' } else { bad 'parses' (($errors | ForEach-Object { "line $($_.Extent.StartLineNumber): $($_.Message)" }) -join '; ') }
$found = New-Object System.Collections.ArrayList
$ast.FindAll({ $true }, $true) | ForEach-Object {
  $n = $_; $t = $n.GetType().Name; $line = $n.Extent.StartLineNumber
  switch ($t) {
    'PipelineChainAst' { [void]$found.Add("line ${line}: && or || between commands") }
    'TernaryExpressionAst' { [void]$found.Add("line ${line}: ?: operator") }
    'BinaryExpressionAst' { if ($n.Operator -eq 'QuestionQuestion') { [void]$found.Add("line ${line}: ?? operator") } }
    'AssignmentStatementAst' { if ([string]$n.Operator -eq 'QuestionQuestionEquals') { [void]$found.Add("line ${line}: ??= operator") } }
    'MemberExpressionAst' { if ($n.PSObject.Properties['NullConditional'] -and $n.NullConditional) { [void]$found.Add("line ${line}: ?. operator") } }
    'InvokeMemberExpressionAst' { if ($n.PSObject.Properties['NullConditional'] -and $n.NullConditional) { [void]$found.Add("line ${line}: ?. operator") } }
    'IndexExpressionAst' { if ($n.PSObject.Properties['NullConditional'] -and $n.NullConditional) { [void]$found.Add("line ${line}: ?[] operator") } }
    'CommandAst' {
      $name = $n.GetCommandName()
      $params = @($n.CommandElements | Where-Object { $_ -is [System.Management.Automation.Language.CommandParameterAst] } | ForEach-Object { $_.ParameterName })
      $newer = @('Remove-Service', 'Get-Error', 'Test-Json', 'Join-String', 'Start-ThreadJob', 'ConvertFrom-Markdown', 'Get-Uptime')
      if ($newer -contains $name) { [void]$found.Add("line ${line}: $name (PowerShell 6+)") }
      if ($name -eq 'ForEach-Object' -and $params -contains 'Parallel') { [void]$found.Add("line ${line}: ForEach-Object -Parallel") }
      if ($name -eq 'ConvertFrom-Json' -and $params -contains 'AsHashtable') { [void]$found.Add("line ${line}: ConvertFrom-Json -AsHashtable") }
      if ($name -eq 'Join-Path' -and $params -contains 'AdditionalChildPath') { [void]$found.Add("line ${line}: Join-Path -AdditionalChildPath") }
      if ($name -eq 'Join-Path' -and @($n.CommandElements).Count -gt 3 -and $params.Count -eq 0) { [void]$found.Add("line ${line}: Join-Path with more than two paths") }
      if ($params -contains 'Encoding' -and $n.Extent.Text -match 'utf8NoBOM|utf8BOM') { [void]$found.Add("line ${line}: -Encoding utf8NoBOM") }
    }
  }
}
if ($found.Count -eq 0) { ok 'no syntax or cmdlets newer than Windows PowerShell 5.1' } else { bad 'Windows PowerShell 5.1 syntax' ($found -join '; ') }
# "$name:" in a string is a drive-qualified variable: an error, or worse, silently another variable
$bad = @($tokens | Where-Object { $_.Kind -eq 'StringExpandable' -and $_.Text -match '\$(?!env:|script:|global:|using:|null|true|false)[A-Za-z_]\w*:\w' })
if ($bad.Count -eq 0) { ok 'no "$var:x" drive-qualified variables by accident' } else { bad 'drive-qualified variables' (($bad | ForEach-Object { "line $($_.Extent.StartLineNumber)" }) -join ', ') }

# ---- 2. functions
. $Script
$Json = $false

# .wslconfig
$l = New-IniLines "# mine`r`n[wsl2]`r`nmemory=16GB`r`nkernelCommandLine = quiet`r`n`r`n[experimental]`r`nsparseVhd=true`r`n"
$r1 = Set-IniValue $l 'wsl2' 'memory' '23GB'
$r2 = Set-IniValue $l 'wsl2' 'processors' '20'
$r3 = Set-IniValue $l 'general' 'instanceIdleTimeout' '-1'
$r4 = Set-IniValue $l 'WSL2' 'Memory' '23 gb'
eq '.wslconfig: change a value, add a key to its section, add a section; keep the rest' `
  "# mine`r`n[wsl2]`r`nmemory=23GB`r`nkernelCommandLine = quiet`r`nprocessors=20`r`n`r`n[experimental]`r`nsparseVhd=true`r`n`r`n[general]`r`ninstanceIdleTimeout=-1`r`n" (ConvertTo-IniText $l)
eq '.wslconfig: what changed, and the old values' 'True 16GB | True  | True  | False 23GB' ("$($r1.Changed) $($r1.Old) | $($r2.Changed) $($r2.Old) | $($r3.Changed) $($r3.Old) | $($r4.Changed) $($r4.Old)")
$again = New-IniLines (ConvertTo-IniText $l)
$changed = @(@('wsl2', 'memory', '23GB'), @('wsl2', 'processors', '20'), @('general', 'instanceIdleTimeout', '-1')) | ForEach-Object { (Set-IniValue $again $_[0] $_[1] $_[2]).Changed }
eq '.wslconfig: a second run changes nothing' 'False False False' ($changed -join ' ')
eq '.wslconfig: read a value (names case-insensitive)' '20' (Get-IniValue $l 'WSL2' 'PROCESSORS')
eq '.wslconfig: a missing value is $null' 'True' ($null -eq (Get-IniValue $l 'wsl2' 'swap'))
eq '.wslconfig: a repeated key gives the first value (what WSL uses)' '8GB' (Get-IniValue (New-IniLines "[wsl2]`nmemory=8GB`nmemory=16GB`n") 'wsl2' 'memory')
$e = New-IniLines ''
[void](Set-IniValue $e 'general' 'instanceIdleTimeout' '-1'); [void](Set-IniValue $e 'wsl2' 'swap' '8GB')
eq '.wslconfig: from nothing' "[general]`r`ninstanceIdleTimeout=-1`r`n`r`n[wsl2]`r`nswap=8GB`r`n" (ConvertTo-IniText $e)
eq '.wslconfig: BOM and LF line endings' 'memory=1GB' ((New-IniLines ([string][char]0xFEFF + "[wsl2]`nmemory=1GB`n"))[1])
# uninstall: remove what was absent, restore what was there
[void](Remove-IniValue $l 'general' 'instanceIdleTimeout')
[void](Remove-IniValue $l 'wsl2' 'processors')
[void](Set-IniValue $l 'wsl2' 'memory' '16GB')
eq '.wslconfig: uninstall restores the file (an emptied section goes)' "# mine`r`n[wsl2]`r`nmemory=16GB`r`nkernelCommandLine = quiet`r`n`r`n[experimental]`r`nsparseVhd=true`r`n" (ConvertTo-IniText $l)
eq '.wslconfig: removing a missing key reports False' 'False' (Remove-IniValue $l 'wsl2' 'nothing')

# Windows command-line quoting (CommandLineToArgvW rules)
eq 'quoting: plain' 'mu-compute' (ConvertTo-WinArgument 'mu-compute')
eq 'quoting: spaces' '"C:\Program Files\x"' (ConvertTo-WinArgument 'C:\Program Files\x')
eq 'quoting: empty' '""' (ConvertTo-WinArgument '')
eq 'quoting: quotes and trailing backslashes' '"a\"b c\\"' (ConvertTo-WinArgument 'a"b c\')
eq 'quoting: a shell command' '"umask 022; cat > /x.new && mv -f /x.new /x"' (ConvertTo-WinArgument 'umask 022; cat > /x.new && mv -f /x.new /x')

# the task
$xmlText = Get-TaskXml 'S-1-5-21-1-2-3-1001' 'C:\Program Files\WSL\wsl.exe' 'mu-compute'
$x = [xml]$xmlText
$ns = New-Object System.Xml.XmlNamespaceManager($x.NameTable); $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
$get = { param($p) $x.SelectSingleNode($p, $ns).InnerText }
eq 'task XML: action' 'C:\Program Files\WSL\wsl.exe | -d mu-compute --exec /bin/true' ((& $get '//t:Exec/t:Command') + ' | ' + (& $get '//t:Exec/t:Arguments'))
eq 'task XML: at startup +30 s, every 5 min, indefinitely' 'PT30S PT5M false' ((& $get '//t:BootTrigger/t:Delay') + ' ' + (& $get '//t:BootTrigger/t:Repetition/t:Interval') + ' ' + (& $get '//t:BootTrigger/t:Repetition/t:StopAtDurationEnd'))
eq 'task XML: S4U, highest, the user SID' 'S4U HighestAvailable S-1-5-21-1-2-3-1001' ((& $get '//t:LogonType') + ' ' + (& $get '//t:RunLevel') + ' ' + (& $get '//t:UserId'))
eq 'task XML: 5 min limit, IgnoreNew' 'PT5M IgnoreNew' ((& $get '//t:Settings/t:ExecutionTimeLimit') + ' ' + (& $get '//t:MultipleInstancesPolicy'))
eq 'task XML: no Duration (repeats forever)' 'True' ($null -eq $x.SelectSingleNode('//t:Repetition/t:Duration', $ns))
eq 'task XML: names are escaped' 'True' ((Get-TaskXml 'S-1' 'C:\a&b\wsl.exe' 'x<y') -match '(?s)x&lt;y.*a&amp;b\\wsl\.exe')
# a registered task as Get-ScheduledTask shows it (fake CIM objects)
function New-FakeTask([string]$Delay = 'PT30S', [string]$Interval = 'PT5M', [string]$Duration = '', [string]$Logon = 'S4U', [string]$Arguments = '-d mu-compute --exec /bin/true') {
  [pscustomobject]@{
    Actions = @([pscustomobject]@{ Execute = '"C:\Program Files\WSL\wsl.exe"'; Arguments = $Arguments })
    Triggers = @([pscustomobject]@{ CimClass = [pscustomobject]@{ CimClassName = 'MSFT_TaskBootTrigger' }; Delay = $Delay; Enabled = $true
        Repetition = [pscustomobject]@{ Interval = $Interval; Duration = $Duration } })
    Principal = [pscustomobject]@{ LogonType = $Logon; RunLevel = 'Highest'; UserId = 'S-1-5-21-1-2-3-1001' }
    Settings = [pscustomobject]@{ ExecutionTimeLimit = 'PT5M'; MultipleInstances = 'IgnoreNew'; Enabled = $true; DisallowStartIfOnBatteries = $false; StopIfGoingOnBatteries = $false }
  }
}
eq 'task compare: the S3 hand-made task matches' '' ((Compare-StartupTask (New-FakeTask) 'S-1-5-21-1-2-3-1001' 'C:\Program Files\WSL\wsl.exe' 'mu-compute') -join '; ')
eq 'task compare: differences are named' "arguments '-d other --exec /bin/true'; logon type Password; repeats only for P1D; startup delay 'PT1M'" `
  ((Compare-StartupTask (New-FakeTask -Delay 'PT1M' -Duration 'P1D' -Logon 'Password' -Arguments '-d other --exec /bin/true') 'S-1-5-21-1-2-3-1001' 'C:\Program Files\WSL\wsl.exe' 'mu-compute' | Sort-Object) -join '; ')

# powercfg (English and German labels: only the hex values are used)
$en = @('Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)', '  Subgroup GUID: 238c9fa8-0aad-41ed-83f4-97be242c8f20  (Sleep)',
  '    Power Setting GUID: 29f6c1db-86da-48c5-9fdb-f2b67b1f44da  (Sleep after)', '      Minimum Possible Setting: 0x00000000',
  '      Maximum Possible Setting: 0xffffffff', '      Possible Settings increment: 0x00000001', '      Possible Settings units: Seconds',
  '    Current AC Power Setting Index: 0x00000708', '    Current DC Power Setting Index: 0x00000384', '')
eq 'powercfg: AC value (English)' '1800' (Get-PowercfgAcValue $en)
$de = $en -replace 'Current AC Power Setting Index', 'Index der aktuellen Wechselstromeinstellung' -replace 'Current DC Power Setting Index', 'Index der aktuellen Gleichstromeinstellung'
eq 'powercfg: AC value (German)' '1800' (Get-PowercfgAcValue $de)
eq 'powercfg: no values' 'True' ($null -eq (Get-PowercfgAcValue @('error')))

# failure actions as the registry stores them (20-byte header, then type/delay pairs)
function New-FailureBytes([int]$Reset, [int[]]$Delays) {
  $b = New-Object System.Collections.Generic.List[byte]
  $b.AddRange([BitConverter]::GetBytes([uint32]$Reset)); $b.AddRange([BitConverter]::GetBytes([uint32]0)); $b.AddRange([BitConverter]::GetBytes([uint32]0))
  $b.AddRange([BitConverter]::GetBytes([uint32]$Delays.Count)); $b.AddRange([BitConverter]::GetBytes([uint32]20))
  foreach ($d in $Delays) { $b.AddRange([BitConverter]::GetBytes([uint32]1)); $b.AddRange([BitConverter]::GetBytes([uint32]$d)) }
  return ,$b.ToArray()
}
eq 'failure actions: as set' 'True' (Test-FailureActionsBytes (New-FailureBytes 86400 @(10000, 10000, 60000)))
eq 'failure actions: other delays' 'False' (Test-FailureActionsBytes (New-FailureBytes 86400 @(60000, 0, 0)))
eq 'failure actions: none' 'False' (Test-FailureActionsBytes $null)

# PreshutdownOrder
eq 'PreshutdownOrder: put first' 'mu-wsl-shutdown,DeviceInstall,UsoSvc,gpsvc,trustedinstaller' ((Merge-PreshutdownOrder @('DeviceInstall', 'UsoSvc', 'mu-wsl-shutdown', 'gpsvc', 'trustedinstaller') 'mu-wsl-shutdown') -join ',')
eq 'PreshutdownOrder: from nothing' 'mu-wsl-shutdown' ((Merge-PreshutdownOrder @() 'mu-wsl-shutdown') -join ',')
eq 'PreshutdownOrder: one element stays an array' '1' (@(Merge-PreshutdownOrder $null 'mu-wsl-shutdown').Count)
eq 'PreshutdownOrder: removed again' 'DeviceInstall,UsoSvc' ((Remove-FromPreshutdownOrder @('mu-wsl-shutdown', 'DeviceInstall', 'UsoSvc') 'mu-wsl-shutdown') -join ',')

# memory default: 75% of the PC's memory (the office PC: 31.x GB visible -> 23 GB)
eq 'memory default: office PC' '23' (Get-DefaultMemoryGB (31.6 * 1GB))
eq 'memory default: 64 GB' '48' (Get-DefaultMemoryGB (64 * 1GB))
eq 'memory default: tiny' '2' (Get-DefaultMemoryGB (2 * 1GB))

# what the hook did at the last shutdown, from its log
$boot = [datetime]::SpecifyKind([datetime]'2026-11-13 09:20:33', 'Utc')
$clean = @('2026-11-12T08:00:00.000Z running: stops WSLService when Windows shuts down or restarts',
  '2026-11-13T09:19:40.100Z preshutdown: Windows is shutting down or restarting',
  '2026-11-13T09:19:40.120Z stopping WSLService (Running): WSL powers its distros off cleanly',
  '2026-11-13T09:19:44.300Z WSLService stopped after 4180 ms',
  '2026-11-13T09:21:01.000Z running: stops WSLService when Windows shuts down or restarts')
eq 'hook log: clean stop before this boot' 'pass|last shutdown: WSL stopped cleanly (WSLService stopped after 4180 ms)' ((Get-LastShutdownResult $clean $boot) -join '|')
$none = @($clean[0], $clean[4])
eq 'hook log: no preshutdown before this boot' 'warn' ((Get-LastShutdownResult $none $boot)[0])
$late = @($clean[0], $clean[1], $clean[2], '2026-11-13T09:21:00.000Z WSLService still StopPending after 80000 ms: giving up (Windows will stop the VM hard)')
eq 'hook log: gave up' 'fail' ((Get-LastShutdownResult ($late[0..2] + @('2026-11-13T09:20:00.000Z WSLService still StopPending after 80000 ms: giving up (Windows will stop the VM hard)')) $boot)[0])
eq 'hook log: nothing before this boot' 'info' ((Get-LastShutdownResult @($clean[4]) $boot)[0])
# whose WSL session a wsl.exe call from here would use
$svcStart = [datetime]::SpecifyKind([datetime]'2026-11-13 09:20:10', 'Utc')
eq 'WSL session: WSLService stopped' 'none none' ((Get-WslSessionSource 'Stopped' $null $null 0) + ' ' + (Get-WslSessionSource '' $null $null 0))
eq 'WSL session: the task ran after WSL started' 'task' (Get-WslSessionSource 'Running' $svcStart $svcStart.AddMinutes(5) 0)
eq 'WSL session: the task started WSL itself (a moment after its run began)' 'task' (Get-WslSessionSource 'Running' $svcStart $svcStart.AddSeconds(-1) 0)
eq 'WSL session: WSL restarted after the task last ran' 'unknown' (Get-WslSessionSource 'Running' $svcStart $svcStart.AddMinutes(-3) 0)
eq 'WSL session: the last run failed, never ran, or WSL is starting' 'unknown unknown unknown' ((Get-WslSessionSource 'Running' $svcStart $svcStart.AddMinutes(1) 1) + ' ' + (Get-WslSessionSource 'Running' $svcStart $null 0x41303) + ' ' + (Get-WslSessionSource 'Start Pending' $svcStart $svcStart.AddMinutes(1) 0))
eq 'dates: ISO string and DateTime' '2026-11-13T09:20:03Z 2026-11-13T09:20:03Z' ((Format-Utc (ConvertTo-UtcDate '2026-11-13T09:20:03Z')) + ' ' + (Format-Utc (ConvertTo-UtcDate ([datetime]::SpecifyKind([datetime]'2026-11-13 09:20:03', 'Utc')))))

# ---- 3. the shutdown hook's C# (C# 5; compiled here against .NET's reference assemblies, not run)
$src = $script:ShutdownServiceSource
if ($src -match '\$"|\?\.|nameof\(|=>\s*[^{]') { bad 'C#: no syntax newer than C# 5' 'string interpolation, ?., nameof or an expression-bodied member' } else { ok 'C#: no syntax newer than C# 5 (interpolation, ?., nameof, =>)' }
try {
  $refs = @('System.ServiceProcess.ServiceController', 'Microsoft.Win32.Registry', 'System.Console', 'System.Collections', 'System.Runtime.InteropServices', 'System.Threading', 'System.Threading.Thread', 'System.ComponentModel.Primitives', 'System.Diagnostics.Process', 'System.Runtime')
  $avail = @($refs | Where-Object { try { [void][System.Reflection.Assembly]::Load($_); $true } catch { $false } })
  Add-Type -TypeDefinition $src -Language CSharp -ReferencedAssemblies $avail -CompilerOptions '-langversion:5' -IgnoreWarnings -ErrorAction Stop
  ok 'C#: the shutdown hook compiles as C# 5'
} catch {
  bad 'C#: the shutdown hook compiles as C# 5' ($_.Exception.Message -replace '\s+', ' ')
}

if ($script:fails -gt 0) { exit 1 }
exit 0
