# Testes executados — Painel de HDs (v1.0.0 + v1.0.1)

Data: 10/11/2026. Ambiente: Ubuntu 24.04, Python 3.12 (só stdlib), porta 8765.
Método: API + inventários reais de pastas de teste criadas para isso
(`test-drives/`, removidas após os testes). O catálogo foi entregue **vazio**,
para o usuário começar com os drives reais dele — sem inventar drives.

## v1.0.1 — correção encontrada no teste real no Windows (10/11/2026)
- **Sintoma:** no PC do usuário, o .exe abriu o painel e detectou o HD "Arquivos (4TB)" (E:\, NTFS, UUID) — mas a leitura terminou com **0 arquivos e 0 pastas**, num drive com 1,6 TB usados, e a interface dizia "Leitura concluída. Já pode ejetar".
- **Causa:** o `_scan_worker` pulava todo item cujo `st_dev` diferisse do `st_dev` da raiz — check pensado para não atravessar para outro volume. No Windows o `st_dev` não distingue volumes de forma confiável e o check pulou **tudo**, em silêncio (só avisos, sem erros — por isso nada acusou).
- **Correção:** check de dispositivo agora só vale em POSIX (Linux/macOS); no Windows fica desligado. Além disso, uma leitura que volta 0 arquivos e 0 pastas num **volume** (ponto de montagem) com mais de 1 GB usado não é mais registrada como concluída: fica como falha, com aviso, sem apagar o último inventário válido. Pasta comum vazia não entra no guarda (o uso medido seria o do disco inteiro).
- **Interface:** avisos/erros da leitura agora aparecem no aviso de conclusão, e há um aviso próprio para leitura que falhou.
- **Regressão (VM Linux, v1.0.1):** pasta com 4 arquivos/3 pastas → leitura completa, busca achou os arquivos; pasta vazia → concluída normalmente (guarda não disparou, como esperado); guarda simulada com 1,6 TB usados dispara para raiz de volume e não dispara para pasta comum; `node --check` em app.js OK; index/app.js 200. Catálogo de teste limpo depois (0 drives).

## Resultado geral: todos os fluxos principais passaram

| # | Teste | Resultado |
|---|---|---|
| 1 | Detecção real do SO (`/api/system/drives`, `lsblk`) | ✅ Passou — neste VM não há volume externo montado; retornou lista vazia honesta, sem inventar drives |
| 2 | Cadastro duplicado bloqueado (mesma pasta 2x) | ✅ Passou — 2ª tentativa devolveu 409 “já está catalogada” |
| 3 | Inventário rápido (só nomes/tamanhos/datas) | ✅ Passou — drive 1: 7 arquivos/5 pastas; drive 2: 3 arquivos/5 pastas |
| 4 | Link simbólico para fora do volume | ✅ Passou — ignorado e registrado em `warnings` (“aponta para fora do volume”) |
| 5 | Hard link não contado 2x | ✅ Passou — tamanho da pasta Fotos: 15.460 B (sem duplicar o hard link); antes do ajuste era 15.920 B |
| 6 | Capacidade compartilhada não somada 2x | ✅ Passou — 2 pastas no mesmo disco: total 161 GB (antes do ajuste: 322 GB) |
| 7 | Interrupção durante o inventário | ✅ Passou — scan de 6.300 arquivos interrompido aos 1.498: status `interrupted`, `last_scan_id` permaneceu nulo (último inventário válido mantido); releitura completa depois: 6.300 arquivos, `completed` |
| 8 | Preservação de etiquetas na releitura | ✅ Passou — etiqueta alterada para `HD-01-EDITADA` + finalidade, releitura feita, etiqueta e finalidade intactas, `last_scan_id` atualizado |
| 9 | Busca com drive desconectado | ✅ Passou — pasta renomeada (simula desplugue): `connected=false`, busca por `foto-capa` achou 2 resultados no inventário; releitura bloqueada com 409; ao reconectar, `connected=true` |
| 10 | Candidatos ordenados por economia potencial | ✅ Passou — 2 grupos de 5.000 B cada, `total_potential` 10.000 B; pares: HD Fotos 01 ↔ SSD Vídeos 02 = 1 correspondência |
| 11 | Candidato ≠ confirmado (SHA-256) | ✅ Passou — `foto-diferente.jpg` (mesmo nome+tamanho, conteúdo diferente) ficou só como candidato; `foto-capa.jpg` (mesmo conteúdo) e `arquivo-grande.bin` + `copia-renomeada.bin` (**nomes diferentes**, mesmo conteúdo) foram confirmados por SHA-256: 2 grupos confirmados |
| 12 | Pausar/retomar verificação | ✅ Passou — pausa e retomada preservam os 6 arquivos já verificados; pausa após conclusão devolve 409 (não sobrescreve o estado `completed`) |
| 13 | Planejamento (simulação) | ✅ Passou — liberar HD Fotos 01: pastas Fotos + Backup alocadas no SSD Vídeos 02, ocupação prevista 5,9%, margem de 10% mantida; nada foi movido |
| 14 | Exportação CSV/JSON | ✅ Passou — inventário CSV, duplicatas JSON (4 linhas), confirmadas JSON (4 linhas) |
| 15 | Atalho anti-duplicata | ✅ Passou — `iniciar-painel.sh` com o servidor já no ar responde “já está rodando” e não cria segundo servidor |
| 16 | Bugs encontrados e corrigidos | `errors, warnings = []` (desempacotamento inválido travava o scan); INSERT com 20 valores/19 colunas; INSERT da verificação com 5 valores/4 colunas; pausa após conclusão sobrescrevia `completed` |

## O que NÃO foi testado aqui (depende do computador do usuário)

- Conectar um HD/SSD físico via USB e ver o UUID real aparecer em **Detectar drives**
  (neste VM não há USB). O caminho de detecção é o mesmo usado nos testes (`lsblk` no
  Linux, `Get-Volume` no Windows, `diskutil` no macOS).
- Leitura integral de vários terabytes (a fila mostra o tamanho total antes de iniciar).
