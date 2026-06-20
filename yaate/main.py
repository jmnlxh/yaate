#!/usr/bin/env python3
"""
yaate — Yet Another AI-assisted Text Editor
Entry point
"""

import sys
import argparse
from pathlib import Path
from .editor import Editor


def main():
    parser = argparse.ArgumentParser(
        prog="yaate",
        description="Yet Another AI-assisted text editor powered by Gemini"
    )
    parser.add_argument("file", nargs="?", help="File to open")
    parser.add_argument("--readme", action="store_true", help="Generate README.md for current project")
    parser.add_argument("--version", action="version", version="yaate 0.1.0")

    args = parser.parse_args()

    if args.readme:
        from .ai import generate_readme
        generate_readme(Path.cwd())
        return

    if args.file:
        filepath = Path(args.file)
    else:
        filepath = None  # open scratch buffer

    try:
        editor = Editor(filepath)
        editor.run()
    finally:
        # Guarantee terminal is fully reset (mouse tracking off, alt screen off)
        sys.stdout.write("\x1b[?1049l\x1b[?1000l\x1b[?1002l\x1b[?1015l\x1b[?1006l\x1b[0m")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
