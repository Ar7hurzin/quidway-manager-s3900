# Monitoramento contínuo local — Quidway S3900

Nesta versão, a coleta física
não depende de clicar em "Atualizar leitura". Ao iniciar o Manager no modo
serial, um worker consulta o console em segundo plano; a página apenas lê
`GET /api/dashboard`, sem usar a serial.

## Ativar

1. Mantenha o console físico ligado, com o dispositivo serial definido em
   `local-settings.ps1` (`QM_MODE='serial'`).
2. Inicie como antes: `.venv/bin/python start.py`.
3. Abra `http://127.0.0.1:8787/` e recarregue com Ctrl+Shift+R.
4. O topo mostra "Monitoramento ativo", "Lendo o switch", "CLI aberta · pausado"
   ou o motivo de uma falha.
5. O botão de atualização manual é ocultado somente no modo serial.
6. A aba CLI continua funcionando: com a CLI aberta, o worker não disputa a
   porta console. Ao encerrá-la, o monitoramento é retomado automaticamente.

## Ritmo e fidelidade dos dados

- A tela consulta o cache HTTP a cada ~2,5 segundos quando visível, e ~8
  segundos em segundo plano. Não executa comando serial a cada atualização
  visual.
- A coleta física executa pequenos lotes, com intervalo mínimo de ~4 segundos
  entre tentativas e pausa de ~12 segundos após erro. A duração total depende
  do console de 9600 baud, tamanho da resposta e paginação do firmware.
- Portas 1, 2 e 3 têm prioridade para o teste do YouTube; as demais são
  percorridas progressivamente.
- Uma consulta inválida não apaga MACs/VLANs/portas reconhecidos. Cada porta
  contém horário próprio; informação antiga não representa link atual.
- O S3900 reporta alguns indicadores como médias de **300 segundos**, por
  isso o painel é de atualização contínua, não telemetria instantânea.
- O worker usa somente comandos `display` de leitura. As configurações do
  switch não são modificadas.
- As observações ainda são mantidas **na memória do servidor** e se perdem
  ao parar o processo. Histórico persistente de testes requer implementação
  separada.

## Validação de código

```bash
cd ~/Projetos/quidway-manager-s3900
.venv/bin/python -m unittest discover -s tests -q
node --check static/app.js
```

Versão do código publicada na branch `feature/s3900-virtual`. Configurações locais e credenciais não são incluídas no repositório.
