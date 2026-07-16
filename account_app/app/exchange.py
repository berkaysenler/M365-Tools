"""
Exchange Online PowerShell session via a persistent pwsh subprocess.

Auth: Connect-ExchangeOnline opens a browser window (or uses a cached MSAL
token silently). No password ever touches this app — Microsoft handles it.

Session lifecycle: one ExchangeSession per RTO, kept alive so the EXO
connection persists for the duration of the app run.
"""

import json
import os
import queue
import shutil
import subprocess
import threading
import time

SENTINEL = "##PYDONE##"
_LOG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug.log")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _log(msg):
    with open(_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")


def _powershell_exe() -> str:
    pwsh = shutil.which("pwsh")
    if pwsh:
        return pwsh

    windows_ps = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )
    if os.path.exists(windows_ps):
        return windows_ps

    powershell = shutil.which("powershell")
    if powershell:
        return powershell

    raise FileNotFoundError(
        "Could not find PowerShell. Install PowerShell or make sure "
        "powershell.exe is available on this computer."
    )


class ExchangeSession:
    def __init__(self, hide_window: bool = False):
        self._proc: subprocess.Popen | None = None
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._connected = False
        self._hide_window = hide_window

    # ------------------------------------------------------------------
    # Internal plumbing
    # ------------------------------------------------------------------

    def _start_process(self):
        # No -NonInteractive: some EXO cmdlets behave differently (Set-User in
        # particular fails with "server side error" against fresh mailboxes
        # under -NonInteractive while working fine in a normal pwsh window).
        # -NoLogo silences the banner; we keep the user's profile loaded.
        ps_exe = _powershell_exe()
        self._proc = subprocess.Popen(
            [ps_exe, "-NoLogo", "-ExecutionPolicy", "Bypass"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW if self._hide_window else 0,
        )

        def _reader():
            for line in self._proc.stdout:
                self._queue.put(line.rstrip())
            self._queue.put(None)  # EOF signal

        threading.Thread(target=_reader, daemon=True).start()

        # Drain any PS startup banner output
        time.sleep(0.3)
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        # Global session preferences — suppress warning/verbose/info output
        # so they never pollute JSON responses from data-fetch commands.
        # UTF-8 on both pipes: without it, PowerShell writes with the OEM
        # codepage and best-fits Unicode (smart quotes in display names) into
        # ASCII quotes, corrupting JSON output.
        prefs = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$OutputEncoding = [System.Text.Encoding]::UTF8; "
            "try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force } catch {}; "
            "$ErrorActionPreference  = 'Stop'; "
            "$WarningPreference      = 'SilentlyContinue'; "
            "$VerbosePreference      = 'SilentlyContinue'; "
            "$InformationPreference  = 'SilentlyContinue'; "
            "$ConfirmPreference      = 'None'"
        )
        self._send_raw(prefs)
        self._collect(timeout=10)

    def _send_raw(self, text: str):
        self._proc.stdin.write(text + "\n")
        self._proc.stdin.flush()

    def _readline(self, timeout=120) -> str:
        try:
            line = self._queue.get(timeout=timeout)
            if line is None:
                raise RuntimeError("PowerShell process ended unexpectedly")
            return line
        except queue.Empty:
            raise TimeoutError("Timed out waiting for PowerShell output")

    @staticmethod
    def _is_prompt(line: str) -> bool:
        """True if line is a PS prompt echo (should be ignored as output)."""
        return (
            (line.startswith("PS ") and ">" in line)
            or line.startswith(">> ")
        )

    @staticmethod
    def _is_confirm_prompt(line: str) -> bool:
        """True if a cmdlet is waiting at a PowerShell confirmation prompt."""
        return (
            "[Y] Yes" in line
            and "Help (default is" in line
        )

    def _drain(self):
        """Discard any stale lines sitting in the queue from a previous command."""
        while not self._queue.empty():
            try:
                stale = self._queue.get_nowait()
                _log(f"DRAIN: {str(stale)[:80]}")
            except queue.Empty:
                break

    def _collect(self, timeout=60) -> list[str]:
        """Send sentinel then collect output until sentinel arrives.
        Filters out PS prompt echo lines."""
        self._send_raw(f"Write-Output '{SENTINEL}'")
        lines = []
        while True:
            line = self._readline(timeout=timeout)
            # Prompt-echo check MUST come before the sentinel check: PS echoes
            # `PS ...> Write-Output '##PYDONE##'` back, and that echo contains
            # the sentinel literal, which would break the loop before the
            # command's actual output arrives.
            if self._is_prompt(line):
                _log(f"SKIP: {line[:80]}")
                continue
            if self._is_confirm_prompt(line):
                _log("AUTO-CONFIRM: replying Y to PowerShell confirmation prompt")
                self._send_raw("Y")
                continue
            if SENTINEL in line:
                break
            lines.append(line)
        return lines

    def _run(self, cmd: str, timeout=60) -> list[str]:
        """Run a single-line try/catch command; raise RuntimeError on PS errors."""
        short = cmd[:80].replace("\n", " ")
        _log(f"RUN: {short}")
        script = (
            f"try {{ {cmd} }} "
            f"catch {{ Write-Output ('PSERROR:' + $_.Exception.Message) }}"
        )
        with self._lock:
            self._drain()  # discard stale output from any previous command
            self._send_raw(script)
            lines = self._collect(timeout=timeout)
        _log(f"OUT ({len(lines)} lines): {str(lines[:3])[:200]}")
        errors = [l[8:] for l in lines if l.startswith("PSERROR:")]
        if errors:
            _log(f"PSERROR: {errors[0]}")
            raise RuntimeError(errors[0])
        return [l for l in lines if not l.startswith("PSERROR:")]

    def _run_json(self, cmd: str, timeout=60):
        """Run a command and parse its output as JSON.

        Skips any stray non-JSON lines (warnings that slipped through) —
        including lines that merely LOOK like JSON (e.g. '[WARNING] ...'):
        a failed parse is logged and the next candidate line is tried,
        instead of aborting the whole command.
        """
        lines = self._run(f"{cmd} | ConvertTo-Json -Depth 3 -Compress", timeout=timeout)
        for line in lines:
            stripped = line.strip()
            if stripped and stripped[0] in ("{", "["):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    context = stripped[max(0, exc.pos - 60):exc.pos + 60]
                    _log(
                        f"JSON parse failed (len {len(stripped)}, pos {exc.pos}): "
                        f"...{context!r}..."
                    )
                    continue
                return parsed if isinstance(parsed, list) else [parsed]
        return []

    def _run_json_lines(self, cmd: str, timeout=60):
        """Run a command emitting one JSON object per line (NDJSON).

        For large result sets. Robust against a single poisoned record
        (e.g. a display name whose characters break JSON encoding): the bad
        line is logged and skipped instead of losing the whole result.
        """
        lines = self._run(
            f"{cmd} | ForEach-Object {{ $_ | ConvertTo-Json -Depth 3 -Compress }}",
            timeout=timeout,
        )
        items = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped[0] not in ("{", "["):
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                _log(f"NDJSON line skipped (pos {exc.pos}): {stripped[:150]!r}")
                continue
            items.extend(parsed if isinstance(parsed, list) else [parsed])
        return items

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, admin_upn: str, on_code_ready, on_complete, access_token: str | None = None):
        """Connect to Exchange Online in a background thread.

        access_token (from our MSAL device-code flow) is required — auth is
        handled out-of-band so EXO never opens its own browser/MSAL cache.
        """
        def _run():
            try:
                self._start_process()

                out = self._run(
                    "if (!(Get-Module -ListAvailable ExchangeOnlineManagement)) "
                    "{ Write-Output 'MODULE_MISSING' }"
                )
                if any("MODULE_MISSING" in l for l in out):
                    on_complete(
                        "ExchangeOnlineManagement module not installed.\n\n"
                        "Run this in PowerShell:\n"
                        "  Install-Module ExchangeOnlineManagement"
                    )
                    return

                self._run("Import-Module ExchangeOnlineManagement")
                connect_flags = "-ShowBanner:$false"

                if not access_token:
                    on_complete(
                        "No access token available. Sign in via the device-code "
                        "dialog first."
                    )
                    return

                safe_upn = admin_upn.replace("'", "''")
                # Token is sent over stdin (PS subprocess), not via command
                # line / shell history. Single quotes block PS interpolation.
                safe_token = access_token.replace("'", "''")
                connect_script = (
                    f"try {{ "
                    f"Connect-ExchangeOnline -AccessToken '{safe_token}' "
                    f"-UserPrincipalName '{safe_upn}' "
                    f"{connect_flags} 6>&1; "
                    f"Write-Output 'CONNECTED_OK' "
                    f"}} catch {{ Write-Output ('CONNECT_ERROR:' + $_.Exception.Message) }}"
                )
                _log("Connecting with MSAL access token")

                with self._lock:
                    self._send_raw(connect_script)
                    self._send_raw(f"Write-Output '{SENTINEL}'")

                    while True:
                        line = self._readline(timeout=300)
                        _log(f"CONN: {line[:120]}")
                        # Skip PS prompt echoes before matching the sentinel —
                        # the echo `PS ...> Write-Output '##PYDONE##'` contains
                        # the sentinel literal and would otherwise short-circuit
                        # the auth wait.
                        if self._is_prompt(line):
                            continue
                        if SENTINEL in line:
                            break
                        if line.startswith("CONNECT_ERROR:"):
                            on_complete(line[14:])
                            return

                    # Flush any trailing output the EXO module emits AFTER
                    # the SENTINEL (post-connect init messages, prompt echoes).
                    # Without this drain, those leak into the next command.
                    time.sleep(1.0)
                    self._drain()

                # Verify by asking the EXO session itself — more reliable than
                # parsing a CONNECTED_OK marker out of a noisy output stream.
                try:
                    upn = self.get_connected_upn()
                except Exception as exc:
                    on_complete(f"Could not verify connection: {exc}")
                    return
                if not upn:
                    on_complete(
                        "Connection appeared to complete but the session has "
                        "no UPN. Likely the device-code sign-in was cancelled "
                        "or timed out. Check debug.log for EXO's output."
                    )
                    return
                _log(f"Verified connection as {upn}")

                self._connected = True
                on_complete(None)

            except Exception as exc:
                on_complete(str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def disconnect(self):
        self._connected = False
        if not self._proc:
            return
        try:
            with self._lock:
                self._send_raw("Disconnect-ExchangeOnline -Confirm:$false")
                self._collect(timeout=15)
        except Exception:
            pass
        try:
            self._send_raw("Exit")
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()
        self._proc = None

    @property
    def connected(self):
        return self._connected

    # ------------------------------------------------------------------
    # Data fetches
    # ------------------------------------------------------------------

    def get_connected_upn(self) -> str:
        """Return the UPN of the currently connected account."""
        try:
            info = self._run_json(
                "Get-ConnectionInformation | "
                "Select-Object @{n='upn';e={$_.UserPrincipalName}} -First 1",
                timeout=15,
            )
            return info[0].get("upn", "") if info else ""
        except Exception:
            return ""

    def verify_connection(self) -> tuple[bool, str]:
        """Quick sanity check that the EXO session can read tenant data.

        Returns (ok, detail). detail is the first accepted domain name on
        success, or an error string on failure. Lets us tell the difference
        between "authenticated handle but no data access" (e.g. admin lacks
        recipient roles) and a real working connection.
        """
        try:
            domains = self._run_json(
                "Get-AcceptedDomain | Select-Object -First 1 "
                "@{n='name';e={$_.DomainName}}",
                timeout=30,
            )
            if not domains:
                return False, "Connected, but tenant returned no accepted domains."
            return True, domains[0].get("name", "")
        except Exception as exc:
            return False, str(exc)

    def get_distribution_groups(self, progress=None):
        """Fetch all mail-enabled groups in a single Get-Recipient call.

        Get-UnifiedGroup is very slow (calls SharePoint internally). Using
        Get-Recipient filtered by RecipientTypeDetails returns DLs +
        mail-enabled security groups + Microsoft 365 groups in one shot,
        usually in under 30 seconds.

        progress(text) — optional callback, fires before the cmdlet runs.
        """
        if progress:
            progress("Fetching all mail-enabled groups...")
        groups = self._run_json_lines(
            "Get-Recipient -ResultSize Unlimited -RecipientTypeDetails "
            "MailUniversalDistributionGroup,MailUniversalSecurityGroup,GroupMailbox "
            "| Select-Object "
            "@{n='id';e={$_.PrimarySmtpAddress}}, "
            "@{n='displayName';e={$_.DisplayName}}, "
            "@{n='mail';e={$_.PrimarySmtpAddress}}, "
            "@{n='recipientType';e={$_.RecipientTypeDetails}}",
            timeout=300,
        )
        if progress:
            progress(f"Got {len(groups)} groups")
        return groups

    def get_users(self):
        """Fetch all mailbox users (used to build department list)."""
        return self._run_json(
            "Get-Mailbox -ResultSize Unlimited | "
            "Select-Object "
            "@{n='id';e={$_.ExternalDirectoryObjectId}}, "
            "@{n='displayName';e={$_.DisplayName}}, "
            "@{n='userPrincipalName';e={$_.UserPrincipalName}}, "
            "@{n='mail';e={$_.PrimarySmtpAddress}}, "
            "@{n='department';e={$_.CustomAttribute1}}",
            timeout=120,
        )

    def get_departments(self):
        """Fetch unique department values from user objects."""
        try:
            users = self._run_json(
                "Get-User -ResultSize Unlimited | "
                "Select-Object @{n='dept';e={$_.Department}} | "
                "Where-Object {$_.dept -ne $null -and $_.dept -ne ''}",
                timeout=120,
            )
            return sorted({u.get("dept", "") for u in users if u.get("dept")})
        except Exception:
            return []

    def account_exists(self, upn: str) -> bool:
        """Check whether a mailbox already exists for this UPN/email.

        Uses Get-EXOMailbox -Identity which is index-lookup fast (vs the
        -Filter form which scans). Returns False on any error/timeout —
        New-Mailbox will surface a real conflict if there is one.
        """
        safe = upn.replace("'", "")
        try:
            lines = self._run(
                f"try {{ "
                f"$null = Get-EXOMailbox -Identity '{safe}' -ErrorAction Stop; "
                f"Write-Output 'EXISTS' "
                f"}} catch {{ Write-Output 'NOT_FOUND' }}",
                timeout=60,
            )
            return any("EXISTS" in l for l in lines)
        except Exception as exc:
            _log(f"account_exists swallowed: {exc}")
            return False

    def search_users(self, query: str):
        """Live search users by display name using ANR (Ambiguous Name Resolution)."""
        safe = query.replace("'", "").replace('"', "")
        return self._run_json(
            f"Get-User -Anr '{safe}' -ResultSize 15 | "
            "Select-Object "
            "@{n='id';e={$_.ExternalDirectoryObjectId}}, "
            "@{n='displayName';e={$_.DisplayName}}, "
            "@{n='userPrincipalName';e={$_.UserPrincipalName}}, "
            "@{n='mail';e={$_.WindowsEmailAddress}}",
            timeout=30,
        )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def create_mailbox(self, account, temp_password: str) -> list[str]:
        """Create a cloud mailbox (and its Azure AD account) via New-Mailbox.

        Returns a list of warning strings. New-Mailbox failures raise; failures
        applying secondary properties (Title/Department/Office/Mobile/Manager)
        are returned as warnings instead — the mailbox itself was created, so
        we don't want the UI to say "Failed".

        Why this is messy: EXO often returns the generic "A server side error
        has occurred" for Set-User against a freshly-created mailbox, even
        after Get-User succeeds. The provisioning of a writable user object
        lags the readable one — sometimes by several minutes. We mitigate by
        (a) splitting -Office out to Set-Mailbox per sample.md (different
        backend, frequently succeeds when Set-User does not), (b) retrying
        the combined Set-User call with growing waits, and (c) falling back
        to per-property Set-User calls so a single bad field doesn't block
        the others.
        """
        warnings: list[str] = []
        upn = account.upn.replace("'", "")

        self._run(
            f"$pw = ConvertTo-SecureString '{temp_password}' -AsPlainText -Force"
        )
        self._run(
            f"New-Mailbox "
            f"-Name '{account.display_name}' "
            f"-DisplayName '{account.display_name}' "
            f"-FirstName '{account.first_name}' "
            f"-LastName '{account.last_name}' "
            f"-MicrosoftOnlineServicesID '{account.upn}' "
            f"-Password $pw "
            f"-ResetPasswordOnNextLogon $false "
            f"-Confirm:$false",
            timeout=300,
        )

        # Wait for the new user object to surface. Polls every 6s up to 60s —
        # this is the cheap precondition; the real wait happens via Set-User
        # retries below.
        for attempt in range(10):
            time.sleep(6)
            try:
                self._run(
                    f"$null = Get-User -Identity '{upn}' -ErrorAction Stop",
                    timeout=30,
                )
                _log(f"User object ready after {(attempt + 1) * 6}s")
                break
            except Exception as exc:
                _log(f"Waiting for user object ({attempt + 1}/10): {exc}")

        def _safe(value: str) -> str:
            return value.replace("'", "''")

        SERVER_SIDE_ERROR = "A server side error has occurred"

        def _try(cmd: str, label: str, waits=(0, 20, 40)) -> str | None:
            """Run cmd with a few short retries. Returns last error or None."""
            last_err = None
            for i, delay in enumerate(waits):
                if delay:
                    time.sleep(delay)
                try:
                    self._run(cmd, timeout=60)
                    return None
                except Exception as exc:
                    last_err = str(exc)
                    _log(f"{label} attempt {i + 1}/{len(waits)} failed: {last_err}")
            return last_err

        # Set-User properties (everything except Office, per sample.md).
        user_props = []
        if account.title:
            user_props.append(("Title", account.title))
        if account.department:
            user_props.append(("Department", account.department))
        if account.mobile:
            user_props.append(("MobilePhone", account.mobile))

        server_side_failed_props = []
        if user_props:
            combined = " ".join(f"-{k} '{_safe(v)}'" for k, v in user_props)
            err = _try(
                f"Set-User -Identity '{upn}' {combined} -Confirm:$false",
                label="Set-User combined",
            )
            if err:
                # Try per-property to isolate the bad field and to match the
                # old template's "set whatever Exchange accepts" behavior.
                _log("Set-User combined failed; falling back to per-property")
                for key, value in user_props:
                    per_err = _try(
                        f"Set-User -Identity '{upn}' -{key} '{_safe(value)}' "
                        f"-Confirm:$false",
                        label=f"Set-User {key}",
                    )
                    if per_err:
                        if SERVER_SIDE_ERROR in per_err:
                            server_side_failed_props.append(key)
                        warnings.append(f"Could not set {key}: {per_err}")

        if server_side_failed_props:
            fields = ", ".join(server_side_failed_props)
            warnings.append(
                f"Could not set {fields} via Set-User: Exchange Online "
                "returned its generic 'server side error'. This is a known "
                "limitation when Set-User writes to a cloud-only Azure AD "
                "user — the proper fix is Microsoft Graph (see "
                "GRAPH_API_MIGRATION.md). For now, set these fields in the "
                "M365 admin centre."
            )

        if account.manager_upn:
            try:
                self._run(
                    f"$null = Get-User -Identity '{_safe(account.manager_upn)}' "
                    f"-ErrorAction Stop",
                    timeout=30,
                )
                err = _try(
                    f"Set-User -Identity '{upn}' "
                    f"-Manager '{_safe(account.manager_upn)}' -Confirm:$false",
                    label="Set-User Manager",
                )
                if err:
                    warnings.append(
                        f"Manager was not set ({account.manager_upn}): {err}"
                    )
            except Exception:
                warnings.append(
                    f"Manager was not set: '{account.manager_upn}' was not found. "
                    "The account was created and the other details were still applied."
                )

        # Office goes via Set-Mailbox (different backend; sample.md does this).
        # This often succeeds where Set-User does not.
        if account.office_location:
            err = _try(
                f"Set-Mailbox -Identity '{upn}' "
                f"-Office '{_safe(account.office_location)}' "
                f"-Confirm:$false",
                label="Set-Mailbox -Office",
            )
            if err:
                warnings.append(f"Could not set Office: {err}")

        return warnings

    def add_to_groups(self, upn: str, groups: list) -> list[str]:
        """Add user to each group; return list of error strings for any failures.

        Microsoft 365 groups (GroupMailbox) need Add-UnifiedGroupLinks;
        traditional DLs and mail-enabled security groups use
        Add-DistributionGroupMember.
        """
        errors = []
        for group in groups:
            identity = group.get("mail") or group.get("id", "")
            rtype = (group.get("recipientType") or "").lower()
            try:
                if "groupmailbox" in rtype:
                    self._run(
                        f"Add-UnifiedGroupLinks -Identity '{identity}' "
                        f"-LinkType Members -Links '{upn}' -Confirm:$false"
                    )
                else:
                    self._run(
                        f"Add-DistributionGroupMember -Identity '{identity}' "
                        f"-Member '{upn}' -Confirm:$false"
                    )
            except Exception as exc:
                errors.append(f"{group.get('displayName', identity)}: {exc}")
        return errors

    def check_students(self, rto: str, student_ids: list[str], group_name: str, progress, should_stop=None) -> None:
        """Run the student checker using this existing EXO session.

        Emits the same log line shape as the old check_students.ps1 script so
        the existing Student Checker UI, tallying, issue report, and last-ID
        tracking continue to work.
        """
        def _safe(value: str) -> str:
            return value.replace("'", "''")

        progress(f"[{rto}] Using shared Exchange connection.")
        progress(f"[{rto}] Found {len(student_ids)} students to check.")
        progress(f"[{rto}] Fetching group members for: {group_name} ...")

        group_members = set()
        group_email = ""
        try:
            group = self._run_json(
                f"Get-DistributionGroup -Identity '{_safe(group_name)}' "
                "| Select-Object @{n='mail';e={$_.PrimarySmtpAddress}}",
                timeout=60,
            )
            group_email = group[0].get("mail", "") if group else ""
            members = self._run_json(
                f"Get-DistributionGroupMember -Identity '{_safe(group_name)}' "
                "-ResultSize Unlimited | "
                "Select-Object @{n='mail';e={$_.PrimarySmtpAddress}}",
                timeout=180,
            )
            group_members = {
                (member.get("mail") or "").lower()
                for member in members
                if member.get("mail")
            }
            progress(f"[{rto}] Loaded {len(group_members)} group members. Checking students...")
        except Exception as exc:
            progress(f"[{rto}] WARNING: Could not load group '{group_name}' - {exc}")
            progress(f"[{rto}] Continuing without group check...")

        for student_id in student_ids:
            if should_stop and should_stop():
                progress(f"[{rto}] Stopped.")
                return
            sid = student_id.strip()
            if not sid:
                continue
            safe_sid = _safe(sid)
            try:
                recipients = self._run_json(
                    f"Get-EXORecipient -Filter \"DisplayName -like '*{safe_sid}*'\" "
                    "-RecipientTypeDetails UserMailbox -ResultSize 1 | "
                    "Select-Object "
                    "@{n='mail';e={$_.PrimarySmtpAddress}}, "
                    "@{n='displayName';e={$_.DisplayName}}",
                    timeout=60,
                )
            except Exception as exc:
                progress(f"[{rto}] StudentID: {sid} | ERROR: {exc} ")
                continue

            if not recipients:
                users = self._run_json(
                    f"Get-User -Filter \"DisplayName -like '*{safe_sid}*'\" "
                    "| Select-Object -First 1 @{n='department';e={$_.Department}}",
                    timeout=60,
                )
                if users:
                    department = users[0].get("department") or ""
                    progress(
                        f"[{rto}] StudentID: {sid} | Account: EXISTS (NO LICENSE) "
                        f"| Has Group: No | Department: {department}"
                    )
                else:
                    progress(
                        f"[{rto}] StudentID: {sid} | Account: MISSING "
                        "| Has Group: No | Department: N/A"
                    )
                continue

            mail = recipients[0].get("mail") or ""
            department = "Not set"
            if mail:
                try:
                    details = self._run_json(
                        f"Get-User -Identity '{_safe(mail)}' "
                        "| Select-Object @{n='department';e={$_.Department}}",
                        timeout=60,
                    )
                    if details and details[0].get("department"):
                        department = details[0].get("department")
                except Exception:
                    pass

            has_group = bool(mail and mail.lower() in group_members)
            if has_group and group_email:
                group_info = f"Yes - {group_email}"
            elif has_group:
                group_info = "Yes"
            else:
                group_info = "No"
            progress(
                f"[{rto}] StudentID: {sid} | Account: EXISTS "
                f"| Has Group: {group_info} | Department: {department}"
            )
