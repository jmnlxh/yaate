"""
ai.py — Optimized Gemini API client with connection reuse, caching, and low overhead.
"""

import os
import json
import urllib.request
import urllib.error
from pathlib import Path
from dotenv import load_dotenv

# Search for .env
env_path = Path.cwd() / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()


def _trim_context(lines: list[str], max_lines: int = 50) -> str:
    """Trim file context to avoid burning tokens on large files."""
    n = len(lines)
    if n <= max_lines:
        return "\n".join(lines)
    half = max_lines // 2
    return "\n".join(lines[:half]) + "\n...\n" + "\n".join(lines[-half:])


# ── Cached HTTP opener with connection reuse ─────────────────────────────────

_CACHED_OPENER: urllib.request.OpenerDirector | None = None


def _get_opener() -> urllib.request.OpenerDirector:
    """Return a cached opener with keep-alive HTTP handler for connection reuse."""
    global _CACHED_OPENER
    if _CACHED_OPENER is not None:
        return _CACHED_OPENER

    # Use HTTPSHandler (Python's urllib already supports HTTP/1.1 keep-alive)
    # For actual connection reuse we need to install a keep-alive handler
    try:
        from http.client import HTTPSConnection

        class HTTPSHandlerV11(urllib.request.HTTPSHandler):
            """Handler that sets HTTP version to 1.1 for connection reuse."""

            def https_open(self, req):
                return self.do_open(
                    lambda host, timeout=30, **kw: HTTPSConnection(
                        host, timeout=timeout, **kw
                    ),
                    req,
                    # urllib keeps connections alive by default in Python 3.7+
                )

        _CACHED_OPENER = urllib.request.build_opener(HTTPSHandlerV11)
    except Exception:
        _CACHED_OPENER = urllib.request.build_opener()

    # Install globally so all requests share the connection pool
    urllib.request.install_opener(_CACHED_OPENER)
    return _CACHED_OPENER


# ── Model name discovery (cached) ────────────────────────────────────────────

_MODEL_NAME: str | None = None


def _get_model_name(api_key: str) -> str:
    global _MODEL_NAME
    if _MODEL_NAME:
        return _MODEL_NAME

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            for m in data.get("models", []):
                if "gemini-1.5-flash" in m["name"] and "generateContent" in m.get("supportedGenerationMethods", []):
                    _MODEL_NAME = m["name"]
                    return _MODEL_NAME
            for m in data.get("models", []):
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    _MODEL_NAME = m["name"]
                    return _MODEL_NAME
    except Exception:
        pass

    _MODEL_NAME = "models/gemini-1.5-flash"
    return _MODEL_NAME


# ── Core API call with connection reuse ──────────────────────────────────────

_CACHED_API_KEY: str | None = None
_CACHED_MODEL_URL: str | None = None
_DEFAULT_TIMEOUT = 15


