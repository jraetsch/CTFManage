#!/usr/bin/env bash
# ctftool installer.
#
# Default mode copies the ctf/ package into a canonical directory
# (~/.local/share/ctftool by default) and drops a launcher on
# ~/.local/bin/ctf. --symlink links the canonical directory to this repo
# checkout instead, so edits here take effect without reinstalling.
#
# Also offers to install the cd-after-get shell hook (zsh, bash, or fish —
# pick one interactively, or pass --shell). This is optional: `ctf get` and
# `ctf cd` always work and always print the destination path; the hook is
# only what turns that into an actual `cd`, since a child process cannot
# change its parent shell's directory.
#
# --uninstall removes exactly what this script created and nothing else —
# it never touches $CTF_ROOT (your challenges/DB) and only removes
# ~/.config/ctftool if you ask it to.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR"

INSTALL_DIR="${CTFTOOL_HOME:-$HOME/.local/share/ctftool}"
BIN_DIR="${CTFTOOL_BIN_DIR:-$HOME/.local/bin}"
CONFIG_DIR="$HOME/.config/ctftool"
ZSHRC="$HOME/.zshrc"
BASHRC="$HOME/.bashrc"
FISH_FUNC_DIR="$HOME/.config/fish/functions"
FISH_FUNC_FILE="$FISH_FUNC_DIR/ctf.fish"

MODE="copy"
ACTION="install"
PURGE_CONFIG=""   # "" = ask/skip depending on tty, "yes", "no"
SHELL_ARG=""      # "" = ask/skip depending on tty, else zsh|fish|bash|none

LAUNCHER_MARKER="# ctftool-installed"
FISH_HOOK_MARKER="# ctftool-installed"
HOOK_BEGIN="# >>> ctftool shell hook >>>"
HOOK_END="# <<< ctftool shell hook <<<"

# Set by do_install, persisted via write_manifest, read back by do_uninstall.
INSTALLED_SHELL="none"
INSTALLED_SHELL_HOOK=""

usage() {
    cat <<'EOF'
Usage: install.sh [--symlink] [--prefix DIR] [--bin-dir DIR] [--shell NAME]
       install.sh --uninstall [--purge-config | --keep-config]

  --symlink        Link the install directory to this checkout instead of
                    copying it, so edits to the repo take effect immediately.
  --prefix DIR     Canonical install directory (default: ~/.local/share/ctftool,
                    or $CTFTOOL_HOME if set).
  --bin-dir DIR    Where to put the launcher (default: ~/.local/bin,
                    or $CTFTOOL_BIN_DIR if set).
  --shell NAME     Install the 'ctf get'/'ctf cd' auto-cd hook for this shell
                    without prompting: zsh, bash, fish, or none. Interactive
                    prompt otherwise (skipped, defaulting to none, when not
                    running in a terminal).
  --uninstall      Remove the launcher, install directory, and shell hook this
                    script created. Never touches $CTF_ROOT.
  --purge-config   With --uninstall, also delete ~/.config/ctftool without asking.
  --keep-config    With --uninstall, leave ~/.config/ctftool without asking.
  -h, --help       Show this help.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --symlink|--link)
            MODE="symlink"
            ;;
        --uninstall)
            ACTION="uninstall"
            ;;
        --prefix)
            [ $# -ge 2 ] || { echo "install.sh: --prefix needs an argument" >&2; exit 2; }
            INSTALL_DIR="$2"
            shift
            ;;
        --bin-dir)
            [ $# -ge 2 ] || { echo "install.sh: --bin-dir needs an argument" >&2; exit 2; }
            BIN_DIR="$2"
            shift
            ;;
        --shell)
            [ $# -ge 2 ] || { echo "install.sh: --shell needs an argument" >&2; exit 2; }
            case "$2" in
                zsh|bash|fish|none) SHELL_ARG="$2" ;;
                *) echo "install.sh: --shell must be one of zsh, bash, fish, none" >&2; exit 2 ;;
            esac
            shift
            ;;
        --purge-config)
            PURGE_CONFIG="yes"
            ;;
        --keep-config)
            PURGE_CONFIG="no"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "install.sh: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

LAUNCHER="$BIN_DIR/ctf"
MANIFEST="${INSTALL_DIR%/}.manifest"

# The manifest is what lets --uninstall (and a re-run of install) tell "a
# directory/symlink this script created" apart from something else that
# happens to already be sitting at $INSTALL_DIR — and which shell hook (if
# any) it installed, so uninstall can remove exactly that one. Without it,
# uninstall would have to guess, and guessing wrong means deleting something
# we didn't create.
write_manifest() {
    cat > "$MANIFEST" <<EOF
mode=$MODE
source=$SOURCE_DIR
install_dir=$INSTALL_DIR
launcher=$LAUNCHER
shell=$INSTALLED_SHELL
shell_hook=$INSTALLED_SHELL_HOOK
installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
}

manifest_matches_install_dir() {
    [ -f "$MANIFEST" ] || return 1
    local recorded
    recorded="$(sed -n 's/^install_dir=//p' "$MANIFEST")"
    [ "$recorded" = "$INSTALL_DIR" ]
}

