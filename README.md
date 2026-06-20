# yaate — Yet Another AI-assisted Text Editor

A lightweight terminal editor powered by Gemini 1.5 Flash. Call it like `nano` or `vim`.

```bash
yaate file.py
yaate flake.nix
yaate /etc/nginx/nginx.conf
```

## Features

| Keybind | Feature |
|---|---|
| `Ctrl+O` | Save (WriteOut) |
| `Ctrl+X` | Quit (Exit) |
| `Ctrl+W` | Find (Where Is) |
| `Ctrl+_` | Go To Line |
| `(Automatic)` | AI autocomplete as you type — Tab accept, Esc dismiss |
| `Ctrl+/` | AI-Comment current line with explanation |
| `Ctrl+E` | AI-Explain error on current line |
| `(Automatic)` | AI-Analyze file — code smell detector with gutter markers as you type |
| `Ctrl+F` | AI-Format file (local formatter → Gemini fallback) |
| `Ctrl+D` | AI-Generate docstring for current function |
| `Ctrl+C` | Toggle AI-Chat panel |

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
git clone https://github.com/jmnlxh/yaate
cd yaate
pip install -e .
cp .env.example .env
# add your GEMINI_API_KEY to .env
yaate file.py
```

### NixOS (flake devShell)
```bash
nix develop
yaate file.py
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
yaate/
├── yaate/
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
