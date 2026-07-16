# Combined M365 Portable Prototype

This is a separate prototype for a first-run, portable Windows app. It does not change the existing `CombinedM365` folder.

Runtime user data is stored under:

```text
%APPDATA%\VCIT\M365Tools
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

Build an exe folder:

```powershell
.\build_exe.ps1
```

The first launch shows a setup page if `%APPDATA%\VCIT\M365Tools\config.json` does not exist.

On the setup page:

1. Click `Create folders and config`.
2. Click `Add account credential`.
3. Enter the RTO name, admin email, domain, and password.
4. The app creates the `.cred` file and updates `config.json`.
5. Click `Open application`.