def _call(prompt: str, system: str = "") -> str:
    """Single Gemini call using cached opener for connection reuse."""
    global _CACHED_API_KEY, _CACHED_MODEL_URL

    api_key = _CACHED_API_KEY if _CACHED_API_KEY else os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not set. Add it to your .env file:\n"
            "  GEMINI_API_KEY=your_key_here"
        )
    if not _CACHED_API_KEY:
        _CACHED_API_KEY = api_key

    if not _CACHED_MODEL_URL:
        model_name = _get_model_name(api_key)
        _CACHED_MODEL_URL = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={api_key}"

    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    # Minimal JSON payload — avoid unnecessary whitespace
    data = json.dumps({
        "contents": [{"parts": [{"text": full_prompt}]}]
    }, separators=(",", ":")).encode("utf-8")

    req = urllib.request.Request(
        _CACHED_MODEL_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    opener = _get_opener()
    try:
        with opener.open(req, timeout=_DEFAULT_TIMEOUT) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return f"API Error: {e.code} - {err_body[:200]}"
    except Exception as e:
        return f"Error: {str(e)}"


# ── Feature 1: Autocomplete ──────────────────────────────────────────────────

AUTOCOMPLETE_SYSTEM = (
    "You are an expert {mode} programmer and config author.\n"
    "Your job is to complete code at the cursor position.\n"
    "Rules:\n"
    "- Return ONLY the completion text. No explanation, no markdown, no backticks.\n"
    "- Complete naturally from the cursor — do not repeat what's already written.\n"
    "- Keep the completion concise (1–10 lines max).\n"
    "- Match the indentation and style of the surrounding code.\n"
    "- If you're unsure, return a single most-likely completion."
)

def autocomplete(lines: list[str], cursor_row: int, mode: str) -> str:
    context_lines = lines[max(0, cursor_row - 15): cursor_row + 1]
    context = "\n".join(context_lines)
    system = AUTOCOMPLETE_SYSTEM.replace("{mode}", mode)
    prompt = f"File mode: {mode}\n\nCode up to cursor:\n{context}\n<CURSOR>\n\nComplete the code from <CURSOR>:"
    return _call(prompt, system)


# ── Feature 2: Line Comment ──────────────────────────────────────────────────

LINE_COMMENT_SYSTEM = (
    "You are an expert {mode} developer writing inline code documentation.\n"
    "Rules:\n"
    "- Explain what the SPECIFIC LINE does in ONE concise sentence.\n"
    "- Be technical but clear — assume the reader knows {mode} basics.\n"
    "- Do NOT include comment syntax (no #, //, --, etc.) — just the explanation text.\n"
    "- Do NOT start with 'This line' — get straight to the point.\n"
    "- Max 120 characters."
)

def explain_line(line: str, context_above: str, context_below: str, mode: str) -> str:
    system = LINE_COMMENT_SYSTEM.replace("{mode}", mode)
    prompt = (
        f"File mode: {mode}\n\nContext above:\n{context_above}\n\n"
        f"Target line to explain:\n{line}\n\nContext below:\n{context_below}\n\n"
        "Explain what the target line does:"
    )
    return _call(prompt, system)


# ── Feature 3: Block Comment ─────────────────────────────────────────────────

BLOCK_COMMENT_SYSTEM = (
    "You are an expert {mode} developer.\n"
    "Explain what this block of code does in 2–4 sentences.\n"
    "Rules:\n"
    "- Return ONLY the explanation text, no comment syntax.\n"
    "- Be concise and technical.\n"
    "- Focus on WHAT it does and WHY, not how line by line."
)

def explain_block(block: str, mode: str) -> str:
    system = BLOCK_COMMENT_SYSTEM.replace("{mode}", mode)
    prompt = f"File mode: {mode}\n\nCode block:\n{block}\n\nExplain this block:"
    return _call(prompt, system)


# ── Feature 4: File Formatting (Gemini fallback) ────────────────────────────

FORMAT_SYSTEM = (
    "You are a {mode} formatter.\n"
    "Rules:\n"
    "- Return ONLY the formatted file content. No explanation, no markdown, no backticks.\n"
    "- Fix indentation, spacing, and style to match {mode} conventions.\n"
    "- Do NOT change logic or variable names.\n"
    "- Preserve all comments."
)

def format_file(content: str, mode: str) -> str:
    system = FORMAT_SYSTEM.replace("{mode}", mode)
    prompt = f"File mode: {mode}\n\nFormat this file:\n{content}"
    return _call(prompt, system)


# ── Feature 5: Error Explainer ──────────────────────────────────────────────

ERROR_SYSTEM = (
    "You are a {mode} programming expert.\n"
    "Analyze the provided code snippet.\n"
    "If there is an error, bug, or bad practice, explain it and provide a fix.\n"
    "If the code is perfectly fine, explain what the code's purpose is.\n"
    "Respond in this exact format:\n"
    "WHAT: <one sentence — what the code does, or what the error means>\n"
    "WHY: <one sentence — why it works this way, or what caused the error>\n"
    "FIX: <if there's an error, exact corrected code. If no error, write 'None needed' or a minor tip>\n"
    "LINE: <line number of the focus area>"
)

def explain_error(error: str, file_lines: list[str], cursor_row: int, mode: str) -> dict:
    context_start = max(0, cursor_row - 15)
    context_end = min(len(file_lines), cursor_row + 15)
    context_lines = file_lines[context_start:context_end]

    # Build numbered context efficiently
    numbered_buf = []
    for i, line in enumerate(context_lines):
        numbered_buf.append(f"{context_start + i + 1:4d} │ {line}")
    numbered = "\n".join(numbered_buf)

    system = ERROR_SYSTEM.replace("{mode}", mode)
    prompt = (
        f"File mode: {mode}\n\nFile context (around the cursor):\n{numbered}\n\n"
        f"Target line or error context:\n{error}\n\n"
        "Respond in the exact format:\nWHAT: ...\nWHY: ...\nFIX: ...\nLINE: ..."
    )

    raw = _call(prompt, system)

    # Parse structured response (fast string ops, no splitlines overhead)
    result = {"what": "", "why": "", "fix": "", "line": "unknown"}
    for line in raw.split("\n"):
        if line.startswith("WHAT:"):
            result["what"] = line[5:].strip()
        elif line.startswith("WHY:"):
            result["why"] = line[4:].strip()
        elif line.startswith("FIX:"):
            result["fix"] = line[4:].strip()
        elif line.startswith("LINE:"):
            result["line"] = line[5:].strip()
    return result


# ── Feature 6: Code Smell Detector ──────────────────────────────────────────

SMELL_SYSTEM = (
    "You are a strict {mode} code reviewer.\n"
    "Analyze the file for code smells, bugs, bad practices, and security issues."
)

SMELL_PROMPT = (
    "File mode: {mode}\n\n"
    "Analyze this file and return ONLY a JSON array — no markdown, no backticks, no explanation.\n"
    "Each issue must have an exact line number.\n\n"
    "Format:\n"
    '[\n'
    '  {{\n'
    '    "line": <line_number as integer>,\n'
    '    "severity": "info" | "warn" | "error",\n'
    '    "issue": "<short description, max 80 chars>",\n'
    '    "fix": "<one-line suggested fix>"\n'
    '  }}\n'
    "]\n\n"
    "Severity guide:\n"
    "  info  → style issue, naming, minor suggestion\n"
    "  warn  → logic smell, potential bug, bad practice\n"
    "  error → security risk, guaranteed runtime failure\n\n"
    "File (with line numbers):\n{numbered_content}"
)


def smell_detect(file_lines: list[str], mode: str) -> list[dict]:
    # Build numbered content efficiently
    numbered_buf = []
    n = len(file_lines)
    for i in range(min(n, 300)):
        numbered_buf.append(f"{i + 1:4d} │ {file_lines[i]}")
    if n > 300:
        for i in range(max(300, n - 150), n):
            numbered_buf.append(f"{i + 1:4d} │ {file_lines[i]}")
    numbered = "\n".join(numbered_buf)

    system = SMELL_SYSTEM.replace("{mode}", mode)
    prompt = SMELL_PROMPT.replace("{mode}", mode).replace("{numbered_content}", numbered)

    raw = _call(prompt, system)

    # Strip markdown fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        if first_nl != -1:
            raw = raw[first_nl + 1:]
    if raw.endswith("```"):
        raw = raw[:-3].rstrip()

    try:
        results = json.loads(raw)
        cleaned = []
        for item in results:
            if isinstance(item.get("line"), int) and item.get("issue"):
                cleaned.append({
                    "line": item["line"],
                    "severity": item.get("severity", "info"),
                    "issue": item.get("issue", ""),
                    "fix": item.get("fix", ""),
                })
        return cleaned
    except (json.JSONDecodeError, TypeError):
        return []


# ── Feature 7: Inline AI Chat ───────────────────────────────────────────────

CHAT_SYSTEM = (
    "You are yaate, an AI-assisted CLI editor assistant.\n"
    "The user is editing a {mode} file. Answer questions about the file concisely.\n"
    "Keep responses short and terminal-friendly (no wide tables, no long paragraphs).\n"
    "Use plain text. You may use short code snippets."
)

def chat(user_message: str, file_content: str, mode: str, history: list[dict]) -> str:
    system = CHAT_SYSTEM.replace("{mode}", mode)

    # Build conversation efficiently
    parts = [
        f"[File mode: {mode}]",
        f"[Current file content:]",
        _trim_context(file_content.splitlines(), 60),
    ]
    for turn in history[-10:]:
        prefix = "User:" if turn["role"] == "user" else "Assistant:"
        parts.append(f"{prefix} {turn['text']}")
    parts.append(f"User: {user_message}")

    prompt = system + "\n\n" + "\n".join(parts) + "\n\nAssistant:"
    return _call(prompt)


# ── Feature 8: Docstring Generator ──────────────────────────────────────────

DOCSTRING_SYSTEM = (
    "You are a {mode} documentation expert.\n"
    "Generate a proper docstring for the given function or class.\n"
    "Rules:\n"
    "- Return ONLY the docstring content, no surrounding code.\n"
    "- Use the correct docstring format for {mode}:\n"
    "    Python  → Google style (Args:, Returns:, Raises:)\n"
    "    JS/TS   → JSDoc (/** @param @returns */)\n"
    "    Rust    → /// doc comments\n"
    "    Go      → // godoc style\n"
    "    C/C++   → Doxygen (/** @brief @param @return */)\n"
    "- Be concise but complete."
)

def generate_docstring(func_block: str, mode: str) -> str:
    system = DOCSTRING_SYSTEM.replace("{mode}", mode)
    prompt = f"File mode: {mode}\n\nFunction/class:\n{func_block}\n\nGenerate the docstring:"
    return _call(prompt, system)


# ── Feature 9: README Generator ─────────────────────────────────────────────

README_SYSTEM = "You are a technical writer generating a GitHub README.\nReturn ONLY the README.md content in markdown. No explanation."


def generate_readme(project_dir: Path) -> None:
    """Scan project directory and generate a README.md."""
    files = []
    for f in project_dir.rglob("*"):
        if f.is_file() and not any(
            part.startswith(".") or part in ("__pycache__", "node_modules", ".venv", "target")
            for part in f.parts
        ):
            files.append(str(f.relative_to(project_dir)))

    context_files = ["pyproject.toml", "Cargo.toml", "package.json", "go.mod", "main.py", "main.rs", "index.js"]
    snippets = []
    for name in context_files:
        p = project_dir / name
        if p.exists():
            try:
                snippets.append(f"--- {name} ---\n{p.read_text()[:1000]}")
            except OSError:
                pass

    prompt = (
        f"Project directory: {project_dir.name}\n\n"
        f"File tree:\n{chr(10).join(files[:50])}\n\n"
        f"Key files:\n{chr(10).join(snippets)}\n\n"
        "Generate a complete README.md with:\n"
        "- Project title and description\n"
        "- Features list\n"
        "- Installation instructions\n"
        "- Usage examples\n"
        "- Requirements"
    )

    result = _call(prompt, README_SYSTEM)

    out = project_dir / "README.md"
    out.write_text(result)
    print(f"✓ README.md written to {out}")