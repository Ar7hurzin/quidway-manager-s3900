"""Read-only console serial collector with a fixed display-command allowlist."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass


class SerialReadOnlyError(RuntimeError):
    """Safe, user-facing serial connection or collection failure."""


@dataclass(frozen=True)
class SerialSettings:
    port: str
    baud_rate: int
    timeout_seconds: int

    @classmethod
    def from_environment(cls) -> "SerialSettings":
        port = os.getenv("QM_SERIAL_PORT", "COM9").strip()
        try:
            baud_rate = int(os.getenv("QM_SERIAL_BAUD", "9600"))
            timeout = int(os.getenv("QM_CONNECT_TIMEOUT", "10"))
        except ValueError as exc:
            raise SerialReadOnlyError("QM_SERIAL_BAUD e QM_CONNECT_TIMEOUT devem ser números inteiros.") from exc
        if not port:
            raise SerialReadOnlyError("Defina QM_SERIAL_PORT com a porta COM do cabo console.")
        if baud_rate not in {1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200}:
            raise SerialReadOnlyError("QM_SERIAL_BAUD deve ser uma velocidade serial comum entre 1200 e 115200.")
        if not 3 <= timeout <= 60:
            raise SerialReadOnlyError("QM_CONNECT_TIMEOUT deve estar entre 3 e 60 segundos.")
        return cls(port=port, baud_rate=baud_rate, timeout_seconds=timeout)


READ_ONLY_COMMANDS = ("display version", "display vlan")
PROMPT = re.compile(rb"(?m)(?:<[^>\r\n]+>|\[[^\]\r\n]+\])\s*$")
MORE_MARKERS = (b"---- More ----", b"More: ")


def _read_until_prompt(connection, timeout_seconds: int, max_bytes: int = 256_000) -> bytes:
    start = time.monotonic()
    data = bytearray()
    handled_more = 0
    while time.monotonic() - start < timeout_seconds:
        block = connection.read(4096)
        if block:
            data.extend(block)
            if len(data) > max_bytes:
                raise SerialReadOnlyError("A saída do switch excedeu o limite seguro de leitura.")
            marker_count = max((bytes(data).count(marker) for marker in MORE_MARKERS), default=0)
            if marker_count > handled_more:
                connection.write(b" ")
                handled_more = marker_count
            if PROMPT.search(bytes(data).rstrip()):
                return bytes(data)
    raise SerialReadOnlyError("Tempo esgotado esperando o prompt do console. Nenhuma configuração foi alterada.")


def _clean_output(raw: bytes, command: str) -> str:
    text = raw.decode("utf-8", errors="replace").replace("\r", "")
    lines = [line.rstrip() for line in text.splitlines()]
    if lines and command in lines[0]:
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and (lines[-1].strip().startswith("<") or lines[-1].strip().startswith("[")):
        lines.pop()
    return "\n".join(lines).strip()


def collect_serial_diagnostics(settings: SerialSettings) -> list[dict[str, str]]:
    try:
        import serial
    except ImportError as exc:
        raise SerialReadOnlyError("PySerial não está instalado. Execute start.ps1 para preparar as bibliotecas.") from exc

    try:
        with serial.Serial(
            port=settings.port,
            baudrate=settings.baud_rate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
            write_timeout=2,
        ) as connection:
            # Wake a quiet console with an empty line, then require a recognizable VRP prompt.
            connection.reset_input_buffer()
            connection.write(b"\r\n")
            _read_until_prompt(connection, settings.timeout_seconds)
            results: list[dict[str, str]] = []
            for command in READ_ONLY_COMMANDS:
                connection.write(command.encode("ascii") + b"\r\n")
                raw = _read_until_prompt(connection, settings.timeout_seconds)
                results.append({"command": command, "output": _clean_output(raw, command)})
            return results
    except SerialReadOnlyError:
        raise
    except Exception as exc:
        # SerialException varies by PySerial/platform; avoid leaking device paths or internals.
        raise SerialReadOnlyError(
            f"Não foi possível abrir ou consultar {settings.port}. Confira cabo, porta COM e se Tera Term/MobaXterm está fechado."
        ) from exc
