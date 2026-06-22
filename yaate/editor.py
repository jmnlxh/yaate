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
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, ConditionalContainer, WindowAlign
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.layout.margins import NumberedMargin
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.filters import Condition
from prompt_toolkit.styles import Style
from pygments.lexers import get_lexer_by_name, TextLexer

from .detector import detect_mode, get_comment_syntax, get_formatter, mode_display_name
from . import ai

class SmellProcessor(Processor):
    def __init__(self, editor):
        self.editor = editor

    def apply_transformation(self, transformation_input):
        smell_lines = {s["line"]: s["severity"] for s in self.editor.smells}
        lineno = transformation_input.lineno + 1
        if lineno in smell_lines:
            icon = SEVERITY_ICON.get(smell_lines[lineno], "⚠")
            fragments = list(transformation_input.fragments)
            fragments.append(("class:gutter.smell", f"  [{icon} smell]"))
            return Transformation(fragments)
        return Transformation(transformation_input.fragments)

class GhostTextProcessor(Processor):
    def __init__(self, editor):
        self.editor = editor

    def apply_transformation(self, transformation_input):
        ghost_text = self.editor.ghost_text
        if not ghost_text:
            return Transformation(transformation_input.fragments)

        doc = transformation_input.document
        if transformation_input.lineno == doc.cursor_position_row:
            line = doc.lines[transformation_input.lineno]
            if doc.cursor_position_col == len(line):
                fragments = list(transformation_input.fragments)
                fragments.append(("class:ghostbar", ghost_text))
                return Transformation(fragments)
                
        return Transformation(transformation_input.fragments)


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
        self.find_panel_visible = False
        self.find_input_text = ""
        self.jump_panel_visible = False
        self.jump_input_text = ""

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
        self.layout = Layout(
            HSplit([
                # Title bar (like nano)
                VSplit([
                    Window(
                        content=FormattedTextControl(lambda: "  yaate 0.1.0"),
                        align=WindowAlign.LEFT,
                        style="class:titlebar",
                    ),
                    Window(
                        content=FormattedTextControl(lambda: f"File: {self.filepath.name if self.filepath else 'New Buffer'} [{mode_display_name(self.mode)}]{' (Modified)' if self.modified else ''}"),
                        align=WindowAlign.CENTER,
                        style="class:titlebar",
                    ),
                    Window(
                        content=FormattedTextControl(lambda: ""),
                        align=WindowAlign.RIGHT,
                        style="class:titlebar",
                    ),
                ], height=1),
                # Editor row: buffer with numbered margin
                Window(
                    content=BufferControl(
                        buffer=self.buffer,
                        lexer=_get_lexer(self.mode),
                        focus_on_click=True,
                        input_processors=[GhostTextProcessor(self), SmellProcessor(self)],
                    ),
                    left_margins=[NumberedMargin()],
                ),
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
                # Find input panel
                ConditionalContainer(
                    content=HSplit([
                        Window(
                            content=FormattedTextControl(lambda: "─── Find (Where Is) (Enter to search, Esc to close) ───"),
                            height=1,
                            style="class:panel.header",
                        ),
                        Window(
                            content=FormattedTextControl(lambda: f"Search: {self.find_input_text}█"),
                            height=1,
                            style="class:panel",
                        ),
                    ]),
                    filter=Condition(lambda: self.find_panel_visible),
                ),
                # Jump input panel
                ConditionalContainer(
                    content=HSplit([
                        Window(
                            content=FormattedTextControl(lambda: "─── Go To Line (Enter to jump, Esc to close) ──────────"),
                            height=1,
                            style="class:panel.header",
                        ),
                        Window(
                            content=FormattedTextControl(lambda: f"Line number: {self.jump_input_text}█"),
                            height=1,
                            style="class:panel",
                        ),
                    ]),
                    filter=Condition(lambda: self.jump_panel_visible),
                ),
                # Help bar
                Window(
                    content=FormattedTextControl(
                        " ^O Save  ^X Quit  ^W Find  ^_ Line  ^T Comment  ^E Explain  ^F Format  ^C Chat  ^Y YankChat"
                    ),
                    height=1,
                    style="class:helpbar",
                ),
            ])
        )

    # ── Keybindings ────────────────────────────────────────────────────────────

    def _build_keybindings(self):
        kb = KeyBindings()

        # ── Ctrl+O — Save (WriteOut) ───────────────────────────────────────────
        @kb.add("c-o")
        def save(event):
            self._save()

        # ── Ctrl+X — Quit (Exit) ───────────────────────────────────────────────
        @kb.add("c-x")
        def quit(event):
            event.app.exit()

        # ── Ctrl+W — Find (Where Is) ───────────────────────────────────────────
        @kb.add("c-w")
        def find(event):
            self.find_panel_visible = True
            self.find_input_text = ""
            self.jump_panel_visible = False
            self.chat_panel_visible = False
            self.bottom_panel_visible = False
            self._refresh()

        # ── Ctrl+_ — Go To Line ────────────────────────────────────────────────
        @kb.add("c-_")
        def jump_to_line(event):
            self.jump_panel_visible = True
            self.jump_input_text = ""
            self.find_panel_visible = False
            self.chat_panel_visible = False
            self.bottom_panel_visible = False
            self._refresh()

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
            else:
                self.chat_panel_visible = False
                self.find_panel_visible = False
                self.jump_panel_visible = False
                self.bottom_panel_visible = False
                self._refresh()

        # ── Ctrl+T — Comment current line ─────────────────────────────────────
        @kb.add("c-t")
        def comment_line(event):
            self._run_in_thread(self._do_comment_line)

        # ── Ctrl+E — Error explainer ───────────────────────────────────────────
        @kb.add("c-e")
        def error_explain(event):
            self._run_in_thread(self._do_error_explain)

        # ── Ctrl+F — Format file ──────────────────────────────────────────────
        @kb.add("c-f")
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

        # ── Ctrl+Y — Yank (insert) last AI chat response ───────────────────────
        @kb.add("c-y")
        def yank_chat(event):
            if hasattr(self, 'chat_history') and self.chat_history:
                last_msg = self.chat_history[-1]
                if last_msg["role"] == "model":
                    self.buffer.insert_text(last_msg["text"])
                    self._show_panel("✓ Inserted AI response into code.")
                else:
                    self._show_panel("✗ Last message was not from AI.")
            else:
                self._show_panel("✗ No AI chat history to copy.")

        # ── Multi-panel character input ────────────────────────────────────────
        panel_visible = Condition(lambda: self.chat_panel_visible or self.find_panel_visible or self.jump_panel_visible)

        @kb.add("enter", filter=panel_visible)
        @kb.add("c-m", filter=panel_visible)
        def panel_enter(event):
            if self.chat_panel_visible and self.chat_input_text.strip():
                self._run_in_thread(self._do_chat)
            elif self.find_panel_visible and self.find_input_text:
                self._do_find()
            elif self.jump_panel_visible and self.jump_input_text:
                self._do_jump()

        @kb.add("backspace", filter=panel_visible)
        def panel_backspace(event):
            if self.chat_panel_visible: self.chat_input_text = self.chat_input_text[:-1]
            elif self.find_panel_visible: self.find_input_text = self.find_input_text[:-1]
            elif self.jump_panel_visible: self.jump_input_text = self.jump_input_text[:-1]
            self._refresh()

        @kb.add("<any>", filter=panel_visible)
        def multi_input(event):
            key = event.key_sequence[0].key
            if len(key) == 1:
                if self.chat_panel_visible: self.chat_input_text += key
                elif self.find_panel_visible: self.find_input_text += key
                elif self.jump_panel_visible: self.jump_input_text += key
                self._refresh()

        self.kb = kb

    # ── Style ──────────────────────────────────────────────────────────────────

    def _build_style(self):
        self.style = Style.from_dict({
            "titlebar":         "reverse bold",
            "ghostbar":         "fg:ansibrightgreen italic",
            "helpbar":          "reverse",
            "line-number":      "fg:ansigray",
            "gutter.smell":     "fg:ansiyellow bold",
            "panel":            "",
            "panel.header":     "reverse",
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
        if self.ghost_text:
            self.ghost_text = ""
            self._refresh()

        import asyncio
        loop = asyncio.get_event_loop()

        if hasattr(self, '_autocomplete_task') and self._autocomplete_task:
            self._autocomplete_task.cancel()
        if hasattr(self, '_smell_task') and self._smell_task:
            self._smell_task.cancel()
            
        async def delayed_autocomplete():
            try:
                await asyncio.sleep(0.75)
                import threading
                threading.Thread(target=self._do_autocomplete, daemon=True).start()
            except asyncio.CancelledError:
                pass

        async def delayed_smell():
            try:
                await asyncio.sleep(10.0)
                import threading
                threading.Thread(target=self._do_smell_detect, daemon=True).start()
            except asyncio.CancelledError:
                pass

        self._autocomplete_task = loop.create_task(delayed_autocomplete())
        self._smell_task = loop.create_task(delayed_smell())

    def _save(self):
        if not self.filepath:
            self._show_panel("No file path — use 'yaate <filename>' to open with a path.")
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

    def _run_in_thread_silent(self, fn):
        """Run AI call silently in background thread."""
        thread = threading.Thread(target=fn, daemon=True)
        thread.start()

    def _current_line_index(self) -> int:
        """Return 0-based index of cursor line."""
        doc = self.buffer.document
        return doc.cursor_position_row

    def _file_lines(self) -> list[str]:
        return self.buffer.text.splitlines()

    def _do_find(self):
        text = self.buffer.text
        # Search starting from right after the cursor
        idx = text.find(self.find_input_text, self.buffer.cursor_position + 1)
        if idx == -1:
            idx = text.find(self.find_input_text) # Wrap around
        if idx != -1:
            self.buffer.cursor_position = idx
        self.find_panel_visible = False
        self._refresh()

    def _do_jump(self):
        try:
            line = int(self.jump_input_text) - 1
            lines = self.buffer.text.splitlines()
            line = max(0, min(line, len(lines) - 1))
            # Calculate absolute character position
            pos = sum(len(l) + 1 for l in lines[:line])
            self.buffer.cursor_position = pos
        except ValueError:
            pass
        self.jump_panel_visible = False
        self._refresh()

    # ── AI Feature Implementations ─────────────────────────────────────────────

    def _do_autocomplete(self):
        try:
            doc = self.buffer.document
            row = doc.cursor_position_row
            line = doc.lines[row]

            # Optimize API usage: Only trigger if at the end of a non-empty line
            if not line.strip() or doc.cursor_position_col < len(line):
                self.ghost_text = ""
                self._refresh()
                return

            lines = self._file_lines()
            suggestion = ai.autocomplete(lines, row, self.mode)
            self.ghost_text = suggestion
            self._refresh()
        except Exception:
            pass

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
            self._refresh()
        except Exception:
            pass

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
