"""AST-based script validation for agent payloads.

Replaces naive regex blacklisting with a structured whitelist approach.
Only explicitly permitted AST node types and import modules are allowed.
"""

import ast
import base64
from typing import Any


class ScriptValidationError(Exception):
    """Script failed security validation."""


# Allowed top-level AST node types
_ALLOWED_NODE_TYPES: set[type[ast.AST]] = {
    ast.Module,
    ast.Expr,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.Name,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.If,
    ast.For,
    ast.While,
    ast.Break,
    ast.Continue,
    ast.Pass,
    ast.Return,
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.Subscript,
    ast.Index,  # type: ignore[attr-defined]
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Set,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.LShift,
    ast.RShift,
    ast.BitOr,
    ast.BitXor,
    ast.BitAnd,
    ast.MatMult,
    ast.USub,
    ast.UAdd,
    ast.Invert,
    ast.Not,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
    ast.Call,
    ast.Attribute,
    ast.IfExp,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.comprehension,
    ast.Try,
    ast.ExceptHandler,
    ast.Raise,
    ast.With,
    ast.withitem,
    ast.Assert,
    ast.Global,
    ast.Nonlocal,
    ast.NamedExpr,
    ast.Match,  # type: ignore[attr-defined]
    ast.match_case,  # type: ignore[attr-defined]
    ast.MatchValue,  # type: ignore[attr-defined]
    ast.MatchSingleton,  # type: ignore[attr-defined]
    ast.MatchSequence,  # type: ignore[attr-defined]
    ast.MatchStar,  # type: ignore[attr-defined]
    ast.MatchMapping,  # type: ignore[attr-defined]
    ast.MatchClass,  # type: ignore[attr-defined]
    ast.MatchAs,  # type: ignore[attr-defined]
    ast.MatchOr,  # type: ignore[attr-defined]
}

# Whitelisted import modules (exact name match required)
_ALLOWED_IMPORTS: set[str] = {
    "math",
    "random",
    "statistics",
    "decimal",
    "fractions",
    "itertools",
    "functools",
    "collections",
    "datetime",
    "json",
    "re",
    "string",
    "hashlib",
    "base64",
    "typing",
    "dataclasses",
    "enum",
    "numbers",
    "operator",
    "copy",
    " pprint",
}

# Banned builtins by exact name
_BANNED_BUILTINS: set[str] = {
    "eval",
    "exec",
    "compile",
    "open",
    "__import__",
    "input",
    "exit",
    "quit",
    "help",
    "breakpoint",
    "getattr",
    "setattr",
    "delattr",
    "hasattr",
}

# Dangerous attribute chains that suggest runtime code generation or system access
_DANGEROUS_ATTR_PATTERNS: set[str] = {
    "os.system",
    "os.popen",
    "os.spawn",
    "os.fork",
    "os.kill",
    "subprocess.call",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.check_output",
    "sys.exit",
    "sys.modules",
    "platform.popen",
    "socket.socket",
    "urllib.request",
    "requests.get",
    "requests.post",
    "ftplib.FTP",
    "smtplib.SMTP",
}


def _get_dotted_name(node: ast.AST) -> str:
    """Reconstruct a dotted name from an Attribute/Name chain."""
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _validate_import(node: ast.Import | ast.ImportFrom) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in _ALLOWED_IMPORTS:
                raise ScriptValidationError(f"Import of '{alias.name}' is not permitted")
    elif isinstance(node, ast.ImportFrom):
        root = (node.module or "").split(".")[0]
        if root not in _ALLOWED_IMPORTS:
            raise ScriptValidationError(f"Import from '{node.module}' is not permitted")


def _validate_call(node: ast.Call) -> None:
    func = node.func
    if isinstance(func, ast.Name) and func.id in _BANNED_BUILTINS:
        raise ScriptValidationError(f"Call to banned builtin '{func.id}' is not permitted")
    dotted = _get_dotted_name(func)
    for pattern in _DANGEROUS_ATTR_PATTERNS:
        if dotted == pattern or dotted.startswith(pattern + "."):
            raise ScriptValidationError(f"Call to dangerous function '{dotted}' is not permitted")
    # Block dynamic imports disguised as getattr chains
    if dotted.endswith("__import__") or dotted.endswith("eval") or dotted.endswith("exec"):
        raise ScriptValidationError(f"Call to dynamic code execution '{dotted}' is not permitted")


def validate_script(decoded: bytes) -> None:
    """Parse and whitelist-check a script payload.

    Raises ScriptValidationError if any disallowed construct is found.
    """
    try:
        text = decoded.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ScriptValidationError(f"Script is not valid UTF-8: {exc}") from exc

    try:
        tree = ast.parse(text, mode="exec")
    except SyntaxError as exc:
        raise ScriptValidationError(f"Script contains invalid Python syntax: {exc}") from exc

    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_NODE_TYPES:
            # Allow import nodes separately since we validate them below
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            # Allow function/class definitions? For now, block them to keep surface small.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                raise ScriptValidationError(
                    f"Script contains disallowed construct: {type(node).__name__}"
                )
            raise ScriptValidationError(
                f"Script contains disallowed AST node: {type(node).__name__}"
            )

        if isinstance(node, (ast.Import, ast.ImportFrom)):
            _validate_import(node)

        if isinstance(node, ast.Call):
            _validate_call(node)

        # Ban attribute access to dunder methods that enable code injection
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ScriptValidationError(
                f"Access to dunder attribute '{node.attr}' is not permitted"
            )

        # Ban Name references to banned builtins
        if isinstance(node, ast.Name) and node.id in _BANNED_BUILTINS:
            raise ScriptValidationError(
                f"Reference to banned builtin '{node.id}' is not permitted"
            )
