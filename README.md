# aied — AI-Assisted CLI Editor

A lightweight terminal editor powered by Gemini 1.5 Flash. Call it like `nano` or `vim`.

```bash
aied file.py
aied flake.nix
aied /etc/nginx/nginx.conf
```

## Features

| Keybind | Feature |
|---|---|
| `Ctrl+Space` | AI autocomplete — Tab accept, Esc dismiss |
| `Ctrl+/` | Comment current line with AI explanation |
| `Ctrl+E` | Explain error on current line |
| `Ctrl+A` | Analyze file — code smell detector with gutter markers |
| `Ctrl+F` | Format file (local formatter → Gemini fallback) |
| `Ctrl+D` | Generate docstring for current function |
| `Ctrl+C` | Toggle AI chat panel |
| `Ctrl+S` | Save |
| `Ctrl+Q` | Quit |

## Config Recognizer

Detects file mode automatically from filename, extension, directory, or shebang — no manual language selection.

```
flake.nix            → nixos-flake
/etc/nginx/*.conf    → nginx-config
~/.config/hypr/*     → hyprland-config
file.ino             → arduino
file.py              → python
# !/usr/bin/env bash → bash
```

## Install

### Standard (pip)
```bash
git clone https://github.com/jmnlxh/aied
cd aied
pip install -e .
cp .env.example .env
# add your GEMINI_API_KEY to .env
aied file.py
```

### NixOS (flake devShell)
```bash
nix develop
aied file.py
```

### NixOS (system package)
Add to your `flake.nix` inputs and `environment.systemPackages`.

## Requirements

- Python 3.11+
- Gemini API key (free tier: [aistudio.google.com](https://aistudio.google.com))
- Optional local formatters: `black`, `alejandra`, `prettier`, `shfmt`

## Token Usage

All features use `gemini-1.5-flash` — the free tier gives 1M tokens/day.

| Feature | ~Tokens per call |
|---|---|
| Autocomplete | 200–600 |
| Line comment | 150–400 |
| Error explain | 300–700 |
| Code smell detect | 500–2000 |
| Format file | 1000–5000 |
| Chat | 200–800 |

## Project Structure

```
aied/
├── aied/
│   ├── __init__.py
│   ├── main.py       ← entry point, CLI args
│   ├── editor.py     ← prompt_toolkit editor, keybinds, layout
│   ├── ai.py         ← all Gemini prompts and API calls
│   └── detector.py   ← config/language detection
├── pyproject.toml
├── flake.nix
├── .env.example
└── .gitignore
```
