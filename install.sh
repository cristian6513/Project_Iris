#!/usr/bin/env bash
# ============================================
# IRIS OS — Linux/macOS bootstrap installer
# ============================================
# Usage from a verified checkout: bash install.sh

set -e

GOLD='\033[1;33m'
BLUE='\033[1;36m'
GREEN='\033[1;32m'
RED='\033[1;31m'
NC='\033[0m'

echo -e "${GOLD}"
echo "  ⚔️  IRIS OS — Installer"
echo "  Open-Source Robotic AI Assistant"
echo -e "${NC}"

# --- Detect OS ---
OS="unknown"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if [[ -d "/data/data/com.termux" ]]; then
        OS="android"
    elif grep -q "Raspberry" /proc/cpuinfo 2>/dev/null; then
        OS="rpi"
    else
        OS="linux"
    fi
elif [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
fi

echo -e "${BLUE}Detected OS:${NC} $OS"

# --- Detect RAM ---
if [[ "$OS" == "linux" || "$OS" == "rpi" ]]; then
    RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    RAM_GB=$((RAM_KB / 1024 / 1024))
elif [[ "$OS" == "macos" ]]; then
    RAM_BYTES=$(sysctl -n hw.memsize)
    RAM_GB=$((RAM_BYTES / 1024 / 1024 / 1024))
elif [[ "$OS" == "android" ]]; then
    RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    RAM_GB=$((RAM_KB / 1024 / 1024))
else
    echo -e "${RED}This bootstrap supports Linux, macOS, Raspberry Pi and Termux.${NC}"
    echo "On Windows, run: python -m wizard.app"
    exit 1
fi

echo -e "${BLUE}RAM:${NC} ${RAM_GB}GB"

# --- Engine readiness ---
# IRIS integrates Hermes Agent. Do not promise an unrelated fallback that the
# runtime cannot configure or launch.
ENGINE="hermes"
echo -e "${GREEN}Engine: Hermes Agent${NC}"
if [[ $RAM_GB -lt 4 ]]; then
    echo -e "${GOLD}Low-memory system: use a remote provider; local models are not recommended.${NC}"
fi

# --- Ensure uv (required by Hermes) ---
if [[ "$ENGINE" == "hermes" ]] && ! command -v uv &> /dev/null; then
    echo -e "\n${BLUE}Installing uv (Hermes dependency)...${NC}"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Make uv visible to the rest of this shell session.
    export PATH="$HOME/.local/bin:$PATH"
fi

# --- Install Python dependencies ---
echo -e "\n${BLUE}Installing Python dependencies...${NC}"
if command -v pip3 &> /dev/null; then
    pip3 install -e .
elif command -v pip &> /dev/null; then
    pip install -e .
else
    echo -e "${RED}Python pip not found. Please install Python 3.11+${NC}"
    exit 1
fi

# --- Install Hermes if needed ---
if [[ "$ENGINE" == "hermes" ]]; then
    if ! command -v hermes &> /dev/null; then
        echo -e "\n${BLUE}Installing Hermes Agent...${NC}"
        curl -fsSL https://install.hermes-agent.nousresearch.com | bash
    else
        echo -e "${GREEN}Hermes already installed.${NC}"
    fi
fi

# --- Done ---
echo -e "\n${GOLD}============================================${NC}"
echo -e "${GREEN}✅ IRIS installed successfully!${NC}"
echo -e "${GOLD}============================================${NC}"
echo ""
echo -e "Next steps:"
echo -e "  1. ${BLUE}python -m wizard.app${NC}              (run setup wizard at http://127.0.0.1:8000)"
echo -e "  2. ${BLUE}python -m core.agent${NC}              (start IRIS)"
echo -e "  3. Open ${BLUE}https://localhost:8765/face/${NC}   (see the avatar — accept the self-signed cert the first time)"
echo ""
echo -e "${GOLD}✨  IRIS is ready. Enjoy.${NC}"
