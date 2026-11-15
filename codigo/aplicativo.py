"""Registrador de Condutividade para notebook: tela, grafico ao vivo e Excel."""
import json
import math
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import nucleo

CONGELADO = getattr(sys, "frozen", False)
BASE = Path(sys.executable).parent if CONGELADO else Path(__file__).resolve().parent.parent
RECURSOS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PASTA_DADOS = BASE / "dados"
ARQ_CONFIG = BASE / "configuracao.json"
VERSAO = "2.1"

# Duas cores: o azul escuro e o branco. Os demais tons sao o mesmo azul, mais claro.
ESCURO = "#263238"
ESCURO_HOVER = "#37474F"
TINTA = "#607D8B"       # textos secundarios
TINTA_CLARA = "#B0BEC5"
BORDA = "#CFD8DC"
CLARO = "#ECEFF1"
BRANCO = "#FFFFFF"
FONTE = "Segoe UI"
ALERTA_FALHAS = 3
MAX_PONTOS_TELA = 2500
REGISTRO = {"Todas as leituras": 0, "A cada 10 s": 10, "A cada 30 s": 30,
            "A cada 1 min": 60, "A cada 2 min": 120, "A cada 5 min": 300}


def escala(minimo, maximo, partes=6):
    """Marcas redondas para um eixo."""
    if maximo <= minimo:
        maximo = minimo + 1
    passo = (maximo - minimo) / partes
    base = 10 ** math.floor(math.log10(passo))
    passo = next(m * base for m in (1, 2, 2.5, 5, 10) if m * base >= passo)
    inicio = math.floor(minimo / passo + 1e-9) * passo
    fim = math.ceil(maximo / passo - 1e-9) * passo
    n = round((fim - inicio) / passo)
    return [inicio + i * passo for i in range(n + 1)]


def rotulo(valor):
    return f"{valor:g}".replace(".", ",")


class Botao(tk.Frame):
    """Botao plano: cheio (azul com texto branco) ou contorno (branco com borda azul)."""

    def __init__(self, pai, texto, comando, cheio=False, grande=False, padx=12):
        super().__init__(pai, bg=ESCURO, padx=0 if cheio else 1, pady=0 if cheio else 1)
        self.cheio, self.comando, self.ativo = cheio, comando, True
        self.rot = tk.Label(self, text=texto, cursor="hand2", padx=padx,
                            font=(FONTE, 12 if grande else 10, "bold" if grande else "normal"),
                            pady=10 if grande else 5)
        self.rot.pack(fill="both", expand=True)
        self.rot.bind("<Button-1>", lambda _: self.ativo and self.comando())
        self.rot.bind("<Enter>", lambda _: self._pintar(True))
        self.rot.bind("<Leave>", lambda _: self._pintar(False))
        self._pintar(False)

    def _pintar(self, sobre):
        if not self.ativo:
            fundo, letra, borda = (CLARO, TINTA_CLARA, CLARO) if self.cheio else (BRANCO, TINTA_CLARA, BORDA)
        elif self.cheio:
            fundo, letra, borda = (ESCURO_HOVER if sobre else ESCURO), BRANCO, ESCURO
        else:
            fundo, letra, borda = (CLARO if sobre else BRANCO), ESCURO, ESCURO
        self.rot.configure(bg=fundo, fg=letra, cursor="hand2" if self.ativo else "arrow")
        self.configure(bg=borda)

    def ativar(self, ativo):
        self.ativo = ativo
        self._pintar(False)

    def texto(self, texto):
        self.rot.configure(text=texto)


