"""
Manual end-to-end check of the access-control layer (KNOWN_ISSUES #5).

Deliberately not part of the pytest suite: it needs a live stack, and the suite must stay
runnable with `--no-deps`. Run it against a running compose stack:

    docker compose exec -T api python scripts/e2e_access_check.py

Exits non-zero if any check fails, so it can be dropped into CI once a stack is available
there. Uses only the standard library plus pypdf, both present in the api image.
"""
import io
import json
import sys
import urllib.error
import urllib.request

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

BASE = "http://localhost:8000"
CONTRACT_TEXT = (
    "PERJANJIAN KERJA SAMA\n"
    "Perjanjian ini dibuat pada tanggal 1 Agustus 2026, antara:\n\n"
    "PIHAK PERTAMA (Klien): PT Maju Bersama Teknologi, beralamat di Jl. Sudirman No. 100, "
    "Jakarta Selatan (selanjutnya disebut \"Klien\").\n"
    "PIHAK KEDUA (Freelancer): Budi Santoso, beralamat di Jl. Kebon Jeruk No. 5, "
    "Jakarta Barat (selanjutnya disebut \"Freelancer\").\n\n"
    "PASAL 1 - LINGKUP PEKERJAAN\n"
    "Freelancer setuju untuk menyediakan jasa pengembangan perangkat lunak secara penuh "
    "waktu untuk Klien, termasuk desain, pengembangan, testing, dan deployment seluruh "
    "produk digital Klien selama periode kontrak.\n\n"
    "PASAL 2 - KOMPENSASI\n"
    "Klien setuju membayar Freelancer sebesar Rp 5.000.000 per bulan. Pembayaran dilakukan "
    "paling lambat 60 hari setelah invoice diterima.\n\n"
    "PASAL 3 - KERAHASIAAN\n"
    "Freelancer wajib menjaga kerahasiaan seluruh informasi yang diperoleh dari Klien, "
    "termasuk setelah kontrak berakhir. Pelanggaran ketentuan ini mengakibatkan denda.\n\n"
    "PASAL 4 - HUKUM YANG BERLAKU\n"
    "Perjanjian ini diatur oleh hukum Republik Indonesia. Segala sengketa diselesaikan "
    "melalui arbitrase di Singapura dengan bahasa Inggris.\n\n"
    "PASAL 5 - PERUBAHAN KONTRAK\n"
    "Klien berhak mengubah syarat dan ketentuan kontrak ini dengan pemberitahuan tertulis "
    "24 jam sebelumnya.\n\n"
    "Demikian perjanjian ini dibuat dengan itikad baik dan ditandatangani oleh para pihak,\n\n"
    "PT Maju Bersama Teknologi                    Budi Santoso\n"
    "Direktur Utama                                Freelancer\n"
)


def request(path, method="GET", cookie=None, headers=None):
    req = urllib.request.Request(BASE + path, method=method)
    if cookie:
        req.add_header("Cookie", cookie)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers


def build_contract_pdf(text: str) -> bytes:
    """
    A one-page PDF whose only content stream is the contract text.

    pypdf can write blank pages but has no high-level "add text" API, so the content
    stream is built by hand with a Type1 font. Parentheses and backslashes are escaped
    for the PDF string syntax.
    """

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    lines = [f"({esc(line)}) Tj T*" for line in text.splitlines()]
    content = "\n".join(["BT /F1 9 Tf 72 720 Td 12 TL", *lines, "ET"])

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(content.encode())
    page[NameObject("/Contents")] = stream
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                        }
                    )
                }
            )
        }
    )
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def upload(cookie=None):
    boundary = "----legalshieldcheck"
    pdf_bytes = build_contract_pdf(CONTRACT_TEXT)
    payload = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="cek.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + pdf_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + "/api/contracts/upload", method="POST", data=payload)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read()), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers


def owner_cookie(headers):
    for value in headers.get_all("Set-Cookie") or []:
        if value.startswith("ls_owner="):
            return value.split(";")[0]
    return None


def main() -> int:
    failures = []

    def check(label, condition, detail=""):
        print(f"{'PASS' if condition else 'FAIL'}  {label}  {detail}".rstrip())
        if not condition:
            failures.append(label)

    status, _, headers = request("/")
    cookie_a = owner_cookie(headers)
    check("index issues an owner cookie", status == 200 and cookie_a is not None, str(status))

    status, body, _ = upload(cookie_a)
    check("upload accepted", status == 200, f"{status} {body if status != 200 else ''}")
    if status != 200:
        return 1
    contract_id = body["id"]
    print(f"      contract_id={contract_id}")

    status, _, _ = request(f"/api/contracts/{contract_id}/status", cookie=cookie_a)
    check("owner can read status", status == 200, str(status))

    _, _, headers_b = request("/")
    cookie_b = owner_cookie(headers_b)
    check("second visitor gets a different cookie", cookie_b != cookie_a)

    for label, path in [
        ("stranger refused on status", f"/api/contracts/{contract_id}/status"),
        ("stranger refused on result page", f"/contracts/{contract_id}"),
        ("stranger refused on result partial", f"/partials/{contract_id}/result"),
        ("stranger refused on status partial", f"/partials/{contract_id}/status"),
    ]:
        status, _, _ = request(path, cookie=cookie_b)
        check(label, status == 404, str(status))

    status, _, _ = request(f"/api/contracts/{contract_id}/status")
    check("no cookie is refused", status == 404, str(status))

    status, _, _ = request(f"/api/contracts/{contract_id}/status", cookie="ls_owner=forged.sig")
    check("forged cookie is refused", status == 404, str(status))

    status, _, _ = request("/api/contracts/not-a-uuid/status", cookie=cookie_a)
    check("malformed uuid yields 404 not 500", status == 404, str(status))

    status, _, _ = request("/health")
    check("health stays public", status == 200, str(status))

    print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILURES: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
