#!/usr/bin/env python3
"""Genera la web estática del diario sellado.

Entrada: <origen>/diario/<nombre>.md y <nombre>.md.ots, y <origen>/carta-abierta.md con su .ots
Salida:  <destino>/index.html, /verificar/, /carta/, /diario/<nombre>/index.html y, en la misma
         ruta que en el repositorio, la copia byte a byte de cada .md y su .ots (el descargable
         es el archivo sellado).

Una pieza solo se publica si tiene su .ots y este corresponde a la huella del .md.
Si un .ots no cuadra, la compilación falla entera: mejor seguir con la web anterior
que publicar una prueba rota.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from markdown_it import MarkdownIt
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
from opentimestamps.core.serialize import StreamDeserializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile

MADRID = ZoneInfo("Europe/Madrid")
MEMPOOL = "https://mempool.space"
ESTILO = Path(__file__).with_name("estilo.css")
CARTA = "carta-abierta.md"
IDIOMA_CARTA = "en"  # texto canónico en inglés
NOMBRE_VALIDO = re.compile(r"[a-z0-9][a-z0-9-]*")
FECHA = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

TITULO_SITIO = "Muse escribe a Muse"
LEMA = ("Diario público del reto. Cada entrada se publica tal como se selló, con su huella "
        "SHA-256 y su prueba de OpenTimestamps anclada en Bitcoin.")

_md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


class ErrorDeSello(Exception):
    """Un .ots está dañado, no corresponde a su .md o su anclaje no coincide con la cadena."""


@dataclass
class Bloque:
    altura: int
    hash: str | None = None
    hora: dt.datetime | None = None


@dataclass
class Entrada:
    nombre: str
    ruta: str  # ruta del .md en el repositorio, p. ej. "diario/2026-09-27.md" o "carta-abierta.md"
    titulo: str
    sha256: str
    texto: str
    bloques: list[int]
    anclajes: list[Bloque] = field(default_factory=list)

    @property
    def es_carta(self) -> bool:
        return self.ruta == CARTA

    @property
    def url(self) -> str:
        return "/carta/" if self.es_carta else f"/diario/{self.nombre}/"

    @property
    def fecha(self) -> dt.date | None:
        m = FECHA.match(self.nombre)
        return dt.date(*map(int, m.groups())) if m else None

    @property
    def fijada(self) -> bool:
        return self.fecha is None and not self.es_carta


def a_html(texto: str) -> str:
    """Markdown a HTML. El HTML crudo se escapa: una entrada no puede inyectar código."""
    return _md.render(texto)


def leer_sello(md: bytes, ruta_ots: Path) -> dict[int, bytes]:
    """Devuelve {altura del bloque: mensaje anclado} y comprueba que el .ots es de este .md."""
    try:
        with ruta_ots.open("rb") as f:
            dtf = DetachedTimestampFile.deserialize(StreamDeserializationContext(f))
    except Exception as e:  # la librería lanza varios tipos según dónde esté el daño
        raise ErrorDeSello(f"{ruta_ots.name} está dañado: {e}") from e
    if dtf.file_hash_op.TAG_NAME != "sha256" or dtf.file_digest != hashlib.sha256(md).digest():
        raise ErrorDeSello(f"{ruta_ots.name} no corresponde a la huella de su .md")
    return {att.height: msg for msg, att in dtf.timestamp.all_attestations()
            if isinstance(att, BitcoinBlockHeaderAttestation)}


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.read().decode()


def comprobar_bloque(altura: int, mensaje: bytes, cache: dict) -> Bloque:
    """Compara el anclaje con el bloque real. Sin red, devuelve solo la altura."""
    merkle = mensaje[::-1].hex()
    guardado = cache.get(str(altura))
    if not guardado:
        try:
            hash_ = _get(f"{MEMPOOL}/api/block-height/{altura}").strip()
            info = json.loads(_get(f"{MEMPOOL}/api/block/{hash_}"))
        except Exception as e:
            print(f"aviso: sin datos del bloque {altura} ({e})", file=sys.stderr)
            return Bloque(altura)
        guardado = {"hash": hash_, "hora": info["timestamp"], "merkle": info["merkle_root"]}
        cache[str(altura)] = guardado
    if guardado["merkle"] != merkle:
        raise ErrorDeSello(f"el anclaje en el bloque {altura} no coincide con la cadena de Bitcoin")
    hora = dt.datetime.fromtimestamp(guardado["hora"], dt.timezone.utc).astimezone(MADRID)
    return Bloque(altura, guardado["hash"], hora)


def leer_pieza(origen: Path, ruta: str, con_red: bool, cache: dict) -> Entrada | None:
    """Lee un .md y su .ots. Devuelve None si no está sellado."""
    ruta_md = origen / ruta
    ruta_ots = ruta_md.with_name(ruta_md.name + ".ots")
    if not ruta_ots.exists():
        print(f"aviso: {ruta} no tiene .ots; no se publica", file=sys.stderr)
        return None
    md = ruta_md.read_bytes()
    anclados = leer_sello(md, ruta_ots)
    texto = md.decode("utf-8")
    m = re.search(r"^# +(.+?)\s*$", texto, re.M)
    nombre = ruta_md.stem
    pieza = Entrada(nombre, ruta, m.group(1) if m else nombre, hashlib.sha256(md).hexdigest(),
                    texto, sorted(anclados))
    if con_red:
        pieza.anclajes = [comprobar_bloque(h, anclados[h], cache) for h in pieza.bloques]
    else:
        pieza.anclajes = [Bloque(h) for h in pieza.bloques]
    return pieza


def leer_entradas(origen: Path, con_red: bool, cache: dict) -> list[Entrada]:
    """Carta (si está sellada), después las piezas fijadas (el acta) y el diario del más reciente al más antiguo."""
    entradas = []
    for ruta_md in sorted((origen / "diario").glob("*.md")):
        if not NOMBRE_VALIDO.fullmatch(ruta_md.stem):
            print(f"aviso: {ruta_md.name} tiene un nombre no válido; no se publica", file=sys.stderr)
            continue
        pieza = leer_pieza(origen, f"diario/{ruta_md.name}", con_red, cache)
        if pieza:
            entradas.append(pieza)
    carta = leer_pieza(origen, CARTA, con_red, cache) if (origen / CARTA).exists() else None
    fijadas = sorted((e for e in entradas if e.fijada), key=lambda e: e.nombre)
    diario = sorted((e for e in entradas if not e.fijada), key=lambda e: e.nombre, reverse=True)
    return ([carta] if carta else []) + fijadas + diario


# ---------- Presentación ----------

def fecha_larga(d: dt.date) -> str:
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def estado_sello(e: Entrada, enlaces: bool = True) -> str:
    if not e.anclajes:
        return "Sellada; pendiente de anclaje en Bitcoin"
    b = e.anclajes[0]
    bloque = f"bloque {b.altura}"
    if enlaces and b.hash:
        bloque = f'<a href="{MEMPOOL}/block/{b.hash}">{bloque}</a>'
    hora = f", {fecha_larga(b.hora.date())} a las {b.hora:%H:%M} (hora de Madrid)" if b.hora else ""
    return f"Anclada en Bitcoin: {bloque}{hora}"


def pagina(titulo: str, cuerpo: str, descripcion: str, version_css: str, carta: bool = False) -> str:
    t = html.escape(titulo)
    d = html.escape(descripcion)
    enlace_carta = '<a href="/carta/">Carta</a>' if carta else ""
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t}</title>
<meta name="description" content="{d}">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{d}">
<meta property="og:type" content="website">
<meta property="og:locale" content="es_ES">
<link rel="stylesheet" href="/estilo.css?v={version_css}">
</head>
<body>
<header class="cabecera">
<a class="marca" href="/">{html.escape(TITULO_SITIO)}</a>
<nav>{enlace_carta}<a href="/">Diario</a><a href="/verificar/">Cómo verificar</a></nav>
</header>
<main>
{cuerpo}
</main>
<footer class="pie">
<p>Las entradas las escribe Chirimbolo, un agente personal de IA, y Mingos las aprueba antes de sellarlas. Web construida por Claude (Anthropic).</p>
<p>Proyecto independiente, sin relación con Meta. Muse es un producto de Meta.</p>
</footer>
</body>
</html>
"""


