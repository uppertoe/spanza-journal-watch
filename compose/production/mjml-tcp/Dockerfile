# Pinned by digest; bump deliberately (Dependabot opens PRs for this).
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5

ARG EXPOSE=28101

ENV WORKDIR=/app
ENV SCRIPTSDIR=/scripts
ENV PATH="$SCRIPTSDIR:${PATH}"
ENV HOST="0.0.0.0"
ENV PORT=$EXPOSE

COPY ./compose/production/mjml-tcp/entrypoint.sh $SCRIPTSDIR/
RUN chmod +x $SCRIPTSDIR/*.sh

WORKDIR $WORKDIR

# mjml and its whole dependency tree come from the committed lockfile, so two
# builds of the same commit produce the same renderer.
COPY ./compose/production/mjml-tcp/package.json ./compose/production/mjml-tcp/package-lock.json $WORKDIR/
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force

COPY ./compose/production/mjml-tcp/tcpserver.js $WORKDIR/

EXPOSE $EXPOSE

ENTRYPOINT ["entrypoint.sh"]

CMD ["--mjml.minify=true", "--mjml.validationLevel=strict"]
