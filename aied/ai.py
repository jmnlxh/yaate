"""
ai.py — Gemini API integration
All prompts and AI feature calls live here.
Model: gemini-1.5-flash (free tier, 1M token context)
"""

import os
import json
import google.generativeai as genai
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Setup ──────────────────────────────────────────────────────────────────────

def _get_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not set. Add it to your .env file:\n"
            "  GEMINI_API_KEY=your_key_here"
        )
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-1.5-flash")


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _trim_context(lines: list[str], max_lines: int = 50) -> str:
    """Trim file context to avoid burning tokens on large files."""
    if len(lines) <= max_lines:
        return "\n".join(lines)
    half = max_lines // 2
    return "\n".join(lines[:half]) + "\n...\n" + "\n".join(lines[-half:])


def _call(prompt: str, system: str = "") -> str:
    """Single Gemini call, returns response text."""
    model = _get_client()
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    response = model.generate_content(full_prompt)
    return response.text.strip()


# ── Feature 1: Autocomplete ────────────────────────────────────────────────────

AUTOCOMPLETE_SYSTEM = """You are an expert {mode} programmer and config author.
Your job is to complete code at the cursor position.
Rules:
- Return ONLY the completion text. No explanation, no markdown, no backticks.
- Complete naturally from the cursor — do not repeat what's already written.
- Keep the completion concise (1–10 lines max).
- Match the indentation and style of the surrounding code.
- If you're unsure, return a single most-likely completion."""

def autocomplete(lines: list[str], cursor_row: int, mode: str) -> str:
    """
    Return AI completion text starting from the cursor position.
    Context: lines above cursor (max 40 lines).
    """
    context_lines = lines[max(0, cursor_row - 40): cursor_row + 1]
    context = "\n".join(context_lines)

    system = AUTOCOMPLETE_SYSTEM.format(mode=mode)
    prompt = f"""File mode: {mode}

Code up to cursor:
{context}
<CURSOR>

Complete the code from <CURSOR>:"""

    return _call(prompt, system)


# ── Feature 2: Line Comment ────────────────────────────────────────────────────

LINE_COMMENT_SYSTEM = """You are an expert {mode} developer writing inline code documentation.
Rules:
- Explain what the SPECIFIC LINE does in ONE concise sentence.
- Be technical but clear — assume the reader knows {mode} basics.
- Do NOT include comment syntax (no #, //, --, etc.) — just the explanation text.
- Do NOT start with 'This line' — get straight to the point.
- Max 120 characters."""

def explain_line(line: str, context_above: str, context_below: str, mode: str) -> str:
    """
    Return a comment explanation for a single line.
    Returns raw text — caller adds comment syntax.
    """
    system = LINE_COMMENT_SYSTEM.format(mode=mode)
    prompt = f"""File mode: {mode}

Context above:
{context_above}

Target line to explain:
{line}

Context below:
{context_below}

Explain what the target line does:"""

    return _call(prompt, system)


# ── Feature 3: Block Comment ───────────────────────────────────────────────────

BLOCK_COMMENT_SYSTEM = """You are an expert {mode} developer.
Explain what this block of code does in 2–4 sentences.
Rules:
- Return ONLY the explanation text, no comment syntax.
- Be concise and technical.
- Focus on WHAT it does and WHY, not how line by line."""

def explain_block(block: str, mode: str) -> str:
    """Return a multi-line explanation for a selected block."""
    system = BLOCK_COMMENT_SYSTEM.format(mode=mode)
    prompt = f"""File mode: {mode}

Code block:
{block}

Explain this block:"""

    return _call(prompt, system)


# ── Feature 4: File Formatting (Gemini fallback) ───────────────────────────────

FORMAT_SYSTEM = """You are a {mode} formatter.
Rules:
- Return ONLY the formatted file content. No explanation, no markdown, no backticks.
- Fix indentation, spacing, and style to match {mode} conventions.
- Do NOT change logic or variable names.
- Preserve all comments."""

def format_file(content: str, mode: str) -> str:
    """
    Format file content using Gemini.
    Used as fallback when no local formatter is available.
    """
    system = FORMAT_SYSTEM.format(mode=mode)
    prompt = f"""File mode: {mode}

Format this file:
{content}"""

    return _call(prompt, system)


# ── Feature 5: Error Explainer ─────────────────────────────────────────────────

ERROR_SYSTEM = """You are a {mode} debugging expert.
Analyze the error in context and return a structured explanation."""

ERROR_PROMPT = """File mode: {mode}

File context (around the error):
{context}

Error or problem:
{error}

Respond in this exact format:
WHAT: <one sentence — what the error means in plain English>
WHY: <one sentence — what caused it in this specific code>
FIX: <exact corrected code or action, as concise as possible>
LINE: <line number where the fix should be applied, or 'unknown'>"""

def explain_error(
    error: str,
    file_lines: list[str],
    cursor_row: int,
    mode: str
) -> dict:
    """
    Explain an error and suggest a fix.
    Returns dict: { what, why, fix, line }
    """
    context_start = max(0, cursor_row - 15)
    context_end = min(len(file_lines), cursor_row + 15)
    context_lines = file_lines[context_start:context_end]

    # Add line numbers to context
    numbered = "\n".join(
        f"{context_start + i + 1:4d} │ {line}"
        for i, line in enumerate(context_lines)
    )

    system = ERROR_SYSTEM.format(mode=mode)
    prompt = ERROR_PROMPT.format(
        mode=mode,
        context=numbered,
        error=error
    )

    raw = _call(prompt, system)

    # Parse structured response
    result = {"what": "", "why": "", "fix": "", "line": "unknown"}
    for line in raw.splitlines():
        if line.startswith("WHAT:"):
            result["what"] = line[5:].strip()
        elif line.startswith("WHY:"):
            result["why"] = line[4:].strip()
        elif line.startswith("FIX:"):
            result["fix"] = line[4:].strip()
        elif line.startswith("LINE:"):
            result["line"] = line[5:].strip()

    return result


