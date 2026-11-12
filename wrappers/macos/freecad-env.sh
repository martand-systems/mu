# Shared by the FreeCAD wrappers (cam-python, freecad, freecadcmd). Sourced; expects T and S.
export HOME="$S/home"
export FREECAD_USER_HOME="$S/freecad"
export FREECAD_USER_DATA="$S/freecad/data"
export FREECAD_USER_TEMP="$S/tmp"
mkdir -p "$FREECAD_USER_DATA" "$S/tmp"
# FreeCAD always creates an empty ~/Library/Caches/FreeCAD/v1-1 (macOS resolves Caches via the
# system, not $HOME). If it did not exist before this run, remove it afterwards (only if empty).
_FC_CACHE="$(dscl . -read "/Users/$(id -un)" NFSHomeDirectory | awk '{print $2}')/Library/Caches/FreeCAD"
[ -e "$_FC_CACHE" ] && _FC_PREEXISTING=1 || _FC_PREEXISTING=0
_fc_cleanup() { [ "$_FC_PREEXISTING" = 0 ] && rmdir "$_FC_CACHE/v1-1" "$_FC_CACHE" 2>/dev/null; return 0; }
