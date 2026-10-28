#!/usr/bin/env python3

"""JARVIS: assistente de voz em cima do Ollama local.



Você segura a barra de espaço e fala. O áudio é transcrito pelo Whisper local, enviado ao Ollama local, e a resposta é falada pelo Piper local.



Só usa a biblioteca padrão do Python (3.9+). Rodar:  python3 server.py

"""

import json

import os

import queue

import random

import re

import shlex

import shutil

import signal

import subprocess

import sys

import tempfile

import threading

import time

import unicodedata

import urllib.error

import urllib.request

import uuid

import webbrowser

from concurrent.futures import ThreadPoolExecutor

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pathlib import Path



RAIZ = Path(__file__).resolve().parent

WEB = RAIZ / "web"

ESTADO = RAIZ / ".estado"





# ---------------------------------------------------------------- configuração



def ler_env(caminho):

    valores = {}

    try:

        for linha in Path(caminho).expanduser().read_text(encoding="utf-8").splitlines():

            linha = linha.strip()

            if not linha or linha.startswith("#") or "=" not in linha:

                continue

            k, v = linha.split("=", 1)

            valores[k.strip()] = v.strip().strip('"').strip("'")

    except FileNotFoundError:

        pass

    return valores





def carregar_config():

    # Ordem: variável de ambiente > .env do projeto > ~/.config/vortex-marketing/.env

    base = ler_env("~/.config/vortex-marketing/.env")

    base.update(ler_env(RAIZ / ".env"))

    base.update({k: v for k, v in os.environ.items() if k.startswith(("JARVIS_", "WHISPER_", "PIPER_", "OLLAMA_"))})

    pasta = Path(base.get("JARVIS_PASTA") or Path.home()).expanduser()

    return {

        "pasta": pasta,

        "porta": int(base.get("JARVIS_PORTA", "8790")),

        "modelo": base.get("OLLAMA_MODEL", base.get("JARVIS_MODELO", "llama3.2")),

        "ollama_url": base.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),



        "persona": base.get("JARVIS_PERSONA", "persona.md"),

        "tts": "piper",

        "piper_exe": base.get("PIPER_EXECUTABLE", "piper"),

        "piper_model": base.get("PIPER_MODEL", ""),

        "piper_config": base.get("PIPER_CONFIG", ""),

        "velocidade_voz": 1.0,

        "transcricao": base.get("JARVIS_TRANSCRICAO", "whisper"),

        "whisper_url": base.get("WHISPER_URL", "http://127.0.0.1:8178").rstrip("/"),

        "aquecer": base.get("JARVIS_AQUECER", "1") == "1",

        "aquecer_atalhos": base.get("JARVIS_AQUECER_ATALHOS", ""),

    }





CFG = carregar_config()

PREFS = ESTADO / "preferencias.json"  # o que se escolhe na engrenagem vale mais que o .env





def carregar_vozes():

    return []





def carregar_preferencias():

    try:

        p = json.loads(PREFS.read_text(encoding="utf-8"))

    except (FileNotFoundError, ValueError):

        return







def salvar_preferencias():

    ESTADO.mkdir(exist_ok=True)

    PREFS.write_text("{}", encoding="utf-8")





VOZES = []

carregar_preferencias()

AMOSTRA = "Às suas ordens, Senhor. É assim que eu vou falar com o senhor daqui pra frente."





def persona():

    for nome in (CFG.get("persona") or "persona.md", "persona.md", "persona.example.md"):

        p = RAIZ / nome

        if p.exists():

            return p.read_text(encoding="utf-8")

    return ""





# ------------------------------------------------------------ eventos pra tela



class Eventos:

    """Fila de eventos que a página recebe por Server-Sent Events."""



    def __init__(self):

        self.ouvintes = []

        self.lock = threading.Lock()

        self.ultimo_estado = {"tipo": "estado", "estado": "iniciando"}



    def emitir(self, evento):

        if evento.get("tipo") == "estado":

            self.ultimo_estado = evento

        with self.lock:

            for q in list(self.ouvintes):

                q.put(evento)



    def assinar(self):

        q = queue.Queue()

        with self.lock:

            self.ouvintes.append(q)

        q.put(self.ultimo_estado)

        return q



    def sair(self, q):

        with self.lock:

            if q in self.ouvintes:

                self.ouvintes.remove(q)





EVENTOS = Eventos()





