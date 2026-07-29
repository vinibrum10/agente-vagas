# Instala a tarefa agendada no Windows: tercas e quintas, 08:00.
# Uso: powershell -ExecutionPolicy Bypass -File instalar_agendamento.ps1

$dir = $PSScriptRoot
$py  = (Get-Command python).Source

$action   = New-ScheduledTaskAction -Execute $py -Argument "main.py --auto" -WorkingDirectory $dir
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday, Thursday -At 8:00AM
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName "AgenteVagas" -Action $action -Trigger $trigger -Settings $settings -Force
Write-Host "OK: tarefa 'AgenteVagas' agendada para ter/qui as 08:00."
