"""Read-only physical S3900 dashboard. Unknown observations stay unknown."""
from __future__ import annotations

import copy
import re
import time
from datetime import datetime, timezone

from serial_readonly import (SerialReadOnlyError, _read_until_prompt, _clean_output,
                             open_connection)
from serial_config import _parse_vlan_inventory
from switch_config import ConfigurationError
from switch_text import command_rejected

INTERVAL = 12
PORT_BATCH = 3
VLAN_BATCH = 2


def now():
    return datetime.now(timezone.utc).isoformat()


def interface_name(n):
    return f"Ethernet1/0/{n}" if n <= 24 else f"GigabitEthernet1/1/{n - 24}"


def terminal_text(text):
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text).replace("\r", "").replace("\x00", "")


def _field(text, pattern):
    m = re.search(pattern, text, re.I | re.M)
    return m.group(1).strip() if m else None


def parse_identity(text):
    text = terminal_text(text)
    model = _field(text, r"\b(Quidway[ \t]+3900-SI[ \t]+28-Port)\b")
    version = _field(text, r"\b(Version[ \t]+3\.10\(CN\),[ \t]+RELEASE[ \t]+0002)\b")
    if not model or not version:
        raise SerialReadOnlyError("Identificação do S3900 incompleta ou incompatível; não vou atribuir dados a outro modelo.")
    return {"model": model, "version": version,
            "hardware": _field(text, r"^[ \t]*Hardware Version is[ \t]+([^\n]+)"),
            "cpld": _field(text, r"^[ \t]*CPLD Version is[ \t]+([^\n]+)"),
            "bootrom": _field(text, r"^[ \t]*Bootrom Version is[ \t]+([^\n]+)")}


def parse_port(text, number):
    text = terminal_text(text)
    interface = interface_name(number)
    link = _field(text, rf"^[ \t]*{re.escape(interface)}[ \t]+(?:current[ \t]+state[ \t]*:|is)[ \t]*(UP|DOWN|Administratively[ \t]+DOWN)\b")
    pvid = _field(text, r"^[ \t]*PVID[ \t]*:[ \t]*(\d{1,4})\b")
    mode = _field(text, r"^[ \t]*Port[ \t]+link-type[ \t]*:[ \t]*(access|trunk|hybrid)\b")
    desc = _field(text, r"^[ \t]*Description[ \t]*:[ \t]*([^\n]+)")
    tagged = _field(text, r"^[ \t]*Tagged[ \t]+VLAN[ \t]+ID[ \t]*:[ \t]*([^\n]+)")
    untagged = _field(text, r"^[ \t]*Untagged[ \t]+VLAN[ \t]+ID[ \t]*:[ \t]*([^\n]+)")
    rx = _field(text, r"^[ \t]*Last[ \t]+\d+[ \t]+seconds[ \t]+input:[ \t]*\d+[ \t]+packets/sec[ \t]+(\d+)[ \t]+bytes/sec")
    tx = _field(text, r"^[ \t]*Last[ \t]+\d+[ \t]+seconds[ \t]+output:[ \t]*\d+[ \t]+packets/sec[ \t]+(\d+)[ \t]+bytes/sec")
    if not any(v is not None for v in (link, pvid, mode, desc, tagged, untagged, rx, tx)):
        return None
    return {"number": number, "interface": interface,
            "state": ("up" if link.lower() == "up" else "down") if link else None,
            "admin": False if link and "administratively" in link.lower() else None,
            "pvid": int(pvid) if pvid and 1 <= int(pvid) <= 4094 else None,
            "mode": mode.lower() if mode else None, "description": desc,
            "tagged": tagged, "untagged": untagged,
            "rx_bps": int(rx)*8 if rx is not None else None,
            "tx_bps": int(tx)*8 if tx is not None else None, "collected_at": now()}


def parse_brief(text):
    """Accept only full, recognized interface IDs and explicit state columns."""
    result = {}
    for n in range(1, 29):
        interface = interface_name(n)
        m = re.search(rf"(?mi)^[ \t]*{re.escape(interface)}[ \t]+(UP|DOWN|ADM|ADMIN[ \t]+DOWN)\b", terminal_text(text))
        if m:
            result[n] = {"state": "up" if m[1].lower() == "up" else "down",
                         "admin": False if m[1].lower().startswith("adm") else None,
                         "link_at": now()}
    return result