def log(registro):

    ESTADO.mkdir(exist_ok=True)

    registro["quando"] = time.strftime("%Y-%m-%d %H:%M:%S")

    with open(ESTADO / "log.jsonl", "a", encoding="utf-8") as f:

        f.write(json.dumps(registro, ensure_ascii=False) + "\n")





# --------------------------------------------------- rótulos do que ele está usando



NOMES_SERVIDOR = {

    "geracorte": "GeraCorte",

    "geragrupos": "GeraGrupos",

    "panda-video": "Panda Video",

    "claude_ai_Google_Calendar": "Agenda",

    "claude_ai_Gmail": "Gmail",

    "claude_ai_Google_Drive": "Drive",

}





INTERNAS = {"ToolSearch", "TodoWrite", "BashOutput", "KillShell", "ListMcpResourcesTool", "ReadMcpResourceTool"}





def rotulo_ferramenta(nome, entrada):

    entrada = entrada or {}

    if nome in INTERNAS:

        return None

    if nome.startswith("mcp__"):

        servidor = nome.split("__")[1]

        for chave, rot in NOMES_SERVIDOR.items():

            if chave.lower() in servidor.lower():

                return rot

        return servidor.replace("claude_ai_", "").replace("_", " ")

    if nome == "Bash":

        cmd = entrada.get("command", "")

        if re.search(r"zerni&#x6F;**\\.**&#x70;y|redes_resumo|isca_resumo", cmd):

            return "Redes sociais"

        if "supabase" in cmd or "psql" in cmd:

            return "Banco de dados"

        if "Memória" in cmd or "/memory" in cmd:

            return "Memória"

        if "TaskNotes" in cmd:

            return "Kanban"

        if re.match(r"\s*(grep|rg|find|cat|head|tail|ls)\b", cmd):

            return "Lendo o vault"

        return "Terminal"

    caminho = str(entrada.get("file_path") or entrada.get("path") or entrada.get("pattern") or "")

    if nome in ("Write", "Edit", "MultiEdit"):

        if "TaskNotes" in caminho:

            return "Kanban"

        return "Escrevendo nota"

    if nome in ("Read", "Grep", "Glob", "LS"):

        if "Memória" in caminho or "memory" in caminho:

            return "Memória"

        if "TaskNotes" in caminho:

            return "Kanban"

        return "Lendo o vault"

    if nome == "Skill":

        return "Skill " + str(entrada.get("skill", ""))

    if nome in ("WebSearch", "WebFetch"):

        return "Internet"

    if nome in ("Task", "Agent"):

        return "Agente"

    return nome





# ------------------------------------------------------------ sessão Ollama local



# Vai junto de cada fala: o modelo tende a se alongar quando acha muita coisa.

LEMBRETE = ("\n\n[modo voz: pergunta simples (conversa, confirmação, algo que você já sabe) = responda direto, "

            "sem aviso e sem ferramenta. Só se precisar de ferramenta, comece com uma frase curta de aviso (até 10 palavras). "

            "Resposta final em no máximo 2 frases curtas, até 30 palavras. Trate-o por Senhor.]")

ESPERA_AVISO = 0.3  # só fala "um instante" se o trabalho com ferramenta passar disso





class TurnoOllama:

    """Uma pergunta em andamento: recebe o aviso rápido e a resposta final."""



    def __init__(self, ao_avisar=None):

        self.fila = queue.Queue()

        self.entregue = False

        self.avisou = False

        self.ao_avisar = ao_avisar

        self.agendado = False

        self.trava = threading.Lock()

        self.marcas = {"envio": time.time()}  # tempos de cada etapa, pro log



    def marcar(self, nome):

        self.marcas.setdefault(nome, round(time.time() - self.marcas["envio"], 2))



    def entregar(self, resultado):

        if not self.entregue:

            self.entregue = True

            self.fila.put(resultado)



    def avisar(self, frase=None):

        """Fala o aviso rápido uma vez só por pergunta (o primeiro gatilho que chegar)."""

        with self.trava:

            if self.avisou or self.entregue or not self.ao_avisar:

                return

            self.avisou = True

        self.ao_avisar(frase)



    def pedir_aviso(self, frase=None):

        """Ele começou a usar ferramenta: se a resposta não sair em ESPERA_AVISO s, avisa."""

        with self.trava:

            if self.avisou or self.agendado:

                return

            self.agendado = True

        relogio = threading.Timer(ESPERA_AVISO, self.avisar, args=(frase,))

        relogio.daemon = True

        relogio.start()





