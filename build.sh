#!/usr/bin/env bash
# Build Uplift.app for macOS
#
# Uses system Python for dev builds; bundles a complete Python framework into
# the DMG so the receiving machine needs nothing pre-installed.
#
# PyInstaller bundles an older OpenSSL that crashes on macOS 26's xzone
# allocator, so we embed Python ourselves instead.
#
# The CFBundleExecutable is a compiled C binary (not a shell script) because
# macOS 26 security policy blocks shell scripts as app executables.
#
#   bash build.sh          — fast dev build (requires system Python 3.14)
#   bash build.sh --dmg    — portable DMG with bundled Python (no Python needed on target)

set -e

MAKE_DMG=0
for arg in "$@"; do
  [ "$arg" = "--dmg" ] && MAKE_DMG=1
done

APP_NAME="Uplift"
BUNDLE_ID="com.kootenaycolor.uplift"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST="$SCRIPT_DIR/dist/$APP_NAME.app"

PYVER="3.14"
PYFW="/Library/Frameworks/Python.framework/Versions/$PYVER"

echo "Building $APP_NAME.app…"

# Clean
rm -rf "$DIST"
mkdir -p "$DIST/Contents/MacOS"
mkdir -p "$DIST/Contents/Resources"

# ── Info.plist ──────────────────────────────────────────────────────────────
cat > "$DIST/Contents/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>             <string>$APP_NAME</string>
  <key>CFBundleDisplayName</key>      <string>$APP_NAME</string>
  <key>CFBundleIdentifier</key>       <string>$BUNDLE_ID</string>
  <key>CFBundleVersion</key>          <string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleExecutable</key>       <string>$APP_NAME</string>
  <key>CFBundlePackageType</key>      <string>APPL</string>
  <key>CFBundleIconFile</key>         <string>AppIcon</string>
  <key>NSHighResolutionCapable</key>  <true/>
  <key>LSMinimumSystemVersion</key>   <string>12.0</string>
</dict>
</plist>
EOF

# ── Compiled C launcher ─────────────────────────────────────────────────────
# Single-quoted heredoc: no bash expansion — all $ are literal C code.
# Launcher detects bundled Python at runtime and falls back to system install.
LAUNCHER_SRC="$SCRIPT_DIR/launcher_stub.c"

cat > "$LAUNCHER_SRC" << 'CSRC'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wchar.h>
#include <sys/stat.h>
#include <mach-o/dyld.h>
#include <Python.h>

int main(int argc, char *argv[]) {
    char exec_path[4096];
    uint32_t size = sizeof(exec_path);
    if (_NSGetExecutablePath(exec_path, &size) != 0) {
        fprintf(stderr, "Uplift: could not resolve executable path\n");
        return 1;
    }

    // Strip filename → exec_path now points to Contents/MacOS
    char *slash = strrchr(exec_path, '/');
    if (!slash) return 1;
    *slash = '\0';

    // Resources dir and main.py path
    char resources[4096], main_py[4096];
    snprintf(resources, sizeof(resources), "%s/../Resources", exec_path);
    snprintf(main_py,   sizeof(main_py),   "%s/main.py", resources);

    // Prefer bundled Python framework; fall back to system install
    char py_home[4096];
    struct stat st;
    snprintf(py_home, sizeof(py_home),
        "%s/../Frameworks/Python.framework/Versions/3.14", exec_path);
    if (stat(py_home, &st) != 0 || !S_ISDIR(st.st_mode)) {
        strlcpy(py_home,
            "/Library/Frameworks/Python.framework/Versions/3.14",
            sizeof(py_home));
    }

    // Change to Resources so relative imports work
    chdir(resources);

    // Configure and start the embedded interpreter
    PyConfig cfg;
    PyConfig_InitPythonConfig(&cfg);
    PyConfig_SetBytesString(&cfg, &cfg.home, py_home);

    // program_name must be an absolute path for NSBundle to work
    wchar_t prog[4096];
    mbstowcs(prog, exec_path, 4096);
    wcscat(prog, L"/Uplift");
    PyConfig_SetString(&cfg, &cfg.program_name, prog);

    PyConfig_SetBytesString(&cfg, &cfg.run_filename, main_py);
    cfg.isolated = 0;
    cfg.site_import = 1;

    PyStatus status = Py_InitializeFromConfig(&cfg);
    PyConfig_Clear(&cfg);
    if (PyStatus_Exception(status)) {
        Py_ExitStatusException(status);
    }

    // Add Resources to sys.path so sibling modules are importable
    PyRun_SimpleString("import sys, os; sys.path.insert(0, os.getcwd())");

    FILE *fp = fopen(main_py, "r");
    if (!fp) {
        fprintf(stderr, "Uplift: cannot open %s\n", main_py);
        return 1;
    }
    int rc = PyRun_SimpleFile(fp, main_py);
    fclose(fp);

    Py_Finalize();
    return rc;
}
CSRC

