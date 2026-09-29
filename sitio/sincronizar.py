#!/usr/bin/env python3
"""Mantiene la web al día con el repositorio público.

Cada INTERVALO segundos trae la rama. Si cambió (o si en la última compilación faltaron
datos de algún bloque por falta de red), regenera el sitio en una carpeta nueva y cambia
el enlace <BASE>/sitios/actual de golpe. Si la compilación falla, la web sigue con la
versión anterior.

Sin REPO_URL, publica una vez el diario incluido en la imagen y termina.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build  # noqa: E402

REPO_URL = os.environ.get("REPO_URL", "")
RAMA = os.environ.get("RAMA", "main")
INTERVALO = int(os.environ.get("INTERVALO", "60"))
BASE = Path(os.environ.get("BASE", "/srv"))
REPO = BASE / "repo"
INICIAL = BASE / "repo-inicial"
SITIOS = BASE / "sitios"
ACTUAL = SITIOS / "actual"
CACHE = BASE / "cache" / "bloques.json"


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def git(*args):
    r = subprocess.run(["git", "-c", "core.autocrlf=false", *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def publicar(origen, etiqueta):
    """Compila en una carpeta nueva y la pone en línea. Devuelve (ok, completo)."""
    destino = SITIOS / f"sitio-{etiqueta}-{int(time.time())}"
    try:
        entradas = build.compilar(origen, destino, con_red=True, ruta_cache=CACHE)
    except Exception as e:
        log(f"compilación fallida ({etiqueta}): {e}. La web sigue con la versión anterior.")
        shutil.rmtree(destino, ignore_errors=True)
        return False, True
    tmp = SITIOS / "actual.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(destino.name)
    os.replace(tmp, ACTUAL)  # cambio atómico del enlace
    for viejo in SITIOS.glob("sitio-*"):
        if viejo != destino:
            shutil.rmtree(viejo, ignore_errors=True)
    completo = all(b.hora for e in entradas for b in e.anclajes)
    log(f"publicado {etiqueta}: {len(entradas)} entradas" + ("" if completo else " (faltan datos de algún bloque)"))
    return True, completo


def main():
    SITIOS.mkdir(parents=True, exist_ok=True)
    if not REPO_URL:
        log("sin REPO_URL: se publica solo el diario incluido en la imagen")
        publicar(INICIAL, "imagen")
        return 0
    publicado, completo = None, True
    while True:
        try:
            if not (REPO / ".git").exists():
                shutil.rmtree(REPO, ignore_errors=True)
                git("clone", "--depth", "1", "--branch", RAMA, REPO_URL, str(REPO))
            else:
                git("-C", str(REPO), "fetch", "--depth", "1", "origin", RAMA)
                git("-C", str(REPO), "reset", "--hard", "FETCH_HEAD")
            commit = git("-C", str(REPO), "rev-parse", "HEAD")
            if commit != publicado or not completo:
                ok, completo = publicar(REPO, commit[:7])
                publicado = commit  # si falló, no se reintenta hasta el siguiente commit
        except Exception as e:
            log(f"error al sincronizar: {e}")
        time.sleep(INTERVALO)


if __name__ == "__main__":
    sys.exit(main())
