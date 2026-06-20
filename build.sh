#!/usr/bin/env bash
# Build Uplift.app for macOS
#
# Uses system Python (not a bundled interpreter) so the app inherits the
# system's SSL stack — required for macOS 26 compatibility. PyInstaller
# bundles an older OpenSSL that crashes on macOS 26's new xzone allocator.
#
# The CFBundleExecutable is a compiled C binary (not a shell script) because
# macOS 26 security policy blocks shell scripts as app executables.

set -e

APP_NAME="Uplift"
BUNDLE_ID="com.kootenaycolor.uplift"
PYTHON="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST="$SCRIPT_DIR/dist/$APP_NAME.app"

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
# A real binary is required — macOS 26 blocks shell scripts as app executables.
# This stub resolves its own path at runtime so it works from any install location.
LAUNCHER_SRC="$SCRIPT_DIR/launcher_stub.c"
PYVER="3.14"
PYFW="/Library/Frameworks/Python.framework/Versions/$PYVER"

cat > "$LAUNCHER_SRC" << CSRC
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wchar.h>
#include <mach-o/dyld.h>
#include <Python.h>

int main(int argc, char *argv[]) {
    char exec_path[4096];
    uint32_t size = sizeof(exec_path);
    if (_NSGetExecutablePath(exec_path, &size) != 0) {
        fprintf(stderr, "Uplift: could not resolve executable path\n");
        return 1;
    }

    // Strip filename → get Contents/MacOS dir
    char *slash = strrchr(exec_path, '/');
    if (!slash) return 1;
    *slash = '\0';

    // Resources dir and main.py path
    char resources[4096], main_py[4096];
    snprintf(resources, sizeof(resources), "%s/../Resources", exec_path);
    snprintf(main_py,   sizeof(main_py),   "%s/main.py", resources);

    // Change to Resources so relative imports work
    chdir(resources);

    // Configure and start the embedded interpreter
    PyConfig cfg;
    PyConfig_InitPythonConfig(&cfg);

    // Set home so Python finds its stdlib in the framework
    PyConfig_SetBytesString(&cfg, &cfg.home, "$PYFW");

    // program_name must be an absolute path to our binary for NSBundle to work
    wchar_t prog[4096];
    mbstowcs(prog, exec_path, 4096);
    // Append "/Uplift" back (exec_path now ends at MacOS dir)
    wcscat(prog, L"/$APP_NAME");
    PyConfig_SetString(&cfg, &cfg.program_name, prog);

    // sys.argv[0] = main.py path
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

    // Run main.py
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

# Compile the launcher — link directly against the versioned Python dylib
clang -O2 \
  -I "$PYFW/Headers" \
  "$PYFW/Python" \
  -Wl,-rpath,"$PYFW" \
  -o "$DIST/Contents/MacOS/$APP_NAME" "$LAUNCHER_SRC"
rm -f "$LAUNCHER_SRC"

# ── App icon ────────────────────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/design/uplift.icns" ]; then
  cp "$SCRIPT_DIR/design/uplift.icns" "$DIST/Contents/Resources/AppIcon.icns"
fi

# ── Source files ────────────────────────────────────────────────────────────
cp "$SCRIPT_DIR/main.py" "$DIST/Contents/Resources/main.py"

for f in drive.py state.py config.py drive_accounts.py mailer.py sender_profile.py; do
  cp "$SCRIPT_DIR/$f" "$DIST/Contents/Resources/"
done

# Copy fonts
if [ -d "$SCRIPT_DIR/fonts" ]; then
  cp -r "$SCRIPT_DIR/fonts" "$DIST/Contents/Resources/fonts"
fi

# Copy credentials + token if present
for f in credentials.json token.json; do
  [ -f "$SCRIPT_DIR/$f" ] && cp "$SCRIPT_DIR/$f" "$DIST/Contents/Resources/"
done

# Ad-hoc sign AFTER all files are in place so the signature covers everything
codesign --deep --force -s - "$DIST" 2>/dev/null && echo "  (ad-hoc signed)" || echo "  (signing skipped)"

echo ""
echo "Done!  →  dist/$APP_NAME.app"
echo ""

# Auto-install to /Applications
rm -rf "/Applications/$APP_NAME.app"
cp -r "$DIST" "/Applications/$APP_NAME.app"
echo "Installed →  /Applications/$APP_NAME.app"
