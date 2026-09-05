# Pinned by digest; bump deliberately (Dependabot opens PRs for this).
FROM node:26-bookworm-slim@sha256:367679cf9792759492a486e4aa4b421764d71a9546a6dae8aab81a99eb797b3e

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
