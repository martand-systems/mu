#!/bin/bash
# Install μ on this computer:   ./install.sh            (options: ./install.sh --help)
case "$(uname -s)" in
  Darwin) exec "$(dirname "$0")/install/install-mac.sh" "$@" ;;
  Linux)  echo "μ for Linux (compute role) is not released yet (phase S2)." >&2; exit 2 ;;
  *)      echo "μ supports macOS (Apple Silicon) and, soon, Linux / Windows via WSL2." >&2; exit 2 ;;
esac
