# Translation Endpoints Documentation

This document describes the API endpoints for the translation service in the Hermes ChatGPT Web application.

## 1. Submit Translation Job
**Endpoint:** `POST /translate`

Submits a new translation job. This is an asynchronous operation. The endpoint will return a job ID which can be used to poll for the status.

### Request Body
Content-Type: `application/json`

| Field | Type | Required | Description |
|---|---|---|---|
| `model` | string | Yes | The LLM model to use for translation. |
| `source_lang` | string | Yes | The source language (e.g., "id", "en", "ko"). Must be in supported languages. |
| `target_lang` | string | Yes | The target language (e.g., "en", "id"). Must be in supported languages. |
| `novel_id` | string | Yes | Unique identifier for the novel. |
| `chapter_number` | integer | Yes | The chapter number. |
| `text` | string | Yes | The text to be translated. Cannot exceed max length. |
| `force` | boolean | No | If true, forces re-translation even if the chapter is already translated. Defaults to `false`. |

### Example cURL Requests

#### Basic Translation (Submit new job)
```bash
curl -X POST http://127.0.0.1:18111/translate \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "model": "gpt-5.6-luna",
    "source_lang": "ko",
    "target_lang": "id",
    "novel_id": "novel_abc",
    "chapter_number": 1,
    "text": "다음 날 학교, 수아는 처음 보는 남학생과 눈이 마주쳤다.\n\n*저 사람은 누구지?*\n\n쉬는 시간, 그가 먼저 다가와 말을 걸었다.\n\n\"안녕. 나는 **이현우**라고 해. 너도 **각성자**지?\"\n\n수아는 놀라서 뒷걸음질 쳤다.\n\n\"어, 어떻게 그걸...\"\n\n\"느낌으로 알아. 나도 같은 부류거든.\"\n\n현우는 손바닥을 펼쳐 보였다. 순간 손끝에서 푸른 불꽃이 피어올랐다.\n\n![현우의 손에서 피어오르는 푸른 불꽃](https://07.ikiru.wtf/wp-content/uploads/2025/02/084b482e-4e9f-4319-b524-cac4526e3be2-370301-Yd2MKRBI-128x183.jpg)\n\n\"이건 **청염**이라는 기술이야. **각성자 연합**에 소속된 사람들만 배울 수 있지.\"\n\n\"각성자... 연합?\"\n\n\"우리처럼 눈뜬 사람들을 관리하고 보호하는 조직이야. 너도 곧 알게 될 거야.\"\n\n수아는 문득 어젯밤 창밖에서 들렸던 소리가 떠올랐다. 어쩌면 저 조직과 관련이 있을지도 모른다는 생각이 들었다.."
  }'
```

#### Force Re-translate
Use this when a chapter has already been translated but you want to re-translate it. Requires `force: true`.
```bash
curl -X POST http://127.0.0.1:18111/translate \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "model": "gpt-5.6-luna",
    "source_lang": "ko",
    "target_lang": "id",
    "novel_id": "novel_abc",
    "chapter_number": 1,
    "text": "다음 날 학교, 수아는 처음 보는 남학생과 눈이 마주쳤다.",
    "force": true
  }'
```

#### Minimal Request (Chinese → Indonesian)
```bash
curl -X POST http://127.0.0.1:18111/translate \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{
    "model": "gpt-5.6-luna",
    "source_lang": "zh",
    "target_lang": "id",
    "novel_id": "novel_xyz",
    "chapter_number": 5,
    "text": "第二天早上，苏雅在学校遇到了一个陌生的男生。"
  }'
```


### Responses

*   **202 Accepted**
    Translation job accepted and queued for processing.
    ```json
    {
      "id": "job_12345",
      "novel_id": "novel_abc",
      "chapter_number": 1,
      "status": "pending",
      "created_at": "2023-10-27T10:00:00Z",
      "model": "gpt-5.6-luna",
      "source_lang": "ko",
      "target_lang": "en"
    }
    ```

