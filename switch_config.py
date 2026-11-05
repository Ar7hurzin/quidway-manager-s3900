"""Validated configuration plans for the Quidway S3900 console workflow.

The browser can only select one of these typed operations; arbitrary CLI text is never accepted.
Writes stay disabled unless QM_ENABLE_CONFIG_WRITES=true is set explicitly.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
import time
from dataclasses import dataclass


class ConfigurationError(ValueError):
    """Safe, user-facing validation, firmware, or serial execution failure."""


@dataclass(frozen=True)
class ConfigurationPlan:
    action: str
    summary: str
    commands: tuple[str, ...]
    warnings: tuple[str, ...]


def _vlan_id(value: object) -> int:
    if isinstance(value, bool):
        raise ConfigurationError("O ID da VLAN deve ser um número entre 1 e 4094.")
    try:
        vlan_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("O ID de VLAN deve ser um número entre 1 e 4094.") from exc
    if not 1 <= vlan_id <= 4094:
        raise ConfigurationError("O ID da VLAN deve estar entre 1 e 4094.")
    return vlan_id


def _vlan_list(value: object, field: str = "VLANs") -> list[int]:
    if not isinstance(value, list) or not value or len(value) > 64:
        raise ConfigurationError(f"Selecione de 1 a 64 IDs para {field}.")
    values = sorted({_vlan_id(item) for item in value})
    return values


def _port(value: object) -> tuple[int, str]:
    if isinstance(value, bool):
        raise ConfigurationError("Selecione uma porta entre 1 e 28.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("Selecione uma porta entre 1 e 28.") from exc
    if not 1 <= number <= 28:
        raise ConfigurationError("Este modelo tem 24 portas FE e 4 GE; use a numeração de 1 a 28.")
    if number <= 24:
        return number, f"Ethernet1/0/{number}"
    return number, f"GigabitEthernet1/1/{number - 24}"


def _safe_text(value: object, label: str, max_length: int = 31) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(f"Informe {label}.")
    value = value.strip()
    if not value or len(value) > max_length or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ConfigurationError(f"{label.capitalize()} aceita de 1 a {max_length} caracteres: letras, números, ponto, hífen ou sublinhado.")
    return value


def _port_description(value: object) -> str:
    if not isinstance(value, str):
        raise ConfigurationError("Informe a descrição da porta.")
    value = " ".join(value.strip().split())
    if not value or len(value) > 64 or not re.fullmatch(r"[A-Za-z0-9 _.-]+", value):
        raise ConfigurationError("A descrição aceita de 1 a 64 caracteres: letras, números, espaço, ponto, hífen ou sublinhado.")
    return value


def _vlan_words(values: list[int]) -> str:
    return " ".join(str(value) for value in values)


def build_plan(payload: object) -> ConfigurationPlan:
    if not isinstance(payload, dict):
        raise ConfigurationError("A solicitação de configuração está inválida.")
    action = payload.get("action")
    warnings: list[str] = [
        "As mudanças entram imediatamente na configuração em execução. Use o console serial local para manter acesso ao switch.",
        "A sintaxe está baseada no Command Manual da série S3900; o release VRP 3.10(CN) RELEASE 0002 deste aparelho ainda requer validação específica antes de habilitar gravações.",
    ]
    if action == "create_vlan":
        vlan = _vlan_id(payload.get("vlan_id"))
        name = payload.get("name", "")
        commands = ["system-view", f"vlan {vlan}"]
        if name:
            commands.append(f"name {_safe_text(name, 'nome da VLAN')}")
        commands.extend(("quit", "return"))
        return ConfigurationPlan(action, f"Criar VLAN {vlan}" + (f" ({name})" if name else ""), tuple(commands), tuple(warnings))

    if action == "rename_vlan":
        vlan = _vlan_id(payload.get("vlan_id"))
        name = _safe_text(payload.get("name"), "nome da VLAN")
        commands = ("system-view", f"vlan {vlan}", f"name {name}", "quit", "return")
        return ConfigurationPlan(action, f"Alterar nome da VLAN {vlan} para {name}", commands, tuple(warnings))

    if action == "delete_vlan":
        vlan = _vlan_id(payload.get("vlan_id"))
        if vlan == 1:
            raise ConfigurationError("A VLAN 1 padrão é protegida e não pode ser excluída pela interface.")
        warnings.extend(("A exclusão remove a VLAN e pode interromper todos os dispositivos associados.", "Confira portas e VLAN de gerenciamento antes de continuar."))
        commands = ("system-view", f"undo vlan {vlan}", "return")
        return ConfigurationPlan(action, f"Excluir VLAN {vlan}", commands, tuple(warnings))

    if action in {"set_access_port", "set_trunk_port", "set_hybrid_port", "set_port_description", "set_port_state"}:
        number, interface = _port(payload.get("port"))
        base = ["system-view", f"interface {interface}"]
        if action == "set_access_port":
            vlan = _vlan_id(payload.get("vlan_id"))
            commands = tuple(base + ["port link-type access", f"port access vlan {vlan}", "quit", "return"])
            summary = f"Configurar porta {number} como access na VLAN {vlan}"
            warnings.append("A troca para access remove o comportamento anterior de trunk/hybrid nesta porta.")
        elif action == "set_trunk_port":
            vlans = _vlan_list(payload.get("vlans"), "VLANs permitidas")
            pvid = _vlan_id(payload.get("pvid", 1))
            if pvid not in vlans:
                raise ConfigurationError("A VLAN nativa/PVID precisa estar incluída na lista permitida do trunk.")
            commands = tuple(base + ["port link-type trunk", f"port trunk permit vlan {_vlan_words(vlans)}", f"port trunk pvid vlan {pvid}", "quit", "return"])
            summary = f"Configurar porta {number} como trunk (PVID {pvid})"
            warnings.append("Revise a lista completa de VLANs permitidas e o PVID antes de aplicar; o link pode ser interrompido.")
        elif action == "set_hybrid_port":
            tagged = _vlan_list(payload.get("tagged_vlans"), "VLANs tagged") if payload.get("tagged_vlans") else []
            untagged = _vlan_list(payload.get("untagged_vlans"), "VLANs untagged") if payload.get("untagged_vlans") else []
            if not tagged and not untagged:
                raise ConfigurationError("Informe ao menos uma VLAN tagged ou untagged.")
            overlap = set(tagged) & set(untagged)
            if overlap:
                raise ConfigurationError("Uma VLAN não pode aparecer ao mesmo tempo como tagged e untagged.")
            pvid = _vlan_id(payload.get("pvid", 1))
            if pvid not in untagged:
                raise ConfigurationError("No modo hybrid, a PVID deve estar entre as VLANs untagged.")
            commands_list = base + ["port link-type hybrid"]
            if tagged:
                commands_list.append(f"port hybrid vlan {_vlan_words(tagged)} tagged")
            if untagged:
                commands_list.append(f"port hybrid vlan {_vlan_words(untagged)} untagged")
            commands_list.extend((f"port hybrid pvid vlan {pvid}", "quit", "return"))
            commands = tuple(commands_list)
            summary = f"Configurar porta {number} como hybrid (PVID {pvid})"
            warnings.append("Revise todas as VLANs tagged/untagged; a configuração pode afetar serviços conectados à porta.")
        elif action == "set_port_description":
            description = _port_description(payload.get("description"))
            commands = tuple(base + [f"description {description}", "quit", "return"])
            summary = f"Alterar descrição da porta {number}"
        else:
            state = payload.get("state")
            if state not in {"up", "down"}:
                raise ConfigurationError("Estado da porta inválido.")
            command = "undo shutdown" if state == "up" else "shutdown"
            commands = tuple(base + [command, "quit", "return"])
            summary = f"{('Ativar' if state == 'up' else 'Desativar')} porta {number}"
            if state == "down":
                warnings.append("Desativar a porta interrompe imediatamente a conexão física dos dispositivos ligados a ela.")
        return ConfigurationPlan(action, summary, commands, tuple(warnings))

    if action == "set_sysname":
        name = _safe_text(payload.get("name"), "nome do equipamento")
        commands = ("system-view", f"sysname {name}", "return")
        return ConfigurationPlan(action, f"Alterar nome do switch para {name}", commands, tuple(warnings))

    if action == "set_management_ip":
        vlan = _vlan_id(payload.get("vlan_id"))
        try:
            interface = ipaddress.IPv4Interface(f"{payload.get('ip')}/{payload.get('prefix')}")
        except (ValueError, TypeError) as exc:
            raise ConfigurationError("Informe um IPv4 válido e um prefixo de 1 a 30.") from exc
        if interface.network.prefixlen < 1 or interface.network.prefixlen > 30 or interface.ip.is_multicast or interface.ip.is_unspecified:
            raise ConfigurationError("O endereço de gestão precisa ser IPv4 unicast com prefixo entre /1 e /30.")
        mask = str(interface.network.netmask)
        commands = ("system-view", f"interface Vlan-interface {vlan}", f"ip address {interface.ip} {mask}", "quit", "return")
        warnings.extend(("Esta alteração troca o endereço de gestão do switch. A sessão remota pode cair; use o console local e planeje o novo endereço antes de aplicar.", "O endereço anterior não é restaurado automaticamente."))
        return ConfigurationPlan(action, f"Alterar IP de gestão da VLAN {vlan} para {interface.ip}/{interface.network.prefixlen}", commands, tuple(warnings))

    if action in {"add_static_route", "delete_static_route"}:
        try:
            network = ipaddress.IPv4Network(f"{payload.get('destination')}/{payload.get('prefix')}", strict=False)
            next_hop = ipaddress.IPv4Address(str(payload.get("next_hop", "")))
        except (ValueError, TypeError) as exc:
            raise ConfigurationError("Informe a rede IPv4 de destino, prefixo CIDR e próximo salto válidos.") from exc
        if next_hop.is_multicast or next_hop.is_unspecified or next_hop == ipaddress.IPv4Address("255.255.255.255"):
            raise ConfigurationError("O próximo salto precisa ser um endereço IPv4 unicast válido.")
        route = f"ip route-static {network.network_address} {network.netmask} {next_hop}"
        if action == "add_static_route":
            commands = ("system-view", route, "return")
            summary = f"Adicionar rota estática para {network} via {next_hop}"
            warnings.extend(("A rota altera o encaminhamento IPv4 e pode interromper o gerenciamento remoto se o destino ou próximo salto estiver incorreto.", "Este S3900-SI oferece recursos de roteamento limitados em comparação ao modelo EI. Confirme a topologia e a compatibilidade no firmware instalado."))
        else:
            commands = ("system-view", f"undo {route}", "return")
            summary = f"Excluir rota estática para {network} via {next_hop}"
            warnings.extend(("A exclusão pode remover o caminho usado por redes e serviços remotos.", "Confira a tabela de rotas e mantenha o console serial disponível."))
        return ConfigurationPlan(action, summary, commands, tuple(warnings))

    if action == "save":
        warnings.append("Salvar grava a configuração atual no arquivo de inicialização do switch; confirme depois de revisar as mudanças em execução.")
        return ConfigurationPlan(action, "Salvar a configuração atual no switch", ("save",), tuple(warnings))

    raise ConfigurationError("Operação de configuração não reconhecida.")


_PREVIEWS: dict[str, tuple[float, ConfigurationPlan]] = {}


def issue_preview(plan: ConfigurationPlan, lifetime_seconds: int = 300) -> str:
    now = time.monotonic()
    expired = [key for key, (expiry, _) in _PREVIEWS.items() if expiry < now]
    for key in expired:
        _PREVIEWS.pop(key, None)
    token = secrets.token_urlsafe(32)
    _PREVIEWS[token] = (now + lifetime_seconds, plan)
    return token


def consume_preview(token: object) -> ConfigurationPlan:
    if not isinstance(token, str):
        raise ConfigurationError("Prévia ausente. Gere uma nova prévia.")
    entry = _PREVIEWS.pop(token, None)
    if not entry or entry[0] < time.monotonic():
        raise ConfigurationError("A prévia expirou ou já foi usada. Gere uma nova prévia.")
    return entry[1]
