from setuptools import setup

APP = ["kbo_bar.py"]

OPTIONS = {
    "argv_emulation": False,
    "packages": [
        "rumps",
        "requests",
        "certifi",
        "charset_normalizer",
        "idna",
        "urllib3",
        "tzdata",
    ],
    "includes": ["kbo_scraper"],
    "plist": {
        "LSUIElement": True,
        "CFBundleName": "KBOBar",
        "CFBundleDisplayName": "KBO Bar",
        "CFBundleIdentifier": "com.james.kbobar",
        "CFBundleVersion": "1.0.1",
        "CFBundleShortVersionString": "1.0.1",
        "NSHighResolutionCapable": True,
    },
}

setup(
    name="KBOBar",
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)