class Grafico(tk.Canvas):
    """Condutividade x tempo, desenhado direto no Tk (sem bibliotecas extras)."""

    def __init__(self, pai):
        super().__init__(pai, bg=BRANCO, highlightthickness=0)
        self.pontos = []
        self._agendado = False
        self.bind("<Configure>", lambda _: self.desenhar())

    def limpar(self):
        self.pontos = []
        self.desenhar()

    def adicionar(self, minutos, valor):
        self.pontos.append((minutos, valor))
        if not self._agendado:   # junta varias leituras num redesenho so
            self._agendado = True
            self.after(250, self.desenhar)

    def _amostra(self):
        n = len(self.pontos)
        if n <= MAX_PONTOS_TELA:
            return self.pontos
        passo = math.ceil(n / MAX_PONTOS_TELA)
        return [p for i, p in enumerate(self.pontos) if i % passo == 0 or p[1] is None] + [self.pontos[-1]]

    def desenhar(self):
        self._agendado = False
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 160 or h < 120:
            return
        e, d, c, b = 84, 36, 58, 52
        pontos = self._amostra()
        validos = [p for p in pontos if p[1] is not None]
        self.create_text(24, 26, anchor="w", text="Condutividade × Tempo", fill=ESCURO,
                         font=(FONTE, 13, "bold"))
        if not validos:
            self.create_text(w / 2, h / 2, fill=TINTA_CLARA, font=(FONTE, 11),
                             text="O gráfico aparece aqui quando a primeira leitura chegar")
            return

        tmax = max(p[0] for p in pontos)
        horas = tmax > 180
        k = 1 / 60 if horas else 1
        xs = escala(0, max(tmax * k, 1 / 60 if horas else 1))
        ymin, ymax = min(p[1] for p in validos), max(p[1] for p in validos)
        folga = (ymax - ymin) * 0.08 or max(abs(ymax) * 0.05, 1)
        baixo = ymin - folga
        if ymin >= 0:
            baixo = max(0, baixo)
        ys = escala(baixo, ymax + folga, 5)

        def px(x):
            return e + (x * k - xs[0]) / (xs[-1] - xs[0]) * (w - e - d)

        def py(y):
            return h - b - (y - ys[0]) / (ys[-1] - ys[0]) * (h - b - c)

        for x in xs:
            xx = e + (x - xs[0]) / (xs[-1] - xs[0]) * (w - e - d)
            self.create_line(xx, c, xx, h - b, fill=CLARO)
            self.create_text(xx, h - b + 15, text=rotulo(round(x, 6)), fill=TINTA, font=(FONTE, 9))
        for y in ys:
            self.create_line(e, py(y), w - d, py(y), fill=CLARO)
            self.create_text(e - 10, py(y), text=rotulo(round(y, 6)), anchor="e", fill=TINTA,
                             font=(FONTE, 9))
        self.create_line(e, h - b, w - d, h - b, fill=TINTA_CLARA)
        self.create_line(e, c, e, h - b, fill=TINTA_CLARA)
        self.create_text((e + w - d) / 2, h - 16, text="Tempo (h)" if horas else "Tempo (min)",
                         fill=TINTA, font=(FONTE, 9, "bold"))
        self.create_text(24, (c + h - b) / 2, text=f"Condutividade ({nucleo.UNIDADE})", angle=90,
                         fill=TINTA, font=(FONTE, 9, "bold"))

        marcar = len(validos) <= 60
        trecho = []
        for x, y in pontos + [(0, None)]:
            if y is not None:
                trecho += [px(x), py(y)]
                if marcar:
                    self.create_oval(px(x) - 2.5, py(y) - 2.5, px(x) + 2.5, py(y) + 2.5,
                                     fill=ESCURO, outline=ESCURO)
                continue
            if len(trecho) >= 4:
                self.create_line(*trecho, fill=ESCURO, width=2.5, smooth=False)
            trecho = []

        x, y = validos[-1]
        self.create_oval(px(x) - 9, py(y) - 9, px(x) + 9, py(y) + 9, fill=CLARO, outline="")
        self.create_oval(px(x) - 5, py(y) - 5, px(x) + 5, py(y) + 5, fill=ESCURO, outline=BRANCO, width=2)
        texto = nucleo.formatar_cond(y)
        lado = "e" if px(x) > w - 170 else "w"
        tx = px(x) + (-16 if lado == "e" else 16)
        ty = max(c + 12, py(y) - 18)
        etiqueta = self.create_text(tx, ty, anchor=lado, text=texto, fill=BRANCO, font=(FONTE, 9, "bold"))
        x1, y1, x2, y2 = self.bbox(etiqueta)
        fundo = self.create_rectangle(x1 - 7, y1 - 3, x2 + 7, y2 + 3, fill=ESCURO, outline=ESCURO)
        self.tag_lower(fundo, etiqueta)