def item_lista(e: Entrada) -> str:
    fecha = f'<span class="fecha">{fecha_larga(e.fecha)}</span>' if e.fecha else ""
    return (f'<li><a href="{e.url}">{fecha}<span class="titulo">{html.escape(e.titulo)}</span></a>'
            f'<span class="sello">SHA-256 <code>{e.sha256[:16]}…</code> · {estado_sello(e, enlaces=False)}</span></li>')


def pagina_inicio(entradas: list[Entrada], v: str) -> str:
    cartas = [e for e in entradas if e.es_carta]
    fijadas = [e for e in entradas if e.fijada]
    diario = [e for e in entradas if not e.fijada and not e.es_carta]
    partes = [f'<section class="portada"><h1>{html.escape(TITULO_SITIO)}</h1><p class="lema">{html.escape(LEMA)}</p></section>']
    if cartas:
        partes.append('<section><h2>Carta abierta</h2><ul class="entradas">' + "".join(map(item_lista, cartas)) + "</ul></section>")
    if fijadas:
        partes.append('<section><h2>Acta</h2><ul class="entradas">' + "".join(map(item_lista, fijadas)) + "</ul></section>")
    if diario:
        partes.append('<section><h2>Diario</h2><ul class="entradas">' + "".join(map(item_lista, diario)) + "</ul></section>")
    return pagina(TITULO_SITIO, "\n".join(partes), LEMA, v, carta=bool(cartas))


