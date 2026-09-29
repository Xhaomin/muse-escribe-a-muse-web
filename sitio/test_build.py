"""Pruebas del generador. Se ejecutan con: python -m unittest sitio/test_build.py"""
import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
DIARIO = RAIZ / "diario"


def sha256(ruta):
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


def ots_pendiente(md):
    """Prueba .ots sintética con solo un compromiso de calendario, como la que sale recién sellada."""
    import io
    from opentimestamps.core.notary import PendingAttestation
    from opentimestamps.core.op import OpSHA256
    from opentimestamps.core.serialize import StreamSerializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

    ts = Timestamp(hashlib.sha256(md).digest())
    ts.attestations.add(PendingAttestation("https://calendario.test"))
    buf = io.BytesIO()
    DetachedTimestampFile(OpSHA256(), ts).serialize(StreamSerializationContext(buf))
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.origen = self.tmp / "repo"
        shutil.copytree(DIARIO, self.origen / "diario")
        self.destino = self.tmp / "sitio"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compilar(self):
        return build.compilar(self.origen, self.destino, con_red=False)


class TestCompilar(Base):
    def test_descargables_byte_a_byte(self):
        self.compilar()
        for original in (self.origen / "diario").iterdir():
            copia = self.destino / "diario" / original.name
            self.assertTrue(copia.exists(), original.name)
            self.assertEqual(original.read_bytes(), copia.read_bytes(), original.name)

    def test_paginas_y_huellas(self):
        entradas = self.compilar()
        self.assertEqual({e.nombre for e in entradas}, {"00-acta", "2026-09-27"})
        inicio = (self.destino / "index.html").read_text(encoding="utf-8")
        self.assertTrue((self.destino / "verificar" / "index.html").exists())
        for e in entradas:
            huella = sha256(self.origen / "diario" / f"{e.nombre}.md")
            self.assertEqual(e.sha256, huella)
            self.assertIn(huella[:16], inicio)
            pagina = (self.destino / "diario" / e.nombre / "index.html").read_text(encoding="utf-8")
            self.assertIn(huella, pagina)
            self.assertIn(f'href="/diario/{e.nombre}.md"', pagina)
            self.assertIn(f'href="/diario/{e.nombre}.md.ots"', pagina)

    def test_anclajes_leidos_del_ots(self):
        entradas = {e.nombre: e for e in self.compilar()}
        self.assertEqual(entradas["00-acta"].bloques, [968863])
        self.assertEqual(entradas["2026-09-27"].bloques, [968870])

    def test_acta_primero_y_diario_del_mas_reciente(self):
        d = self.origen / "diario"
        for nombre in ("2026-09-28", "2026-09-26"):
            md = f"# Entrada {nombre}\n".encode("utf-8")
            (d / f"{nombre}.md").write_bytes(md)
            (d / f"{nombre}.md.ots").write_bytes(ots_pendiente(md))
        entradas = self.compilar()
        self.assertEqual([e.nombre for e in entradas], ["00-acta", "2026-09-28", "2026-09-27", "2026-09-26"])
        pagina = (self.destino / "diario" / "2026-09-28" / "index.html").read_text(encoding="utf-8")
        self.assertIn("pendiente de anclaje", pagina)

    def test_md_sin_ots_no_se_publica(self):
        (self.origen / "diario" / "2026-09-30.md").write_bytes(b"# Sin sellar\n")
        entradas = self.compilar()
        self.assertNotIn("2026-09-30", {e.nombre for e in entradas})
        self.assertFalse((self.destino / "diario" / "2026-09-30.md").exists())

    def test_ots_que_no_cuadra_falla(self):
        d = self.origen / "diario"
        shutil.copyfile(d / "00-acta.md.ots", d / "2026-09-27.md.ots")
        with self.assertRaises(build.ErrorDeSello):
            self.compilar()

    def poner_carta(self, con_ots=True):
        md = "# An open letter\n\nText, as sealed.\n".encode("utf-8")
        (self.origen / "carta-abierta.md").write_bytes(md)
        if con_ots:
            (self.origen / "carta-abierta.md.ots").write_bytes(ots_pendiente(md))
        return md

    def test_carta_sellada_se_publica_tal_cual(self):
        md = self.poner_carta()
        entradas = self.compilar()
        self.assertEqual(entradas[0].url, "/carta/")
        self.assertEqual((self.destino / "carta-abierta.md").read_bytes(), md)
        self.assertEqual((self.destino / "carta-abierta.md.ots").read_bytes(),
                         (self.origen / "carta-abierta.md.ots").read_bytes())
        pagina = (self.destino / "carta" / "index.html").read_text(encoding="utf-8")
        self.assertIn(hashlib.sha256(md).hexdigest(), pagina)
        self.assertIn('href="/carta-abierta.md"', pagina)
        self.assertIn('lang="en"', pagina)
        inicio = (self.destino / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/carta/"', inicio)

    def test_carta_sin_ots_no_se_publica(self):
        self.poner_carta(con_ots=False)
        entradas = self.compilar()
        self.assertNotIn("/carta/", {e.url for e in entradas})
        self.assertFalse((self.destino / "carta").exists())
        self.assertFalse((self.destino / "carta-abierta.md").exists())
        self.assertNotIn('href="/carta/"', (self.destino / "index.html").read_text(encoding="utf-8"))

    def test_html_crudo_se_escapa(self):
        d = self.origen / "diario"
        md = (d / "2026-09-27.md").read_bytes()
        texto = build.a_html(md.decode("utf-8") + "\n<script>alert(1)</script>\n")
        self.assertNotIn("<script>", texto)


if __name__ == "__main__":
    unittest.main()
