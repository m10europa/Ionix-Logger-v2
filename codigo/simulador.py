"""Finge ser o mCA 150 em uma porta TCP, para testar sem o aparelho.

Envia no mesmo formato visto no aparelho real (linhas terminadas em CR):
    419.74 ,uS,27.6,º        abaixo de 2000 µS/cm
    2.21 ,mS,27.7,ºC         acima, em mS
     ,uS,27.5,:              valor em branco na troca de escala

Uso:   python simulador.py [--porta 5050] [--periodo 1] [--comando R] [--acelerar 20]
No aplicativo, digite como porta:   socket://localhost:5050
"""
import argparse
import math
import socket
import time


def valor(segundos):
    """Curva parecida com a oxidacao: base estavel e subida depois da inducao."""
    return 400 + 3600 / (1 + math.exp(-(segundos - 60) / 12))


def texto(inicio, n, acelerar=1.0):
    t = (time.monotonic() - inicio) * acelerar
    temp = 27.5 + 0.3 * math.sin(t / 30)
    if n % 17 == 5:
        linha = f" ,uS,{temp:.1f},:"
    elif valor(t) < 2000:
        linha = f"{valor(t):.2f} ,uS,{temp:.1f},º"
    else:
        linha = f"{valor(t) / 1000:.2f} ,mS,{temp:.1f},ºC "
    return (linha + "\r").encode("latin-1")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--porta", type=int, default=5050)
    p.add_argument("--periodo", type=float, default=1.0)
    p.add_argument("--comando", help="so responde quando receber este texto")
    p.add_argument("--acelerar", type=float, default=1.0, help="faz a curva andar N vezes mais rapido")
    a = p.parse_args()

    with socket.create_server(("127.0.0.1", a.porta)) as servidor:
        print(f"Simulador em socket://localhost:{a.porta}", flush=True)
        inicio = time.monotonic()
        n = 0
        while True:
            conexao, _ = servidor.accept()
            with conexao:
                conexao.settimeout(a.periodo)
                try:
                    while True:
                        if a.comando:
                            try:
                                pedido = conexao.recv(64)
                            except TimeoutError:
                                continue
                            if not pedido:
                                break
                            if a.comando.encode() not in pedido:
                                continue
                        else:
                            time.sleep(a.periodo)
                        conexao.sendall(texto(inicio, n, a.acelerar))
                        n += 1
                except OSError:
                    pass


if __name__ == "__main__":
    main()
