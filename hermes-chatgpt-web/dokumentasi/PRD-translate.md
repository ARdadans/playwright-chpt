# PRD — Endpoint `/translate` (v2, final)

## 1. Ringkasan Keputusan (update dari v1)

| Pertanyaan terbuka | Keputusan |
|---|---|
| Webhook vs polling | **Polling** dulu untuk v1 (`GET /translate/{job_id}`). Webhook tidak diimplementasikan sekarang. |
| `force: true` re-translate | **Perlu approval/flag khusus** — tidak bisa dipanggil bebas oleh siapa saja yang punya akses ke endpoint biasa. Lihat §5.1.3. |
| Batas ukuran teks | **100.000 karakter** — berlaku untuk **field `text` (isi novel) itu sendiri**, bukan total ukuran payload JSON keseluruhan. |

## 2. Perubahan Arsitektur Utama

Kamu sudah punya endpoint generik `POST /v1/chat/completions` yang menerima `messages` (role `system`/`user`) + `model`, dan mengembalikan response format ala OpenAI. Endpoint itu **tidak diubah** — tetap generik, tidak tahu apa-apa soal translation.

`POST /translate` adalah **modul terpisah** yang:
1. Menerima payload spesifik translation (lihat §4).
2. Berdasarkan `source_lang`, memilih **system prompt yang sudah di-hardcode** di server (KO→ID, EN→ID, atau JA→ID — lihat §7).
3. Menyusun `messages` array (system + user, dengan continuity context dan delimiter `<<<TEXT_START>>>`).
4. **Memanggil fungsi/mekanisme completion yang SAMA** dengan yang dipakai `/v1/chat/completions` secara internal (bukan duplikat logic HTTP call ke LLM provider) — lihat §3.
5. Parse response JSON, simpan ke SQLite, kelola job queue.

```
┌─────────────────────┐        ┌──────────────────────────┐
│   POST /translate    │        │  POST /v1/chat/completions│
│   (modul baru)        │        │  (sudah ada, tidak diubah)│
└──────────┬───────────┘        └─────────────┬─────────────┘
           │                                    │
           │  build messages[]                  │
           │  (system prompt dipilih             │
           │   berdasarkan source_lang)          │
           ▼                                    │
   ┌───────────────────────┐                    │
   │  createChatCompletion() │◄──────────────────┘
   │  (shared internal module)│   dipanggil langsung sebagai
   └───────────┬─────────────┘   function call, BUKAN lewat HTTP
               │                  (hindari network hop internal)
               ▼
   ┌───────────────────────┐
   │  parse & validate JSON  │
   └───────────┬─────────────┘
               ▼
   ┌───────────────────────┐
   │  simpan ke SQLite        │
   │  (translation_jobs,       │
   │   characters, glossary)   │
   └───────────────────────┘
```

## 3. Modul `createChatCompletion()` (shared, reused)

Fungsi internal yang jadi satu-satunya tempat logic "panggil LLM provider" berada. Baik handler HTTP `/v1/chat/completions` maupun modul `/translate` memanggil fungsi yang sama ini — supaya tidak ada duplikasi logic retry/timeout/error-handling terhadap LLM provider.

```
// pseudocode struktur modul

// file: core/completion.js  (SUDAH ADA — dipakai oleh /v1/chat/completions)
async function createChatCompletion({ model, messages, temperature }) {
  // panggil LLM provider (OpenAI-compatible endpoint / Anthropic / dsb.)
  // return response dalam struktur { role, content, finish_reason, usage }
}

module.exports = { createChatCompletion };
```

```
// file: modules/translate/service.js  (BARU)
const { createChatCompletion } = require('../../core/completion');
const { getSystemPrompt } = require('./prompts');           // §7
const { getExistingContext, saveResult } = require('./repository'); // §6

async function runTranslationJob(job) {
  const systemPrompt = getSystemPrompt(job.source_lang, job.target_lang);
  const { characters, glossary } = await getExistingContext(job.novel_id);

  const userMessage = buildUserMessage({
    existingCharacters: characters,
    existingGlossary: glossary,
    text: job.source_text,
  });

  const response = await createChatCompletion({
    model: job.model,                 // model dikirim client, diteruskan apa adanya
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userMessage },
    ],
    temperature: 0.3,
  });

  const parsed = parseAndValidateJson(response.content); // §5.3
  await saveResult(job, parsed);
  return parsed;
}

module.exports = { runTranslationJob };
```

