"""Private Hermes' urllib HTTPS must verify with its bundled roots on clean Windows."""
import datetime
import http.server
import json
import os
import ssl
import subprocess
import sys
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from core.hermes_runtime import hermes_subprocess_env
from core.runtime_descriptor import DESCRIPTOR_ENV, get_runtime
from tests.runtime_descriptor_fixtures import write_runtime_fixture


@pytest.fixture
def https_installation(tmp_path, monkeypatch):
    descriptor, _ = write_runtime_fixture(tmp_path / 'Iris con espacios')
    monkeypatch.setenv(DESCRIPTOR_ENV, str(descriptor))
    monkeypatch.setenv('IRIS_HOME', str(tmp_path / 'profile'))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'profile/hermes'))
    private_bundle = get_runtime().hermes.python.parent / 'Lib/site-packages/certifi/cacert.pem'
    private_bundle.parent.mkdir(parents=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Iris local test CA')])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
            .sign(key, hashes.SHA256()))
    private_bundle.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file = tmp_path / 'key.pem'
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"account_read":true}')
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(private_bundle, key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield private_bundle, server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def child_request(env, url):
    code = ("import json,ssl,urllib.request; c=ssl.create_default_context(); "
            "print(json.dumps({'verify':c.verify_mode==ssl.CERT_REQUIRED,"
            "'hostname':c.check_hostname})); "
            f"print(urllib.request.urlopen({url!r},timeout=5).read().decode())")
    return subprocess.run([sys.executable, '-E', '-s', '-B', '-c', code], env=env,
                          capture_output=True, text=True, timeout=20)


def test_packaged_urllib_uses_owned_bundle_without_changing_parent(
    https_installation, monkeypatch, tmp_path,
):
    bundle, port = https_installation
    inherited = tmp_path / 'unrelated.pem'
    inherited.write_text('not a trusted CA')
    monkeypatch.setenv('SSL_CERT_FILE', str(inherited))
    before = dict(os.environ)
    env = hermes_subprocess_env()
    result = child_request(env, f'https://localhost:{port}/api/oauth/account')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[0]) == {'verify': True, 'hostname': True}
    assert '"account_read":true' in result.stdout
    assert env['SSL_CERT_FILE'] == str(bundle)
    assert dict(os.environ) == before


def test_packaged_bundle_does_not_disable_hostname_validation(https_installation):
    _, port = https_installation
    result = child_request(hermes_subprocess_env(), f'https://127.0.0.1:{port}/')
    assert result.returncode != 0
    assert 'CERTIFICATE_VERIFY_FAILED' in result.stderr
    assert 'ip address mismatch' in result.stderr.lower()
    assert json.loads(result.stdout.splitlines()[0]) == {'verify': True, 'hostname': True}


def test_packaged_unknown_certificate_is_still_rejected(https_installation):
    bundle, port = https_installation
    bundle.write_text('not a trusted CA')
    result = child_request(hermes_subprocess_env(), f'https://localhost:{port}/')
    assert result.returncode != 0
    assert 'CERTIFICATE_VERIFY_FAILED' in result.stderr


def test_development_keeps_its_configured_trust(monkeypatch, tmp_path):
    monkeypatch.delenv(DESCRIPTOR_ENV, raising=False)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'hermes'))
    monkeypatch.setenv('IRIS_HOME', str(tmp_path / 'iris'))
    monkeypatch.setenv('SSL_CERT_FILE', 'development-ca.pem')
    assert hermes_subprocess_env()['SSL_CERT_FILE'] == 'development-ca.pem'
