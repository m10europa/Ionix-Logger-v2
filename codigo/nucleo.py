"""Parte do registrador que nao depende da tela: serial, gravacao e Excel."""
import csv
import math
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import serial
from serial.tools import list_ports

BAUDS = [9600, 4800, 2400, 19200, 1200, 38400]
FORMATOS = {"8N1": (8, "N", 1), "7E1": (7, "E", 1)}
SEGUNDOS_POR_TENTATIVA = 8
ESPERA_RESPOSTA_S = 2   # o comando sai esse tempo antes de cada registro
PERIODO_COMANDO_S = 2   # com "todas as leituras", pede uma leitura a cada 2 s
SILENCIO_S = 30         # sem leitura por esse tempo, grava uma linha SEM DADOS
LIMITE_BUFFER = 4096
UNIDADE = "µS/cm"
NUMERO = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
CONTROLE = re.compile(r"[\x00-\x1f\x7f]")   # o mCA 150 manda um NUL depois de "ºC"
# Formato do mCA 150:  "419.74 ,uS,27.6,º"  ou  "2.21 ,mS,27.7,ºC"
# Valor em branco (" ,uS,27.5,") acontece na troca de escala.
MCA = re.compile(r"^\s*(?P<v>[-+]?\d+(?:[.,]\d+)?)?\s*,\s*(?P<u>[uµm])S\s*,\s*(?P<t>[-+]?\d+(?:[.,]\d+)?)?")
CABECALHO_V1 = ["data_hora", "tempo_min", "condutividade", "temperatura", "linha_recebida"]
CABECALHO = ["data_hora", "tempo_min", "condutividade_uS_cm", "temperatura_C", "situacao", "linha_recebida"]
OK, SEM_LEITURA, SEM_DADOS = "OK", "SEM LEITURA", "SEM DADOS"


@dataclass
class Config:
    porta: str = ""
    baud: int = 9600
    formato: str = "8N1"
    intervalo: float = 0.0   # 0 = grava todas as leituras que o aparelho enviar
    comando: str = ""
    amostra: str = ""
    pasta: Path = field(default_factory=Path.cwd)


class SeparadorDeLinhas:
    """Junta os bytes recebidos em linhas (terminadas em CR, LF ou CRLF)."""

    def __init__(self):
        self.buffer = b""

    def alimentar(self, dados):
        partes = re.split(rb"[\r\n]+", self.buffer + dados)
        self.buffer = partes.pop()[-LIMITE_BUFFER:]
        linhas = (limpar(p.decode("latin-1")) for p in partes)
        return [linha for linha in linhas if linha]


def limpar(texto):
    return CONTROLE.sub("", texto).strip()


def _num(texto):
    return float(texto.replace(",", ".")) if texto else None


def numeros_da_linha(linha):
    return [_num(n) for n in NUMERO.findall(linha)]


def interpretar(linha):
    """Devolve (condutividade em µS/cm, temperatura em °C); None no que faltar."""
    m = MCA.match(linha)
    if m:
        cond = _num(m["v"])
        if cond is not None and m["u"].lower() == "m":
            cond = round(cond * 1000, 3)
        return cond, _num(m["t"])
    # outro formato: primeiro numero = condutividade, segundo = temperatura
    numeros = numeros_da_linha(linha)
    return (numeros[0] if numeros else None), (numeros[1] if len(numeros) > 1 else None)


def excel(valor):
    """Numero com virgula decimal, como o Excel em portugues espera."""
    return "" if valor is None else str(valor).replace(".", ",")


def listar_portas():
    """Lista (dispositivo, descricao), com adaptadores USB primeiro."""
    portas = sorted(list_ports.comports(), key=lambda p: (p.vid is None, p.device))
    return [(p.device, re.sub(r"\s*\(COM\d+\)", "", p.description).strip()) for p in portas]


def abrir_porta(porta, baud, formato="8N1", timeout=0.2):
    bits, paridade, parada = FORMATOS[formato]
    return serial.serial_for_url(porta, baudrate=baud, bytesize=bits, parity=paridade,
                                 stopbits=parada, timeout=timeout)


def fechar_porta(ser):
    try:
        ser.close()
    except (serial.SerialException, OSError):
        pass


def manter_ligado(ativo):
    """Pede ao Windows para nao suspender. Vale para a thread que chamou."""
    if sys.platform == "win32":
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        estado = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if ativo else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(estado)