class SessaoOllama:

    """Chat persistente com um modelo local servido pelo Ollama."""



    def __init__(self):

        self.lock = threading.Lock()

        self.turno = None

        self.livre = threading.Event()

        self.livre.set()

        self.ferramentas_turno = []

        self.historico = []

        self.iniciado = False

        self.cancelado = False



    def iniciar(self):

        url = CFG["ollama_url"] + "/api/tags"

        try:

            req = urllib.request.Request(url)

            with urllib.request.urlopen(req, timeout=4) as r:

                dados = json.loads(r.read().decode("utf-8"))

            nomes = [m.get("name", "") for m in dados.get("models", [])]

            modelo = CFG["modelo"]

            if not any(n == modelo or n.startswith(modelo + ":") for n in nomes):

                raise RuntimeError(f"Ollama está ativo, mas não encontrei o modelo '{modelo}'. Execute: ollama pull {modelo}")

            self.iniciado = True

        except Exception as e:

            self.iniciado = False

            raise RuntimeError(f"Não consegui ligar ao Ollama em {url}. Abra o Ollama e confirme OLLAMA_MODEL. Detalhe: {e}") from e



    def parar(self):

        self.cancelado = True



    def vivo(self):

        return self.iniciado



    def interromper(self):

        if not self.livre.is_set():

            self.cancelado = True

            return True

        return False



    def perguntar(self, texto, limite=300, ao_avisar=None, aviso_apos=None):

        with self.lock:

            if not self.iniciado:

                self.iniciar()

            self.cancelado = False

            turno = TurnoOllama(ao_avisar)

            self.turno = turno

            self.livre.clear()

            system = persona() + ("\n\nÉs o JARVIS, assistente de voz em português. Trata o utilizador por Senhor. "

                                 "Responde de forma concisa, sem afirmar que executaste ações externas. "

                                 "Não tens acesso direto a ficheiros, conectores ou comandos.")

            mensagens = [{"role": "system", "content": system}] + self.historico + [{"role": "user", "content": texto + LEMBRETE}]

            req_data = json.dumps({"model": CFG["modelo"], "messages": mensagens, "stream": False,

                                   "options": {"num_predict": 400}}, ensure_ascii=False).encode("utf-8")

            req = urllib.request.Request(CFG["ollama_url"] + "/api/chat", data=req_data,

                                         headers={"Content-Type": "application/json"}, method="POST")

            try:

                with urllib.request.urlopen(req, timeout=limite) as r:

                    data = json.loads(r.read().decode("utf-8"))

                if self.cancelado:

                    return {"result": "", "interrompido": True}

                resposta = (data.get("message") or {}).get("content", "").strip()

                if not resposta:

                    raise RuntimeError("Ollama devolveu uma resposta vazia.")

                self.historico.extend([{"role": "user", "content": texto}, {"role": "assistant", "content": resposta}])

                # Evita crescimento ilimitado do contexto.

                self.historico = self.historico[-20:]

                turno.marcar("resposta_s")

                return {"result": resposta, "cedo": True}

            except Exception as e:

                return {"is_error": True, "result": f"Erro ao consultar o Ollama: {e}"}

            finally:

                self.livre.set()



    def nova_conversa(self):

        self.historico.clear()

        self.cancelado = False







SESSAO = SessaoOllama()





# ---------------------------------------------------------------- transcrição



def multipart(campos, nome_arquivo, conteudo, mime, campo_arquivo="file"):

    limite = uuid.uuid4().hex

    partes = []

    for k, v in campos.items():

        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())

    partes.append(

        f'--{limite}\r\nContent-Disposition: form-data; name="{campo_arquivo}"; filename="{nome_arquivo}"\r\n'

        f"Content-Type: {mime}\r\n\r\n".encode() + conteudo + b"\r\n"

    )

    partes.append(f"--{limite}--\r\n".encode())

    return b"".join(partes), f"multipart/form-data; boundary={limite}"





def para_wav(audio, mime):

    ext = ".mp4" if "mp4" in mime else ".ogg" if "ogg" in mime else ".webm"

    with tempfile.TemporaryDirectory() as tmp:

        entrada = Path(tmp) / ("fala" + ext)

        saida = Path(tmp) / "fala.wav"

        entrada.write_bytes(audio)

        subprocess.run(

            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(entrada), "-ar", "16000", "-ac", "1", str(saida)],

            check=True,

        )

        return saida.read_bytes()





_modelo_whisper_local = None
_modelo_whisper_lock = threading.Lock()