*   **400 Bad Request**
    Missing or invalid parameters (e.g., unsupported language, text too long, missing fields).
    ```json
    {
      "error": "unsupported_source_lang",
      "message": "'source_lang' is required. Supported: ['en', 'id', 'ko', 'zh']"
    }
    ```

*   **409 Conflict**
    Job already in progress or already translated.
    ```json
    {
      "error": "job_already_in_progress",
      "message": "A job for novel 'novel_abc' chapter 1 is already processing (job_id: job_12345).",
      "job_id": "job_12345"
    }
    ```
    Or if already translated without `force`:
    ```json
    {
      "error": "chapter_already_translated",
      "hint": "use force:true to re-translate",
      "job_id": "job_12345"
    }
    ```

---

## 2. Get Translation Status
**Endpoint:** `GET /translate/{job_id}`

Polls the status of a submitted translation job and retrieves the result if completed.

### Path Parameters
*   `job_id` (string): The ID of the job returned by the `POST /translate` endpoint.

### Example cURL Request

```bash
curl -X GET http://localhost:18111/translate/job_12345
```

### Responses

*   **200 OK (Pending/Processing)**
    ```json
    {
      "job_id": "job_12345",
      "status": "processing",
      "novel_id": "novel_abc",
      "chapter_number": 1,
      "created_at": "2023-10-27T10:00:00Z",
      "updated_at": "2023-10-27T10:01:00Z",
      "result": null,
      "error": null
    }
    ```

*   **200 OK (Done)**
    ```json
    {
      "job_id": "job_12345",
      "status": "done",
      "novel_id": "novel_abc",
      "chapter_number": 1,
      "created_at": "2023-10-27T10:00:00Z",
      "updated_at": "2023-10-27T10:05:00Z",
      "result": {
        "translation": "This is the translated text...",
        "chapter_summary": "A brief summary of the chapter."
      },
      "error": null
    }
    ```

*   **200 OK (Failed)**
    ```json
    {
      "job_id": "job_12345",
      "status": "failed",
      "novel_id": "novel_abc",
      "chapter_number": 1,
      "created_at": "2023-10-27T10:00:00Z",
      "updated_at": "2023-10-27T10:02:00Z",
      "result": null,
      "error": {
        "code": "translation_error",
        "message": "Failed to communicate with LLM provider.",
        "retry_count": 2
      }
    }
    ```

*   **404 Not Found**
    The specified job ID does not exist.
    ```json
    {
      "error": {
        "message": "Job 'job_invalid' not found",
        "type": "not_found"
      }
    }
    ```

---

## 3. Get Novel Context (Debug)
**Endpoint:** `GET /translate/novel/{novel_id}/context`

A debug endpoint to view the current accumulated context (characters and glossary) for a specific novel.

### Path Parameters
*   `novel_id` (string): Unique identifier for the novel.

### Example cURL Request

```bash
curl -X GET http://localhost:18111/translate/novel/novel_abc/context
```

### Responses

*   **200 OK**
    ```json
    {
      "novel_id": "novel_abc",
      "characters": [
        {
          "name": "Hero",
          "description": "The main protagonist."
        }
      ],
      "glossary": {
        "magic_sword": "A sword that glows in the dark."
      }
    }
    ```

---

## 4. Cancel/Delete Translation Job
**Endpoint:** `DELETE /translate/{job_id}`

Cancels a running job or deletes an existing (done/failed) job from the database.

### Path Parameters
*   `job_id` (string): The ID of the job to delete.

### Example cURL Request

```bash
curl -X DELETE http://localhost:18111/translate/job_12345
```

### Responses

*   **200 OK**
    ```json
    {
      "status": "deleted",
      "job_id": "job_12345"
    }
    ```

*   **404 Not Found**
    The specified job ID does not exist.
    ```json
    {
      "error": {
        "message": "Job 'job_invalid' not found",
        "type": "not_found"
      }
    }
    ```
