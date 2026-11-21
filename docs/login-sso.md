# Login SSO com 2FA (OpenID Connect)

O Quidway Manager não tem mais login local. Quem entra se autentica no provedor de identidade da organização (Microsoft Entra ID, Keycloak, Authentik, Google…) com autenticação de dois fatores. O aplicativo **nunca recebe, guarda ou calcula hash de senha de usuário**. Ele só confere um ID token assinado pelo provedor.

## Como funciona

1. **Entrar com SSO** envia o navegador ao provedor (Authorization Code + PKCE S256, com `state` e `nonce` de uso único, válidos por 10 minutos).
2. O provedor autentica a pessoa, com senha e 2FA, e devolve um código para `/auth/callback`.
3. O servidor troca o código pelo ID token e confere:
   - a assinatura, pelas chaves públicas (JWKS) do provedor. Só são aceitos algoritmos assimétricos; `none` e HS256 são recusados;
   - `iss`, `aud`/`azp`, `exp`, `iat` e `nonce`;
   - `email_verified = true` e o e-mail na allowlist `QM_OIDC_ALLOWED_EMAILS`;
   - **2FA**: o claim `amr` contém um dos valores de `QM_OIDC_MFA_AMR` (padrão `mfa,otp,totp,hwk,fido,webauthn`) **ou** o `acr` está em `QM_OIDC_ACCEPTED_ACR`. Se o token não prova 2FA, o login é recusado.
4. A sessão continua como antes: cookie HttpOnly, CSRF, 8 horas, revogável em **Sair**. Mudar o provedor, o client ID ou a allowlist revoga todas as sessões.

## Configurar

```bash
.venv/bin/python start.py configure        # Linux
.\configure-login.ps1                      # Windows
docker compose run --rm app configure      # Docker
```

O assistente pede o issuer, o client ID, o client secret (opcional: Enter cria um cliente público só com PKCE), os e-mails autorizados, a redirect URI e se o 2FA deve ser exigido. Ele grava `QM_OIDC_*` e uma nova `QM_SECRET_KEY` em `local-settings.ps1` e **apaga as antigas linhas `QM_AUTH_USERNAME`/`QM_AUTH_PASSWORD_HASH`**.

No provedor, registre um aplicativo **Web** com:

- Redirect URI: `http://127.0.0.1:8787/auth/callback`. Com outra porta ou acesso pelo Tailscale Serve, use a URL HTTPS da tailnet, por exemplo `https://pc.tailnet.ts.net/auth/callback`, e informe a mesma em `QM_OIDC_REDIRECT_URI`.
- Escopos: `openid email profile`.

| Variável | Obrigatória | Descrição |
|---|---|---|
| `QM_OIDC_ISSUER` | sim | URL do emissor, sempre https (http só em localhost) |
| `QM_OIDC_CLIENT_ID` | sim | ID do aplicativo no provedor |
| `QM_OIDC_CLIENT_SECRET` | não | Segredo do aplicativo (não é senha de usuário) |
| `QM_OIDC_ALLOWED_EMAILS` | sim | E-mails autorizados, separados por vírgula |
| `QM_OIDC_REDIRECT_URI` | não | Padrão `http://127.0.0.1:{QM_WEB_PORT}/auth/callback` |
| `QM_OIDC_REQUIRE_MFA` | não | Padrão `true` |
| `QM_OIDC_MFA_AMR` | não | Valores `amr` aceitos como 2FA |
| `QM_OIDC_ACCEPTED_ACR` | não | Valores `acr` aceitos como 2FA |
| `QM_OIDC_ACR_VALUES` | não | `acr_values` pedido ao provedor (ex. nível com OTP no Keycloak) |
| `QM_OIDC_PROVIDER_LOGOUT` | não | `true` também encerra a sessão no provedor ao sair (cadastre `…/login` como post-logout redirect) |

## 2FA por provedor

- **Microsoft Entra ID**: issuer `https://login.microsoftonline.com/<tenant-id>/v2.0`. Exija MFA por Acesso Condicional. O token traz `amr: ["pwd","mfa"]`, que o padrão já aceita.
- **Keycloak / Authentik**: configure o fluxo de login com OTP ou WebAuthn obrigatório. Inclua o claim `amr` (mapper "Authentication Method Reference") ou use um nível `acr` e defina `QM_OIDC_ACR_VALUES` e `QM_OIDC_ACCEPTED_ACR` com esse valor.
- **Google**: o ID token do Google **não informa** se houve 2FA. Com o padrão, todo login Google é recusado. Para usar Google, imponha a verificação em duas etapas no Admin do Workspace para todas as contas autorizadas e defina `QM_OIDC_REQUIRE_MFA=false` conscientemente. Nesse caso o aplicativo confia no provedor sem conseguir conferir o 2FA.

## Observações

- O servidor precisa alcançar o provedor (HTTPS de saída) no momento do login. `/api/health` e a página `/login` funcionam sem rede.
- O console serial físico continua sendo o acesso de emergência ao switch se o provedor estiver fora do ar.
