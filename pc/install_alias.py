"""Install or remove the `opencode --pet` shell wrapper for PowerShell and Bash."""

import argparse
import ctypes
import os
from pathlib import Path
import re
import sys
import tempfile

START_MARKER = "# >>> openbuddy opencode alias >>>"
END_MARKER = "# <<< openbuddy opencode alias <<<"
BASH_LOADER_START = "# >>> openbuddy bashrc loader >>>"
BASH_LOADER_END = "# <<< openbuddy bashrc loader <<<"


def default_repo_root():
    return Path(__file__).resolve().parents[1]


def default_python_executable(repo_root=None):
    root = Path(repo_root).expanduser().resolve() if repo_root is not None else default_repo_root()
    candidates = [
        root / ".venv/Scripts/python.exe",
        root / ".venv/bin/python",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return Path(sys.executable).resolve()


def windows_documents_dir():
    """Return the user's Windows Documents folder (supporting redirected paths)."""
    if sys.platform == "win32":
        try:
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"


def default_powershell_profiles():
    if sys.platform == "win32":
        docs = windows_documents_dir()
        return [
            docs / "PowerShell/Microsoft.PowerShell_profile.ps1",
            docs / "WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
        ]
    return [Path.home() / ".config/powershell/Microsoft.PowerShell_profile.ps1"]


def default_bash_profiles():
    home = Path.home()
    return home / ".bashrc", home / ".bash_profile"


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _sh_quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def render_powershell_block(repo_root, python_exe):
    """Render the managed PowerShell `opencode` function block (pure ASCII)."""
    root_str = _ps_quote(str(Path(repo_root)))
    python_str = _ps_quote(str(Path(python_exe)))
    lines = [
        START_MARKER,
        "function opencode {",
        "    $usePet = $false",
        "    $useOverlay = $false",
        "    $afterDoubleDash = $false",
        "    $forwardArgs = [System.Collections.Generic.List[object]]::new()",
        "    foreach ($arg in $args) {",
        "        $text = [string]$arg",
        "        if (-not $afterDoubleDash -and $text -ceq '--') {",
        "            $afterDoubleDash = $true",
        "            $forwardArgs.Add($arg)",
        "        } elseif (-not $afterDoubleDash -and ($text -ieq '--pet' -or $text -ieq '-pet')) {",
        "            $usePet = $true",
        "        } elseif (-not $afterDoubleDash -and ($text -ieq '--pet-overlay' -or $text -ieq '-pet-overlay')) {",
        "            $usePet = $true",
        "            $useOverlay = $true",
        "        } else {",
        "            $forwardArgs.Add($arg)",
        "        }",
        "    }",
        "    if ($usePet) {",
        "        $obRoot = " + root_str,
        "        $obPython = " + python_str,
        "        if (-not (Test-Path -LiteralPath $obPython)) {",
        "            $obPython = 'python'",
        "        }",
        "        $ensureArgs = @('-P', '-m', 'pc.opencode', 'ensure')",
        "        if ($useOverlay) {",
        "            $ensureArgs += '--overlay'",
        "        }",
        "        $prevPythonPath = $env:PYTHONPATH",
        "        try {",
        "            $env:PYTHONPATH = $obRoot",
        "            & $obPython @ensureArgs",
        "            if ($LASTEXITCODE -ne 0) {",
        "                Write-Warning 'OpenBuddy bridge could not be started (continuing with opencode).'",
        "            }",
        "        } finally {",
        "            if ($null -eq $prevPythonPath) {",
        "                Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue",
        "            } else {",
        "                $env:PYTHONPATH = $prevPythonPath",
        "            }",
        "        }",
        "    }",
        "    $realOpencode = Get-Command -Name 'opencode' -CommandType Application, ExternalScript -ErrorAction SilentlyContinue | Select-Object -First 1",
        "    if (-not $realOpencode) {",
        "        Write-Error 'opencode command not found in PATH.'",
        "        $global:LASTEXITCODE = 1",
        "        return",
        "    }",
        "    $forwardArray = $forwardArgs.ToArray()",
        "    & $realOpencode.Source @forwardArray",
        "}",
        END_MARKER,
    ]
    return "\n".join(lines)


def render_bash_block(repo_root, python_exe):
    """Render the managed Bash `opencode` function block (pure ASCII)."""
    root_str = _sh_quote(Path(repo_root).as_posix())
    python_str = _sh_quote(Path(python_exe).as_posix())
    lines = [
        START_MARKER,
        "opencode() {",
        "    local use_pet=0",
        "    local use_overlay=0",
        "    local after_double_dash=0",
        "    local -a forward_args=()",
        "    local arg",
        '    for arg in "$@"; do',
        '        if [ "$after_double_dash" -eq 0 ] && [ "$arg" = "--" ]; then',
        "            after_double_dash=1",
        '            forward_args+=("$arg")',
        '        elif [ "$after_double_dash" -eq 0 ] && { [ "$arg" = "--pet" ] || [ "$arg" = "-pet" ]; }; then',
        "            use_pet=1",
        '        elif [ "$after_double_dash" -eq 0 ] && { [ "$arg" = "--pet-overlay" ] || [ "$arg" = "-pet-overlay" ]; }; then',
        "            use_pet=1",
        "            use_overlay=1",
        "        else",
        '            forward_args+=("$arg")',
        "        fi",
        "    done",
        '    if [ "$use_pet" -eq 1 ]; then',
        "        local ob_root=" + root_str,
        "        local ob_python=" + python_str,
        '        if [ ! -f "$ob_python" ]; then',
        "            if command -v python >/dev/null 2>&1; then",
        "                ob_python=python",
        "            else",
        "                ob_python=python3",
        "            fi",
        "        fi",
        "        local -a ensure_args=(-P -m pc.opencode ensure)",
        '        if [ "$use_overlay" -eq 1 ]; then',
        "            ensure_args+=(--overlay)",
        "        fi",
        '        if ! PYTHONPATH="$ob_root" "$ob_python" "${ensure_args[@]}"; then',
        '            echo "Warning: OpenBuddy bridge could not be started (continuing with opencode)." >&2',
        "        fi",
        "    fi",
        '    command opencode ${forward_args[@]+"${forward_args[@]}"}',
        "}",
        END_MARKER,
    ]
    return "\n".join(lines)


def render_bash_profile_loader():
    return "\n".join([
        BASH_LOADER_START,
        'if [ -f "$HOME/.bashrc" ]; then',
        '    . "$HOME/.bashrc"',
        "fi",
        BASH_LOADER_END,
    ])


def _write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".openbuddy-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _block_pattern(start_marker, end_marker):
    return re.compile(
        r"(?ms)^" + re.escape(start_marker) + r"\r?\n.*?^" + re.escape(end_marker) + r"[ \t]*(?:\r?\n)?"
    )


def upsert_managed_block(path, block, start_marker=START_MARKER, end_marker=END_MARKER):
    """Insert or update a marker-delimited block in a shell profile without touching other lines."""
    target = Path(path).expanduser()
    if target.is_symlink():
        raise ValueError("Refusing to modify symlinked profile: %s" % target)
    if target.exists() and not target.is_file():
        raise ValueError("Profile path is not a regular file: %s" % target)

    existing = target.read_bytes().decode("utf-8") if target.exists() else ""
    newline = "\r\n" if "\r\n" in existing else "\n"
    normalized_block = block.replace("\r\n", "\n").replace("\n", newline) + newline

    pattern = _block_pattern(start_marker, end_marker)
    if pattern.search(existing):
        updated = pattern.sub(lambda _match: normalized_block, existing, count=1)
    elif not existing:
        updated = normalized_block
    else:
        separator = "" if existing.endswith(("\n", "\r")) else newline
        if existing.strip() and not existing.endswith(("\n\n", "\r\n\r\n")):
            separator += newline
        updated = existing + separator + normalized_block

    if updated == existing and target.exists():
        return {"path": str(target), "changed": False}
    _write_atomic(target, updated)
    return {"path": str(target), "changed": True}


def remove_managed_block(path, start_marker=START_MARKER, end_marker=END_MARKER):
    """Remove a marker-delimited block from a shell profile if present."""
    target = Path(path).expanduser()
    if not target.exists():
        return {"path": str(target), "changed": False}
    if target.is_symlink():
        raise ValueError("Refusing to modify symlinked profile: %s" % target)
    existing = target.read_bytes().decode("utf-8")
    pattern = _block_pattern(start_marker, end_marker)
    if not pattern.search(existing):
        return {"path": str(target), "changed": False}
    newline = "\r\n" if "\r\n" in existing else "\n"
    updated = pattern.sub("", existing, count=1)
    updated = re.sub(r"(\r?\n){3,}", newline * 2, updated).lstrip("\r\n")
    if updated and not updated.endswith(("\n", "\r")):
        updated += newline
    _write_atomic(target, updated)
    return {"path": str(target), "changed": True}


def _bash_profile_sources_bashrc(text):
    return bool(re.search(r"(?m)^\s*(?:source|\.)\s+[^\n#]*\.bashrc\b", text)) or ".bashrc" in text


def install_aliases(shell="all", repo_root=None, python_exe=None,
                    powershell_profiles=None, bash_rc=None, bash_profile=None):
    """Install the `opencode --pet` wrapper in the requested shell profiles."""
    root = Path(repo_root).expanduser().resolve() if repo_root is not None else default_repo_root()
    python_path = Path(python_exe).expanduser().resolve() if python_exe is not None else default_python_executable(root)
    results = []

    if shell in ("all", "powershell"):
        ps_block = render_powershell_block(root, python_path)
        targets = powershell_profiles if powershell_profiles is not None else default_powershell_profiles()
        seen = set()
        for target in targets:
            resolved = Path(target).expanduser()
            key = str(resolved).lower() if sys.platform == "win32" else str(resolved)
            if key in seen:
                continue
            seen.add(key)
            res = upsert_managed_block(resolved, ps_block)
            res["shell"] = "powershell"
            results.append(res)

    if shell in ("all", "bash"):
        default_rc, default_profile = default_bash_profiles()
        rc_path = Path(bash_rc).expanduser() if bash_rc is not None else default_rc
        profile_path = Path(bash_profile).expanduser() if bash_profile is not None else default_profile

        bash_block = render_bash_block(root, python_path)
        rc_res = upsert_managed_block(rc_path, bash_block)
        rc_res["shell"] = "bash"
        results.append(rc_res)

        existing_profile = profile_path.read_text(encoding="utf-8") if profile_path.is_file() else ""
        has_managed_loader = BASH_LOADER_START in existing_profile
        if has_managed_loader or not _bash_profile_sources_bashrc(existing_profile):
            loader_res = upsert_managed_block(
                profile_path,
                render_bash_profile_loader(),
                start_marker=BASH_LOADER_START,
                end_marker=BASH_LOADER_END,
            )
            loader_res["shell"] = "bash"
            results.append(loader_res)

    return results


def uninstall_aliases(shell="all", powershell_profiles=None, bash_rc=None, bash_profile=None):
    """Remove the managed `opencode --pet` wrapper blocks from shell profiles."""
    results = []

    if shell in ("all", "powershell"):
        targets = powershell_profiles if powershell_profiles is not None else default_powershell_profiles()
        for target in targets:
            res = remove_managed_block(target)
            res["shell"] = "powershell"
            results.append(res)

    if shell in ("all", "bash"):
        default_rc, default_profile = default_bash_profiles()
        rc_path = Path(bash_rc).expanduser() if bash_rc is not None else default_rc
        profile_path = Path(bash_profile).expanduser() if bash_profile is not None else default_profile

        rc_res = remove_managed_block(rc_path)
        rc_res["shell"] = "bash"
        results.append(rc_res)

        loader_res = remove_managed_block(
            profile_path,
            start_marker=BASH_LOADER_START,
            end_marker=BASH_LOADER_END,
        )
        loader_res["shell"] = "bash"
        results.append(loader_res)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Install or remove the `opencode --pet` shell alias for PowerShell and Bash",
    )
    parser.add_argument("--shell", choices=("all", "powershell", "bash"), default="all",
                        help="Target shell(s) to configure (default: all)")
    parser.add_argument("--uninstall", action="store_true",
                        help="Remove previously installed OpenBuddy alias blocks")
    parser.add_argument("--repo-root", type=Path, help="Override OpenBuddy repository root path")
    parser.add_argument("--python", type=Path, help="Override Python interpreter used for pc.opencode")
    parser.add_argument("--powershell-profile", type=Path, action="append",
                        help="Override PowerShell profile path (may be passed multiple times)")
    parser.add_argument("--bash-rc", type=Path, help="Override ~/.bashrc path")
    parser.add_argument("--bash-profile", type=Path, help="Override ~/.bash_profile path")
    args = parser.parse_args()

    try:
        if args.uninstall:
            results = uninstall_aliases(
                shell=args.shell,
                powershell_profiles=args.powershell_profile,
                bash_rc=args.bash_rc,
                bash_profile=args.bash_profile,
            )
            for item in results:
                status = "Removed" if item["changed"] else "Not present"
                print("[%s] %s: %s" % (item["shell"], status, item["path"]))
        else:
            results = install_aliases(
                shell=args.shell,
                repo_root=args.repo_root,
                python_exe=args.python,
                powershell_profiles=args.powershell_profile,
                bash_rc=args.bash_rc,
                bash_profile=args.bash_profile,
            )
            for item in results:
                status = "Installed/updated" if item["changed"] else "Already up to date"
                print("[%s] %s: %s" % (item["shell"], status, item["path"]))
            print("Usage: opencode --pet [opencode options]  (or opencode --pet-overlay)")
            print("Open a new terminal (or reload your shell profile) to activate the alias.")
    except (OSError, ValueError) as error:
        parser.exit(1, "Error: %s\n" % error)


if __name__ == "__main__":
    main()
