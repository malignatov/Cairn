"""PyInstaller entry point for the Cairn menu-bar .app.

Same reasoning as packaging/cairn_entry.py: a package submodule can't be the
frozen entry script directly, so import the app's main absolutely.
"""

from meta_assistant.macos_app import main

if __name__ == "__main__":
    main()