Keuntungan desain ini:
- **Tidak ada duplikasi** logic pemanggilan LLM provider (retry, timeout, auth ke provider) — semua tetap satu sumber kebenaran di `core/completion.js`.
- `/translate` murni modul **orkestrasi domain-spesifik** (pilih prompt, susun context, parse, simpan) — tidak tahu detail bagaimana cara bicara ke LLM provider.
- Kalau nanti provider LLM diganti/ditambah, cukup ubah `core/completion.js`, seluruh modul lain (termasuk `/translate`) otomatis ikut tanpa perubahan.

## 4. `POST /translate`

### 4.1 Payload

```json
{
  "model": "gpt-5.6-luna",
  "source_lang": "ko",
  "target_lang": "id",
  "novel_id": "the-awakened-ones",
  "chapter_number": 4,
  "text": "다음 날 학교, 수아는 처음 보는 남학생과 눈이 마주쳤다...",
  "force": false
}
```

| Field | Type | Required | Deskripsi |
|---|---|---|---|
| `model` | string | ya | Nama model yang diteruskan apa adanya ke `createChatCompletion()` — sama seperti field `model` di `/v1/chat/completions`. Modul `/translate` tidak memvalidasi model spesifik (biar tetap fleksibel), hanya meneruskan. |
| `source_lang` | string | ya | `"ko"` \| `"en"` \| `"ja"` — v1 mendukung 3 pasangan ini saja. Selain itu → `400`. |
| `target_lang` | string | ya | v1 hanya menerima `"id"`. Selain itu → `400`. |
| `novel_id` | string | ya | Scoping continuity context. |
| `chapter_number` | integer | ya | Nomor chapter, dipakai untuk unique constraint + urutan. |
| `text` | string | ya | Teks sumber. **Maks 100.000 karakter** (dihitung dari field ini saja, bukan total payload). |
| `force` | boolean | tidak (default `false`) | Re-translate chapter yang statusnya sudah `done`. Wajib disertai header approval — lihat §4.3. |

### 4.2 Validasi

| Kondisi | Response |
|---|---|
| `text` kosong setelah trim | `400 { "error": "text_empty" }` |
| `text` > 100.000 karakter | `400 { "error": "text_too_long", "max_length": 100000, "received_length": N }` |
| `source_lang` bukan salah satu dari `ko`/`en`/`ja` | `400 { "error": "unsupported_source_lang" }` |
| `target_lang` bukan `id` | `400 { "error": "unsupported_target_lang" }` |
| `model` kosong | `400 { "error": "model_required" }` |
| Kombinasi `novel_id`+`chapter_number` sudah ada dengan status `pending`/`processing` | `409 { "error": "job_already_in_progress" }` |
| Kombinasi sudah `done` dan `force` bukan `true` | `409 { "error": "chapter_already_translated", "hint": "use force:true with approval header to re-translate" }` |
| `force: true` tapi tanpa header approval (§4.3) | `403 { "error": "force_requires_approval" }` |

### 4.3 Approval untuk `force: true`

Karena `force: true` bisa menimpa hasil translate yang sudah ada (berpotensi merusak data yang sudah dipakai/dipublikasikan), request dengan `force: true` **wajib** menyertakan header approval terpisah dari auth biasa:

```
X-Translate-Override-Token: <token khusus>
```

- Token ini **berbeda** dari API key biasa yang dipakai untuk request normal — treat sebagai permission terpisah/elevated, bukan sekadar flag boolean yang bisa dikirim siapa saja.
- Kalau `force: true` dikirim tanpa header ini (atau token tidak valid) → `403 { "error": "force_requires_approval" }`, job **tidak dibuat sama sekali**.
- Rekomendasi implementasi: token ini bisa berupa static secret yang disimpan di env server (`TRANSLATE_OVERRIDE_TOKEN`), atau kalau sistem auth kamu sudah role-based, cukup role tertentu (misal `admin`/`editor`) yang boleh mengirim `force: true` — pilih salah satu tergantung sistem auth yang sudah kamu punya. PRD ini tidak mengasumsikan sistem auth spesifik, hanya menegaskan **harus ada gate terpisah**, tidak boleh sekadar boolean di body yang bebas dipakai siapa saja yang bisa akses endpoint.
- Saat `force: true` diproses, hasil lama dipindah ke tabel `translation_history` (lihat PRD v1 §6.5) sebelum ditimpa.

### 4.4 Response `202 Accepted`

