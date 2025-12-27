#!/bin/bash

# UnixSync Runner
# Installs dependencies and starts the main application

set -e

echo "=== UnixSync: iCloud for Manjaro ==="

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed."
    exit 1
fi

# Check for virtualenv
if [ ! -d "venv" ]; then
    echo " Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# Install dependencies
echo " Installing dependencies..."
pip install -r requirements.txt > /dev/null 2>&1

# Ensure Mount Point Exists
if [ ! -d ~/iCloud ]; then
    mkdir -p ~/iCloud
fi

# Run
echo " Starting Service..."
echo "---------------------------------------------------"
echo "  Web Interface: http://localhost:8080/api/v1/status"
echo "  Mount Point:   ~/iCloud"
echo "---------------------------------------------------"

# We run with python directly. 
# Note: If FUSE fails, run this script with 'sudo' might be needed depending on fuse config,
# but usually FUSE works for users.

python3 main.py --mount ~/iCloud "$@"