def transcrever_whisper_externo(wav):
    """Usa o whisper-server externo compatível com o endpoint /inference."""
    corpo, tipo = multipart({"response_format": "json", "temperature": "0.0"}, "fala.wav", wav, "audio/wav")
    req = urllib.request.Request(CFG["whisper_url"] + "/inference", data=corpo, headers={"Content-Type": tipo})
    with urllib.request.urlopen(req, timeout=60) as r:
        resposta = json.loads(r.read().decode("utf-8"))
    return resposta.get("text", "")


def obter_modelo_whisper_local():
    """Carrega faster-whisper apenas uma vez e reutiliza o modelo entre falas."""
    global _modelo_whisper_local
    if _modelo_whisper_local is not None:
        return _modelo_whisper_local
    with _modelo_whisper_lock:
        if _modelo_whisper_local is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as erro:
                raise RuntimeError(
                    "A transcrição local precisa do faster-whisper. Instale com: "
                    "python -m pip install faster-whisper"
                ) from erro
            _modelo_whisper_local = WhisperModel(
                CFG["whisper_modelo"], device="cpu", compute_type="int8"
            )
    return _modelo_whisper_local


def transcrever_whisper_local(wav):
    modelo = obter_modelo_whisper_local()
    with tempfile.TemporaryDirectory() as tmp:
        arquivo = Path(tmp) / "fala.wav"
        arquivo.write_bytes(wav)
        segmentos, _ = modelo.transcribe(
            str(arquivo), language="pt", vad_filter=True,
            beam_size=5, condition_on_previous_text=False
        )
        return " ".join(segmento.text.strip() for segmento in segmentos).strip()





# Frases que o whisper "inventa" quando o áudio é quase só silêncio (vêm de legendas de vídeo).

ALUCINACOES = [

    re.compile(p) for p in (

        r"^legendas? (por|pela|de)\b", r"\bruberti\b", r"\bamara org\b",

        r"\bobrigad[oa] por assistir", r"\binscreva se\b",

    )

]





def transcrever(audio, mime):

    try:
        wav = para_wav(audio, mime)
        modo = CFG.get("transcricao", "faster-whisper")
        if modo in ("faster-whisper", "local", "faster_whisper"):
            texto = transcrever_whisper_local(wav)
        elif modo in ("whisper", "externo", "server"):
            texto = transcrever_whisper_externo(wav)
        else:
            raise RuntimeError(
                f"JARVIS_TRANSCRICAO inválido: {modo}. Use faster-whisper ou whisper."
            )
    except Exception as erro:
        detalhe = str(erro)
        if isinstance(erro, FileNotFoundError):
            detalhe = "Não encontrei o FFmpeg. Verifique se 'ffmpeg -version' funciona no mesmo terminal."
        raise RuntimeError(
            "Falha na transcrição de voz. "
            "O modo padrão usa faster-whisper local; para usá-lo instale: "
            "python -m pip install faster-whisper. "
            f"Detalhe: {detalhe}"
        ) from erro

    texto = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", texto)

    texto = " ".join(texto.split())

    limpo = " ".join(normalizar(texto))

    if len(limpo.replace(" ", "")) < 2 or any(p.search(limpo) for p in ALUCINACOES):

        log({"evento": "transcricao_descartada", "texto": texto})

        return ""

    return texto





# ---------------------------------------------------------------- confirmação



def normalizar(texto):

    sem_acento = unicodedata.normalize("NFD", texto.lower())

    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")

    return re.sub(r"[^a-z0-9 ]", " ", sem_acento).split()





AFIRMACOES = {"sim", "confirma", "confirmo", "confirmado", "pode", "manda", "isso", "ok", "okay",

              "fechado", "vai", "faz", "beleza", "claro", "positivo", "certo", "bora"}





def eh_confirmacao(texto):

    palavras = normalizar(texto)

    return 0 < len(palavras) <= 6 and palavras[0] in AFIRMACOES and "nao" not in palavras





def pediu_confirmacao(resposta):

    return "confirma" in " ".join(normalizar(resposta[-80:]))





# ------------------------------------------------------------------ resposta falada



def limpar_para_fala(texto):

    texto = re.sub(r"**\[**([^**\]**]+)**\]\(**[^)]+**\)**", r"\1", texto)  # [link](url) -> link

    texto = re.sub(r"https?://\S+", "", texto)

    texto = re.sub(r"[*_#`>|]", "", texto)

    texto = re.sub(r"^\s*[-•]\s*", "", texto, flags=re.M)

    texto = " ".join(texto.split())

    return texto[:700]





