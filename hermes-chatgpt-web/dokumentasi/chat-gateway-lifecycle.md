# Dokumentasi Siklus Chat & Browser Gateway (Hermes ChatGPT Web)

Dokumen ini menjelaskan alur kerja internal aplikasi Hermes ChatGPT Web dalam mengelola sesi browser, mengirimkan pesan (*prompt*), mengambil respons (*polling & streaming*), mendeteksi penyelesaian respons, penanganan error, serta mekanisme pembuatan percakapan baru (*new chat*).

---

## 1. Struktur Komponen & Peran

| Komponen | Lokasi File | Peran Utama |
| :--- | :--- | :--- |
| **Browser Core** | `src/hermes_chatgpt_web/core/browser.py` | Menginisialisasi Chromium async Playwright, mengelola isolasi `BrowserContext` dan `Page` per akun, injeksi *cookies/localStorage*, dan *stealth evasion*. |
| **Worker Pool** | `src/hermes_chatgpt_web/chatgpt/worker_pool.py` | Mengelola lifecycle akun worker ChatGPT, antrean lock per-akun (`worker.lock`), deteksi limit kuota, dan round-robin dispatch. |
| **Chat Engine** | `src/hermes_chatgpt_web/chatgpt/chat.py` | Mengontrol interaksi langsung dengan UI ChatGPT web (input teks, klik tombol, pembacaan DOM, polling streaming delta). |
| **In-Process Gateway**| `src/hermes_chatgpt_web/api/gateway_client.py` | Gateway async in-process untuk memanggil engine chat tanpa latency network/sub-process. |
| **API Adapter** | `src/hermes_chatgpt_web/api/routes.py` | Menyediakan endpoint `/v1/chat/completions`, `/settings`, `/cookies`, `/database` yang terpadu dalam satu aplikasi FastAPI. |

---

## 2. Alur Pengiriman Prompt (*Submission Flow*)

Sebelum prompt dikirim ke ChatGPT web, langkah-langkah berikut dijalankan di `chat.py`:

1. **Hitung Turn Awal (`turns0`)**:
   Menghitung jumlah respons asisten yang sudah ada di halaman untuk mendeteksi kapan giliran pesan baru muncul:
   ```javascript
   document.querySelectorAll('[data-message-author-role="assistant"]').length
   ```
2. **Fokus ke Composer**:
   Mencari elemen `#prompt-textarea`, melakukan scroll ke posisi tengah, dan memfokuskan kursor ke input area.
3. **Memasukkan Teks Prompt**:
   - **Metode Utama**: Menggunakan `document.execCommand('insertText', false, txt)`.
   - **Metode Cadangan (Fallback)**: Menggunakan simulasi *ClipboardEvent* via `DataTransfer` untuk menjaga keutuhan karakter Unicode/non-ASCII (mencegah karakter berbahasa asing berubah menjadi tanda tanya `?`).
4. **Kirim Pesan**:
   Menekan tombol `Enter` pada keyboard via Playwright (`page.keyboard.press("Enter")`).

---

## 3. Cara Mengambil Respons (*Polling & Streaming*)

Setelah pesan terkirim, aplikasi masuk ke dalam **loop polling** setiap **0.25 detik** dengan batas waktu maksimal 480 detik (8 menit).

### Pembacaan Status DOM
Di setiap iterasi loop, JavaScript dieksekusi di halaman browser untuk mengekstrak data status:
- **`cur`**: Teks (`innerText`) dari elemen pesan asisten paling terakhir (`[data-message-author-role="assistant"]`).
- **`gen`**: Boolean penanda apakah ChatGPT sedang mengetik (ditandai dengan munculnya tombol stop `[data-testid="stop-button"]`).
- **`sbtn_ok`**: Boolean penanda apakah tombol send (`[data-testid="send-button"]`) sudah kembali aktif (tidak *disabled*).
- **`turns`**: Jumlah pesan asisten yang terdeteksi di DOM.

### Streaming Delta
- Jika teks `cur` mengalami penambahan dibanding teks putaran sebelumnya (`last`), aplikasi menghitung potongan teks baru (`delta = cur[len(last):]`).
- Potongan teks ini langsung di-*yield* secara realtime sebagai stream:
  ```python
  {"delta": delta, "text": cur}
  ```
