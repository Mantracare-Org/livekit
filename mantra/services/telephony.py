""" Telephony trunk and SIP forwarding helpers (Plivo, Zadarma, Twilio, VoiceLink). """

from fastapi.responses import JSONResponse
from livekit import api
from mantra.dependencies.database import get_db_connection
from mantra.services import clients as _svc_clients
from mantra.services.clients import AGENT_NAME
from urllib.parse import urlencode
from xml.sax.saxutils import escape
import aiohttp
import base64
import hashlib
import hmac
import json
import os
import traceback

import logging

logger = logging.getLogger("mantra.telephony")

def _normalize_phone_number(number: str) -> str:
    return str(number or "").replace(" ", "").replace("+", "")


async def _resolve_plivo_sip_trunk_id(to_number: str) -> str | None:
    """Resolve the LiveKit inbound SIP trunk for a Plivo dial target."""
    clean_to = _normalize_phone_number(to_number)
    candidate_numbers = [to_number, clean_to]
    if to_number and not to_number.startswith("+") and clean_to:
        candidate_numbers.append(f"+{clean_to}")

    if _svc_clients.redis_client:
        for candidate in candidate_numbers:
            try:
                trunk_id = await _svc_clients.redis_client.get(f"plivo:sip_trunk:{candidate}")
                if trunk_id:
                    logger.info(f"Resolved Plivo SIP trunk from Redis for {to_number}: {trunk_id}")
                    return trunk_id
            except Exception as e:
                logger.warning(f"Redis lookup failed for Plivo SIP trunk {candidate}: {e}")

    try:
        conn = await get_db_connection()
        row = await conn.fetchrow(
            "SELECT sip_trunk_id FROM org_configs WHERE phone_number IN ($1, $2)",
            to_number,
            clean_to,
        )
        await conn.close()
        if row and row["sip_trunk_id"]:
            trunk_id = row["sip_trunk_id"]
            logger.info(f"Resolved Plivo SIP trunk from DB for {to_number}: {trunk_id}")
            if _svc_clients.redis_client:
                await _svc_clients.redis_client.set(f"plivo:sip_trunk:{to_number}", trunk_id, ex=86400 * 30)
            return trunk_id
    except Exception as e:
        logger.warning(f"DB lookup failed for Plivo SIP trunk: {e}")

    if _svc_clients.lk_client:
        try:
            resp = await _svc_clients.lk_client.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
            for item in getattr(resp, "items", []) or []:
                numbers = [str(n).strip() for n in getattr(item, "numbers", []) or []]
                normalized_numbers = {_normalize_phone_number(n) for n in numbers}
                if clean_to in normalized_numbers or (to_number and _normalize_phone_number(to_number) in normalized_numbers):
                    trunk_id = getattr(item, "sip_trunk_id", None)
                    if trunk_id:
                        logger.info(f"Resolved Plivo SIP trunk from LiveKit inbound trunks for {to_number}: {trunk_id}")
                        if _svc_clients.redis_client:
                            await _svc_clients.redis_client.set(f"plivo:sip_trunk:{to_number}", trunk_id, ex=86400 * 30)
                        return trunk_id
        except Exception as e:
            logger.warning(f"LiveKit inbound trunk lookup failed for Plivo: {e}")

    return None


def _build_plivo_xml(sip_trunk_id: str, sip_domain: str, action_url: str, phone_number: str = "") -> str:
    """
    DEPRECATED: Plivo Application XML approach is replaced by Zentrunk SIP trunking.
    Kept for backward compatibility; new setups should use Zentrunk.
    """
    sip_username = escape(phone_number or sip_trunk_id)
    sip_domain = escape(sip_domain)
    action_url = escape(action_url)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Dial action="{action_url}" method="POST" timeout="20">
        <User>sip:{sip_username}@{sip_domain}</User>
    </Dial>
