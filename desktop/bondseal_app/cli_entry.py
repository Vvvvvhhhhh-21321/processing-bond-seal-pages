from __future__ import annotations

from bondseal_app.runtime import ensure_core_importable


def main() -> int:
    ensure_core_importable()
    from bond_seal_pages.cli import main as core_main

    return core_main()


if __name__ == "__main__":
    raise SystemExit(main())
