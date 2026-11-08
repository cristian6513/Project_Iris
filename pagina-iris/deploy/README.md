# Publicar la página en un VPS

Guía para servir la página con **nginx** y HTTPS. Los comandos de Node se ejecutan
desde la raíz del repositorio y los comandos con sudo se ejecutan en el VPS.
Antes de configurar nginx, comprueba que tienes disponibles los puertos 80 y 443.

## 1. Dominio y DNS (lo haces tú)

1. Abre la zona DNS de tu dominio. Conserva los registros de correo que ya utilices.
2. En la zona DNS crea:

   | Tipo | Nombre | Valor |
   | --- | --- | --- |
   | `A` | `@` | `IP_DEL_VPS` |
   | `A` | `www` | `IP_DEL_VPS` |
   | `AAAA` (opcional) | `@` y `www` | `IPV6_DEL_VPS` |

   El IPv6 es opcional: nginx ya escucha en IPv4 e IPv6.
3. Comprueba que propagó: `nslookup iris-os.cloud` debe devolver la IP del VPS.

## 2. Acceso SSH con clave (lo haces tú)

Para desplegar se recomienda un usuario propio en vez de `root`:

```bash
adduser deploy
mkdir -p /home/deploy/.ssh && cp ~/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh && chmod 700 /home/deploy/.ssh
```

Si aún no tienes clave en tu PC:

```powershell
ssh-keygen -t ed25519
type $env:USERPROFILE\.ssh\id_ed25519.pub
```

Añade la clave pública al VPS y comprueba la conexión por SSH. Luego crea
`pagina-iris/deploy.local.json` con el host y el usuario que configuraste.
Este archivo queda en tu equipo.

```json
{
  "host": "vps.example.com",
  "user": "usuario-ssh"
}
```

## 3. Preparar el servidor (una vez)

```bash
sudo apt update && sudo apt install -y nginx certbot
sudo mkdir -p /var/www/iris-os/releases /var/www/letsencrypt
sudo chown -R deploy:deploy /var/www/iris-os
```

Comprueba que el firewall permita HTTP y HTTPS en los puertos 80 y 443.

## 4. Certificado HTTPS (una vez por dominio)

```bash
sudo certbot certonly --standalone \
  -d iris-os.cloud -d www.iris-os.cloud \
  --pre-hook "systemctl stop nginx" --post-hook "systemctl start nginx"
```

Certbot guarda los hooks y renueva solo (nginx se detiene unos segundos cada ~60 días).
Comprueba la renovación con:

```bash
sudo certbot renew --dry-run
```

## 5. Primer despliegue

En tu PC:

```powershell
node pagina-iris/scripts/build.mjs
node pagina-iris/scripts/deploy.mjs --dry-run
node pagina-iris/scripts/deploy.mjs
node pagina-iris/scripts/deploy.mjs --nginx
```

`--dry-run` solo muestra lo que haría. `--nginx` sube la configuración generada y te
muestra estos comandos, que ejecutas en el servidor:

```bash
sudo cp ~/iris-os-nginx/iris-os-security-headers.conf /etc/nginx/snippets/
sudo cp ~/iris-os-nginx/iris-os.conf /etc/nginx/sites-available/iris-os.conf
sudo ln -sf /etc/nginx/sites-available/iris-os.conf /etc/nginx/sites-enabled/iris-os.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

## 6. Cada actualización

```powershell
node pagina-iris/scripts/build.mjs
node pagina-iris/scripts/deploy.mjs
```

Cada despliegue crea una carpeta nueva en `releases/` y cambia `current` de golpe, así
que nadie ve una web a medio subir. Se guardan las últimas 5 versiones.

Si algo sale mal:

```powershell
node pagina-iris/scripts/deploy.mjs --rollback
```

Solo hace falta repetir `--nginx` si cambias la plantilla de nginx, las cabeceras o el
dominio.

## 7. Comprobar que todo está bien

- `curl -I https://iris-os.cloud/` → `200`, con `Content-Security-Policy` y `Strict-Transport-Security`.
- `curl -I https://www.iris-os.cloud/` → `301` hacia `https://iris-os.cloud/`.
- [securityheaders.com](https://securityheaders.com) → debería dar A+.
- [SSL Labs](https://www.ssllabs.com/ssltest/) → A o A+.
- Vista previa al compartir: [opengraph.xyz](https://www.opengraph.xyz) o el depurador de Facebook.
- [PageSpeed Insights](https://pagespeed.web.dev) → rendimiento y accesibilidad.
- [Google Search Console](https://search.google.com/search-console): añade el dominio
  (verificación por registro DNS TXT) y envía `https://iris-os.cloud/sitemap.xml`.

## 8. Notas

- **HSTS** se aplica durante un año al dominio configurado. Activa `includeSubDomains`
  solo si todos tus subdominios se sirven por HTTPS.
- **Cambiar de dominio:** cambia `siteUrl`, saca el certificado del dominio nuevo, haz
  `build`, `deploy` y `deploy --nginx`, y deja el dominio antiguo con un `return 301`
  hacia el nuevo durante al menos un año.
- **Entorno de pruebas:** con `"indexable": false` el build bloquea a los buscadores
  (`robots.txt` y `noindex`).
- **security.txt:** si rellenas `securityContact` (por ejemplo, un correo de
  `@iris-os.cloud`), el build publica `/.well-known/security.txt`. Será público.
- **Brotli:** el build ya genera los `.br`. Si tu Ubuntu tiene el paquete
  `libnginx-mod-http-brotli-static`, instálalo y descomenta `brotli_static on;`.
  Sin él, nginx sirve los `.gz`.
- **El navegador descarga la página («descarga») en vez de abrirla:** ese navegador guardó
  una respuesta con tipo equivocado (pasó el 18-10-2026, mientras un bloque `types` dejó
  todo como `application/octet-stream`). Con la plantilla actual el HTML se envía entero
  en cada visita, sin 304, así que se corrige solo al instalarla con `--nginx`. Con una
  configuración anterior, borra la caché del navegador o vuelve a hacer `build` y
  `deploy`: un archivo con fecha nueva cambia su ETag.
- **HTTP/2:** con nginx 1.25.1 o superior, `listen … http2` muestra un aviso de
  obsoleto, pero funciona. Puedes cambiarlo por `http2 on;`.
- **Login de Comunidad:** la CSP ya permite peticiones al propio dominio
  (`connect-src 'self'`, `form-action 'self'`). El backend irá en `/api/` con el bloque
  `proxy_pass` que está comentado en la plantilla.
