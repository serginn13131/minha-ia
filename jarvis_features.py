"""Capacidades locais para o JARVIS: tarefas, notícias e automação segura.

Este módulo não executa comandos arbitrários nem depende de pacotes não instalados.
"""
from __future__ import annotations

import datetime as _dt
import email.utils
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

_DIAS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")


def local_time() -> dict[str, str]:
    now = _dt.datetime.now().astimezone()
    return {"hora": now.strftime("%H:%M"), "data": now.strftime("%d/%m/%Y"), "dia": _DIAS[now.weekday()], "fuso": now.tzname() or "local"}


class TaskManager:
    """Executor limitado com estados consultáveis, logs e cancelamento cooperativo."""
    def __init__(self, log_dir: Path, on_change: Callable[[dict], None] | None = None, workers: int = 4):
        self.log_dir = Path(log_dir)
        self.on_change = on_change
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="jarvis-task")
        self.lock = threading.RLock()
        self.tasks: dict[str, dict[str, Any]] = {}
        self.cancel_events: dict[str, threading.Event] = {}

    def _save(self, item: dict) -> None:
        marca = f"{item.get('status', 'desconhecido')} · {time.strftime('%H:%M:%S')}"
        logs = item.setdefault("logs", [])
        if not logs or logs[-1].split(" · ", 1)[0] != item.get("status"):
            item["logs"] = (logs[-19:] + [marca])
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with (self.log_dir / "tarefas.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(self.public(item), ensure_ascii=False) + "\n")
        if self.on_change:
            try:
                self.on_change({"tipo": "tarefa", "tarefa": self.public(item)})
            except Exception:
                pass

    @staticmethod
    def public(item: dict) -> dict:
        return {k: item.get(k) for k in ("id", "nome", "status", "criada_em", "iniciada_em", "terminada_em", "resultado", "erro", "logs")}

    def submit(self, name: str, fn: Callable[[threading.Event], Any]) -> str:
        task_id = uuid.uuid4().hex[:12]
        cancel_event = threading.Event()
        item = {"id": task_id, "nome": str(name)[:100], "status": "na_fila", "criada_em": time.time(), "logs": []}
        with self.lock:
            self.tasks[task_id] = item
            self.cancel_events[task_id] = cancel_event
        self._save(item.copy())
        future = self.executor.submit(self._run, task_id, fn)
        with self.lock:
            item["future"] = future
        return task_id

    def _run(self, task_id: str, fn: Callable[[threading.Event], Any]) -> None:
        with self.lock:
            item = self.tasks[task_id]
            event = self.cancel_events[task_id]
            if event.is_set():
                item.update(status="cancelada", terminada_em=time.time())
                self._save(item.copy())
                return
            item.update(status="executando", iniciada_em=time.time())
            self._save(item.copy())
        try:
            result = fn(event)
            with self.lock:
                item["status"] = "cancelada" if event.is_set() else "concluida"
                if result is not None:
                    item["resultado"] = str(result)[:12000]
        except Exception as exc:
            with self.lock:
                if event.is_set():
                    item.update(status="cancelada", erro=None)
                else:
                    item.update(status="falhou", erro=f"{type(exc).__name__}: {exc}"[:2000])
        finally:
            with self.lock:
                item["terminada_em"] = time.time()
                self._save(item.copy())

    def add_log(self, task_id: str, message: str) -> None:
        with self.lock:
            item = self.tasks.get(task_id)
            if not item:
                return
            item["logs"] = (item.get("logs") or [])[-49:] + [str(message)[:1000]]
            self._save(item.copy())

    def cancel(self, task_id: str) -> bool:
        with self.lock:
            item = self.tasks.get(task_id)
            if not item or item["status"] not in ("na_fila", "executando"):
                return False
            self.cancel_events[task_id].set()
            future = item.get("future")
            if future and future.cancel():
                item.update(status="cancelada", terminada_em=time.time())
            self._save(item.copy())
            return True

    def list(self) -> list[dict]:
        with self.lock:
            return [self.public(t) for t in sorted(self.tasks.values(), key=lambda x: x["criada_em"], reverse=True)[:50]]

    def get(self, task_id: str) -> dict | None:
        with self.lock:
            item = self.tasks.get(task_id)
            return self.public(item) if item else None

    def shutdown(self) -> None:
        with self.lock:
            for event in self.cancel_events.values():
                event.set()
        self.executor.shutdown(wait=False, cancel_futures=True)


def fetch_news(topic: str = "", cancel: threading.Event | None = None, limit: int = 5) -> list[dict]:
    """Consulta feed público do Google News RSS; limita saída e respeita cancelamento."""
    query = topic.strip() or "principais notícias"
    params = urllib.parse.urlencode({"q": query, "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"})
    url = "https://news.google.com/rss/search?" + params
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-local/1.0 (RSS reader)"})
    with urllib.request.urlopen(req, timeout=12) as response:
        data = response.read(1_500_000)
    if cancel and cancel.is_set():
        return []
    root = ET.fromstring(data)
    entries: list[dict] = []
    for node in root.findall("./channel/item")[:max(1, min(limit, 10))]:
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        raw_date = (node.findtext("pubDate") or "").strip()
        description = re.sub(r"<[^>]+>", " ", node.findtext("description") or "")
        description = " ".join(urllib.parse.unquote(description).split())
        source = (node.findtext("source") or "Google News").strip()
        try:
            published = email.utils.parsedate_to_datetime(raw_date).astimezone().isoformat(timespec="minutes")
        except (TypeError, ValueError, OverflowError):
            published = raw_date
        # O RSS de agregação nem sempre inclui texto do artigo. Não inventamos um resumo:
        # quando só há título, este é apresentado explicitamente como resumo editorial curto.
        summary = description[:600] if description and description.casefold() not in title.casefold() else title
        entries.append({"titulo": title, "fonte": source, "data": published, "link": link, "resumo": summary})
    return entries


class LocalTools:
    """Ações explícitas, confinadas ao diretório de trabalho e sem shell arbitrário."""
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.pending: dict[str, tuple[float, dict]] = {}
        self.lock = threading.Lock()

    def _path(self, raw: str) -> Path:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = self.root / p
        resolved = p.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError("O caminho tem de ficar dentro da pasta de trabalho configurada.")
        partes = {parte.casefold() for parte in resolved.relative_to(self.root).parts}
        if any(self._restricted_name(parte) for parte in partes):
            raise ValueError("Por segurança, este caminho parece conter credenciais ou metadados Git e não pode ser acedido.")
        return resolved

    @staticmethod
    def _restricted_name(name: str) -> bool:
        value = name.casefold()
        return (value in {".git", ".secrets", ".ssh", ".aws", ".azure", ".gnupg", "id_rsa", "id_ed25519", "credentials.json", "secrets.json"}
                or value == ".env" or value.startswith(".env.") or value.startswith("credentials.")
                or value.endswith((".pem", ".key")))

    @staticmethod
    def _is_windows() -> bool:
        return platform.system() == "Windows"

    def prepare(self, action: str, params: dict) -> dict:
        action = str(action).strip().lower()
        params = params if isinstance(params, dict) else {}
        if action in ("time", "data"):
            return {"ok": True, "resultado": local_time()}
        if action == "news":
            return {"ok": True, "task": True, "parametros": {"topic": str(params.get("topic", ""))[:160]}}
        if action == "list_files":
            folder = self._path(str(params.get("path", ".")))
            if not folder.is_dir():
                raise ValueError("A pasta não existe.")
            return {"ok": True, "resultado": [{"nome": p.name, "tipo": "pasta" if p.is_dir() else "ficheiro"} for p in sorted(folder.iterdir()) if not self._restricted_name(p.name)][:200]}
        if action == "read_file":
            file = self._path(str(params.get("path", "")))
            if not file.is_file() or file.stat().st_size > 1_000_000:
                raise ValueError("Ficheiro inexistente ou demasiado grande (limite: 1 MB).")
            return {"ok": True, "resultado": file.read_text(encoding="utf-8")[:20000]}
        if action == "delete_file":
            file = self._path(str(params.get("path", "")))
            if not file.is_file():
                raise ValueError("O ficheiro não existe ou não é um ficheiro normal.")
            return self._confirmation(action, {"path": str(file)}, f"Mover {file} para a reciclagem reversível do JARVIS")
        if action in ("write_file", "edit_file"):
            file = self._path(str(params.get("path", "")))
            content = str(params.get("content", ""))
            if len(content) > 200_000:
                raise ValueError("O conteúdo excede o limite de 200 KB.")
            if action == "edit_file" and not file.is_file():
                raise ValueError("O ficheiro a editar não existe.")
            if file.exists() and file.is_dir():
                raise ValueError("O caminho aponta para uma pasta.")
            return self._confirmation(action, {"path": str(file), "content": content}, f"{action}: {file} ({len(content)} caracteres)")
        if action == "run_tests":
            target = self._path(str(params.get("path", ".")))
            if not target.exists():
                raise ValueError("O caminho indicado não existe.")
            return self._confirmation(action, {"path": str(target)}, f"Executar testes Python em {target} (limite de 120 s)")
        if action == "run_python":
            file = self._path(str(params.get("path", "")))
            if not file.is_file() or file.suffix.lower() != ".py":
                raise ValueError("Indica um ficheiro .py existente dentro da pasta de trabalho.")
            return self._confirmation(action, {"path": str(file)}, f"Executar script Python {file} (limite de 60 s)")
        if action == "open_app":
            app = str(params.get("name", "")).strip().lower()
            allowed = {"notepad": "notepad.exe", "calculadora": "calc.exe", "bloco de notas": "notepad.exe", "explorer": "explorer.exe", "explorador": "explorer.exe", "edge": "msedge.exe", "chrome": "chrome.exe", "firefox": "firefox.exe"}
            if not self._is_windows():
                raise RuntimeError("A abertura de aplicações Windows só funciona quando executado no Windows.")
            if app not in allowed:
                raise ValueError("Aplicação não permitida. Opções: Notepad, Calculadora, Explorer, Edge, Chrome, Firefox.")
            executable = allowed[app]
            located = shutil.which(executable)
            if not located:
                roots = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"]
                for env_name, suffix in (("PROGRAMFILES", ""), ("PROGRAMFILES(X86)", ""), ("LOCALAPPDATA", "")):
                    if os.environ.get(env_name):
                        base = Path(os.environ[env_name])
                        roots.extend((base, base / "Microsoft" / "Edge" / "Application", base / "Google" / "Chrome" / "Application", base / "Mozilla Firefox"))
                located = next((str(root / executable) for root in roots if (root / executable).is_file()), None)
            if not located:
                raise FileNotFoundError(f"Não encontrei {executable} instalado. Não foi iniciada nenhuma aplicação.")
            return {"ok": True, "task": True, "exec": [located]}
        if action == "open_url":
            url = str(params.get("url", "")).strip()
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("Só são permitidos endereços HTTP/HTTPS válidos.")
            return {"ok": True, "task": True, "exec": ["browser", url]}
        if action in ("press_key", "type_text", "mouse_click", "window_action"):
            if not self._is_windows():
                raise RuntimeError("A automação de teclado, rato e janelas só está disponível no Windows.")
            if action == "press_key":
                key = str(params.get("key", "")).lower()
                keys = {"enter", "esc", "escape", "tab", "space", "backspace", "delete", "up", "down", "left", "right", "ctrl+c", "ctrl+v", "ctrl+s", "ctrl+z", "alt+tab", "win"}
                if key not in keys:
                    raise ValueError("Tecla ou combinação não permitida.")
                return self._confirmation(action, {"key": key}, f"Premir a tecla {key}")
            if action == "type_text":
                text = str(params.get("text", ""))
                if len(text) > 2000:
                    raise ValueError("Texto demasiado longo.")
                return self._confirmation(action, {"text": text}, f"Escrever texto ({len(text)} caracteres) na janela ativa")
            if action == "mouse_click":
                x, y = int(params.get("x", -1)), int(params.get("y", -1))
                if x < 0 or y < 0 or x > 10000 or y > 10000:
                    raise ValueError("Coordenadas fora dos limites permitidos.")
                return self._confirmation(action, {"x": x, "y": y}, f"Clicar na posição ({x}, {y})")
            op = str(params.get("operation", "")).lower()
            if op not in ("minimize", "maximize", "restore", "close"):
                raise ValueError("Operação de janela permitida: minimize, maximize, restore, close.")
            return self._confirmation(action, {"operation": op}, f"Ação {op} na janela ativa")
        raise ValueError("Ação desconhecida ou não permitida.")

    def _confirmation(self, action: str, params: dict, description: str) -> dict:
        token = uuid.uuid4().hex
        with self.lock:
            self.pending[token] = (time.time() + 120, {"action": action, "params": params})
        return {"ok": True, "confirmation_required": True, "token": token, "description": description}

    def confirm(self, token: str, cancel_event: threading.Event | None = None) -> dict | None:
        with self.lock:
            item = self.pending.pop(token, None)
        if not item or item[0] < time.time():
            raise ValueError("Confirmação inexistente ou expirada.")
        action, params = item[1]["action"], item[1]["params"]
        if action == "delete_file":
            path = self._path(params["path"])
            destino = path.with_name(path.name + ".jarvis-trash-" + time.strftime("%Y%m%d-%H%M%S"))
            path.rename(destino)
            return {"result": f"Ficheiro movido para reciclagem reversível: {destino}"}
        if action in ("write_file", "edit_file"):
            path = self._path(params["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                backup = path.with_name(path.name + ".jarvis-backup-" + time.strftime("%Y%m%d-%H%M%S"))
                shutil.copy2(path, backup)
            path.write_text(params["content"], encoding="utf-8")
            return {"result": f"Ficheiro guardado: {path}"}
        if action in ("run_tests", "run_python"):
            path = self._path(params["path"])
            if action == "run_python":
                command = [os.sys.executable, str(path)]
                timeout = 60
            else:
                if importlib.util.find_spec("pytest"):
                    command = [os.sys.executable, "-m", "pytest", str(path)]
                else:
                    test_root = path if path.is_dir() else path.parent
                    command = [os.sys.executable, "-m", "unittest", "discover", "-s", str(test_root)]
                    if path.is_file():
                        command.extend(["-p", path.name])
                timeout = 120
            process = subprocess.Popen(command, cwd=str(self.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=False)
            deadline = time.monotonic() + timeout
            while True:
                restante = deadline - time.monotonic()
                try:
                    stdout, stderr = process.communicate(timeout=max(0.05, min(0.25, restante)))
                    break
                except subprocess.TimeoutExpired:
                    if cancel_event and cancel_event.is_set():
                        process.terminate()
                        try:
                            stdout, stderr = process.communicate(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            stdout, stderr = process.communicate()
                        return {"result": "Execução cancelada pelo utilizador.", "returncode": process.returncode}
                    if restante <= 0:
                        process.kill()
                        stdout, stderr = process.communicate()
                        raise TimeoutError(f"O comando excedeu o limite de {timeout} segundos.")
            output = (stdout + "\n" + stderr).strip()[-10000:]
            return {"result": output or f"Processo terminou com código {process.returncode}.", "returncode": process.returncode}
        try:
            import pyautogui  # opcional; não importar fora do Windows
        except ImportError as exc:
            raise RuntimeError("Instale pyautogui e as dependências Windows para ativar esta ação.") from exc
        if action == "press_key":
            keys = params["key"].split("+")
            pyautogui.hotkey(*keys) if len(keys) > 1 else pyautogui.press(keys[0])
            return {"result": "Tecla enviada."}
        if action == "type_text":
            pyautogui.write(params["text"], interval=0.01)
            return {"result": "Texto enviado à janela ativa."}
        if action == "mouse_click":
            pyautogui.click(params["x"], params["y"])
            return {"result": "Clique executado."}
        if action == "window_action":
            try:
                import pygetwindow
                active = pygetwindow.getActiveWindow()
                if active is None:
                    raise RuntimeError("Não há janela ativa.")
                {"minimize": active.minimize, "maximize": active.maximize, "restore": active.restore, "close": active.close}[params["operation"]]()
            except ImportError as exc:
                raise RuntimeError("Instale pygetwindow para gerir janelas.") from exc
            return {"result": "Ação de janela executada."}
        raise ValueError("Ação não implementada.")


def spoken_action(text: str) -> tuple[str, dict] | None:
    """Interpretador determinístico de comandos de utilidade, sem LLM/executar shell."""
    norm = " ".join(text.casefold().split())
    if re.search(r"\b(que horas|hora agora|horas são|data de hoje|que dia é hoje|dia da semana)\b", norm):
        return "time", {}
    m = re.search(r"\bnot[ií]cias(?: sobre| de| do| da)?\s*(.*)$", norm)
    if m:
        return "news", {"topic": m.group(1).strip()}
    m = re.search(r"\b(?:abrir|abra|abre)\s+(notepad|bloco de notas|calculadora|explorer|explorador|edge|chrome|firefox)\b", norm)
    if m:
        return "open_app", {"name": m.group(1)}
    m = re.search(r"\b(?:abrir|abra|abre)\s+(https?://\S+)", norm)
    if m:
        return "open_url", {"url": m.group(1)}
    if re.search(r"\b(?:listar|mostra|mostrar) (?:os )?ficheiros\b", norm):
        return "list_files", {"path": "."}
    m = re.search(r"\b(?:ler|l[eê]|abrir) (?:o )?ficheiro\s+(.+)$", text, re.IGNORECASE)
    if m:
        return "read_file", {"path": m.group(1).strip().strip('"')}
    m = re.search(r"\b(?:criar|escrever|guardar) (?:o )?ficheiro\s+(.+?)\s+(?:com conte[uú]do|conte[uú]do)\s*:?\s*(.+)$", text, re.IGNORECASE)
    if m:
        return "write_file", {"path": m.group(1).strip().strip('"'), "content": m.group(2).strip()}
    m = re.search(r"\beditar (?:o )?ficheiro\s+(.+?)\s+(?:com conte[uú]do|conte[uú]do)\s*:?\s*(.+)$", text, re.IGNORECASE)
    if m:
        return "edit_file", {"path": m.group(1).strip().strip('"'), "content": m.group(2).strip()}
    m = re.search(r"\b(?:apagar|eliminar|remover) (?:o )?ficheiro\s+(.+)$", text, re.IGNORECASE)
    if m:
        return "delete_file", {"path": m.group(1).strip().strip('"')}
    if re.search(r"\b(?:executar|correr|rodar) (?:os )?testes\b", norm):
        return "run_tests", {"path": "."}
    m = re.search(r"\b(?:executar|correr|depurar) (?:o )?(?:script|ficheiro python)\s+(.+)$", text, re.IGNORECASE)
    if m:
        return "run_python", {"path": m.group(1).strip().strip('"')}
    m = re.search(r"\b(?:escreve|digita|escrever)\s+(.+)$", text, re.IGNORECASE)
    if m:
        return "type_text", {"text": m.group(1).strip()}
    m = re.search(r"\b(?:pressiona|pressionar|carrega na tecla|tecla)\s+(.+)$", norm)
    if m:
        tecla = m.group(1).strip().replace("controlo", "ctrl").replace("control", "ctrl").replace(" ", "+")
        aliases = {"retorno": "enter", "escapar": "escape", "controlo+c": "ctrl+c", "controlo+v": "ctrl+v", "controlo+s": "ctrl+s"}
        return "press_key", {"key": aliases.get(tecla, tecla)}
    m = re.search(r"\b(?:clica|clique|clicar)\s+(?:em )?(\d{1,5})\s*[, ]\s*(\d{1,5})\b", norm)
    if m:
        return "mouse_click", {"x": int(m.group(1)), "y": int(m.group(2))}
    m = re.search(r"\b(minimiza|minimizar|maximiza|maximizar|restaura|restaurar|fecha|fechar) (?:a )?janela\b", norm)
    if m:
        op = m.group(1)
        return "window_action", {"operation": "minimize" if op.startswith("minim") else "maximize" if op.startswith("maxim") else "restore" if op.startswith("restaur") else "close"}
    return None