# ── Feature 6: Code Smell Detector ────────────────────────────────────────────

SMELL_SYSTEM = """You are a strict {mode} code reviewer.
Analyze the file for code smells, bugs, bad practices, and security issues."""

SMELL_PROMPT = """File mode: {mode}

Analyze this file and return ONLY a JSON array — no markdown, no backticks, no explanation.
Each issue must have an exact line number.

Format:
[
  {{
    "line": <line_number as integer>,
    "severity": "info" | "warn" | "error",
    "issue": "<short description, max 80 chars>",
    "fix": "<one-line suggested fix>"
  }}
]

Severity guide:
  info  → style issue, naming, minor suggestion
  warn  → logic smell, potential bug, bad practice
  error → security risk, guaranteed runtime failure

File (with line numbers):
{numbered_content}"""

def smell_detect(file_lines: list[str], mode: str) -> list[dict]:
    """
    Detect code smells in the file.
    Returns list of { line, severity, issue, fix }
    """
    numbered = "\n".join(
        f"{i + 1:4d} │ {line}"
        for i, line in enumerate(file_lines)
    )

    # Trim very large files
    if len(file_lines) > 300:
        numbered = _trim_context(
            [f"{i+1:4d} │ {l}" for i, l in enumerate(file_lines)],
            max_lines=300
        )

    system = SMELL_SYSTEM.format(mode=mode)
    prompt = SMELL_PROMPT.format(mode=mode, numbered_content=numbered)

    raw = _call(prompt, system)

    # Strip markdown fences if model added them
    raw = raw.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.splitlines()[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[:-1])

    try:
        results = json.loads(raw)
        # Validate and sanitize
        cleaned = []
        for item in results:
            if isinstance(item.get("line"), int) and item.get("issue"):
                cleaned.append({
                    "line":     item["line"],
                    "severity": item.get("severity", "info"),
                    "issue":    item.get("issue", ""),
                    "fix":      item.get("fix", ""),
                })
        return cleaned
    except (json.JSONDecodeError, TypeError):
        return []


# ── Feature 7: Inline AI Chat ─────────────────────────────────────────────────

CHAT_SYSTEM = """You are aied, an AI-assisted CLI editor assistant.
The user is editing a {mode} file. Answer questions about the file concisely.
Keep responses short and terminal-friendly (no wide tables, no long paragraphs).
Use plain text. You may use short code snippets."""

def chat(
    user_message: str,
    file_content: str,
    mode: str,
    history: list[dict]
) -> str:
    """
    Inline AI chat with file context.
    history: list of { role: 'user'|'model', text: str }
    """
    model = _get_client()

    system = CHAT_SYSTEM.format(mode=mode)

    # Build conversation
    convo_parts = [
        f"[File mode: {mode}]\n[Current file content:]\n{_trim_context(file_content.splitlines(), 60)}\n"
    ]
    for turn in history[-10:]:  # last 10 turns to stay token-efficient
        prefix = "User:" if turn["role"] == "user" else "Assistant:"
        convo_parts.append(f"{prefix} {turn['text']}")
    convo_parts.append(f"User: {user_message}")

    prompt = system + "\n\n" + "\n".join(convo_parts) + "\n\nAssistant:"

    return _call(prompt)


# ── Feature 8: Docstring Generator ────────────────────────────────────────────

DOCSTRING_SYSTEM = """You are a {mode} documentation expert.
Generate a proper docstring for the given function or class.
Rules:
- Return ONLY the docstring content, no surrounding code.
- Use the correct docstring format for {mode}:
    Python  → Google style (Args:, Returns:, Raises:)
    JS/TS   → JSDoc (/** @param @returns */)
    Rust    → /// doc comments
    Go      → // godoc style
    C/C++   → Doxygen (/** @brief @param @return */)
- Be concise but complete."""

def generate_docstring(func_block: str, mode: str) -> str:
    """Generate a docstring for a function/class block."""
    system = DOCSTRING_SYSTEM.format(mode=mode)
    prompt = f"""File mode: {mode}

Function/class:
{func_block}

Generate the docstring:"""

    return _call(prompt, system)


# ── Feature 9: README Generator ───────────────────────────────────────────────

README_SYSTEM = """You are a technical writer generating a GitHub README.
Return ONLY the README.md content in markdown. No explanation."""

def generate_readme(project_dir: Path) -> None:
    """Scan project directory and generate a README.md."""
    # Collect file tree
    files = []
    for f in project_dir.rglob("*"):
        if f.is_file() and not any(
            part.startswith(".") or part in ("__pycache__", "node_modules", ".venv", "target")
            for part in f.parts
        ):
            files.append(str(f.relative_to(project_dir)))

    # Read key files for context
    context_files = ["pyproject.toml", "Cargo.toml", "package.json", "go.mod", "main.py", "main.rs", "index.js"]
    snippets = []
    for name in context_files:
        p = project_dir / name
        if p.exists():
            try:
                snippets.append(f"--- {name} ---\n{p.read_text()[:1000]}")
            except OSError:
                pass

    prompt = f"""Project directory: {project_dir.name}

File tree:
{chr(10).join(files[:50])}

Key files:
{chr(10).join(snippets)}

Generate a complete README.md with:
- Project title and description
- Features list
- Installation instructions
- Usage examples
- Requirements"""

    result = _call(prompt, README_SYSTEM)

    out = project_dir / "README.md"
    out.write_text(result)
    print(f"✓ README.md written to {out}")
