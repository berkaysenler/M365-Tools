# =============================================================================
# check_students.ps1  - called by student_checker.py
# Password decrypted from .cred file by Python host; passed via EXO_PASSWORD env var.
# =============================================================================
param(
    [Parameter(Mandatory)][string]$RTO,
    [Parameter(Mandatory)][string]$CsvPath,
    [Parameter(Mandatory)][string]$UPN,
    [string]$GroupName = "All Students",
    [string]$IDColumn  = "StudentID",
    [switch]$DisableWAM
)

$ErrorActionPreference = "Stop"

function Log($msg) { Write-Output $msg }

if ($DisableWAM) {
    $env:MSAL_DISABLE_WAM = "1"
    Log "[$RTO] WAM disabled for this account."
} else {
    Remove-Item Env:\MSAL_DISABLE_WAM -ErrorAction SilentlyContinue
}
# ---- Load module ------------------------------------------------------------
Import-Module ExchangeOnlineManagement -ErrorAction SilentlyContinue
if (-not (Get-Command Connect-ExchangeOnline -ErrorAction SilentlyContinue)) {
    Log "[$RTO] ERROR: ExchangeOnlineManagement module not found."
    Log "[$RTO] Fix: Install-Module ExchangeOnlineManagement -Scope CurrentUser"
    exit 1
}

# ---- Connect ----------------------------------------------------------------
Log "[$RTO] Connecting as $UPN ..."
try {
    $plainPwd = $env:EXO_PASSWORD
    if (-not [string]::IsNullOrEmpty($plainPwd)) {
        Log "[$RTO] Auth mode: saved credential."
        $secStr = New-Object System.Security.SecureString
        foreach ($c in $plainPwd.ToCharArray()) { $secStr.AppendChar($c) }
        $secStr.MakeReadOnly()
        $cred = New-Object System.Management.Automation.PSCredential($UPN, $secStr)
        if ($DisableWAM) {
            Connect-ExchangeOnline -Credential $cred -ShowBanner:$false -DisableWAM -ErrorAction Stop
        } else {
            Connect-ExchangeOnline -Credential $cred -ShowBanner:$false -ErrorAction Stop
        }
    } else {
        Log "[$RTO] Auth mode: Microsoft sign-in."
        if ($DisableWAM) {
            Connect-ExchangeOnline -UserPrincipalName $UPN -ShowBanner:$false -DisableWAM -ErrorAction Stop
        } else {
            Connect-ExchangeOnline -UserPrincipalName $UPN -ShowBanner:$false -ErrorAction Stop
        }
    }
    Log "[$RTO] Connected."
} catch {
    Log "[$RTO] ERROR: Connection failed -$_"
    if (-not [string]::IsNullOrEmpty($env:EXO_PASSWORD)) {
        Log "[$RTO] If this direct saved-credential login also fails, clear the Cred File for this account and use Microsoft sign-in/MFA."
    }
    exit 1
}

# ---- Import CSV -------------------------------------------------------------
if (-not (Test-Path $CsvPath)) {
    Log "[$RTO] ERROR: CSV not found at $CsvPath"
    Disconnect-ExchangeOnline -Confirm:$false | Out-Null
    exit 1
}

try {
    $students = Import-Csv -Path $CsvPath
} catch {
    Log "[$RTO] ERROR: Failed to read CSV -$_"
    Disconnect-ExchangeOnline -Confirm:$false | Out-Null
    exit 1
}
Log "[$RTO] Found $($students.Count) students to check."

# Verify the ID column exists
if ($students.Count -gt 0) {
    $cols = $students[0].PSObject.Properties.Name
    if ($IDColumn -notin $cols) {
        Log "[$RTO] WARNING: Column '$IDColumn' not found in CSV. Available columns: $($cols -join ', ')"
        Log "[$RTO] Edit the account and set the correct Student ID Column name."
        Disconnect-ExchangeOnline -Confirm:$false | Out-Null
        exit 1
    }
}
Log "[$RTO] Using column: $IDColumn"

# ---- Load group members -----------------------------------------------------
Log "[$RTO] Fetching group members for: $GroupName ..."

$groupMembers = @{}
$groupEmail   = $null
try {
    $groupEmail = (Get-DistributionGroup -Identity $GroupName -ErrorAction Stop).PrimarySmtpAddress
    Get-DistributionGroupMember -Identity $GroupName -ResultSize Unlimited | ForEach-Object {
        if ($_.PrimarySmtpAddress) {
            $groupMembers[$_.PrimarySmtpAddress.ToLower()] = $true
        }
    }
    Log "[$RTO] Loaded $($groupMembers.Count) group members. Checking students..."
} catch {
    Log "[$RTO] WARNING: Could not load group '$GroupName' -$_"
    Log "[$RTO] Continuing without group check..."
}

# ---- Check students ---------------------------------------------------------
foreach ($row in $students) {
    $StudentID = $row.$IDColumn
    if ([string]::IsNullOrWhiteSpace($StudentID)) { continue }
    $StudentID = $StudentID.Trim()

    try {
        $student = Get-EXORecipient -Filter "DisplayName -like '*$StudentID*'" `
                   -RecipientTypeDetails UserMailbox -ResultSize 1 -ErrorAction SilentlyContinue
    } catch {
        Log "[$RTO] StudentID: $StudentID | ERROR: $_ "
        continue
    }

    if (-not $student) {
        $userExists = Get-User -Filter "DisplayName -like '*$StudentID*'" -ErrorAction SilentlyContinue
        if ($userExists) {
            Log "[$RTO] StudentID: $StudentID | Account: EXISTS (NO LICENSE) | Has Group: No | Department: $($userExists.Department)"
        } else {
            Log "[$RTO] StudentID: $StudentID | Account: MISSING | Has Group: No | Department: N/A"
        }
        continue
    }

    $userDetails = Get-User -Identity $student.PrimarySmtpAddress -ErrorAction SilentlyContinue
    $department  = if ($userDetails.Department) { $userDetails.Department } else { "Not set" }
    $hasGroup    = $groupMembers.ContainsKey($student.PrimarySmtpAddress.ToLower())
    $groupInfo   = if ($hasGroup -and $groupEmail) { "Yes - $groupEmail" } elseif ($hasGroup) { "Yes" } else { "No" }

    Log "[$RTO] StudentID: $StudentID | Account: EXISTS | Has Group: $groupInfo | Department: $department"
}

# ---- Disconnect -------------------------------------------------------------
Disconnect-ExchangeOnline -Confirm:$false | Out-Null
Log "[$RTO] Disconnected."
