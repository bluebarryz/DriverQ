# Production Apache2 Setup

## Architecture

```
Internet → Apache2 (443 HTTPS)
             ├─ /DriverQ/*   → proxy → 127.0.0.1:8000  (FastAPI: API + camera images)
             └─ /*           → proxy → 127.0.0.1:3000  (Next.js frontend)
```

`NEXT_PUBLIC_API_BASE_URL=https://wiselab.uwaterloo.ca/DriverQ` is baked into the JS
bundle at build time, so the browser sends API calls to `/DriverQ/api/…` which
Apache strips the prefix from before forwarding to the FastAPI container.

### Virtual Host (`/etc/apache2/sites-available/driverq.conf`)

```apache
<VirtualHost *:80>
    ServerName wiselab.uwaterloo.ca
    RewriteEngine On
    RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [L,R=301]
</VirtualHost>

<VirtualHost *:443>
    ServerName wiselab.uwaterloo.ca

    SSLEngine on
    SSLCertificateFile    /etc/.../wiselab.uwaterloo.ca/fullchain.pem
    SSLCertificateKeyFile /etc/.../wiselab.uwaterloo.ca/privkey.pem

    # Security headers
    Header always set Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"
    Header always set X-Frame-Options "SAMEORIGIN"
    Header always set X-Content-Type-Options "nosniff"
    Header always set Referrer-Policy "strict-origin-when-cross-origin"
    Header always set Permissions-Policy "geolocation=(), microphone=(), camera=()"

    ProxyPreserveHost On

    # API + camera images (strip /DriverQ prefix before forwarding)
    ProxyPass        /DriverQ/ http://127.0.0.1:8000/
    ProxyPassReverse /DriverQ/ http://127.0.0.1:8000/

    # Next.js frontend
    ProxyPass        / http://127.0.0.1:3000/
    ProxyPassReverse / http://127.0.0.1:3000/
</VirtualHost>
```

Enable and reload:

```bash
sudo a2ensite driverq.conf
sudo apache2ctl configtest
sudo systemctl reload apache2
```

### Firewall

Block direct access to Docker ports from the internet — only Apache should reach them:

```bash
ufw allow 80/tcp
ufw allow 443/tcp
ufw deny 8000/tcp
ufw deny 3000/tcp
ufw enable
```