"""
detector.py — Config Determiner
Detects file mode based on extension, filename, directory, and shebang.
All AI features inherit the detected mode as context.
"""

from pathlib import Path


# Extension → mode map
EXT_MAP = {
    ".py":      "python",
    ".js":      "javascript",
    ".ts":      "typescript",
    ".jsx":     "javascript-react",
    ".tsx":     "typescript-react",
    ".rs":      "rust",
    ".c":       "c",
    ".cpp":     "cpp",
    ".h":       "c-header",
    ".go":      "go",
    ".lua":     "lua",
    ".sh":      "bash",
    ".bash":    "bash",
    ".zsh":     "zsh",
    ".fish":    "fish",
    ".nix":     "nix",
    ".toml":    "toml",
    ".yaml":    "yaml",
    ".yml":     "yaml",
    ".json":    "json",
    ".html":    "html",
    ".css":     "css",
    ".scss":    "scss",
    ".md":      "markdown",
    ".ino":     "arduino",
    ".dart":    "dart",
    ".kt":      "kotlin",
    ".java":    "java",
    ".rb":      "ruby",
    ".php":     "php",
    ".sql":     "sql",
    ".r":       "r",
    ".tex":     "latex",
    ".xml":     "xml",
    ".conf":    "config",
    ".cfg":     "config",
    ".ini":     "ini",
    ".env":     "dotenv",
}

# Exact filename → mode map (checked before extension)
FILENAME_MAP = {
    "flake.nix":        "nixos-flake",
    "configuration.nix":"nixos-config",
    "home.nix":         "nixos-home-manager",
    "Makefile":         "makefile",
    "makefile":         "makefile",
    "Dockerfile":       "dockerfile",
    "docker-compose.yml": "docker-compose",
    "docker-compose.yaml": "docker-compose",
    ".gitignore":       "gitignore",
    ".gitconfig":       "gitconfig",
    "CMakeLists.txt":   "cmake",
    "package.json":     "nodejs-package",
    "pyproject.toml":   "python-project",
    "Cargo.toml":       "rust-project",
    "go.mod":           "go-module",
}

# Directory segment → mode override
DIR_MAP = {
    "nginx":        "nginx-config",
    "systemd":      "systemd-unit",
    "apache2":      "apache-config",
    "apache":       "apache-config",
    "udev":         "udev-rules",
    "cron":         "cron",
    "ssh":          "ssh-config",
    "hypr":         "hyprland-config",
    "waybar":       "waybar-config",
    "sddm":         "sddm-config",
    "kitty":        "kitty-config",
    "nvim":         "neovim-lua-config",
    "rofi":         "rofi-config",
}

# Shebang → mode map
SHEBANG_MAP = {
    "python":   "python",
    "python3":  "python",
    "bash":     "bash",
    "sh":       "bash",
    "zsh":      "zsh",
    "fish":     "fish",
    "node":     "javascript",
    "ruby":     "ruby",
    "perl":     "perl",
    "lua":      "lua",
}

# Mode → comment syntax
COMMENT_SYNTAX = {
    "python":               ("#", None),
    "bash":                 ("#", None),
    "zsh":                  ("#", None),
    "fish":                 ("#", None),
    "nix":                  ("#", None),
    "nixos-flake":          ("#", None),
    "nixos-config":         ("#", None),
    "nixos-home-manager":   ("#", None),
    "ruby":                 ("#", None),
    "r":                    ("#", None),
    "toml":                 ("#", None),
    "yaml":                 ("#", None),
    "makefile":             ("#", None),
    "dockerfile":           ("#", None),
    "dotenv":               ("#", None),
    "gitignore":            ("#", None),
    "javascript":           ("//", None),
    "typescript":           ("//", None),
    "javascript-react":     ("//", None),
    "typescript-react":     ("//", None),
    "rust":                 ("//", None),
    "c":                    ("//", None),
    "cpp":                  ("//", None),
    "c-header":             ("//", None),
    "go":                   ("//", None),
    "java":                 ("//", None),
    "kotlin":               ("//", None),
    "dart":                 ("//", None),
    "php":                  ("//", None),
    "arduino":              ("//", None),
    "lua":                  ("--", None),
    "sql":                  ("--", None),
    "html":                 ("<!--", "-->"),
    "xml":                  ("<!--", "-->"),
    "css":                  ("/*", "*/"),
    "scss":                 ("//", None),
    "latex":                ("%", None),
    "matlab":               ("%", None),
    "ini":                  (";", None),
    "config":               ("#", None),
}

# Mode → local formatter command
FORMATTER_MAP = {
    "python":           ["black", "-"],
    "javascript":       ["prettier", "--parser", "babel", "--stdin-filepath", "file.js"],
    "typescript":       ["prettier", "--parser", "typescript", "--stdin-filepath", "file.ts"],
    "rust":             ["rustfmt"],
    "nix":              ["alejandra", "-"],
    "nixos-flake":      ["alejandra", "-"],
    "nixos-config":     ["alejandra", "-"],
    "nixos-home-manager":["alejandra", "-"],
    "json":             ["python3", "-m", "json.tool"],
    "yaml":             ["yamlfmt", "-"],
    "bash":             ["shfmt", "-"],
    "css":              ["prettier", "--parser", "css"],
    "html":             ["prettier", "--parser", "html"],
    "sql":              ["sqlformat", "--reindent", "--keywords", "upper", "-"],
}


def detect_mode(filepath: Path | None) -> str:
    """
    Detect the file mode using a priority chain:
    1. Exact filename match
    2. Extension match
    3. Directory segment match
    4. Shebang read
    5. Fallback: plaintext
    """
    if filepath is None:
        return "plaintext"

    name = filepath.name
    ext = filepath.suffix.lower()
    parts = [p.lower() for p in filepath.parts]

    # 1. Exact filename
    if name in FILENAME_MAP:
        return FILENAME_MAP[name]

    # 2. Extension
    if ext in EXT_MAP:
        mode = EXT_MAP[ext]
        # Refine .nix files by directory
        if mode == "nix":
            for part in parts:
                if part in DIR_MAP:
                    return DIR_MAP[part]
        return mode

    # 3. Directory segments
    for part in parts:
        if part in DIR_MAP:
            return DIR_MAP[part]

    # 4. Shebang
    try:
        with open(filepath, "r", errors="ignore") as f:
            first_line = f.readline().strip()
        if first_line.startswith("#!"):
            for key, mode in SHEBANG_MAP.items():
                if key in first_line:
                    return mode
    except (OSError, PermissionError):
        pass

    # 5. Fallback
    return "plaintext"


def get_comment_syntax(mode: str) -> tuple[str, str | None]:
    """Return (prefix, suffix) for inline comments in this mode."""
    return COMMENT_SYNTAX.get(mode, ("#", None))


def get_formatter(mode: str) -> list[str] | None:
    """Return local formatter command list, or None if not available."""
    return FORMATTER_MAP.get(mode)


def mode_display_name(mode: str) -> str:
    """Human-readable mode label for the status bar."""
    return mode.replace("-", " ").replace("_", " ").title()
