"""PyInstaller entry point.

A package's ``__main__.py`` can't be the frozen entry script directly: it runs
as top-level ``__main__`` with no parent package, so its ``from .server import
run`` relative import fails ("attempted relative import with no known parent
package"). Import the package's main absolutely instead — once ``meta_assistant``
is imported as a package, its internal relative imports resolve normally.
"""

from meta_assistant.__main__ import main

if __name__ == "__main__":
    main()
