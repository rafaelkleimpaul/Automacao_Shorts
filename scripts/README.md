# Scripts Auxiliares

Scripts standalone para gerenciamento de assets do pipeline. Rodam diretamente no **Mac**, sem precisar subir o Docker.

---

## Requisitos

```bash
pip install yt-dlp requests
brew install ffmpeg   # macOS
```

Para renomear com Ollama (local):
```bash
ollama pull llava
```

---

## download_music.py

Baixa o áudio de vídeos ou playlists do YouTube e salva como `.mp3` na pasta de músicas do projeto.

### Uso

```bash
python scripts/download_music.py <url> [url2 ...] [--profile PROFILE] [--no-playlist] [--items RANGE]
```

### Exemplos

```bash
# Vídeo único
python scripts/download_music.py https://www.youtube.com/watch?v=XXXXX --profile mindset

# Playlist completa
python scripts/download_music.py https://www.youtube.com/playlist?list=XXXXX --profile mindset

# Apenas os 10 primeiros da playlist
python scripts/download_music.py https://www.youtube.com/playlist?list=XXXXX --profile mindset --items 1-10

# URL de vídeo que contém playlist — baixar só o vídeo
python scripts/download_music.py "https://www.youtube.com/watch?v=XXX&list=YYY" --no-playlist

# Múltiplos links
python scripts/download_music.py https://youtu.be/AAA https://youtu.be/BBB --profile finance
```

### Parâmetros

| Parâmetro | Obrigatório | Descrição |
|---|---|---|
| `urls` | sim | Um ou mais links do YouTube (vídeo ou playlist) |
| `--profile` / `-p` | não | Sub-pasta de destino (ex: `mindset`, `finance`) |
| `--no-playlist` | não | Baixa só o vídeo, ignorando a playlist da URL |
| `--items` | não | Faixa de itens da playlist: `1-10`, `1,3,5`, `1-5,7` |

### Copiar para a VPS após download

```bash
# Mindset
scp data/assets/music/mindset/*.mp3 root@IP_VPS:/opt/projects/automacao_shorts/data/assets/music/mindset/

# Finance
scp data/assets/music/finance/*.mp3 root@IP_VPS:/opt/projects/automacao_shorts/data/assets/music/finance/

# Pasta raiz
scp data/assets/music/*.mp3 root@IP_VPS:/opt/projects/automacao_shorts/data/assets/music/
```

---

## rename_video.py

Renomeia arquivos de b-roll (vídeos e imagens) usando IA de visão para gerar nomes descritivos em slug. Facilita a seleção automática de assets pelo pipeline.

**Localização:** `python_service/utils/rename_video.py`

### Uso

```bash
python python_service/utils/rename_video.py <pasta> [--theme TEMA] [--apply] [--no-skip] [--frames N]
```

### Exemplos

```bash
# Preview (sem renomear) — mostra o que seria alterado
python python_service/utils/rename_video.py data/assets/broll/finance

# Aplicar renomeação com prefixo de tema
python python_service/utils/rename_video.py data/assets/broll/finance --theme finance --apply

# Pasta mindset
python python_service/utils/rename_video.py data/assets/broll/mindset --theme mindset --apply

# Re-analisar todos os arquivos (inclusive os que já têm nome descritivo)
python python_service/utils/rename_video.py data/assets/broll/finance --no-skip --apply
```

### Parâmetros

| Parâmetro | Obrigatório | Descrição |
|---|---|---|
| `root_dir` | sim | Pasta a escanear recursivamente |
| `--theme` | não | Prefixo adicionado ao slug (ex: `finance`, `mindset`) |
| `--apply` | não | Aplica a renomeação. Sem este flag, roda em dry-run (preview) |
| `--no-skip` | não | Re-analisa arquivos que já têm nome descritivo |
| `--frames N` | não | Frames extraídos por vídeo para análise (padrão: 3) |

### Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `VISION_ENDPOINT` | `http://localhost:11434/api/generate` | Endpoint do modelo de visão |
| `VISION_MODEL` | `llava` | Modelo de visão a usar |
| `LLM_TIMEOUT` | `60` | Timeout em segundos |

### Usar com OpenAI (gpt-4o-mini)

```bash
VISION_ENDPOINT=https://api.openai.com/v1/chat/completions \
VISION_MODEL=gpt-4o-mini \
python python_service/utils/rename_video.py data/assets/broll/finance --theme finance --apply
```

### Copiar assets renomeados para a VPS

```bash
scp data/assets/broll/mindset/*.mp4 root@IP_VPS:/opt/projects/automacao_shorts/data/assets/broll/mindset/
```

> Um arquivo `rename_map.csv` é gerado na pasta analisada com o mapeamento de todos os arquivos renomeados.
