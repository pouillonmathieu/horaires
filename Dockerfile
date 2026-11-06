FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HORAIRES_DATA_DIR=/donnees
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
# Utilisateur sans privilèges, système de fichiers applicatif en lecture seule
RUN useradd --system --uid 10001 horaires && mkdir /donnees && chown horaires /donnees
USER horaires
VOLUME /donnees
EXPOSE 8000
# --proxy-headers : l'adresse IP réelle est fournie par le reverse proxy HTTPS (journal + anti-force brute)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
