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

INTERVAL = 4
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
    pvid = _field(text, r"\bPVID[ \t]*:[ \t]*(\d{1,4})\b")
    mode = _field(text, r"\bPort[ \t]+link-type[ \t]*:[ \t]*(access|trunk|hybrid)\b")
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


def _vlan_member_sections(text):
    """Parse VRP multiline VLAN member lists, without guessing damaged port IDs."""
    recognized = {}
    partial = False
    current = None
    entry = re.compile(
        r"(?i)\b(?:Ethernet1/0/(?:[1-9]|1[0-9]|2[0-4])|"
        r"GigabitEthernet1/1/[1-4])\b"
    )
    header = re.compile(r"(?i)^[ \t]*(Tagged|Untagged)[ \t]+Ports[ \t]*:[ \t]*(.*)$")
    for raw in terminal_text(text).splitlines():
        match = header.match(raw)
        if match:
            current = match[1].lower()
            recognized[current] = {"ports": set(), "explicit_none": False}
            value = match[2].strip()
        elif current and raw.startswith((" ", "\t")):
            value = raw.strip()
        else:
            current = None
            continue
        if not value:
            continue
        if value.lower().strip(' *"') == "none":
            recognized[current]["explicit_none"] = True
            continue
        matches = list(entry.finditer(value))
        for m in matches:
            name = m[0]
            recognized[current]["ports"].add(
                int(name.rsplit("/", 1)[-1]) +
                (24 if name.lower().startswith("gigabit") else 0)
            )
        residual = entry.sub(" ", value)
        # A damaged Ethernet name is incomplete information, not a missing port.
        if re.search(r"(?i)\b(?:Ethernet|GigabitEthernet)", residual) or "\ufffd" in residual:
            partial = True
    return recognized, partial


def _members_label(tagged, untagged, *, partial=False):
    labels = []
    if tagged:
        labels.append("Tagged: " + ", ".join(map(str, sorted(tagged))))
    if untagged:
        labels.append("Untagged: " + ", ".join(map(str, sorted(untagged))))
    if not labels:
        return "Nenhuma" if not partial else None
    return "; ".join(labels) + (" (leitura parcial)" if partial else "")


def parse_vlan_detail(text, vlan):
    text = terminal_text(text)
    actual = _field(text, r"^[ \t]*VLAN[ \t]+ID[ \t]*:[ \t]*(\d{1,4})\b")
    if actual is None or int(actual) != vlan:
        return None
    name = _field(text, r"^[ \t]*(?:VLAN[ \t]+)?Description[ \t]*:[ \t]*([^\n]+)")
    sections, partial = _vlan_member_sections(text)
    tagged = sections.get("tagged", {}).get("ports", set())
    untagged = sections.get("untagged", {}).get("ports", set())
    both_present = "tagged" in sections and "untagged" in sections
    if not both_present:
        partial = True
    all_none = both_present and all(sections[k]["explicit_none"]
                                    for k in ("tagged", "untagged"))
    members = _members_label(tagged, untagged, partial=partial)
    if not tagged and not untagged and not all_none:
        members = None
    return {
        "id": vlan, "name": name, "members": members,
        "tagged_numbers": sorted(tagged),
        "untagged_numbers": sorted(untagged),
        "membership_partial": partial,
        "membership_reported": both_present,
        "note": "Console", "collected_at": now(),
    }