def na_bateria():
    """True se o notebook estiver fora da tomada."""
    if sys.platform != "win32":
        return False
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("linha", ctypes.c_ubyte), ("flag", ctypes.c_ubyte),
                    ("carga", ctypes.c_ubyte), ("sistema", ctypes.c_ubyte),
                    ("restante", ctypes.c_ulong), ("total", ctypes.c_ulong)]

    status = Status()
    return bool(ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))
                and status.linha == 0)


def nome_seguro(texto):
    return re.sub(r"[^0-9A-Za-zÀ-ÿ_-]+", "_", texto.strip()).strip("_")[:40]


class Ensaio(threading.Thread):
    """Grava as leituras ate parar() ser chamado.

    intervalo = 0: uma linha por leitura recebida.
    intervalo > 0: uma linha por intervalo, com a leitura mais recente.
    Fala com a tela pela fila `eventos`, em tuplas (tipo, dados):
    conexao, linha, registro, erro, fim.
    """

    def __init__(self, cfg, eventos):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.eventos = eventos
        self._parar = threading.Event()
        cfg.pasta.mkdir(parents=True, exist_ok=True)
        amostra = nome_seguro(cfg.amostra)
        partes = ["ensaio"] + ([amostra] if amostra else []) + [f"{datetime.now():%Y-%m-%d_%H%M%S}"]
        self.caminho = cfg.pasta / ("_".join(partes) + ".csv")
        self.inicio = None
        self.falhas = 0

    def parar(self):
        self._parar.set()

    def run(self):
        manter_ligado(True)
        try:
            # utf-8-sig e ';' fazem o Excel em portugues abrir as colunas certas.
            with open(self.caminho, "x", newline="", encoding="utf-8-sig") as arquivo:
                self.arquivo = arquivo
                self.saida = csv.writer(arquivo, delimiter=";")
                self.saida.writerow(CABECALHO)
                arquivo.flush()
                self._laco()
        except OSError as erro:
            self.eventos.put(("erro", f"Falha ao gravar o arquivo: {erro}"))
        finally:
            manter_ligado(False)
            self.eventos.put(("fim", self.caminho))

    def _gravar(self, cond, temp, linha):
        momento = datetime.now()
        # Relogio real: se o notebook parar, o buraco aparece no tempo.
        self.inicio = self.inicio or momento
        minutos = round((momento - self.inicio).total_seconds() / 60, 3)
        situacao = OK if cond is not None else (SEM_LEITURA if linha else SEM_DADOS)
        self.saida.writerow([f"{momento:%Y-%m-%d %H:%M:%S}", excel(minutos), excel(cond),
                             excel(temp), situacao, linha or ""])
        self.arquivo.flush()
        os.fsync(self.arquivo.fileno())
        self.falhas = 0 if cond is not None else self.falhas + 1
        self.eventos.put(("registro", {"hora": momento, "minutos": minutos, "cond": cond,
                                       "temp": temp, "situacao": situacao, "falhas": self.falhas}))

    def _laco(self):
        cfg = self.cfg
        todas = cfg.intervalo <= 0
        ser = None
        separador = SeparadorDeLinhas()
        avisou_falha = False
        ultima, hora_leitura = None, float("-inf")   # (cond, temp, linha)
        t0 = time.monotonic()
        ultimo_registro = t0
        # o tempo zero e a primeira leitura; sem ela, espera ate gravar falha
        limite_primeira = t0 + max(cfg.intervalo, SILENCIO_S if todas else 10)
        proximo = t0 + min(3, cfg.intervalo)
        proximo_cmd = t0 if todas else proximo - ESPERA_RESPOSTA_S
        validade = cfg.intervalo * 1.1 + 1   # folga para aparelho no mesmo ritmo

        while not self._parar.is_set():
            if ser is None:
                try:
                    ser = abrir_porta(cfg.porta, cfg.baud, cfg.formato)
                    separador = SeparadorDeLinhas()
                    avisou_falha = False
                    self.eventos.put(("conexao", True))
                except (serial.SerialException, OSError, ValueError) as erro:
                    if not avisou_falha:
                        self.eventos.put(("conexao", False))
                        self.eventos.put(("erro", f"Sem conexão, tentando de novo: {erro}"))
                        avisou_falha = True
                    self._parar.wait(0.5)

            if ser is not None:
                try:
                    dados = ser.read(max(1, ser.in_waiting))
                    agora = time.monotonic()
                    for linha in separador.alimentar(dados):
                        self.eventos.put(("linha", linha))
                        cond, temp = interpretar(linha)
                        if cond is None:
                            continue
                        ultima, hora_leitura = (cond, temp, linha), agora
                        if todas:
                            self._gravar(cond, temp, linha)
                            ultimo_registro = agora
                    if cfg.comando and agora >= proximo_cmd:
                        ser.write(cfg.comando.encode("latin-1") + b"\r\n")
                        proximo_cmd = agora + PERIODO_COMANDO_S if todas else float("inf")
                except (serial.SerialException, OSError) as erro:
                    self.eventos.put(("conexao", False))
                    self.eventos.put(("erro", f"Conexão perdida: {erro}"))
                    fechar_porta(ser)
                    ser = None

            agora = time.monotonic()
            if todas:
                # aparelho calado: marca o buraco a cada SILENCIO_S
                if (agora - ultimo_registro >= SILENCIO_S
                        and (self.inicio is not None or agora >= limite_primeira)):
                    self._gravar(None, None, None)
                    ultimo_registro = agora
                continue

            if agora < proximo:
                continue
            # So vale leitura recente: um valor velho repetido pareceria uma
            # curva estavel que nao existiu.
            recente = ultima if agora - hora_leitura <= validade else None
            if self.inicio is None and recente is None and agora < limite_primeira:
                continue
            self._gravar(*(recente or (None, None, None)))
            # Mantem a grade de tempo e pula horarios perdidos, sem acumular.
            while proximo <= agora:
                proximo += cfg.intervalo
            proximo_cmd = proximo - ESPERA_RESPOSTA_S

        if ser is not None:
            fechar_porta(ser)