```json
{
  "job_id": "job_9f8a3c2e",
  "status": "pending",
  "novel_id": "the-awakened-ones",
  "chapter_number": 4,
  "model": "gpt-5.6-luna",
  "source_lang": "ko",
  "target_lang": "id"
}
```

## 5. `GET /translate/{job_id}`

Sama seperti desain PRD v1 §5.2 (tidak berubah) — status `pending`/`processing`/`done`/`failed`, dengan `result` berisi `translation`, `chapter_summary`, `characters_new`, `characters_updated`, `glossary_new`, `glossary_updated`.

### 5.3 Parse & validasi JSON dari LLM

```
function parseAndValidateJson(rawContent) {
  // 1. Strip markdown code fence kalau model tetap membungkusnya (```json ... ```)
  //    walau instruksi sudah melarang — defensive parsing.
  // 2. JSON.parse()
  // 3. Validasi schema: field wajib ada (translation, chapter_summary, characters[], glossary[])
  //    dan tipe data sesuai (string/array/enum status new|updated).
  // 4. Kalau gagal di langkah manapun → throw ke retry logic (PRD v1 §7.4).
}
```

## 6. SQLite — tidak berubah dari PRD v1

Tabel `translation_jobs`, `characters`, `glossary`, `translation_history` tetap sama seperti didesain sebelumnya (§6 PRD v1), dengan tambahan kolom `model` di `translation_jobs` untuk audit model apa yang dipakai per job:

```sql
ALTER TABLE translation_jobs ADD COLUMN model TEXT NOT NULL DEFAULT '';
```

## 7. System Prompts per Pasangan Bahasa

Setiap `source_lang` punya system prompt terpisah, dipilih via `getSystemPrompt(source_lang, target_lang)`. Ketiganya berbagi kerangka rule yang sama (continuity context, JSON output schema, integrity handling) tapi berbeda di bagian yang budaya/linguistik-spesifik (honorifik, speech level, dialek).

### 7.1 `KO_TO_ID_PROMPT`

