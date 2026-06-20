# yaate — Yet Another AI-assisted Text Editor (WIP)

A lightweight terminal editor powered by Gemini AI. Call it like `nano` or `vim`.

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
| `Ctrl+T` | AI-Comment current line with explanation |
| `Ctrl+E` | AI-Explain code or error on current line |
| `(Automatic)` | AI-Analyze file — code smell detector with inline markers as you type |
| `Ctrl+F` | AI-Format file (local formatter → Gemini fallback) |
| `Ctrl+D` | AI-Generate docstring for current function |
| `Ctrl+C` | Toggle AI-Chat panel |
| `Ctrl+Y` | Yank (insert) the last AI Chat response directly into your code |

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

### Linux & macOS (pip)
For standard Linux distributions (Ubuntu, Arch, Fedora, etc.) and macOS, install using `pip`:
```bash
git clone https://github.com/jmnlxh/yaate
cd yaate
pip install .

# Setup your API key
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY

yaate file.py
```

<details>
<summary>NixOS</summary>

You can build the native executable natively using the Nix flake:
```bash
git clone https://github.com/jmnlxh/yaate
cd yaate
nix build
./result/bin/yaate file.py
```
Or run a development shell:
```bash
nix develop
yaate file.py
```
</details>

### Windows (pip)
Because `yaate` is built entirely in Python using `prompt_toolkit`, it is 100% natively compatible with Windows. It works flawlessly in CMD, PowerShell, and Windows Terminal.
```bash
git clone https://github.com/jmnlxh/yaate
cd yaate
pip install .

# Setup your API key
copy .env.example .env
# Edit .env and add your GEMINI_API_KEY

yaate file.py
```

## Requirements

- Python 3.11+
- Gemini API key (free tier: [aistudio.google.com](https://aistudio.google.com))
- Optional local formatters: `black`, `alejandra`, `prettier`, `shfmt`

## Token Usage

All features dynamically fetch the best available Gemini model linked to your API key (defaulting to `gemini-1.5-flash` or `gemini-pro`). The free tier gives 1M tokens/day.

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

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.
