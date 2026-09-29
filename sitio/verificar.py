#!/usr/bin/env python3
"""Comprueba la web publicada: cada .md y .ots sellado (diario y carta) que se descarga de la
web tiene, byte a byte, la misma huella que en el repositorio.

Uso: python sitio/verificar.py https://reto.chiq.es [--origen .]
Sale con código 1 si algo no cuadra o falta.
"""
import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path


def descargar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "verificar-diario/1.0", "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--origen", type=Path, default=Path("."))
    a = p.parse_args()
    base = a.url.rstrip("/")
    fallos = comprobados = 0
    piezas = sorted((a.origen / "diario").glob("*.md")) + [a.origen / "carta-abierta.md"]
    for md in piezas:
        ots = md.with_name(md.name + ".ots")
        if not md.exists() or not ots.exists():
            continue  # sin sellar: no se publica
        for local in (md, ots):
            ruta = local.relative_to(a.origen).as_posix()
            esperado = hashlib.sha256(local.read_bytes()).hexdigest()
            try:
                obtenido = hashlib.sha256(descargar(f"{base}/{ruta}")).hexdigest()
            except Exception as e:
                obtenido = f"error: {e}"
            ok = obtenido == esperado
            fallos += not ok
            comprobados += 1
            print(f"{'OK   ' if ok else 'FALLO'} {ruta}  {esperado[:16]}…" + ("" if ok else f"  web: {obtenido[:40]}"))
    if not comprobados:
        print(f"No hay piezas selladas en {a.origen}: no se ha comprobado nada.")
        return 1
    print(f"Todo cuadra ({comprobados} archivos)." if not fallos else f"{fallos} de {comprobados} archivos no cuadran.")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
