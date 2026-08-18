# Hermes Client Application (`client-app`)

CLI Client dan Automation Tool untuk berinteraksi dengan backend **Hermes Novel Translation System** (`hermes-chatgpt-web`).

Aplikasi ini mendukung seluruh fitur terbaru dari Hermes, termasuk **True Concurrent Multi-Account Processing**, **Database Backup & Restore System**, **Asynchronous Translation Queue**, **Per-Context Cooldown**, **Dynamic `/settings` Management**, **Cookie Worker Pool & Context Refresh**, **Novel & Continuity Tracking (Characters & Glossary CRUD/Bulk/Export)**, **Job Version History & Rollback**, serta **Batch TOC Generator**.

---

## 📌 Fitur Utama

- ⚡ **True Concurrent Multi-Account Processing**: Ketika backend Hermes memiliki banyak akun aktif, pengiriman batch bab novel akan langsung diproses secara **paralel murni (concurrent)** oleh worker-worker yang siap tanpa bottleneck.
- 💾 **Sistem Backup & Restore SQLite (`db` / `backup` / `restore`)**: Mengunduh arsip snapshot konsisten `.zip` berisi database SQLite dan metadata, melihat statistik tabel, dan merestore database dari file `.zip`.
- 🚀 **Asynchronous Queue Integration (`/translate`)**: Mengirimkan bab novel ke antrean SQLite Hermes, polling realtime hingga selesai, dan menyimpan output Markdown secara otomatis.
- ⏱️ **Context Cooldown & Worker Monitoring**: Melihat status isolated Playwright context, status cooldown (default 60 detik), mereset cooldown per akun, dan me-refresh browser context.
- ⚙️ **Dynamic Server Settings (`/settings`)**: Mengatur parameter `job_cooldown_seconds`, `worker_poll_interval`, dan parameter server lainnya langsung dari CLI.
- 👥 **Character Continuity Management**: CRUD karakter lengkap (add, list, get, update, delete) dengan pelacakan bab kemunculan (`first_seen_chapter`, `appeared_chapters`).
- 📖 **Glossary Dictionary Management**: CRUD glossary (add, list, get, update, delete), import massal (`bulk`), dan ekspor ke format JSON / CSV.
- 🔄 **Job Version History & Rollback**: Melihat riwayat revisi terjemahan lama dan melakukan rollback hasil terjemahan ke versi sebelumnya.
- 🛑 **Bulk Job Cancellation (`cancel-all`)**: Membatalkan seluruh job yang sedang pending atau running sekaligus.
- 📑 **Batch Translation & TOC Generator**: Memindai folder bab novel, membuat `toc.json` dan `toc.md`, serta mengirimkan batch bab ke queue terjemahan.
- 💬 **Interactive Chat Fallback**: Mode terminal chat langsung menggunakan backend ChatGPT Web (`/v1/chat/completions`).
- 🌐 **Global Server Flag & Dynamic Config**: Fleksibel dijalankan dengan flag `--server`, `--internal-key`, `--model`, dll. via `uv run start`.

---

## 🛠️ Instalasi & Konfigurasi

### 1. Prasyarat & Setup
Pastikan server `hermes-chatgpt-web` sudah berjalan.

```bash
cd client-app
uv sync
```

### 2. Konfigurasi Server (Flag CLI atau Environment Variable)

Anda dapat menentukan alamat server Hermes langsung menggunakan flag `--server` / `-s` saat menjalankan `uv run start`, atau melalui environment variable.

#### Opsi A: Menggunakan Flag CLI (Direkomendasikan)
```bash
# Menghubungkan ke server lokal atau remote
uv run start --server http://192.168.1.50:18111 status
uv run start --server http://localhost:18111 translate --file chapter_1.txt --wait
```

#### Opsi B: Menggunakan Environment Variable
```bash
# Windows PowerShell
$env:HERMES_API_URL = "http://localhost:18111"
$env:HERMES_DEFAULT_MODEL = "gpt-5.6-luna"
$env:HERMES_SOURCE_LANG = "ko"   # ko, ja, zh, en
$env:HERMES_TARGET_LANG = "id"   # id, en

# Linux / macOS Bash
export HERMES_API_URL="http://localhost:18111"
```

