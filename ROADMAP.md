# Roadmap de Melhorias — Shorts Video Pipeline

Documento de planejamento das melhorias priorizadas para o pipeline. Cada item inclui objetivo, arquivos envolvidos, dependências e critério de conclusão.

---

## Índice

- [Prioridade 1 — Fetch de notícias + sugestão de pautas com IA](#1-fetch-de-notícias--sugestão-de-pautas-com-ia)
- [Prioridade 2 — Notificação Telegram ao concluir vídeo](#2-notificação-telegram-ao-concluir-vídeo)
- [Prioridade 3 — Dashboard web no FastAPI](#3-dashboard-web-no-fastapi)
- [Prioridade 4 — Geração paralela de jobs](#4-geração-paralela-de-jobs)
- [Prioridade 5 — CLI interativo de criação de jobs](#5-cli-interativo-de-criação-de-jobs)
- [Prioridade 6 — Cache de TTS por frase](#6-cache-de-tts-por-frase)
- [Prioridade 7 — Thumbnail automática](#7-thumbnail-automática)
- [Prioridade 8 — Variação automática de hooks](#8-variação-automática-de-hooks)
- [Prioridade 9 — Analytics em loop fechado](#9-analytics-em-loop-fechado)

---

## 1. Fetch de notícias + sugestão de pautas com IA

**Objetivo:** Eliminar o maior gargalo manual do processo — escolher o tópico do vídeo. Um script busca notícias financeiras atuais, filtra via LLM local e insere os melhores tópicos como jobs no banco automaticamente.

**Esforço:** Médio | **Impacto:** Altíssimo

### Fontes de dados (todas gratuitas)

| Fonte | API / Método | Limite gratuito |
|-------|-------------|-----------------|
| NewsAPI | `newsapi.org` (key gratuita) | 100 req/dia |
| GNews | `gnews.io` (key gratuita) | 100 req/dia |
| Reddit | API pública sem auth | Sem limite razoável |
| Google Trends | `pytrends` (sem key) | Sem limite oficial |
| RSS Feeds | `feedparser` (sem key) | Ilimitado |

### Fluxo

```
fetch_news.py
    │
    ├─ NewsAPI / GNews / Reddit / RSS
    │       ↓
    ├─ Filtra duplicatas e notícias irrelevantes
    │       ↓
    ├─ LLM local (Ollama) avalia cada notícia:
    │   "É um bom tópico para short financeiro educativo? Sim/Não + motivo"
    │       ↓
    ├─ suggest_topics.py: LLM transforma notícias aprovadas
    │   em tópicos no formato ideal para o script_gen.py
    │       ↓
    └─ Insere jobs aprovados no banco (evita duplicatas por hash do tópico)
```

### Arquivos a criar

```
python_service/utils/
├── fetch_news.py          ← busca e filtra notícias de múltiplas fontes
└── suggest_topics.py      ← transforma notícias em pautas via LLM + insere jobs
```

### Novas variáveis de ambiente (.env)

```env
# Notícias
NEWS_API_KEY=           # newsapi.org (opcional, tem RSS como fallback)
GNEWS_API_KEY=          # gnews.io (opcional)
NEWS_SOURCES=newsapi,reddit,rss   # fontes ativas (separadas por vírgula)
NEWS_MAX_TOPICS=5       # máx de jobs inseridos por execução
NEWS_MIN_SCORE=7        # nota mínima do LLM (0-10) para aprovar um tópico

# RSS feeds customizados (separados por vírgula)
RSS_FEEDS=https://feeds.investopedia.com/investopedia/rss/news.aspx,https://www.cnbc.com/id/10000664/device/rss/rss.html
```

### Uso

```bash
# Buscar notícias e sugerir pautas (dry-run — só exibe, não insere)
python3 python_service/utils/fetch_news.py

# Inserir automaticamente no banco
python3 python_service/utils/fetch_news.py --insert

# Agendar via cron (todo dia às 7h)
# Adicionar ao crontab: 0 7 * * * cd /path/to/project && python3 python_service/utils/fetch_news.py --insert
```

### Critério de conclusão

- [ ] Script busca de pelo menos 2 fontes simultâneas
- [ ] LLM local filtra e pontua cada notícia (0–10)
- [ ] Tópicos aprovados são inseridos como `PENDING` no banco
- [ ] Sem duplicatas: mesmo tópico não é inserido duas vezes na mesma semana
- [ ] Funciona sem nenhuma API key (fallback RSS puro)

---

## 2. Notificação Telegram ao concluir vídeo

**Objetivo:** Receber uma mensagem no celular assim que um vídeo fica pronto, com o título, duração e o conteúdo do `post_pack.txt` — sem precisar checar logs ou abrir o computador.

**Esforço:** Baixo | **Impacto:** Alto

### Fluxo

```
orchestrator.py (POST_PACK step concluído)
    │
    └─ notify_telegram.py
            ├─ Monta mensagem com: título, duração, hook, hashtags
            ├─ Envia via Bot API do Telegram
            └─ Se falhar: loga warning (não quebra o pipeline)
```

### Configuração do bot Telegram

```
1. Abrir @BotFather no Telegram
2. /newbot → definir nome e username
3. Copiar o TOKEN gerado
4. Enviar qualquer mensagem para o bot
5. Acessar: https://api.telegram.org/bot<TOKEN>/getUpdates
6. Copiar o "chat_id" retornado
```

### Arquivos a criar/modificar

```
python_service/utils/
└── notify_telegram.py     ← novo: envia mensagem via Bot API

python_service/pipeline/
└── orchestrator.py        ← modificar: chamar notify_telegram após POST_PACK
```

### Novas variáveis de ambiente (.env)

```env
TELEGRAM_BOT_TOKEN=        # token do @BotFather
TELEGRAM_CHAT_ID=          # seu chat_id pessoal ou de um grupo
TELEGRAM_ENABLED=false     # ativar explicitamente
```

### Formato da notificação

```
🎬 Vídeo pronto!

📌 How to Build a $1,000 Emergency Fund
⏱ 32s  |  Job: abc-123

🪝 Hook:
"Did you know 60% of Americans can't cover a $1,000 emergency?"

📋 Caption pronta para postar:
[conteúdo do post_pack.txt]

#PersonalFinance #EmergencyFund #Finance101

📁 /data/videos/2025-01-15/abc-123/final.mp4
```

### Critério de conclusão

- [ ] Bot criado e TOKEN/CHAT_ID configuráveis via .env
- [ ] Notificação disparada após POST_PACK com sucesso
- [ ] Falha na notificação não interrompe o pipeline
- [ ] Mensagem inclui hook, caption e hashtags prontos para copiar

---

## 3. Dashboard web no FastAPI

**Objetivo:** Uma única página HTML servida em `GET /dashboard` que mostra a fila de jobs, progresso em tempo real, links para download do vídeo e prévia do script — sem instalar nada adicional.

**Esforço:** Médio | **Impacto:** Alto

### Telas / seções

```
┌─────────────────────────────────────────────────┐
│  Shorts Pipeline Dashboard          [+ Novo Job] │
├─────────────────────────────────────────────────┤
│  PROCESSANDO (2)                                │
│  ├─ "What is a Roth IRA?" ████████░░ 80% RENDER │
│  └─ "ETFs vs Index Funds" ████░░░░░░ 40% TTS    │
├─────────────────────────────────────────────────┤
│  CONCLUÍDOS HOJE (5)                            │
│  ├─ "Emergency Fund Guide"  [▶ Ver] [⬇ Download]│
│  └─ ...                                         │
├─────────────────────────────────────────────────┤
│  FILA (12 pendentes)                [Limpar]    │
│  ├─ P10  "Dollar-Cost Averaging"               │
│  └─ ...                                         │
└─────────────────────────────────────────────────┘
```

### Arquivos a criar/modificar

```
python_service/
├── main.py                     ← adicionar rotas /dashboard, /dashboard/new
└── templates/
    └── dashboard.html          ← HTML puro com auto-refresh a cada 10s (sem JS framework)
```

### Funcionalidades

- Listagem de jobs por status com progresso visual por step
- Formulário de criação de novo job (tópico, tom, duração, prioridade)
- Link direto para download do `final.mp4` e `post_pack.txt`
- Auto-refresh leve a cada 10 segundos via `<meta http-equiv="refresh">`
- Botão para re-enfileirar jobs com falha

### Critério de conclusão

- [ ] `GET /dashboard` retorna HTML sem dependência de JS externo
- [ ] Formulário cria job via `POST /dashboard/new`
- [ ] Jobs em progresso mostram o step atual
- [ ] Links de download funcionam para vídeos prontos
- [ ] Responsivo (legível no celular)

---

## 4. Geração paralela de jobs

**Objetivo:** Processar múltiplos jobs simultaneamente, reduzindo o tempo total de uma fila de 10 vídeos de ~40min para ~15min.

**Esforço:** Médio-Alto | **Impacto:** Alto

### Abordagem

Usar `asyncio` com semáforo para limitar a concorrência. O gargalo principal é o LLM e o FFmpeg — com 2–3 workers paralelos o ganho é substancial sem sobrecarregar a máquina.

### Arquivos a modificar

```
python_service/
├── main.py          ← endpoint /run lança background tasks com semáforo
└── pipeline/
    └── orchestrator.py  ← tornar steps async-compatíveis
```

### Nova variável de ambiente (.env)

```env
MAX_CONCURRENT_JOBS=2   # padrão conservador (ajustar conforme RAM/CPU disponível)
```

### Critério de conclusão

- [ ] `MAX_CONCURRENT_JOBS` jobs rodam simultaneamente
- [ ] Banco usa `FOR UPDATE SKIP LOCKED` (já implementado) — sem conflito
- [ ] Log diferencia jobs rodando em paralelo claramente
- [ ] Sem degradação perceptível no LLM com 2 workers simultâneos

---

## 5. CLI interativo de criação de jobs

**Objetivo:** Substituir o SQL `INSERT` manual por um CLI com prompts amigáveis, com sugestões de tópico via LLM local.

**Esforço:** Baixo | **Impacto:** Médio

### UX esperada

```bash
$ python3 scripts/create_job.py

? Tópico do vídeo: What is Dollar-Cost Averaging?
? (ou pressione Enter para o LLM sugerir baseado em tendências)

? Tom do vídeo:
  ❯ friendly
    educational
    urgent
    motivational

? Duração (segundos):
  ❯ 30
    45
    60

? Complexidade:
  ❯ beginner
    intermediate

? Prioridade (1-10): 8

──────────────────────────────────────
✅ Job criado com sucesso!
   ID       : abc-123-def
   Tópico   : What is Dollar-Cost Averaging?
   Prioridade: 8
   Status   : PENDING
──────────────────────────────────────
Criar outro? (s/N)
```

### Arquivos a criar

```
scripts/
└── create_job.py    ← CLI interativo com questionary + psycopg2
```

### Dependência

```
pip install questionary   # para o prompt interativo
```

### Critério de conclusão

- [ ] CLI guia criação de job sem precisar escrever SQL
- [ ] Opção de pedir sugestão de tópico ao LLM local
- [ ] Confirma criação e exibe o ID do job
- [ ] Loop para criar múltiplos jobs em sequência

---

## 6. Cache de TTS por frase

**Objetivo:** Frases idênticas entre vídeos (disclaimer, CTA final) não precisam ser re-geradas. Um cache baseado em hash do texto elimina chamadas redundantes ao TTS.

**Esforço:** Baixo | **Impacto:** Médio

### Funcionamento

```python
# Hash do texto + voz + velocidade → nome do arquivo em cache
cache_key = sha256(f"{text}:{voice}:{speed}").hexdigest()[:16]
cache_path = CACHE_DIR / f"{cache_key}.wav"

if cache_path.exists():
    return cache_path   # reutiliza direto

# Caso contrário: chama TTS e salva no cache
wav = call_tts(text)
cache_path.write_bytes(wav)
return cache_path
```

### Arquivos a modificar

```
python_service/pipeline/
└── tts.py    ← adicionar camada de cache antes de chamar o endpoint TTS
```

### Nova variável de ambiente (.env)

```env
TTS_CACHE_ENABLED=true
TTS_CACHE_MAX_MB=500    # limpar cache quando passar de 500MB
```

### Critério de conclusão

- [ ] Frases idênticas não geram nova chamada ao TTS
- [ ] Cache persiste entre restarts do container (volume montado)
- [ ] Limpeza automática quando ultrapassar `TTS_CACHE_MAX_MB`

---

## 7. Thumbnail automática

**Objetivo:** Gerar uma thumbnail 1080×1920 pronta para upload no YouTube/TikTok com o título do vídeo sobreposto em um frame representativo — sem edição manual.

**Esforço:** Baixo | **Impacto:** Médio

### Funcionamento

```
POST_PACK step
    │
    └─ Extrair frame do meio do final.mp4 (ffmpeg -ss duration/2)
            │
            └─ Pillow:
                ├─ Redimensionar para 1080×1920
                ├─ Gradiente escuro no terço inferior
                ├─ Texto do título (fonte Arial Bold, grande)
                └─ Salvar como thumbnail.jpg no diretório do job
```

### Arquivos a modificar/criar

```
python_service/pipeline/
└── orchestrator.py    ← adicionar geração de thumbnail ao step POST_PACK
```

### Saída

```
data/videos/2025-01-15/<uuid>/
├── final.mp4
├── post_pack.txt
└── thumbnail.jpg    ← novo
```

### Critério de conclusão

- [ ] `thumbnail.jpg` gerado automaticamente após POST_PACK
- [ ] Título legível com contraste adequado
- [ ] Funciona sem fonte externa (fallback para fonte padrão do Pillow)
- [ ] Incluído no link de download do dashboard

---

## 8. Variação automática de hooks

**Objetivo:** Gerar 3 versões do gancho de abertura e selecionar o mais impactante automaticamente (ou permitir escolha manual no dashboard).

**Esforço:** Médio | **Impacto:** Médio

### Funcionamento

No `script_gen.py`, após gerar o script principal, fazer uma segunda chamada ao LLM pedindo apenas 3 variações do `hook`. O sistema seleciona automaticamente o mais curto (hooks curtos têm maior retenção em shorts).

### Nova seção no script JSON

```json
{
  "hook": "Did you know 60% of Americans...",
  "hook_variants": [
    "Did you know 60% of Americans can't cover a $1,000 emergency?",
    "You're one car repair away from financial disaster — here's the fix.",
    "The $1,000 rule that could save your financial life."
  ],
  "hook_selected": 1
}
```

### Critério de conclusão

- [ ] 3 variações geradas por job
- [ ] Seleção automática por critério de comprimento
- [ ] Variantes exibidas no dashboard para escolha manual opcional
- [ ] Campo `hook_selected` salvo no `script.json`

---

## 9. Analytics em loop fechado

**Objetivo:** Usar os dados de performance do YouTube (views, CTR, retenção) para retroalimentar o `suggest_topics.py`, priorizando automaticamente os formatos e temas que performam melhor.

**Esforço:** Alto | **Impacto:** Alto (médio prazo)

### Dependências

- YouTube habilitado e com vídeos publicados (mín. 1 semana de dados)
- YouTube Analytics API ativada no Google Cloud (mesma conta do upload)

### Fluxo

```
fetch_analytics.py (roda semanalmente)
    │
    ├─ YouTube Analytics API → views, CTR, avg_view_duration por vídeo
    │
    ├─ Cruza com publish_results (job_id → video_id)
    │
    ├─ Identifica padrões:
    │   "Vídeos sobre investimento passivo: CTR médio 8.2%"
    │   "Vídeos sobre dívida: CTR médio 4.1%"
    │
    └─ Salva em analytics_summary.json
            │
            └─ suggest_topics.py lê o summary e pondera os temas
               antes de enviar para o LLM
```

### Arquivos a criar

```
python_service/utils/
└── fetch_analytics.py    ← busca dados do YouTube Analytics e gera resumo

data/
└── analytics_summary.json  ← cache local dos insights (gitignored)
```

### Critério de conclusão

- [ ] Script busca métricas dos últimos 30 dias via Analytics API
- [ ] Gera `analytics_summary.json` com temas e métricas médias
- [ ] `suggest_topics.py` usa o resumo para ponderar sugestões
- [ ] Agendável via cron semanalmente

---

## Resumo de prioridades

| # | Melhoria | Esforço | Impacto | Dependências |
|---|----------|---------|---------|--------------|
| 1 | Fetch de notícias + sugestão via IA | Médio | Altíssimo | nenhuma (RSS gratuito) |
| 2 | Notificação Telegram | Baixo | Alto | Bot Telegram criado |
| 3 | Dashboard web | Médio | Alto | nenhuma |
| 4 | Jobs em paralelo | Médio-Alto | Alto | nenhuma |
| 5 | CLI interativo | Baixo | Médio | `questionary` pip |
| 6 | Cache de TTS | Baixo | Médio | nenhuma |
| 7 | Thumbnail automática | Baixo | Médio | `Pillow` (já no requirements) |
| 8 | Variação de hooks | Médio | Médio | nenhuma |
| 9 | Analytics loop fechado | Alto | Alto (médio prazo) | YouTube habilitado + dados |

---

## Ordem de implementação sugerida

```
Sprint 1 (impacto imediato, baixo risco):
  ✦ #2 Telegram notification   — 1 sessão
  ✦ #6 TTS cache               — 1 sessão
  ✦ #7 Thumbnail automática    — 1 sessão

Sprint 2 (novos fluxos de entrada):
  ✦ #5 CLI interativo          — 1 sessão
  ✦ #1 Fetch de notícias       — 2–3 sessões

Sprint 3 (infraestrutura e visibilidade):
  ✦ #3 Dashboard web           — 2 sessões
  ✦ #4 Jobs em paralelo        — 2 sessões

Sprint 4 (otimização de conteúdo):
  ✦ #8 Variação de hooks       — 1 sessão
  ✦ #9 Analytics loop fechado  — 3+ sessões
```
