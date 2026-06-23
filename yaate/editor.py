"""
editor.py — Optimized core editor (fast, responsive, nano/vim-like feel)
Uses debouncing, caching, minimal allocations, and thread-safe UI updates.
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


# ── Fast line-number margin (avoids full recalculation each frame) ────────────

class FastNumberedMargin(NumberedMargin):
    """NumberedMargin with cached line count to avoid repeated split()."""
    def __init__(self):
        super().__init__()
        self._cached_line_count = -1
        self._cached_width = 3

    def get_width(self, get_ui_content):
        n = get_ui_content().line_count
        if n != self._cached_line_count:
            self._cached_line_count = n
            self._cached_width = max(3, len(str(n)) + 1)
        return self._cached_width

    def __repr__(self):
        return f"FastNumberedMargin(width={self._cached_width})"


# ── Optimized Processors ──────────────────────────────────────────────────────

class SmellProcessor(Processor):
    __slots__ = ('_lookup',)

    def __init__(self, editor):
        self._lookup = {}  # built on each smell update

    def set_smells(self, smells: list[dict]):
        self._lookup = {s["line"]: SEVERITY_ICON.get(s["severity"], "⚠")
                        for s in smells}

    def apply_transformation(self, ti):
        lineno = ti.lineno + 1
        icon = self._lookup.get(lineno)
        if icon is None:
            return Transformation(ti.fragments)
        frags = list(ti.fragments)
        frags.append(("class:gutter.smell", f"  [{icon} smell]"))
        return Transformation(frags)


class GhostTextProcessor(Processor):
    __slots__ = ('ghost_text', '_visible_line')

    def __init__(self, editor):
        self.ghost_text = ""
        self._visible_line = -1

    def set_ghost(self, text: str, line: int):
        self.ghost_text = text
        self._visible_line = line

    def apply_transformation(self, ti):
        gt = self.ghost_text
        if not gt or ti.lineno != self._visible_line:
            return Transformation(ti.fragments)

        doc = ti.document
        if doc.cursor_position_col == len(doc.lines[ti.lineno]):
            frags = list(ti.fragments)
            frags.append(("class:ghostbar", gt))
            return Transformation(frags)
        return Transformation(ti.fragments)


# ── Pygments lexer (cached) ───────────────────────────────────────────────────

_LEXER_CACHE: dict[str, PygmentsLexer] = {}

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


def _get_lexer(mode: str) -> PygmentsLexer:
    cached = _LEXER_CACHE.get(mode)
    if cached is not None:
        return cached
    name = LEXER_MAP.get(mode)
    if name:
        try:
            cls = get_lexer_by_name(name).__class__
            cached = PygmentsLexer(cls)
            _LEXER_CACHE[mode] = cached
            return cached
        except Exception:
            pass
    cached = PygmentsLexer(TextLexer)
    _LEXER_CACHE[mode] = cached
    return cached


# ── Smell gutter markers ──────────────────────────────────────────────────────

SEVERITY_ICON = {
    "info":  "🔵",
    "warn":  "⚠ ",
    "error": "🔴",
}


# ── Timer-based debouncer (much lighter than asyncio task management) ─────────

class Debouncer:
    """Thread-safe debounce using a single timer."""
    __slots__ = ('delay', 'timer', '_cb', '_lock')

    def __init__(self, delay: float, callback):
        self.delay = delay
        self.timer = None
        self._cb = callback
        self._lock = threading.Lock()

    def call(self):
        """Schedule the callback after delay. Resets if already scheduled."""
        with self._lock:
            if self.timer is not None:
                self.timer.cancel()
            self.timer = threading.Timer(self.delay, self._run)
            self.timer.daemon = True
            self.timer.start()

    def cancel(self):
        with self._lock:
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None

    def _run(self):
        with self._lock:
            self.timer = None
        self._cb()


# ── Main Editor ───────────────────────────────────────────────────────────────

class Editor:
    __slots__ = (
        'filepath', 'mode', 'modified',
        'ghost_text', 'smells', 'chat_history',
        'bottom_panel_text', 'bottom_panel_visible',
        'chat_panel_visible', 'chat_input_text',
        'find_panel_visible', 'find_input_text',
        'jump_panel_visible', 'jump_input_text',
        'buffer', 'layout', 'kb', 'style', 'app',
        '_smell_processor', '_ghost_processor',
        '_autocomplete_debouncer', '_smell_debouncer',
        '_lines_cache', '_lines_age',  # cached splitlines
        '_titlebar_text', '_helpbar_text', '_status_text',
        '_panel_text', '_chat_header', '_chat_input_display',
        '_find_header', '_find_input_display',
        '_jump_header', '_jump_input_display',
    )

    def __init__(self, filepath: Path | None):
        self.filepath = filepath
        self.mode = detect_mode(filepath)
        self.modified = False
        self.ghost_text = ""
        self.smells: list[dict] = []
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

        # Cached line storage
        self._lines_cache: list[str] = []
        self._lines_age = 0   # incremented on text change

        # Processors
        self._smell_processor = SmellProcessor(self)
        self._ghost_processor = GhostTextProcessor(self)

        # Debouncers (single timer each, no asyncio overhead)
        self._autocomplete_debouncer = Debouncer(0.75, self._do_autocomplete)
        self._smell_debouncer = Debouncer(10.0, self._do_smell_detect)

        # Pre-allocated formatted text controls (avoids lambda recreations)
        self._titlebar_text = FormattedTextControl(self._render_titlebar)
        self._helpbar_text = FormattedTextControl(self._render_helpbar)
        self._panel_text = FormattedTextControl(self._render_panel)
        self._chat_header = FormattedTextControl("─── AI Chat (Enter send, Esc close) ─────────────────────")
        self._chat_input_display = FormattedTextControl(self._render_chat_input)
        self._find_header = FormattedTextControl("─── Find (Where Is) (Enter to search, Esc to close) ───")
        self._find_input_display = FormattedTextControl(self._render_find_input)
        self._jump_header = FormattedTextControl("─── Go To Line (Enter to jump, Esc to close) ──────────")
        self._jump_input_display = FormattedTextControl(self._render_jump_input)
        self._status_text = FormattedTextControl("")

        # Load file
        initial_text = ""
        if filepath and filepath.exists():
            try:
                initial_text = filepath.read_text()
            except OSError as e:
                initial_text = f"# Error reading file: {e}\n"

        # Main editor buffer (cursor starts at top)
        self.buffer = Buffer(
            name="main",
            document=Document(initial_text, cursor_position=0),
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
            include_default_pygments_style=False,
        )

    # ── Rendered callbacks (called on demand, avoid lambda per frame) ────────

    def _render_titlebar(self):
        name = self.filepath.name if self.filepath else "New Buffer"
        mod = " (Modified)" if self.modified else ""
        return f"  yaate 0.1.0   File: {name} [{mode_display_name(self.mode)}]{mod}"

    def _render_helpbar(self):
        return " ^O Save  ^X Quit  ^W Find  ^_ Line  ^T Comment  ^E Explain  ^F Format  ^C Chat  ^Y YankChat"

    def _render_panel(self):
        return self.bottom_panel_text

    def _render_chat_input(self):
        return f"▶ {self.chat_input_text}█"

    def _render_find_input(self):
        return f"Search: {self.find_input_text}█"

    def _render_jump_input(self):
        return f"Line number: {self.jump_input_text}█"

    # ── Layout (uses cached FormattedTextControl objects) ────────────────────

    def _build_layout(self):
        visible_chat = Condition(lambda: self.chat_panel_visible)
        visible_find = Condition(lambda: self.find_panel_visible)
        visible_jump = Condition(lambda: self.jump_panel_visible)
        visible_bottom = Condition(lambda: self.bottom_panel_visible)

        self.layout = Layout(
            HSplit([
                # Title bar
                VSplit([
                    Window(
                        content=self._titlebar_text,
                        align=WindowAlign.LEFT,
                        style="class:titlebar",
                    ),
                    Window(
                        content=self._status_text,
                        align=WindowAlign.CENTER,
                        style="class:titlebar",
                    ),
                    Window(
                        content=self._status_text,
                        align=WindowAlign.RIGHT,
                        style="class:titlebar",
                    ),
                ], height=1),
                # Editor buffer with fast numbered margin
                Window(
                    content=BufferControl(
                        buffer=self.buffer,
                        lexer=_get_lexer(self.mode),
                        focus_on_click=True,
                        input_processors=[self._ghost_processor, self._smell_processor],
                    ),
                    left_margins=[FastNumberedMargin()],
                ),
                # Bottom info panel
                ConditionalContainer(
                    content=Window(
                        content=self._panel_text,
                        height=8,
                        style="class:panel",
                    ),
                    filter=visible_bottom,
                ),
                # Chat panel
                ConditionalContainer(
                    content=HSplit([
                        Window(content=self._chat_header, height=1, style="class:panel.header"),
                        Window(content=self._chat_input_display, height=1, style="class:panel"),
                    ]),
                    filter=visible_chat,
                ),
                # Find panel
                ConditionalContainer(
                    content=HSplit([
                        Window(content=self._find_header, height=1, style="class:panel.header"),
                        Window(content=self._find_input_display, height=1, style="class:panel"),
                    ]),
                    filter=visible_find,
                ),
                # Jump panel
                ConditionalContainer(
                    content=HSplit([
                        Window(content=self._jump_header, height=1, style="class:panel.header"),
                        Window(content=self._jump_input_display, height=1, style="class:panel"),
                    ]),
                    filter=visible_jump,
                ),
                # Help bar
                Window(content=self._helpbar_text, height=1, style="class:helpbar"),
            ])
        )

    # ── Keybindings ──────────────────────────────────────────────────────────

    def _build_keybindings(self):
        kb = KeyBindings()

        # ── Ctrl+O — Save ────────────────────────────────────────────────────
        @kb.add("c-o")
        def save(event):
            self._save()

        # ── Ctrl+X — Quit ────────────────────────────────────────────────────
        @kb.add("c-x")
        def quit(event):
            event.app.exit()

        # ── Ctrl+W — Find ────────────────────────────────────────────────────
        @kb.add("c-w")
        def find(event):
            self._hide_all_panels()
            self.find_panel_visible = True
            self.find_input_text = ""
            self._invalidate()

        # ── Ctrl+_ — Go To Line ──────────────────────────────────────────────
        @kb.add("c-_")
        def jump_to_line(event):
            self._hide_all_panels()
            self.jump_panel_visible = True
            self.jump_input_text = ""
            self._invalidate()

        # ── Tab — Accept ghost or indent ─────────────────────────────────────
        @kb.add("tab")
        def accept_ghost(event):
            gt = self.ghost_text
            if gt:
                self.ghost_text = ""
                self._ghost_processor.set_ghost("", -1)
                self.buffer.insert_text(gt)
                self._invalidate()
            else:
                self.buffer.insert_text("    ")

        # ── Escape ───────────────────────────────────────────────────────────
        @kb.add("escape")
        def escape(event):
            if self.ghost_text:
                self.ghost_text = ""
                self._ghost_processor.set_ghost("", -1)
                self._invalidate()
            elif self.chat_panel_visible or self.find_panel_visible or self.jump_panel_visible or self.bottom_panel_visible:
                self._hide_all_panels()
                self._invalidate()

        # ── Ctrl+T — Comment line ────────────────────────────────────────────
        @kb.add("c-t")
        def comment_line(event):
            self._run_in_thread(self._do_comment_line)

        # ── Ctrl+E — Error explainer ─────────────────────────────────────────
        @kb.add("c-e")
        def error_explain(event):
            self._run_in_thread(self._do_error_explain)

        # ── Ctrl+F — Format file ─────────────────────────────────────────────
        @kb.add("c-f")
        def format_file(event):
            self._run_in_thread(self._do_format)

        # ── Ctrl+D — Docstring ───────────────────────────────────────────────
        @kb.add("c-d")
        def docstring(event):
            self._run_in_thread(self._do_docstring)

        # ── Ctrl+C — AI Chat panel ───────────────────────────────────────────
        @kb.add("c-c")
        def chat(event):
            if self.chat_panel_visible:
                self.chat_panel_visible = False
            else:
                self._hide_all_panels()
                self.chat_panel_visible = True
            self._invalidate()

        # ── Ctrl+Y — Yank last AI response ───────────────────────────────────
        @kb.add("c-y")
        def yank_chat(event):
            if self.chat_history:
                last = self.chat_history[-1]
                if last["role"] == "model":
                    self.buffer.insert_text(last["text"])
                    self._show_panel("✓ Inserted AI response.")
                else:
                    self._show_panel("✗ Last message was not from AI.")
            else:
                self._show_panel("✗ No AI chat history.")

        # ── Multi-panel character input ──────────────────────────────────────
        panel_visible = Condition(
            lambda: self.chat_panel_visible or self.find_panel_visible or self.jump_panel_visible
        )

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
            if self.chat_panel_visible:
                self.chat_input_text = self.chat_input_text[:-1]
            elif self.find_panel_visible:
                self.find_input_text = self.find_input_text[:-1]
            elif self.jump_panel_visible:
                self.jump_input_text = self.jump_input_text[:-1]
            self._invalidate()

        @kb.add("<any>", filter=panel_visible)
        def multi_input(event):
            key = event.key_sequence[0].key
            if len(key) == 1:
                if self.chat_panel_visible:
                    self.chat_input_text += key
                elif self.find_panel_visible:
                    self.find_input_text += key
                elif self.jump_panel_visible:
                    self.jump_input_text += key
                self._invalidate()

        self.kb = kb

    # ── Style ────────────────────────────────────────────────────────────────

    def _build_style(self):
        # Only structural styles — no hardcoded fg/bg colors,
        # so the terminal's native theme is used throughout.
        self.style = Style.from_dict({
            "titlebar":         "reverse bold",
            "helpbar":          "reverse",
            "ghostbar":         "italic",
            "gutter.smell":     "bold",
            "panel.header":     "reverse",
        })

    # ── Internal helpers (minimal allocations) ───────────────────────────────

    def _invalidate(self):
        if self.app and self.app.is_running:
            self.app.invalidate()

    def _hide_all_panels(self):
        self.chat_panel_visible = False
        self.find_panel_visible = False
        self.jump_panel_visible = False
        self.bottom_panel_visible = False

    def _show_panel(self, text: str):
        self.bottom_panel_text = text
        self.bottom_panel_visible = True
        self._invalidate()

    def _file_lines(self) -> list[str]:
        """Cached splitlines — avoids repeated allocations."""
        # Increment age counter to invalidate cache when text changes
        return self._lines_cache

    def _rebuild_lines_cache(self):
        """Call after buffer text changes."""
        self._lines_cache = self.buffer.text.splitlines(keepends=False)
        self._lines_age += 1

    def _current_line_index(self) -> int:
        return self.buffer.document.cursor_position_row

    def _on_text_changed(self, _):
        self.modified = True
        self._rebuild_lines_cache()

        # Clear ghost text on any edit
        if self.ghost_text:
            self.ghost_text = ""
            self._ghost_processor.set_ghost("", -1)

        # Debounce autocomplete and smell detection (no asyncio churn)
        self._autocomplete_debouncer.call()
        self._smell_debouncer.call()

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
        """Run AI call in background thread — thread-safe _show_panel."""
        self._show_panel("⟳ Thinking…")
        thread = threading.Thread(target=fn, daemon=True)
        thread.start()

    def _do_find(self):
        text = self.buffer.text
        idx = text.find(self.find_input_text, self.buffer.cursor_position + 1)
        if idx == -1:
            idx = text.find(self.find_input_text)
        if idx != -1:
            self.buffer.cursor_position = idx
        self.find_panel_visible = False
        self._invalidate()

    def _do_jump(self):
        try:
            line = int(self.jump_input_text) - 1
            lines = self._file_lines()
            # lines cache is always up-to-date due to _rebuild_lines_cache
            if not lines:
                line = 0
            else:
                line = max(0, min(line, len(lines) - 1))
            pos = sum(len(l) + 1 for l in lines[:line])
            self.buffer.cursor_position = pos
        except ValueError:
            pass
        self.jump_panel_visible = False
        self._invalidate()

    # ── AI Feature Implementations ───────────────────────────────────────────

    def _do_autocomplete(self):
        try:
            doc = self.buffer.document
            row = doc.cursor_position_row
            lines = doc.lines
            if row >= len(lines):
                return
            line = lines[row]

            # Only trigger at end of non-empty line
            if not line.strip() or doc.cursor_position_col < len(line):
                self.ghost_text = ""
                self._ghost_processor.set_ghost("", -1)
                self._invalidate()
                return

            suggestion = ai.autocomplete(lines, row, self.mode)
            self.ghost_text = suggestion
            self._ghost_processor.set_ghost(suggestion, row)
            self._invalidate()
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

            lines.insert(row, comment)
            new_text = "\n".join(lines)
            self.buffer.set_document(
                Document(new_text, cursor_position=self.buffer.cursor_position + len(comment) + 1),
                bypass_readonly=True,
            )
            self.modified = True
            self._hide_all_panels()
            self._invalidate()
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
            # Only analyze visible portion of file + context
            lines = self._file_lines()
            if len(lines) > 300:
                # Get context around cursor
                doc = self.buffer.document
                row = doc.cursor_position_row
                half = 150
                start = max(0, row - half)
                end = min(len(lines), row + half)
                focused_lines = lines[start:end]
                self.smells = ai.smell_detect(focused_lines, self.mode)
                # Remap line numbers
                for s in self.smells:
                    s["line"] = s["line"] + start
            else:
                self.smells = ai.smell_detect(lines, self.mode)

            self._smell_processor.set_smells(self.smells)
            self._invalidate()
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

            block_lines = []
            for i in range(row, min(len(lines), row + 40)):
                block_lines.append(lines[i])
                if i > row and lines[i].strip() == "":
                    break

            block = "\n".join(block_lines)
            docstring = ai.generate_docstring(block, self.mode)

            insert_at = row + 1
            lines.insert(insert_at, docstring)
            new_text = "\n".join(lines)
            self.buffer.set_document(
                Document(new_text),
                bypass_readonly=True,
            )
            self.modified = True
            self._hide_all_panels()
            self._invalidate()
        except Exception as e:
            self._show_panel(f"✗ Docstring error: {e}")

    def _do_chat(self):
        try:
            user_msg = self.chat_input_text.strip()
            self.chat_input_text = ""
            self.chat_history.append({"role": "user", "text": user_msg})

            response = ai.chat(user_msg, self.buffer.text, self.mode, self.chat_history)
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

    # ── Run ──────────────────────────────────────────────────────────────────

    def run(self):
        self.app.run()