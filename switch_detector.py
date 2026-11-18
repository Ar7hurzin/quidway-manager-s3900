import re


def identificar_switch(resposta):
	# Remove sequencias de controle do terminal.
	texto = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", resposta)
	texto = texto.replace("\r", "").replace("\x00", "")

	# Identifica o modelo do equipamento.
	s3000 = re.search(r"\bQuidway\s+(S30\d{2}[A-Z0-9-]*)\b", texto, re.I)
	s3900 = re.search(
		r"\bQuidway\s+((?:S3900|3900-SI)(?:\s+28-Port)?)\b", texto, re.I
	)

	if s3000:
		modelo = s3000.group(1).upper()
		serie = "S3000"
		perfil = "s3000"
	elif s3900:
		modelo = s3900.group(1)
		serie = "S3900"
		perfil = "s3900"
	else:
		return {
			"fabricante": "Não identificado",
			"serie": None,
			"modelo": None,
			"versao": None,
			"release": None,
			"perfil": "desconhecido",
			"configuracao_habilitada": False,
		}

	versao = re.search(r"Version\s+([\d.]+(?:\([A-Za-z]+\))?)", texto, re.I)
	release = re.search(r"Release\s+(\d+)", texto, re.I)

	return {
		"fabricante": "Huawei",
		"serie": serie,
		"modelo": modelo,
		"versao": versao.group(1) if versao else None,
		"release": release.group(1) if release else None,
		"perfil": perfil,
		"configuracao_habilitada": False,
	}