def voz_disponivel():

    exe = CFG["piper_exe"]

    return bool(CFG["piper_model"] and Path(CFG["piper_model"]).expanduser().is_file()

                and (shutil.which(exe) or Path(exe).expanduser().is_file()))





class Fala:

    """Gera áudio local com Piper e entrega os bytes ao navegador."""



    todas = {}



    def __init__(self, texto, voz=None, velocidade=None):

        self.pedacos = bytearray()

        self.pronto = False

        self.erro = None

        self.cond = threading.Condition()



        threading.Thread(target=self._gerar, args=(texto,), daemon=True).start()



    def esperar(self, limite=60):

        with self.cond:

            fim = time.time() + limite

            while not self.pronto and time.time() < fim:

                self.cond.wait(timeout=1)

        return bytes(self.pedacos) if self.pronto and not self.erro else b""



    def _gerar(self, texto):

        wav = ESTADO / f"piper-{uuid.uuid4().hex}.wav"

        try:

            ESTADO.mkdir(exist_ok=True)

            cmd = [CFG["piper_exe"], "--model", str(Path(CFG["piper_model"]).expanduser()),

                   "--output_file", str(wav)]

            config = CFG["piper_config"]

            if config:

                cmd += ["--config", str(Path(config).expanduser())]

            resultado = subprocess.run(cmd, input=texto, text=True, capture_output=True, timeout=90)

            if resultado.returncode != 0 or not wav.exists():

                raise RuntimeError((resultado.stderr or "Piper não gerou o ficheiro WAV.")[-700:])

            with self.cond:

                self.pedacos.extend(wav.read_bytes())

                self.cond.notify_all()

        except Exception as erro:

            self.erro = repr(erro)

            log({"evento": "piper_falhou", "detalhe": self.erro})

        finally:

            wav.unlink(missing_ok=True)

            with self.cond:

                self.pronto = True

                self.cond.notify_all()



    def ler(self):

        pos = 0

        while True:

            with self.cond:

                while pos >= len(self.pedacos) and not self.pronto:

                    self.cond.wait(timeout=30)

                novo = bytes(self.pedacos[pos:])

                acabou = self.pronto

            pos += len(novo)

            if novo:

                yield novo

            if acabou and pos >= len(self.pedacos):

                return



    fixas = {}



    @classmethod

    def fixa(cls, texto):

        chave = texto

        id_fala = "f" + uuid.uuid5(uuid.NAMESPACE_URL, chave).hex[:11]

        if id_fala not in cls.fixas:

            cls.fixas[id_fala] = cls(texto)

        return id_fala



    @classmethod

    def criar(cls, texto):

        id_fala = uuid.uuid4().hex[:12]

        cls.todas[id_fala] = cls(texto)

        for antigo in list(cls.todas)[:-20]:  # guarda só as últimas 20

            cls.todas.pop(antigo, None)

        return id_fala





# Ditas quando ele vai trabalhar e não escreveu o próprio aviso.

AVISOS_PRONTOS = ["Um instante, Senhor.", "Deixa comigo, Senhor.", "Verificando, Senhor.",

                  "Já volto com isso, Senhor.", "Consultando, Senhor."]





# ---------------------------------------------------------------- pré-busca

# Pergunta que bate com um gatilho de gatilhos.json já sai com o dado buscado:

# o atalho roda enquanto a pergunta vai pro Claude e ele só precisa falar.



def carregar_gatilhos():

    try:

        return json.loads((RAIZ / "gatilhos.json").read_text(encoding="utf-8"))

    except (FileNotFoundError, ValueError):

        return []





GATILHOS = carregar_gatilhos()





def periodo_falado(texto):

    t = " ".join(normalizar(texto))

    if re.search(r"\b(semana|ultimos dias|7 dias|sete dias)\b", t):

        return ["--ultimos", "7"]

    if re.search(r"\b(mes|30 dias|trinta dias)\b", t):

        return ["--ultimos", "30"]

    if re.search(r"\bontem\b", t):

        return ["--dia", "ontem"]

    return ["--dia", "hoje"]





