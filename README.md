# Shorts Video Pipeline — Guia Completo de Instalação e Uso

Pipeline 100% local para gerar vídeos curtos 9:16 de finanças com n8n 2.9.4, PostgreSQL 16, FastAPI + FFmpeg e IA local (LLM, TTS, ASR). Suporta postagem automática no **YouTube**, **Instagram** e **TikTok**.

---

## Índice

- [Visão geral da arquitetura](#visão-geral-da-arquitetura)
- [PARTE 1 — Pré-requisitos](#parte-1--pré-requisitos)
- **[PARTE 11 — Postagem automática (YouTube, Instagram, TikTok)](#parte-11--postagem-automática)**
- [PARTE 2 — Serviços de IA local](#parte-2--serviços-de-ia-local)
- [PARTE 3 — Configurar o projeto](#parte-3--configurar-o-projeto)
- [PARTE 4 — Subir os containers](#parte-4--subir-os-containers)
- [PARTE 5 — Banco de dados](#parte-5--banco-de-dados)
- [PARTE 6 — Assets de vídeo e música](#parte-6--assets-de-vídeo-e-música)
- [PARTE 6.1 — Renomear assets com IA](#parte-61--renomear-assets-com-ia)
- [PARTE 7 — Configurar o n8n](#parte-7--configurar-o-n8n)
- [PARTE 8 — Criar e executar o primeiro job](#parte-8--criar-e-executar-o-primeiro-job)
- [PARTE 9 — Ver os resultados](#parte-9--ver-os-resultados)
- [PARTE 10 — Operação diária](#parte-10--operação-diária)
- [Referência rápida de comandos](#referência-rápida-de-comandos)
- [Solução de problemas](#solução-de-problemas)

---

## Visão geral da arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│  Host (sua máquina)                                             │
│                                                                 │
│  ┌──────────────┐  claim job   ┌────────────────────────────┐  │
│  │  n8n 2.9.4   │─────────────▶│  PostgreSQL 16             │  │
│  │  :5678       │              │  video_jobs  job_steps     │  │
│  └──────┬───────┘              │  job_logs    assets        │  │
│         │ POST /run/{id}       └────────────────────────────┘  │
│         ▼                                  ▲                   │
│  ┌──────────────┐  escreve status ─────────┘                   │
│  │  FastAPI     │                                              │
│  │  Worker :8000│──▶ ./data/videos/YYYY-MM-DD/<job-id>/        │
│  │  + FFmpeg    │       final.mp4  voice.wav  captions.srt     │
│  └──────┬───────┘       post_pack.txt  script.json             │
│         │                                                       │
│  ┌──────┴──────────────────────────────────────┐               │
│  │  IA local (no host, acessada via Docker)    │               │
│  │  LLM : Ollama  :11434  → gera roteiro       │               │
│  │  TTS : Kokoro  :8880   → gera voz (WAV)     │               │
│  │  ASR : Whisper :9000   → gera legenda (SRT) │               │
│  └─────────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────────┘
```

**Fluxo resumido:**
1. n8n (cron a cada 5 min) busca um job `PENDING` no Postgres
2. n8n chama `POST http://video_worker:8000/run/{job_id}`
3. O worker Python executa 8 steps em sequência (idempotentes)
4. Resultado: `final.mp4` + `post_pack.txt` prontos para postar manualmente

---

## PARTE 1 — Pré-requisitos

### 1.1 Docker e Docker Compose

Instale o **Docker Desktop** (inclui Docker Compose v2):

- **Mac:** https://www.docker.com/products/docker-desktop/
- **Linux:** `curl -fsSL https://get.docker.com | sh` e depois instale o plugin compose

Verifique as versões após instalar:

```bash
docker --version          # precisa ser 24+
docker compose version    # precisa ser v2.x
```

> Se o comando for `docker-compose` (com hífen) é a versão v1 antiga. Use sempre `docker compose` (sem hífen) para v2.

---

### 1.2 Verificar portas disponíveis

O projeto usa as portas abaixo. Confirme que estão livres:

```bash
# Mac / Linux — verificar se as portas estão em uso
lsof -i :5432   # PostgreSQL
lsof -i :5678   # n8n
lsof -i :8000   # worker FastAPI
lsof -i :11434  # Ollama (LLM)
```

Se alguma aparecer ocupada, pare o serviço conflitante antes de continuar.

---

## PARTE 2 — Serviços de IA local

O pipeline precisa de 3 serviços de IA. Todos rodam no **host** (não no Docker), e os containers os acessam via `host.docker.internal`.

### 2.1 LLM — Ollama (geração do roteiro)

**Instalar Ollama:**

```bash
# Mac
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh
```

**Baixar o modelo (escolha um):**

```bash
ollama pull llama3          # recomendado — bom balanço qualidade/velocidade (~4.7 GB)
ollama pull mistral         # alternativa leve (~4.1 GB)
ollama pull phi3            # mais leve (~2.3 GB), qualidade menor
```

**Subir o servidor Ollama:**

```bash
ollama serve
# Fica rodando em http://localhost:11434
```

**Testar se funciona:**

```bash
curl http://localhost:11434/api/generate \
  -d '{"model":"llama3","prompt":"Say hello","stream":false}'
# Deve retornar JSON com campo "response"
```

> O Ollama inicia automaticamente no Mac após instalar. Se já estiver rodando, `ollama serve` dará erro "already running" — pode ignorar.

---

### 2.2 TTS — Kokoro (geração da voz)

Kokoro é uma TTS local de alta qualidade com API REST nativa.

**Instalar via Docker (forma mais fácil):**

```bash
docker run -d \
  --name kokoro_tts \
  -p 8880:8880 \
  ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.2
```

> Se tiver GPU NVIDIA: use a tag `:v0.2.2` (cpu já funciona bem para textos curtos de 30s).

**Testar:**

```bash
curl -X POST http://localhost:8880/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"kokoro","input":"Hello, this is a test.","voice":"af_heart"}' \
  --output test.wav

# Deve gerar um arquivo test.wav
```

**Configurar no .env:**

```env
TTS_ENDPOINT=http://host.docker.internal:8880/v1/audio/speech
TTS_VOICE=af_heart
```

> **Alternativa simples:** Se não quiser instalar TTS agora, o sistema gera o vídeo mesmo sem voz (usando o texto como placeholder). Configure `TTS_ENDPOINT=http://host.docker.internal:8880/v1/audio/speech` e o pipeline fará fallback graciosamente.

---

### 2.3 ASR — Whisper (geração das legendas) — OPCIONAL

O ASR é **completamente opcional**. Se não estiver disponível, o sistema gera as legendas automaticamente a partir do texto do roteiro com timing estimado.

**Se quiser instalar (mais precisão):**

```bash
docker run -d \
  --name whisper_asr \
  -p 9000:9000 \
  onerahmet/openai-whisper-asr-webservice:latest-cpu \
  -e ASR_MODEL=base.en
```

**Testar:**

```bash
curl http://localhost:9000/docs   # Swagger UI do Whisper
```

> Se pular esta etapa, o pipeline funciona normalmente com legendas estimadas.

---

## PARTE 3 — Configurar o projeto

### 3.1 Entrar na pasta do projeto

```bash
cd /Users/rafaelkleimpaul/Desktop/Projetos/Automacao_Shorts
```

### 3.2 Criar o arquivo `.env`

```bash
cp .env.example .env
```

### 3.3 Editar o `.env`

Abra o arquivo `.env` e ajuste os valores:

```bash
# Mac
open -e .env

# Ou use qualquer editor
nano .env
```

**Campos obrigatórios de alterar:**

```env
# Gere uma chave aleatória de 32+ caracteres para o n8n
# Rode no terminal para gerar uma:
#   openssl rand -hex 24
N8N_ENCRYPTION_KEY=cole_aqui_sua_chave_gerada_de_32_chars
```

**Gerar a chave (rode no terminal):**

```bash
openssl rand -hex 24
# Exemplo de saída: a3f7c2e91b4d8052f6a0e3c7b1d4f9e2a5c8b0d3e6f1a4
```

Cole o resultado no `N8N_ENCRYPTION_KEY`.

**Verificar os endpoints dos serviços de IA:**

```env
# LLM (Ollama padrão — provavelmente não precisa mudar)
LLM_ENDPOINT=http://host.docker.internal:11434/api/generate
LLM_MODEL=llama3        # ou mistral, phi3 — o que você baixou

# TTS (Kokoro)
TTS_ENDPOINT=http://host.docker.internal:8880/v1/audio/speech
TTS_VOICE=af_heart

# ASR (Whisper — deixe como está se não instalou)
ASR_ENDPOINT=http://host.docker.internal:9000/transcribe
```

**`.env` final mínimo funcional:**

```env
POSTGRES_USER=shorts
POSTGRES_PASSWORD=shorts_secret
POSTGRES_DB=shorts_db

N8N_ENCRYPTION_KEY=a3f7c2e91b4d8052f6a0e3c7b1d4f9e2a5c8b0d3e6f1  # ← SUA CHAVE

TIMEZONE=America/Sao_Paulo

DEFAULT_LANGUAGE=en_US
DEFAULT_NICHE=finance
DEFAULT_STYLE=commentary
DEFAULT_DURATION=30

LLM_ENDPOINT=http://host.docker.internal:11434/api/generate
LLM_MODEL=llama3

TTS_ENDPOINT=http://host.docker.internal:8880/v1/audio/speech
TTS_VOICE=af_heart
TTS_SPEED=1.0

ASR_ENDPOINT=http://host.docker.internal:9000/transcribe

LOG_LEVEL=INFO
MAX_RETRIES=3
```

---

## PARTE 4 — Subir os containers

### 4.1 Build e start

Na pasta raiz do projeto:

```bash
docker compose up -d --build
```

O `--build` compila a imagem do worker Python (demora ~2–3 min na primeira vez).

### 4.2 Verificar se tudo subiu

```bash
docker compose ps
```

Resultado esperado:

```
NAME               IMAGE                  STATUS
shorts_postgres    postgres:16-alpine     healthy
shorts_n8n         n8nio/n8n:2.9.4        running
shorts_worker      automacao-video_worker running
```

> **Se o status for `starting` ou `unhealthy`**, aguarde 30 segundos e rode `docker compose ps` novamente.

### 4.3 Ver os logs em tempo real

```bash
# Todos os containers
docker compose logs -f

# Apenas o worker
docker compose logs -f video_worker

# Apenas o postgres
docker compose logs -f postgres
```

### 4.4 Testar se o worker está funcionando

```bash
curl http://localhost:8000/health
```

Resposta esperada:

```json
{"status":"ok","db":"ok","worker":"ok","data_root":"/data"}
```

> Se `"db":"unreachable"`, o postgres ainda está iniciando. Aguarde e tente novamente.

---

## PARTE 5 — Banco de dados

### 5.1 Inicialização automática

Na **primeira vez** que o Postgres sobe, ele executa automaticamente os arquivos da pasta `sql/` em ordem:
- `01_schema.sql` — cria as tabelas, enums, índices, funções
- `02_seeds.sql` — insere 5 jobs de exemplo

**Confirmar que as tabelas foram criadas:**

```bash
docker exec -it shorts_postgres psql -U shorts -d shorts_db -c "\dt"
```

Deve listar: `assets`, `dead_letters`, `job_logs`, `job_steps`, `video_jobs`.

### 5.2 Se as tabelas NÃO foram criadas

Isso acontece quando o volume `data/postgres/` já existia de um start anterior. Execute manualmente:

```bash
docker exec -i shorts_postgres psql -U shorts -d shorts_db < sql/01_schema.sql
docker exec -i shorts_postgres psql -U shorts -d shorts_db < sql/02_seeds.sql
```

### 5.3 Verificar os jobs de exemplo

```bash
docker exec -it shorts_postgres psql -U shorts -d shorts_db \
  -c "SELECT id, status, main_subject, priority FROM video_jobs ORDER BY priority DESC;"
```

Deve mostrar 5 jobs com status `PENDING`.

### 5.4 Conectar ao banco (interface interativa)

```bash
docker exec -it shorts_postgres psql -U shorts -d shorts_db
# Prompt: shorts_db=#
# Para sair: \q
```

---

## PARTE 6 — Assets de vídeo e música

O sistema **não baixa nada da internet**. Você precisa colocar os arquivos manualmente.

### 6.1 Estrutura de pastas (já criada)

```
data/assets/
├── broll/
│   ├── finance/     ← COLOQUE b-roll aqui (.mp4 ou .jpg/.png)
│   └── default/     ← fallback genérico
├── music/           ← COLOQUE músicas aqui (.mp3 ou .wav)
└── fonts/
    └── Arial.ttf    ← opcional, melhora o visual das legendas
```

### 6.2 Onde conseguir assets gratuitos

**B-roll de finanças (vídeos e fotos — baixe manualmente):**

| Site | Busca sugerida | Formato |
|------|---------------|---------|
| pexels.com | "money", "stocks", "finance", "investing" | .mp4, .jpg |
| pixabay.com | "coins", "economy", "chart", "business" | .mp4, .jpg |
| unsplash.com | "money", "finance", "bank" | .jpg |

**Músicas de fundo (sem copyright):**

| Site | O que buscar |
|------|-------------|
| freemusicarchive.org | instrumental, corporate, lo-fi |
| ccmixter.org | instrumental |
| youtube.com/audiolibrary | (precisa de conta Google) |

**Fontes:**

```bash
# Baixar Arial (Linux/Mac via brew)
brew install --cask font-arial        # Mac com Homebrew

# Ou copie o Arial.ttf do seu sistema (no Mac está em /Library/Fonts/)
cp /Library/Fonts/Arial.ttf data/assets/fonts/
```

### 6.3 Nomear os arquivos para melhor matching

O sistema escolhe assets comparando as **palavras-chave do roteiro** com os **nomes dos arquivos**. Nomeie descritivamente:

```
✅ Bom:    money_coins_close_up.mp4
✅ Bom:    stock_chart_growing.jpg
✅ Bom:    credit_card_payment.mp4
❌ Ruim:   clip001.mp4
❌ Ruim:   IMG_4523.jpg
```

### 6.4 Verificar se os assets são reconhecidos

```bash
# Listar o que está nas pastas
ls data/assets/broll/finance/
ls data/assets/music/
```

> **Mínimo para funcionar:** pelo menos 3 imagens `.jpg` em `data/assets/broll/finance/`. Sem nenhum asset, o FFmpeg gera fundo preto (o vídeo ainda é criado).

---

## PARTE 6.1 — Renomear assets com IA

O script `python_service/utils/rename_video.py` usa um modelo de visão (Ollama `llava` ou qualquer endpoint OpenAI-compatible) para analisar os frames de cada vídeo/imagem e gerar um nome descritivo automaticamente — facilitando muito a escolha do asset certo na hora de montar o short.

### Por que isso importa

O sistema de seleção de assets em `assets.py` faz **keyword matching pelo nome do arquivo**. Quanto mais descritivo o nome, melhor o b-roll escolhido para cada cena.

```
❌ Antes:  IMG_4523.mp4, clip001.mp4, An icon spot.MP4
✅ Depois: finance_coins_close_up_hands.mp4, stock_chart_growing_screen.mp4
```

### Pré-requisito: modelo de visão no Ollama

```bash
ollama pull llava          # recomendado (~4.7 GB)
# ou mais leve:
ollama pull moondream      # ~1.7 GB, menos preciso
```

### Uso básico

```bash
# Navegue até o script
cd python_service/utils/

# 1. DRY-RUN — preview sem renomear nada:
python3 rename_video.py ../../data/assets/broll/finance

# 2. Aplicar a renomeação:
python3 rename_video.py ../../data/assets/broll/finance --apply

# 3. Aplicar com prefixo de tema (recomendado):
python3 rename_video.py ../../data/assets/broll/finance --theme finance --apply

# 4. Processar toda a pasta broll de uma vez:
python3 rename_video.py ../../data/assets/broll --theme finance --apply
```

### Variáveis de ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `VISION_ENDPOINT` | `http://localhost:11434/api/generate` | Ollama ou endpoint OpenAI-compat |
| `VISION_MODEL` | `llava` | Modelo de visão a usar |
| `LLM_TIMEOUT` | `60` | Timeout em segundos por arquivo |

### Usar com modelo diferente

```bash
# Outro modelo Ollama:
VISION_MODEL=moondream python3 rename_video.py ../../data/assets/broll --apply

# OpenAI gpt-4o-mini:
VISION_ENDPOINT=https://api.openai.com/v1/chat/completions \
VISION_MODEL=gpt-4o-mini \
python3 rename_video.py ../../data/assets/broll --apply

# LM Studio local (OpenAI-compat):
VISION_ENDPOINT=http://localhost:1234/v1/chat/completions \
VISION_MODEL=llava-v1.6 \
python3 rename_video.py ../../data/assets/broll --apply
```

### Opções do CLI

| Flag | Descrição |
|------|-----------|
| `--apply` | Executa a renomeação (padrão: dry-run) |
| `--theme <nome>` | Prefixo adicionado a todos os slugs (ex: `finance`) |
| `--no-skip` | Re-analisa arquivos que já têm nome descritivo |
| `--frames N` | Número de frames extraídos por vídeo (padrão: 3) |

### Log de renomeações

Após rodar, o script salva um CSV em `data/assets/broll/<pasta>/rename_map.csv` com o mapeamento completo de nomes antigos → novos, útil para rastrear ou desfazer.

### Fallback sem IA

Se o modelo de visão não estiver disponível ou retornar erro, o script faz fallback automático gerando o nome a partir do **nome das pastas + nome original do arquivo** — sem travar o processo.

---

## PARTE 7 — Configurar o n8n

### 7.1 Acessar o n8n

Abra no navegador: **http://localhost:5678**

Na primeira vez, crie uma conta de owner (email/senha locais — não conecta na internet).

### 7.2 Criar a credencial do PostgreSQL

1. Clique no seu avatar (canto superior direito) → **Settings**
2. Vá em **Credentials** → **Add credential**
3. Busque por `PostgreSQL` → selecione
4. Preencha os campos:

   | Campo | Valor |
   |-------|-------|
   | Credential Name | `PostgreSQL Shorts` |
   | Host | `postgres` |
   | Database | `shorts_db` |
   | User | `shorts` |
   | Password | `shorts_secret` |
   | Port | `5432` |
   | SSL | Off |

5. Clique em **Test connection** — deve aparecer "Connection tested successfully"
6. Clique em **Save**

> O host é `postgres` (nome do serviço no docker-compose), não `localhost`.

### 7.3 Importar o workflow

1. Vá em **Workflows** (menu lateral esquerdo)
2. Clique em **Add workflow** → **Import from file**
3. Selecione o arquivo: `n8n/workflow_shorts.json`
4. O workflow "Shorts Video Pipeline" aparecerá com 11 nodes

### 7.4 Vincular a credencial nos nodes Postgres

Após importar, os nodes Postgres mostrarão um alerta de credencial. Para cada node de Postgres (são 5 no total):

1. Clique no node
2. Na seção **Credential to connect with** → clique no campo
3. Selecione `PostgreSQL Shorts` (a que você criou)
4. Feche o painel do node

Os 5 nodes Postgres para atualizar:
- `Postgres – Claim Job`
- `Postgres – Log Dispatched`
- `Postgres – Mark Dispatch Failed`
- `Postgres – Dead Letter Check`
- `Postgres – Log Workflow Error`

### 7.5 Verificar o node HTTP

Clique no node **`HTTP – Call Worker`** e confirme:
- Method: `POST`
- URL: `={{ $env.WORKER_URL }}/run/{{ $json.id }}`

O `WORKER_URL` é resolvido via variável de ambiente (`http://video_worker:8000`), já configurada no docker-compose.

### 7.6 Ativar o workflow

No canto superior direito do workflow, clique no toggle **Inactive → Active**.

O cron dispara automaticamente a cada 5 minutos.

---

## PARTE 8 — Criar e executar o primeiro job

### 8.1 Inserir um job personalizado

Conecte ao banco e insira:

```bash
docker exec -it shorts_postgres psql -U shorts -d shorts_db
```

```sql
INSERT INTO video_jobs (
    main_subject,
    niche,
    language,
    style,
    duration_target_seconds,
    assets_profile,
    priority,
    scheduled_at,
    extra_params
) VALUES (
    'How to Build a $1,000 Emergency Fund From Scratch',
    'finance',
    'en_US',
    'commentary',
    30,
    'finance',
    10,
    NOW(),
    '{"tone": "motivational", "complexity": "beginner"}'
);
```

```sql
-- Confirmar que foi inserido
SELECT id, status, main_subject, priority FROM video_jobs ORDER BY priority DESC LIMIT 5;
\q
```

### 8.2 Executar manualmente (sem esperar o cron)

No n8n, com o workflow aberto:

1. Clique no botão **Execute workflow** (triângulo ▶ no canto superior)
2. O workflow roda imediatamente

Alternativamente, execute via API do worker:

```bash
# Pegar o ID do job mais recente
JOB_ID=$(docker exec -it shorts_postgres psql -U shorts -d shorts_db -tAc \
  "SELECT id FROM video_jobs WHERE status='PENDING' ORDER BY priority DESC LIMIT 1;")

echo "Job ID: $JOB_ID"

# Disparar o pipeline manualmente
curl -X POST "http://localhost:8000/run/$JOB_ID"
```

### 8.3 Acompanhar o progresso em tempo real

**Via logs do worker:**

```bash
docker compose logs -f video_worker
```

Você verá algo como:

```
INFO | job.abc12345.INIT         | Starting step INIT
INFO | job.abc12345.INIT         | Step INIT done
INFO | job.abc12345.SCRIPT_GEN   | Starting step SCRIPT_GEN
INFO | job.abc12345.SCRIPT_GEN   | Calling LLM at http://...
INFO | job.abc12345.SCRIPT_GEN   | Step SCRIPT_GEN done
INFO | job.abc12345.TTS          | Calling TTS at http://...
...
INFO | job.abc12345.RENDER       | Final video: /data/videos/2025-01-15/abc.../final.mp4 (12.3 MB)
INFO | job.abc12345.POST_PACK    | Step POST_PACK done
```

**Via API:**

```bash
curl http://localhost:8000/job/$JOB_ID
```

**Via SQL:**

```bash
docker exec -it shorts_postgres psql -U shorts -d shorts_db -c \
  "SELECT step_name, status, duration_ms FROM job_steps WHERE job_id='$JOB_ID' ORDER BY started_at;"
```

### 8.4 Duração esperada por step

| Step | Tempo típico |
|------|-------------|
| INIT | < 1s |
| SCRIPT_GEN | 10–60s (depende do LLM e modelo) |
| VALIDATE_SCRIPT | < 1s |
| TTS | 5–30s (depende do texto e hardware) |
| ASR | 5–20s (ou instantâneo no fallback) |
| ASSET_SELECT | < 2s |
| RENDER | 15–90s (depende da duração e número de assets) |
| POST_PACK | < 1s |
| **Total** | **~1–4 minutos** |

---

## PARTE 9 — Ver os resultados

### 9.1 Verificar os arquivos gerados

```bash
ls data/videos/$(date +%Y-%m-%d)/
# Lista as pastas de cada job gerado hoje
```

```bash
# Ver o conteúdo de um job específico
ls data/videos/$(date +%Y-%m-%d)/<job-uuid>/
```

Você deve ver:

```
job_meta.json      ← configuração do job
script.json        ← roteiro gerado pelo LLM
voice.wav          ← narração gerada pelo TTS
captions.srt       ← legendas (ASR ou estimadas)
background.mp4     ← b-roll / slideshow montado
audio_mix.aac      ← voz + música mixada
final.mp4          ← ← ← ESTE É O VÍDEO FINAL (1080×1920, 30fps)
post_pack.txt      ← caption + hashtags + disclaimer prontos
```

### 9.2 Ver o post_pack.txt

```bash
cat data/videos/$(date +%Y-%m-%d)/*/post_pack.txt
```

Conteúdo típico:

```
=== POST PACK ===
Status : READY_FOR_MANUAL_POST
Job ID : abc123...
Topic  : How to Build a $1,000 Emergency Fund From Scratch

--- TITLE (for video / cover) ---
Build Your Emergency Fund: Start Today

--- CAPTION (copy-paste) ---
Did you know 60% of Americans can't cover a $1,000 emergency?
Here's how to change that — starting with just $5 a day.

#PersonalFinance #EmergencyFund #MoneyTips #Finance101 ...

---
This video is for educational purposes only and does not constitute financial advice.

--- FILE PATHS ---
Video  : /data/videos/2025-01-15/abc.../final.mp4
SRT    : /data/videos/2025-01-15/abc.../captions.srt
Script : /data/videos/2025-01-15/abc.../script.json
```

### 9.3 Abrir o vídeo final

```bash
open data/videos/$(date +%Y-%m-%d)/*/final.mp4    # Mac
xdg-open data/videos/$(date +%Y-%m-%d)/*/final.mp4 # Linux
```

### 9.4 Ver o status no n8n

Na interface do n8n → **Executions** (menu lateral) → você verá todas as execuções do workflow com status ✅ ou ❌.

---

## PARTE 10 — Operação diária

### Inserir um novo job com tópico customizado

```sql
-- Conectar ao banco
docker exec -it shorts_postgres psql -U shorts -d shorts_db

-- Inserir job com tópico personalizado
INSERT INTO video_jobs (main_subject, niche, language, style, duration_target_seconds, assets_profile, priority, extra_params)
VALUES (
    'What Is a Roth IRA and Should You Open One?',  -- ← altere só isso
    'finance', 'en_US', 'commentary', 30, 'finance', 7,
    '{"tone": "friendly", "complexity": "beginner"}'
);
```

### Tópicos de exemplo para variações

```sql
-- ETFs
INSERT INTO video_jobs (main_subject, ...) VALUES ('ETFs vs Index Funds: Which Is Better for Beginners?', ...);

-- Budgeting
INSERT INTO video_jobs (main_subject, ...) VALUES ('The 50/30/20 Budget Rule Explained Simply', ...);

-- Investing
INSERT INTO video_jobs (main_subject, ...) VALUES ('Dollar-Cost Averaging: The Set-It-and-Forget-It Strategy', ...);

-- Credit
INSERT INTO video_jobs (main_subject, ...) VALUES ('Why Your Credit Score Matters More Than You Think', ...);

-- Debt
INSERT INTO video_jobs (main_subject, ...) VALUES ('Debt Snowball vs Debt Avalanche: Which Pays Off Debt Faster?', ...);
```

### Mudar o intervalo do cron

No n8n, clique no node **Schedule Trigger** e altere:
- `minutesInterval: 5` → o intervalo que preferir (ex: 10, 15, 30)

### Pausar o pipeline temporariamente

```bash
# No n8n: desative o workflow (toggle Inactive)
# Ou pare apenas o worker:
docker compose stop video_worker

# Reativar:
docker compose start video_worker
```

### Parar todos os serviços

```bash
docker compose down        # para e remove containers (dados preservados)
docker compose down -v     # para, remove containers E volumes (apaga DB)
```

### Reiniciar após reboot do Mac

```bash
cd /Users/rafaelkleimpaul/Desktop/Projetos/Automacao_Shorts
ollama serve &             # reiniciar Ollama (se não estiver como daemon)
docker compose up -d       # reiniciar containers
```

---

## Referência rápida de comandos

### Containers

```bash
docker compose up -d --build    # subir tudo (rebuild do worker)
docker compose up -d            # subir tudo (sem rebuild)
docker compose down             # parar tudo
docker compose ps               # ver status
docker compose logs -f          # ver todos os logs
docker compose logs -f video_worker  # logs do worker
```

### Banco de dados

```bash
# Conectar
docker exec -it shorts_postgres psql -U shorts -d shorts_db

# Jobs recentes
docker exec -it shorts_postgres psql -U shorts -d shorts_db \
  -c "SELECT id, status, main_subject, retry_count, created_at FROM video_jobs ORDER BY created_at DESC LIMIT 10;"

# Logs de um job
docker exec -it shorts_postgres psql -U shorts -d shorts_db \
  -c "SELECT step_name, level, message, logged_at FROM job_logs WHERE job_id='<UUID>' ORDER BY logged_at;"

# Re-enfileirar job com falha
docker exec -it shorts_postgres psql -U shorts -d shorts_db \
  -c "UPDATE video_jobs SET status='PENDING', retry_count=0, error_message=NULL, locked_by=NULL, locked_at=NULL, started_at=NULL WHERE id='<UUID>';"
```

### Worker API

```bash
curl http://localhost:8000/health          # saúde do serviço
curl http://localhost:8000/jobs            # listar jobs recentes
curl http://localhost:8000/job/<UUID>      # status de um job
curl -X POST http://localhost:8000/run/<UUID>  # disparar job manualmente
curl http://localhost:8000/docs            # Swagger UI interativo
```

### Arquivos de saída

```bash
ls data/videos/$(date +%Y-%m-%d)/            # jobs de hoje
cat data/videos/$(date +%Y-%m-%d)/*/post_pack.txt  # ver todos os post_packs de hoje
open data/videos/$(date +%Y-%m-%d)/*/final.mp4     # abrir todos os vídeos
```

---

## Solução de problemas

### ❌ Worker não sobe (`docker compose ps` mostra `exited`)

```bash
docker compose logs video_worker
```

Causas comuns:
- **Import error Python:** dependência faltando → `docker compose build --no-cache video_worker`
- **DATABASE_URL inválida:** verifique o `.env` e se o postgres está healthy

---

### ❌ `"db":"unreachable"` no `/health`

O Postgres ainda está iniciando. Aguarde 15 segundos e tente de novo:

```bash
docker compose ps postgres    # deve mostrar "healthy"
curl http://localhost:8000/health
```

---

### ❌ Job fica em `PROCESSING` mas não avança

```bash
# Ver o último log do job
docker exec -it shorts_postgres psql -U shorts -d shorts_db \
  -c "SELECT step_name, level, message FROM job_logs WHERE job_id='<UUID>' ORDER BY logged_at DESC LIMIT 5;"
```

Causas comuns:
- **LLM não responde:** verifique se `ollama serve` está rodando no host
- **TTS não responde:** o pipeline continua com fallback, mas verifique os logs

---

### ❌ SCRIPT_GEN falha (LLM não acessível)

```bash
# Testar se o Ollama está acessível do host
curl http://localhost:11434/api/generate \
  -d '{"model":"llama3","prompt":"test","stream":false}'

# Testar se é acessível dentro do container
docker exec shorts_worker curl http://host.docker.internal:11434/api/generate \
  -d '{"model":"llama3","prompt":"test","stream":false}'
```

Se o host funciona mas o container não, verifique se o `extra_hosts: host.docker.internal:host-gateway` está no docker-compose (já está por padrão).

---

### ❌ VALIDATE_SCRIPT falha (roteiro inválido)

O LLM não gerou um JSON válido ou o voiceover é muito curto/longo. Soluções:

1. **Trocar o modelo** para um mais capaz: `LLM_MODEL=llama3` → `LLM_MODEL=mistral`
2. **Verificar o script gerado:** `cat data/videos/.../script.json`
3. **Re-enfileirar o job:**

```sql
UPDATE video_jobs
SET status='PENDING', retry_count=0, error_message=NULL,
    locked_by=NULL, locked_at=NULL, started_at=NULL
WHERE id='<UUID>';
-- Deleta apenas o step que falhou para re-executar do SCRIPT_GEN
DELETE FROM job_steps WHERE job_id='<UUID>' AND step_name IN ('SCRIPT_GEN','VALIDATE_SCRIPT','TTS','ASR','ASSET_SELECT','RENDER','POST_PACK');
```

---

### ❌ RENDER falha (FFmpeg error)

```bash
docker compose logs video_worker | grep -A 5 "FFmpeg failed"
```

Causas comuns:
- **Nenhum asset:** sem arquivos em `data/assets/broll/finance/` → FFmpeg usa fundo preto (não é erro fatal, deve funcionar)
- **Codec não suportado:** raro na imagem base, mas tente: `docker compose build --no-cache video_worker`
- **Arquivo corrompido:** remova e substitua o asset problemático

---

### ❌ n8n não consegue chamar o worker

No n8n, veja os logs da execução do node **HTTP – Call Worker**. Se o erro for de conexão:

```bash
# Testar de dentro do container n8n
docker exec shorts_n8n wget -qO- http://video_worker:8000/health
```

Se falhar, verifique se ambos estão na mesma rede (`shorts_net`):

```bash
docker network inspect automacao_shorts_shorts_net | grep -E "Name|IPv4"
```

---

### ❌ Tabelas do banco não existem após primeiro start

```bash
docker exec -i shorts_postgres psql -U shorts -d shorts_db < sql/01_schema.sql
docker exec -i shorts_postgres psql -U shorts -d shorts_db < sql/02_seeds.sql
```

---

### ❌ n8n mostra erro de credencial no workflow importado

Os IDs de credenciais não transferem entre instâncias. Após importar o workflow:

1. Clique em cada node Postgres com o ícone ⚠️
2. No campo **Credential**, selecione manualmente `PostgreSQL Shorts`
3. Clique em **Save** no workflow

---

### Resetar tudo do zero

```bash
# Para tudo e apaga dados do Postgres e n8n (IRREVERSÍVEL)
docker compose down -v
rm -rf data/postgres data/n8n

# Sobe novamente (vai recriar o banco do zero)
docker compose up -d --build
```

> Os vídeos gerados em `data/videos/` e os assets em `data/assets/` **não são apagados** por este comando.

---

## Checklist completo de primeira execução

```
[ ] Docker Desktop instalado e rodando
[ ] Ollama instalado + modelo baixado + ollama serve rodando
[ ] TTS (Kokoro) instalado e testado em :8880
[ ] ASR (Whisper) instalado OU decidido usar fallback
[ ] cp .env.example .env
[ ] N8N_ENCRYPTION_KEY gerado (openssl rand -hex 24) e colado no .env
[ ] LLM/TTS/ASR endpoints configurados no .env
[ ] docker compose up -d --build  ← sem erros
[ ] curl http://localhost:8000/health  ← {"status":"ok","db":"ok"}
[ ] Tabelas criadas no postgres (SELECT * FROM video_jobs)
[ ] Assets colocados em data/assets/broll/finance/ (mín. 3 imagens)
[ ] Música colocada em data/assets/music/ (mín. 1 arquivo .mp3)
[ ] n8n aberto em http://localhost:5678
[ ] Credencial "PostgreSQL Shorts" criada e testada no n8n
[ ] Workflow importado de n8n/workflow_shorts.json
[ ] Credencial vinculada nos 5 nodes Postgres
[ ] Workflow ativado (toggle Active)
[ ] Job inserido no banco (INSERT INTO video_jobs ...)
[ ] Execute workflow clicado no n8n (ou aguardar cron)
[ ] docker compose logs -f video_worker  ← vendo o progresso
[ ] final.mp4 gerado em data/videos/YYYY-MM-DD/<uuid>/
[ ] post_pack.txt revisado e pronto para postar
```

---

## PARTE 11 — Postagem automática

O pipeline inclui um step `PUBLISH` que roda automaticamente após gerar o vídeo e posta nas plataformas configuradas. **Todos estão desabilitados por padrão** — ative um de cada vez após concluir o setup de autenticação.

### Visão geral do fluxo de publicação

```
RENDER → POST_PACK → PUBLISH
                        │
               ┌────────┼────────┐
               ▼        ▼        ▼
           YouTube  Instagram  TikTok
               │        │        │
               └────────┴────────┘
                        │
              job.status = PUBLISHED
                   (ou PUBLISH_FAILED)
```

Novos status do job com publicação ativa:

| Status | Significado |
|--------|------------|
| `PUBLISHING` | Publicando nas plataformas agora |
| `PUBLISHED` | Publicado com sucesso (ao menos 1 plataforma) |
| `PUBLISH_FAILED` | Todas as plataformas falharam |
| `READY_FOR_MANUAL_POST` | Nenhuma plataforma habilitada (padrão) |

### Verificar resultados de publicação

```bash
# Via API
curl http://localhost:8000/job/<UUID>
# → campo "publish_results" com status por plataforma

curl http://localhost:8000/publish_results
# → histórico completo de todas as postagens

# Via SQL
docker exec -it shorts_postgres psql -U shorts -d shorts_db \
  -c "SELECT * FROM v_publish_summary ORDER BY completed_at DESC LIMIT 10;"
```

---

### 11.1 YouTube

#### Pré-requisitos

1. Conta Google com canal no YouTube
2. Projeto no [Google Cloud Console](https://console.cloud.google.com/) com YouTube Data API v3 ativada
3. OAuth 2.0 Client ID do tipo **Desktop app**

#### Setup passo a passo

**Passo 1 — Criar o projeto e ativar a API:**

1. Acesse https://console.cloud.google.com/
2. Crie um novo projeto (ex: `shorts-pipeline`)
3. **APIs & Services → Library** → busque `YouTube Data API v3` → **Enable**

**Passo 2 — Criar as credenciais OAuth:**

1. **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
2. Application type: **Desktop app**
3. Name: `shorts-worker`
4. Clique em **Download JSON**
5. Renomeie o arquivo baixado para `youtube_client_secrets.json`
6. Coloque em: `./data/credentials/youtube_client_secrets.json`

**Passo 3 — Configurar a tela de consentimento OAuth:**

1. **APIs & Services → OAuth consent screen**
2. User Type: **External**
3. Preencha nome do app e e-mail
4. Scopes: adicione `https://www.googleapis.com/auth/youtube.upload`
5. Test users: adicione seu e-mail do Google

**Passo 4 — Instalar dependências e rodar o script de auth no host:**

```bash
# Na pasta raiz do projeto (no HOST, não no Docker)
pip install google-auth-oauthlib google-api-python-client

python scripts/auth_youtube.py
# → Abre o browser → faça login → autorize → token salvo em data/credentials/youtube_token.json
```

**Passo 5 — Ativar no .env:**

```env
YOUTUBE_ENABLED=true
YOUTUBE_PRIVACY=public          # public | unlisted | private
YOUTUBE_CATEGORY_ID=27          # 27=Education
```

**Passo 6 — Reiniciar o worker:**

```bash
docker compose restart video_worker
curl http://localhost:8000/health
# → "youtube": "true"
```

#### Testar manualmente

```bash
# Inserir um job e executar
docker exec -it shorts_postgres psql -U shorts -d shorts_db \
  -c "INSERT INTO video_jobs (main_subject, priority) VALUES ('Test YouTube Post', 10) RETURNING id;"

# Pegar o ID retornado e disparar
curl -X POST http://localhost:8000/run/<UUID>

# Acompanhar
docker compose logs -f video_worker
```

#### Token expirado

O token do YouTube é renovado automaticamente pelo worker. Se falhar, rode o script de auth novamente:

```bash
python scripts/auth_youtube.py
docker compose restart video_worker
```

---

### 11.2 Instagram (Reels)

> **Requisito importante:** O Instagram exige que o vídeo esteja em uma URL **publicamente acessível** para processamento. Você precisa de um `PUBLIC_BASE_URL` — use ngrok ou Cloudflare Tunnel.

#### Pré-requisitos

1. Conta Instagram **Business ou Creator** conectada a uma **Facebook Page**
2. App no [Meta Developers](https://developers.facebook.com/) com Instagram Graph API
3. Permissões aprovadas: `instagram_basic`, `instagram_content_publish`, `pages_show_list`
4. URL pública (ngrok, Cloudflare Tunnel, domínio próprio)

#### Setup passo a passo

**Passo 1 — Criar App no Meta:**

1. Acesse https://developers.facebook.com/
2. **My Apps → Create App → Consumer** (ou Business)
3. Nome: `shorts-pipeline`
4. **Add Product → Instagram Graph API**

**Passo 2 — Obter o token de acesso:**

1. Vá em **Tools → Graph API Explorer**
2. Selecione seu App no menu superior
3. Clique em **Generate Access Token**
4. Marque as permissões: `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`
5. Autorize e copie o token gerado (curto prazo, ~1h)

**Passo 3 — Rodar o script de auth:**

```bash
pip install requests   # no HOST

python scripts/auth_instagram.py
# → Guia você pelo processo passo a passo
# → No final exibe os valores para colocar no .env
```

O script retorna algo como:
```
INSTAGRAM_ENABLED=true
INSTAGRAM_USER_ID=123456789012345
INSTAGRAM_ACCESS_TOKEN=EAABxxxxxx...  (válido por 60 dias)
```

**Passo 4 — Configurar URL pública (ngrok — forma mais fácil):**

```bash
# Instalar ngrok: https://ngrok.com/download
# Mac:
brew install ngrok

# Criar conta gratuita em ngrok.com e autenticar:
ngrok config add-authtoken <seu-token>

# Expor o worker (deixe rodando em um terminal separado):
ngrok http 8000
# → Copia a URL: https://abc123.ngrok-free.app
```

**Alternativa — Cloudflare Tunnel (mais estável, gratuito):**

```bash
# Instalar cloudflared:
brew install cloudflared   # Mac

# Criar túnel:
cloudflared tunnel --url http://localhost:8000
# → Copia a URL: https://xxx.trycloudflare.com
```

**Passo 5 — Adicionar ao .env:**

```env
INSTAGRAM_ENABLED=true
INSTAGRAM_USER_ID=123456789012345
INSTAGRAM_ACCESS_TOKEN=EAABxxxxxx...
PUBLIC_BASE_URL=https://abc123.ngrok-free.app   # ← URL do ngrok/Cloudflare
```

**Passo 6 — Reiniciar o worker:**

```bash
docker compose restart video_worker
```

#### Renovar o token (a cada 60 dias)

```bash
python scripts/auth_instagram.py
# → Atualiza INSTAGRAM_ACCESS_TOKEN no .env
docker compose restart video_worker
```

#### Limitações do Instagram

- O vídeo deve estar acessível por 10+ minutos durante o processamento
- Manter o ngrok/Cloudflare Tunnel rodando durante a postagem
- Specs do Reel: MP4, H.264+AAC, 9:16, 3–90s, máx 100MB ✅ (já compatível)
- Conta deve ser Business ou Creator (não funciona com conta pessoal)

---

### 11.3 TikTok

> O TikTok **aceita upload direto de arquivo** — não precisa de URL pública.

#### Pré-requisitos

1. Conta TikTok com conteúdo (quanto mais seguidores, maior chance de aprovação do app)
2. App aprovado em [TikTok for Developers](https://developers.tiktok.com/) com **Content Posting API**
3. Scopes aprovados: `video.publish`, `video.upload`, `user.info.basic`

#### Setup passo a passo

**Passo 1 — Criar App no TikTok for Developers:**

1. Acesse https://developers.tiktok.com/
2. **Manage Apps → Create app**
3. Plataforma: **Web**
4. Adicione o produto: **Content Posting API**
5. Em **Login Kit**: adicione redirect URI: `http://localhost:8180/callback`
6. Copie o **Client Key** e **Client Secret**

> A aprovação do app pode levar alguns dias. Para testes, você pode usar o "Sandbox mode" disponível no painel do app.

**Passo 2 — Rodar o script de auth:**

```bash
pip install requests   # no HOST

TIKTOK_CLIENT_KEY=sua_key TIKTOK_CLIENT_SECRET=seu_secret python scripts/auth_tiktok.py
# → Abre o browser para autorização TikTok
# → Token salvo em: data/credentials/tiktok_token.json
```

**Passo 3 — Adicionar ao .env:**

```env
TIKTOK_ENABLED=true
TIKTOK_CLIENT_KEY=sua_client_key
TIKTOK_CLIENT_SECRET=seu_client_secret
TIKTOK_PRIVACY=PUBLIC_TO_EVERYONE   # PUBLIC_TO_EVERYONE | FOLLOWER_OF_CREATOR | SELF_ONLY
```

**Passo 4 — Reiniciar o worker:**

```bash
docker compose restart video_worker
```

#### Token refresh automático

O refresh token do TikTok é válido por ~365 dias. O worker renova o access token automaticamente. Se o refresh token também expirar, rode o script de auth novamente.

#### Specs do TikTok

- MP4, H.264+AAC ✅
- Duração: 3s–10min ✅ (nós geramos 20–45s)
- Máx: 4GB ✅
- Aspectos aceitos: 9:16 ✅, também 1:1, 16:9
- Título: máx 150 chars

---

### 11.4 Habilitar múltiplas plataformas simultaneamente

Você pode habilitar qualquer combinação. O pipeline tenta publicar em todas as plataformas ativas. Se uma falhar, as outras ainda são publicadas.

```env
# Exemplo: habilitar YouTube e TikTok, mas não Instagram
YOUTUBE_ENABLED=true
INSTAGRAM_ENABLED=false
TIKTOK_ENABLED=true
```

Resultado no banco após publicação:

```sql
SELECT platform, status, platform_url, published_at
FROM publish_results
WHERE job_id = '<uuid>';

-- Exemplo:
-- youtube  | SUCCESS | https://youtube.com/watch?v=xxxx | 2025-01-15 10:30:00
-- instagram| SKIPPED | NULL                              | NULL
-- tiktok   | SUCCESS | https://www.tiktok.com/          | 2025-01-15 10:31:00
```

---

### 11.5 Checklist de publicação automática

```
[ ] SQL 03_publishing.sql executado no banco
    docker exec -i shorts_postgres psql -U shorts -d shorts_db < sql/03_publishing.sql

YouTube:
[ ] YouTube Data API v3 ativada no Google Cloud
[ ] OAuth 2.0 Client ID (Desktop app) criado
[ ] youtube_client_secrets.json em ./data/credentials/
[ ] python scripts/auth_youtube.py → token salvo
[ ] YOUTUBE_ENABLED=true no .env
[ ] docker compose restart video_worker

Instagram:
[ ] App Meta criado com permissões aprovadas
[ ] python scripts/auth_instagram.py → token + user_id obtidos
[ ] ngrok ou Cloudflare Tunnel rodando (ngrok http 8000)
[ ] PUBLIC_BASE_URL configurado no .env
[ ] INSTAGRAM_ENABLED=true, USER_ID e TOKEN no .env
[ ] docker compose restart video_worker

TikTok:
[ ] App TikTok for Developers criado com Content Posting API aprovado
[ ] redirect_uri http://localhost:8180/callback registrado no app
[ ] python scripts/auth_tiktok.py → token salvo
[ ] TIKTOK_ENABLED=true, CLIENT_KEY e CLIENT_SECRET no .env
[ ] docker compose restart video_worker

Geral:
[ ] curl http://localhost:8000/health → plataformas habilitadas visíveis
[ ] Inserir job de teste e executar
[ ] curl http://localhost:8000/publish_results → ver resultado
```

---

### 11.6 Solução de problemas de publicação

**Job travado em `PUBLISHING` por muito tempo:**

```bash
docker compose logs -f video_worker | grep -i "instagram\|youtube\|tiktok"
```

**Re-tentar publicação após falha (sem re-gerar o vídeo):**

```sql
-- O job deve estar em PUBLISH_FAILED ou PUBLISHED
-- Deletar apenas o step PUBLISH para re-executar só a publicação
DELETE FROM job_steps WHERE job_id='<UUID>' AND step_name='PUBLISH';
UPDATE video_jobs SET status='READY_FOR_MANUAL_POST' WHERE id='<UUID>';

-- Disparar via API:
-- curl -X POST http://localhost:8000/run/<UUID>
```

**Instagram — erro "Video URL not accessible":**

Verifique se o ngrok/tunnel está ativo e se o `PUBLIC_BASE_URL` no .env está correto:

```bash
# Testar se o worker serve o arquivo corretamente
curl "https://seu-ngrok.ngrok-free.app/serve/<JOB_UUID>/final.mp4" -I
# → deve retornar HTTP 200
```

**YouTube — "Token expired":**

```bash
python scripts/auth_youtube.py    # renova automaticamente
docker compose restart video_worker
```

**TikTok — "Scope not authorized":**

No painel do TikTok for Developers, verifique se os scopes `video.publish` e `video.upload` estão aprovados. Em sandbox mode, alguns scopes têm restrições.
```
