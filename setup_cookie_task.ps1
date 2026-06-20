# Create a Windows scheduled task to refresh Instagram cookies every 4 hours.
# Run interactively so the browser window is visible when manual login is needed.

$taskName = "Instagram Cookie Refresh"
$batchPath = "C:\Users\skang\telebot\run_cookie_refresh.bat"
$workDir = "C:\Users\skang\telebot"

$action = New-ScheduledTaskAction -Execute $batchPath -WorkingDirectory $workDir

# Start now, repeat every 4 hours, for the next 10 years
$startTime = (Get-Date).AddMinutes(1)
$trigger = New-ScheduledTaskTrigger -Once -At $startTime -RepetitionInterval (New-TimeSpan -Hours 4) -RepetitionDuration (New-TimeSpan -Days 3650)

# Run as current user, interactive (browser window visible)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -WakeToRun

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force
Write-Host "Scheduled task '$taskName' created. It will run every 4 hours."
