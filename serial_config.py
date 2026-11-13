"""Serial execution for typed, reviewed Quidway configuration plans."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from serial_readonly import SerialReadOnlyError, SerialSettings, open_connection
from switch_config import ConfigurationError, ConfigurationPlan
from switch_text import decode_switch_text, command_rejected


PROMPT = re.compile(rb"(?m)(?:<[^>\r\n]+>|\[[^\]\r\n]+\])\s*$")
CONFIRM = re.compile(rb"(?i)(?:\[\s*y\s*/\s*n\s*\]|\(y/n\)|\[y/n\])")
MORE_MARKERS = (b"---- More ----", b"More: ")


def _read_response(connection, timeout_seconds: int, allow_confirmation: bool = False, max_bytes: int = 512_000) -> tuple[bytes, bool]:
    started = time.monotonic()
    data = bytearray()
    handled_more = 0
    last_data = started
    while time.monotonic() - started < timeout_seconds:
        block = connection.read(4096)
        if block:
            last_data = time.monotonic()
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
        elif data and PROMPT.search(bytes(data).rstrip()) and time.monotonic() - last_data >= 0.20:
            return bytes(data), False
        else:
            time.sleep(0.01)
    raise ConfigurationError("O switch não respondeu a tempo. Confira o prompt no console; não reenviei o comando.")


def _exchange(connection, command: str, settings: SerialSettings, allow_confirmation: bool = False) -> tuple[str, bool]:
    connection.write(command.encode("ascii") + b"\r")
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


def _parse_vlan_inventory(response: str) -> set[int]:
    """Parse VRP inventory, including legacy trailing comma/control characters."""
    vlan_ids: set[int] = set()
    response = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", response)
    inventory = re.search(r"(?i)following VLAN exist\(s\):[ \t]*\r?\n?", response)
    if inventory:
        token = r"([0-9]{1,4})(?:[ \t]*\([^()\r\n]*\))?"
        row = re.compile(token + r"(?:[ \t]*,[ \t]*" + token + r")*[ \t]*,?")
        for raw_line in response[inventory.end():].split("\n"):
            line = raw_line.strip().rstrip("\x00\x07\x08\x1a\x7f\ufffd\u25a1\u25ab").rstrip()
            if not line:
                if vlan_ids:
                    break
                continue
            if line.startswith(("<", "[")) or not line[0].isdigit():
                break
            if not row.fullmatch(line):
                raise ConfigurationError("Não consegui interpretar toda a lista de VLANs; nenhuma alteração foi enviada.")
            for item in line.rstrip(",").split(","):
                match = re.fullmatch(token, item.strip())
                if not match or not 1 <= int(match.group(1)) <= 4094:
                    raise ConfigurationError("ID de VLAN inválido no retorno do switch; nenhuma alteração foi enviada.")
                vlan_ids.add(int(match.group(1)))
        # Never fall back to the first ID of a malformed comma-separated list.
    else:
        vlan_ids.update(
            int(match.group(1))
            for match in re.finditer(r"(?mi)^[ \t]*([0-9]{1,4})(?:[ \t]*\(|[ \t]+(?:common|static|dynamic|default)\b)", response)
        )
        vlan_ids.update(
            int(match.group(1))
            for match in re.finditer(r"(?mi)^[ \t]*VLAN[ \t]+ID[ \t]*:[ \t]*([0-9]{1,4})\b", response)
        )
    if not vlan_ids or any(not 1 <= v <= 4094 for v in vlan_ids):
        raise ConfigurationError("Não consegui interpretar a lista atual de VLANs; nenhuma alteração foi enviada.")
    return vlan_ids


def _existing_vlans(connection, settings: SerialSettings) -> set[int]:
    response, _ = _exchange(connection, "display vlan", settings)
    return _parse_vlan_inventory(response)


def _query_vlan_presence(connection, settings: SerialSettings, vlan: int) -> bool:
    """Verify the exact VLAN on legacy VRP; never infer absence from a bad list."""
    if not 1 <= vlan <= 4094:
        raise ConfigurationError("ID de VLAN inválido.")
    connection.write(f"display vlan {vlan}".encode("ascii") + b"\r")
    raw, _ = _read_response(connection, settings.timeout_seconds)
    text = decode_switch_text(raw).replace("\r", "")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    ids = {int(value) for value in re.findall(
        r"(?mi)^[ \t]*VLAN[ \t]+ID[ \t]*:[ \t]*([0-9]{1,4})\b", text
    )}
    if ids == {vlan} and not command_rejected(text):
        return True
    # Only an explicit, unambiguous missing-VLAN message can authorize creation.
    absent = re.search(
        rf"(?mi)^[ \t]*(?:%[ \t]*)?(?:Error:[ \t]*)?(?:The[ \t]+)?VLAN(?:[ \t]+{vlan})?[ \t]+(?:does[ \t]+not[ \t]+exist|doesn't[ \t]+exist|not[ \t]+found|does[ \t]+not[ \t]+exist[ \t]+in[ \t]+the[ \t]+system)[.!]?[ \t]*$",
        text,
    )
    if not ids and absent:
        return False
    raise ConfigurationError(
        f"Não consegui confirmar a VLAN {vlan} pela consulta display vlan {vlan}. "
        "Nenhuma alteração foi enviada; confira a resposta na CLI."
    )


def _verify_vlan_preconditions(connection, settings: SerialSettings, plan: ConfigurationPlan) -> None:
    needs_catalog = plan.action in {
        "create_vlan", "rename_vlan", "delete_vlan", "set_access_port",
        "set_trunk_port", "set_hybrid_port", "set_management_ip",
    }
    if not needs_catalog:
        return
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

    if not required:
        raise ConfigurationError("Não consegui identificar as VLANs do plano; nenhuma alteração foi enviada.")
    if settings.virtual:
        existing = _existing_vlans(connection, settings)
    else:
        # The physical VRP 3.10 returns a reliable per-VLAN detail, even when
        # its global inventory contains unexpected terminal characters.
        existing = {vlan for vlan in sorted(required)
                    if _query_vlan_presence(connection, settings, vlan)}

    if plan.action == "create_vlan":
        if required & existing:
            raise ConfigurationError(f"A VLAN {min(required & existing)} já existe. Atualize a prévia ou escolha outra ação.")
    else:
        missing = required - existing
        if missing:
            raise ConfigurationError("As VLANs necessárias não existem: " + ", ".join(str(vlan) for vlan in sorted(missing)) + ". Nenhuma mudança foi enviada.")


def _open(settings: SerialSettings):
    try:
        connection = open_connection(settings)
    except Exception as exc:
        raise ConfigurationError(f"Não foi possível abrir {settings.port}. Feche Tera Term/MobaXterm e confirme o cabo.") from exc
    return connection


def execute_plan(settings: SerialSettings, plan: ConfigurationPlan) -> list[dict[str, str]]:
    connection = _open(settings)
    output: list[dict[str, str]] = []
    try:
        connection.reset_input_buffer()
        connection.write(b"\r")
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
            connection.write(b"return\r")
        except Exception:
            pass
        raise
    finally:
        connection.close()


def export_current_configuration(settings: SerialSettings) -> tuple[bytes, str]:
    connection = _open(settings)
    try:
        connection.reset_input_buffer()
        connection.write(b"\r")
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
