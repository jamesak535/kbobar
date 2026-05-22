#!/bin/bash
set -e

echo "⚾ Building KBOBar..."

# Clean previous builds
rm -rf build dist .eggs

# Build the .app bundle using the venv's Python + py2app
.venv/bin/python setup.py py2app

# Fix any bundled .so that still references @rpath/libffi.8.dylib
find "dist/KBOBar.app" -name "*.so" | while read -r so; do
    if otool -L "$so" 2>/dev/null | grep -q '@rpath/libffi'; then
        install_name_tool -change @rpath/libffi.8.dylib /usr/lib/libffi.dylib "$so"
        codesign --force --sign - "$so"
    fi
done

# Bundle OpenSSL dylibs (needed by _ssl.so for HTTPS)
MINIFORGE_LIB="/opt/homebrew/Caskroom/miniforge/base/lib"
FRAMEWORKS="dist/KBOBar.app/Contents/Frameworks"
SSL_SO="dist/KBOBar.app/Contents/Resources/lib/python3.10/lib-dynload/_ssl.so"

cp "$MINIFORGE_LIB/libssl.3.dylib"    "$FRAMEWORKS/"
cp "$MINIFORGE_LIB/libcrypto.3.dylib" "$FRAMEWORKS/"

# Fix install names of the bundled dylibs
install_name_tool -id "@executable_path/../Frameworks/libssl.3.dylib"    "$FRAMEWORKS/libssl.3.dylib"
install_name_tool -id "@executable_path/../Frameworks/libcrypto.3.dylib" "$FRAMEWORKS/libcrypto.3.dylib"
install_name_tool \
    -change "@rpath/libcrypto.3.dylib" "@executable_path/../Frameworks/libcrypto.3.dylib" \
    "$FRAMEWORKS/libssl.3.dylib"

# Point _ssl.so at the bundled dylibs
install_name_tool \
    -change "@rpath/libssl.3.dylib"    "@executable_path/../Frameworks/libssl.3.dylib" \
    -change "@rpath/libcrypto.3.dylib" "@executable_path/../Frameworks/libcrypto.3.dylib" \
    "$SSL_SO"

codesign --force --sign - "$FRAMEWORKS/libcrypto.3.dylib"
codesign --force --sign - "$FRAMEWORKS/libssl.3.dylib"
codesign --force --sign - "$SSL_SO"

# Sign the app
codesign --force --deep --sign - "dist/KBOBar.app"

echo ""
echo "✅ Build complete: dist/KBOBar.app"
echo "   Run with: open \"dist/KBOBar.app\""