clear_existing_install_dir() {
    if [ -e "$INSTALL_DIR" ] || [ -L "$INSTALL_DIR" ]; then
        if manifest_matches_install_dir; then
            rm -rf -- "$INSTALL_DIR"
        else
            echo "install.sh: $INSTALL_DIR already exists and was not created by this script." >&2
            echo "Move it aside or pass --prefix to install somewhere else." >&2
            exit 1
        fi
    fi
}

install_launcher() {
    mkdir -p -- "$BIN_DIR"
    if [ -e "$LAUNCHER" ] && ! grep -q -- "$LAUNCHER_MARKER" "$LAUNCHER" 2>/dev/null; then
        echo "install.sh: $LAUNCHER already exists and wasn't created by this script." >&2
        echo "Remove it or pass --bin-dir to install the launcher elsewhere." >&2
        exit 1
    fi
    cat > "$LAUNCHER" <<EOF
#!/bin/sh
$LAUNCHER_MARKER — safe to remove with: $SOURCE_DIR/install.sh --uninstall
CTFTOOL_HOME="\${CTFTOOL_HOME:-$INSTALL_DIR}"
export PYTHONPATH="\$CTFTOOL_HOME\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -P -m ctf "\$@"
EOF
    chmod +x -- "$LAUNCHER"
}

path_hint() {
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) echo "Note: $BIN_DIR is not on your PATH yet — add it to run 'ctf' directly." ;;
    esac
}

# -- shell hook --------------------------------------------------------------
#
# `ctf get`/`ctf cd` always print the destination path to stdout — that part
# needs no shell-specific help. What a plain subprocess can never do is
# change ITS PARENT'S working directory, so turning "printed a path" into
# "you are now standing in it" requires a function defined in the shell
# itself. That function is inherently shell-specific (fish's syntax shares
# nothing with zsh/bash's), so there is one implementation per shell below,
# all doing the same three things: run `command ctf ...` for real, only ever
# `cd` into an absolute, existing, freshly-printed path, and fail closed
# (stay put) on any error, empty output, or unexpected shape.

choose_shell() {
    if [ -n "$SHELL_ARG" ]; then
        printf '%s\n' "$SHELL_ARG"
        return
    fi
    if [ ! -t 0 ]; then
        echo none
        return
    fi
    {
        echo
        echo "Install the 'ctf get'/'ctf cd' auto-cd hook for which shell?"
        echo "  1) zsh   -> $ZSHRC"
        echo "  2) fish  -> $FISH_FUNC_FILE"
        echo "  3) bash  -> $BASHRC"
        echo "  4) skip"
        printf 'Choice [1-4, default 4]: '
    } >&2
    local reply
    read -r reply || reply=4
    case "$reply" in
        1) echo zsh ;;
        2) echo fish ;;
        3) echo bash ;;
        *) echo none ;;
    esac
}

