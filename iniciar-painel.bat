@echo off
REM Painel de HDs — @ginhocarvalho
REM Inicia o painel local (http://127.0.0.1:8765) sem criar servidores duplicados.
REM Se algo falhar, esta janela mostra o motivo em vez de fechar sozinha.
setlocal
cd /d "%~dp0"
if not exist data mkdir data
set URL=http://127.0.0.1:8765

REM 0) Ignorar configuracao antiga e conflitante do Python (PYTHONHOME/PYTHONPATH
REM    apontando para outra instalacao e o motivo classico do erro
REM    "Failed to import encodings"). So vale para esta janela e para o servidor.
set PYTHONHOME=
set PYTHONPATH=

REM 1) O painel ja esta rodando?
curl -fsS --max-time 2 %URL%/api/health >nul 2>&1
if not errorlevel 1 (
  echo Painel de HDs ja esta rodando em %URL% — abrindo essa instancia.
  start %URL%
  exit /b 0
)

REM 2) Achar um Python que realmente funciona.
REM    Nao basta existir: testamos com "import encodings", que e o que quebra
REM    quando ha duas instalacoes brigando (ex.: C:\Python314 x AppData).
REM    O lancador "py" vem primeiro: ele usa o registro do Windows e acha
REM    a instalacao certa.
set PY=
py -c "import encodings" >nul 2>&1
if not errorlevel 1 set PY=py
if not defined PY (
  python -c "import encodings" >nul 2>&1
  if not errorlevel 1 set PY=python
)
if not defined PY (
  echo.
  echo Nenhum Python funcionando foi encontrado.
  echo O mais provavel e a instalacao do Python estar danificada ou duplicada.
  echo Reinstale o Python em https://www.python.org/downloads/
  echo Na instalacao, marque a caixa "Add python.exe to PATH".
  echo Se existir uma pasta C:\Python314 solta, apague-a ou renomeie-a antes.
  echo Depois rode este arquivo de novo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

REM 3) Iniciar o servidor numa janela propria (nao fechar essa janela enquanto usar o painel)
echo Iniciando o Painel de HDs com %PY% ...
start "Painel de HDs" cmd /c "%PY% painel-hds.py > data\servidor.log 2>&1"

REM 4) Esperar o servidor responder (ate ~30 segundos) antes de abrir o navegador
for /L %%i in (1,1,30) do (
  timeout /t 1 /nobreak >nul
  curl -fsS --max-time 2 %URL%/api/health >nul 2>&1
  if not errorlevel 1 goto :ok
)
echo.
echo O servidor nao respondeu. O erro esta em data\servidor.log:
echo ---------------------------------------------------------------
type data\servidor.log
echo ---------------------------------------------------------------
echo Me mande um print desta janela que eu te ajudo.
pause
exit /b 1

:ok
start %URL%
echo.
echo Painel aberto em %URL%
echo Para encerrar: feche a janela "Painel de HDs" ou pressione Ctrl+C nela.
echo Catalogo SQLite: %CD%\data\catalog.db
echo Pode fechar esta janela — o painel continua rodando na janela "Painel de HDs".
pause
