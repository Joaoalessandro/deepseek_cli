from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import locale
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from openai import APITimeoutError, APIConnectionError, APIStatusError, OpenAI, RateLimitError
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table


APP_NAME = "DeepSeek Dev Agent"
APP_VERSION = "1.1.0"
AGENT_DIR_NAME = ".deepseek-agent"
TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "latin-1")
IGNORED_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    ".deepseek-agent",
    "node_modules",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
}
CODE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".kts",
    ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cs", ".php", ".rb",
    ".swift", ".dart", ".scala", ".groovy", ".sh", ".bash", ".ps1", ".bat",
    ".cmd", ".sql", ".html", ".css", ".scss", ".vue", ".svelte", ".xml",
    ".json", ".yaml", ".yml", ".toml", ".gradle", ".properties",
}
SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
    "service-account.json",
    "secrets.json",
}
VALIDATION_PATTERNS = (
    r"\bpytest\b",
    r"\bunittest\b",
    r"\bpython(?:3)?\s+-m\s+py_compile\b",
    r"\bpython(?:3)?\s+-m\s+compileall\b",
    r"\bruff\b",
    r"\bflake8\b",
    r"\bmypy\b",
    r"\bpyright\b",
    r"\bnpm\s+(?:run\s+)?(?:test|build|lint|check)\b",
    r"\bpnpm\s+(?:run\s+)?(?:test|build|lint|check)\b",
    r"\byarn\s+(?:test|build|lint|check)\b",
    r"\bbun\s+(?:test|run\s+(?:test|build|lint|check))\b",
    r"\bnpx\s+(?:tsc|eslint|jest|vitest)\b",
    r"\bnode\s+--check\b",
    r"\btsc\b.*--noEmit",
    r"\bmvn\w*\s+(?:test|verify|package)\b",
    r"\bgradle\w*\s+(?:test|check|build)\b",
    r"\bdotnet\s+(?:test|build)\b",
    r"\bgo\s+(?:test|vet|build)\b",
    r"\bcargo\s+(?:test|check|build|clippy)\b",
    r"\bflutter\s+(?:test|analyze|build)\b",
    r"\bdart\s+(?:test|analyze|compile)\b",
    r"\bcomposer\s+(?:test|validate)\b",
    r"\bphp\s+-l\b",
    r"\bgit\s+diff\s+--check\b",
)

console = Console(highlight=False)


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.72)
    tail = limit - head
    return f"{text[:head]}\n\n... [conteúdo truncado: {len(text) - limit} caracteres] ...\n\n{text[-tail:]}"


def json_text(data: Any, limit: int = 24000) -> str:
    return clip(json.dumps(data, ensure_ascii=False, indent=2, default=str), limit)


def is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def decode_file(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    if b"\x00" in raw[:8192]:
        raise ValueError("Arquivo aparentemente binário.")
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("Não foi possível decodificar o arquivo como texto.")


def atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(content, encoding=encoding, newline="")
    os.replace(temp, path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_is_validation(command: str) -> bool:
    return any(re.search(pattern, command, re.IGNORECASE) for pattern in VALIDATION_PATTERNS)


def file_is_code(path: Path) -> bool:
    name = path.name.lower()
    if path.suffix.lower() in CODE_EXTENSIONS:
        return True
    return name in {
        "dockerfile", "makefile", "pom.xml", "package.json", "pubspec.yaml",
        "requirements.txt", "pyproject.toml", "cargo.toml", "go.mod",
    }


def sanitized_subprocess_env() -> dict[str, str]:
    """Remove credenciais do ambiente herdado pelos comandos do agente."""
    blocked_tokens = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "API_KEY", "PRIVATE_KEY")
    safe_env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if key == "DEEPSEEK_API_KEY" or any(token in upper for token in blocked_tokens):
            continue
        safe_env[key] = value
    return safe_env


@contextmanager
def api_progress(label: str, heartbeat_seconds: int):
    """Mostra atividade mesmo quando o terminal não renderiza spinners dinâmicos."""
    started = time.monotonic()
    stop_event = threading.Event()
    console.print(f"[bold cyan][IA][/bold cyan] {label}...")

    def heartbeat() -> None:
        while not stop_event.wait(heartbeat_seconds):
            elapsed = int(time.monotonic() - started)
            console.print(
                f"[dim][IA] Aguardando resposta da DeepSeek há {elapsed}s. "
                "Pressione Ctrl+C para interromper.[/dim]"
            )

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=0.2)


@dataclass
class Config:
    api_key: str
    base_url: str
    model: str
    summary_model: str
    thinking: bool
    reasoning_effort: str
    workspace: Path
    max_rounds: int
    max_context_chars: int
    max_tool_output_chars: int
    command_timeout: int
    api_timeout: int
    heartbeat_seconds: int
    max_retries: int

    @classmethod
    def load(cls, workspace_arg: str | None = None) -> "Config":
        script_dir = Path(__file__).resolve().parent
        load_dotenv(script_dir / ".env")

        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key or api_key.lower() in {
            "coloque_sua_chave_aqui",
            "sua_chave",
            "your_api_key",
            "your_api_key_here",
        }:
            raise RuntimeError(
                "DEEPSEEK_API_KEY não configurada. Copie .env.example para .env e informe a chave."
            )

        workspace_raw = workspace_arg or os.getenv("AGENT_WORKSPACE") or os.getcwd()
        workspace = Path(workspace_raw).expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)

        return cls(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
            summary_model=os.getenv("DEEPSEEK_SUMMARY_MODEL", "deepseek-v4-flash"),
            thinking=os.getenv("DEEPSEEK_THINKING", "true").lower() in {"1", "true", "yes", "sim"},
            reasoning_effort=os.getenv("DEEPSEEK_REASONING_EFFORT", "high"),
            workspace=workspace,
            max_rounds=max(5, int(os.getenv("AGENT_MAX_ROUNDS", "60"))),
            max_context_chars=max(40000, int(os.getenv("AGENT_MAX_CONTEXT_CHARS", "140000"))),
            max_tool_output_chars=max(4000, int(os.getenv("AGENT_MAX_TOOL_OUTPUT_CHARS", "24000"))),
            command_timeout=max(10, int(os.getenv("AGENT_COMMAND_TIMEOUT", "180"))),
            api_timeout=max(30, int(os.getenv("AGENT_API_TIMEOUT", "300"))),
            heartbeat_seconds=max(5, int(os.getenv("AGENT_HEARTBEAT_SECONDS", "10"))),
            max_retries=max(1, int(os.getenv("AGENT_API_RETRIES", "2"))),
        )


