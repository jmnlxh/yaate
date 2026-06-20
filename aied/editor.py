"""
editor.py — Core editor using prompt_toolkit
Handles the buffer, keybinds, panels, and feature dispatch.
"""

import subprocess
import threading
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, ConditionalContainer
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.filters import Condition
from prompt_toolkit.styles import Style
from pygments.lexers import get_lexer_by_name, TextLexer

from .detector import detect_mode, get_comment_syntax, get_formatter, mode_display_name
from . import ai


# ── Pygments lexer map ─────────────────────────────────────────────────────────

LEXER_MAP = {
    "python":               "python",
    "javascript":           "javascript",
    "typescript":           "typescript",
    "javascript-react":     "jsx",
    "typescript-react":     "tsx",
    "rust":                 "rust",
    "c":                    "c",
    "cpp":                  "cpp",
    "go":                   "go",
    "lua":                  "lua",
    "bash":                 "bash",
    "zsh":                  "bash",
    "fish":                 "fish",
    "nix":                  "nix",
    "nixos-flake":          "nix",
    "nixos-config":         "nix",
    "nixos-home-manager":   "nix",
    "toml":                 "toml",
    "yaml":                 "yaml",
    "json":                 "json",
    "html":                 "html",
    "css":                  "css",
    "scss":                 "scss",
    "markdown":             "markdown",
    "sql":                  "sql",
    "dockerfile":           "docker",
    "arduino":              "cpp",
    "makefile":             "makefile",
    "kotlin":               "kotlin",
    "java":                 "java",
    "ruby":                 "ruby",
    "dart":                 "dart",
    "php":                  "php",
}


def _get_lexer(mode: str):
    name = LEXER_MAP.get(mode)
    if name:
        try:
            return PygmentsLexer(get_lexer_by_name(name).__class__)
        except Exception:
            pass
    return PygmentsLexer(TextLexer)


# ── Smell gutter markers ───────────────────────────────────────────────────────

SEVERITY_ICON = {
    "info":  "🔵",
    "warn":  "⚠ ",
    "error": "🔴",
}


# ── Editor ─────────────────────────────────────────────────────────────────────

