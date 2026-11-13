#!/bin/bash
# Install μ on this computer:   ./install.sh            (options: ./install.sh --help)
# macOS (Apple Silicon) and Linux x86_64 (Ubuntu 24.04, including WSL2).
exec "$(dirname "$0")/install/install.sh" "$@"