def reconcile_vlan_members(data):
    """Combine confirmed VLAN lists and individual access-port configuration."""
    for row in data.get("vlans") or []:
        vlan = row["id"]
        tagged = set(row.get("tagged_numbers", []))
        untagged = set(row.get("untagged_numbers", []))
        verified = set()
        for port in data.get("ports", []):
            if (port.get("collected_at") and port.get("mode") == "access"
                    and port.get("pvid") == vlan):
                verified.add(port["number"])
        extra = verified - untagged
        untagged.update(verified)
        partial = bool(row.get("membership_partial"))
        if tagged or untagged:
            row["members"] = _members_label(tagged, untagged, partial=partial)
            row["membership_from_ports"] = sorted(extra)
        elif row.get("membership_reported"):
            row["members"] = "Nenhuma" if not partial else None


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
        self.last_success = 0.0
        self.port_cursor = 0
        self.vlan_cursor = 0
        self.stage = 0
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
                "stale": self.dirty or bool(self.error) or time.monotonic() - self.last_success > INTERVAL * 3,
                "message": self.error, "collected_at": self.data.get("collected_at") if self.data else None}

    def collect(self, settings, force=False):
        """Read at most a small group of read-only CLI commands per poll.

        Each successful detail replaces only that detail. A rejected, malformed,
        or timed-out command never erases a previously verified observation.
        """
        key = (settings.port, settings.baud_rate)
        if self.settings_key != key:
            self.reset()
            self.settings_key = key
        if not force and not self.dirty and time.monotonic() - self.last_attempt < INTERVAL:
            return self.cached()

        self.last_attempt = time.monotonic()
        self.error = None
        outputs = []
        data = copy.deepcopy(self.data) if self.data else {
            "ports": [
                {"number": n, "interface": interface_name(n), "state": None,
                 "admin": None, "pvid": None, "mode": None, "description": None,
                 "tagged": None, "untagged": None, "rx_bps": None,
                 "tx_bps": None, "collected_at": None}
                for n in range(1, 29)
            ],
            "vlans": None,
        }
        notes = []
        active_command = "conexão serial"
        deadline = time.monotonic() + 22

        def query(connection, command):
            nonlocal active_command
            active_command = command
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SerialReadOnlyError("Limite da coleta serial atingido.")
            connection.write(command.encode("ascii") + b"\r")
            raw = _read_until_prompt(connection, min(settings.timeout_seconds, 10, remaining))
            output = _clean_output(raw, command)
            outputs.append({"command": command, "output": output})
            if command_rejected(output):
                notes.append("Consulta não suportada pelo firmware: " + command)
                return None
            return output

        def publish():
            data.update(collected_at=now(), port=settings.port,
                        baud_rate=settings.baud_rate, notes=list(notes),
                        expected_ports=28)
            reconcile_vlan_members(data)
            self.data = copy.deepcopy(data)
            self.outputs = copy.deepcopy(outputs)
            self.last_success = time.monotonic()
            self.dirty = False

        def refresh_inventory(connection):
            try:
                inventory = _parse_vlan_inventory(query(connection, "display vlan") or "")
            except ConfigurationError as exc:
                notes.append("Inventário VLAN não reconhecido nesta tentativa; dados anteriores preservados.")
                return
            old = {v["id"]: v for v in data.get("vlans") or []}
            # Some firmware/terminal responses list only part of the inventory.
            # Keep separately confirmed VLANs with their original observation
            # timestamp instead of silently discarding them.
            confirmed = {vid for vid, row in old.items()
                         if row.get("verified_individually")}
            missing_confirmed = confirmed - inventory
            data["vlans"] = [
                old.get(v, {"id": v, "name": None, "members": None,
                            "note": "Inventário", "collected_at": None})
                for v in sorted(inventory | confirmed)
            ]
            if missing_confirmed:
                notes.append("Inventário incompleto: VLAN(s) "
                             + ", ".join(map(str, sorted(missing_confirmed)))
                             + " confirmada(s) anteriormente em consulta individual.")
                for row in data["vlans"]:
                    if row["id"] in missing_confirmed:
                        row["note"] = "Consulta individual anterior · ausente no inventário"

        def refresh_port(connection, n):
            output = query(connection, "display interface " + interface_name(n))
            new = parse_port(output or "", n)
            if not new:
                notes.append(f"Porta {n}: resposta não reconhecida; leitura anterior preservada.")
                return
            old = data["ports"][n - 1]
            if new["state"] is None:
                new["state"] = old.get("state")
                new["link_at"] = old.get("link_at")
            else:
                new["link_at"] = new["collected_at"]
            # Never overwrite a valid field with None from a partial response.
            old.update({k: v for k, v in new.items() if v is not None})
            old["collected_at"] = new["collected_at"]

        def refresh_vlan_detail(connection, vlan):
            # Do not infer that a VLAN exists merely from a port PVID, a MAC,
            # a cached inventory number or its previous existence.
            new = parse_vlan_detail(query(connection, f"display vlan {vlan}") or "", vlan)
            if not new:
                notes.append(f"VLAN {vlan}: consulta individual não confirmada.")
                return False
            new["verified_individually"] = True
            new["note"] = "Confirmada por display vlan " + str(vlan)
            if data.get("vlans") is None:
                data["vlans"] = []
            for row in data["vlans"]:
                if row["id"] == vlan:
                    row.update({k: v for k, v in new.items() if v is not None})
                    break
            else:
                data["vlans"].append(new)
            data["vlans"].sort(key=lambda v: v["id"])
            return True

        try:
            with open_connection(settings) as connection:
                connection.reset_input_buffer()
                connection.write(b"\r")
                active_command = "prompt serial"
                remaining = max(1, min(settings.timeout_seconds, 10, deadline - time.monotonic()))
                _read_until_prompt(connection, remaining)

                if self.data is None:
                    identity = parse_identity(query(connection, "display version") or "")
                    data.update(identity, name=identity["model"])
                    publish()
                    refresh_inventory(connection)
                    publish()
                    # The lab VLAN is verified independently of the inventory,
                    # whose legacy response can be truncated or malformed.
                    refresh_vlan_detail(connection, 12)
                    publish()
                elif self.dirty:
                    refresh_inventory(connection)
                    publish()
                    if self.priority_ports:
                        n = min(self.priority_ports)
                        refresh_port(connection, n)
                        self.priority_ports.discard(n)
                        publish()
                    elif self.priority_vlans:
                        v = min(self.priority_vlans)
                        if any(row["id"] == v for row in data.get("vlans") or []):
                            refresh_vlan_detail(connection, v)
                        self.priority_vlans.discard(v)
                        publish()
                else:
                    stage = self.stage % 13
                    self.stage += 1
                    if self.priority_ports:
                        n = min(self.priority_ports)
                        refresh_port(connection, n)
                        self.priority_ports.discard(n)
                    elif self.priority_vlans:
                        v = min(self.priority_vlans)
                        if any(row["id"] == v for row in data.get("vlans") or []):
                            refresh_vlan_detail(connection, v)
                        self.priority_vlans.discard(v)
                    elif stage == 0:
                        refresh_inventory(connection)
                    elif stage in {1, 2, 3, 5, 6, 7, 9, 10, 11}:
                        n = (stage - 1) % 4 + 1
                        n = {1: 1, 2: 2, 3: 3, 5: 1, 6: 2,
                             7: 3, 9: 1, 10: 2, 11: 3}[stage]
                        refresh_port(connection, n)
                    elif stage == 4:
                        # Periodic independent check of the lab VLAN.
                        refresh_vlan_detail(connection, 12)
                    elif stage == 8:
                        n = 4 + (self.port_cursor % 25)
                        self.port_cursor += 1
                        refresh_port(connection, n)
                    elif stage == 12 and (not self.mac_attempt or time.monotonic() - self.mac_attempt >= 60):
                        self.mac_attempt = time.monotonic()
                        new_macs = parse_mac_table(query(connection, "display mac-address") or "")
                        if new_macs is not None:
                            new_macs["entries"].sort(key=lambda r: (r["port"], r["vlan"], r["mac"]))
                            data["mac_table"] = new_macs
                        else:
                            notes.append("Tabela MAC não reconhecida; leitura anterior preservada.")
                    elif data.get("vlans"):
                        rows = data["vlans"]
                        vlan = rows[self.vlan_cursor % len(rows)]["id"]
                        self.vlan_cursor += 1
                        refresh_vlan_detail(connection, vlan)
                    publish()
            return self.cached()
        except (SerialReadOnlyError, OSError, ValueError) as exc:
            self.error = f"Falha em {active_command}: {exc}"
            self.dirty = False
            if self.data is not None:
                return self.cached()
            raise SerialReadOnlyError(self.error) from exc
        except Exception as exc:
            self.error = "Erro na leitura serial em " + active_command + ". Verifique o console."
            self.dirty = False
            if self.data is not None:
                return self.cached()
            raise SerialReadOnlyError(self.error) from exc