# zsh and bash share one function body (audited 2026-08-04 — see
# docs/ARCHITECTURE.md § Why the wrapper is not a security surface),
# appended between markers so a re-run or --uninstall can find and remove
# exactly this block without disturbing anything else in the rc file.
rc_hook_body() {
    cat <<'EOF'
ctf() {
  case "$1" in
    get|cd)
      local d
      d=$(command ctf "$@" --print-path) || return
      [[ "$d" == /* && -d "$d" ]] && cd -- "$d"
      ;;
    *) command ctf "$@" ;;
  esac
}
EOF
}

remove_rc_hook() {
    local rc_file="$1"
    [ -f "$rc_file" ] || return 0
    if grep -qF -- "$HOOK_BEGIN" "$rc_file" 2>/dev/null; then
        awk -v b="$HOOK_BEGIN" -v e="$HOOK_END" '
            $0 == b {skip=1; next}
            skip    {if ($0 == e) skip=0; next}
                    {print}
        ' "$rc_file" > "$rc_file.ctftool-tmp" && mv -- "$rc_file.ctftool-tmp" "$rc_file"
        echo "Removed the cd hook from $rc_file"
    fi
}

install_rc_hook() {
    local rc_file="$1"
    mkdir -p -- "$(dirname -- "$rc_file")"
    touch -- "$rc_file"
    remove_rc_hook "$rc_file"   # idempotent: replace a previous block instead of duplicating it
    {
        printf '\n%s\n' "$HOOK_BEGIN"
        rc_hook_body
        printf '%s\n' "$HOOK_END"
    } >> "$rc_file"
    echo "Added the cd hook to $rc_file — open a new shell, or run: source $rc_file"
}

# fish autoloads one function per file from this directory, so the file IS
# the hook: no rc file to edit, and --uninstall is a single rm.
install_fish_hook() {
    mkdir -p -- "$FISH_FUNC_DIR"
    if [ -e "$FISH_FUNC_FILE" ] && ! grep -q -- "$FISH_HOOK_MARKER" "$FISH_FUNC_FILE" 2>/dev/null; then
        echo "install.sh: $FISH_FUNC_FILE already exists and wasn't created by this script." >&2
        echo "Remove it manually and re-run to install the fish hook." >&2
        return 1
    fi
    cat > "$FISH_FUNC_FILE" <<EOF
$FISH_HOOK_MARKER — safe to remove with: $SOURCE_DIR/install.sh --uninstall
function ctf
    switch \$argv[1]
        case get cd
            set -l d (command ctf \$argv --print-path)
            or return
            if string match -q '/*' -- "\$d"; and test -d "\$d"
                cd -- "\$d"
            end
        case '*'
            command ctf \$argv
    end
end
EOF
    echo "Installed the fish cd hook at $FISH_FUNC_FILE — new shells pick it up automatically."
}

remove_fish_hook() {
    if [ -e "$FISH_FUNC_FILE" ]; then
        if grep -q -- "$FISH_HOOK_MARKER" "$FISH_FUNC_FILE" 2>/dev/null; then
            rm -f -- "$FISH_FUNC_FILE"
            echo "Removed $FISH_FUNC_FILE"
        else
            echo "Leaving $FISH_FUNC_FILE in place — it wasn't created by this script."
        fi
    fi
}

# -- install / uninstall ------------------------------------------------------

do_install() {
    clear_existing_install_dir
    mkdir -p -- "$(dirname -- "$INSTALL_DIR")"

    if [ "$MODE" = symlink ]; then
        ln -s -- "$SOURCE_DIR" "$INSTALL_DIR"
        echo "Linked $INSTALL_DIR -> $SOURCE_DIR"
    else
        mkdir -p -- "$INSTALL_DIR"
        cp -R -- "$SOURCE_DIR/ctf" "$INSTALL_DIR/ctf"
        echo "Copied $SOURCE_DIR/ctf -> $INSTALL_DIR/ctf"
    fi

    install_launcher
    echo "Installed launcher at $LAUNCHER"
    path_hint

    local shell_choice
    shell_choice="$(choose_shell)"
    INSTALLED_SHELL="$shell_choice"
    case "$shell_choice" in
        zsh)
            if install_rc_hook "$ZSHRC"; then INSTALLED_SHELL_HOOK="$ZSHRC"; else INSTALLED_SHELL="none"; fi
            ;;
        bash)
            if install_rc_hook "$BASHRC"; then INSTALLED_SHELL_HOOK="$BASHRC"; else INSTALLED_SHELL="none"; fi
            ;;
        fish)
            if install_fish_hook; then INSTALLED_SHELL_HOOK="$FISH_FUNC_FILE"; else INSTALLED_SHELL="none"; fi
            ;;
        none)
            echo "Skipping the cd shell hook (re-run with --shell zsh|bash|fish to add it later)."
            ;;
    esac

    write_manifest

    if [ "$MODE" = copy ]; then
        echo "Re-run install.sh after pulling changes to this repo (or use --symlink for a dev install)."
    fi
}

do_uninstall() {
    local recorded_shell="" recorded_hook=""
    if [ -f "$MANIFEST" ]; then
        recorded_shell="$(sed -n 's/^shell=//p' "$MANIFEST")"
        recorded_hook="$(sed -n 's/^shell_hook=//p' "$MANIFEST")"
    fi
    case "$recorded_shell" in
        zsh|bash)
            [ -n "$recorded_hook" ] && remove_rc_hook "$recorded_hook"
            ;;
        fish)
            remove_fish_hook
            ;;
    esac

    if [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
        if grep -q -- "$LAUNCHER_MARKER" "$LAUNCHER" 2>/dev/null; then
            rm -f -- "$LAUNCHER"
            echo "Removed $LAUNCHER"
        else
            echo "Leaving $LAUNCHER in place — it wasn't created by this script."
        fi
    fi

    if [ -e "$INSTALL_DIR" ] || [ -L "$INSTALL_DIR" ]; then
        if manifest_matches_install_dir; then
            rm -rf -- "$INSTALL_DIR"
            rm -f -- "$MANIFEST"
            echo "Removed $INSTALL_DIR"
        else
            echo "Leaving $INSTALL_DIR in place — no manifest confirms this script created it."
        fi
    else
        rm -f -- "$MANIFEST"
    fi

    case "$PURGE_CONFIG" in
        yes)
            rm -rf -- "$CONFIG_DIR"
            echo "Removed $CONFIG_DIR"
            ;;
        no)
            echo "Leaving $CONFIG_DIR in place."
            ;;
        "")
            if [ -e "$CONFIG_DIR" ] && [ -t 0 ]; then
                printf 'Also remove %s (settings)? [y/N] ' "$CONFIG_DIR"
                read -r reply
                case "$reply" in
                    [yY]|[yY][eE][sS])
                        rm -rf -- "$CONFIG_DIR"
                        echo "Removed $CONFIG_DIR"
                        ;;
                    *)
                        echo "Leaving $CONFIG_DIR in place."
                        ;;
                esac
            elif [ -e "$CONFIG_DIR" ]; then
                echo "Leaving $CONFIG_DIR in place (pass --purge-config to remove it non-interactively)."
            fi
            ;;
    esac

    echo "Your challenges and database under \$CTF_ROOT were not touched."
}

if [ "$ACTION" = uninstall ]; then
    do_uninstall
else
    do_install
fi
