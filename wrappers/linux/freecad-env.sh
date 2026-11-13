# Shared by the FreeCAD wrappers (cam-python, freecad, freecadcmd). Sourced; expects T and S.
export HOME="$S/home"
export FREECAD_USER_HOME="$S/freecad" FREECAD_USER_DATA="$S/freecad/data" FREECAD_USER_TEMP="$S/tmp"
# app layers are read-only: Python bytecode for the bundle's own modules is cached in μ state instead
export PYTHONPYCACHEPREFIX="$S/pycache"
# headless unless a display is explicitly wanted
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
mkdir -p "$FREECAD_USER_DATA" "$S/tmp"
FC="$T/apps/FreeCAD-1.1.4/usr"
_fc_cleanup() { return 0; }