# Compile — link system Python, add rpaths for both bundled and system locations
clang -O2 \
  -I "$PYFW/Headers" \
  "$PYFW/Python" \
  -Wl,-rpath,"@executable_path/../Frameworks/Python.framework/Versions/$PYVER" \
  -Wl,-rpath,"$PYFW" \
  -o "$DIST/Contents/MacOS/$APP_NAME" "$LAUNCHER_SRC"
rm -f "$LAUNCHER_SRC"

# Retarget Python dylib reference from absolute path to @rpath so either
# rpath entry (bundled or system) can satisfy it at runtime
install_name_tool \
  -change "$PYFW/Python" "@rpath/Python" \
  "$DIST/Contents/MacOS/$APP_NAME"

# ── App icon ────────────────────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/design/uplift.icns" ]; then
  cp "$SCRIPT_DIR/design/uplift.icns" "$DIST/Contents/Resources/AppIcon.icns"
fi

# ── Source files ────────────────────────────────────────────────────────────
cp "$SCRIPT_DIR/main.py" "$DIST/Contents/Resources/main.py"

for f in drive.py state.py config.py drive_accounts.py mailer.py sender_profile.py; do
  cp "$SCRIPT_DIR/$f" "$DIST/Contents/Resources/"
done

if [ -d "$SCRIPT_DIR/fonts" ]; then
  cp -r "$SCRIPT_DIR/fonts" "$DIST/Contents/Resources/fonts"
fi

if [ -d "$SCRIPT_DIR/design/new-icons" ]; then
  cp -r "$SCRIPT_DIR/design/new-icons" "$DIST/Contents/Resources/icons"
fi

# Copy OAuth client credentials (needed for auth flow; not a user token)
if [ -f "$SCRIPT_DIR/credentials.json" ]; then
  cp "$SCRIPT_DIR/credentials.json" "$DIST/Contents/Resources/credentials.json"
  chmod 600 "$DIST/Contents/Resources/credentials.json"
fi
# token.json is NOT bundled — tokens are stored in Keychain

# Ad-hoc sign the dev build
codesign --deep --force -s - "$DIST" 2>/dev/null && echo "  (ad-hoc signed)" || echo "  (signing skipped)"

echo ""
echo "Done!  →  dist/$APP_NAME.app"
echo ""

# Auto-install to /Applications
rm -rf "/Applications/$APP_NAME.app"
cp -r "$DIST" "/Applications/$APP_NAME.app"
echo "Installed →  /Applications/$APP_NAME.app"