---

## 🚩 Parameter / Flag Global

Flag berikut dapat disisipkan baik di tingkat root maupun setelah subcommand:

| Flag | Alias | Deskripsi | Default |
|------|-------|-----------|---------|
| `--server` | `-s`, `--url` | Alamat base URL server Hermes (e.g. `http://localhost:18111` atau `:18111`) | `http://localhost:18111` |
| `--internal-key` | `-k` | Key otentikasi internal (`X-Internal-Key`) jika diaktifkan di server | `""` |
| `--model` | `-m` | Model LLM yang digunakan | `gpt-5.6-luna` |
| `--source-lang` | `--src` | Bahasa sumber (e.g. `ko`, `ja`, `zh`, `en`) | `ko` |
| `--target-lang` | `--tgt` | Bahasa target terjemahan (e.g. `id`, `en`) | `id` |
| `--timeout` | `-t` | Batas waktu timeout permintaan / wait polling (detik) | `300` |

---

## 📖 Panduan Penggunaan CLI (`uv run start`)

### 1. Mode Interaktif Terminal Dashboard (Direkomendasikan)

Jika dijalankan tanpa subcommand, aplikasi akan membuka **Menu Interaktif Terminal** yang terus berjalan dalam loop (tidak langsung keluar), sehingga Anda dapat melakukan berbagai aksi secara berulang dan cepat:

```bash
# Menjalankan menu interaktif dengan server default (localhost:18111)
uv run start

# Menjalankan menu interaktif yang terhubung ke server remote / lokal lain
uv run start --server http://192.168.1.50:18111
uv run start -s :18111
```

Dalam mode interaktif, tersedia menu:
- `[1]` 📊 Status Server & Worker Pool Cooldowns
- `[2]` 🚀 Terjemahkan Satu Bab Novel (File / Text)
- `[3]` 📑 Batch Ingestion Seluruh Bab Folder Novel
- `[4]` 📋 Manajemen Jobs & Riwayat Revisi (History/Rollback)
- `[5]` 📚 Manajemen Novel (Karakter, Glossary, Chapters, Stats)
- `[6]` 🍪 Akun Cookie & Browser Context (Refresh/Cooldown/CRUD)
- `[7]` ⚙️ Pengaturan Server Dinamis (`/settings`)
- `[8]` 💾 Backup & Restore Database SQLite Hermes
- `[9]` 💬 Interactive Terminal Chat (ChatGPT Web Backend)
- `[10]` 🌐 Ganti Alamat Server URL / Model / Config saat runtime
- `[0]` 🚪 Keluar (Exit)

---

### 2. Sistem Backup, Restore & Pembaca Arsip Database (`db` / `backup` / `restore` / `read-backup`)

Mengelola snapshot cadangan SQLite database Hermes secara konsisten dan aman, serta membaca/mengeksplorasi isi file backup (`.zip` / `.db`) secara offline langsung berdasarkan path:

```bash
# Melihat statistik jumlah baris seluruh tabel database Hermes aktif
uv run start db stats --server http://localhost:18111

# Mengunduh snapshot backup database (.zip)
uv run start backup --server http://localhost:18111
# Atau dengan path tujuan kustom:
uv run start db backup --output backups/hermes_backup_20260816.zip

# Merestore database dari arsip file .zip
uv run start restore --file backups/hermes_backup_20260816.zip --server http://localhost:18111
# Atau menggunakan subcommand db:
uv run start db restore --file backups/hermes_backup_20260816.zip

# 📖 Membaca & mengeksplorasi arsip backup secara interaktif (Terminal UI)
uv run start read-backup --file hermes_backup_20260817_074046.zip
# Atau cukup jalankan tanpa argumen untuk memilih dari daftar file zip di folder:
uv run start read-backup

# 📊 Inspeksi ringkasan arsip backup via CLI
uv run start db inspect --file hermes_backup_20260817_074046.zip

# 🔍 Melihat daftar bab & status novel tertentu dari file backup
uv run start db inspect --file hermes_backup_20260817_074046.zip --novel office-worker-who-sees-fate

# 📖 Membaca teks hasil terjemahan bab tertentu langsung dari backup
uv run start db inspect --file hermes_backup_20260817_074046.zip --novel office-worker-who-sees-fate --chapter 2

# 💾 Mengekspor seluruh bab selesai dari backup ke folder atau file kompilasi
uv run start db export --file hermes_backup_20260817_074046.zip --novel office-worker-who-sees-fate --format txt --output-dir exported_novel/
uv run start db export --file hermes_backup_20260817_074046.zip --novel office-worker-who-sees-fate --format md --single-file --output novel_lengkap.md
```

