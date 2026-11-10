# Painel de HDs — Idealizado por @ginhocarvalho

Aplicativo **local** para catalogar HDs e SSDs externos, descobrir o que está em cada um,
localizar possíveis arquivos repetidos e planejar a distribuição para liberar espaço.

- **Sem internet para funcionar.** Só biblioteca padrão Python. Nada é enviado para serviços externos.
- **Somente leitura nos drives de origem.** Não apaga, move, formata ou sobrescreve arquivos.
- **Banco local:** `data/catalog.db` (SQLite), fora dos drives analisados. O catálogo continua pesquisável com os drives desconectados.
- **Endereço local:** http://127.0.0.1:8765 — não é publicado na internet.

## Versão .exe portátil (Windows, sem precisar de Python)

O `.exe` é gerado pelo GitHub Actions (veja `build-windows-exe.yml` — o arquivo
de workflow precisa ser criado pela web no GitHub, em
`.github/workflows/build-windows-exe.yml`).

- Baixe o artefato **PainelHDs-Windows** da build verde e extraia o
  `PainelHDs.exe` para uma pasta (ex.: `C:\PainelHDs`).
- Clique duas vezes no `PainelHDs.exe`. Ele abre o painel no navegador sozinho.
- Na primeira execução, o Windows SmartScreen avisa "editor desconhecido"
  (o programa não é assinado): clique em **"Mais informações"** e depois em
  **"Executar mesmo assim"**.
- O catálogo fica em `data\catalog.db` **ao lado do .exe**. Não apague a pasta
  `data` — é onde ficam seus drives catalogados.
- Para encerrar: feche a janela do console do PainelHDs.
- Cada build gera um artefato com o mesmo nome: apague o `.exe` antigo antes
  de testar um novo.

## Como iniciar (com Python)

**Linux / macOS:**
```bash
bash iniciar-painel.sh
# ou
./iniciar-painel.sh
```

**Windows:** clique duas vezes em `iniciar-painel.bat`.

O atalho verifica se o painel já está rodando na porta 8765. Se já estiver, ele só abre o navegador — não cria um segundo servidor.

**Se o navegador abrir e disser que não consegue acessar:** o servidor não subiu.
Não feche a janela preta — o `iniciar-painel.bat` novo mostra o motivo nela
(Python não instalado, por exemplo) e, se faltar o Python, ele mesmo abre a
página para baixar. Instale o Python marcando **"Add python.exe to PATH"** e
rode o `.bat` de novo. Enquanto o painel estiver em uso, deixe aberta a janela
chamada **"Painel de HDs"** (é o servidor); a outra janela pode ser fechada.

Depois abra http://127.0.0.1:8765 no navegador.

## Como encerrar

Feche a janela do terminal onde o painel está rodando, ou pressione `Ctrl+C` nela.
O catálogo já está salvo em `data/catalog.db` — pode fechar sem medo.

## Como recuperar o catálogo

- **Backup:** copie `data/catalog.db` (e, se existir, `data/catalog.db-wal`) para um lugar seguro, com o painel encerrado.
- **Restaurar:** feche o painel, substitua `data/catalog.db` pelo backup e inicie de novo.
- **Recomeçar do zero:** feche o painel e apague `data/catalog.db*`. Na próxima abertura, o catálogo começa vazio.

## Como usar (resumo)

1. **Conecte um drive por vez** → Visão geral → **Detectar drives** → **Catalogar**.
   O drive é reconhecido pelo UUID (identidade persistente), não pela letra ou pelo nome.
   Dê uma etiqueta física (ex.: HD-01) e edite nome/finalidade — isso muda só o catálogo.
2. **Fazer inventário:** com o drive conectado, clique em **Fazer inventário** e acompanhe o progresso.
   A leitura lê só nomes, caminhos, tamanhos e datas — não lê o conteúdo.
   Quando aparecer **“Leitura concluída”**, ejete o drive pelo sistema operacional.
   Se a leitura for interrompida, o último inventário válido é mantido.
3. **Encontrar arquivos:** Explorador → navegue por pasta, veja o mapa de espaço e use a busca global
   (funciona mesmo com o drive desconectado).
4. **Duplicatas:** a aba Duplicatas mostra candidatos por nome + tamanho, ordenados pela maior
   economia potencial. **Candidato não é prova.** Na aba Confirmação, a verificação por SHA-256
   lê o conteúdo e separa confirmados de candidatos. Dá para pausar e retomar.
5. **Planejar:** escolha um drive para liberar, ajuste a margem de espaço livre e simule.
   É só uma simulação — nada é movido.

O passo a passo completo está na página **Como usar** dentro do próprio painel.

## O que o painel detecta de verdade

- **Linux:** `lsblk` (UUID, sistema de arquivos, tamanho, transporte USB/SATA/NVMe, rotacional) + `/proc/mounts` + uso de disco.
- **Windows:** PowerShell `Get-Volume` (UniqueId, letra, sistema de arquivos, tamanho).
- **macOS:** `diskutil` (Volume UUID).

Tipo HD/SSD só é exibido quando há informação confiável; caso contrário, mostra **“Tipo não identificado”**.
Volumes sem UUID nem número de série são sinalizados como **identidade ambígua**.

## Exportação

Na Visão geral e em Duplicatas há botões para exportar o inventário e a análise em **CSV e JSON**
(`/api/export?kind=inventory|duplicates|verified&format=csv|json`).

## Arquivos

| Arquivo | Papel |
|---|---|
| `painel-hds.py` | Servidor local (Python, só stdlib) + API + SQLite |
| `static/index.html` | Interface em português |
| `static/app.js` | Lógica das telas |
| `static/style.css` | Visual do painel |
| `iniciar-painel.sh` / `.bat` | Atalho de início com anti-duplicata |
| `data/catalog.db` | Catálogo (criado na primeira execução) |

Idealizado por [@ginhocarvalho](https://www.instagram.com/ginhocarvalho/)
