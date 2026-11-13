"""Serial execution for typed, reviewed Quidway configuration plans."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from serial_readonly import SerialReadOnlyError, SerialSettings
from switch_config import ConfigurationError, ConfigurationPlan
from switch_text import decode_switch_text, command_rejected


PROMPT = re.compile(rb"(?m)(?:<[^>\r\n]+>|\[[^\]\r\n]+\])\s*$")
CONFIRM = re.compile(rb"(?i)(?:\[\s*y\s*/\s*n\s*\]|\(y/n\)|\[y/n\])")
MORE_MARKERS = (b"---- More ----", b"More: ")


def _read_response(connection, timeout_seconds: int, allow_confirmation: bool = False, max_bytes: int = 512_000) -> tuple[bytes, bool]:
    started = time.monotonic()
    data = bytearray()
    handled_more = 0
    while time.monotonic() - started < timeout_seconds:
        block = connection.read(4096)
        if block:
            data.extend(block)
            if len(data) > max_bytes:
                raise ConfigurationError("A resposta do switch excedeu o limite seguro de leitura.")
            raw = bytes(data)
            if allow_confirmation and CONFIRM.search(raw):
                return raw, True
            marker_count = max((raw.count(marker) for marker in MORE_MARKERS), default=0)
            if marker_count > handled_more:
                connection.write(b" ")
                handled_more = marker_count
            if PROMPT.search(raw.rstrip()):
                return raw, False
    raise ConfigurationError("O switch não respondeu a tempo. Confira o prompt no console; não reenviei o comando.")


def _exchange(connection, command: str, settings: SerialSettings, allow_confirmation: bool = False) -> tuple[str, bool]:
    connection.write(command.encode("ascii") + b"\r\n")
    raw, confirmation = _read_response(connection, settings.timeout_seconds, allow_confirmation=allow_confirmation)
    text = decode_switch_text(raw).replace("\r", "").strip()
    if command_rejected(text):
        raise ConfigurationError(f"O switch rejeitou um comando do plano: {command}. O restante não foi enviado; confira a configuração em execução.")
    return text, confirmation


def _check_target(connection, settings: SerialSettings) -> str:
    response, _ = _exchange(connection, "display version", settings)
    if "Quidway 3900-SI 28-Port" not in response or "Version 3.10(CN), RELEASE 0002" not in response:
        raise ConfigurationError("O switch não corresponde ao perfil Quidway 3900-SI/VRP 3.10(CN) RELEASE 0002 reconhecido. Nenhuma alteração foi enviada.")
    return response


def _existing_vlans(connection, settings: SerialSettings) -> set[int]:
    response, _ = _exchange(connection, "display vlan", settings)
    vlan_ids: set[int] = set()

    # VRP 3.10 can report the VLAN inventory as a comma-separated list, e.g.
    # "1 (default), 10". Parse that section separately so every ID is kept.
    inventory = re.search(r"(?i)following VLAN exist\(s\):\s*", response)
    if inventory:
        for line in response[inventory.end():].splitlines():
            line = line.strip()
            if not line or line.startswith(("<", "[")):
                break
            if re.fullmatch(r"\d{1,4}(?:\s*\([^)]*\))?(?:\s*,\s*\d{1,4}(?:\s*\([^)]*\))?)*", line):
                vlan_ids.update(int(value) for value in re.findall(r"\d{1,4}", line))

    # Keep compatibility with tabular/detail formats used by other S3900
    # firmware builds and command output variants.
    vlan_ids.update(
        int(match.group(1))
        for match in re.finditer(r"(?mi)^\s*(\d{1,4})(?:\s*\(|\s+(?:common|static|dynamic|default)\b)", response)
    )
    vlan_ids.update(
        int(match.group(1))
        for match in re.finditer(r"(?mi)^\s*VLAN\s+ID\s*:\s*(\d{1,4})\b", response)
    )
    if not vlan_ids:
        raise ConfigurationError("Não consegui interpretar a lista atual de VLANs; nenhuma alteração foi enviada.")
    return vlan_ids


def _verify_vlan_preconditions(connection, settings: SerialSettings, plan: ConfigurationPlan) -> None:
    needs_catalog = plan.action in {
        "create_vlan", "rename_vlan", "delete_vlan", "set_access_port",
        "set_trunk_port", "set_hybrid_port", "set_management_ip",
    }
    if not needs_catalog:
        return
    existing = _existing_vlans(connection, settings)
    required: set[int] = set()
    for command in plan.commands:
        if command.startswith("vlan ") or command.startswith("undo vlan "):
            required.add(int(command.split()[-1]))
        elif command.startswith("port access vlan ") or command.startswith("port trunk pvid vlan ") or command.startswith("port hybrid pvid vlan "):
            required.add(int(command.split()[-1]))
        elif command.startswith("port trunk permit vlan "):
            required.update(int(part) for part in command.removeprefix("port trunk permit vlan ").split())
        elif command.startswith("port hybrid vlan "):
            match = re.fullmatch(r"port hybrid vlan ([0-9 ]+) (?:tagged|untagged)", command)
            if match:
                required.update(int(part) for part in match.group(1).split())
        elif command.startswith("interface Vlan-interface "):
            required.add(int(command.rsplit(" ", 1)[1]))

    if plan.action == "create_vlan":
        if required & existing:
            raise ConfigurationError(f"A VLAN {min(required & existing)} já existe. Atualize a prévia ou escolha outra ação.")
    else:
        missing = required - existing
        if missing:
            raise ConfigurationError("As VLANs necessárias não existem: " + ", ".join(str(vlan) for vlan in sorted(missing)) + ". Nenhuma mudança foi enviada.")


def _open(settings: SerialSettings):
    try:
        import serial
    except ImportError as exc:
        raise ConfigurationError("PySerial não está instalado. Execute start.ps1 para preparar as bibliotecas.") from exc
    try:
        connection = serial.Serial(
            port=settings.port,
            baudrate=settings.baud_rate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
            write_timeout=2,
        )
    except Exception as exc:
        raise ConfigurationError(f"Não foi possível abrir {settings.port}. Feche Tera Term/MobaXterm e confirme o cabo.") from exc
    return connection


def execute_plan(settings: SerialSettings, plan: ConfigurationPlan) -> list[dict[str, str]]:
    connection = _open(settings)
    output: list[dict[str, str]] = []
    try:
        connection.reset_input_buffer()
        connection.write(b"\r\n")
        _read_response(connection, settings.timeout_seconds)
        version = _check_target(connection, settings)
        output.append({"command": "display version", "output": version})
        _verify_vlan_preconditions(connection, settings, plan)
        for command in plan.commands:
            response, confirmation = _exchange(connection, command, settings, allow_confirmation=(command == "save"))
            if confirmation:
                response, _ = _exchange(connection, "y", settings)
            output.append({"command": command, "output": response})
        return output
    except ConfigurationError:
        # Best effort: leave a command subview without issuing any other configuration changes.
        try:
            connection.write(b"return\r\n")
        except Exception:
            pass
        raise
    finally:
        connection.close()


def export_current_configuration(settings: SerialSettings) -> tuple[bytes, str]:
    connection = _open(settings)
    try:
        connection.reset_input_buffer()
        connection.write(b"\r\n")
        _read_response(connection, settings.timeout_seconds)
        version = _check_target(connection, settings)
        response, _ = _exchange(connection, "display current-configuration", settings)
        content = ("; Backup de leitura do Quidway Manager\r\n; Criado em " + datetime.now(timezone.utc).isoformat() + "\r\n; Identificação do switch:\r\n" + version + "\r\n\r\n" + response).encode("utf-8", errors="replace")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return content, f"quidway-config-{timestamp}.txt"
    except SerialReadOnlyError as exc:
        raise ConfigurationError(str(exc)) from exc
    finally:
        connection.close()
