# Ionix Logger: registrador de condutividade

Programa que transformou um condutivímetro de bancada (MS Tecnopon **mCA 150**), que só mostrava a leitura do momento no display, em um registrador contínuo para ensaios de laboratório de até 24 horas.

## O problema
O aparelho não guarda histórico. Em ensaios longos, alguém precisava anotar o valor de tempos em tempos, inclusive de madrugada.

## O que o programa faz
- Lê o aparelho pela porta **RS-232** (adaptador USB-serial) e **descobre sozinho a configuração** da comunicação, testando velocidades e formatos.
- Grava **cada leitura com data, hora, condutividade e temperatura**. Se faltar energia, o que já foi gravado não se perde.
- Mostra o **gráfico ao vivo** durante o ensaio e avisa quando o notebook está na bateria ou o aparelho para de responder.
- Converte mS para µS/cm na troca de escala, para a curva não "cair".
- Ao parar o ensaio, gera uma **planilha Excel com relatório e gráficos**.
- Tem um **simulador** do aparelho (`codigo/simulador.py`) para testar sem o equipamento.

O passo a passo de uso está em [`COMO USAR.txt`](COMO%20USAR.txt).

## Como rodar pelo código
```bash
pip install pyserial openpyxl
python codigo/aplicativo.py
```
Sem o aparelho, em outro terminal: `python codigo/simulador.py --porta 5050`, e no programa use a porta `socket://localhost:5050`.

## Tecnologias
Python · Tkinter · pyserial · openpyxl · PyInstaller · RS-232

---
Desenvolvido por **Matheus Ferreira de Sousa** · Ferreira Soluções em Engenharia · [LinkedIn](https://www.linkedin.com/in/matheus-ferreira-de-sousa/)
