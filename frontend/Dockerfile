# =============================================================================
# Frontend Dockerfile — Next.js 16 + Tailwind 4
# =============================================================================
# Jednostavan single-stage image koji pokreće `next dev` u kontejneru.
# Bez produkcijskog multi-stage builda — za diplomski demo dovoljan je
# dev server, hot-reload uključen.

FROM node:20-alpine

WORKDIR /app

# Najprije package manifeste, da iskoristimo Docker layer cache —
# `npm install` se rebuilda samo kad se ovisnosti promijene.
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

# Kopija ostatka projekta (source kod). Volume mount u docker-compose-u
# omogućuje hot-reload za promjene u src/.
COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]