@dataclass
class ValidationRecord:
    command: str
    cwd: str
    exit_code: int
    revision: int


@dataclass
class TaskTracker:
    task_id: str
    request: str
    started_at: str
    backup_dir: Path
    revision: int = 0
    changed_files: set[str] = field(default_factory=set)
    created_files: set[str] = field(default_factory=set)
    deleted_paths: set[str] = field(default_factory=set)
    backed_up_paths: set[str] = field(default_factory=set)
    commands: list[dict[str, Any]] = field(default_factory=list)
    validations: list[ValidationRecord] = field(default_factory=list)
    final_payload: dict[str, Any] | None = None

    def code_changed(self, root: Path) -> bool:
        return any(file_is_code(root / relative) for relative in self.changed_files)

    def has_current_successful_validation(self) -> bool:
        return any(item.revision == self.revision and item.exit_code == 0 for item in self.validations)

    def record_change(self, relative: str, created: bool = False, deleted: bool = False) -> None:
        self.revision += 1
        self.changed_files.add(relative)
        if created:
            self.created_files.add(relative)
        if deleted:
            self.deleted_paths.add(relative)


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.cwd = self.root
        self.agent_dir = self.root / AGENT_DIR_NAME
        self.backups_dir = self.agent_dir / "backups"
        self.tasks_dir = self.agent_dir / "tasks"
        self.memory_file = self.agent_dir / "MEMORY.md"
        self.last_task_file = self.agent_dir / "last_task.json"
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        if not self.memory_file.exists():
            atomic_write(
                self.memory_file,
                "# Memória do projeto\n\nAinda não há contexto persistente.\n",
            )

    def resolve(self, raw_path: str | None, base: Path | None = None) -> Path:
        value = (raw_path or ".").strip()
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = (base or self.cwd) / candidate
        resolved = candidate.resolve(strict=False)
        if not is_within(resolved, self.root):
            raise PermissionError(f"Caminho fora do workspace: {resolved}")
        return resolved

    def relative(self, path: Path) -> str:
        return path.resolve(strict=False).relative_to(self.root).as_posix()

    def set_cwd(self, raw_path: str) -> Path:
        path = self.resolve(raw_path)
        if not path.exists() or not path.is_dir():
            raise NotADirectoryError(f"Diretório inexistente: {path}")
        self.cwd = path
        return path

    def read_memory(self, limit: int = 12000) -> str:
        try:
            return clip(self.memory_file.read_text(encoding="utf-8"), limit)
        except OSError:
            return "# Memória do projeto\n\nIndisponível."

    def top_level_snapshot(self, limit: int = 120) -> str:
        lines: list[str] = []
        try:
            entries = sorted(self.cwd.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except OSError as exc:
            return f"Não foi possível listar o diretório: {exc}"
        for item in entries:
            if item.name in IGNORED_DIRS or item.name == AGENT_DIR_NAME:
                continue
            suffix = "/" if item.is_dir() else ""
            lines.append(f"- {item.name}{suffix}")
            if len(lines) >= limit:
                lines.append("- ...")
                break
        return "\n".join(lines) or "- diretório vazio"


class SafetyPolicy:
    BLOCK_PATTERNS = (
        r"(^|[;&|]\s*)rm\s+-rf\s+[/~](\s|$)",
        r"(^|[;&|]\s*)rm\s+-rf\s+\*",
        r"\bmkfs(?:\.|\s)",
        r"\bdd\s+if=.*\s+of=/dev/",
        r"\bformat\s+[a-z]:",
        r"\bdiskpart\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bbcdedit\b",
        r"\breg\s+delete\b",
        r"\bdel\s+/[a-z]*s[a-z]*\s+/[a-z]*q[a-z]*\s+[a-z]:\\",
        r"\brmdir\s+/[a-z]*s[a-z]*\s+/[a-z]*q[a-z]*\s+[a-z]:\\",
        r"\bRemove-Item\b.*(?:-Recurse|-Force).*(?:[A-Za-z]:\\|/)",
        r"\bcipher\s+/w:",
    )
    CONFIRM_PATTERNS = (
        r"\b(?:cat|type|Get-Content|more)\b.*(?:\.env(?:\.|\s|$)|id_rsa|id_ed25519|credentials|\.pem|\.key)",
        r"(?:%[A-Za-z_][A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Za-z0-9_]*%|\$env:[A-Za-z_][A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Za-z0-9_]*)",
        r"^\s*(?:env|printenv|set)\s*$",
        r"(?:^|[;&|]\s*)(?:cd|pushd)\s+\.\.[\\/]?",
        r"(?:^|\s)\.\.[\\/]",
        r"\brm\b",
        r"\bdel\b",
        r"\brmdir\b",
        r"\bRemove-Item\b",
        r"\bgit\s+(?:reset\s+--hard|clean\s+-|checkout\s+--|restore\s+--source)",
        r"\bgit\s+push\b",
        r"\bgit\s+commit\b",
        r"\bnpm\s+publish\b",
        r"\bpypi\b",
        r"\btwine\s+upload\b",
        r"\bdocker\s+system\s+prune\b",
        r"\bdocker\s+(?:rm|rmi)\b",
        r"\bterraform\s+(?:apply|destroy)\b",
        r"\bkubectl\s+(?:delete|apply)\b",
        r"\bchmod\s+-R\b",
        r"\bchown\s+-R\b",
    )

    @classmethod
    def classify_command(cls, command: str) -> tuple[str, str]:
        normalized = command.strip()
        for pattern in cls.BLOCK_PATTERNS:
            if re.search(pattern, normalized, re.IGNORECASE):
                return "block", f"Comando bloqueado pela política local: {pattern}"
        for pattern in cls.CONFIRM_PATTERNS:
            if re.search(pattern, normalized, re.IGNORECASE):
                return "confirm", "O comando pode excluir dados, publicar conteúdo ou alterar estado externo."
        return "allow", ""

    @staticmethod
    def is_sensitive(path: Path) -> bool:
        name = path.name.lower()
        if name in SENSITIVE_NAMES:
            return True
        suffix = path.suffix.lower()
        if suffix in {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}:
            return True
        lowered = path.as_posix().lower()
        return any(token in lowered for token in ("/.ssh/", "/.aws/", "/.gnupg/"))


class AgentTools:
    def __init__(self, workspace: Workspace, tracker: TaskTracker, config: Config):
        self.workspace = workspace
        self.tracker = tracker
        self.config = config
        self.handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "search_text": self.search_text,
            "write_file": self.write_file,
            "replace_in_file": self.replace_in_file,
            "make_directory": self.make_directory,
            "move_path": self.move_path,
            "delete_path": self.delete_path,
            "set_working_directory": self.set_working_directory,
            "run_command": self.run_command,
            "git_status": self.git_status,
            "finish_task": self.finish_task,
        }

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [
            self._schema("list_files", "Lista a árvore de arquivos sem enviar conteúdo dos arquivos.", {
                "path": {"type": "string", "description": "Caminho relativo ao diretório atual."},
                "max_depth": {"type": "integer", "description": "Profundidade máxima, de 1 a 8."},
                "limit": {"type": "integer", "description": "Máximo de entradas, de 1 a 500."},
            }, ["path", "max_depth", "limit"]),
            self._schema("read_file", "Lê somente um intervalo de linhas de um arquivo de texto.", {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            }, ["path", "start_line", "end_line"]),
            self._schema("search_text", "Procura texto ou expressão regular nos arquivos do workspace.", {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string", "description": "Filtro como *.py ou **/*.ts."},
                "regex": {"type": "boolean"},
                "case_sensitive": {"type": "boolean"},
                "max_results": {"type": "integer"},
            }, ["query", "path", "glob", "regex", "case_sensitive", "max_results"]),
            self._schema("write_file", "Cria ou sobrescreve atomicamente um arquivo de texto e salva backup.", {
                "path": {"type": "string"},
                "content": {"type": "string"},
            }, ["path", "content"]),
            self._schema("replace_in_file", "Substitui trecho exato em arquivo e salva backup antes da mudança.", {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
                "replace_all": {"type": "boolean"},
            }, ["path", "old", "new", "replace_all"]),
            self._schema("make_directory", "Cria um diretório dentro do workspace.", {
                "path": {"type": "string"},
            }, ["path"]),
            self._schema("move_path", "Move ou renomeia arquivo ou diretório dentro do workspace.", {
                "source": {"type": "string"},
                "destination": {"type": "string"},
            }, ["source", "destination"]),
            self._schema("delete_path", "Exclui arquivo ou diretório após confirmação humana e mantém backup.", {
                "path": {"type": "string"},
            }, ["path"]),
            self._schema("set_working_directory", "Muda o diretório de trabalho atual dentro do workspace.", {
                "path": {"type": "string"},
            }, ["path"]),
            self._schema("run_command", "Executa comando no terminal, captura saída e código de retorno.", {
                "command": {"type": "string"},
                "cwd": {"type": "string", "description": "Diretório relativo; use vazio para o atual."},
                "timeout": {"type": "integer", "description": "Tempo máximo em segundos."},
            }, ["command", "cwd", "timeout"]),
            self._schema("git_status", "Obtém status e diff resumido do Git sem modificar o repositório.", {
                "path": {"type": "string"},
            }, ["path"]),
            self._schema("finish_task", "Finaliza apenas após revisar e validar as alterações.", {
                "summary": {"type": "string", "description": "Resumo objetivo do que foi concluído."},
                "tests": {"type": "string", "description": "Testes ou validações executados e resultados."},
                "remaining": {"type": "string", "description": "Pendências reais; vazio quando não houver."},
            }, ["summary", "tests", "remaining"]),
        ]

    @staticmethod
    def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        handler = self.handlers.get(name)
        if handler is None:
            return json_text({"ok": False, "error": f"Ferramenta desconhecida: {name}"})
        try:
            return json_text(handler(**arguments), self.config.max_tool_output_chars)
        except TypeError as exc:
            return json_text({"ok": False, "error": f"Argumentos inválidos: {exc}"})
        except Exception as exc:
            return json_text({"ok": False, "error": str(exc), "type": type(exc).__name__})

    def _backup(self, path: Path) -> None:
        relative = self.workspace.relative(path)
        if relative in self.tracker.backed_up_paths or not path.exists():
            return
        destination = self.tracker.backup_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, destination)
        else:
            shutil.copy2(path, destination)
        self.tracker.backed_up_paths.add(relative)

    def _confirm_sensitive(self, path: Path, action: str) -> bool:
        if not SafetyPolicy.is_sensitive(path):
            return True
        return Confirm.ask(
            f"[yellow]{action} arquivo potencialmente sensível[/yellow] [bold]{path}[/bold]?",
            default=False,
        )

    def list_files(self, path: str, max_depth: int, limit: int) -> dict[str, Any]:
        base = self.workspace.resolve(path)
        if not base.exists() or not base.is_dir():
            return {"ok": False, "error": "Diretório inexistente."}
        max_depth = min(max(int(max_depth), 1), 8)
        limit = min(max(int(limit), 1), 500)
        lines: list[str] = []
        count = 0

        def walk(current: Path, depth: int) -> None:
            nonlocal count
            if depth > max_depth or count >= limit:
                return
            try:
                entries = sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
            except OSError:
                return
            for item in entries:
                if count >= limit:
                    return
                if item.name in IGNORED_DIRS or item.name == AGENT_DIR_NAME:
                    continue
                relative = item.relative_to(base).as_posix()
                suffix = "/" if item.is_dir() else f" ({item.stat().st_size} bytes)"
                lines.append(f"{'  ' * (depth - 1)}{relative}{suffix}")
                count += 1
                if item.is_dir():
                    walk(item, depth + 1)

        walk(base, 1)
        return {
            "ok": True,
            "base": self.workspace.relative(base) or ".",
            "entries": lines,
            "truncated": count >= limit,
        }

    def read_file(self, path: str, start_line: int, end_line: int) -> dict[str, Any]:
        target = self.workspace.resolve(path)
        if not target.exists() or not target.is_file():
            return {"ok": False, "error": "Arquivo inexistente."}
        if target.stat().st_size > 5 * 1024 * 1024:
            return {"ok": False, "error": "Arquivo maior que 5 MB. Leia uma versão reduzida ou use busca textual."}
        if not self._confirm_sensitive(target, "Ler"):
            return {"ok": False, "error": "Leitura não autorizada pelo usuário."}
        text, encoding = decode_file(target)
        lines = text.splitlines()
        start = max(1, int(start_line))
        end = min(max(start, int(end_line)), min(len(lines), start + 799))
        selected = [f"{number:>6}: {lines[number - 1]}" for number in range(start, end + 1)]
        return {
            "ok": True,
            "path": self.workspace.relative(target),
            "encoding": encoding,
            "total_lines": len(lines),
            "start_line": start,
            "end_line": end,
            "content": "\n".join(selected),
        }

    def search_text(
        self,
        query: str,
        path: str,
        glob: str,
        regex: bool,
        case_sensitive: bool,
        max_results: int,
    ) -> dict[str, Any]:
        base = self.workspace.resolve(path)
        if not base.exists() or not base.is_dir():
            return {"ok": False, "error": "Diretório inexistente."}
        max_results = min(max(int(max_results), 1), 300)
        flags = 0 if case_sensitive else re.IGNORECASE
        matcher = re.compile(query if regex else re.escape(query), flags)
        results: list[dict[str, Any]] = []
        skipped = 0
        for current, directories, files in os.walk(base):
            directories[:] = [item for item in directories if item not in IGNORED_DIRS and item != AGENT_DIR_NAME]
            current_path = Path(current)
            for filename in files:
                target = current_path / filename
                relative_to_base = target.relative_to(base).as_posix()
                if glob and glob not in {"*", "**/*"}:
                    if not (fnmatch.fnmatch(filename, glob) or fnmatch.fnmatch(relative_to_base, glob)):
                        continue
                try:
                    if target.stat().st_size > 2 * 1024 * 1024 or SafetyPolicy.is_sensitive(target):
                        skipped += 1
                        continue
                    text, _ = decode_file(target)
                except (OSError, ValueError):
                    skipped += 1
                    continue
                for number, line in enumerate(text.splitlines(), 1):
                    if matcher.search(line):
                        results.append({
                            "path": self.workspace.relative(target),
                            "line": number,
                            "text": clip(line.strip(), 500),
                        })
                        if len(results) >= max_results:
                            return {"ok": True, "results": results, "truncated": True, "skipped": skipped}
        return {"ok": True, "results": results, "truncated": False, "skipped": skipped}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        target = self.workspace.resolve(path)
        if len(content) > 1_500_000:
            return {"ok": False, "error": "Conteúdo acima de 1,5 milhão de caracteres."}
        if not self._confirm_sensitive(target, "Alterar"):
            return {"ok": False, "error": "Alteração não autorizada pelo usuário."}
        existed = target.exists()
        if existed and target.is_dir():
            return {"ok": False, "error": "O caminho aponta para um diretório."}
        old_hash = file_sha256(target) if existed else None
        if existed:
            self._backup(target)
        atomic_write(target, content)
        new_hash = file_sha256(target)
        relative = self.workspace.relative(target)
        if old_hash != new_hash:
            self.tracker.record_change(relative, created=not existed)
        return {
            "ok": True,
            "path": relative,
            "created": not existed,
            "changed": old_hash != new_hash,
            "bytes": target.stat().st_size,
            "revision": self.tracker.revision,
        }

    def replace_in_file(self, path: str, old: str, new: str, replace_all: bool) -> dict[str, Any]:
        target = self.workspace.resolve(path)
        if not target.exists() or not target.is_file():
            return {"ok": False, "error": "Arquivo inexistente."}
        if not old:
            return {"ok": False, "error": "O trecho antigo não pode ser vazio."}
        if not self._confirm_sensitive(target, "Alterar"):
            return {"ok": False, "error": "Alteração não autorizada pelo usuário."}
        text, encoding = decode_file(target)
        occurrences = text.count(old)
        if occurrences == 0:
            return {"ok": False, "error": "Trecho exato não encontrado."}
        if occurrences > 1 and not replace_all:
            return {
                "ok": False,
                "error": f"Trecho encontrado {occurrences} vezes. Forneça contexto maior ou use replace_all=true.",
            }
        self._backup(target)
        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        atomic_write(target, updated, encoding=encoding)
        relative = self.workspace.relative(target)
        self.tracker.record_change(relative)
        return {
            "ok": True,
            "path": relative,
            "replacements": occurrences if replace_all else 1,
            "revision": self.tracker.revision,
        }

    def make_directory(self, path: str) -> dict[str, Any]:
        target = self.workspace.resolve(path)
        existed = target.exists()
        target.mkdir(parents=True, exist_ok=True)
        if not existed:
            self.tracker.record_change(self.workspace.relative(target), created=True)
        return {"ok": True, "path": self.workspace.relative(target), "created": not existed}

    def move_path(self, source: str, destination: str) -> dict[str, Any]:
        source_path = self.workspace.resolve(source)
        destination_path = self.workspace.resolve(destination)
        if not source_path.exists():
            return {"ok": False, "error": "Origem inexistente."}
        if destination_path.exists():
            return {"ok": False, "error": "Destino já existe."}
        source_relative = self.workspace.relative(source_path)
        destination_relative = self.workspace.relative(destination_path)
        self._backup(source_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(destination_path))
        self.tracker.record_change(source_relative, deleted=True)
        self.tracker.record_change(destination_relative, created=True)
        return {"ok": True, "source": source_relative, "destination": destination_relative}

    def delete_path(self, path: str) -> dict[str, Any]:
        target = self.workspace.resolve(path)
        if not target.exists():
            return {"ok": False, "error": "Caminho inexistente."}
        if target == self.workspace.root or target == self.workspace.agent_dir:
            return {"ok": False, "error": "Exclusão bloqueada."}
        relative = self.workspace.relative(target)
        if not Confirm.ask(f"[red]Permitir exclusão de[/red] [bold]{relative}[/bold]?", default=False):
            return {"ok": False, "error": "Exclusão não autorizada pelo usuário."}
        self._backup(target)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        self.tracker.record_change(relative, deleted=True)
        return {"ok": True, "deleted": relative, "revision": self.tracker.revision}

    def set_working_directory(self, path: str) -> dict[str, Any]:
        target = self.workspace.set_cwd(path)
        return {"ok": True, "cwd": self.workspace.relative(target) or "."}

    def run_command(self, command: str, cwd: str, timeout: int) -> dict[str, Any]:
        command = command.strip()
        if not command:
            return {"ok": False, "error": "Comando vazio."}
        target_cwd = self.workspace.resolve(cwd or ".")
        if not target_cwd.exists() or not target_cwd.is_dir():
            return {"ok": False, "error": "Diretório de execução inexistente."}
        classification, reason = SafetyPolicy.classify_command(command)
        if classification == "block":
            return {"ok": False, "error": reason}
        if classification == "confirm":
            console.print(Panel.fit(f"[bold]{command}[/bold]\n\n{reason}", title="Confirmação necessária", border_style="yellow"))
            if not Confirm.ask("Executar este comando?", default=False):
                return {"ok": False, "error": "Comando não autorizado pelo usuário."}
        timeout = min(max(int(timeout), 1), 900)
        relative_cwd = self.workspace.relative(target_cwd) or "."
        console.print(f"[cyan]$[/cyan] [bold]{command}[/bold]  [dim]({relative_cwd})[/dim]")
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=target_cwd,
                shell=True,
                capture_output=True,
                text=True,
                encoding=locale.getpreferredencoding(False) or "utf-8",
                errors="replace",
                timeout=timeout,
                env=sanitized_subprocess_env(),
            )
            duration = round(time.monotonic() - started, 3)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            record = {
                "command": command,
                "cwd": relative_cwd,
                "exit_code": completed.returncode,
                "duration_seconds": duration,
            }
            self.tracker.commands.append(record)
            if command_is_validation(command):
                self.tracker.validations.append(ValidationRecord(
                    command=command,
                    cwd=relative_cwd,
                    exit_code=completed.returncode,
                    revision=self.tracker.revision,
                ))
            status = "green" if completed.returncode == 0 else "red"
            console.print(f"[{status}]exit {completed.returncode}[/{status}] [dim]{duration}s[/dim]")
            return {
                "ok": completed.returncode == 0,
                **record,
                "stdout": clip(stdout, self.config.max_tool_output_chars // 2),
                "stderr": clip(stderr, self.config.max_tool_output_chars // 2),
                "output_truncated": len(stdout) + len(stderr) > self.config.max_tool_output_chars,
                "current_revision": self.tracker.revision,
            }
        except subprocess.TimeoutExpired as exc:
            duration = round(time.monotonic() - started, 3)
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            record = {
                "command": command,
                "cwd": relative_cwd,
                "exit_code": 124,
                "duration_seconds": duration,
            }
            self.tracker.commands.append(record)
            if command_is_validation(command):
                self.tracker.validations.append(ValidationRecord(
                    command=command,
                    cwd=relative_cwd,
                    exit_code=124,
                    revision=self.tracker.revision,
                ))
            return {
                "ok": False,
                "error": f"Comando excedeu {timeout} segundos.",
                **record,
                "stdout": clip(str(stdout), self.config.max_tool_output_chars // 2),
                "stderr": clip(str(stderr), self.config.max_tool_output_chars // 2),
            }

    def git_status(self, path: str) -> dict[str, Any]:
        target = self.workspace.resolve(path or ".")
        probe = subprocess.run(
            "git rev-parse --show-toplevel",
            cwd=target,
            shell=True,
            capture_output=True,
            text=True,
            encoding=locale.getpreferredencoding(False) or "utf-8",
            errors="replace",
        )
        if probe.returncode != 0:
            return {"ok": False, "error": "O diretório não pertence a um repositório Git."}
        status = subprocess.run(
            "git status --short && git diff --stat && git diff --check",
            cwd=target,
            shell=True,
            capture_output=True,
            text=True,
            encoding=locale.getpreferredencoding(False) or "utf-8",
            errors="replace",
        )
        return {
            "ok": status.returncode == 0,
            "exit_code": status.returncode,
            "output": clip((status.stdout or "") + (status.stderr or ""), self.config.max_tool_output_chars),
        }

    def finish_task(self, summary: str, tests: str, remaining: str) -> dict[str, Any]:
        if self.tracker.code_changed(self.workspace.root) and not self.tracker.has_current_successful_validation():
            return {
                "ok": False,
                "error": (
                    "Há alterações de código sem validação bem-sucedida na revisão atual. "
                    "Execute teste, build, lint, compilação ou git diff --check apropriado antes de finalizar."
                ),
                "revision": self.tracker.revision,
            }
        self.tracker.final_payload = {
            "summary": summary.strip(),
            "tests": tests.strip(),
            "remaining": remaining.strip(),
        }
        return {"ok": True, "message": "Tarefa registrada como concluída."}


class DeepSeekAgent:
    def __init__(self, config: Config):
        self.config = config
        self.workspace = Workspace(config.workspace)
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=float(config.api_timeout),
            max_retries=0,
        )
        self.current_tracker: TaskTracker | None = None
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        return """
Você é um agente local de programação sênior operando ferramentas reais no computador do usuário.

Regras operacionais:
1. Trabalhe autonomamente até concluir a solicitação ou encontrar um bloqueio externo real.
2. Antes de editar, inspecione a estrutura e leia apenas os arquivos e intervalos necessários.
3. Não invente arquivos, resultados de comandos, APIs ou testes. Use as ferramentas.
4. Mantenha o trabalho dentro do workspace. Use set_working_directory para navegar.
5. Faça mudanças pequenas, coerentes e compatíveis com o projeto existente. Evite reescrever arquivos inteiros quando replace_in_file for suficiente.
6. Não leia segredos. Nunca exponha chaves, tokens, senhas, cookies ou credenciais em respostas, logs ou arquivos.
7. Não execute exclusões, publicação, push, deploy ou alterações externas sem a confirmação exigida pelas ferramentas.
8. Após alterar código, execute testes, build, lint, compilação ou outra validação adequada. Se falhar, analise e corrija; depois valide novamente.
9. Revise o diff e a consistência antes de terminar. Não declare sucesso quando ainda houver erro conhecido.
10. Seja econômico no contexto: use buscas e leituras por linha; não leia diretórios de dependências nem arquivos grandes sem necessidade.
11. Não mostre raciocínio interno. Mostre apenas atualizações curtas de execução quando forem úteis.
12. Para finalizar, chame finish_task com resumo objetivo, validações executadas e pendências reais. O campo remaining deve ficar vazio quando não houver pendências.

Quando a solicitação estiver ambígua, investigue o projeto primeiro. Só peça informação ao usuário quando uma decisão de produto, credencial ou dado ausente impedir o avanço seguro.
""".strip()

    def _new_tracker(self, request: str) -> TaskTracker:
        task_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        backup_dir = self.workspace.backups_dir / task_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        return TaskTracker(
            task_id=task_id,
            request=request,
            started_at=utc_now_iso(),
            backup_dir=backup_dir,
        )

    def _task_user_message(self, request: str, progress_summary: str = "") -> str:
        memory = self.workspace.read_memory()
        snapshot = self.workspace.top_level_snapshot()
        cwd = self.workspace.relative(self.workspace.cwd) or "."
        sections = [
            f"WORKSPACE: {self.workspace.root}",
            f"DIRETÓRIO ATUAL: {cwd}",
            f"SISTEMA: {platform.system()} {platform.release()} | Python {platform.python_version()}",
            "\nMEMÓRIA PERSISTENTE CURTA:\n" + memory,
            "\nVISÃO DO DIRETÓRIO ATUAL:\n" + snapshot,
            "\nSOLICITAÇÃO ATUAL:\n" + request,
        ]
        if progress_summary:
            sections.append("\nRESUMO DO PROGRESSO DESTA TAREFA:\n" + progress_summary)
        return "\n".join(sections)

    def _api_call(self, **kwargs: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except (APITimeoutError, RateLimitError, APIConnectionError, APIStatusError) as exc:
                last_error = exc
                if isinstance(exc, APIStatusError) and exc.status_code < 500 and exc.status_code != 429:
                    raise
                if attempt >= self.config.max_retries:
                    raise
                delay = min(2 ** attempt, 12)
                console.print(
                    f"[yellow]Falha temporária da API na tentativa {attempt}/"
                    f"{self.config.max_retries}. Nova tentativa em {delay}s.[/yellow]"
                )
                time.sleep(delay)
        raise RuntimeError(str(last_error))

    def _format_api_error(self, exc: Exception) -> str:
        if isinstance(exc, APITimeoutError):
            return (
                f"A DeepSeek não respondeu dentro de {self.config.api_timeout}s. "
                "Tente novamente, use deepseek-v4-flash ou aumente AGENT_API_TIMEOUT."
            )
        if isinstance(exc, RateLimitError):
            return "A API limitou as requisições. Aguarde alguns instantes e tente novamente."
        if isinstance(exc, APIConnectionError):
            return (
                "Não foi possível conectar à DeepSeek. Verifique internet, proxy, firewall, "
                "certificados e DEEPSEEK_BASE_URL."
            )
        if isinstance(exc, APIStatusError):
            status = exc.status_code
            explanations = {
                400: "Requisição rejeitada. Verifique o modelo e a configuração de thinking/tools.",
                401: "Chave da API inválida ou ausente.",
                402: "A conta da API está sem saldo disponível.",
                403: "A chave não tem permissão para essa operação.",
                404: "Endpoint ou modelo não encontrado.",
                429: "Limite de requisições atingido.",
            }
            detail = explanations.get(status, "A API retornou um erro.")
            return f"DeepSeek HTTP {status}: {detail} Detalhe: {clip(str(exc), 1000)}"
        return clip(str(exc), 1200)

    def _chat(self, messages: list[Any], tools: list[dict[str, Any]] | None = None, model: str | None = None, thinking: bool | None = None, max_tokens: int | None = None) -> Any:
        thinking_enabled = self.config.thinking if thinking is None else thinking
        kwargs: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            kwargs["tools"] = tools
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if thinking_enabled:
            kwargs["reasoning_effort"] = self.config.reasoning_effort
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            kwargs["temperature"] = 0.2
        return self._api_call(**kwargs)

    @staticmethod
    def _assistant_message_dict(message: Any) -> dict[str, Any]:
        """Mantém reasoning_content em turnos com ferramentas, como exigido pela API."""
        if hasattr(message, "model_dump"):
            raw = message.model_dump(exclude_none=True)
        else:
            raw = dict(message)
        normalized: dict[str, Any] = {
            "role": "assistant",
            "content": raw.get("content") or "",
        }
        reasoning_content = raw.get("reasoning_content") or getattr(message, "reasoning_content", None)
        if reasoning_content:
            normalized["reasoning_content"] = reasoning_content
        if raw.get("tool_calls"):
            normalized["tool_calls"] = raw["tool_calls"]
        return normalized

    @staticmethod
    def _describe_tool(name: str, arguments: dict[str, Any]) -> str:
        path = arguments.get("path") or arguments.get("source") or ""
        descriptions = {
            "list_files": f"Listando arquivos em {path or '.'}",
            "read_file": f"Lendo {path}",
            "search_text": f"Pesquisando por {arguments.get('query', '')!r}",
            "write_file": f"Gravando {path}",
            "replace_in_file": f"Alterando {path}",
            "make_directory": f"Criando diretório {path}",
            "move_path": f"Movendo {arguments.get('source', '')} para {arguments.get('destination', '')}",
            "delete_path": f"Excluindo {path}",
            "set_working_directory": f"Mudando diretório para {path}",
            "run_command": f"Executando: {clip(str(arguments.get('command', '')), 180)}",
            "git_status": f"Revisando Git em {path or '.'}",
            "finish_task": "Finalizando e registrando o resultado",
        }
        return descriptions.get(name, f"Executando ferramenta {name}")

    @staticmethod
    def _message_size(messages: list[Any]) -> int:
        total = 0
        for message in messages:
            if hasattr(message, "model_dump_json"):
                total += len(message.model_dump_json())
            else:
                total += len(json.dumps(message, ensure_ascii=False, default=str))
        return total

    def _compact_context(self, request: str, messages: list[Any]) -> str:
        serializable: list[dict[str, Any]] = []
        for item in messages[1:]:
            if hasattr(item, "model_dump"):
                data = item.model_dump(exclude_none=True)
            else:
                data = dict(item)
            data.pop("reasoning_content", None)
            if data.get("role") == "tool":
                data["content"] = clip(str(data.get("content", "")), 6000)
            serializable.append(data)
        transcript = clip(json.dumps(serializable, ensure_ascii=False, default=str), 110000)
        prompt = f"""
Resuma o progresso de uma tarefa de programação para que outro agente continue sem o histórico completo.
Não inclua raciocínio interno. Preserve somente fatos verificados, arquivos relevantes, alterações feitas, comandos e resultados, erros atuais e próximos passos.
Máximo de 2500 palavras. Markdown compacto.

Solicitação original:
{request}

Histórico operacional:
{transcript}
""".strip()
        response = self._chat(
            messages=[
                {"role": "system", "content": "Você resume sessões técnicas com precisão e sem inventar fatos."},
                {"role": "user", "content": prompt},
            ],
            model=self.config.summary_model,
            thinking=False,
            max_tokens=3500,
        )
        return response.choices[0].message.content or "Progresso não resumido. Reinspecione os arquivos necessários."

    def _update_memory(self, tracker: TaskTracker) -> None:
        old_memory = self.workspace.read_memory(12000)
        changed = "\n".join(f"- {item}" for item in sorted(tracker.changed_files)) or "- Nenhum arquivo alterado"
        commands = "\n".join(
            f"- `{item['command']}` em `{item['cwd']}`: exit {item['exit_code']}"
            for item in tracker.commands[-20:]
        ) or "- Nenhum comando"
        final = tracker.final_payload or {}
        prompt = f"""
Atualize a memória Markdown curta de um projeto de software.
A memória será enviada em futuras tarefas, então retenha apenas contexto durável e útil.
Remova detalhes obsoletos, repetições, saídas extensas, raciocínio e conversas.
Não inclua segredos. Limite aproximado: 9000 caracteres.
Use estas seções quando aplicáveis: Visão geral, Stack e comandos, Arquitetura, Decisões, Estado atual, Pendências.

MEMÓRIA ANTERIOR:
{old_memory}

TAREFA:
{tracker.request}

ARQUIVOS TOCADOS:
{changed}

COMANDOS E RESULTADOS:
{commands}

RESULTADO:
Resumo: {final.get('summary', '')}
Testes: {final.get('tests', '')}
Pendências: {final.get('remaining', '')}
""".strip()
        try:
            response = self._chat(
                messages=[
                    {"role": "system", "content": "Produza somente Markdown técnico compacto e factual."},
                    {"role": "user", "content": prompt},
                ],
                model=self.config.summary_model,
                thinking=False,
                max_tokens=3000,
            )
            content = response.choices[0].message.content or old_memory
            atomic_write(self.workspace.memory_file, clip(content.strip() + "\n", 12000))
        except Exception as exc:
            console.print(f"[yellow]Não foi possível atualizar MEMORY.md: {exc}[/yellow]")

    def _write_task_metadata(self, tracker: TaskTracker) -> None:
        payload = {
            "task_id": tracker.task_id,
            "request": tracker.request,
            "started_at": tracker.started_at,
            "finished_at": utc_now_iso(),
            "backup_dir": str(tracker.backup_dir),
            "revision": tracker.revision,
            "changed_files": sorted(tracker.changed_files),
            "created_files": sorted(tracker.created_files),
            "deleted_paths": sorted(tracker.deleted_paths),
            "backed_up_paths": sorted(tracker.backed_up_paths),
            "commands": tracker.commands,
            "validations": [item.__dict__ for item in tracker.validations],
            "result": tracker.final_payload,
        }
        task_file = self.workspace.tasks_dir / f"{tracker.task_id}.json"
        atomic_write(task_file, json.dumps(payload, ensure_ascii=False, indent=2))
        atomic_write(self.workspace.last_task_file, json.dumps(payload, ensure_ascii=False, indent=2))

    def run_task(self, request: str) -> None:
        tracker = self._new_tracker(request)
        self.current_tracker = tracker
        tools = AgentTools(self.workspace, tracker, self.config)
        messages: list[Any] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self._task_user_message(request)},
        ]
        rounds = 0
        progress_summary = ""

        console.print(Panel.fit(request, title=f"Tarefa {tracker.task_id}", border_style="cyan"))

        try:
            while rounds < self.config.max_rounds:
                rounds += 1
                if self._message_size(messages) > self.config.max_context_chars:
                    console.print("[dim]Compactando o contexto operacional desta tarefa...[/dim]")
                    progress_summary = self._compact_context(request, messages)
                    messages = [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": self._task_user_message(request, progress_summary)},
                    ]

                with api_progress(
                    f"Analisando a tarefa (etapa {rounds}/{self.config.max_rounds})",
                    self.config.heartbeat_seconds,
                ):
                    response = self._chat(messages=messages, tools=tools.schemas)
                message = response.choices[0].message
                assistant_message = self._assistant_message_dict(message)
                messages.append(assistant_message)

                content = str(assistant_message.get("content") or "").strip()
                if content:
                    console.print(Markdown(content))

                tool_calls = message.tool_calls or []
                if not tool_calls:
                    if tracker.final_payload is None:
                        messages.append({
                            "role": "user",
                            "content": (
                                "Você ainda não chamou finish_task. Revise o estado real, valide alterações de código "
                                "e finalize pela ferramenta. Se estiver bloqueado, registre a pendência real."
                            ),
                        })
                        continue
                    break

                for tool_call in tool_calls:
                    name = tool_call.function.name
                    if name == "finish_task" and len(tool_calls) > 1:
                        result = json_text({
                            "ok": False,
                            "error": "Chame finish_task sozinho, depois de todas as demais ferramentas.",
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result,
                        })
                        continue
                    try:
                        arguments = json.loads(tool_call.function.arguments or "{}")
                    except json.JSONDecodeError as exc:
                        result = json_text({"ok": False, "error": f"JSON inválido nos argumentos: {exc}"})
                    else:
                        console.print(f"[bold blue][AÇÃO][/bold blue] {self._describe_tool(name, arguments)}")
                        result = tools.execute(name, arguments)
                        try:
                            parsed_result = json.loads(result)
                        except json.JSONDecodeError:
                            parsed_result = {"ok": False}
                        status = "green" if parsed_result.get("ok") else "red"
                        console.print(f"[{status}][{'OK' if parsed_result.get('ok') else 'ERRO'}][/{status}] {name}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })

                if tracker.final_payload is not None:
                    break

            if tracker.final_payload is None:
                tracker.final_payload = {
                    "summary": "A execução atingiu o limite operacional antes de uma conclusão confirmada.",
                    "tests": "Consulte os comandos exibidos e o registro da tarefa.",
                    "remaining": "Continuar a partir do estado atual ou aumentar AGENT_MAX_ROUNDS.",
                }

        except KeyboardInterrupt:
            tracker.final_payload = {
                "summary": "Tarefa interrompida pelo usuário.",
                "tests": "Não concluídos.",
                "remaining": "Revisar alterações parciais antes de continuar.",
            }
            console.print("\n[yellow]Tarefa interrompida.[/yellow]")
        except Exception as exc:
            tracker.final_payload = {
                "summary": "A tarefa terminou com erro no agente.",
                "tests": "Não concluídos.",
                "remaining": self._format_api_error(exc),
            }
            console.print(
                Panel.fit(
                    self._format_api_error(exc),
                    title=f"Erro: {type(exc).__name__}",
                    border_style="red",
                )
            )
            if os.getenv("AGENT_DEBUG", "false").lower() in {"1", "true", "yes"}:
                console.print(traceback.format_exc())
        finally:
            self._write_task_metadata(tracker)
            if tracker.changed_files or tracker.commands:
                self._update_memory(tracker)
            self._show_completion(tracker)
            self.current_tracker = None

    def _show_completion(self, tracker: TaskTracker) -> None:
        result = tracker.final_payload or {}
        body = f"**Concluído:** {result.get('summary', '')}\n\n**Validação:** {result.get('tests', '')}"
        remaining = result.get("remaining", "").strip()
        if remaining:
            body += f"\n\n**Pendências:** {remaining}"
        body += f"\n\n**Memória:** `{self.workspace.memory_file}`\n**Backup:** `{tracker.backup_dir}`"
        console.print(Panel(Markdown(body), title="Resultado", border_style="green" if not remaining else "yellow"))

    def undo_last_task(self) -> None:
        if not self.workspace.last_task_file.exists():
            console.print("[yellow]Nenhuma tarefa anterior registrada.[/yellow]")
            return
        data = json.loads(self.workspace.last_task_file.read_text(encoding="utf-8"))
        backup_dir = Path(data["backup_dir"])
        created_files = sorted(data.get("created_files", []), key=lambda item: item.count("/"), reverse=True)
        backed_up_paths = sorted(data.get("backed_up_paths", []), key=lambda item: item.count("/"))

        table = Table(title=f"Desfazer tarefa {data.get('task_id')}")
        table.add_column("Ação")
        table.add_column("Caminho")
        for relative in created_files:
            table.add_row("Remover criado", relative)
        for relative in backed_up_paths:
            table.add_row("Restaurar backup", relative)
        console.print(table)
        if not Confirm.ask("Confirmar restauração?", default=False):
            return

        for relative in created_files:
            target = self.workspace.root / relative
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()

        for relative in backed_up_paths:
            source = backup_dir / relative
            target = self.workspace.root / relative
            if not source.exists():
                continue
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)

        console.print("[green]Última tarefa desfeita.[/green]")

    def show_status(self) -> None:
        table = Table(title=APP_NAME)
        table.add_column("Campo")
        table.add_column("Valor")
        table.add_row("Versão", APP_VERSION)
        table.add_row("Modelo", self.config.model)
        table.add_row("Thinking", "ativado" if self.config.thinking else "desativado")
        table.add_row("Timeout da API", f"{self.config.api_timeout}s")
        table.add_row("Tentativas da API", str(self.config.max_retries))
        table.add_row("Workspace", str(self.workspace.root))
        table.add_row("Diretório atual", self.workspace.relative(self.workspace.cwd) or ".")
        table.add_row("Memória", str(self.workspace.memory_file))
        console.print(table)

    def doctor(self) -> bool:
        console.print(Panel.fit(
            "Verificando configuração, autenticação, conexão e modelo sem executar uma tarefa.",
            title="Diagnóstico DeepSeek",
            border_style="cyan",
        ))
        table = Table()
        table.add_column("Item")
        table.add_column("Valor")
        table.add_row("Base URL", self.config.base_url)
        table.add_row("Modelo", self.config.model)
        table.add_row("Thinking", "ativado" if self.config.thinking else "desativado")
        table.add_row("Timeout", f"{self.config.api_timeout}s")
        table.add_row("Chave", "configurada" if self.config.api_key else "ausente")
        console.print(table)

        try:
            with api_progress("Consultando modelos disponíveis", self.config.heartbeat_seconds):
                response = self.client.models.list()
            model_ids = sorted(item.id for item in response.data)
            console.print(f"[green]Conexão e autenticação: OK[/green]")
            if model_ids:
                console.print("Modelos retornados: " + ", ".join(model_ids))
            if model_ids and self.config.model not in model_ids:
                console.print(
                    f"[yellow]O modelo configurado `{self.config.model}` não apareceu na lista da conta.[/yellow]"
                )
                return False
            return True
        except Exception as exc:
            console.print(Panel.fit(self._format_api_error(exc), title="Diagnóstico falhou", border_style="red"))
            return False

    def print_help(self) -> None:
        console.print(Markdown("""
### Comandos locais

- `:help` — mostra esta ajuda
- `:status` — mostra modelo, workspace e diretório atual
- `:doctor` — testa chave, conexão e modelo
- `:cd caminho` — muda o diretório atual dentro do workspace
- `:memory` — mostra a memória persistente curta
- `:undo` — restaura os backups da última tarefa
- `:clear` — limpa a tela
- `:exit` — encerra o agente

Qualquer outro texto inicia uma tarefa autônoma.
"""))

    def repl(self) -> None:
        console.print(Panel.fit(
            f"[bold]{APP_NAME} {APP_VERSION}[/bold]\n"
            f"Modelo: {self.config.model}\n"
            f"Workspace: {self.workspace.root}\n\n"
            "Digite uma tarefa ou use :help.",
            border_style="cyan",
        ))
        while True:
            try:
                cwd = self.workspace.relative(self.workspace.cwd) or "."
                raw = Prompt.ask(f"[bold cyan]agente[/bold cyan] [dim]{cwd}[/dim]").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\nEncerrado.")
                return
            if not raw:
                continue
            if raw in {":exit", ":quit", "sair", "exit", "quit"}:
                console.print("Encerrado.")
                return
            if raw == ":help":
                self.print_help()
                continue
            if raw == ":status":
                self.show_status()
                continue
            if raw == ":doctor":
                self.doctor()
                continue
            if raw == ":memory":
                console.print(Markdown(self.workspace.read_memory()))
                continue
            if raw == ":undo":
                self.undo_last_task()
                continue
            if raw == ":clear":
                os.system("cls" if os.name == "nt" else "clear")
                continue
            if raw.startswith(":cd "):
                try:
                    target = self.workspace.set_cwd(raw[4:].strip())
                    console.print(f"[green]Diretório atual:[/green] {target}")
                except Exception as exc:
                    console.print(f"[red]{exc}[/red]")
                continue
            self.run_task(raw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--workspace", help="Diretório raiz em que o agente pode operar.")
    parser.add_argument("--task", help="Executa uma única tarefa e encerra.")
    parser.add_argument("--doctor", action="store_true", help="Testa configuração, conexão e modelo.")
    parser.add_argument("--version", action="version", version=APP_VERSION)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = Config.load(args.workspace)
        agent = DeepSeekAgent(config)
        if args.doctor:
            return 0 if agent.doctor() else 1
        if args.task:
            agent.run_task(args.task)
        else:
            agent.repl()
        return 0
    except Exception as exc:
        console.print(Panel.fit(str(exc), title="Falha ao iniciar", border_style="red"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
