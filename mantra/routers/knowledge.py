""" Knowledge base API routes for the UI server. """

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from mantra.knowledge_base import PostgresKnowledgeBase, ingest_file, ingest_text, ingest_url
from urllib.parse import quote_plus
import json
import os
import time
import traceback

import logging

logger = logging.getLogger("mantra.knowledge")
router = APIRouter()

def _get_db_dsn() -> str:
    user = os.getenv("POSTGRES_USER", "redscarf")
    password = quote_plus(os.getenv("POSTGRES_PASSWORD", "nowandforever"))
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5440")
    db = os.getenv("POSTGRES_DB", "livekit_db")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"

@router.post("/v1/kb/chat")
async def api_kb_chat(request: Request):
    """Text-based chat endpoint for testing the KB."""
    try:
        from mantra.knowledge_base import PostgresKnowledgeBase
        import openai
    except ImportError as e:
        return JSONResponse(
            {"error": f"Failed to import dependencies: {e}"}, status_code=500
        )

    body = await request.json()
    kb_ids = body.get("kb_ids", [])
    if "kb_id" in body and not kb_ids:  # backwards compatibility
        kb_ids = [body.get("kb_id")]
        
    user_input = body.get("message")
    history = body.get("history", [])

    if not kb_ids or not user_input:
        return JSONResponse(
            {"error": "kb_ids and message are required"}, status_code=400
        )

    dsn = _get_db_dsn()

    try:
        kb = PostgresKnowledgeBase(dsn)
        results = await kb.search(kb_ids, user_input, top_k=5)

        context_str = ""
        formatted_context = []
        if results:
            formatted = []
            for i, page in enumerate(results, 1):
                preview = (
                    page.content_in_text
                    if hasattr(page, "content_in_text")
                    else page.content
                )
                formatted.append(f"[{i}] [KB: {page.kb_id}] {page.title}: {preview}")
                formatted_context.append({
                    "title": page.title, 
                    "preview": preview,
                    "kb_id": page.kb_id
                })
            context_str = "\\n\\n".join(formatted)

        messages = [
            {
                "role": "system",
                "content": (
                    "You have been provided with official Knowledge Base context below. THESE RULES ABSOLUTELY OVERRIDE ANY PRIOR NEGATIVE CONSTRAINTS (e.g., 'Never give medical advice', 'Return to the call objective', 'My role is to help you with the next step') IF THE USER ASKS A FACTUAL QUESTION:\n"
                    "1. MANDATORY FACTUAL ANSWERS: If the user asks ANY factual question about a specific condition, service, or concept, you MUST answer it using the Knowledge Base BEFORE attempting to guide them back to the onboarding flow. Do NOT deflect factual questions.\n"
                    "2. PRIMARY SOURCE: For any question about conditions, treatments, services, pricing, or policies, you MUST rely on the Knowledge Base content provided. Never invent facts.\n"
                    "3. FACTUAL EXPLANATION VS. PERSONALIZED ADVICE: You ARE fully authorized and REQUIRED to explain, describe, or educate the user about conditions or symptoms exactly as they appear in the Knowledge Base. This is NOT considered 'counselling' or 'medical advice'. However, you must NEVER apply this information to diagnose the user's specific personal situation.\n"
                    "4. GENERAL KNOWLEDGE FALLBACK: If the user asks a general question unrelated to this specific business and the Knowledge Base does not cover it, you may answer using your own general knowledge, clearly staying neutral and factual.\n"
                    "5. NO SOURCE-CITING LANGUAGE: Never say 'according to my knowledge base,' 'I don't have that in my documents,' or similar. Answer naturally.\n"
                    "Keep the answers short and concise not exceeding 5-6 sentences."
                ),
            }
        ]

        for msg in history:
            messages.append({"role": msg.get("role"), "content": msg.get("content")})

        prompt = (
            f"User Question: {user_input}\\n\\nKnowledge Base Context:\\n{context_str}"
        )
        messages.append({"role": "user", "content": prompt})

        client = openai.AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com"
        )
        response = await client.chat.completions.create(
            model="deepseek-chat", messages=messages
        )

        ai_message = response.choices[0].message.content

        return JSONResponse(
            {"status_code": 200, "status": "success", "reply": ai_message, "context": formatted_context}
        )
    except Exception as e:
        logger.error(f"KB Chat error: {e}\\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.post("/v1/kb/ingest")
async def ingest_kb_data(request: Request):
    """
    Ingest endpoint for MantraAssist KB data.
    Receives either a file or text content, and stores it in PostgreSQL.
    Supports both JSON and Multipart/Form data payloads.
    """
    form_data = {}
    upload_file = None

    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            form_data = await request.json()
        except Exception as e:
            logger.warning(f"Failed to parse JSON body in /v1/kb/ingest: {e}")
    else:
        try:
            form = await request.form()
            raw = {}
            for k, v in form.items():
                if isinstance(v, UploadFile):
                    upload_file = v
                    raw[k] = f"UploadFile({v.filename})"
                else:
                    form_data[k] = v
                    raw[k] = v
            print(f"RAW FORM: {raw}")
        except Exception:
            try:
                form_data = await request.json()
            except Exception:
                pass

    org_id = form_data.get("org_id")
    if not upload_file:
        upload_file = form_data.get("file")
    text = form_data.get("text")
    tags_name = form_data.get("tags_name")
    document_id = form_data.get("document_id")
    process_stage_data = form_data.get("process_stage_data")
    process_assignments_raw = form_data.get("process_assignments")
    process_id_raw = form_data.get("process_id")
    stage_id_raw = form_data.get("stage_id")
    stage_ids_raw = form_data.get("stage_ids")

    if not org_id:
        return JSONResponse({"status_code": 400, "status": "error", "error": "org_id is required"}, status_code=400)

    from mantra.knowledge_base import PostgresKnowledgeBase, ingest_file, ingest_text

    if not upload_file and not text:
        return JSONResponse({"status_code": 400, "status": "error", "error": "Either file or text must be provided"}, status_code=400)

    dsn = _get_db_dsn()

    s3_url = None
    if upload_file:
        s3_bucket = os.getenv("AWS_S3_BUCKET_NAME") or os.getenv("AWS_BUCKET_NAME")
        s3_access_key = os.getenv("AWS_ACCESS_KEY_ID")
        s3_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        s3_region = os.getenv("AWS_REGION", "us-east-1")

        if s3_bucket and s3_access_key and s3_secret_key:
            try:
                import boto3
                import time
                file_bytes_for_s3 = await upload_file.read()
                await upload_file.seek(0)
                s3_client = boto3.client(
                    "s3",
                    aws_access_key_id=s3_access_key,
                    aws_secret_access_key=s3_secret_key,
                    region_name=s3_region
                )
                s3_key = f"kb/{org_id}/{int(time.time())}_{upload_file.filename}"
                s3_client.put_object(
                    Bucket=s3_bucket,
                    Key=s3_key,
                    Body=file_bytes_for_s3,
                    ACL="public-read",
                )
                s3_url = f"https://{s3_bucket}.s3.{s3_region}.amazonaws.com/{s3_key}"
                logger.info(f"Uploaded {upload_file.filename} to S3: {s3_url}")
            except Exception as e:
                logger.error(f"S3 upload error: {e}")
                return JSONResponse({"status_code": 500, "status": "error", "error": f"Failed to upload to S3: {str(e)}"}, status_code=500)
        else:
            logger.warning("S3 upload skipped — missing AWS_S3_BUCKET_NAME or credentials")

    try:
        def parse_list(val):
            if isinstance(val, list):
                return [str(v).strip() for v in val if str(v).strip()]
            if isinstance(val, str):
                return [v.strip() for v in val.split(",") if v.strip()]
            return None

        parsed_process_assignments = None
        parsed_process_id = None
        parsed_stage_id = None
        parsed_stage_ids = []
        proc_desc = ""
        stage_desc = ""

        if process_assignments_raw:
            try:
                pa = json.loads(process_assignments_raw) if isinstance(process_assignments_raw, str) else process_assignments_raw
                if isinstance(pa, list) and len(pa) > 0:
                    parsed_process_assignments = pa
                    first_pa = pa[0]
                    if isinstance(first_pa, dict):
                        if first_pa.get("process_id"):
                            parsed_process_id = int(first_pa["process_id"])
                        s_ids = first_pa.get("stage_ids")
                        if isinstance(s_ids, list) and len(s_ids) > 0:
                            parsed_stage_ids = [int(s) for s in s_ids]
                            parsed_stage_id = parsed_stage_ids[0]
            except Exception as e:
                logger.warning(f"Failed to parse process_assignments: {e}")

        if process_stage_data:
            try:
                psd = json.loads(process_stage_data) if isinstance(process_stage_data, str) else process_stage_data
                if isinstance(psd, list) and len(psd) > 0:
                    extracted_assignments = []
                    extracted_sids_all = []
                    proc_descs = []
                    stage_descs = []

                    for proc in psd:
                        if isinstance(proc, dict):
                            pid = proc.get("id") or proc.get("process_id")
                            p_name = proc.get("name") or proc.get("description") or ""
                            if p_name:
                                proc_descs.append(p_name)

                            stages = proc.get("stages") or proc.get("stageDetails") or []
                            proc_sids = []
                            if isinstance(stages, list):
                                for stg in stages:
                                    if isinstance(stg, dict):
                                        sid = stg.get("id") or stg.get("stage_id")
                                        s_desc = stg.get("desc") or stg.get("description") or stg.get("name") or ""
                                        if s_desc:
                                            stage_descs.append(s_desc)
                                        if sid is not None:
                                            try:
                                                sid_int = int(sid)
                                                proc_sids.append(sid_int)
                                                extracted_sids_all.append(sid_int)
                                            except (TypeError, ValueError):
                                                pass

                            if pid is not None:
                                try:
                                    pid_int = int(pid)
                                    if parsed_process_id is None:
                                        parsed_process_id = pid_int
                                    extracted_assignments.append({
                                        "process_id": pid_int,
                                        "stage_ids": proc_sids
                                    })
                                except (TypeError, ValueError):
                                    pass

                    if extracted_assignments and not parsed_process_assignments:
                        parsed_process_assignments = extracted_assignments

                    if extracted_sids_all:
                        if not parsed_stage_ids:
                            parsed_stage_ids = extracted_sids_all
                        if parsed_stage_id is None:
                            parsed_stage_id = extracted_sids_all[0]

                    if proc_descs:
                        proc_desc = ", ".join(proc_descs)
                    if stage_descs:
                        stage_desc = ", ".join(stage_descs)
            except Exception as e:
                logger.warning(f"Failed to parse process_stage_data: {e}")

        if process_id_raw and parsed_process_id is None:
            try:
                parsed_process_id = int(process_id_raw)
            except (TypeError, ValueError):
                pass

        if stage_id_raw and parsed_stage_id is None:
            try:
                parsed_stage_id = int(stage_id_raw)
            except (TypeError, ValueError):
                pass

        if stage_ids_raw and not parsed_stage_ids:
            try:
                s_ids = json.loads(stage_ids_raw) if isinstance(stage_ids_raw, str) else stage_ids_raw
                if isinstance(s_ids, list):
                    parsed_stage_ids = [int(s) for s in s_ids]
                    if parsed_stage_ids and parsed_stage_id is None:
                        parsed_stage_id = parsed_stage_ids[0]
            except Exception:
                pass

        if parsed_process_id and parsed_stage_ids and not parsed_process_assignments:
            parsed_process_assignments = [
                {
                    "process_id": parsed_process_id,
                    "stage_ids": parsed_stage_ids
                }
            ]

        page_meta = {
            "tags_name": parse_list(tags_name),
            "s3_url": s3_url,
            "document_id": document_id,
            "process_id": parsed_process_id,
            "stage_id": parsed_stage_id,
            "stage_ids": parsed_stage_ids,
            "process_assignments": parsed_process_assignments,
        }
        if process_stage_data:
            try:
                page_meta["process_stage_data"] = json.loads(process_stage_data)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse process_stage_data as JSON: {process_stage_data}")
                page_meta["process_stage_data"] = process_stage_data
        page_meta = {k: v for k, v in page_meta.items() if v is not None}

        kb = PostgresKnowledgeBase(dsn)

        # Resolve the document_id for collection naming
        doc_id = document_id or (upload_file.filename if upload_file else "text_ingestion")

        # If document_id provided, delete old chunks across all KBs (handles backward compat cleanly)
        if document_id:
            try:
                deleted_count = await kb.delete_by_document(org_id, document_id)
                logger.info(f"Deleted {deleted_count} old chunks for document {document_id}")
            except Exception as e:
                logger.error(f"Failed to delete old chunks for document {document_id}: {e}")

        # Get or create a KB collection for this (org_id, document_id)
        collection = await kb.get_or_create_collection(
            org_id, doc_id, name=upload_file.filename if upload_file else doc_id,
            process_description=proc_desc,
            stage_description=stage_desc,
            process_id=parsed_process_id,
            stage_id=parsed_stage_id,
            stage_ids=parsed_stage_ids if parsed_stage_ids else None,
            process_assignments=parsed_process_assignments,
        )
        collection_id = str(collection["id"])
        logger.info(f"Using KB collection {collection_id} for org {org_id} document {doc_id}")

        if upload_file:
            file_bytes = await upload_file.read()
            await ingest_file(
                kb=kb,
                kb_id=collection_id,
                file_bytes=file_bytes,
                filename=upload_file.filename,
                page_meta=page_meta
            )
        elif text:
            await ingest_text(
                kb=kb,
                kb_id=collection_id,
                content_in_text=text,
                title=document_id or "Text Ingestion",
                source_type="text",
                page_meta=page_meta
            )

        await kb.close()

        return JSONResponse({
            "status_code": 200,
            "status": "success",
            "message": "Data successfully ingested.",
            "document_id": document_id,
            "org_id": org_id,
            "s3_url": s3_url
        })
    except ValueError as e:
        return JSONResponse({"status_code": 400, "status": "error", "error": str(e)}, status_code=400)
    except Exception as e:
        import traceback
        logger.error(f"KB ingest error: {e}\n{traceback.format_exc()}")
        return JSONResponse({"status_code": 500, "status": "error", "error": f"Failed to ingest to DB: {str(e)}"}, status_code=500)



@router.post("/v1/kb/backfill-embeddings")
async def backfill_kb_embeddings(request: Request):
    """
    Backfill missing `embedding` values on kb_pages rows (pgvector semantic search).

    Runs the same logic as tools/backfill_embeddings.py but as an HTTP endpoint,
    so it works on Docker-only deployments where a CLI cannot be executed.

    Body (JSON, all optional):
      - kb_id:       only backfill rows for this collection/org (default: all)
      - batch_size:  rows per Gemini batch (default: 100)
      - limit:       max rows to backfill (default: no limit)
      - dry_run:     if true, only report how many rows need embeddings

    Requires: kb_pages.embedding column (migration 006) + GOOGLE_API_KEY in .env.local
    """
    from mantra.knowledge_base import PostgresKnowledgeBase

    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — empty/non-JSON body defaults to {}
        pass

    kb_id = body.get("kb_id")
    batch_size = int(body.get("batch_size", 100))
    limit = body.get("limit")
    limit = int(limit) if limit is not None else None
    dry_run = bool(body.get("dry_run", False))

    dsn = _get_db_dsn()

    kb = PostgresKnowledgeBase(dsn)
    try:
        summary = await kb.backfill_embeddings(
            kb_id=kb_id,
            batch_size=batch_size,
            limit=limit,
            dry_run=dry_run,
        )
    except RuntimeError as e:
        return JSONResponse({"status_code": 400, "status": "error", "error": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"KB backfill error: {e}\n{traceback.format_exc()}")
        return JSONResponse({"status_code": 500, "status": "error", "error": f"Backfill failed: {str(e)}"}, status_code=500)
    finally:
        await kb.close()

    return JSONResponse({"status_code": 200, "status": "success", "summary": summary})



@router.delete("/v1/kb/document")
async def delete_kb_document(
    org_id: str = Form(None),
    document_id: str = Form(None)
):
    """Delete all KB chunks associated with a specific document_id."""
    if not org_id or not document_id:
        return JSONResponse({"status_code": 400, "status": "error", "error": "org_id and document_id are required"}, status_code=400)

    from mantra.knowledge_base import PostgresKnowledgeBase
    dsn = _get_db_dsn()

    try:
        kb = PostgresKnowledgeBase(dsn)
        deleted_count = await kb.delete_by_document(org_id, document_id)
        await kb.close()
        
        return JSONResponse({
            "status_code": 200,
            "status": "success",
            "message": "Document successfully deleted.",
            "deleted_chunks": deleted_count,
            "document_id": document_id,
            "org_id": org_id
        })
    except Exception as e:
        import traceback
        logger.error(f"KB document delete error: {e}\n{traceback.format_exc()}")
        return JSONResponse({"status_code": 500, "status": "error", "error": f"Failed to delete document: {str(e)}"}, status_code=500)

@router.post("/v1/knowledge/upload")
async def kb_upload(request: Request, kb_id: str, file: UploadFile = File(...)):
    """Upload a file (.pdf, .txt, .md) and index it into the specified KB."""
    # require_auth(request)

    if not file.filename:
        return JSONResponse({"error": "No filename provided"}, status_code=400)

    ext = file.filename.lower().split(".")[-1]
    if ext not in ("pdf", "txt", "md"):
        return JSONResponse({"error": f"Unsupported file type: {ext}"}, status_code=400)

    file_bytes = await file.read()

    try:
        from mantra.knowledge_base import PostgresKnowledgeBase, ingest_file

        dsn = _get_db_dsn()
        kb = PostgresKnowledgeBase(dsn)
        result = await ingest_file(kb, kb_id, file_bytes, file.filename)
        await kb.close()

        return {"status": "success", **result}
    except Exception as e:
        logger.error(f"KB upload failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.post("/v1/knowledge/text")
async def kb_text(request: Request):
    """Ingest a raw text block into the specified KB."""
    # require_auth(request)

    try:
        body = await request.json()
        kb_id = body.get("kb_id")
        content = body.get("content")
        title = body.get("title")

        if not kb_id or not content:
            return JSONResponse(
                {"error": "kb_id and content are required"}, status_code=400
            )

        from mantra.knowledge_base import PostgresKnowledgeBase, ingest_text

        dsn = _get_db_dsn()
        kb = PostgresKnowledgeBase(dsn)
        result = await ingest_text(kb, kb_id, content, title=title, source_type="text")
        await kb.close()

        return {"status": "success", **result}
    except Exception as e:
        logger.error(f"KB text ingest failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.post("/v1/knowledge/url")
async def kb_url(request: Request):
    """Fetch a URL, extract text, and index it into the specified KB."""
    # require_auth(request)

    try:
        body = await request.json()
        kb_id = body.get("kb_id")
        url = body.get("url")

        if not kb_id or not url:
            return JSONResponse(
                {"error": "kb_id and url are required"}, status_code=400
            )

        from mantra.knowledge_base import PostgresKnowledgeBase, ingest_url

        dsn = _get_db_dsn()
        kb = PostgresKnowledgeBase(dsn)
        result = await ingest_url(kb, kb_id, url)
        await kb.close()

        return {"status": "success", **result}
    except Exception as e:
        logger.error(f"KB URL ingest failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.get("/v1/knowledge/list")
async def kb_list(request: Request):
    """List distinct KB IDs available in the database."""
    try:
        from mantra.knowledge_base import PostgresKnowledgeBase

        dsn = _get_db_dsn()
        kb = PostgresKnowledgeBase(dsn)
        pool = await kb._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT kb_id FROM kb_pages ORDER BY kb_id"
            )
            kbs = [r["kb_id"] for r in rows]
        await kb.close()
        return {"status": "success", "kbs": kbs}
    except Exception as e:
        logger.error(f"KB list error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.delete("/v1/knowledge/{page_id}")
async def kb_delete_page(request: Request, page_id: str):
    """Delete a single page from the KB."""
    # require_auth(request)

    try:
        from mantra.knowledge_base import PostgresKnowledgeBase

        dsn = _get_db_dsn()
        kb = PostgresKnowledgeBase(dsn)
        success = await kb.delete_page(page_id)
        await kb.close()

        if success:
            return {"status": "success", "deleted": page_id}
        else:
            return JSONResponse({"error": "Page not found"}, status_code=404)
    except Exception as e:
        logger.error(f"KB delete failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.delete("/v1/knowledge/by-kb/{kb_id}")
async def kb_delete_by_kb(request: Request, kb_id: str):
    """Delete all pages for a KB."""
    # require_auth(request)

    try:
        from mantra.knowledge_base import PostgresKnowledgeBase

        dsn = _get_db_dsn()
        kb = PostgresKnowledgeBase(dsn)
        count = await kb.delete_by_kb(kb_id)
        await kb.close()

        return {"status": "success", "deleted_count": count, "kb_id": kb_id}
    except Exception as e:
        logger.error(f"KB delete by KB failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ──────────────────────────────────────────────
# ORG CONFIGS MANAGEMENT (FOR MANTRAASSIST)
# ──────────────────────────────────────────────