def parse_vlan_detail(text, vlan):
    text = terminal_text(text)
    actual = _field(text, r"^[ \t]*VLAN[ \t]+ID[ \t]*:[ \t]*(\d{1,4})\b")
    if actual is None or int(actual) != vlan:
        return None
    name = _field(text, r"^[ \t]*(?:VLAN[ \t]+)?Description[ \t]*:[ \t]*([^\n]+)")
    tagged = _field(text, r"^[ \t]*Tagged[ \t]+Ports[ \t]*:[ \t]*([^\n]+)")
    untagged = _field(text, r"^[ \t]*Untagged[ \t]+Ports[ \t]*:[ \t]*([^\n]+)")
    members = []
    for label, value in (("tagged", tagged), ("untagged", untagged)):
        if value and value.lower() != "none":
            members.append(f"{label}: {value}")
    return {"id": vlan, "name": name, "members": "; ".join(members) if members else ("Nenhuma" if tagged and untagged and tagged.lower() == untagged.lower() == "none" else None),
            "note": "Console", "collected_at": now()}


def parse_mac_table(text):
    """Recognize legacy learned-MAC rows; never guess from arbitrary text."""
    text = terminal_text(text)
    pattern = re.compile(
        r"(?mi)^[ \t]*([0-9a-f]{4}(?:-[0-9a-f]{4}){2}|[0-9a-f]{2}(?::[0-9a-f]{2}){5})"
        r"[ \t]+([0-9]{1,4})[ \t]+(?:learned|dynamic)[ \t]+"
        r"((?:GigabitEthernet|Ethernet)1/[01]/[0-9]{1,2})\b"
    )
    rows = []
    interfaces = {interface_name(n).lower(): n for n in range(1, 29)}
    for m in pattern.finditer(text):
        port = interfaces.get(m[3].lower())
        vlan = int(m[2])
        if port is None or not 1 <= vlan <= 4094:
            continue
        mac = re.sub(r"[:-]", "", m[1]).lower()
        mac = ":".join(mac[i:i+2] for i in range(0,12,2))
        rows.append({"mac": mac, "vlan": vlan, "port": port})
    if rows:
        return {"entries": rows, "count": len({r["mac"] for r in rows}), "collected_at": now()}
    if re.search(r"(?mi)^[ \t]*(?:Total(?:[ \t]+MAC[ \t]+addresses?|[ \t]+items?|[ \t]+entries)?[ \t]*:[ \t]*0|No[ \t]+MAC[ \t]+address(?:es)?(?:[ \t]+found)?)[.!]?[ \t]*$", text):
        return {"entries": [], "count": 0, "collected_at": now()}
    return None


