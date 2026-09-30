""" Org config API routes for the UI server. """

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from mantra.dependencies.database import get_db_connection
import json

import logging

logger = logging.getLogger("mantra.org_configs")
router = APIRouter()

@router.get("/v1/org-configs")
async def list_org_configs(request: Request):
    """List all org configs, optionally filtered by org_id."""
    org_id = request.query_params.get("org_id")
    try:
        conn = await get_db_connection()
        if org_id:
            rows = await conn.fetch("SELECT * FROM org_configs WHERE org_id = $1 ORDER BY created_at DESC", org_id)
        else:
            rows = await conn.fetch("SELECT * FROM org_configs ORDER BY created_at DESC")
        await conn.close()
        
        results = [dict(row) for row in rows]
        # Convert datetime objects and lists/dicts to JSON serializable formats
        for r in results:
            if r.get('created_at'): r['created_at'] = r['created_at'].isoformat()
            if r.get('updated_at'): r['updated_at'] = r['updated_at'].isoformat()
            if r.get('id'): r['id'] = str(r['id'])
            if r.get('transfer_numbers') and isinstance(r['transfer_numbers'], str):
                try: r['transfer_numbers'] = json.loads(r['transfer_numbers'])
                except: pass
        
        return {"status": "success", "count": len(results), "org_configs": results}
    except Exception as e:
        logger.error(f"Failed to list org configs: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.get("/v1/org-configs/{phone_number}")
async def get_org_config(phone_number: str):
    """Get a specific org config by phone number."""
    try:
        clean_number = phone_number.replace("+", "")
        conn = await get_db_connection()
        # Check with and without plus
        row = await conn.fetchrow(
            "SELECT * FROM org_configs WHERE phone_number IN ($1, $2)", 
            phone_number, clean_number
        )
        await conn.close()
        
        if not row:
            return JSONResponse({"status_code": 404, "status": "error", "error": "Not found"}, status_code=404)
            
        result = dict(row)
        if result.get('created_at'): result['created_at'] = result['created_at'].isoformat()
        if result.get('updated_at'): result['updated_at'] = result['updated_at'].isoformat()
        if result.get('id'): result['id'] = str(result['id'])
        if result.get('transfer_numbers') and isinstance(result['transfer_numbers'], str):
            try: result['transfer_numbers'] = json.loads(result['transfer_numbers'])
            except: pass
            
        return {"status": "success", "org_config": result}
    except Exception as e:
        logger.error(f"Failed to get org config: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.put("/v1/org-configs/{phone_number}")
async def update_org_config(phone_number: str, request: Request):
    """Update fields on an existing org config."""
    payload = await request.json()
    if not payload:
        return JSONResponse({"error": "No payload provided"}, status_code=400)
        
    try:
        clean_number = phone_number.replace("+", "")
        conn = await get_db_connection()
        
        # Check if exists
        row = await conn.fetchrow(
            "SELECT id FROM org_configs WHERE phone_number IN ($1, $2)", 
            phone_number, clean_number
        )
        if not row:
            await conn.close()
            return JSONResponse({"status_code": 404, "status": "error", "error": "Not found"}, status_code=404)
            
        # Build dynamic update query
        update_fields = []
        values = [row['id']]
        idx = 2
        
        allowed_fields = [
            "name", "prompt", "voice", "model", "kb_tags", 
            "transfer_numbers", "client_name", "process_id", "is_active"
        ]
        
        for field in allowed_fields:
            if field in payload:
                val = payload[field]
                if field == "transfer_numbers" and isinstance(val, dict):
                    val = json.dumps(val)
                
                update_fields.append(f"{field} = ${idx}")
                values.append(val)
                idx += 1
                
        if not update_fields:
            await conn.close()
            return {"status": "success", "message": "No valid fields to update"}
            
        update_fields.append(f"updated_at = NOW()")
        
        query = f"UPDATE org_configs SET {', '.join(update_fields)} WHERE id = $1 RETURNING *"
        updated_row = await conn.fetchrow(query, *values)
        await conn.close()
        
        result = dict(updated_row)
        if result.get('created_at'): result['created_at'] = result['created_at'].isoformat()
        if result.get('updated_at'): result['updated_at'] = result['updated_at'].isoformat()
        if result.get('id'): result['id'] = str(result['id'])
        if result.get('transfer_numbers') and isinstance(result['transfer_numbers'], str):
            try: result['transfer_numbers'] = json.loads(result['transfer_numbers'])
            except: pass
            
        return {"status": "success", "org_config": result}
    except Exception as e:
        logger.error(f"Failed to update org config: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)



@router.delete("/v1/org-configs/{phone_number}")
async def delete_org_config(phone_number: str):
    """Soft delete an org config by setting is_active = false."""
    try:
        clean_number = phone_number.replace("+", "")
        conn = await get_db_connection()
        
        row = await conn.fetchrow(
            "UPDATE org_configs SET is_active = false, updated_at = NOW() WHERE phone_number IN ($1, $2) RETURNING id", 
            phone_number, clean_number
        )
        await conn.close()
        
        if not row:
            return JSONResponse({"status_code": 404, "status": "error", "error": "Not found"}, status_code=404)
            
        return {"status": "success", "message": f"Org config for {phone_number} deactivated"}
    except Exception as e:
        logger.error(f"Failed to delete org config: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)