class Editor:
    def __init__(self, filepath: Path | None):
        self.filepath = filepath
        self.mode = detect_mode(filepath)
        self.modified = False
        self.ghost_text = ""          # autocomplete suggestion
        self.smells: list[dict] = []  # code smell markers { line, severity, issue, fix }
        self.chat_history: list[dict] = []

        # Panel state
        self.bottom_panel_text = ""
        self.bottom_panel_visible = False
        self.chat_panel_visible = False
        self.chat_input_text = ""

        # Load file
        initial_text = ""
        if filepath and filepath.exists():
            try:
                initial_text = filepath.read_text()
            except OSError as e:
                initial_text = f"# Error reading file: {e}\n"

        # Main editor buffer
        self.buffer = Buffer(
            name="main",
            document=Document(initial_text),
            multiline=True,
            on_text_changed=self._on_text_changed,
        )

        self._build_layout()
        self._build_keybindings()
        self._build_style()

        self.app = Application(
            layout=self.layout,
            key_bindings=self.kb,
            style=self.style,
            full_screen=True,
            mouse_support=True,
        )

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build_layout(self):
        # Status bar text
        def status_text():
            fname = self.filepath.name if self.filepath else "[scratch]"
            mod = " ●" if self.modified else ""
            mode_label = mode_display_name(self.mode)
            ghost = f"  ┆ {self.ghost_text[:40]}…" if self.ghost_text else ""
            smell_count = len(self.smells)
            smells_label = f"  ┆ {smell_count} issue{'s' if smell_count != 1 else ''}" if smell_count else ""
            return f" {fname}{mod}  ┆  {mode_label}{smells_label}{ghost}   [Ctrl+H help]"

        # Gutter (line numbers + smell markers)
        def gutter_text():
            lines = self.buffer.text.splitlines()
            smell_lines = {s["line"]: s["severity"] for s in self.smells}
            result = []
            for i, _ in enumerate(lines):
                lineno = i + 1
                if lineno in smell_lines:
                    icon = SEVERITY_ICON.get(smell_lines[lineno], "  ")
                    result.append(("class:gutter.smell", f" {icon} {lineno:4d} │ "))
                else:
                    result.append(("class:gutter", f"      {lineno:4d} │ "))
                result.append(("", "\n"))
            return result

        self.layout = Layout(
            HSplit([
                # Status bar
                Window(
                    content=FormattedTextControl(status_text),
                    height=1,
                    style="class:statusbar",
                ),
                # Editor row: gutter + buffer
                VSplit([
                    Window(
                        content=FormattedTextControl(gutter_text),
                        width=12,
                        style="class:gutter",
                    ),
                    Window(
                        content=BufferControl(
                            buffer=self.buffer,
                            lexer=_get_lexer(self.mode),
                            focus_on_click=True,
                        ),
                    ),
                ]),
                # Bottom info panel (error explainer, smell list, etc.)
                ConditionalContainer(
                    content=Window(
                        content=FormattedTextControl(lambda: self.bottom_panel_text),
                        height=8,
                        style="class:panel",
                    ),
                    filter=Condition(lambda: self.bottom_panel_visible),
                ),
                # Chat input panel
                ConditionalContainer(
                    content=HSplit([
                        Window(
                            content=FormattedTextControl(lambda: "─── AI Chat (Enter send, Esc close) ─────────────────────"),
                            height=1,
                            style="class:panel.header",
                        ),
                        Window(
                            content=FormattedTextControl(lambda: f"▶ {self.chat_input_text}█"),
                            height=1,
                            style="class:panel",
                        ),
                    ]),
                    filter=Condition(lambda: self.chat_panel_visible),
                ),
                # Help bar
                Window(
                    content=FormattedTextControl(
                        " ^Space autocomplete  ^/ comment  ^E error  ^A analyze  ^F format  ^D docstring  ^C chat  ^S save  ^Q quit"
                    ),
                    height=1,
                    style="class:helpbar",
                ),
            ])
        )

    # ── Keybindings ────────────────────────────────────────────────────────────

    def _build_keybindings(self):
        kb = KeyBindings()

        # ── Ctrl+S — Save ──────────────────────────────────────────────────────
        @kb.add("c-s")
        def save(event):
            self._save()

        # ── Ctrl+Q — Quit ──────────────────────────────────────────────────────
        @kb.add("c-q")
        def quit(event):
            event.app.exit()

        # ── Ctrl+Space — Autocomplete ──────────────────────────────────────────
        @kb.add("c-space")
        def autocomplete(event):
            self._run_in_thread(self._do_autocomplete)

        # ── Tab — Accept ghost text ────────────────────────────────────────────
        @kb.add("tab")
        def accept_ghost(event):
            if self.ghost_text:
                self.buffer.insert_text(self.ghost_text)
                self.ghost_text = ""
                self._refresh()
            else:
                self.buffer.insert_text("    ")  # 4-space indent fallback

        # ── Escape — Dismiss ghost / close panels ──────────────────────────────
        @kb.add("escape")
        def escape(event):
            if self.ghost_text:
                self.ghost_text = ""
                self._refresh()
            elif self.chat_panel_visible:
                self.chat_panel_visible = False
                self.chat_input_text = ""
                self._refresh()
            elif self.bottom_panel_visible:
                self.bottom_panel_visible = False
                self._refresh()

        # ── Ctrl+/ — Comment current line ─────────────────────────────────────
        @kb.add("c-/")
        def comment_line(event):
            self._run_in_thread(self._do_comment_line)

        # ── Ctrl+E — Error explainer ───────────────────────────────────────────
        @kb.add("c-e")
        def error_explain(event):
            self._run_in_thread(self._do_error_explain)

        # ── Ctrl+Shift+A — Code smell detector ────────────────────────────────
        @kb.add("c-A")
        def smell_detect(event):
            self._run_in_thread(self._do_smell_detect)

        # ── Ctrl+Shift+F — Format file ────────────────────────────────────────
        @kb.add("c-F")
        def format_file(event):
            self._run_in_thread(self._do_format)

        # ── Ctrl+D — Docstring ────────────────────────────────────────────────
        @kb.add("c-d")
        def docstring(event):
            self._run_in_thread(self._do_docstring)

        # ── Ctrl+C — AI Chat panel ─────────────────────────────────────────────
        @kb.add("c-c")
        def chat(event):
            self.chat_panel_visible = not self.chat_panel_visible
            self.bottom_panel_visible = False
            self._refresh()

        # ── Chat: character input ──────────────────────────────────────────────
        @kb.add("<any>", filter=Condition(lambda: self.chat_panel_visible))
        def chat_input(event):
            key = event.key_sequence[0].key
            if key == "enter":
                if self.chat_input_text.strip():
                    self._run_in_thread(self._do_chat)
            elif key == "backspace":
                self.chat_input_text = self.chat_input_text[:-1]
                self._refresh()
            elif len(key) == 1:
                self.chat_input_text += key
                self._refresh()

        # ── Ctrl+H — Toggle help ───────────────────────────────────────────────
        @kb.add("c-h")
        def help(event):
            self._show_panel(
                "─── Keybindings ──────────────────────────────────────────\n"
                "  Ctrl+Space      Autocomplete (Tab accept, Esc dismiss)\n"
                "  Ctrl+/          Comment current line\n"
                "  Ctrl+E          Explain error on current line\n"
                "  Ctrl+A          Analyze file (code smell detector)\n"
                "  Ctrl+F          Format file\n"
                "  Ctrl+D          Generate docstring for current function\n"
                "  Ctrl+C          Toggle AI chat panel\n"
                "  Ctrl+S          Save\n"
                "  Ctrl+Q          Quit\n"
                "  Esc             Dismiss ghost text / close panels\n"
                "──────────────────────────────────────────────────────────"
            )

        self.kb = kb

    # ── Style ──────────────────────────────────────────────────────────────────

    def _build_style(self):
        self.style = Style.from_dict({
            "statusbar":        "bg:#1e2030 #8090aa bold",
            "helpbar":          "bg:#161622 #4a5568",
            "gutter":           "bg:#161622 #3d4455",
            "gutter.smell":     "bg:#161622 #e0af68",
            "panel":            "bg:#0f0e16 #a9b1d6",
            "panel.header":     "bg:#1e2030 #8090aa",
        })

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _refresh(self):
        if self.app and self.app.is_running:
            self.app.invalidate()

    def _show_panel(self, text: str):
        self.bottom_panel_text = text
        self.bottom_panel_visible = True
        self._refresh()

    def _on_text_changed(self, _):
        self.modified = True
        self.ghost_text = ""

    def _save(self):
        if not self.filepath:
            self._show_panel("No file path — use 'aied <filename>' to open with a path.")
            return
        try:
            self.filepath.write_text(self.buffer.text)
            self.modified = False
            self._show_panel(f"✓ Saved: {self.filepath}")
        except OSError as e:
            self._show_panel(f"✗ Save failed: {e}")

    def _run_in_thread(self, fn):
        """Run AI call in background thread so UI doesn't freeze."""
        self._show_panel("⟳ Thinking…")
        thread = threading.Thread(target=fn, daemon=True)
        thread.start()

    def _current_line_index(self) -> int:
        """Return 0-based index of cursor line."""
        doc = self.buffer.document
        return doc.cursor_position_row

    def _file_lines(self) -> list[str]:
        return self.buffer.text.splitlines()

    # ── AI Feature Implementations ─────────────────────────────────────────────

    def _do_autocomplete(self):
        try:
            lines = self._file_lines()
            row = self._current_line_index()
            suggestion = ai.autocomplete(lines, row, self.mode)
            self.ghost_text = suggestion
            self._show_panel(f"  Suggestion ready — Tab to accept, Esc to dismiss\n  {suggestion[:120]}")
        except Exception as e:
            self._show_panel(f"✗ Autocomplete error: {e}")

    def _do_comment_line(self):
        try:
            lines = self._file_lines()
            row = self._current_line_index()
            target = lines[row] if row < len(lines) else ""

            if not target.strip():
                self._show_panel("  Nothing to comment on this line.")
                return

            above = "\n".join(lines[max(0, row - 10): row])
            below = "\n".join(lines[row + 1: min(len(lines), row + 10)])

            explanation = ai.explain_line(target, above, below, self.mode)

            prefix, suffix = get_comment_syntax(self.mode)
            if suffix:
                comment = f"{prefix} {explanation} {suffix}"
            else:
                comment = f"{prefix} {explanation}"

            # Insert comment line above current line
            lines.insert(row, comment)
            new_text = "\n".join(lines)
            self.buffer.set_document(
                Document(new_text, cursor_position=self.buffer.cursor_position + len(comment) + 1),
                bypass_readonly=True,
            )
            self.modified = True
            self.bottom_panel_visible = False
            self._refresh()
        except Exception as e:
            self._show_panel(f"✗ Comment error: {e}")

    def _do_error_explain(self):
        try:
            lines = self._file_lines()
            row = self._current_line_index()
            target_line = lines[row] if row < len(lines) else ""

            result = ai.explain_error(target_line, lines, row, self.mode)

            panel = (
                f"─── Error Explainer ─────────────────────────────────────\n"
                f"  Line {row + 1} │ {target_line.strip()[:60]}\n\n"
                f"  WHAT: {result['what']}\n"
                f"  WHY:  {result['why']}\n"
                f"  FIX:  {result['fix']}\n"
                f"  LINE: {result['line']}\n"
                f"─────────────────────────────────────────────────────────"
            )
            self._show_panel(panel)
        except Exception as e:
            self._show_panel(f"✗ Error explainer failed: {e}")

    def _do_smell_detect(self):
        try:
            lines = self._file_lines()
            self.smells = ai.smell_detect(lines, self.mode)

            if not self.smells:
                self._show_panel("✓ No issues found.")
                return

            rows = [f"─── Code Analysis ── {len(self.smells)} issue(s) found ──────────────────"]
            for s in self.smells:
                icon = SEVERITY_ICON.get(s["severity"], "  ")
                rows.append(f"  [{icon}]  Line {s['line']:4d}  │  {s['issue']}")
                if s.get("fix"):
                    rows.append(f"              Fix: {s['fix']}")
            rows.append("─────────────────────────────────────────────────────────")
            rows.append("  Ctrl+E on a flagged line for detailed explanation")

            self._show_panel("\n".join(rows))
        except Exception as e:
            self._show_panel(f"✗ Analysis error: {e}")

    def _do_format(self):
        try:
            formatter_cmd = get_formatter(self.mode)
            content = self.buffer.text
            formatted = None

            if formatter_cmd:
                try:
                    result = subprocess.run(
                        formatter_cmd,
                        input=content,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if result.returncode == 0:
                        formatted = result.stdout
                    else:
                        # Local formatter failed, fall through to Gemini
                        pass
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass

            if not formatted:
                formatted = ai.format_file(content, self.mode)

            cur = self.buffer.cursor_position
            self.buffer.set_document(
                Document(formatted, cursor_position=min(cur, len(formatted))),
                bypass_readonly=True,
            )
            self.modified = True
            self._show_panel("✓ File formatted.")
        except Exception as e:
            self._show_panel(f"✗ Format error: {e}")

    def _do_docstring(self):
        try:
            lines = self._file_lines()
            row = self._current_line_index()

            # Grab function block — look forward for end of function
            block_lines = []
            for i in range(row, min(len(lines), row + 40)):
                block_lines.append(lines[i])
                if i > row and lines[i].strip() == "":
                    break

            block = "\n".join(block_lines)
            docstring = ai.generate_docstring(block, self.mode)

            prefix, suffix = get_comment_syntax(self.mode)

            # Insert docstring below the function definition line
            insert_at = row + 1
            lines.insert(insert_at, docstring)
            new_text = "\n".join(lines)
            self.buffer.set_document(
                Document(new_text),
                bypass_readonly=True,
            )
            self.modified = True
            self.bottom_panel_visible = False
            self._refresh()
        except Exception as e:
            self._show_panel(f"✗ Docstring error: {e}")

    def _do_chat(self):
        try:
            user_msg = self.chat_input_text.strip()
            self.chat_input_text = ""
            self.chat_history.append({"role": "user", "text": user_msg})

            response = ai.chat(
                user_msg,
                self.buffer.text,
                self.mode,
                self.chat_history,
            )
            self.chat_history.append({"role": "model", "text": response})

            self._show_panel(
                f"─── AI Chat ──────────────────────────────────────────────\n"
                f"  You: {user_msg}\n\n"
                f"  AI:  {response[:400]}\n"
                f"─────────────────────────────────────────────────────────"
            )
            self.chat_panel_visible = False
        except Exception as e:
            self._show_panel(f"✗ Chat error: {e}")

    # ── Run ────────────────────────────────────────────────────────────────────

    def run(self):
        self.app.run()
