import argparse
import base64
import hashlib
import http.client
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

CHUNK_SIZE = 8 * 1024 * 1024


def sha256_base64(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
    return base64.b64encode(digest.digest()).decode("ascii")


def function_region(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    match = re.search(r"\.lambda-url\.([a-z0-9-]+)\.on\.aws$", parsed.hostname or "")
    if parsed.scheme != "https" or not match:
        raise ValueError("Endpoint invalido; informe a Function URL HTTPS da Lambda.")
    return match.group(1)


def request_upload_url(endpoint: str, region: str, profile: str | None, file_name: str, checksum: str) -> dict:
    session = boto3.Session(profile_name=profile, region_name=region)
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("Nenhuma credencial AWS encontrada para assinar a chamada da Lambda.")

    body = json.dumps({"filename": file_name, "checksum_sha256": checksum}).encode("utf-8")
    signed_request = AWSRequest(
        method="POST",
        url=endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(credentials.get_frozen_credentials(), "lambda", region).add_auth(signed_request)
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers=dict(signed_request.headers.items()),
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"Lambda respondeu HTTP {error.code}: {details}") from error

    if not isinstance(result, dict) or not result.get("upload_url") or not result.get("object_key"):
        raise RuntimeError("Resposta da Lambda sem URL de upload ou chave S3.")
    return result


def upload_file(upload_url: str, file_path: Path, checksum: str) -> None:
    parsed = urlsplit(upload_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("A Lambda retornou uma URL de upload invalida.")

    request_target = parsed.path or "/"
    if parsed.query:
        request_target += f"?{parsed.query}"

    connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=300)
    try:
        connection.putrequest("PUT", request_target, skip_accept_encoding=True)
        connection.putheader("Content-Length", str(file_path.stat().st_size))
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("x-amz-checksum-sha256", checksum)
        connection.endheaders()

        with file_path.open("rb") as source:
            while chunk := source.read(CHUNK_SIZE):
                connection.send(chunk)

        response = connection.getresponse()
        response_body = response.read(2048).decode("utf-8", errors="replace")
        if response.status != 200:
            raise RuntimeError(f"S3 respondeu HTTP {response.status}: {response_body}")

        stored_checksum = response.getheader("x-amz-checksum-sha256")
        if stored_checksum and stored_checksum != checksum:
            raise RuntimeError("O checksum retornado pelo S3 nao corresponde ao arquivo local.")
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Envia um arquivo diretamente ao bucket Bronze usando URL pre-assinada.")
    parser.add_argument("--endpoint", required=True, help="Function URL autenticada da Lambda")
    parser.add_argument("--file", required=True, type=Path, help="Arquivo a enviar")
    parser.add_argument("--profile", help="Perfil AWS CLI; se omitido, usa a cadeia padrao de credenciais")
    args = parser.parse_args()

    if not args.file.is_file():
        parser.error(f"Arquivo nao encontrado: {args.file}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", args.file.name):
        parser.error("O nome do arquivo deve conter apenas letras, numeros, ponto, hifen ou sublinhado.")

    try:
        region = function_region(args.endpoint)
        checksum = sha256_base64(args.file)
        print(f"Solicitando URL de upload para {args.file.name} ({args.file.stat().st_size:,} bytes)...", flush=True)
        upload = request_upload_url(args.endpoint, region, args.profile, args.file.name, checksum)
        upload_file(upload["upload_url"], args.file, checksum)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        print(f"Falha no upload: {error}", file=sys.stderr)
        return 1

    print(f"Objeto enviado. Chave S3: {upload['object_key']}")
    print(f"Tamanho: {args.file.stat().st_size:,} bytes")
    print(f"SHA-256 (Base64): {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