def prebusca(texto):

    palavras = set(normalizar(texto))

    comandos = []

    for g in GATILHOS:

        alvos = {" ".join(normalizar(p)) for p in g.get("palavras", [])}

        if palavras & alvos:

            cmd = [sys.executable, str(RAIZ / "atalhos" / g["comando"][0])] + g["comando"][1:]

            if g.get("periodo"):

                cmd += periodo_falado(texto)

            comandos.append((g["nome"], cmd))

    if not comandos:

        return ""



    def rodar(item):

        nome, cmd = item

        try:

            r = subprocess.run(cmd, cwd=str(RAIZ), capture_output=True, text=True, timeout=8)

            return f"{nome}: {r.stdout.strip()}" if r.returncode == 0 and r.stdout.strip() else ""

        except Exception:

            return ""



    with ThreadPoolExecutor(max_workers=len(comandos)) as grupo:

        linhas = [l for l in grupo.map(rodar, comandos) if l]

    if not linhas:

        return ""

    return ("\n\n[dados que o JARVIS já buscou agora pra esta pergunta. Responda direto com eles, "

            "sem rodar comando e sem aviso. Só rode algo se faltar o que ele pediu:]\n" + "\n".join(linhas))





class Turno:

    ocupado = threading.Lock()

    aguardando_confirmacao = False

    interrompido = False  # você apertou espaço no meio: esse pedido não fala mais nada





def interromper():

    if not Turno.ocupado.locked():

        return False

    Turno.interrompido = True

    SESSAO.interromper()

    log({"evento": "interrompido"})

    return True





def processar(audio=None, mime="audio/webm", texto=None):

    # Se o pedido anterior foi interrompido, ele libera em instantes: espera um pouco.

    if not Turno.ocupado.acquire(timeout=10 if Turno.interrompido else 0.01):

        EVENTOS.emitir({"tipo": "aviso", "texto": "Ainda estou no pedido anterior, Senhor."})

        return

    token = ESTADO / "confirmado"

    inicio = time.time()

    registro = {"evento": "turno"}

    try:

        if audio is not None:

            EVENTOS.emitir({"tipo": "estado", "estado": "transcrevendo"})

            texto = transcrever(audio, mime)

            registro["transcricao_s"] = round(time.time() - inicio, 2)

            if Turno.interrompido:

                registro["interrompido"] = True

                return

        texto = (texto or "").strip()

        if not texto:

            EVENTOS.emitir({"tipo": "aviso", "texto": "Não consegui ouvir. Segure o espaço e fale de novo."})

            EVENTOS.emitir({"tipo": "estado", "estado": "parado"})

            return

        EVENTOS.emitir({"tipo": "voce", "texto": texto})

        registro["voce"] = texto



        # Mantém o estado de confirmação da interface; Ollama não executa ferramentas.

        if Turno.aguardando_confirmacao and eh_confirmacao(texto):

            ESTADO.mkdir(exist_ok=True)

            token.write_text(str(time.time()))

            registro["confirmou"] = True



        EVENTOS.emitir({"tipo": "estado", "estado": "pensando"})

        t_claude = time.time()



        def avisar(frase=None):

            # "Um instante, Senhor." sai na hora, enquanto ele trabalha.

            if Turno.interrompido:

                return

            frase = limpar_para_fala(frase or "")[:160]

            pronta = not frase

            if pronta:

                frase = random.choice(AVISOS_PRONTOS)

            registro["aviso"] = frase + (" (pronta)" if pronta else "")

            registro["aviso_s"] = round(time.time() - inicio, 2)

            if voz_disponivel():

                id_fala = Fala.fixa(frase) if pronta else Fala.criar(frase)

                EVENTOS.emitir({"tipo": "fala", "texto": frase, "audio": f"/api/voz/{id_fala}"})



        t_busca = time.time()

        dados = prebusca(texto)

        if dados:

            registro["prebusca_s"] = round(time.time() - t_busca, 2)

        r = SESSAO.perguntar(texto + dados, ao_avisar=avisar, aviso_apos=2.5 if dados else None)

        registro["ollama_s"] = round(time.time() - t_claude, 2)

        registro["cedo"] = bool(r.get("cedo"))

        if SESSAO.turno:

            registro["etapas"] = {k: v for k, v in SESSAO.turno.marcas.items() if k != "envio"}

        if Turno.interrompido or r.get("interrompido"):

            registro["interrompido"] = True

            return

        registro["ferramentas"] = SESSAO.ferramentas_turno

        resposta = limpar_para_fala(r.get("result") or "") or "Feito."

        if r.get("is_error"):

            registro["erro"] = r.get("result")

        Turno.aguardando_confirmacao = pediu_confirmacao(resposta)



        audio_url = f"/api/voz/{Fala.criar(resposta)}" if voz_disponivel() else ""

        registro["resposta"] = resposta

        registro["total_s"] = round(time.time() - inicio, 2)

        EVENTOS.emitir({

            "tipo": "resposta", "texto": resposta, "audio": audio_url,

            "segundos": registro["total_s"], "confirmar": Turno.aguardando_confirmacao,

        })

        EVENTOS.emitir({"tipo": "estado", "estado": "parado"})

    except Exception as erro:

        registro["erro"] = repr(erro)

        EVENTOS.emitir({"tipo": "aviso", "texto": f"Deu erro: {erro}"})

        EVENTOS.emitir({"tipo": "estado", "estado": "parado"})

    finally:

        if token.exists():

            token.unlink()

        log(registro)

        Turno.interrompido = False

        Turno.ocupado.release()





