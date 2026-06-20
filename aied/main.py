#!/usr/bin/env python3
"""
aied — AI-Assisted CLI Editor
Entry point
"""

import sys
import argparse
from pathlib import Path
from .editor import Editor


def main():
    parser = argparse.ArgumentParser(
        prog="aied",
        description="AI-assisted CLI editor powered by Gemini"
    )
    parser.add_argument("file", nargs="?", help="File to open")
    parser.add_argument("--readme", action="store_true", help="Generate README.md for current project")
    parser.add_argument("--version", action="version", version="aied 0.1.0")

    args = parser.parse_args()

    if args.readme:
        from .ai import generate_readme
        generate_readme(Path.cwd())
        return

    if args.file:
        filepath = Path(args.file)
    else:
        filepath = None  # open scratch buffer

    editor = Editor(filepath)
    editor.run()


if __name__ == "__main__":
    main()
