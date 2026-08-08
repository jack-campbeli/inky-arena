# Raspberry Pi Deployment and Rollback

This is a manual runbook. It does not discover the Pi, connect automatically, or assume that the checked-in service file matches the live device.

## Safety Rules

- Never print or share `config.toml`, `ARENA_TOKEN`, or a complete service environment.
- Stop if the repository path, branch, tracked worktree state, Git locks, service unit, or Python interpreter differs from expectations.
- Preserve untracked and ignored machine-specific files.
- Do not use `git reset --hard`, recursive deletion, or commands that overwrite `config.toml`.
- Record the live commit and installed Python packages before changing anything.
- Do not continue past a failing preflight, test, service check, or physical display check.

## 1. Connect to a Confirmed Device

On the operator machine, enter a hostname or address that has been independently confirmed to be the Inky Pi:

```bash
read -r INKY_PI_HOST
ssh "jcampbell@$INKY_PI_HOST"
```

If the login user is not `jcampbell`, stop and verify the intended account and checkout rather than substituting paths blindly.

## 2. Read-Only Device and Repository Preflight

Run on the Pi:

```bash
hostname
cd /home/jcampbell/inky-arena
pwd -P
git status --short --branch
git branch --show-current
git rev-parse HEAD
find .git -maxdepth 1 -name '*.lock' -print
git diff --quiet
git diff --cached --quiet
```

Expected conditions:

- `pwd -P` is `/home/jcampbell/inky-arena`.
- The intended deployment branch is `main`.
- `git diff --quiet` and `git diff --cached --quiet` both exit with status `0`.
- The lock search prints nothing.
- Ignored machine-specific files such as `config.toml` may exist, but must not be displayed or overwritten.

Stop if any expected condition is false. Resolve the discrepancy with the operator before fetching, installing, switching commits, or restarting the service.

## 3. Read-Only Service and Interpreter Preflight

Inspect only the unit and its current status:

```bash
systemctl --user cat inky-arena.service
systemctl --user show inky-arena.service -p FragmentPath -p ExecStart -p ActiveState -p SubState
systemctl --user status inky-arena.service --no-pager
```

The checked-in example currently names `/home/jcampbell/inky-app/.venv/bin/python`. Treat that as a value to verify, not as proof of the live interpreter.

Enter the exact Python executable shown by the live `ExecStart`, then validate it:

```bash
read -r INKY_SERVICE_PYTHON
test -x "$INKY_SERVICE_PYTHON"
"$INKY_SERVICE_PYTHON" --version
"$INKY_SERVICE_PYTHON" -c "import sys; print(sys.executable)"
```

Stop if the executable does not exist or the printed path is not the interpreter from the live service.

## 4. Record Rollback State

Before fetching or installing, record the current branch, commit, and package versions outside the checkout:

```bash
git branch --show-current > /tmp/inky-arena-predeploy-branch
git rev-parse HEAD > /tmp/inky-arena-predeploy-sha
touch /tmp/inky-arena-predeploy-requirements.txt
chmod 600 /tmp/inky-arena-predeploy-requirements.txt
"$INKY_SERVICE_PYTHON" -m pip list --format=freeze > /tmp/inky-arena-predeploy-requirements.txt
git cat-file -e "$(cat /tmp/inky-arena-predeploy-sha)^{commit}"
```

Confirm that `/tmp/inky-arena-predeploy-branch` contains `main`. Do not share the package snapshot; retain it only for rollback on the device.

## 5. Fast-Forward Update

Update without rewriting local history:

```bash
git fetch --prune origin
git merge --ff-only origin/main
git status --short --branch
git log -1 --oneline
```

Stop if the merge is not fast-forward-only or if tracked changes appear afterward.

## 6. Install and Test With the Service Interpreter

Use the previously verified interpreter:

```bash
"$INKY_SERVICE_PYTHON" -m pip install -r requirements-pi.txt
"$INKY_SERVICE_PYTHON" -m unittest discover tests/
```

Do not restart the service unless dependency installation and the complete test suite both succeed.

## 7. Restart and Verify

Restart the user service, then inspect its immediate state and recent logs:

```bash
systemctl --user restart inky-arena.service
systemctl --user status inky-arena.service --no-pager
journalctl --user -u inky-arena.service -n 80 --no-pager
```

Verification is incomplete until an operator confirms all of the following:

- The service is active and not entering a restart loop.
- The journal shows a completed Inky refresh rather than only a saved diagnostic preview.
- The physical panel displays the expected landscape frame.
- A later scheduled rotation also refreshes the physical panel successfully.
- No log output exposes a token or machine-specific configuration value.

If any check fails, use the rollback procedure below.

## 8. Non-Destructive Rollback

First confirm that the recorded commit still resolves:

```bash
git cat-file -e "$(cat /tmp/inky-arena-predeploy-sha)^{commit}"
```

Detach the device checkout at the recorded known-good commit. This does not delete ignored `config.toml` or other untracked machine-specific files:

```bash
git switch --detach "$(cat /tmp/inky-arena-predeploy-sha)"
"$INKY_SERVICE_PYTHON" -m pip install -r /tmp/inky-arena-predeploy-requirements.txt
systemctl --user restart inky-arena.service
systemctl --user status inky-arena.service --no-pager
journalctl --user -u inky-arena.service -n 80 --no-pager
```

Physically confirm that the previous display behavior is restored. If rollback verification also fails, stop; preserve the checkout and logs for diagnosis rather than applying further changes.

## 9. Return From a Detached Rollback

Only after the deployment failure is understood and a corrected version is ready, return deliberately to the recorded branch:

```bash
git switch "$(cat /tmp/inky-arena-predeploy-branch)"
git status --short --branch
```

Repeat the full preflight before another deployment attempt.