</Response>'''



def _get_sip_domain() -> str:
    configured_domain = os.getenv("LIVEKIT_SIP_DOMAIN") or os.getenv("SIP_DOMAIN")
    if configured_domain:
        return configured_domain

    lk_url = os.getenv("LIVEKIT_URL", "")
    host_lk = lk_url.replace("wss://", "").replace("ws://", "").replace("https://", "").replace("http://", "")
    if "livekit.cloud" in host_lk:
        subdomain = host_lk.split(".")[0]
        if subdomain and subdomain != "www":
            return f"{subdomain}.sip.livekit.cloud"
    return "sip.livekit.cloud"



def _get_zadarma_credentials() -> tuple[str, str]:
    """Resolve Zadarma credentials from either the current or legacy env var names."""
    zadarma_key = os.getenv("ZADARMA_API_KEY") or os.getenv("ZADARMA_KEY")
    zadarma_secret = os.getenv("ZADARMA_API_SECRET") or os.getenv("ZADARMA_SECRET")
    return zadarma_key or "", zadarma_secret or ""



async def _update_zadarma_sip_forwarding(phone_number: str, sip_uri: str) -> dict:
    """
    Updates the SIP URI forwarding in Zadarma using their REST API.
    Handles the HMAC-SHA1 + MD5 signature required by Zadarma.
    """
    zadarma_key, zadarma_secret = _get_zadarma_credentials()
    
    if not zadarma_key or not zadarma_secret:
        raise ValueError("Zadarma API credentials not found in environment variables.")

    # Normalize phone number (Zadarma expects it without the '+')
    number_clean = phone_number.replace("+", "")
    
    # Zadarma expects external SIP URIs without the 'sip:' prefix
    sip_uri_clean = sip_uri.replace("sip:", "")
    
    # Sort parameters alphabetically as required by Zadarma for signature
    params = {
        'number': number_clean,
        'sip_id': sip_uri_clean
    }
    # Create ordered query string
    sorted_params = {k: params[k] for k in sorted(params.keys())}
    query_string = urlencode(sorted_params)
    
    # 1. MD5 of the query string
    md5_hash = hashlib.md5(query_string.encode('utf-8')).hexdigest()
    
    # 2. String to sign: API_METHOD + QUERY_STRING + MD5_HASH
    api_method = "/v1/direct_numbers/set_sip_id/"
    string_to_sign = api_method + query_string + md5_hash
    
    # 3. HMAC-SHA1 signature using Secret Key, hex digest, then Base64 encoded
    mac_hex = hmac.new(
        zadarma_secret.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        hashlib.sha1
    ).hexdigest()
    signature = base64.b64encode(mac_hex.encode('utf-8')).decode('utf-8')
    
    headers = {
        'Authorization': f'{zadarma_key}:{signature}',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    url = f"https://api.zadarma.com{api_method}"
    
    # Send PUT request with query parameters
    async with aiohttp.ClientSession() as session:
        async with session.put(url, data=sorted_params, headers=headers) as resp:
            text = await resp.text()
            if resp.status == 200:
                try:
                    return json.loads(text)
                except Exception:
                    return {"status": "success", "response": text}
            else:
                logger.error(f"Zadarma API error {resp.status}: {text}")
                raise Exception(f"Zadarma API error: {text}")



async def _update_twilio_sip_forwarding(phone_number: str, sip_uri: str) -> dict:
    """
    Updates the SIP URI forwarding in Twilio by updating the Incoming Phone Number's Voice URL.
    Uses Twilio REST API to set the SIP trunk as the voice webhook destination.
    """
    twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    
    if not twilio_account_sid or not twilio_auth_token:
        raise ValueError("Twilio API credentials not found in environment variables.")

    # Normalize phone number (Twilio expects E.164 format with +)
    number_clean = phone_number if phone_number.startswith("+") else f"+{phone_number}"
    
    # Twilio SIP URI format - remove sip: prefix for the Voice URL
    # Twilio expects a webhook URL that returns TwiML, but for SIP trunking
    # we use the SIP Domain approach. The SIP URI is used in the SIP Domain.
    sip_uri_clean = sip_uri.replace("sip:", "")
    
    # Find the incoming phone number resource
    import base64
    auth = base64.b64encode(f"{twilio_account_sid}:{twilio_auth_token}".encode()).decode()
    headers = {
        'Authorization': f'Basic {auth}',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    async with aiohttp.ClientSession() as session:
        # First, find the phone number SID
        url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_account_sid}/IncomingPhoneNumbers.json"
        params = {"PhoneNumber": number_clean}
        async with session.get(url, headers=headers, params=params) as resp:
            text = await resp.text()
            if resp.status != 200:
                logger.error(f"Twilio API error listing numbers {resp.status}: {text}")
                raise Exception(f"Twilio API error: {text}")
            
            data = json.loads(text)
            numbers = data.get("incoming_phone_numbers", [])
            if not numbers:
                raise Exception(f"Phone number {number_clean} not found in Twilio account")
            
            number_sid = numbers[0]["sid"]
        
        # Update the VoiceUrl to point to our SIP domain
        # For SIP trunking, Twilio uses SIP Domain - we need to configure the SIP Domain
        # to route to the LiveKit SIP URI. This is typically done via TwiML app or SIP Domain.
        # Here we'll use the VoiceUrl with a TwiML that forwards to the SIP URI
        voice_url = f"https://{os.getenv('LIVEKIT_URL', '').replace('wss://', '').replace('ws://', '').replace('https://', '').replace('http://', '')}/v1/sip/twilio-webhook"
        
        update_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_account_sid}/IncomingPhoneNumbers/{number_sid}.json"
        update_data = {"VoiceUrl": voice_url, "VoiceMethod": "POST"}
        async with session.post(update_url, headers=headers, data=update_data) as resp:
            text = await resp.text()
            if resp.status == 200:
                try:
                    return json.loads(text)
                except Exception:
                    return {"status": "success", "response": text}
            else:
                logger.error(f"Twilio API error updating number {resp.status}: {text}")
                raise Exception(f"Twilio API error: {text}")



async def _plivo_number_is_linked_to_zentrunk(phone_number: str) -> bool:
    """
    Return True if the Plivo number is already linked to the LiveKit SIP domain's
    Zentrunk inbound trunk, i.e. provider forwarding is genuinely configured.
    """
    plivo_auth_id = os.getenv("PLIVO_AUTH_ID")
    plivo_auth_token = os.getenv("PLIVO_AUTH_TOKEN")
    if not plivo_auth_id or not plivo_auth_token:
        return True  # Cannot verify; keep the caller's existing behaviour.

    number_clean = phone_number.replace("+", "").replace(" ", "")
    import base64
    auth = base64.b64encode(f"{plivo_auth_id}:{plivo_auth_token}".encode()).decode()
    headers = {
        'Authorization': f'Basic {auth}',
        'Content-Type': 'application/json'
    }
    base_url = f"https://api.plivo.com/v1/Account/{plivo_auth_id}"
    trunk_label = f"LiveKit ({_get_sip_domain().split('.')[0]})"
    expected_trunk_name = f"Inbound via {trunk_label}"

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{base_url}/Zentrunk/Trunk/", headers=headers) as resp:
            trunk_list = json.loads(await resp.text()) if resp.status == 200 else {"objects": []}

        trunk_id = None
        for trunk_obj in trunk_list.get("objects", []):
            if trunk_obj.get("name") == expected_trunk_name:
                trunk_id = trunk_obj.get("trunk_id")
                break
        if not trunk_id:
            return False
        async with session.get(f"{base_url}/Number/{number_clean}/", headers=headers) as resp:
            if resp.status != 200:
                return False
            number_obj = json.loads(await resp.text())
        return number_obj.get("app_id") == trunk_id



async def _update_plivo_sip_forwarding(phone_number: str, sip_uri: str) -> dict:
    """
    Configures Plivo Zentrunk SIP trunking for inbound calls.
    Creates a Zentrunk origination URI pointing to LiveKit's SIP domain,
    a Zentrunk inbound trunk, and links the phone number to the trunk.
    This replaces the Plivo Application XML webhook approach with
    direct SIP trunking as documented by LiveKit.
    """
    plivo_auth_id = os.getenv("PLIVO_AUTH_ID")
    plivo_auth_token = os.getenv("PLIVO_AUTH_TOKEN")
    
    if not plivo_auth_id or not plivo_auth_token:
        raise ValueError("Plivo API credentials not found in environment variables.")

    number_clean = phone_number.replace("+", "").replace(" ", "")
    
    import base64
    auth = base64.b64encode(f"{plivo_auth_id}:{plivo_auth_token}".encode()).decode()
    headers = {
        'Authorization': f'Basic {auth}',
        'Content-Type': 'application/json'
    }
    
    sip_domain = _get_sip_domain()
    origination_host = f"{sip_domain}:5061;transport=tls"
    base_url = f"https://api.plivo.com/v1/Account/{plivo_auth_id}"
    trunk_label = f"LiveKit ({sip_domain.split('.')[0]})"
    
    async with aiohttp.ClientSession() as session:
        # 1. Create or find existing Zentrunk origination URI for this SIP domain.
        #    The URI name is deterministic per SIP domain, so match by name as well
        #    as by uri field — a URI may exist under this name even when the uri
        #    field doesn't contain our current sip_domain string.
        async with session.get(f"{base_url}/Zentrunk/URI/", headers=headers) as resp:
            uri_list = json.loads(await resp.text()) if resp.status == 200 else {"objects": []}

        uri_uuid = None
        for uri_obj in uri_list.get("objects", []):
            if sip_domain in uri_obj.get("uri", "") and "transport=tls" in uri_obj.get("uri", ""):
                uri_uuid = uri_obj.get("uri_uuid")
                logger.info(f"Found existing Zentrunk origination URI {uri_uuid}: {uri_obj.get('uri')}")
                break

        if not uri_uuid:
            for uri_obj in uri_list.get("objects", []):
                if uri_obj.get("name") == trunk_label:
                    uri_uuid = uri_obj.get("uri_uuid")
                    logger.info(f"Found existing Zentrunk origination URI {uri_uuid} by name: {trunk_label}")
                    break

        if not uri_uuid:
            uri_data = {"uri": origination_host, "name": trunk_label}
            async with session.post(f"{base_url}/Zentrunk/URI/", headers=headers, json=uri_data) as resp:
                result_text = await resp.text()
                result = json.loads(result_text) if result_text else {}
                if resp.status in (200, 201, 202):
                    uri_uuid = result.get("uri_uuid")
                    logger.info(f"Created Zentrunk origination URI {uri_uuid}: {origination_host}")
                else:
                    raise Exception(f"Failed to create Zentrunk URI: {result}")

        # 2. Create or find existing Zentrunk inbound trunk using this URI.
        #    The trunk name is deterministic per SIP domain, so a trunk created for a
        #    previous number already exists under this name. Match by name as well as by
        #    primary_uri_uuid so we reuse it instead of hitting Plivo's
        #    "A trunk with the same name ... already exists" error.
        expected_trunk_name = f"Inbound via {trunk_label}"
        async with session.get(f"{base_url}/Zentrunk/Trunk/", headers=headers) as resp:
            trunk_list = json.loads(await resp.text()) if resp.status == 200 else {"objects": []}

        trunk_id = None
        for trunk_obj in trunk_list.get("objects", []):
            if trunk_obj.get("primary_uri_uuid") == uri_uuid:
                trunk_id = trunk_obj.get("trunk_id")
                logger.info(f"Found existing Zentrunk inbound trunk {trunk_id}: {trunk_obj.get('name')}")
                break

        if not trunk_id:
            for trunk_obj in trunk_list.get("objects", []):
                if trunk_obj.get("name") == expected_trunk_name:
                    trunk_id = trunk_obj.get("trunk_id")
                    logger.info(f"Found existing Zentrunk inbound trunk {trunk_id} by name: {expected_trunk_name}")
                    existing_uri = trunk_obj.get("primary_uri_uuid")
                    if existing_uri and existing_uri != uri_uuid:
                        # Trunk points to a different (possibly stale) URI; repoint it at ours.
                        try:
                            async with session.post(
                                f"{base_url}/Zentrunk/Trunk/{trunk_id}/",
                                headers=headers,
                                json={"primary_uri_uuid": uri_uuid},
                            ) as resp:
                                if resp.status in (200, 202):
                                    logger.info(f"Repointed Zentrunk inbound trunk {trunk_id} to URI {uri_uuid}")
                                else:
                                    logger.warning(f"Could not repoint Zentrunk trunk {trunk_id}: {await resp.text()}")
                        except Exception as e:
                            logger.warning(f"Error repointing Zentrunk trunk {trunk_id}: {e}")
                    elif existing_uri:
                        uri_uuid = existing_uri
                    break

        if not trunk_id:
            trunk_data = {
                "name": expected_trunk_name,
                "trunk_direction": "inbound",
                "primary_uri_uuid": uri_uuid
            }
            async with session.post(f"{base_url}/Zentrunk/Trunk/", headers=headers, json=trunk_data) as resp:
                result_text = await resp.text()
                result = json.loads(result_text) if result_text else {}
                if resp.status in (200, 201, 202):
                    trunk_id = result.get("trunk_id")
                    logger.info(f"Created Zentrunk inbound trunk {trunk_id}")
                else:
                    raise Exception(f"Failed to create Zentrunk trunk: {result}")
        
        # 3. Verify the number exists in Plivo
        async with session.get(f"{base_url}/Number/{number_clean}/", headers=headers) as resp:
            if resp.status != 200:
                raise Exception(f"Phone number +{number_clean} not found in Plivo account")
        
        # 4. Link the phone number to the Zentrunk trunk (replaces any existing Application)
        update_data = {"app_id": trunk_id}
        async with session.post(f"{base_url}/Number/{number_clean}/", headers=headers, json=update_data) as resp:
            text = await resp.text()
            if resp.status in (200, 202):
                try:
                    result = json.loads(text)
                except Exception:
                    result = {"status": "success", "response": text}
                result["zentrunk_trunk_id"] = trunk_id
                result["zentrunk_uri_uuid"] = uri_uuid
                result["zentrunk_sip_domain"] = sip_domain
                logger.info(f"Linked number +{number_clean} to Zentrunk trunk {trunk_id}")
                return result
            else:
                logger.error(f"Plivo API error linking number to Zentrunk trunk {resp.status}: {text}")
                raise Exception(f"Plivo API error: {text}")



async def _update_voicelink_sip_forwarding(phone_number: str, sip_uri: str) -> dict:
    """
    VoiceLink is a LiveKit-native SIP provider — the LiveKit inbound trunk + dispatch rule
    are already configured. The user must link this SIP URI in their VoiceLink dashboard.
    """
    logger.info(f"VoiceLink inbound SIP configured for {phone_number} -> {sip_uri}")
    return {
        "status": "success",
        "provider": "voice_link",
        "phone_number": phone_number,
        "sip_uri": sip_uri,
    }



async def _update_provider_sip_forwarding(provider: str, phone_number: str, sip_uri: str) -> dict:
    """
    Routes to the appropriate provider-specific SIP forwarding function.
    Supported providers: zadarma, twilio, plivo, voice_link
    """
    provider = provider.lower().strip()
    
    if provider == "zadarma":
        return await _update_zadarma_sip_forwarding(phone_number, sip_uri)
    elif provider == "twilio":
        return await _update_twilio_sip_forwarding(phone_number, sip_uri)
    elif provider == "plivo":
        return await _update_plivo_sip_forwarding(phone_number, sip_uri)
    elif provider in ("voicelink", "voice_link"):
        return await _update_voicelink_sip_forwarding(phone_number, sip_uri)
    else:
        raise ValueError(f"Unsupported provider: {provider}. Supported providers: zadarma, twilio, plivo, voice_link")



async def _setup_inbound_sip_process(payload: dict | None) -> JSONResponse:
    if payload is None:
        return JSONResponse({"status_code": 400, "status": "error", "error": "Invalid JSON"}, status_code=400)
    
    number = payload.get("number")
    if not number:
        return JSONResponse({"status_code": 400, "status": "error", "error": "number is required"}, status_code=400)

    raw_provider = payload.get("provider")
    if not isinstance(raw_provider, str) or not raw_provider.strip():
        return JSONResponse({
            "status_code": 400,
            "status": "error",
            "error": "provider is required",
        }, status_code=400)

    provider = raw_provider.lower().strip()
    name = payload.get("name", f"{provider} {number}")
    prompt = payload.get("prompt", "You are a helpful voice assistant.")
    voice = payload.get("voice", "arushi")
    model = payload.get("model", "deepseek")
    
    # New fields for org configuration
    org_id = payload.get("org_id")
    if not org_id:
        return JSONResponse({"status_code": 400, "status": "error", "error": "org_id is required"}, status_code=400)
    kb_tags = payload.get("kb_tags", [])
    transfer_numbers = payload.get("transfer_numbers", {})
    client_name = payload.get("client_name", "User")
    process_id = payload.get("process_id")

    supported_providers = {"zadarma", "twilio", "plivo", "voice_link", "voicelink"}
    if provider not in supported_providers:
        return JSONResponse({
            "status_code": 400,
            "status": "error",
            "error": "unsupported_provider",
            "message": f"Unsupported provider: {provider}. Supported providers: zadarma, twilio, plivo, voice_link",
        }, status_code=400)
    
    logger.info(f"Starting end-to-end SIP setup for number: {number}, org_id: {org_id}, provider: {provider}")
    
    try:
        # 1. Check for existing inbound trunk with this number (skip if force_new=true)
        clean_number = number.replace("+", "")
        existing_trunk_id = None
        existing_rule_id = None
        created_trunk = False
        created_rule = False
        
        force_new = payload.get("force_new", False)
        
        if not force_new:
            # Run trunk listing and rule listing in parallel
            async def _find_existing_trunk():
                try:
                    response = await _svc_clients.lk_client.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
                    for item in response.items:
                        trunk_numbers = list(item.numbers)
                        if number in trunk_numbers or clean_number in trunk_numbers:
                            return item.sip_trunk_id
                except Exception as e:
                    logger.warning(f"Could not list existing trunks: {e}")
                return None
            
            async def _find_existing_rule(trunk_id):
                if not trunk_id:
                    return None
                try:
                    rule_response = await _svc_clients.lk_client.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())
                    for item in rule_response.items:
                        if trunk_id in list(item.trunk_ids):
                            return item.sip_dispatch_rule_id
                except Exception as e:
                    logger.warning(f"Could not list dispatch rules: {e}")
                return None
            
            # First find trunk, then find rule (rule depends on trunk)
            existing_trunk_id = await _find_existing_trunk()
            if existing_trunk_id:
                logger.info(f"Found existing inbound trunk {existing_trunk_id} for number {number}")
                existing_rule_id = await _find_existing_rule(existing_trunk_id)
                if existing_rule_id:
                    logger.info(f"Found existing dispatch rule {existing_rule_id} for trunk {existing_trunk_id}")
        else:
            logger.info(f"force_new=true: Skipping existing trunk/rule checks for {number}")
        
        # If number already fully configured, return clear error to MantraAssist.
        # Provider forwarding is the last and most fragile step of setup; if it failed
        # previously we must NOT report "already configured" — instead fall through and
        # complete the setup idempotently using the existing trunk/rule.
        if existing_trunk_id and existing_rule_id:
            already_configured = True
            if provider == "plivo":
                try:
                    already_configured = await _plivo_number_is_linked_to_zentrunk(number)
                except Exception as e:
                    logger.warning(f"Could not verify Plivo forwarding for {number}: {e}")
            if already_configured:
                return JSONResponse({
                    "status_code": 409,
                    "status": "error",
                    "error": "number_already_configured",
                    "message": f"Phone number {number} is already configured",
                    "existing_trunk_id": existing_trunk_id,
                    "existing_dispatch_rule_id": existing_rule_id
                }, status_code=409)
            logger.info(f"Number {number} partially configured (provider forwarding missing); completing setup with existing trunk/rule")
        
        # 2. Create Inbound Trunk (or reuse existing)
        if existing_trunk_id:
            trunk_id = existing_trunk_id
            logger.info(f"Reusing existing LiveKit SIP Inbound Trunk: {trunk_id}")
        else:
            trunk = await _svc_clients.lk_client.sip.create_sip_inbound_trunk(
                api.CreateSIPInboundTrunkRequest(
                    trunk=api.SIPInboundTrunkInfo(
                        name=name,
                        numbers=[number, clean_number],
                        allowed_addresses=["0.0.0.0/0"],
                    )
                )
            )
            trunk_id = trunk.sip_trunk_id
            created_trunk = True
            logger.info(f"Created LiveKit SIP Inbound Trunk: {trunk_id}")
        
        # Store SIP trunk mapping in Redis for webhooks lookup
        # This allows the provider webhooks (Twilio/Plivo) to find the correct SIP trunk ID for incoming calls
        if _svc_clients.redis_client and provider in ["plivo", "twilio", "voice_link", "voicelink"]:
            try:
                # Store with both +prefix and without for flexible lookup
                await _svc_clients.redis_client.set(f"{provider}:sip_trunk:{number}", trunk_id, ex=86400*30)  # 30 days TTL
                await _svc_clients.redis_client.set(f"{provider}:sip_trunk:{clean_number}", trunk_id, ex=86400*30)
                logger.info(f"Stored {provider} SIP trunk mapping: {number} -> {trunk_id}")
            except Exception as e:
                logger.warning(f"Failed to store {provider} SIP trunk mapping in Redis: {e}")
        
        # 3. Create Dispatch Rule (or reuse if trunk existed but no rule)
        if existing_rule_id:
            rule_id = existing_rule_id
            logger.info(f"Reusing existing LiveKit SIP Dispatch Rule: {rule_id}")
        else:
            # We inject direction=inbound and the given prompt/voice into metadata
            room_prefix = f"inbound_{trunk_id[-6:]}"
            metadata_dict = {
                "direction": "inbound",
                "prompt": prompt,
                "voice": voice,
                "model": model,
                "phone_number": number,
                "provider": provider
            }
            
            rule_req = api.CreateSIPDispatchRuleRequest(
                name=f"Rule for {name}",
                metadata=json.dumps(metadata_dict),
                rule=api.SIPDispatchRule(
                    dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                        room_prefix=room_prefix
                    )
                ),
                room_config=api.RoomConfiguration(
                    empty_timeout=300,
                    departure_timeout=60,
                    agents=[
                        api.RoomAgentDispatch(
                            agent_name=AGENT_NAME,
                            metadata=json.dumps(metadata_dict)
                        )
                    ]
                ),
                trunk_ids=[trunk_id]
            )
            
            rule = await _svc_clients.lk_client.sip.create_sip_dispatch_rule(rule_req)
            rule_id = rule.sip_dispatch_rule_id
            created_rule = True
            logger.info(f"Created LiveKit SIP Dispatch Rule: {rule_id}")
        
        # 4. Generate SIP URI
        sip_domain = _get_sip_domain()
        # Use the clean_number so that provider sends the INVITE with To: <clean_number>@<sip_domain>
        # This allows LiveKit to correctly match the inbound SIP trunk which has this number in its numbers array.
        sip_uri = f"sip:{clean_number}@{sip_domain}"
        
        # 5. Update provider SIP forwarding
        logger.info(f"Updating {provider} SIP ID for {number} to {sip_uri}")
        try:
            provider_response = await _update_provider_sip_forwarding(provider, number, sip_uri)
        except Exception as e:
            # If provider fails (e.g., number not in provider account), return clear error.
            # org_configs has not been saved yet, so a retry will complete the setup
            # instead of being rejected as "already configured".
            if created_rule:
                try:
                    await _svc_clients.lk_client.sip.delete_dispatch_rule(
                        api.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule_id)
                    )
                    logger.info(f"Rolled back SIP dispatch rule {rule_id}")
                except Exception as cleanup_error:
                    logger.error(f"Failed to roll back SIP dispatch rule {rule_id}: {cleanup_error}")
            if created_trunk:
                try:
                    await _svc_clients.lk_client.sip.delete_trunk(
                        api.DeleteSIPTrunkRequest(sip_trunk_id=trunk_id)
                    )
                    logger.info(f"Rolled back SIP inbound trunk {trunk_id}")
                except Exception as cleanup_error:
                    logger.error(f"Failed to roll back SIP inbound trunk {trunk_id}: {cleanup_error}")
            return JSONResponse({
                "status_code": 400,
                "status": "error",
                "error": f"{provider}_configuration_failed",
                "message": f"Failed to configure {provider} for {number}: {str(e)}. Ensure the number exists in your {provider} account.",
                "sip_trunk_id": trunk_id,
                "sip_dispatch_rule_id": rule_id,
                "sip_uri": sip_uri
            }, status_code=400)

        # 5.5 Create or update org_configs mapping (only after provider forwarding has
        # succeeded, so the DB row reflects an actually-configured number)
        try:
            conn = await get_db_connection()
            org_config_id = await conn.fetchval("""
                INSERT INTO org_configs (
                    org_id, phone_number, name, prompt, voice, model, 
                    kb_tags, transfer_numbers, client_name, process_id, 
                    sip_trunk_id, dispatch_rule_id
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, 
                    $7, $8, $9, $10, 
                    $11, $12
                )
                ON CONFLICT (phone_number) DO UPDATE SET
                    org_id = EXCLUDED.org_id,
                    name = EXCLUDED.name,
                    prompt = EXCLUDED.prompt,
                    voice = EXCLUDED.voice,
                    model = EXCLUDED.model,
                    kb_tags = EXCLUDED.kb_tags,
                    transfer_numbers = EXCLUDED.transfer_numbers,
                    client_name = EXCLUDED.client_name,
                    process_id = EXCLUDED.process_id,
                    sip_trunk_id = EXCLUDED.sip_trunk_id,
                    dispatch_rule_id = EXCLUDED.dispatch_rule_id,
                    is_active = true,
                    updated_at = NOW()
                RETURNING id;
            """, 
            str(org_id), clean_number, name, prompt, voice, model, 
            kb_tags, json.dumps(transfer_numbers), client_name, process_id, 
            trunk_id, rule_id)
            await conn.close()
            logger.info(f"Successfully saved org_config for {clean_number} with ID: {org_config_id}")
        except Exception as e:
            logger.error(f"Failed to save org_config to database: {e}")
            # We continue even if this fails, to not break existing functionality completely,
            # though the agent might fall back to MantraAssist.
            org_config_id = None

        return JSONResponse({
            "status_code": 200,
            "status": "success",
            "name": name,
            "org_id": org_id,
            "org_config_id": str(org_config_id) if org_config_id else None,
            "sip_trunk_id": trunk_id,
            "sip_dispatch_rule_id": rule_id,
            "sip_uri": sip_uri,
            "provider": provider,
            "provider_response": provider_response
        })
            
    except Exception as e:
        logger.error(f"Error during SIP setup: {str(e)}")
        logger.error(traceback.format_exc())
        return JSONResponse({"error": str(e)}, status_code=500)




async def _create_sip_outbound_trunk(
    name: str,
    address: str,
    numbers: list,
    auth_username: str,
    auth_password: str,
    client: api.LiveKitAPI = None,
    destination_country: str = None,
):
    if not all([name, address, numbers, auth_username, auth_password]):
        missing = [
            f
            for f, v in [
                ("name", name),
                ("address", address),
                ("numbers", numbers),
                ("auth_username", auth_username),
                ("auth_password", auth_password),
            ]
            if not v
        ]
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    if isinstance(numbers, str):
        numbers = [n.strip() for n in numbers.split(",") if n.strip()]
    elif not isinstance(numbers, list):
        numbers = [str(numbers)]

    svc = (client or _svc_clients.lk_client).sip
    try:
        logger.info(f"Creating SIP outbound trunk: {name} at {address}")
        trunk_request = api.CreateSIPOutboundTrunkRequest(
            trunk=api.SIPOutboundTrunkInfo(
                name=name,
                address=address,
                numbers=numbers,
                auth_username=auth_username,
                auth_password=auth_password,
                destination_country=destination_country,
            )
        )
        trunk = await svc.create_outbound_trunk(trunk_request)
        logger.info(
            f"Successfully created SIP outbound trunk: {trunk.sip_trunk_id} ({name})"
        )
        return trunk
    except Exception as e:
        logger.error(f"LiveKit API error creating SIP trunk: {e}")
        raise

async def _get_provider_from_trunk(trunk_id: str) -> str | None:
    if _svc_clients.redis_client:
        stored = await _svc_clients.redis_client.get(f"trunk:provider:{trunk_id}")
        if stored:
            return stored

    provider = None

    try:
        response = await _svc_clients.lk_client.sip.list_outbound_trunk(
            api.ListSIPOutboundTrunkRequest(trunk_ids=[trunk_id])
        )
        if response.items:
            address = (response.items[0].address or "").lower()
            logger.info(f"Trunk lookup: {trunk_id} address={address}")
            if "twilio" in address:
                provider = "twilio"
            elif "plivo" in address:
                provider = "plivo"
            elif "zadarma" in address:
                provider = "zadarma"
            else:
                logger.warning(f"Trunk {trunk_id} address '{address}' does not match known providers")
    except Exception as e:
        logger.warning(f"Cannot list trunk {trunk_id} via lk_client: {e}")

    if provider is None and _svc_clients.voicelink_client:
        try:
            vl_resp = await _svc_clients.voicelink_client.sip.list_outbound_trunk(
                api.ListSIPOutboundTrunkRequest(trunk_ids=[trunk_id])
            )
            if vl_resp.items:
                provider = "voice_link"
        except Exception as e:
            logger.warning(f"Cannot list trunk {trunk_id} via voicelink_client: {e}")

    if _svc_clients.redis_client and provider:
        await _svc_clients.redis_client.set(f"trunk:provider:{trunk_id}", provider, ex=86400 * 30)
    return provider

