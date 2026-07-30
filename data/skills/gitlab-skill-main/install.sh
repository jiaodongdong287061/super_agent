#!/usr/bin/env bash
set -euo pipefail

# --------------------------------------------------------------------------
# gitlab-skill installer
# One-command install for Claude Code and Cursor AI agent skill
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ekohe/gitlab-skill/main/install.sh | bash
#
# Options (env vars):
#   INSTALL_DIR   Override install directory (default: auto-detect)
#   SKIP_DEPS     Set to 1 to skip pip install (default: 0)
# --------------------------------------------------------------------------

REPO_URL="https://github.com/ekohe/gitlab-skill.git"
SKILL_NAME="gitlab-skill"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[info]${NC}  $*"; }
success() { echo -e "${GREEN}[ok]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC}  $*"; }
error()   { echo -e "${RED}[error]${NC} $*" >&2; }

# --------------------------------------------------------------------------
# Detect install directory
# --------------------------------------------------------------------------
detect_install_dir() {
    if [[ -n "${INSTALL_DIR:-}" ]]; then
        echo "$INSTALL_DIR"
        return
    fi

    # Prefer Claude Code skills dir, fall back to Cursor
    local claude_dir="$HOME/.claude/skills"
    local cursor_dir="$HOME/.cursor/skills"

    if [[ -d "$claude_dir" ]]; then
        echo "$claude_dir/$SKILL_NAME"
    elif [[ -d "$cursor_dir" ]]; then
        echo "$cursor_dir/$SKILL_NAME"
    else
        # Default to Claude Code location
        echo "$claude_dir/$SKILL_NAME"
    fi
}

# --------------------------------------------------------------------------
# Check prerequisites
# --------------------------------------------------------------------------
check_prereqs() {
    local missing=0

    if ! command -v git &>/dev/null; then
        error "git is not installed. Please install git first."
        missing=1
    fi

    if ! command -v python3 &>/dev/null; then
        error "python3 is not installed. Please install Python 3.9+ first."
        missing=1
    fi

    if [[ $missing -eq 1 ]]; then
        exit 1
    fi
}

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
main() {
    echo ""
    echo -e "${BOLD}gitlab-skill installer${NC}"
    echo -e "https://github.com/ekohe/gitlab-skill"
    echo ""

    check_prereqs

    local install_dir
    install_dir="$(detect_install_dir)"

    info "Install directory: ${BOLD}$install_dir${NC}"

    # Clone or update
    if [[ -d "$install_dir/.git" ]]; then
        info "Existing installation found — pulling latest..."
        git -C "$install_dir" pull --ff-only
        success "Updated to latest version."
    else
        # Ensure parent directory exists
        mkdir -p "$(dirname "$install_dir")"

        info "Cloning repository..."
        git clone "$REPO_URL" "$install_dir"
        success "Cloned successfully."
    fi

    # Install Python dependencies
    if [[ "${SKIP_DEPS:-0}" != "1" ]]; then
        info "Installing Python dependencies..."
        if command -v pip3 &>/dev/null; then
            pip3 install -q -r "$install_dir/requirements.txt"
        elif python3 -m pip --version &>/dev/null 2>&1; then
            python3 -m pip install -q -r "$install_dir/requirements.txt"
        else
            warn "pip not found — skipping dependency install."
            warn "Run manually: pip install -r $install_dir/requirements.txt"
        fi
        success "Dependencies installed."
    else
        info "Skipping dependency install (SKIP_DEPS=1)."
    fi

    # Set up config file
    local config_file="$install_dir/gitlab_config.json"
    if [[ ! -f "$config_file" ]]; then
        cp "$install_dir/gitlab_config.json.template" "$config_file"
        chmod 600 "$config_file"
        info "Created config from template: ${BOLD}$config_file${NC}"
        warn "Edit this file to add your GitLab instances and tokens."
    else
        success "Config file already exists — skipping."
    fi

    # Verify
    echo ""
    echo -e "${GREEN}${BOLD}Installation complete!${NC}"
    echo ""
    echo "  Next steps:"
    echo ""
    echo "    1. Edit your config with your GitLab tokens:"
    echo -e "       ${BOLD}\$EDITOR $config_file${NC}"
    echo ""
    echo "    2. Verify it works:"
    echo -e "       ${BOLD}python3 $install_dir/scripts/gitlab_api.py list-instances${NC}"
    echo ""
    echo "    3. Use it with your AI assistant — just mention GitLab issues or MRs!"
    echo ""
}

main "$@"
