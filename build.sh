#!/usr/bin/env bash
# Build Drive Uploader.app for macOS
#
# Uses system Python (not a bundled interpreter) so the app inherits the
# system's SSL stack — required for macOS 26 compatibility. PyInstaller
# bundles an older OpenSSL that crashes on macOS 26's new xzone allocator.
#
# The CFBundleExecutable is a compiled C binary (not a shell script) because
# macOS 26 security policy blocks shell scripts as app executables.

set -e

APP_NAME="Drive Uploader"
BUNDLE_ID="com.kootenaycolor.drive-uploader"
PYTHON="/opt/homebrew/bin/python3.14"
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
  <key>CFBundleExecutable</key>       <string>Drive Uploader</string>
  <key>CFBundlePackageType</key>      <string>APPL</string>
  <key>NSHighResolutionCapable</key>  <true/>
  <key>LSMinimumSystemVersion</key>   <string>12.0</string>
</dict>
</plist>
EOF

# ── Compiled C launcher ─────────────────────────────────────────────────────
# A real binary is required — macOS 26 blocks shell scripts as app executables.
# This stub resolves its own path at runtime so it works from any install location.
LAUNCHER_SRC="$SCRIPT_DIR/launcher_stub.c"
cat > "$LAUNCHER_SRC" << 'CSRC'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <mach-o/dyld.h>

int main(int argc, char *argv[]) {
    // Resolve the path to this executable
    char exec_path[4096];
    uint32_t size = sizeof(exec_path);
    if (_NSGetExecutablePath(exec_path, &size) != 0) {
        fprintf(stderr, "Drive Uploader: could not resolve executable path\n");
        return 1;
    }

    // Strip the filename to get Contents/MacOS/
    char *last_slash = strrchr(exec_path, '/');
    if (!last_slash) { return 1; }
    *last_slash = '\0';

    // Build path to Resources/main.py
    char main_py[4096];
    snprintf(main_py, sizeof(main_py), "%s/../Resources/main.py", exec_path);

    // Change working directory to Resources so relative imports work
    char resources[4096];
    snprintf(resources, sizeof(resources), "%s/../Resources", exec_path);
    chdir(resources);

    // Exec Homebrew Python — uses OpenSSL 3.6.1 (compatible with macOS 26 xzone malloc)
    char *python = "/opt/homebrew/bin/python3.14";
    char *args[] = { python, main_py, NULL };
    execv(python, args);

    // Only reached if execv fails
    fprintf(stderr, "Drive Uploader: failed to launch Python at %s\n", python);
    perror("execv");
    return 1;
}
CSRC

# Compile the launcher
clang -O2 -o "$DIST/Contents/MacOS/Drive Uploader" "$LAUNCHER_SRC"
rm -f "$LAUNCHER_SRC"

# ── Source files ────────────────────────────────────────────────────────────
for f in main.py drive.py state.py config.py drive_accounts.py mailer.py sender_profile.py requirements.txt; do
  cp "$SCRIPT_DIR/$f" "$DIST/Contents/Resources/"
done

# Copy credentials + token if present
for f in credentials.json token.json; do
  [ -f "$SCRIPT_DIR/$f" ] && cp "$SCRIPT_DIR/$f" "$DIST/Contents/Resources/"
done

# Ad-hoc sign AFTER all files are in place so the signature covers everything
codesign --deep --force -s - "$DIST" 2>/dev/null && echo "  (ad-hoc signed)" || echo "  (signing skipped)"

echo ""
echo "Done!  →  dist/$APP_NAME.app"
echo ""
echo "To install:  cp -r \"dist/$APP_NAME.app\" /Applications/"
echo "To update:   re-run this script"
