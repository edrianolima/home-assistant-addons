ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache \
    python3 \
    py3-pip \
    curl \
    i2c-tools \
    jpeg \
    zlib \
    freetype \
    lcms2 \
    openjpeg \
    tiff && \
    apk add --no-cache --virtual .build-deps \
    build-base \
    linux-headers \
    python3-dev \
    jpeg-dev \
    zlib-dev \
    freetype-dev \
    lcms2-dev \
    openjpeg-dev \
    tiff-dev && \
    python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip download --no-binary=:all: rpi_ws281x==5.0.0 -d /tmp && \
    tar -xzf /tmp/rpi_ws281x-5.0.0.tar.gz -C /tmp && \
    curl -fsSL -o /tmp/rpihw.c https://raw.githubusercontent.com/jgarff/rpi_ws281x/master/rpihw.c && \
    cp /tmp/rpihw.c /tmp/rpi_ws281x-5.0.0/lib/rpihw.c && \
    CFLAGS='-U__aarch64__' /opt/venv/bin/pip install --no-cache-dir /tmp/rpi_ws281x-5.0.0 && \
    /opt/venv/bin/pip install --no-cache-dir \
    paho-mqtt \
    psutil \
    luma.oled \
    smbus2 \
    python-periphery && \
    apk del .build-deps

ENV PATH="/opt/venv/bin:${PATH}"

# Criar diretório do serviço gerenciado pelo s6-overlay
RUN mkdir -p /etc/services.d/ws281x

# Copiar o script Python e o arquivo de serviço para o container
COPY ws281x_control.py /app/ws281x_control.py
COPY run /etc/services.d/ws281x/run

# Garantir permissões de execução
RUN chmod +x /app/ws281x_control.py /etc/services.d/ws281x/run

# Configurar o ponto de entrada padrão para o s6-overlay
ENTRYPOINT [ "/init" ]
