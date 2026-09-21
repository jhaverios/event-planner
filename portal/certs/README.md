# Extra CA certificates

Normally empty. On a machine whose outbound HTTPS goes through a
TLS-inspecting proxy, pip fails the image build with

    SSLError ... self-signed certificate in certificate chain

Drop the proxy's CA bundle in here as a `.crt` file and the build trusts it.
The runtime picks it up too, which matters because the portal calls WATI and
ZeptoMail over HTTPS from inside the container.

Never disable certificate verification instead. Trusting a named CA is a
decision someone made; `--trusted-host` is trusting whatever answers.

`.crt` and `.pem` files here are gitignored.