# ── Optional DMG with bundled Python ────────────────────────────────────────
if [ "$MAKE_DMG" = "1" ]; then
  echo ""
  echo "Bundling Python $PYVER…"

  DMG_STAGING="$SCRIPT_DIR/dist/.dmg_staging"
  DMG_OUT="$SCRIPT_DIR/dist/$APP_NAME.dmg"
  DMG_APP="$DMG_STAGING/$APP_NAME.app"
  FW_LIB="$PYFW/lib"

  rm -rf "$DMG_STAGING"
  mkdir -p "$DMG_STAGING"
  # Start from the already-built dev app
  cp -r "$DIST" "$DMG_APP"

  # Target directory inside the bundle
  BUNDLED_FW="$DMG_APP/Contents/Frameworks/Python.framework/Versions/$PYVER"
  mkdir -p "$BUNDLED_FW/lib"

  # Minimal framework symlinks so Python finds itself via the standard layout
  ln -sfh "$PYVER" "$DMG_APP/Contents/Frameworks/Python.framework/Versions/Current"
  ln -sfh "Versions/Current/Python" "$DMG_APP/Contents/Frameworks/Python.framework/Python"

  # ── Python main dylib ───────────────────────────────────────────────────
  echo "  Python dylib…"
  cp "$PYFW/Python" "$BUNDLED_FW/Python"
  chmod +w "$BUNDLED_FW/Python"

  # ── External lib dylibs ─────────────────────────────────────────────────
  # These are referenced by absolute path from lib-dynload .so extension modules.
  # We bundle them alongside the stdlib and rewrite the references below.
  echo "  lib dylibs…"
  BUNDLE_DYLIBS="libssl.3.dylib libcrypto.3.dylib libzstd.1.dylib libncurses.6.dylib libpanel.6.dylib"

  for DYLIB in $BUNDLE_DYLIBS; do
    SRC="$FW_LIB/$DYLIB"
    # Follow symlinks to always copy the real file (e.g. libzstd.1.dylib → libzstd.1.5.7.dylib)
    SRC=$(python3 -c "import os; print(os.path.realpath('$SRC'))")
    cp "$SRC" "$BUNDLED_FW/lib/$DYLIB"
    chmod +w "$BUNDLED_FW/lib/$DYLIB"
  done

  # Fix install names and cross-references inside the bundled dylibs
  for DYLIB in $BUNDLE_DYLIBS; do
    DEST="$BUNDLED_FW/lib/$DYLIB"
    install_name_tool -id "@loader_path/$DYLIB" "$DEST"
    for DEPLIB in $BUNDLE_DYLIBS; do
      install_name_tool \
        -change "$FW_LIB/$DEPLIB" "@loader_path/$DEPLIB" \
        "$DEST" 2>/dev/null || true
    done
  done

  # ── Python stdlib ───────────────────────────────────────────────────────
  echo "  stdlib…"
  rsync -a \
    --exclude '__pycache__/' \
    --exclude 'test/' \
    --exclude 'tests/' \
    --exclude '*.pyc' \
    --exclude 'config-*/' \
    --exclude 'lib-dynload/' \
    "$PYFW/lib/python$PYVER/" \
    "$BUNDLED_FW/lib/python$PYVER/"

  # ── lib-dynload extension modules ───────────────────────────────────────
  echo "  lib-dynload…"
  mkdir -p "$BUNDLED_FW/lib/python$PYVER/lib-dynload"
  rsync -a \
    "$PYFW/lib/python$PYVER/lib-dynload/" \
    "$BUNDLED_FW/lib/python$PYVER/lib-dynload/"

  # Rewrite absolute framework lib refs in each .so
  # lib-dynload/ is two levels under lib/ (lib/python3.14/lib-dynload/),
  # so @loader_path/../../<dylib> resolves to lib/<dylib>
  for SO in "$BUNDLED_FW/lib/python$PYVER/lib-dynload/"*.so; do
    chmod +w "$SO"
    for DEPLIB in $BUNDLE_DYLIBS; do
      install_name_tool \
        -change "$FW_LIB/$DEPLIB" "@loader_path/../../$DEPLIB" \
        "$SO" 2>/dev/null || true
    done
  done

  # ── site-packages ───────────────────────────────────────────────────────
  echo "  site-packages…"
  SP_SRC="$PYFW/lib/python$PYVER/site-packages"
  SP_DEST="$BUNDLED_FW/lib/python$PYVER/site-packages"
  mkdir -p "$SP_DEST"
  rsync -a \
    --exclude 'pip/' \
    --exclude 'pip-*/' \
    --exclude 'pip*.dist-info/' \
    --exclude 'setuptools/' \
    --exclude 'setuptools-*/' \
    --exclude 'setuptools*.dist-info/' \
    --exclude 'wheel/' \
    --exclude 'wheel-*/' \
    --exclude 'wheel*.dist-info/' \
    --exclude 'PyObjCTest/' \
    --exclude 'PIL/' \
    --exclude 'Pillow*/' \
    --exclude 'PyInstaller/' \
    --exclude '_pyinstaller_hooks_contrib*/' \
    --exclude 'py2app/' \
    --exclude 'openpyxl/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    "$SP_SRC/" "$SP_DEST/"

  # Fix any absolute framework lib refs in site-packages .so files.
  # Compute the relative path from each .so's directory back up to lib/ so
  # @loader_path/<rel>/<dylib> resolves correctly regardless of nesting depth.
  while IFS= read -r -d '' SO; do
    chmod +w "$SO" 2>/dev/null || true
    HAS_REF=$(otool -L "$SO" 2>/dev/null | grep -c "$FW_LIB/" || true)
    [ "$HAS_REF" -eq 0 ] && continue
    SO_DIR=$(dirname "$SO")
    REL=$(python3 -c "import os; print(os.path.relpath('$BUNDLED_FW/lib', '$SO_DIR'))")
    for DEPLIB in $BUNDLE_DYLIBS; do
      install_name_tool \
        -change "$FW_LIB/$DEPLIB" "@loader_path/$REL/$DEPLIB" \
        "$SO" 2>/dev/null || true
    done
  done < <(find "$SP_DEST" -name "*.so" -print0 2>/dev/null)

  # ── Re-sign after all install_name_tool modifications ───────────────────
  # codesign --deep can't handle our stripped-down framework (no Info.plist),
  # so sign each binary explicitly in inside-out order before sealing the bundle.
  echo "  Signing…"
  # .so and .dylib inside the framework
  while IFS= read -r -d '' f; do
    codesign --force -s - "$f" 2>/dev/null || true
  done < <(find "$DMG_APP/Contents/Frameworks" -type f \( -name "*.so" -o -name "*.dylib" \) -print0)
  # Python main dylib
  codesign --force -s - "$BUNDLED_FW/Python" 2>/dev/null || true
  # Launcher binary
  codesign --force -s - "$DMG_APP/Contents/MacOS/$APP_NAME" 2>/dev/null || true
  # Outer bundle (seals the whole thing)
  codesign --force -s - "$DMG_APP" 2>/dev/null || true

  # ── Create DMG ──────────────────────────────────────────────────────────
  echo "  Creating DMG…"
  ln -s /Applications "$DMG_STAGING/Applications"
  rm -f "$DMG_OUT"
  hdiutil create -volname "$APP_NAME" -srcfolder "$DMG_STAGING" \
    -ov -format UDZO -quiet "$DMG_OUT"
  rm -rf "$DMG_STAGING"

  DMG_SIZE=$(du -sh "$DMG_OUT" | cut -f1)
  echo "DMG ($DMG_SIZE)  →  dist/$APP_NAME.dmg"
fi
