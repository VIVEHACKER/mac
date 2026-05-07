# Mac Autopush

This repository stores the app under `trading-copilot/`. Use the guarded push
script when you want local changes to land on `mac/main` without repeating the
manual sync, test, commit, and push steps.

## One-shot guarded push

From the repository root:

```powershell
.\scripts\push-trading-copilot-to-mac.ps1 -Message "Update trading copilot"
```

The script:

- verifies the `trading-copilot` test suite unless `-SkipTests` is passed
- runs `compileall`
- runs `git diff --check`
- stages only `trading-copilot/`
- commits if there are staged changes
- pushes only when `HEAD` is a fast-forward of `mac/main`

To sync from a separate app-root checkout first:

```powershell
.\scripts\push-trading-copilot-to-mac.ps1 `
  -SourceAppRoot C:\Users\gidc111\조롱이\trading-copilot `
  -Message "Update trading copilot"
```

## Post-commit autopush hook

Install the local hook:

```powershell
.\scripts\install-mac-autopush-hook.ps1
```

After installation, a commit made in this repository will run the guarded push
script. If tests fail, the hook refuses to push.

The hook is local machine configuration and is not committed to the repository.
