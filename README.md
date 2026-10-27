# GamePulse Agent

Agente local que corta clips MP4 a partir das acções registadas no Analyser do [GamePulse](https://gamepulsecoach.com).

Corre só no seu computador (`localhost:7777`). O site envia-lhe o vídeo, o momento da acção e o desenho do Telestrator; o agente descarrega o vídeo, corta o clip e grava-o em `Documentos\GamePulse\<Jogo>\<Categoria>\`.

## Instalar (Windows 10/11)

1. Descarregue `GamePulseAgent-Setup-<versão>.exe` da página [Releases](../../releases/latest).
2. Execute-o. Não precisa de permissões de administrador.
   - Se o Windows mostrar "O Windows protegeu o seu PC", clique em **Mais informações → Executar mesmo assim** (o instalador ainda não tem assinatura de código).
3. O agente arranca com o Windows e mostra um ícone verde na barra de tarefas.
4. No GamePulse, abra o Analyser → aba **Clips** → **Emparelhar** e introduza o código de 6 dígitos que aparece na notificação do agente.

macOS: em preparação.

## Segurança

- Só escuta em `127.0.0.1`, porta 7777.
- Só aceita pedidos de `https://gamepulsecoach.com`, e só depois de emparelhado com um código de uso único.
- Só descarrega vídeos de YouTube, Google Drive ou ligações MP4 directas.

## Desenvolvimento

```bash
pip install -r requirements.txt pytest pytest-cov httpx
python -m pytest
GP_AGENT_DEV=1 python -m gp_agent        # aceita também origens de desenvolvimento
python -m build_tools.build --tools <pasta com yt-dlp e ffmpeg>
```

Uma release é criada pelo GitHub Actions ao fazer push de uma tag `vX.Y.Z`.

## Software de terceiros

O instalador inclui, sem modificações:

- **FFmpeg** — licença [LGPL v2.1+](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html). Build LGPL de [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds); código-fonte em <https://ffmpeg.org/download.html>. O FFmpeg é executado como programa separado e pode ser substituído por outra versão (variável de ambiente `GP_FFMPEG`).
- **yt-dlp** — [Unlicense](https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE), de <https://github.com/yt-dlp/yt-dlp>.
- Bibliotecas Python: FastAPI e Uvicorn (MIT/BSD), Pillow (HPND), pystray (LGPL v3).