Lihat file `src/hermes_chatgpt_web/translation/prompt.py` — system prompt Korea→Indonesia lengkap (rules #1-30) yang sudah difinalisasi.

### 7.2 `EN_TO_ID_PROMPT`

Lihat file `src/hermes_chatgpt_web/translation/prompt.py` — system prompt English→Indonesian lengkap.

### 7.3 `JA_TO_ID_PROMPT`

Lihat file `src/hermes_chatgpt_web/translation/prompt.py` — system prompt Japanese→Indonesian lengkap.

### 7.4 Routing prompt berdasarkan `source_lang`

```python
SUPPORTED_PAIRS = {
    ("ko", "id"): KO_TO_ID_PROMPT,
    ("en", "id"): EN_TO_ID_PROMPT,
    ("ja", "id"): JA_TO_ID_PROMPT,
}


def get_system_prompt(source_lang, target_lang):
    return SUPPORTED_PAIRS.get((source_lang, target_lang))
```

## 8. Perbedaan Kunci Antar Ketiga Prompt

| Aspek | KO→ID | EN→ID | JA→ID |
|---|---|---|---|
| Speech level | 반말/존댓말 → aku/kau vs saya/Anda | Formality lewat diksi & address term (sir/ma'am) → aku/kau vs saya/Anda | 敬語(keigo)/タメ口(tameguchi) → aku/kau vs saya/Anda |
| Honorifik dipertahankan | hyung-nim, oppa, noona, sunbae | Lord, Lady, Sir, Duke (opsional lokalisasi ke "Tuan" kalau lebih natural) | -san, -chan, -kun, -sama, senpai, kouhai |
| Dialek contoh | Gyeongsang satoori (-라예, -쓴다) | Southern drawl, Cockney, AAVE → render sebagai diksi informal, HINDARI memetakan ke dialek daerah Indonesia asli | Kansai-ben (おおきに, あかん) |
| Tanda kutip dialog | "..." dipertahankan | "..." dipertahankan | 「...」 dipertahankan, TIDAK dikonversi ke tanda kutip Barat |
| Isu integritas teks spesifik | Spacing error (띄어쓰기) | — | Furigana artifact, kanji OCR misread |

## 9. Ringkasan Payload Final `/translate`

```json
{
  "model": "string (required, diteruskan apa adanya ke createChatCompletion)",
  "source_lang": "ko | en | ja",
  "target_lang": "id",
  "novel_id": "string",
  "chapter_number": "integer",
  "text": "string, max 100000 chars",
  "force": "boolean, default false, requires X-Translate-Override-Token header when true"
}
```

## 10. Data Model (SQLite)

### 10.1 Tabel `translation_jobs`

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | TEXT PK | `job_id`, format `job_<random>` |
| `novel_id` | TEXT | FK logis ke novel |
| `chapter_number` | INTEGER | Nomor chapter |
| `source_lang` | TEXT | `"ko"` / `"en"` / `"ja"` |
| `target_lang` | TEXT | `"id"` |
| `source_text` | TEXT | Teks asli yang dikirim client |
| `model` | TEXT | Model yang dipakai (diteruskan dari client) |
| `status` | TEXT | `pending` \| `processing` \| `done` \| `failed` |
| `result_translation` | TEXT | Hasil field `translation` (nullable) |
| `result_summary` | TEXT | Hasil field `chapter_summary` (nullable) |
| `error_code` | TEXT | Nullable |
| `error_message` | TEXT | Nullable |
| `retry_count` | INTEGER | Default 0 |
| `created_at` | TEXT (ISO8601) | |
| `updated_at` | TEXT (ISO8601) | |

Index: `UNIQUE(novel_id, chapter_number)`

### 10.2 Tabel `characters`

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `novel_id` | TEXT | Scoping per-novel |
| `name` | TEXT | Nama di teks Indonesia |
| `native_name` | TEXT | Nama asli karakter |
| `gender` | TEXT | `male` \| `female` \| `unknown` |
| `notes` | TEXT | Deskripsi kumulatif (merge strategy: append with chapter tag) |
| `first_seen_chapter` | INTEGER | Chapter saat pertama muncul |
| `last_updated_chapter` | INTEGER | Chapter saat terakhir ada update |
| `created_at` | TEXT | |
| `updated_at` | TEXT | |

Index: `UNIQUE(novel_id, name)`

### 10.3 Tabel `glossary`

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `novel_id` | TEXT | Scoping per-novel |
| `term_source` | TEXT | Istilah asli |
| `term_translation` | TEXT | Istilah di teks Indonesia |
| `notes` | TEXT | Penjelasan kumulatif |
| `first_seen_chapter` | INTEGER | |
| `last_updated_chapter` | INTEGER | |
| `created_at` | TEXT | |
| `updated_at` | TEXT | |

Index: `UNIQUE(novel_id, term_source)`

### 10.4 Strategi merge untuk `status: "updated"`

```
existing.notes = existing.notes + "\n[Ch{chapter_number}] " + new_entry.notes
existing.last_updated_chapter = chapter_number
existing.updated_at = now()
```

### 10.5 Tabel `translation_history` (audit re-translate)

| Kolom | Tipe |
|---|---|
| `id` | INTEGER PK AUTOINCREMENT |
| `job_id` | TEXT |
| `novel_id` | TEXT |
| `chapter_number` | INTEGER |
| `result_translation` | TEXT |
| `result_summary` | TEXT |
| `archived_at` | TEXT |

## 11. Job Queue & Worker

### 11.1 Queue berbasis SQLite

Worker polling `SELECT ... WHERE status='pending' ORDER BY created_at LIMIT 1` lalu klaim row (update ke `processing`) sebelum proses.

### 11.2 Job lifecycle

```
pending → processing → done
                     ↘ failed → (resubmit manual via POST baru) → pending
```

### 11.3 Retry policy

| Jenis kegagalan | Retry? | Max attempts | Backoff |
|---|---|---|---|
| LLM API error (timeout, 5xx) | Ya | 3 | Exponential (2s, 8s, 30s) |
| JSON parse invalid | Ya | 3 | Immediate retry dengan prompt tambahan |
| JSON valid tapi schema tidak sesuai | Ya | 2 | Immediate retry |
| SQLite write error | Ya | 3 | Short delay (1s) |
| Validasi awal gagal | Tidak | — | Langsung `failed` |

### 11.4 Concurrency

- `WORKER_CONCURRENCY` default = 1
- SQLite WAL mode enabled

## 12. Non-Functional Requirements

- **Observability:** setiap job menyimpan `retry_count`, `error_code`, `error_message`.
- **Data retention:** tidak ada auto-delete di v1.
- **Backward compatibility:** payload menyertakan `source_lang`/`target_lang` eksplisit.
- **Timeout:** job worker timeout 120 detik default.