class PhysicalDashboard:
    """Single-process cache, accessed under the application's serial request lock."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.data = None
        self.outputs = []
        self.last_attempt = 0.0
        self.port_cursor = 0
        self.vlan_cursor = 0
        self.brief_supported = None
        self.mac_attempt = 0.0
        self.priority_ports = set()
        self.priority_vlans = set()
        self.settings_key = None
        self.dirty = True
        self.error = None

    def invalidate(self, plan=None):
        self.dirty = True
        self.last_attempt = 0.0
        self.mac_attempt = 0.0
        if plan is None:
            self.port_cursor = 0
            self.vlan_cursor = 0
            return
        for command in plan.commands:
            for n in range(1, 29):
                if command.lower() == ("interface " + interface_name(n)).lower():
                    self.priority_ports.add(n)
                    break
            if re.fullmatch(r"(?:undo )?vlan [0-9]+|interface Vlan-interface [0-9]+|port (?:access vlan|trunk pvid vlan|hybrid pvid vlan) [0-9]+", command):
                vlan = int(command.rsplit(" ", 1)[1])
                if 1 <= vlan <= 4094:
                    self.priority_vlans.add(vlan)

    def cached(self):
        return {"snapshot": copy.deepcopy(self.data), "outputs": copy.deepcopy(self.outputs),
                "stale": self.dirty or bool(self.error) or time.monotonic() - self.last_attempt > INTERVAL * 3,
                "message": self.error, "collected_at": self.data.get("collected_at") if self.data else None}

    def collect(self, settings, force=False):
        key = (settings.port, settings.baud_rate)
        if self.settings_key != key:
            self.reset()
            self.settings_key = key
        if not force and not self.dirty and time.monotonic() - self.last_attempt < INTERVAL:
            return self.cached()
        self.last_attempt = time.monotonic()
        self.error = None
        outputs = []
        # Work on a private copy; publish atomically after collection.
        data = copy.deepcopy(self.data) if self.data else {"ports": [
            {"number": n, "interface": interface_name(n), "state": None, "admin": None,
             "pvid": None, "mode": None, "description": None, "tagged": None,
             "untagged": None, "rx_bps": None, "tx_bps": None, "collected_at": None}
            for n in range(1, 29)], "vlans": None}
        notes = []
        def query(connection, command):
            connection.write(command.encode("ascii") + b"\r")
            raw = _read_until_prompt(connection, settings.timeout_seconds)
            output = _clean_output(raw, command)
            outputs.append({"command": command, "output": output})
            return None if command_rejected(output) else output
        try:
            with open_connection(settings) as connection:
                connection.reset_input_buffer()
                connection.write(b"\r")
                _read_until_prompt(connection, settings.timeout_seconds)
                identity = parse_identity(query(connection, "display version") or "")
                data.update(identity, name=identity["model"])
                # Inventory is independent of optional details. Never retain a
                # previous count as a fresh observation if parsing fails.
                try:
                    inventory = _parse_vlan_inventory(query(connection, "display vlan") or "")
                    prior = {v["id"]: v for v in data["vlans"] or []}
                    data["vlans"] = [prior.get(v, {"id": v, "name": None, "members": None, "note": "Console", "collected_at": None}) for v in sorted(inventory)]
                except ConfigurationError as exc:
                    data["vlans"] = None
                    notes.append(str(exc))
                if self.brief_supported is not False:
                    brief = query(connection, "display interface brief")
                    states = parse_brief(brief or "")
                    self.brief_supported = bool(states)
                    for number, fields in states.items():
                        data["ports"][number-1].update(fields)
                selected_ports = sorted(self.priority_ports)[:PORT_BATCH]
                while len(selected_ports) < PORT_BATCH:
                    n = self.port_cursor % 28 + 1
                    self.port_cursor += 1
                    if n not in selected_ports:
                        selected_ports.append(n)
                for n in selected_ports:
                    output = query(connection, "display interface " + interface_name(n))
                    port = parse_port(output or "", n)
                    if port:
                        old = data["ports"][n-1]
                        if port["state"] is not None:
                            port["link_at"] = port["collected_at"]
                        else:
                            port["state"] = old.get("state")
                            port["link_at"] = old.get("link_at")
                        data["ports"][n-1].update(port)
                    else:
                        notes.append(f"Porta {n}: resposta sem dados reconhecidos.")
                vlans = data["vlans"] or []
                vlan_indices = {row["id"]: idx for idx,row in enumerate(vlans)}
                selected_vlans = [vlan_indices[v] for v in sorted(self.priority_vlans) if v in vlan_indices][:VLAN_BATCH]
                while len(selected_vlans) < min(VLAN_BATCH, len(vlans)):
                    idx = self.vlan_cursor % len(vlans)
                    self.vlan_cursor += 1
                    if idx not in selected_vlans:
                        selected_vlans.append(idx)
                for idx in selected_vlans:
                    v = vlans[idx]["id"]
                    detail = parse_vlan_detail(query(connection, f"display vlan {v}") or "", v)
                    if detail:
                        vlans[idx] = detail
                if time.monotonic() - self.mac_attempt >= 60 or not self.mac_attempt:
                    self.mac_attempt = time.monotonic()
                    try:
                        data["mac_table"] = parse_mac_table(query(connection, "display mac-address") or "")
                    except SerialReadOnlyError:
                        data["mac_table"] = None
                        notes.append("Tabela MAC indisponível nesta coleta.")
            data.update(collected_at=now(), port=settings.port, baud_rate=settings.baud_rate,
                        notes=notes, expected_ports=28)
            self.priority_ports.difference_update(selected_ports)
            self.priority_vlans.difference_update(vlans[i]["id"] for i in selected_vlans)
            self.data = data
            self.outputs = outputs
            self.dirty = False
            return self.cached()
        except (SerialReadOnlyError, OSError, ValueError) as exc:
            self.error = str(exc)
            self.dirty = True
            # Keep the last successful snapshot, explicitly marked stale.
            raise SerialReadOnlyError(self.error) from exc
        except Exception as exc:
            self.error = "Falha de leitura serial. Confira o cabo e a sessão CLI."
            self.dirty = True
            raise SerialReadOnlyError(self.error) from exc