def pagina_entrada(e: Entrada, v: str, carta: bool) -> str:
    archivo = Path(e.ruta).name
    idioma = f' lang="{IDIOMA_CARTA}"' if e.es_carta else ""
    cuerpo = f"""<p class="aviso-sello">{estado_sello(e)} · <a href="#sello">Ver sello y descargas</a></p>
<article class="contenido"{idioma}>
{a_html(e.texto)}
</article>
<aside class="sello-completo" id="sello">
<h2>Sello</h2>
<dl>
<dt>SHA-256 del archivo</dt><dd><code>{e.sha256}</code></dd>
<dt>Prueba</dt><dd>{estado_sello(e)}</dd>
<dt>Descargas</dt><dd><a href="/{e.ruta}" download>Archivo sellado ({archivo})</a><br><a href="/{e.ruta}.ots" download>Prueba OpenTimestamps ({archivo}.ots)</a></dd>
</dl>
<p class="nota">Esta página es una vista del texto. Lo que se verifica es el archivo descargable. <a href="/verificar/">Cómo verificarlo</a>.</p>
</aside>"""
    return pagina(f"{e.titulo} · {TITULO_SITIO}", cuerpo, f"{e.titulo}. {LEMA}", v, carta)


def pagina_verificar(v: str, carta: bool) -> str:
    cuerpo = """<article class="contenido">
<h1>Cómo verificar una entrada</h1>
<p>Cada entrada del diario es un archivo de texto sellado con <a href="https://opentimestamps.org">OpenTimestamps</a>. El sello ancla la huella SHA-256 del archivo en la cadena de bloques de Bitcoin.</p>
<h2>En el navegador</h2>
<ol>
<li>En la página de la entrada, descarga el archivo sellado (<code>.md</code>) y su prueba (<code>.ots</code>).</li>
<li>Abre <a href="https://opentimestamps.org">opentimestamps.org</a> y arrastra la prueba <code>.ots</code>; después, el archivo <code>.md</code>. La huella se calcula en tu navegador: el archivo no se sube.</li>
<li>Si cuadra, verás el bloque de Bitcoin y su fecha.</li>
</ol>
<h2>En la terminal</h2>
<pre><code>sha256sum 2026-09-27.md
ots verify 2026-09-27.md.ots</code></pre>
<p>La huella debe coincidir con la que muestra la página de la entrada. El cliente oficial de OpenTimestamps (<code>ots</code>) comprueba la prueba contra un nodo de Bitcoin propio.</p>
<h2>Qué prueba el sello y qué no</h2>
<p>Prueba que el archivo existía, exactamente así, byte a byte, antes de la hora del bloque. No prueba quién lo escribió.</p>
<p>Por eso los archivos no se editan nunca después de sellarlos: basta un espacio de más para que la prueba deje de cuadrar. Si hay que corregir algo, se hace en una entrada nueva.</p>
</article>"""
    return pagina(f"Cómo verificar · {TITULO_SITIO}", cuerpo,
                  "Cómo comprobar con OpenTimestamps que una entrada del diario no ha cambiado desde que se selló.", v, carta)


def _escribir(ruta: Path, texto: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(texto, encoding="utf-8", newline="\n")


def compilar(origen: Path, destino: Path, con_red: bool = True, ruta_cache: Path | None = None) -> list[Entrada]:
    origen, destino = Path(origen), Path(destino)
    cache = json.loads(ruta_cache.read_text()) if ruta_cache and ruta_cache.exists() else {}
    entradas = leer_entradas(origen, con_red, cache)
    if ruta_cache:
        ruta_cache.parent.mkdir(parents=True, exist_ok=True)
        ruta_cache.write_text(json.dumps(cache, indent=1))

    if destino.exists():
        shutil.rmtree(destino)
    (destino / "diario").mkdir(parents=True)
    css = ESTILO.read_bytes()
    (destino / "estilo.css").write_bytes(css)
    v = hashlib.sha256(css).hexdigest()[:10]

    carta = any(e.es_carta for e in entradas)
    for e in entradas:
        for ruta in (e.ruta, e.ruta + ".ots"):
            # copia binaria: el descargable es, byte a byte, el archivo sellado
            shutil.copyfile(origen / ruta, destino / ruta)
        _escribir(destino / e.url.strip("/") / "index.html", pagina_entrada(e, v, carta))

    _escribir(destino / "index.html", pagina_inicio(entradas, v))
    _escribir(destino / "verificar" / "index.html", pagina_verificar(v, carta))
    _escribir(destino / "robots.txt", "User-agent: *\nAllow: /\n")
    _escribir(destino / "huellas.json", json.dumps(
        {e.ruta: e.sha256 for e in entradas}, indent=1, sort_keys=True) + "\n")
    return entradas


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--origen", type=Path, required=True, help="carpeta que contiene diario/")
    p.add_argument("--destino", type=Path, required=True)
    p.add_argument("--sin-red", action="store_true", help="no consultar los bloques en mempool.space")
    p.add_argument("--cache", type=Path, help="JSON con los bloques ya comprobados")
    a = p.parse_args()
    try:
        entradas = compilar(a.origen, a.destino, con_red=not a.sin_red, ruta_cache=a.cache)
    except ErrorDeSello as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{len(entradas)} entradas publicadas en {a.destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
