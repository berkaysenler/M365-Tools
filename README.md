# M365 Tools

A portable Windows desktop app for managing Microsoft 365 student/user accounts across multiple tenants: bulk account creation, license and group checks, and optional Syncro MSP contact sync.

Runtime user data is stored under:

```text
%APPDATA%\M365Tools
```

That folder contains:

- `config.json`
- `student_accounts.json`
- `creds\`
- `logs\`
- `last_student_ids.csv`

Run locally:

```powershell
.\run_portable.ps1
```

Build a single-file exe:

```powershell
.\build_exe.ps1
```

The first launch shows a setup page if `%APPDATA%\M365Tools\config.json` does not exist.

On the setup page:

1. Click `Create folders and config`.
2. Click `Add account` for each tenant you manage (RTO name, admin email, domain).
3. Click `Open application`.

## Sign-in

No Entra app registration or client/tenant IDs are needed. The app signs in with a one-time device code per tenant using Microsoft's first-party client — Microsoft handles the password and MFA, and the session stays connected silently for ~90 days.

If one tenant's admin account also administers other tenants, list those tenants in the `Manages` field when adding the account (comma-separated RTO names). Managed tenants then authenticate through the managing tenant's connection instead of needing their own sign-in.

## Syncro integration (optional)

To sync new accounts as contacts to Syncro MSP, enter your Syncro subdomain and API key on the Settings page (stored in `%APPDATA%\M365Tools\.env`), and set a Syncro org ID per tenant when adding accounts.