def config_atual():

    return {"voz": "piper", "velocidade": 1.0, "vozes": []}





def gravar_avisos_prontos():

    """Deixa as frases prontas ("Um instante, Senhor.") gravadas na voz escolhida."""

    if voz_disponivel():

        for frase in AVISOS_PRONTOS:

            Fala.fixa(frase)





def aquecer_atalhos():

    """Roda atalhos lentos quando abre (ex.: o resumo da isca de ontem), pra resposta sair na hora."""

    for comando in filter(None, (c.strip() for c in CFG["aquecer_atalhos"].split(";"))):

        partes = shlex.split(comando)

        try:

            subprocess.run([sys.executable, str(RAIZ / "atalhos" / partes[0])] + partes[1:],

                           cwd=str(RAIZ), capture_output=True, timeout=180)

            log({"evento": "atalho_aquecido", "comando": comando})

        except Exception as erro:

            log({"evento": "atalho_falhou", "comando": comando, "erro": repr(erro)})





def aquecer():

    """Verifica Ollama e prepara atalhos locais."""

    threading.Thread(target=aquecer_atalhos, daemon=True).start()

    EVENTOS.emitir({"tipo": "estado", "estado": "aquecendo"})

    try:

        SESSAO.iniciar()

        gravar_avisos_prontos()

        if CFG["aquecer"]:

            with Turno.ocupado:

                SESSAO.perguntar("(Mensagem automática de início. Não use ferramentas. Responda só: Pronto.)", limite=120)

    finally:

        EVENTOS.emitir({"tipo": "estado", "estado": "parado"})





# ---------------------------------------------------------------------- HTTP



TIPOS = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",

         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}