---

### 2. Melihat Status Server & Worker Cooldown (`status`)

Menampilkan status server Hermes, ketersediaan worker pool, sisa durasi cooldown per akun context, dan konfigurasi server aktif:

```bash
# Default server (localhost:18111)
uv run start status

# Menentukan server kustom
uv run start --server http://192.168.1.100:18111 status
```

---

### 3. Pengaturan Server Hermes (`settings`)

Melihat, memperbarui, atau mereset konfigurasi server:

```bash
# Melihat seluruh settings aktif
uv run start --server http://localhost:18111 settings

# Mengubah post-job cooldown menjadi 30 detik
uv run start settings --server http://localhost:18111 --set job_cooldown_seconds=30

# Mengubah multiple settings
uv run start settings --set job_cooldown_seconds=45 worker_poll_interval=1.5

# Mereset settings kembali ke default
uv run start settings --reset
```

---

### 4. Menerjemahkan Bab Novel (`translate`)

Menerjemahkan satu file bab novel atau teks langsung via Hermes Queue:

```bash
# Submit ke queue dan tunggu sampai selesai (--wait), lalu simpan hasilnya (--output)
uv run start translate \
  --server http://localhost:18111 \
  --file data-translate/office-worker-who-sees-fate/chapters/chapter_1_프롤로그\ _\ 무당이\ 될\ 아이.txt \
  --novel-id office-worker \
  --chapter 1 \
  --source-lang ko \
  --target-lang id \
  --wait \
  --output output_ch1.md

# Paksa re-translasi jika bab sudah pernah diterjemahkan sebelumnya (--force)
uv run start translate --server http://localhost:18111 --file chapter_1.txt --novel-id office-worker --chapter 1 --force --wait

# Mode Direct Chat Completion (bypass queue)
uv run start translate --server http://localhost:18111 --file chapter_1.txt --direct
```

---

### 5. Batch Ingestion Seluruh Bab Novel (`batch`)

Mengirimkan banyak bab sekaligus dari direktori novel ke antrean Hermes:

```bash
# Kirim bab 1 sampai 10 ke queue
uv run start batch \
  --server http://localhost:18111 \
  --dir data-translate/office-worker-who-sees-fate/chapters \
  --novel-id office-worker-who-sees-fate \
  --from-ch 1 \
  --to-ch 10 \
  --source-lang ko \
  --target-lang id
```

---

### 6. Manajemen Cookie & Worker Context (`cookies`)

```bash
# Menampilkan daftar akun cookie yang tersimpan dan sisa cooldown
uv run start cookies list --server http://localhost:18111

# Menambahkan akun cookie baru dari file JSON
uv run start cookies add --name user2 --file cookies/account2.json

# Menghapus akun cookie
uv run start cookies delete --account-id <ACCOUNT_ID>

# Menjeda (pause) atau melanjutkan (resume) akun
uv run start cookies pause --account-id <ACCOUNT_ID>
uv run start cookies resume --account-id <ACCOUNT_ID>

# Membersihkan cache browser dan membuat context bersih (refresh)
uv run start cookies refresh --account-id <ACCOUNT_ID>

# Mereset paksa cooldown context tertentu secara instan
uv run start cookies reset-cd --account-id <ACCOUNT_ID>
```