def pontuacao_texto(dados):
    """Fracao de bytes que parecem texto. Velocidade errada costuma dar lixo."""
    legiveis = sum(1 for b in dados if 32 <= b < 127 or b in (9, 10, 13) or b in (0xB5, 0xBA, 0xB0))
    return legiveis / len(dados)


def descobrir(porta, comando, parar, relatar, abrir=abrir_porta):
    """Procura velocidade e formato. Devolve (baud, formato, amostra) ou None."""
    melhor = None
    for formato in FORMATOS:
        for baud in BAUDS:
            if parar.is_set():
                return melhor and melhor[:3]
            relatar(f"Escutando {baud} baud, {formato}...")
            try:
                ser = abrir(porta, baud, formato)
            except (serial.SerialException, OSError, ValueError) as erro:
                relatar(f"Não consegui abrir a porta: {erro}")
                return None
            dados = b""
            try:
                ser.reset_input_buffer()
                if comando:
                    ser.write(comando.encode("latin-1") + b"\r\n")
                limite = time.monotonic() + SEGUNDOS_POR_TENTATIVA
                while time.monotonic() < limite and len(dados) < 200 and not parar.is_set():
                    dados += ser.read(max(1, ser.in_waiting))
            except (serial.SerialException, OSError) as erro:
                relatar(f"Falha na porta: {erro}")
                return None
            finally:
                fechar_porta(ser)

            if not dados:
                relatar("   nada recebido")
                continue
            nota = pontuacao_texto(dados)
            relatar(f"   {len(dados)} bytes, {nota:.0%} legível: {dados[:60]!r}")
            if len(dados) >= 5 and (melhor is None or nota > melhor[3]):
                melhor = (baud, formato, dados.decode("latin-1"), nota)
            if len(dados) >= 5 and nota >= 0.9:
                return melhor[:3]
    if melhor and melhor[3] >= 0.9:
        return melhor[:3]
    return None


# ------------------------------------------------------------------ planilha
def ler_csv(caminho_csv):
    """Le um ensaio (formato atual ou o da primeira versao) em uma lista de dicts."""
    with open(caminho_csv, newline="", encoding="utf-8-sig") as arquivo:
        linhas = list(csv.reader(arquivo, delimiter=";"))
    if not linhas or linhas[0] not in (CABECALHO, CABECALHO_V1):
        raise ValueError("Este CSV não foi gerado pelo registrador.")
    v1 = linhas[0] == CABECALHO_V1
    registros = []
    for campos in linhas[1:]:
        if len(campos) < len(linhas[0]):
            continue   # linha cortada por queda de energia
        hora = datetime.strptime(campos[0], "%Y-%m-%d %H:%M:%S")
        if v1:
            # a primeira versao nao entendia mS: refaz a partir do texto recebido
            linha = "" if campos[4] == SEM_DADOS else limpar(campos[4])
            cond, temp = interpretar(linha) if linha else (None, None)
            situacao = OK if cond is not None else (SEM_LEITURA if linha else SEM_DADOS)
        else:
            cond, temp, situacao, linha = _num(campos[2]), _num(campos[3]), campos[4], limpar(campos[5])
        registros.append({"hora": hora, "minutos": _num(campos[1]), "cond": cond, "temp": temp,
                          "situacao": situacao, "linha": linha})
    return registros