- *Idle counter* di-reset kembali ke `0` setiap ada teks baru.

---

## 4. Syarat & Deteksi Respons Selesai (*Completion Detection*)

Aplikasi menganggap ChatGPT sudah selesai merespons dan menghentikan loop (*break*) apabila salah satu kondisi berikut terpenuhi:

1. **Kondisi Normal (Ideal)**:
   - Terdeteksi giliran pesan baru (`turns > turns0`).
   - Tombol *Stop* sudah tidak ada (`not gen`).
   - Tombol *Send* sudah aktif kembali (`sbtn_ok`).
   - Tidak ada penambahan teks selama minimal 1 detik (`idle >= 4` tick).
2. **Kondisi Cadangan**:
   - Tombol *Stop* sudah hilang dan tidak ada teks baru selama 3 detik (`idle >= 12` tick).
3. **Safety Timeout**:
   - Tidak ada perubahan teks sama sekali selama ~15 detik (`idle > 60` tick).
4. **Global Timeout**:
   - Total waktu polling melampaui 480 detik (8 menit).

Setelah loop berhenti, fungsi mengembalikan teks akhir yang berhasil diakumulasikan:
```python
{"done": True, "text": last}
```

---

## 5. Cara Ekstraksi Respons (*Copy / Return*)

Aplikasi tidak menggunakan simulasi tombol copy clipboard (Ctrl+C), melainkan:
1. Membaca properti `innerText` dari node DOM asisten terakhir secara langsung.
2. Teks ditangkap oleh `ask_stream()` di `chat.py`.
3. Diteruskan ke Gateway Server (`server.py`) dan diformat oleh API Adapter (`routes.py`) ke format standar OpenAI:
   ```json
   {
     "id": "chatcmpl-...",
     "object": "chat.completion",
     "choices": [
       {
         "index": 0,
         "message": {
           "role": "assistant",
           "content": "Isi teks respons akhir..."
         },
         "finish_reason": "stop"
       }
     ]
   }
   ```

---

## 6. Penanganan Error (*Error Handling*)

### A. Error Eksplisit (Menghentikan Eksekusi & Mengembalikan Error ke Client)
1. **`composer not found`**: Kotak input `#prompt-textarea` tidak ditemukan atau gagal di-klik dalam 8 detik.
2. **`prompt insert failed`**: Gagal memasukkan teks prompt baik lewat `insertText` maupun simulasi *clipboard*.
3. **`send failed`**: Gagal menekan tombol Enter via keyboard.

### B. Error Terabaikan / Fallback (*Silent Handling*)
- **Gagal Klik New Chat**: Jika tombol tidak ditemukan, diabaikan via `try-except`.
- **Gagal Reload (Mode NO_LOGIN)**: Jika reload timeout, proses tetap berlanjut.
- **Gagal Hitung Turn Awal**: Fallback nilai `turns0 = 0`.
- **Gagal Tutup Modal/Popup**: Error penutupan dialog diabaikan.
- **Gagal Baca Snapshot DOM**: Jika parsing DOM gagal sesaat (misal saat DOM re-rendering), loop langsung `continue` ke iterasi berikutnya tanpa memutus koneksi.

---

## 7. Mekanisme Percakapan Baru (*New Chat*)

Aplikasi menentukan apakah perlu mereset obrolan atau melanjutkan percakapan berdasarkan muatan prompt di `routes.py`:

- **Multi-turn (`reset=False`)**: Jika prompt baru merupakan kelanjutan dari prompt sebelumnya, pesan langsung dikirimkan ke obrolan yang sedang aktif tanpa membuat chat baru.
- **Topik Baru (`reset=True`)**:
  - **Mode Normal (Login)**: Playwright mencari dan mengklik tombol antarmuka ChatGPT dengan selector `[data-testid="create-new-chat-button"]`, lalu menunggu `0.8` detik.
  - **Mode Anonim (`NO_LOGIN`)**: Membuka percakapan baru dengan memuat ulang halaman (`page.reload(...)`).
  - **Pembersihan Modal**: Setelah reset, sistem secara otomatis mengklik tombol penutup dialog/modal (`[role="dialog"] button`, `[data-testid="close-button"]`) dan memfokuskan kembali kursor ke `#prompt-textarea`.
