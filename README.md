# JARVIS com Ollama local

Um assistente de voz local que mantém o modelo Ollama como padrão. Pode ser ativado por “Hey Jarvis” (quando o navegador oferece reconhecimento local), duas palmas ou pelo modo original de segurar espaço. Ferramentas explícitas e limitadas podem gerir ficheiros, executar testes confirmados e consultar notícias atuais; o modelo não recebe shell nem ferramentas livres.

```
ativação → transcrição local → Ollama local ou ação explícita permitida → resposta falada → esfera pulsa
```

- **Ouvido:** faster-whisper local por padrão; também aceita um whisper-server local compatível.
- **Cérebro:** um modelo Ollama local com histórico da conversa.
- **Memória:** o ficheiro `persona.md` e o contexto que fornecer na conversa.
- **Mãos:** ações explícitas e permitidas do programa, sempre fora do controlo direto do modelo.
- **Boca:** Piper local quando um executável e modelo válidos estiverem configurados; caso contrário, respostas escritas.
- **Rosto:** só uma esfera de pontos na tela, que reage à sua voz e à dele. Sem texto.
- **Jeito de JARVIS:** te chama de "Senhor", é sério com uma pitada de deboche e, quando precisa buscar alguma coisa, avisa na hora ("Um instante, Senhor.") e volta com a resposta.
- **Segurança:** o modelo não recebe ferramentas. Caminhos ficam confinados à pasta configurada; escrita, execução e controlo do Windows exigem confirmação.

## O que você precisa

