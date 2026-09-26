Drop a PEM CA bundle here (any `*.crt`) if this machine sits behind a TLS-inspecting
proxy, so `npm install` in the Dockerfile can verify the registry.

On a normal server this directory stays empty and the build ignores it.

In the Claude Code sandbox:

    cp /root/.ccr/ca-bundle.crt deploy/n8n/certs/

Bundles are gitignored: they are machine-specific, not project content.
