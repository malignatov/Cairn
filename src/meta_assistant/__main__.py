import sys

from .server import run


def main() -> None:
    """Entry point. Transport defaults to stdio when frozen, HTTP from source;
    `--stdio` / `--http` force one explicitly (the Desktop config passes
    `--stdio`)."""
    args = sys.argv[1:]
    transport: str | None = None
    if "--stdio" in args:
        transport = "stdio"
    elif "--http" in args:
        transport = "streamable-http"
    run(transport=transport)


if __name__ == "__main__":
    main()
