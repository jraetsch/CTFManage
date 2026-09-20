#!/usr/bin/env bash
# ctftool installer.
#
# Default mode copies the ctf/ package into a canonical directory
# (~/.local/share/ctftool by default) and drops a launcher on
# ~/.local/bin/ctf. --symlink links the canonical directory to this repo
# checkout instead, so edits here take effect without reinstalling.
# --uninstall removes exactly what this script created and nothing else —
# it never touches $CTF_ROOT (your challenges/DB) and only removes
# ~/.config/ctftool if you ask it to.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR"

INSTALL_DIR="${CTFTOOL_HOME:-$HOME/.local/share/ctftool}"
BIN_DIR="${CTFTOOL_BIN_DIR:-$HOME/.local/bin}"
CONFIG_DIR="$HOME/.config/ctftool"

MODE="copy"
ACTION="install"
PURGE_CONFIG=""   # "" = ask/skip depending on tty, "yes", "no"

LAUNCHER_MARKER="# ctftool-installed"

usage() {
    cat <<'EOF'
Usage: install.sh [--symlink] [--prefix DIR] [--bin-dir DIR]
       install.sh --uninstall [--purge-config | --keep-config]

  --symlink        Link the install directory to this checkout instead of
                    copying it, so edits to the repo take effect immediately.
  --prefix DIR     Canonical install directory (default: ~/.local/share/ctftool,
                    or $CTFTOOL_HOME if set).
  --bin-dir DIR    Where to put the launcher (default: ~/.local/bin,
                    or $CTFTOOL_BIN_DIR if set).
  --uninstall      Remove the launcher and install directory this script
                    created. Never touches $CTF_ROOT.
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
# happens to already be sitting at $INSTALL_DIR. Without it, uninstall would
# have to guess — and guessing wrong means deleting a directory we didn't
# create.
write_manifest() {
    cat > "$MANIFEST" <<EOF
mode=$MODE
source=$SOURCE_DIR
install_dir=$INSTALL_DIR
launcher=$LAUNCHER
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
    write_manifest
    install_launcher
    echo "Installed launcher at $LAUNCHER"
    path_hint
    if [ "$MODE" = copy ]; then
        echo "Re-run install.sh after pulling changes to this repo (or use --symlink for a dev install)."
    fi
}

do_uninstall() {
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
