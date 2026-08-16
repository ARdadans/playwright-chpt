# Dokumentasi Endpoint Session Injection

Aplikasi Hermes ChatGPT Web sekarang mendukung injeksi sesi secara dinamis melalui REST API tanpa perlu me-restart server secara manual. Hal ini berguna jika sesi (cookie) ChatGPT sudah kedaluwarsa dan Anda ingin mengirimkan sesi baru langsung dari client.

## Endpoint: `POST /cookies/inject-session`

Endpoint ini menerima data JSON yang berisi konfigurasi sesi/cookies dari ChatGPT. Setelah payload diterima, aplikasi akan:
1. Menyimpan data tersebut ke dalam file lokal `cookies/cookie.json`.
2. Menjalankan skrip `session_inject.py` di latar belakang (background) untuk memasukkan cookie ke profil peramban (browser) dan menghasilkan `storage_state.json` baru.
3. Mengembalikan respons setelah injeksi ke browser selesai.

### Header Request
- `Content-Type: application/json`

### Body Request
Body request harus berupa struktur JSON dengan properti `token` (opsional namun direkomendasikan jika ada) dan `cookies`.

**Format JSON:**
```json
{
  "token": "<akses_token_chatgpt>",
  "cookies": "cookie_name_1=value1; cookie_name_2=value2;"
}
```

### Respons (Success 200 OK)
Jika proses injeksi berhasil, server akan merespons:
```json
{
  "ok": true,
  "message": "Session injected successfully",
  "output": "<log_output_dari_session_inject>"
}
```

### Respons (Error 400/500)
Jika JSON tidak valid (400) atau proses injeksi melalui skrip gagal (500):
```json
{
  "error": {
    "message": "Session injection failed",
    "details": "<log_error_detail>"
  }
}
```

---

## Contoh Penggunaan menggunakan cURL

Berikut adalah contoh perintah `curl` yang dapat Anda jalankan dari terminal/command prompt client untuk mengirim session baru ke server:

### 1. Jika Menyimpan Payload ke File Terlebih Dahulu (Direkomendasikan)
Buat file bernama `new_cookie.json`:
```json
{
  "token": "eyJhbGciOiJSU...",
  "cookies": "__Secure-next-auth.session-token=eyJhbGci...; _puid=user-123;"
}
```

Lalu kirimkan file tersebut menggunakan curl (ganti `http://localhost:8000` dengan URL adapter Hermes Anda):
```bash
curl -X POST http://localhost:8000/cookies/inject-session \
  -H "Content-Type: application/json" \
  -d @new_cookie.json
```

### 2. Payload Langsung di Command Line (Inline)
```bash
curl -X POST http://localhost:8000/cookies/inject-session \
  -H "Content-Type: application/json" \
  -d '{
        "token": "eyJhbGciOiJSU...",
        "cookies": "__Secure-next-auth.session-token=eyJhbGci...; _puid=user-123;"
      }'
```

---

## Catatan
- Endpoint ini memakan waktu beberapa saat (sekitar 10-20 detik) untuk mengembalikan respons karena skrip di latar belakang harus meluncurkan browser (Playwright) dan melakukan verifikasi ke `chatgpt.com` untuk memperbarui status dan token di penyimpanan internal server.
- Pastikan string cookie dipisahkan menggunakan titik koma (`;`).
