FROM python:3.12-alpine

RUN apk add --no-cache nginx git

WORKDIR /app
COPY sitio/requirements.txt sitio/
RUN pip install --no-cache-dir -r sitio/requirements.txt

COPY sitio/ sitio/
COPY nginx.conf /etc/nginx/nginx.conf
COPY cabeceras.conf /etc/nginx/cabeceras.conf
COPY entrypoint.sh /entrypoint.sh

# Diario incluido en la imagen: la web arranca con él aunque GitHub no responda.
COPY diario/ /srv/repo-inicial/diario/
RUN python sitio/build.py --origen /srv/repo-inicial --destino /srv/sitios/inicial --sin-red \
 && ln -s inicial /srv/sitios/actual \
 && chmod +x /entrypoint.sh

ENV REPO_URL=https://github.com/Xhaomin/muse-escribe-a-muse-web.git \
    RAMA=main \
    INTERVALO=60

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["/entrypoint.sh"]
