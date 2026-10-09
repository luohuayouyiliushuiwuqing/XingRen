# stop_service.ps1 —— 杀掉 Almond 服务进程并释放端口
#
# 两步清场，防止多实例叠跑（曾出现 3 个实例同存、最老进程占着端口导致重启不生效）：
#   1. 按命令行清扫所有 almond 相关进程（不管谁启动的、占没占端口）
#   2. 按端口清扫监听者（兜底：任何占着这些端口的进程）
#
# 用法（项目根目录）：
#   powershell -ExecutionPolicy Bypass -File .\stop_service.ps1
#   powershell -ExecutionPolicy Bypass -File .\stop_service.ps1 -Ports 4000
#
param(
    [int[]]$Ports = @(4000, 8765)   # 看板 4000 / 选择器试验台 8765
)

$ErrorActionPreference = "SilentlyContinue"
$killed = @{}

# 1) 命令行里含 almond 的进程（python.exe 跑的 console 入口 / 直接起的 server）
Get-CimInstance Win32_Process |
    Where-Object {
        ($_.Name -eq "python.exe" -or $_.Name -eq "almond-webui.exe") -and
        $_.CommandLine -match "almond"
    } |
    ForEach-Object {
        if (-not $killed.ContainsKey($_.ProcessId)) {
            Stop-Process -Id $_.ProcessId -Force
            $killed[$_.ProcessId] = "命令行匹配 almond"
        }
    }

# 2) 占用指定端口的进程（兜底，杀掉一切监听者）
foreach ($port in $Ports) {
    Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        ForEach-Object {
            $procId = $_.OwningProcess
            if (-not $killed.ContainsKey($procId)) {
                Stop-Process -Id $procId -Force
                $killed[$procId] = "监听端口 $port"
            }
        }
}

Start-Sleep -Seconds 1

if ($killed.Count -eq 0) {
    Write-Output "没有发现 almond 进程或端口占用（$($Ports -join ', ')）"
} else {
    foreach ($entry in $killed.GetEnumerator()) {
        Write-Output "已杀掉 PID $($entry.Key)（$($entry.Value)）"
    }
}

# 3) 验证端口确实释放
$busy = @()
foreach ($port in $Ports) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        $busy += $port
    }
}
if ($busy) {
    Write-Output "⚠ 端口仍被占用：$($busy -join ', ')（可能是无 almond 命令行特征的进程，手动查 netstat -ano | findstr $($busy[0])）"
    exit 1
}
Write-Output "端口已释放：$($Ports -join ', ')"
exit 0
