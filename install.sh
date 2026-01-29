#!/bin/bash
# Homie Scheduler Integration - Installation Script
# Copies custom_components/homie_scheduler to Home Assistant.
# Usage: bash install.sh /path/to/homeassistant/config

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

if [ -z "$1" ]; then
    echo -e "${YELLOW}Usage: bash install.sh /path/to/config${NC}"
    echo "Example: bash install.sh /config"
    exit 1
fi

HA_CONFIG="$1"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
INTEGRATION_SRC="$PROJECT_DIR/custom_components/homie_scheduler"
INTEGRATION_DST="$HA_CONFIG/custom_components/homie_scheduler"

echo -e "${BLUE}Homie Scheduler Integration – Installation${NC}"
echo "Project: $PROJECT_DIR"
echo "HA Config: $HA_CONFIG"
echo ""

if [ ! -d "$HA_CONFIG" ]; then
    echo -e "${RED}Error: HA config directory not found: $HA_CONFIG${NC}"
    exit 1
fi

if [ ! -f "$INTEGRATION_SRC/__init__.py" ]; then
    echo -e "${RED}Error: Integration not found at $INTEGRATION_SRC${NC}"
    exit 1
fi

mkdir -p "$INTEGRATION_DST"
mkdir -p "$INTEGRATION_DST/translations"

cp "$INTEGRATION_SRC/__init__.py" "$INTEGRATION_DST/"
cp "$INTEGRATION_SRC/config_flow.py" "$INTEGRATION_DST/"
cp "$INTEGRATION_SRC/const.py" "$INTEGRATION_DST/"
cp "$INTEGRATION_SRC/scheduler.py" "$INTEGRATION_DST/"
cp "$INTEGRATION_SRC/sensor.py" "$INTEGRATION_DST/"
cp "$INTEGRATION_SRC/services.py" "$INTEGRATION_DST/"
cp "$INTEGRATION_SRC/switch.py" "$INTEGRATION_DST/"
cp "$INTEGRATION_SRC/manifest.json" "$INTEGRATION_DST/"
cp "$INTEGRATION_SRC/services.yaml" "$INTEGRATION_DST/"
[ -f "$INTEGRATION_SRC/translations/en.json" ] && cp "$INTEGRATION_SRC/translations/en.json" "$INTEGRATION_DST/translations/"

echo -e "  ${GREEN}✓${NC} Homie Scheduler integration installed"
echo ""
echo -e "${GREEN}Done.${NC} Restart Home Assistant, then add the integration:"
echo "  Settings → Devices & Services → Add Integration → Homie Scheduler"