class Aplicativo(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Registrador de Condutividade")
        self.geometry("1240x760")
        self.minsize(1000, 640)
        try:
            self.state("zoomed")
        except tk.TclError:
            pass
        self.configure(bg=BRANCO)
        self.eventos = queue.Queue()
        self.ensaio = None
        self.busca_parar = None
        self.ultimo_csv = None
        self.ultimo_xlsx = None
        self.momento_inicio = None
        self.hora_ultima = None
        self.n_validas = 0
        self.n_falhas = 0
        self.cond_max = None
        self.bateria = False
        self.portas = {}
        self.alerta = False
        self._piscar = False

        self._estilos()
        self._montar()
        self._carregar_config()
        self.atualizar_portas()
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.after(200, self._receber)
        self.after(1000, self._relogio)
        self.after(700, self._pisca)

    def _estilos(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", font=(FONTE, 10), background=BRANCO, foreground=ESCURO)
        s.configure("TLabel", background=BRANCO, foreground=ESCURO)
        s.configure("Dica.TLabel", foreground=TINTA, font=(FONTE, 8))
        campo = dict(fieldbackground=BRANCO, background=BRANCO, foreground=ESCURO, bordercolor=BORDA,
                     lightcolor=BRANCO, darkcolor=BRANCO, arrowcolor=ESCURO, padding=(6, 4),
                     insertcolor=ESCURO, selectbackground=ESCURO, selectforeground=BRANCO)
        s.configure("TCombobox", **campo)
        s.configure("TEntry", **campo)
        estados = dict(fieldbackground=[("disabled", CLARO), ("readonly", BRANCO)],
                       foreground=[("disabled", TINTA)],
                       bordercolor=[("focus", ESCURO), ("hover", TINTA_CLARA)],
                       lightcolor=[("focus", BRANCO)], darkcolor=[("focus", BRANCO)],
                       arrowcolor=[("disabled", TINTA_CLARA)],
                       selectbackground=[("readonly", BRANCO), ("!focus", BRANCO)],
                       selectforeground=[("readonly", ESCURO), ("!focus", ESCURO)])
        s.map("TCombobox", **estados)
        s.map("TEntry", **{k: v for k, v in estados.items() if k != "arrowcolor"})
        s.configure("Vertical.TScrollbar", background=ESCURO_HOVER, troughcolor=ESCURO, bordercolor=ESCURO,
                    lightcolor=ESCURO_HOVER, darkcolor=ESCURO_HOVER, arrowcolor=TINTA_CLARA, gripcount=0)
        s.map("Vertical.TScrollbar", background=[("active", TINTA)])
        self.option_add("*TCombobox*Listbox.font", (FONTE, 10))
        self.option_add("*TCombobox*Listbox.selectBackground", ESCURO)
        self.option_add("*TCombobox*Listbox.selectForeground", BRANCO)
        self.option_add("*TCombobox*Listbox.background", BRANCO)
        self.option_add("*TCombobox*Listbox.foreground", ESCURO)

    # ---------------------------------------------------------------- tela
    def _montar(self):
        topo = tk.Frame(self, bg=BRANCO)
        topo.pack(fill="x")
        try:
            self.logo = tk.PhotoImage(file=RECURSOS / "logo.png")
            tk.Label(topo, image=self.logo, bg=BRANCO).pack(side="left", padx=(20, 10), pady=10)
        except tk.TclError:
            pass
        tk.Frame(topo, bg=BORDA, width=1).pack(side="left", fill="y", pady=16, padx=(4, 14))
        tk.Label(topo, text="Registrador de Condutividade", bg=BRANCO, fg=ESCURO,
                 font=(FONTE, 18, "bold")).pack(side="left")
        tk.Label(topo, text=f"v{VERSAO}", bg=BRANCO, fg=TINTA_CLARA, font=(FONTE, 9)).pack(side="left", padx=10, pady=(10, 0))
        arquivos = tk.Frame(topo, bg=BRANCO)
        arquivos.pack(side="right", padx=20)
        Botao(arquivos, "Abrir pasta dos dados", self.abrir_pasta).pack(side="right")
        Botao(arquivos, "Gerar planilha de um CSV...", self.gerar).pack(side="right", padx=8)
        self.b_abrir_xlsx = Botao(arquivos, "Abrir planilha do ensaio", self.abrir_planilha, cheio=True)
        self.b_abrir_xlsx.pack(side="right")
        self.b_abrir_xlsx.ativar(False)

        self.faixa = tk.Label(self, text="Pronto. Escolha a porta e inicie o ensaio.", anchor="w",
                              bg=ESCURO, fg=BRANCO, font=(FONTE, 11, "bold"), padx=20, pady=9)
        self.faixa.pack(fill="x")

        corpo = tk.Frame(self, bg=BRANCO)
        corpo.pack(fill="both", expand=True, padx=20, pady=16)
        esquerda = tk.Frame(corpo, bg=BRANCO, width=300)
        esquerda.pack(side="left", fill="y", padx=(0, 18))
        esquerda.pack_propagate(False)
        direita = tk.Frame(corpo, bg=BRANCO)
        direita.pack(side="left", fill="both", expand=True)

        self.v_porta = tk.StringVar()
        self.v_baud = tk.StringVar(value="9600")
        self.v_formato = tk.StringVar(value="8N1")
        self.v_registro = tk.StringVar(value="Todas as leituras")
        self.v_comando = tk.StringVar()
        self.v_amostra = tk.StringVar()

        # --- 1. conexao
        cx = self._cartao(esquerda, "1", "CONEXÃO")
        cx.columnconfigure(0, minsize=86)
        cx.columnconfigure(1, weight=1)
        ttk.Label(cx, text="Porta").grid(row=0, column=0, sticky="w")
        linha = tk.Frame(cx, bg=BRANCO)
        linha.grid(row=0, column=1, sticky="ew", pady=3)
        self.c_porta = ttk.Combobox(linha, textvariable=self.v_porta, width=18)
        self.c_porta.pack(side="left", fill="x", expand=True)
        self.b_atualizar = Botao(linha, "↻", self.atualizar_portas)
        self.b_atualizar.rot.configure(width=3, font=(FONTE, 11), pady=2)
        self.b_atualizar.pack(side="left", padx=(6, 0))
        ttk.Label(cx, text="Velocidade").grid(row=1, column=0, sticky="w")
        vel = tk.Frame(cx, bg=BRANCO)
        vel.grid(row=1, column=1, sticky="ew", pady=3)
        self.c_baud = ttk.Combobox(vel, textvariable=self.v_baud, width=7,
                                   values=[str(b) for b in sorted(nucleo.BAUDS)])
        self.c_baud.pack(side="left")
        self.c_formato = ttk.Combobox(vel, textvariable=self.v_formato, width=5, state="readonly",
                                      values=list(nucleo.FORMATOS))
        self.c_formato.pack(side="left", padx=(6, 0))
        ttk.Label(cx, text="Comando").grid(row=2, column=0, sticky="w")
        cmd = tk.Frame(cx, bg=BRANCO)
        cmd.grid(row=2, column=1, sticky="ew", pady=3)
        self.e_comando = ttk.Entry(cmd, textvariable=self.v_comando, width=7)
        self.e_comando.pack(side="left")
        ttk.Label(cmd, text="vazio no mCA 150", style="Dica.TLabel").pack(side="left", padx=(8, 0))
        self.b_procurar = Botao(cx, "Procurar sinal do aparelho", self.procurar)
        self.b_procurar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        # --- 2. ensaio
        en = self._cartao(esquerda, "2", "ENSAIO")
        en.columnconfigure(0, minsize=86)
        en.columnconfigure(1, weight=1)
        ttk.Label(en, text="Amostra").grid(row=0, column=0, sticky="w")
        self.e_amostra = ttk.Entry(en, textvariable=self.v_amostra)
        self.e_amostra.grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(en, text="Registrar").grid(row=1, column=0, sticky="w")
        self.c_registro = ttk.Combobox(en, textvariable=self.v_registro, state="readonly",
                                       values=list(REGISTRO), width=18)
        self.c_registro.grid(row=1, column=1, sticky="ew", pady=3)
        self.l_reconhecida = ttk.Label(en, text="Última leitura reconhecida: -", style="Dica.TLabel",
                                       wraplength=266)
        self.l_reconhecida.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # --- botoes
        self.b_iniciar = Botao(esquerda, "▶   INICIAR ENSAIO", self.iniciar, cheio=True, grande=True)
        self.b_iniciar.pack(fill="x", pady=(2, 0))
        self.b_parar = Botao(esquerda, "■   PARAR ENSAIO", self.parar, grande=True)
        self.b_parar.pack(fill="x", pady=(8, 0))
        self.b_parar.ativar(False)

        # --- placas de valor
        placas = tk.Frame(direita, bg=BRANCO)
        placas.pack(fill="x")
        for i in range(4):
            placas.columnconfigure(i, weight=1, uniform="placa")
        self.t_cond, self.s_cond = self._placa(placas, 0, "CONDUTIVIDADE", destaque=True)
        self.t_temp, self.s_temp = self._placa(placas, 1, "TEMPERATURA")
        self.t_tempo, self.s_tempo = self._placa(placas, 2, "TEMPO DE ENSAIO")
        self.t_reg, self.s_reg = self._placa(placas, 3, "LEITURAS GRAVADAS")

        moldura = tk.Frame(direita, bg=BORDA, padx=1, pady=1)
        moldura.pack(fill="both", expand=True, pady=14)
        self.grafico = Grafico(moldura)
        self.grafico.pack(fill="both", expand=True)

        fundo = tk.Frame(direita, bg=ESCURO)
        fundo.pack(fill="x")
        tk.Label(fundo, text="TEXTO RECEBIDO DO APARELHO", bg=ESCURO, fg=TINTA_CLARA,
                 font=(FONTE, 8, "bold"), anchor="w", padx=12, pady=6).pack(fill="x")
        caixa = tk.Frame(fundo, bg=ESCURO)
        caixa.pack(fill="x", padx=(12, 0), pady=(0, 8))
        self.texto = tk.Text(caixa, height=4, state="disabled", font=("Consolas", 9), bg=ESCURO,
                             fg=CLARO, relief="flat", wrap="none", borderwidth=0, highlightthickness=0,
                             selectbackground=ESCURO_HOVER, insertbackground=BRANCO)
        barra = ttk.Scrollbar(caixa, command=self.texto.yview)
        self.texto.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        self.texto.pack(side="left", fill="x", expand=True)
        self.texto.tag_configure("info", foreground=TINTA_CLARA)
        self.texto.tag_configure("erro", foreground=BRANCO, font=("Consolas", 9, "bold"))

        self.travaveis = [self.c_porta, self.c_baud, self.c_formato, self.e_comando,
                          self.e_amostra, self.c_registro]
        self.botoes_travaveis = [self.b_atualizar, self.b_procurar]

    def _cartao(self, pai, numero, titulo):
        moldura = tk.Frame(pai, bg=BORDA, padx=1, pady=1)
        moldura.pack(fill="x", pady=(0, 14))
        interno = tk.Frame(moldura, bg=BRANCO)
        interno.pack(fill="both")
        cab = tk.Frame(interno, bg=BRANCO)
        cab.pack(fill="x", padx=16, pady=(12, 6))
        tk.Label(cab, text=numero, bg=ESCURO, fg=BRANCO, font=(FONTE, 9, "bold"), width=2).pack(side="left")
        tk.Label(cab, text=titulo, bg=BRANCO, fg=ESCURO, font=(FONTE, 10, "bold")).pack(side="left", padx=8)
        dentro = tk.Frame(interno, bg=BRANCO)
        dentro.pack(fill="x", padx=16, pady=(0, 14))
        return dentro

    def _placa(self, pai, coluna, titulo, destaque=False):
        fundo, letra, apoio = (ESCURO, BRANCO, TINTA_CLARA) if destaque else (BRANCO, ESCURO, TINTA)
        borda = tk.Frame(pai, bg=ESCURO if destaque else BORDA, padx=1, pady=1)
        borda.grid(row=0, column=coluna, sticky="nsew", padx=(0 if coluna == 0 else 12, 0))
        q = tk.Frame(borda, bg=fundo)
        q.pack(fill="both", expand=True)
        tk.Label(q, text=titulo, bg=fundo, fg=apoio, font=(FONTE, 8, "bold")).pack(anchor="w", padx=16, pady=(14, 0))
        valor = tk.Label(q, text="-", bg=fundo, fg=letra, font=(FONTE, 22, "bold"))
        valor.pack(anchor="w", padx=16)
        sub = tk.Label(q, text=" ", bg=fundo, fg=apoio, font=(FONTE, 9))
        sub.pack(anchor="w", padx=16, pady=(0, 14))
        return valor, sub

    def faixa_msg(self, texto, alerta=False):
        self.alerta = alerta
        self.faixa.configure(text=("⚠   " + texto) if alerta else texto, bg=ESCURO, fg=BRANCO)

    def _pisca(self):
        # alerta: a faixa alterna azul e branco para chamar atencao
        if self.alerta:
            self._piscar = not self._piscar
            self.faixa.configure(bg=BRANCO if self._piscar else ESCURO, fg=ESCURO if self._piscar else BRANCO)
        self.after(700, self._pisca)

    def escrever(self, texto, tag=None):
        self.texto.configure(state="normal")
        self.texto.insert("end", texto + "\n", tag or ())
        if int(self.texto.index("end-1c").split(".")[0]) > 600:
            self.texto.delete("1.0", "200.0")
        self.texto.configure(state="disabled")
        self.texto.see("end")

    def travar(self, travado):
        for item in self.travaveis:
            item.configure(state="disabled" if travado else "normal")
        for botao in self.botoes_travaveis:
            botao.ativar(not travado)
        if not travado:
            self.c_formato.configure(state="readonly")
            self.c_registro.configure(state="readonly")

    # --------------------------------------------------------------- dados
    def atualizar_portas(self):
        atual = self.porta()
        self.portas = {f"{d} - {t}": d for d, t in nucleo.listar_portas()}
        self.c_porta["values"] = list(self.portas)
        rotulo_atual = next((r for r, d in self.portas.items() if d == atual), None)
        if rotulo_atual:
            self.v_porta.set(rotulo_atual)
        elif self.portas and not atual:
            self.v_porta.set(next(iter(self.portas)))

    def porta(self):
        texto = self.v_porta.get().strip()
        return self.portas.get(texto, texto.split(" - ")[0].strip())

    def ler_config(self):
        porta = self.porta()
        if not porta:
            raise ValueError("Escolha a porta. Se nenhuma aparecer, ligue o cabo USB-RS232 e clique em ↻.")
        try:
            baud = int(self.v_baud.get())
        except ValueError:
            raise ValueError("Confira a velocidade.") from None
        return nucleo.Config(porta=porta, baud=baud, formato=self.v_formato.get(),
                             intervalo=REGISTRO.get(self.v_registro.get(), 0),
                             comando=self.v_comando.get().strip(), amostra=self.v_amostra.get(),
                             pasta=PASTA_DADOS)

    def _carregar_config(self):
        try:
            dados = json.loads(ARQ_CONFIG.read_text(encoding="utf8"))
        except (OSError, ValueError):
            return
        for chave, var in (("porta", self.v_porta), ("baud", self.v_baud), ("formato", self.v_formato),
                           ("comando", self.v_comando), ("amostra", self.v_amostra)):
            if chave in dados:
                var.set(str(dados[chave]))
        if dados.get("registro") in REGISTRO:
            self.v_registro.set(dados["registro"])

    def _salvar_config(self):
        dados = {"porta": self.porta(), "baud": self.v_baud.get(), "formato": self.v_formato.get(),
                 "registro": self.v_registro.get(), "comando": self.v_comando.get(),
                 "amostra": self.v_amostra.get()}
        try:
            ARQ_CONFIG.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf8")
        except OSError:
            pass

    # -------------------------------------------------------------- acoes
    def procurar(self):
        porta = self.porta()
        if not porta:
            messagebox.showwarning("Porta", "Escolha a porta primeiro.")
            return
        if self.busca_parar:
            self.busca_parar.set()
            return
        self.busca_parar = threading.Event()
        self.b_procurar.texto("Cancelar procura")
        self.faixa_msg("Procurando sinal. Deixe o aparelho ligado, medindo.")
        comando, parar = self.v_comando.get().strip(), self.busca_parar

        def tarefa():
            achado = nucleo.descobrir(porta, comando, parar,
                                      lambda t: self.eventos.put(("busca", t)))
            self.eventos.put(("busca_fim", achado))

        threading.Thread(target=tarefa, daemon=True).start()

    def iniciar(self):
        try:
            cfg = self.ler_config()
        except ValueError as erro:
            messagebox.showwarning("Configuração", str(erro))
            return
        if self.busca_parar:
            self.busca_parar.set()
        self._salvar_config()
        try:
            self.ensaio = nucleo.Ensaio(cfg, self.eventos)
        except OSError as erro:
            messagebox.showerror("Pasta dos dados", str(erro))
            return
        self.ultimo_csv = self.ensaio.caminho
        self.momento_inicio = datetime.now()
        self.hora_ultima = None
        self.n_validas = self.n_falhas = 0
        self.cond_max = None
        self.grafico.limpar()
        self.travar(True)
        self.b_iniciar.ativar(False)
        self.b_parar.ativar(True)
        for placa in (self.t_cond, self.t_temp):
            placa.configure(text="-")
        self.s_cond.configure(text=" ")
        self.t_reg.configure(text="0")
        self.s_reg.configure(text="falhas: 0")
        self.s_tempo.configure(text=f"início {self.momento_inicio:%H:%M:%S}")
        self.escrever(f"Ensaio iniciado. Arquivo: {self.ensaio.caminho}", "info")
        self.faixa_msg("Conectando...")
        self.ensaio.start()

    def parar(self):
        if self.ensaio and messagebox.askyesno("Parar ensaio", "Encerrar o ensaio agora?"):
            self.b_parar.ativar(False)
            self.ensaio.parar()

    def gerar(self):
        alvo = filedialog.askopenfilename(initialdir=PASTA_DADOS, title="Escolha o ensaio (CSV)",
                                          filetypes=[("Ensaios CSV", "*.csv")])
        if alvo:
            self._gerar(alvo)

    def _gerar(self, alvo, depois=None):
        """Gera a planilha em segundo plano (ensaios longos levam alguns segundos)."""
        self.escrever(f"Gerando planilha de {Path(alvo).name}...", "info")

        def tarefa():
            try:
                self.eventos.put(("excel", (nucleo.gerar_excel(alvo), None, depois)))
            except (OSError, ValueError, IndexError) as erro:
                self.eventos.put(("excel", (None, erro, depois)))

        threading.Thread(target=tarefa, daemon=True).start()

    def abrir_planilha(self):
        if self.ultimo_xlsx and self.ultimo_xlsx.exists():
            os.startfile(self.ultimo_xlsx)

    def abrir_pasta(self):
        PASTA_DADOS.mkdir(parents=True, exist_ok=True)
        os.startfile(PASTA_DADOS)

    def fechar(self):
        if self.ensaio and self.ensaio.is_alive():
            if not messagebox.askyesno("Ensaio em andamento",
                                       "Há um ensaio gravando. Fechar encerra o registro. Fechar mesmo assim?"):
                return
            self.ensaio.parar()
            self.ensaio.join(3)
            if self.ultimo_csv:
                try:
                    nucleo.gerar_excel(self.ultimo_csv)
                except (OSError, ValueError, IndexError):
                    pass
        self.destroy()

    # ------------------------------------------------------------ eventos
    def _receber(self):
        try:
            for _ in range(500):
                tipo, dados = self.eventos.get_nowait()
                getattr(self, f"_ev_{tipo}")(dados)
        except queue.Empty:
            pass
        self.after(200, self._receber)

    def _mostrar_reconhecida(self, linha):
        cond, temp = nucleo.interpretar(linha)
        if cond is None:
            return
        texto = nucleo.formatar_cond(cond)
        if temp is not None:
            texto += f"  •  {rotulo(temp)} °C"
        self.l_reconhecida.configure(text=f"Última leitura reconhecida: {texto}")

    def _ev_linha(self, linha):
        self.escrever(linha)
        self._mostrar_reconhecida(linha)

    def _ev_conexao(self, ok):
        if ok:
            self.faixa_msg("●   Gravando. Conexão com o aparelho ativa.")
            self.escrever("Porta aberta.", "info")
        else:
            self.faixa_msg("SEM CONEXÃO COM A PORTA. Tentando reconectar: confira o cabo USB.", alerta=True)

    def _ev_erro(self, texto):
        self.escrever(texto, "erro")

    def _ev_registro(self, r):
        cond, temp = r["cond"], r["temp"]
        if cond is None:
            self.n_falhas += 1
        else:
            self.n_validas += 1
            self.hora_ultima = r["hora"]
            self.cond_max = cond if self.cond_max is None else max(self.cond_max, cond)
            self.t_cond.configure(text=nucleo.formatar_cond(cond))
            self.s_cond.configure(text=f"máxima: {nucleo.formatar_cond(self.cond_max)}")
            self.grafico.adicionar(r["minutos"], cond)
        if temp is not None:
            self.t_temp.configure(text=f"{rotulo(temp)} °C")
        if cond is None:
            self.grafico.adicionar(r["minutos"], None)
        self.t_reg.configure(text=f"{self.n_validas:,}".replace(",", "."))
        self.s_reg.configure(text=f"falhas: {self.n_falhas}",
                             font=(FONTE, 9, "bold" if self.n_falhas else "normal"))
        if r["falhas"] >= ALERTA_FALHAS:
            self.faixa_msg(f"ATENÇÃO: {r['falhas']} registros seguidos sem leitura. Confira cabo e aparelho.",
                           alerta=True)
        elif r["falhas"] == 0 and self.alerta and not self.bateria:
            self.faixa_msg("●   Gravando. Conexão com o aparelho ativa.")

    def _ev_fim(self, caminho):
        self.ensaio = None
        self.momento_inicio = None
        self.travar(False)
        self.b_iniciar.ativar(True)
        self.b_parar.ativar(False)
        self.faixa_msg("Ensaio encerrado. Gerando a planilha Excel...")
        self._gerar(caminho, depois="fim")

    def _ev_excel(self, dados):
        destino, erro, depois = dados
        if erro:
            self.escrever(f"Não consegui gerar a planilha: {erro}", "erro")
            messagebox.showerror("Excel", f"Não consegui gerar a planilha:\n{erro}")
            return
        self.ultimo_xlsx = destino
        self.b_abrir_xlsx.ativar(True)
        self.escrever(f"Planilha pronta: {destino}", "info")
        if depois == "fim":
            self.faixa_msg(f"✓   Ensaio encerrado. Planilha pronta: {destino.name}")

    def _ev_busca(self, texto):
        self.escrever(texto, "info")

    def _ev_busca_fim(self, achado):
        self.busca_parar = None
        self.b_procurar.texto("Procurar sinal do aparelho")
        if self.ensaio:
            return
        if not achado:
            self.faixa_msg("Sinal não encontrado. Veja o guia: cabo invertido ou aparelho exige comando.",
                           alerta=True)
            return
        baud, formato, amostra = achado
        self.v_baud.set(str(baud))
        self.v_formato.set(formato)
        linhas = [x.strip() for x in amostra.replace("\r", "\n").split("\n") if x.strip()]
        for linha in reversed(linhas[1:-1] or linhas):
            if nucleo.interpretar(linha)[0] is not None:
                self._mostrar_reconhecida(linha)
                break
        self._salvar_config()
        self.faixa_msg(f"✓   Sinal encontrado: {baud} baud, {formato}. Pode iniciar o ensaio.")

    def _relogio(self):
        if self.momento_inicio:
            s = int((datetime.now() - self.momento_inicio).total_seconds())
            self.t_tempo.configure(text=f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")
            if self.hora_ultima:
                atraso = int((datetime.now() - self.hora_ultima).total_seconds())
                self.s_cond.configure(text=f"máxima: {nucleo.formatar_cond(self.cond_max)}"
                                      + (f"  •  há {atraso} s" if atraso >= 5 else ""))
            bateria = nucleo.na_bateria()
            if bateria and not self.bateria:
                self.faixa_msg("ATENÇÃO: notebook fora da tomada. Religue o carregador.", alerta=True)
            elif self.bateria and not bateria:
                self.faixa_msg("●   Gravando. Notebook de volta à tomada.")
            self.bateria = bateria
        self.after(1000, self._relogio)


def principal():
    # uso sem tela:  RegistradorCondutividade.exe --excel ensaio.csv
    if len(sys.argv) == 3 and sys.argv[1] == "--excel":
        nucleo.gerar_excel(sys.argv[2])
        return
    Aplicativo().mainloop()


if __name__ == "__main__":
    principal()