class Servidor(BaseHTTPRequestHandler):

    def log_message(self, *args):

        pass



    def responder(self, codigo, corpo=b"", tipo="application/json"):

        self.send_response(codigo)

        self.send_header("Content-Type", tipo)

        self.send_header("Content-Length", str(len(corpo)))

        self.send_header("Cache-Control", "no-store")

        self.end_headers()

        self.wfile.write(corpo)



    def do_GET(self):

        caminho = self.path.split("?")[0]

        if caminho == "/api/eventos":

            return self.eventos()

        if caminho == "/api/config":

            return self.responder(200, json.dumps(config_atual(), ensure_ascii=False).encode())

        if caminho.startswith("/api/amostra/"):

            return self.amostra(caminho.rsplit("/", 1)[-1])

        if caminho.startswith("/api/voz/"):

            return self.voz(caminho.rsplit("/", 1)[-1])

        if caminho == "/api/saude":

            corpo = json.dumps({"ollama": SESSAO.vivo(), "pasta": str(CFG["pasta"]), "modelo": CFG["modelo"],

                                "telas": len(EVENTOS.ouvintes)})

            return self.responder(200, corpo.encode())

        arquivo = (WEB / ("index.html" if caminho == "/" else caminho.lstrip("/"))).resolve()

        if WEB.resolve() not in arquivo.parents and arquivo != WEB.resolve() / "index.html":

            return self.responder(404, b"{}")

        if not arquivo.is_file():

            return self.responder(404, b"{}")

        self.responder(200, arquivo.read_bytes(), TIPOS.get(arquivo.suffix, "application/octet-stream"))



    def do_POST(self):

        tamanho = int(self.headers.get("Content-Length", "0"))

        corpo = self.rfile.read(tamanho) if tamanho else b""

        if self.path == "/api/interromper":

            return self.responder(200, json.dumps({"interrompeu": interromper()}).encode())

        if self.path in ("/api/audio", "/api/texto") and Turno.ocupado.locked() and not Turno.interrompido:

            return self.responder(409, b'{"ocupado":true}')  # a tela avisa e espera

        if self.path == "/api/audio":

            if len(corpo) < 2000:  # toque rápido no espaço, sem fala

                return self.responder(200, b'{"ok":false}')

            mime = self.headers.get("Content-Type", "audio/webm")

            threading.Thread(target=processar, kwargs={"audio": corpo, "mime": mime}, daemon=True).start()

            return self.responder(202, b'{"ok":true}')

        if self.path == "/api/texto":

            texto = json.loads(corpo or b"{}").get("texto", "")

            threading.Thread(target=processar, kwargs={"texto": texto}, daemon=True).start()

            return self.responder(202, b'{"ok":true}')

        if self.path == "/api/diag":  # a tela avisa se a voz tocou ou falhou

            try:

                dados = json.loads(corpo or b"{}")

            except ValueError:

                dados = {}

            log({"evento": "tela", "tipo": str(dados.get("evento", ""))[:40],

                 "detalhe": str(dados.get("detalhe", ""))[:300]})

            return self.responder(200, b'{"ok":true}')

        if self.path == "/api/config":

            return self.responder(200, json.dumps(config_atual(), ensure_ascii=False).encode())

        if self.path == "/api/reiniciar":

            SESSAO.nova_conversa()

            Turno.aguardando_confirmacao = False

            threading.Thread(target=aquecer, daemon=True).start()

            return self.responder(200, b'{"ok":true}')

        self.responder(404, b"{}")



    def eventos(self):

        self.send_response(200)

        self.send_header("Content-Type", "text/event-stream")

        self.send_header("Cache-Control", "no-store")

        self.end_headers()

        q = EVENTOS.assinar()

        try:

            while True:

                try:

                    evento = q.get(timeout=15)

                    dados = "data: " + json.dumps(evento, ensure_ascii=False) + "\n\n"

                except queue.Empty:

                    dados = ": ping\n\n"

                self.wfile.write(dados.encode("utf-8"))

                self.wfile.flush()

        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):

            # O navegador pode fechar/recarregar o EventSource; no Windows isto

            # também surge como WinError 10053 e não representa falha do servidor.

            pass

        finally:

            EVENTOS.sair(q)



    def amostra(self, voz):

        return self.responder(404, b"{}")



    def voz(self, id_fala):

        fala = Fala.todas.get(id_fala) or Fala.fixas.get(id_fala)

        if not fala:

            return self.responder(404, b"{}")

        if fala.pronto and fala.erro:

            return self.responder(502, b"{}")

        self.send_response(200)

        self.send_header("Content-Type", "audio/wav")

        self.send_header("Cache-Control", "no-store")

        if fala.pronto:

            self.send_header("Content-Length", str(len(fala.pedacos)))

        self.end_headers()

        try:

            for pedaco in fala.ler():

                self.wfile.write(pedaco)

                self.wfile.flush()

        except (BrokenPipeError, ConnectionResetError):

            pass





def completar_path():

    """Quando abre pelo app (fora do terminal), o PATH vem curto. Procura nos lugares de costume."""

    atual = os.environ.get("PATH", "").split(os.pathsep)

    for pasta in ("~/.npm-global/bin", "~/.local/bin", "~/.claude/local", "/opt/homebrew/bin",

                  "/usr/local/bin", "/opt/homebrew/opt/ffmpeg-full/bin"):

        pasta = os.path.expanduser(pasta)

        if os.path.isdir(pasta) and pasta not in atual:

            atual.append(pasta)

    os.environ["PATH"] = os.pathsep.join(atual)





def main():

    completar_path()

    if not CFG["pasta"].is_dir():

        sys.exit(f"JARVIS_PASTA não existe: {CFG['pasta']}")

    # O Ollama é validado ao aquecer; não é necessário o comando Claude Code.

    if not CFG["ollama_url"].startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):

        sys.exit("Por segurança, OLLAMA_URL deve apontar para um endereço local (localhost/127.0.0.1).")

    if not voz_disponivel():

        print("Aviso: Piper não configurado. Defina PIPER_EXECUTABLE e PIPER_MODEL no .env; respostas ficam em texto.")

    servidor = ThreadingHTTPServer(("127.0.0.1", CFG["porta"]), Servidor)

    servidor.daemon_threads = True

    url = f"http://localhost:{CFG['porta']}"

    print(f"JARVIS no ar em {url}  (pasta: {CFG['pasta']})")



    def encerrar(*_):

        SESSAO.parar()

        os._exit(0)



    signal.signal(signal.SIGTERM, encerrar)

    threading.Thread(target=aquecer, daemon=True).start()

    if "--sem-abrir" not in sys.argv:

        webbrowser.open(url)

    try:

        servidor.serve_forever()

    except KeyboardInterrupt:

        pass

    finally:

        SESSAO.parar()





if __name__ == "__main__":

    main()