- [Ollama](https://ollama.com/download) instalado e em execução, com um modelo descarregado (por exemplo, `ollama pull llama3.2`).
- Python 3.9 ou mais novo. O servidor usa a biblioteca padrão do Python.
- Para voz local: `ffmpeg` e `faster-whisper`, ou um whisper-server local compatível. Piper e um modelo de voz são opcionais.
- Para teclado/rato/janelas no Windows: `python -m pip install pyautogui pygetwindow` (opcional; apenas se for utilizar essas ações).

O núcleo de voz/UI mantém o desenho existente. A automação do Windows é condicional e deve ser testada no Windows real; este repositório foi verificado aqui apenas num sandbox Linux sem microfone nem Ollama.

## Instalar

```bash
git clone <este repositório> jarvis
cd jarvis
cp .env.example .env
cp persona.example.md persona.md
```

1. No `.env`, configure `JARVIS_PASTA` com uma pasta existente e `OLLAMA_MODEL` com um modelo que já esteja instalado. A cópia fornecida contém apenas valores de exemplo, sem segredos.
2. Configure `PIPER_MODEL` somente se o ficheiro de voz realmente existir. Sem Piper, a interface continua a responder por texto.
3. No `persona.md`, mantenha as preferências pessoais; a personalidade padrão é formal, serena, precisa e com humor seco ocasional.

### Whisper local (opcional)

```bash
brew install whisper-cpp ffmpeg
mkdir -p ~/.cache/whisper-models
curl -L -o ~/.cache/whisper-models/ggml-large-v3-turbo.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
whisper-server -m ~/.cache/whisper-models/ggml-large-v3-turbo.bin --host 127.0.0.1 --port 8178 -l pt
```

Instale `faster-whisper` no ambiente Python se for usar o modo padrão (`python -m pip install faster-whisper`). O modo `whisper` usa apenas o servidor configurado localmente em `WHISPER_URL`; não existe fallback silencioso para um serviço remoto.

## Executar pelo VS Code

1. Abra a pasta do projeto em **Ficheiro → Abrir Pasta**.
2. No terminal integrado, confirme que o Ollama está ativo (`ollama list`) e descarregue o modelo configurado, se necessário: `ollama pull llama3.2`.
3. Crie `.env` a partir de `.env.example` e configure `JARVIS_PASTA`, `OLLAMA_MODEL` e, se necessário, o caminho real do Piper/Whisper local.
4. Execute `python server.py` no terminal integrado. Abra `http://localhost:8790` se o navegador não abrir automaticamente.

O Ollama deve estar a escutar em `http://127.0.0.1:11434`. Não é necessário instalar bibliotecas Python adicionais.

## Usar

```bash
python3 server.py
```

Abre no navegador em `http://localhost:8790`. Na primeira vez, libere o microfone.

| Tecla | O que faz |
|---|---|
| Segurar **espaço** | Fala com ele. Apertar de novo enquanto ele fala ou pensa **interrompe** na hora |
| **T** | Digita em vez de falar |
| **N** | Começa uma conversa nova |
| **H** | Mostra ou esconde os detalhes (o que você falou, a resposta escrita, o que ele está usando). Por padrão a tela fica só com a esfera |

Clique na janela não liga o microfone. Para escuta contínua, use **Ativar escuta** e conceda permissão ao microfone; a escuta inicia desligada por privacidade. O botão aciona a detecção de duas palmas e, quando o navegador oferece reconhecimento `pt-BR` realmente local, a palavra “Hey Jarvis”. Se o idioma local não estiver disponível, a interface informa a limitação e as duas palmas continuam a funcionar. A terceira forma de ativação é segurar **espaço**, que mantém o modo original de falar.

### Escuta, tarefas e ferramentas locais

O painel **Tarefas e ferramentas** mostra notícias, estado, resultados e erros. Tarefas longas são executadas fora da thread da interface, podem ser canceladas e deixam um registo em `.estado/tarefas.jsonl`.

- Diga “que horas são?”, “notícias sobre tecnologia”, “abre a calculadora” ou “abre https://exemplo.com”. Notícias usam Google News RSS, apresentam título, fonte, data e ligação e tentam obter um resumo em português com o Ollama local; se o Ollama estiver offline, o excerto original é mantido e a interface indica essa limitação.
- O painel permite listar e ler ficheiros da pasta `JARVIS_PASTA`, criar/editar ficheiros, executar testes Python (usa `pytest` se instalado ou `unittest` padrão) e executar um script Python para diagnóstico. Escritas, execução de código, teclado/rato/janelas e movimentação de ficheiros para a reciclagem requerem uma confirmação explícita.
- Comandos de voz incluem “listar ficheiros”, “ler ficheiro `notas.txt`”, “criar ficheiro `notas.txt` com conteúdo …”, “pressiona controlo S”, “escreve …”, “clica 200, 300”, “minimiza a janela”, “executar os testes” e “depurar script `app.py`”. Os caminhos ficam confinados à pasta configurada; `.env`, credenciais e `.git` são bloqueados. Não existe execução de shell arbitrário. A remoção é reversível: o ficheiro é renomeado com o sufixo `.jarvis-trash-…` após confirmação.
- “Hey Jarvis” depende de suporte do navegador a `SpeechRecognition.processLocally` com idioma português instalado. A função fica desativada por padrão e não recorre silenciosamente ao reconhecimento remoto. Duas palmas iniciam uma gravação de até oito segundos, enviada ao transcritor já configurado (`faster-whisper` local por padrão). A deteção de palmas depende do microfone e do ruído ambiente.
- Para automação de janelas/teclado/rato no Windows, instale os extras opcionais no ambiente Windows: `python -m pip install pyautogui pygetwindow`. A abertura de aplicações usa uma lista permitida e verifica se o executável existe. Estes recursos não podem ser exercitados num ambiente Linux.

### Configurações (a engrenagem)

Mexa o mouse e aparece uma engrenagem no canto de cima. Nela você escolhe a voz do JARVIS (8 vozes em português, 4 masculinas e 4 femininas, com botão pra ouvir cada uma) e a velocidade da fala. Vale a partir da próxima resposta e fica salvo em `.estado/preferencias.json`, que vale mais que o `.env`.

A lista de vozes fica em `vozes.json`: troque ou adicione qualquer voz da Voice Library do ElevenLabs (o ID e uma descrição curta). Algumas vozes da biblioteca só funcionam depois de clicar em **Add to my voices** no site.

## App de Mac (opcional)

Pra abrir o JARVIS como um aplicativo, com ícone próprio, pelo Spotlight ou pelo Dock:

```bash
bash app/montar.sh
```

Ele compila o `JARVIS.app` e instala em Aplicativos. O app liga o servidor sozinho, abre a esfera numa janela própria e desliga tudo quando você fecha (⌘Q). Na primeira vez que você segurar o espaço, o Mac pede permissão pro microfone. **⌘R** recarrega e **⌃⌘F** coloca em tela cheia.

Precisa das ferramentas de desenvolvedor da Apple (`xcode-select --install`) e do Pillow (`pip3 install pillow`) pra desenhar o ícone. O app guarda o caminho desta pasta: se mudar a pasta de lugar, rode o `montar.sh` de novo. O log do servidor fica em `.estado/app.log`.

## Limites e segurança

O modelo Ollama não recebe ferramentas nem pode escolher comandos por conta própria. As ações só acontecem por comandos locais explícitos e passam por uma lista permitida; ações sensíveis exigem confirmação no navegador. A automação não envia mensagens nem publica conteúdo:

- **Passa direto:** consultar data/hora, notícias RSS, listar e ler ficheiros dentro da pasta permitida e abrir aplicações/URLs permitidos.
- **Precisa de confirmação:** criar/editar ou mover ficheiros para a reciclagem; executar scripts/testes; enviar teclas, texto, cliques ou ações em janelas.

O sistema não executa comandos de shell fornecidos por voz ou pelo modelo. Notícias são a única função nova que consulta a Internet; a sumarização usa o Ollama local. O microfone contínuo fica desligado até ser ativado pelo utilizador.



## Atalhos

Pergunta que se repete fica mais rápida com um script que já devolve a resposta pronta, numa linha só. A pasta `atalhos/` tem três exemplos:

- `mercado.py`: dólar, euro, bitcoin, Ibovespa, S&P 500 e Nasdaq com a variação do dia (fontes grátis, sem chave).
- `redes_resumo.py` (precisa da Zernio): Instagram, TikTok ou YouTube num dia (`--dia ontem`), na semana (`--ultimos 7`) ou num período (`--de 2026-10-01 --ate 2026-10-21`): posts, views, alcance, comentários, seguidores, o melhor post e o mais comentado.
- `isca_resumo.py` (precisa da Zernio): quantas pessoas comentaram a palavra e pediram a isca no direct, quantas receberam o link, quantas clicaram e qual isca puxou mais.

Crie os seus e conte no `persona.md` quando usar cada um. Diga também que período se pede numa chamada só: sem isso, o modelo tende a chamar o atalho dia por dia (uma pergunta de 21 dias levou 31 s assim e 3 s numa chamada).

Atalho lento pode rodar quando o JARVIS abre: coloque em `JARVIS_AQUECER_ATALHOS` (ex.: `isca_resumo.py --dia ontem`) e o resultado de ontem fica guardado.

### Pré-busca (gatilhos.json)

Pra pergunta que você já sabe que vai fazer, o JARVIS nem precisa decidir rodar o atalho: em `gatilhos.json`, cada palavra aponta pra um atalho ("dólar", "bolsa" → `mercado.py`; "posts", "comentários" → `redes_resumo.py`, com o período que você falou: hoje, ontem, semana ou mês). O servidor roda o atalho na hora e manda o dado junto com a pergunta, e o Claude só precisa responder. Corta a ida e volta da ferramenta.

## Velocidade

Pergunta simples sai em 1 a 2 segundos. Pergunta que precisa de dado sai em 3 a 6. Se uma busca demorar, ele avisa ("Um instante, Senhor.") e volta com o resultado; pergunta simples não tem aviso. A resposta é falada assim que o texto fica pronto, sem esperar o Claude fechar a rodada. O que mais ajuda:

- **Contexto enxuto (`JARVIS_CONTEXTO=leve`, o padrão).** O modelo recebe apenas a persona com a sua pasta anexada, então não carrega o CLAUDE.md e a memória dela a cada pergunta. Medido: resposta simples em ~1 s, contra 2 a 6 s abrindo direto num vault grande. Use `completo` só com pasta pequena (como uma pasta só pra gravar um vídeo, com um CLAUDE.md curto dizendo o que ele vai responder).
- Atalhos e pré-busca pras perguntas frequentes.
- `JARVIS_MODELO=sonnet` e `JARVIS_ESFORCO=low` (o padrão).
- Bloquear conectores que ele não usa em `JARVIS_BLOQUEAR_FERRAMENTAS`.
- `JARVIS_MODELO_VOZ=eleven_flash_v2_5` começa a falar cerca de 1 segundo antes, com voz um pouco pior.
- `JARVIS_VELOCIDADE_VOZ=1.1` deixa a fala 10% mais rápida (vai de 0.7 a 1.2), ou ajuste na engrenagem.
- `JARVIS_PERSONA` escolhe outro arquivo de persona (ex.: um modo só pra gravar vídeo).

## Custos

- **Ollama, faster-whisper e Piper locais:** usam os recursos do seu computador. O JARVIS não precisa de uma chave de IA externa.
- **Internet:** usada para notícias atuais e pelos atalhos já configurados que explicitamente consultem fontes externas.

## Arquivos

```
server.py            servidor local: microfone, transcrição, Ollama, voz
jarvis_features.py   tarefas, notícias RSS e ferramentas locais limitadas
web/index.html       a tela da esfera
tests/               testes unitários (python -m unittest discover -s tests -v)
.env.example         configuração de exemplo sem credenciais
app/                 o app de Mac (montar.sh compila e instala)
persona.example.md   instruções do assistente (copie pra persona.md)
vozes.json           as vozes que aparecem na engrenagem
gatilhos.json        palavras que já disparam um atalho (pré-busca)
atalhos/             scripts de resposta rápida
.estado/             log de cada pedido, com os tempos (não vai pro git)
```

## Licença

MIT.