---

### 7. Memantau & Mengelola Jobs (`jobs`)

```bash
# Melihat daftar job terjemahan
uv run start jobs list --server http://localhost:18111
uv run start jobs list --novel-id office-worker --status done

# Melihat detail satu job tertentu
uv run start jobs get --job-id <JOB_ID> --server http://localhost:18111

# Retry job yang gagal
uv run start jobs retry --job-id <JOB_ID>

# Membatalkan job tertentu
uv run start jobs cancel --job-id <JOB_ID>

# Membatalkan SELURUH job pending dan running sekaligus (bulk cancel)
uv run start jobs cancel --all
uv run start jobs cancel-all --status pending --novel-id office-worker

# Menghapus job yang pending atau dibatalkan
uv run start jobs delete --job-id <JOB_ID>

# Melihat riwayat revisi dan versi lama bab terjemahan
uv run start jobs history --job-id <JOB_ID>

# Mengembalikan (rollback) terjemahan ke versi history tertentu
uv run start jobs rollback --history-id <HISTORY_ID>
```

---

### 8. Memantau & Mengelola Kontinuitas Novel (`novel`)

```bash
# ── Ringkasan & Bab ──
uv run start novel list --server http://localhost:18111
uv run start novel stats --novel-id office-worker
uv run start novel chapters --novel-id office-worker
uv run start novel history --novel-id office-worker

# ── Manajemen Karakter (CRUD) ──
# Melihat daftar karakter
uv run start novel characters --novel-id office-worker --char-action list
# Menambahkan karakter baru
uv run start novel characters --novel-id office-worker --char-action add --name "Han Yujin" --native "한유진" --gender male --first-ch 1
# Detail karakter
uv run start novel characters --novel-id office-worker --char-action get --char-id 1
# Update karakter
uv run start novel characters --novel-id office-worker --char-action update --char-id 1 --notes "Kakak dari Han Yoohyun"
# Hapus karakter
uv run start novel characters --novel-id office-worker --char-action delete --char-id 1

# ── Manajemen Kamus Glossary (CRUD, Bulk, Export) ──
# Melihat daftar istilah
uv run start novel glossary --novel-id office-worker --gloss-action list
# Menambahkan istilah baru
uv run start novel glossary --novel-id office-worker --gloss-action add --source "헌터" --trans "Hunter" --notes "Sebutan untuk pengguna sihir"
# Detail istilah
uv run start novel glossary --novel-id office-worker --gloss-action get --term-id 1
# Update istilah
uv run start novel glossary --novel-id office-worker --gloss-action update --term-id 1 --trans "Pemburu (Hunter)"
# Hapus istilah
uv run start novel glossary --novel-id office-worker --gloss-action delete --term-id 1
# Import massal glossary dari file JSON
uv run start novel glossary --novel-id office-worker --gloss-action bulk --file glossary.json
# Ekspor glossary ke format JSON atau CSV
uv run start novel glossary --novel-id office-worker --gloss-action export --format csv --output glossary.csv
```

---

### 9. Mode Chat Interaktif (`chat`)
Menggunakan backend ChatGPT Web untuk percakapan interaktif:

```bash
uv run start chat --server http://localhost:18111 --model gpt-5.6-luna
```

---

## 📑 Table of Contents (TOC) Generator Script (`create-toc.py`)

Terletak di `data-translate/create-toc.py`, tool ini memindai file novel `.txt`, mengekstrak nomor bab dan judul, serta menyusun daftar isi terstruktur:

```bash
# Scan bab dan buat toc.json serta toc.md
uv run python data-translate/create-toc.py --dir data-translate/office-worker-who-sees-fate/chapters

# Scan dan langsung submit seluruh bab ke antrean Hermes
uv run python data-translate/create-toc.py \
  --server http://localhost:18111 \
  --dir data-translate/office-worker-who-sees-fate/chapters \
  --novel-id office-worker \
  --submit \
  --from-ch 1 \
  --to-ch 20
```
