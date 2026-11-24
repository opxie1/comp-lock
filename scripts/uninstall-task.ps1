#Requires -RunAsAdministrator
<#
.SYNOPSIS
Removes the scheduled task registered by install-task.ps1.

.DESCRIPTION
This leaves any lock in progress alone. Use `complock stop` first if a lock is
still running, or it will stay in place with nothing watching it.
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'comp-lock resume'
)

$ErrorActionPreference = 'Stop'

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "No task named '$TaskName' is registered."
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed the task '$TaskName'."
