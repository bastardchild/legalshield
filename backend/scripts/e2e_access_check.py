"""
Manual end-to-end check of the access-control layer (KNOWN_ISSUES #5).

Deliberately not part of the pytest suite: it needs a live stack, and the suite must stay
runnable with `--no-deps`. Run it against a running compose stack:

    docker compose exec -T api python scripts/e2e_access_check.py

Exits non-zero if any check fails, so it can be dropped into CI once a stack is available
there. Uses only the standard library so it needs nothing beyond the runtime image.
"""
import json
import sys
import urllib.error
import urllib.request

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


def upload(cookie=None):
    boundary = "----legalshieldcheck"
    payload = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="cek.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        f"{CONTRACT_TEXT}\r\n"
        f"--{boundary}--\r\n"
    ).encode()
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
