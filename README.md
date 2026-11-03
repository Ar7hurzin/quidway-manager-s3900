# Quidway Manager

Painel local em português para observar um switch Huawei Quidway S3900. A versão atual é um MVP de **demonstração e consultas SSH somente leitura**. O switch não está conectado e a compatibilidade com qualquer submodelo ou firmware ainda não foi confirmada.

## O que funciona

- Painel web com visão geral, mapa conceitual de portas, VLANs e diagnóstico SSH.
- Demonstração claramente identificada; os valores de exemplo não vêm de um equipamento.
- Modo SSH configurável por variáveis locais. O único conjunto de comandos permitido é `display version` e `display vlan`.
- Verificação estrita de chave SSH conhecida. Chave nova ou alterada é rejeitada.
- Uma credencial por vez: senha ou chave privada. A senha não é solicitada nem gravada pelo painel.
- Serviço web preso a `127.0.0.1`; cabeçalhos impedem cache do diagnóstico no navegador.

## O que ainda depende de conhecer o switch

Não há ainda parser confirmado para portas/VLANs, backup de configuração, séries históricas de tráfego ou escrita de configuração. Esses recursos exigem identificar o submodelo e firmware e validar, com o aparelho disponível, os comandos e formatos de saída. A interface não inventa esses dados.

## Iniciar

Abra a pasta `QuidwayManager` no VS Code. No terminal PowerShell dessa pasta, execute:

```powershell
.\start.ps1
```

O script prepara as bibliotecas na primeira execução e inicia o servidor. Abra `http://127.0.0.1:8787` no navegador. Deixe a janela do terminal aberta enquanto usar o app; pressione `Ctrl+C` para encerrá-lo. Se o app já estiver rodando, o script informa o endereço ativo.

## Acesso privado remoto pelo Tailscale

O Tailscale Serve coloca o painel HTTPS **dentro da tailnet**, sem abrir uma porta no roteador e sem usar Funnel. As regras de acesso da tailnet continuam valendo. O computador que hospeda o app precisa permanecer ligado, conectado ao Tailscale, e com o app iniciado.

1. Inicie o Quidway Manager com `.\start.ps1`.
2. Execute `.\enable-tailnet.ps1` no PowerShell.
3. Se o Tailscale informar que Serve ainda não está habilitado, abra o link oficial mostrado no terminal, entre na conta se necessário e aprove a ativação para a tailnet. Depois execute `.\enable-tailnet.ps1` novamente.
4. O terminal mostrará o endereço HTTPS privado. Em outro dispositivo, conecte-se à mesma tailnet e abra esse endereço.

O comando de configuração verifica se o app responde localmente e não substitui uma configuração Tailscale Serve já existente. Para conferir o compartilhamento, use `tailscale serve status`. Para desligar o compartilhamento, use `tailscale serve off`.

Consulte a documentação oficial de [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve). Serve compartilha o serviço com dispositivos autorizados pelas regras da tailnet; confirme que essas regras correspondem às pessoas que devem acessar o painel.

## Preparar SSH quando o switch estiver disponível

Primeiro, identifique modelo, versão do firmware, IP de gestão e usuário de leitura. Obtenha a chave pública SSH do switch por um canal local confiável e compare sua impressão digital presencialmente. Só depois inclua a chave conferida no arquivo `known_hosts`.

Copie `local-settings.example.ps1` para `local-settings.ps1` e preencha as variáveis de conexão. O arquivo local é ignorado pelo Git. Prefira uma chave privada protegida pelas permissões do Windows; se usar senha, defina `QM_SSH_PASSWORD` somente na sessão PowerShell atual em vez de salvá-la em arquivo.

Exemplo das variáveis necessárias:

```powershell
$env:QM_MODE = 'real'
$env:QM_SWITCH_HOST = 'IP_DE_GESTAO'
$env:QM_SWITCH_PORT = '22'
$env:QM_SWITCH_USER = 'USUARIO_DE_LEITURA'
$env:QM_KNOWN_HOSTS = 'C:\caminho\protegido\known_hosts'
$env:QM_SSH_KEY_FILE = 'C:\caminho\protegido\chave_privada'
```

Feche e inicie novamente o app após alterar configurações. No modo real, use **Atualizar leitura** para executar apenas as duas consultas permitidas. Uma falha de negociação SSH não reduz os algoritmos de segurança do computador.

## Limites de segurança

- O aplicativo não aceita comandos digitados pelo navegador e não altera a configuração do switch.
- Mantenha o servidor em `127.0.0.1`; o acesso remoto é intermediado pelo Tailscale Serve.
- Não crie encaminhamento de porta no roteador nem publique o painel com Tailscale Funnel.
- Não considere a compatibilidade, backup, monitoramento ou recuperação confirmados até validar o equipamento real.

## Documentação

- [Como conectar e usar (TXT)](docs/como-conectar-e-usar.txt)
- [Guia técnico (PDF)](docs/guia-tecnico.pdf) · [DOCX editável](docs/guia-tecnico.docx)
- [Guia de planejamento (PDF)](docs/guia-de-planejamento.pdf) · [DOCX editável](docs/guia-de-planejamento.docx)
- [Mockups conceituais](docs/mockups-conceituais.png)


