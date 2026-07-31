<div align="center">

# 🤖 DeepSeek Dev Agent

**Agente de desenvolvimento autônomo via linha de comando**

Um agente que entende tarefas em linguagem natural, navega pelo seu projeto,
edita arquivos, executa comandos, valida o resultado e guarda memória — tudo
localmente no seu terminal, com o modelo DeepSeek.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Versão](https://img.shields.io/badge/versão-1.1.0-1F6FEB)
![Plataforma](https://img.shields.io/badge/Windows-11-0078D6?logo=windows&logoColor=white)
![Status](https://img.shields.io/badge/status-estável-2EA043)

</div>

---

## 📑 Índice

- [✨ Destaques](#-destaques)
- [🧰 Funcionalidades](#-funcionalidades)
- [📋 Requisitos](#-requisitos)
- [🚀 Instalação](#-instalação)
- [⚙️ Configuração](#️-configuração)
- [🕹️ Uso](#️-uso)
- [🧩 Como o agente trabalha](#-como-o-agente-trabalha)
- [🛡️ Segurança e controle](#️-segurança-e-controle)
- [🧠 Memória e estado persistente](#-memória-e-estado-persistente)
- [🗂️ Estrutura do projeto](#️-estrutura-do-projeto)
- [🔧 Solução de problemas](#-solução-de-problemas)
- [❓ FAQ](#-faq)
- [📄 Licença](#-licença)

---

## ✨ Destaques

- **Tarefas em linguagem natural** — descreva o que quer e o agente planeja e executa sozinho.
- **12 ferramentas integradas** — ler, escrever, pesquisar, mover, excluir e executar comandos no seu projeto.
- **Modo interativo (REPL)** e **modo de execução única** (`--task`).
- **Memória persistente curta** — o agente lembra do projeto entre sessões (`MEMORY.md`).
- **Backup e desfazer** — toda alteração é protegida e a última tarefa pode ser revertida.
- **Confinamento em workspace** — o agente só opera dentro do diretório permitido.
- **Proteção de segredos** — arquivos como `.env`, chaves e credenciais são bloqueados.
- **Validação automática** — detecta e exige testes/lint/build após alterar código (pytest, ruff, tsc, cargo, etc.).
- **Compactação de contexto** — conversas longas são resumidas para caber no limite configurado.
- **Diagnóstico integrado** — `--doctor` testa chave, conexão e modelo em segundos.

---

## 🧰 Funcionalidades

| Área | O que faz |
| --- | --- |
| 🤖 Raciocínio | Planeja e executa tarefas autônomas com *function calling* e modo *thinking* configurável. |
| 📁 Sistema de arquivos | Lista, lê, escreve (atomicamente), substitui, move, renomeia e exclui arquivos/diretórios. |
| 🔎 Busca | Procura texto ou regex no workspace com filtro por extensão (`*.py`, `**/*.ts`...). |
| 💻 Execução | Roda comandos no terminal com timeout configurável e captura saída + código de retorno. |
| 🌿 Git | Consulta status e diff resumido sem modificar o repositório. |
| ✅ Validação | Após alterar código, o agente é instruído a rodar testes/lint/build antes de concluir. |
| ↩️ Desfazer | `:undo` restaura backups e remove arquivos criados pela última tarefa. |
| 🧠 Memória | Atualiza automaticamente um resumo persistente do projeto a cada tarefa. |
| 📡 Resiliência | *Heartbeat* durante chamadas longas, retry com backoff e erros de API formatados. |
| 🖥️ UX | Terminal colorido com `rich` (painéis, tabelas, markdown) e prompts interativos. |

---

## 📋 Requisitos

- **Windows 11** (testado) — também compatível com outros sistemas que tenham Python 3.
- **Python 3.10 ou superior**.
- **Uma chave de API da DeepSeek** ([console.deepseek.com](https://platform.deepseek.com)).
- Acesso à internet para consultar a API.

---

## 🚀 Instalação

### Opção rápida (Windows, via script)

1. Clone o repositório:

   ```bat
   git clone https://github.com/Joaoalessandro/deepseek_cli.git
   cd deepseek_cli
   ```

2. Execute o iniciador:

   ```bat
   start_agent.bat
   ```

   Na primeira execução ele cria o ambiente virtual (`.venv`), instala as
   dependências e, se ainda não existir, copia `.env.example` para `.env`
   abrindo o arquivo para você preencher a chave.

> 💡 Prefere PowerShell? Use `start_agent.ps1`.

### Opção manual

```bash
# 1. Crie e ative o ambiente virtual
py -3 -m venv .venv
.venv\Scripts\activate

# 2. Instale as dependências
pip install -r requirements.txt

# 3. Configure as variáveis de ambiente
copy .env.example .env
# edite o .env com sua chave

# 4. Execute
python deepseek_dev_agent.py
```

---

## ⚙️ Configuração

Crie um arquivo `.env` na raiz do projeto (use `.env.example` como modelo).
O agente carrega essas variáveis automaticamente.

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | *(obrigatória)* | Sua chave de API da DeepSeek. |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | Endpoint da API (compatível com OpenAI). |
| `DEEPSEEK_MODEL` | `deepseek-v4-pro` | Modelo usado no raciocínio principal. |
| `DEEPSEEK_SUMMARY_MODEL` | `deepseek-v4-flash` | Modelo usado para resumos e compactação de contexto. |
| `DEEPSEEK_THINKING` | `true` | Liga/desliga o modo *thinking* do modelo. |
| `DEEPSEEK_REASONING_EFFORT` | `high` | Esforço de raciocínio (`low`, `medium`, `high`). |
| `AGENT_WORKSPACE` | diretório atual | Raiz em que o agente pode operar. |
| `AGENT_MAX_ROUNDS` | `60` | Máximo de rodadas por tarefa (mín. 5). |
| `AGENT_MAX_CONTEXT_CHARS` | `140000` | Limite de contexto antes de compactar (mín. 40000). |
| `AGENT_MAX_TOOL_OUTPUT_CHARS` | `24000` | Tamanho máximo da saída de ferramentas (mín. 4000). |
| `AGENT_COMMAND_TIMEOUT` | `180` | Timeout (s) para comandos executados (mín. 10). |
| `AGENT_API_TIMEOUT` | `300` | Timeout (s) das chamadas à API (mín. 30). |
| `AGENT_HEARTBEAT_SECONDS` | `10` | Intervalo (s) do aviso enquanto aguarda a API (mín. 5). |
| `AGENT_API_RETRIES` | `2` | Tentativas em caso de erro de API (mín. 1). |

> 🔒 O `.env` **nunca** é rastreado pelo Git (está no `.gitignore`).

---

## 🕹️ Uso

### Modo interativo (REPL)

```bat
start_agent.bat
```

Digite uma tarefa em linguagem natural e o agente executa:

```
> Crie uma API REST em FastAPI com rota /health e um teste unitário
```

**Comandos locais do REPL** (não vão para o modelo):

| Comando | Descrição |
| --- | --- |
| `:help` | Mostra a ajuda. |
| `:status` | Exibe versão, modelo, workspace, diretório atual e memória. |
| `:doctor` | Testa chave, conexão e modelo. |
| `:cd caminho` | Muda o diretório atual dentro do workspace. |
| `:memory` | Mostra a memória persistente curta. |
| `:undo` | Restaura os backups da última tarefa. |
| `:clear` | Limpa a tela. |
| `:exit` / `:quit` / `sair` | Encerra o agente. |

Qualquer outro texto inicia uma tarefa autônoma.

### Execução única

```bat
start_agent.bat --task "Refatore main.py e rode os testes"
```

Executa a tarefa e encerra — útil para automação e CI.

### Diagnóstico

```bat
start_agent.bat --doctor
```

Verifica configuração, autenticação, conexão e se o modelo configurado está
disponível na sua conta. Também existe o atalho `diagnosticar.bat`.

### Linha de comando

| Opção | Descrição |
| --- | --- |
| `--workspace DIR` | Diretório raiz em que o agente pode operar. |
| `--task "..."` | Executa uma única tarefa e encerra. |
| `--doctor` | Testa configuração, conexão e modelo. |
| `--version` | Mostra a versão. |

---

## 🧩 Como o agente trabalha

1. **Recebe a tarefa** — você digita (ou passa via `--task`) em linguagem natural.
2. **Planeja e age** — em um loop, o agente escolhe ferramentas, executa, observa o resultado e decide o próximo passo.
3. **Edita com segurança** — antes de modificar arquivos, ele salva backups em `.deepseek-agent/backups/`.
4. **Valida** — quando código muda, ele é instruído a rodar testes/lint/build e só encerra após validação bem-sucedida.
5. **Conclui** — registra resumo, validação e pendências; atualiza a memória persistente.

### Ferramentas disponíveis ao agente

| Ferramenta | Descrição |
| --- | --- |
| `list_files` | Lista a árvore de arquivos sem enviar o conteúdo. |
| `read_file` | Lê um intervalo de linhas de um arquivo. |
| `search_text` | Busca texto ou regex no workspace. |
| `write_file` | Cria/sobrescreve arquivo atomicamente com backup. |
| `replace_in_file` | Substitui trecho exato em arquivo (com backup). |
| `make_directory` | Cria um diretório. |
| `move_path` | Move ou renomeia arquivo/diretório. |
| `delete_path` | Exclui após confirmação humana, mantendo backup. |
| `set_working_directory` | Muda o diretório de trabalho dentro do workspace. |
| `run_command` | Executa comando no terminal com timeout. |
| `git_status` | Obtém status e diff resumido do Git (sem modificar). |
| `finish_task` | Finaliza a tarefa com resumo, testes e pendências. |

---

## 🛡️ Segurança e controle

- **Confinamento** — qualquer caminho fora do workspace é rejeitado (`PermissionError`).
- **Segredos protegidos** — `.env`, `.env.local`, chaves SSH, `credentials.json`, `service-account.json` etc. são bloqueados.
- **Backups antes de editar** — todo arquivo alterado tem uma cópia em `.deepseek-agent/backups/`.
- **Confirmação humana** — exclusões e operações sensíveis pedem confirmação antes de executar.
- **Desfazer** — `:undo` restaura o estado anterior à última tarefa.
- **Diretórios ignorados** — `.git`, `node_modules`, `venv`, `dist`, caches e afins não entram na varredura.
- **Saída truncada** — respostas de ferramentas longas são cortadas para caber no contexto.
- **`.env` fora do Git** — credenciais nunca são versionadas.

---

## 🧠 Memória e estado persistente

O agente mantém estado em `.deepseek-agent/` **dentro do workspace**:

```
.deepseek-agent/
├── MEMORY.md          # memória persistente curta do projeto
├── last_task.json     # registro da última tarefa (usado pelo :undo)
├── backups/           # cópias de segurança das alterações
└── tasks/             # metadados das tarefas executadas
```

Este diretório é criado automaticamente e ignorado pelo Git.

---

## 🗂️ Estrutura do projeto

```
deepseek_cli/
├── deepseek_dev_agent.py   # agente principal (script único)
├── start_agent.bat         # iniciador para CMD (cria .venv, instala deps, abre .env)
├── start_agent.ps1         # iniciador para PowerShell
├── diagnosticar.bat        # atalho para start_agent.bat --doctor
├── requirements.txt        # dependências (openai, python-dotenv, rich)
├── .env.example            # modelo de configuração
├── .env                    # suas credenciais (ignorado pelo Git)
└── .gitignore
```

---

## 🔧 Solução de problemas

| Problema | Solução |
| --- | --- |
| "DEEPSEEK_API_KEY não configurada" | Copie `.env.example` para `.env` e preencha a chave. |
| Erro de conexão/autenticação | Rode `diagnosticar.bat` (ou `start_agent.bat --doctor`). |
| Modelo não encontrado na conta | O `--doctor` lista os modelos disponíveis; ajuste `DEEPSEEK_MODEL`. |
| Ambiente quebrado | Exclua a pasta `.venv` e execute `start_agent.bat` de novo. |
| Quero reverter a última tarefa | No REPL, use `:undo` (confirmando a restauração). |

---

## ❓ FAQ

**O agente envia meu código para a DeepSeek?**
Sim — trechos de arquivos e saídas de comandos fazem parte do contexto enviado à API para executar a tarefa. Evite rodá-lo em projetos com dados sensíveis, ou configure um workspace isolado.

**Posso usar outro provedor?**
`DEEPSEEK_BASE_URL` aceita qualquer endpoint compatível com a API da OpenAI.

**O que acontece se a conversa ficar longa demais?**
O contexto é compactado automaticamente usando o `DEEPSEEK_SUMMARY_MODEL`, respeitando `AGENT_MAX_CONTEXT_CHARS`.

**O agente pode executar qualquer comando?**
Comandos são executados com timeout e podem ser qualquer comando do terminal — use com cuidado. Exclusões e arquivos sensíveis exigem confirmação.

---

## 📄 Licença

Este repositório ainda não possui um arquivo `LICENSE` definido. Entre em contato
com o mantenedor antes de redistribuir ou incorporar o código em outros projetos.