def amostra_do_nome(caminho):
    m = re.match(r"^ensaio_(.*)_\d{4}-\d{2}-\d{2}_\d{6}$", Path(caminho).stem)
    return m[1].replace("_", " ") if m else ""


def duracao_texto(segundos):
    s = int(round(segundos))
    return f"{s // 3600} h {s % 3600 // 60:02d} min {s % 60:02d} s"


def gerar_excel(caminho_csv):
    """Converte o CSV em .xlsx com relatorio, graficos e tabela. Devolve o caminho."""
    from openpyxl import Workbook
    from openpyxl.chart import Reference, ScatterChart, Series
    from openpyxl.chart.shapes import GraphicalProperties
    from openpyxl.drawing.line import LineProperties
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo

    caminho_csv = Path(caminho_csv)
    regs = ler_csv(caminho_csv)
    validos = [r for r in regs if r["cond"] is not None]
    temps = [r["temp"] for r in regs if r["temp"] is not None]
    em_horas = bool(regs) and (regs[-1]["minutos"] or 0) > 180

    azul, cinza = "263238", "CFD8DC"   # mesmo azul escuro da tela
    livro = Workbook()
    rel = livro.active
    rel.title = "Relatório"
    dados = livro.create_sheet("Dados")

    # ---------------- Dados: tabela do Excel, pronta para filtrar e plotar
    colunas = [("Nº", 7, "0"), ("Data", 12, "dd/mm/yyyy"), ("Hora", 10, "hh:mm:ss"),
               ("Tempo (min)", 12, "0.00"), ("Tempo (h)", 11, "0.000"),
               (f"Condutividade ({UNIDADE})", 18, "0.00"), ("Temperatura (°C)", 15, "0.0"),
               ("Situação", 14, "@"), ("Texto recebido do aparelho", 30, "@")]
    dados.append([c[0] for c in colunas])
    for n, r in enumerate(regs, 1):
        minutos = r["minutos"]
        dados.append([n, r["hora"].date(), r["hora"].time(), minutos,
                      None if minutos is None else round(minutos / 60, 4),
                      r["cond"], r["temp"], r["situacao"], r["linha"]])
    centro = Alignment(horizontal="center", vertical="center")
    for i, (_, largura, formato) in enumerate(colunas, 1):
        letra = get_column_letter(i)
        dados.column_dimensions[letra].width = largura
        for (celula,) in dados.iter_rows(min_row=2, min_col=i, max_col=i):
            celula.number_format = formato
            if i < 9:
                celula.alignment = centro
    for celula in dados[1]:
        celula.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        celula.font = Font(bold=True, color="FFFFFF")
        celula.fill = PatternFill("solid", fgColor=azul)
    dados.row_dimensions[1].height = 32
    dados.freeze_panes = "A2"
    if regs:
        tabela = Table(displayName="Leituras", ref=f"A1:I{len(regs) + 1}")
        tabela.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=True)
        dados.add_table(tabela)

    # ---------------- Relatorio: resumo e graficos
    rel.sheet_view.showGridLines = False
    for letra, largura in zip("ABCDEFGHIJKLMN", (26, 22, 4, 28, 22) + (9,) * 9):
        rel.column_dimensions[letra].width = largura
    rel.merge_cells("A1:N1")
    rel["A1"] = "RELATÓRIO DE ENSAIO DE CONDUTIVIDADE"
    rel["A1"].font = Font(bold=True, size=15, color="FFFFFF")
    rel["A1"].fill = PatternFill("solid", fgColor=azul)
    rel["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
    rel.row_dimensions[1].height = 30
    rel.merge_cells("A2:N2")
    rel["A2"] = "Condutivímetro mCA 150  •  Registrador de Condutividade  •  Ferreira Soluções em Engenharia"
    rel["A2"].font = Font(italic=True, size=9, color="607D8B")

    def valor_cond(v):
        return "-" if v is None else f"{v:.2f} {UNIDADE}".replace(".", ",")

    def valor_temp(v):
        return "-" if v is None else f"{v:.1f} °C".replace(".", ",")

    conds = [r["cond"] for r in validos]
    inicio, fim = (regs[0]["hora"], regs[-1]["hora"]) if regs else (None, None)
    esquerda = [("Amostra", amostra_do_nome(caminho_csv) or "-"),
                ("Início", f"{inicio:%d/%m/%Y %H:%M:%S}" if inicio else "-"),
                ("Fim", f"{fim:%d/%m/%Y %H:%M:%S}" if fim else "-"),
                ("Duração", duracao_texto((fim - inicio).total_seconds()) if regs else "-"),
                ("Leituras válidas", len(validos)),
                ("Registros sem leitura", len(regs) - len(validos))]
    direita = [("Condutividade inicial", valor_cond(conds[0] if conds else None)),
               ("Condutividade final", valor_cond(conds[-1] if conds else None)),
               ("Condutividade mínima", valor_cond(min(conds) if conds else None)),
               ("Condutividade máxima", valor_cond(max(conds) if conds else None)),
               ("Temperatura média", valor_temp(sum(temps) / len(temps) if temps else None)),
               ("Temperatura mín. / máx.", f"{valor_temp(min(temps))} / {valor_temp(max(temps))}"
                if temps else "-")]
    linha_fina = Side(style="thin", color=cinza)
    for k, ((r1, v1), (r2, v2)) in enumerate(zip(esquerda, direita)):
        lin = 4 + k
        for col, texto, negrito in (("A", r1, True), ("B", v1, False), ("D", r2, True), ("E", v2, False)):
            c = rel[f"{col}{lin}"]
            c.value = texto
            c.font = Font(bold=negrito, color=azul if negrito else "000000")
            c.alignment = Alignment(horizontal="left", vertical="center")
            c.border = Border(bottom=linha_fina)
        rel.row_dimensions[lin].height = 20

    # eixo do tempo terminando logo depois do fim do ensaio, com passo redondo
    total = max(((regs[-1]["minutos"] or 0) if regs else 0) / (60 if em_horas else 1), 1e-9)
    passos = (0.5, 1, 2, 3, 4, 6, 12) if em_horas else (0.5, 1, 2, 5, 10, 15, 20, 30, 60)
    passo = next((p for p in passos if total / p <= 12), passos[-1])
    eixo_tempo = (math.ceil(total / passo) * passo, passo)

    def grafico(titulo, col_y, rotulo_y, cor, formato_y, altura):
        g = ScatterChart()
        g.title = titulo
        g.style = 2
        g.scatterStyle = "lineMarker"
        g.legend = None
        g.dispBlanksAs = "gap"
        g.x_axis.title = "Tempo (h)" if em_horas else "Tempo (min)"
        g.y_axis.title = rotulo_y
        g.x_axis.number_format = "0.0" if eixo_tempo[1] < 1 else "0"
        g.y_axis.number_format = formato_y
        g.x_axis.scaling.min = 0
        g.x_axis.scaling.max, g.x_axis.majorUnit = eixo_tempo
        if col_y == 7 and temps:   # temperatura: faixa justa, senao a curva fica achatada
            g.y_axis.scaling.min = math.floor(min(temps)) - 1
            g.y_axis.scaling.max = math.ceil(max(temps)) + 1
        for eixo in (g.x_axis, g.y_axis):
            eixo.delete = False
            eixo.title.overlay = False
            eixo.majorGridlines.spPr = GraphicalProperties(ln=LineProperties(solidFill=cinza))
        g.title.overlay = False
        g.width, g.height = 33, altura
        s = Series(Reference(dados, min_col=col_y, min_row=1, max_row=len(regs) + 1),
                   Reference(dados, min_col=5 if em_horas else 4, min_row=2, max_row=len(regs) + 1),
                   title_from_data=True)
        s.marker.symbol = "none"
        s.graphicalProperties.line.solidFill = cor
        s.graphicalProperties.line.width = 22000
        s.smooth = False
        g.series.append(s)
        return g

    if validos:
        rel.add_chart(grafico("Condutividade × Tempo", 6, f"Condutividade ({UNIDADE})", azul, "0", 13), "A11")
    if temps:
        rel.add_chart(grafico("Temperatura × Tempo", 7, "Temperatura (°C)", "78909C", "0.0", 7), "A38")
    if not regs:
        rel["A11"] = "Nenhuma leitura registrada neste ensaio."

    destino = caminho_csv.with_suffix(".xlsx")
    try:
        livro.save(destino)
    except PermissionError:
        # o arquivo anterior esta aberto no Excel
        destino = destino.with_name(f"{destino.stem}_{datetime.now():%H%M%S}.xlsx")
        livro.save(destino)
    return destino


def formatar_cond(valor):
    """Texto curto para a tela: µS/cm abaixo de 1000, mS/cm acima."""
    if valor is None:
        return "-"
    if valor >= 1000:
        return f"{valor / 1000:.3f}".rstrip("0").rstrip(".").replace(".", ",") + " mS/cm"
    return f"{valor:.2f}".replace(".", ",") + f" {UNIDADE}"

