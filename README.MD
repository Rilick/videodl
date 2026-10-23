# VideoDL

Downloader de vídeos com interface de linha de comandos e interface web, baseado em yt-dlp.

## Como usar

Requer Python 3.12, FFmpeg e Deno 2.3 ou superior no `PATH` (o Deno permite
ao yt-dlp processar os desafios JavaScript do YouTube). Para instalar as
dependências Python:

  python3 -m venv venv
  venv/bin/pip install -r requirements.txt

O yt-dlp suporta muitos sites e também URLs diretos de manifests HLS/DASH,
mas a disponibilidade varia conforme o site e o vídeo. Conteúdo que exige
autenticação continua a exigir cookies válidos. O Docker instala FFmpeg e Deno.

  # Um URL
  python download.py https://www.rtp.pt/play/p1234/e56789/episodio-1

  # Vários URLs de uma vez
  python download.py https://rtp.pt/play/... https://tviplayer.iol.pt/...

  # A partir de um ficheiro (um URL por linha, # para comentários)
  python download.py -f lista.txt

  # Pasta de destino personalizada
  python download.py -f lista.txt -o /media/series

  # Ver formatos disponíveis antes de descarregar
  python download.py --list-formats https://www.rtp.pt/play/...
