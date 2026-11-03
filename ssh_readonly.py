"""Conservative SSH reader: only sends a small fixed allowlist of display commands."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path


class SSHReadOnlyError(RuntimeError):
    """Safe, user-facing connection or collection failure."""


@dataclass(frozen=True)
class SwitchSettings:
    host: str
    port: int
    username: str
    password: str | None
    key_filename: str | None
    known_hosts_file: str | None
    timeout_seconds: int

    @classmethod
    def from_environment(cls) -> "SwitchSettings":
        try:
            port = int(os.getenv("QM_SWITCH_PORT", "22"))
            timeout = int(os.getenv("QM_CONNECT_TIMEOUT", "10"))
        except ValueError as exc:
            raise SSHReadOnlyError("QM_SWITCH_PORT e QM_CONNECT_TIMEOUT devem ser números inteiros.") from exc
        if not 1 <= port <= 65535:
            raise SSHReadOnlyError("A porta SSH deve estar entre 1 e 65535.")
        if not 3 <= timeout <= 60:
            raise SSHReadOnlyError("O timeout deve estar entre 3 e 60 segundos.")
        return cls(
            host=os.getenv("QM_SWITCH_HOST", "").strip(),
            port=port,
            username=os.getenv("QM_SWITCH_USER", "").strip(),
            password=os.getenv("QM_SSH_PASSWORD") or None,
            key_filename=os.getenv("QM_SSH_KEY_FILE") or None,
            known_hosts_file=os.getenv("QM_KNOWN_HOSTS") or None,
            timeout_seconds=timeout,
        )


# Never accept commands from the browser. Keep the initial live path query-only.
READ_ONLY_COMMANDS = ("display version", "display vlan")
PROMPT = re.compile(rb"(?m)(?:<[^>\r\n]+>|\[[^\]\r\n]+\])\s*$")


def _read_until_prompt(channel, timeout_seconds: int, max_bytes: int = 256_000) -> bytes:
    start = time.monotonic()
    data = bytearray()
    while time.monotonic() - start < timeout_seconds:
        if channel.recv_ready():
            block = channel.recv(8192)
            if not block:
                break
            data.extend(block)
            if len(data) > max_bytes:
                raise SSHReadOnlyError("A saída do switch excedeu o limite seguro de leitura.")
            if PROMPT.search(bytes(data).rstrip()):
                return bytes(data)
        elif channel.closed or channel.exit_status_ready():
            break
        else:
            time.sleep(0.08)
    raise SSHReadOnlyError("Tempo esgotado esperando a resposta do switch. Nenhuma configuração foi alterada.")


def _clean_output(raw: bytes, command: str) -> str:
    text = raw.decode("utf-8", errors="replace").replace("\r", "")
    lines = [line.rstrip() for line in text.splitlines()]
    # Remove the command echo and final prompt without changing the device response.
    if lines and command in lines[0]:
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and (lines[-1].strip().startswith("<") or lines[-1].strip().startswith("[")):
        lines.pop()
    return "\n".join(lines).strip()


def collect_diagnostics(settings: SwitchSettings) -> list[dict[str, str]]:
    if not settings.host or not settings.username:
        raise SSHReadOnlyError("Defina QM_SWITCH_HOST e QM_SWITCH_USER antes de conectar.")
    if bool(settings.password) == bool(settings.key_filename):
        raise SSHReadOnlyError("Defina exatamente uma credencial: QM_SSH_PASSWORD ou QM_SSH_KEY_FILE.")
    if not settings.known_hosts_file:
        raise SSHReadOnlyError("Defina QM_KNOWN_HOSTS com um arquivo de chaves SSH já verificado; o app não aceita chaves automaticamente.")
    known_hosts = Path(settings.known_hosts_file).expanduser()
    if not known_hosts.is_file():
        raise SSHReadOnlyError("O arquivo QM_KNOWN_HOSTS não foi encontrado.")
    if settings.key_filename and not Path(settings.key_filename).expanduser().is_file():
        raise SSHReadOnlyError("O arquivo da chave privada não foi encontrado.")

    try:
        import paramiko
    except ImportError as exc:
        raise SSHReadOnlyError("Paramiko não está instalado. Execute start.ps1 para preparar o ambiente local.") from exc

    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
        client.load_host_keys(str(known_hosts))
        # StrictHostKeyChecking behavior: reject an unknown or changed host key.
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            hostname=settings.host,
            port=settings.port,
            username=settings.username,
            password=settings.password,
            key_filename=str(Path(settings.key_filename).expanduser()) if settings.key_filename else None,
            timeout=settings.timeout_seconds,
            banner_timeout=settings.timeout_seconds,
            auth_timeout=settings.timeout_seconds,
            allow_agent=False,
            look_for_keys=False,
        )
        channel = client.invoke_shell(term="vt100", width=180, height=48)
        channel.settimeout(settings.timeout_seconds)
        _read_until_prompt(channel, settings.timeout_seconds)
        results: list[dict[str, str]] = []
        for command in READ_ONLY_COMMANDS:
            channel.send(command + "\r\n")
            raw = _read_until_prompt(channel, settings.timeout_seconds)
            results.append({"command": command, "output": _clean_output(raw, command)})
        channel.close()
        return results
    except paramiko.AuthenticationException as exc:
        raise SSHReadOnlyError("O switch recusou a autenticação. Confira usuário, credencial e permissões de leitura.") from exc
    except paramiko.BadHostKeyException as exc:
        raise SSHReadOnlyError("A chave do switch não corresponde ao arquivo known_hosts; interrompido por segurança.") from exc
    except paramiko.SSHException as exc:
        raise SSHReadOnlyError("Falha na negociação SSH. Pode haver incompatibilidade de firmware ou chave. Não enfraqueci os algoritmos do cliente.") from exc
    except (OSError, TimeoutError, EOFError) as exc:
        raise SSHReadOnlyError("Não foi possível alcançar ou autenticar no switch. Confira rede, SSH e credenciais.") from exc
    finally:
        client.close()
