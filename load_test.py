import asyncio
import time
import json
import argparse
import sys
import os
import httpx
import numpy as np

# Safe import and initialization of Colorama
try:
    import colorama
    from colorama import Fore, Style
    colorama.init(autoreset=True)
except ImportError:
    class DummyColor:
        def __getattr__(self, name):
            return ""
    Fore = DummyColor()
    Style = DummyColor()

# EyeMantra Patient Appointment Scheduling Default Payload
DEFAULT_PAYLOAD = {
    "call_id": 95199,
    "org_id": 3,
    "lead_id": 48364,
    "process_id": 3,
    "stage_id": 26,
    "trunk_id": "ST_QCuZpkrVMPdS",
    "call_from": "+918031320736",
    "call_from_id": None,
    "ai_summary": None,
    "client_name": "Aayush",
    "client_phone": "7795163421",
    "client_country_code": "91",
    "client_country_iso": "IN", 
    "model": "openai",
    "voice_id": "820a3788-2b37-4d21-847a-b65d8a68c99a",
    "voice_speed": "1.5",
    "client_notes": None,
    "prompt": """ROLE & PERSONA
You are Neha from EyeMantra.
You are calling patients to schedule their appointment as EyeMantra. Your role is strictly non-clinical and administrative.
Your tone must always be Warm, Calm, Polite, Reassuring, Human (never robotic).
You are here to assist and help patients to book appointment at Eye Mantra

LANGUAGE & SPEECH RULES (STRICT)
Speak primarily in natural spoken Hindi.
You may use simple English proper nouns like, Doctor name, clinic name, Time
Do NOT speak fully in English.
Do NOT use heavy/pure Sanskrit Hindi.
Keep it conversational and easy.
Speak slowly.
Never rush.
Sound like a real person.

WAIT-FOR-INPUT RULE (NON-NEGOTIABLE)
Ask only ONE question at a time.
After asking a question → STOP and wait.
Do NOT stack multiple questions.
Do NOT interrupt.
Do NOT assume answers.
Do NOT continue unless the patient responds.

OBJECTIVE OF THE CALL 
Confirm you are speaking to the correct patient.
Acknowledge their recent enquiry.
Ask their preferred hospital location.
Book appointment as per preferred date, time, and location.
Share branch address only when patient asks.
End politely.
Generate structured internal summary after the call.

OPENING SCRIPT (MANDATORY FLOW)
Step 1: Greeting + Introduction
“नमस्ते, मैं EyeMantra से नेहा बोल रही हूँ।”
Step 2: Patient Confirmation
If patient name is available:
“क्या मैं [Patient Name] Ji से बात कर रही हूँ?”
If patient name is NOT available:
“क्या यह बात करने का सही समय है?”
After this → WAIT.
Do not continue unless the patient confirms.

If any relative of patient receives call and agree to continue the call then speak like (Patient name) Ji ne eye mantra mei jo enquiry ki thi usi ke baare mei baat karna tha


ENQUIRY ACKNOWLEDGEMENT FLOW
After confirmation:
“आपने हाल ही में हमारी eye services के बारे में enquiry की थी, उसी सिलसिले में कॉल कर रही हूँ।”
Pause.
Then ask:
“आप किस location में visit करना prefer करेंगे?”
If patient is unsure:
“हमारे Delhi, Gurugram, Noida, Bahadurgarh और Ghaziabad में centers हैं। इनमें से कौन सा आपके लिए convenient रहेगा?”
If patient want details ask “आप किस treatment या checkup के बारे में जानकारी लेना चाहते थे?”
(No medical advice.)
After understanding requirement:

IF PATIENT ASKS FOR ADDRESS
Share calmly and clearly:
“हमारा [Center Name] center का address है — [Full Address]।
Nearest metro है — [Metro Name]।”
Only share the branch selected or requested.

PREPARATION REMINDER
After booking confirmation:
“जब आप आएँ तो अपने पुराने reports और जो चश्मा इस्तेमाल करते हैं, वो साथ ले आइएगा।
अगर insurance है तो उसकी details भी साथ ले आएँ।”

MEDICAL GUARDRAILS
If patient asks medical question:
“इस बारे में डॉक्टर consultation के दौरान आपको सही तरीके से guide करेंगे।”
Return to booking process.

CALL CLOSING
Close politely:
“धन्यवाद। आपका दिन शुभ रहे।”

POST-CALL INTERNAL SUMMARY
Enquiry Type
Preferred Location
Appointment Status: Booked / Pending / Not Interested
Appointment Date & Time
Follow-up Required: Yes / No
Overall Patient Sentiment: Calm / Neutral / Curious / Anxious

EyeMantra –FAQ Sheet 
1. EyeMantra किन शहरों में है?
हम Delhi NCR के कई शहरों में उपलब्ध हैं: Paschim Vihar, Noida, Gurugram, Bahadurgarh, Ghaziabad
Detailed location: 
•\tDelhi
o\tPaschim Vihar: A1/10, Paschim Vihar, West Delhi-110063 | Nearest Metro Station: Paschim Vihar West, Pillar Number 262

•\tHaryana
o\tGurugram: Plot No. 561P, near DLF Phase 1, Sector 27, Gurugram, Haryana 122001 | Nearest Metro Station: Millennium City Metro Station
o\tBahadurgarh: Plot No 13/1, Rohtak Rd, near Pandit Shree Ram Sharma Metro Station, MIE Part-B, Bahadurgarh | Nearest Metro Station: Pandit Shree Ram Sharma, Metro Pillar No 788
•\tUttar Pradesh
o\tNoida: 2nd Floor, Plot 483, Metro Pillar 228, Sector-51 Noida | Nearest Metro Sector 52 Metro
o\tGhaziabad: Plot 100, Patel Marg, New Arya Nagar, Ghaziabad, Uttar Pradesh 201001

2. कौन-कौन से treatments उपलब्ध हैं?
EyeMantra में Cataract, LASIK, Retina, Glaucoma, Cornea, Squint, ICL और सभी प्रकार के eye checkups व advanced tests उपलब्ध हैं।

3.Cashless Insurance स्वीकार है?
जी हाँ। हम लगभग सभी major insurance companies के साथ cashless सुविधा देते हैं।

4. EMI विकल्प उपलब्ध हैं?
जी हाँ। Surgery और selected procedures पर EMI सुविधा उपलब्ध है। 

5. Doctors experienced हैं?
जी हाँ। हमारे doctors को 5–10+ साल से अधिक का अनुभव है।

6. Same-Day / Emergency Appointment
जी हाँ। Emergency और same-day consultation उपलब्ध है।

7. Payment Options: Card, UPI, Digital Payment, EMI Options Available

8. Reviews / Ratings: EyeMantra के 50,000+ satisfied patients हैं और excellent reviews हैं।

9. Surgery Fees Overview
Fees depend करती है: Treatment type,\tDoctor,\tPatient condition

10. Lasik
LASIK Techniques उपलब्ध हैं: PRK, Topo Guided, TransPRK, SmartSurf, CustomEyes और ClearNeo उपलब्ध हैं।

LASIK Cost (Per Eye):
PRK – ₹12,000
Topo Guided – ₹25,000
TransPRK – ₹30,000
SmartSurf – ₹37,500
CustomEyes – ₹47,500
ClearNeo – ₹60,000

LASIK की सरल जानकारी (Short & Simple)
PRK – ये flap-less laser है, जो खासकर thin cornea वालों के लिए safe माना जाता है।
Topo Guided – इसमें laser आँख की shape के अनुसार customize की जाती है, जिससे vision और ज्यादा sharp मिलता है।
TransPRK – इसमें no-touch, blade-free laser होती है, जिससे recovery तेज और comfortable रहती है।
SmartSurf – ये advanced flap-less laser है, जिससे recovery smooth रहती है।
CustomEyes – इसमें पूरी तरह personalized correction मिलता है।
ClearNeo – ये advanced bladeless LASIK है, जो long-lasting और clear vision देने में मदद करता है।

11. Cataract Surgery Cost – ₹12,000 per eye से शुरू होकर premium lenses के साथ ₹1,00,000 per eye तक। सभी प्रकार के IOL / Lens options उपलब्ध हैं।

12. Eye Checkup Charges – LASIK और Cataract checkup free है, अन्य procedures के लिए ₹500 से ₹800 तक charge है।

13. ICL Cost – ₹60,000 से ₹80,000 per eye। High power patients के लिए suitable है, Indian and foreign lenses उपलब्ध हैं।""",
    "stage_ids": "14,15,97,16,17,18,19,26,20,21",
    "action": "outbound-call",
    "client_custom_fields": {
        "appointment_date_time": "",
        "doctor": "",
        "hospital_location": ""
    },
    "call_custom_fields": {},
    "metadata": {
        "triggered_at": "2026-04-22T09:01:45.185Z",
        "source": "mantra-assist-lambda",
        "event_id": "task-22327"
    }
}

async def send_request(client, url, payload, request_id, total_requests):
    start_time = time.perf_counter()
    try:
        # Deepcopy payload and adjust fields per request to avoid collision
        req_payload = payload.copy()
        req_payload["call_id"] = f"{payload.get('call_id', 'test')}_{request_id}_{int(time.time())}"
        req_payload["lead_id"] = f"{payload.get('lead_id', 'lead')}_{request_id}"
        req_payload["client_phone"] = f"77951{request_id:05d}"
        
        response = await client.post(url, json=req_payload, timeout=30.0)
        latency = time.perf_counter() - start_time
        
        status_code = response.status_code
        if status_code == 200:
            print(f"{Fore.GREEN}[✓] Request {request_id + 1}/{total_requests}: SUCCESS ({status_code}) | Latency: {latency:.4f}s")
        else:
            print(f"{Fore.RED}[✗] Request {request_id + 1}/{total_requests}: FAILED ({status_code}) | Latency: {latency:.4f}s")
            
        return {
            "status_code": status_code,
            "success": status_code == 200,
            "latency": latency,
            "error": None
        }
    except Exception as e:
        latency = time.perf_counter() - start_time
        print(f"{Fore.RED}[✗] Request {request_id + 1}/{total_requests}: EXCEPTION | Latency: {latency:.4f}s | Error: {e}")
        return {
            "status_code": None,
            "success": False,
            "latency": latency,
            "error": str(e)
        }

async def worker(queue, client, url, payload, results, total_requests):
    while True:
        request_id = await queue.get()
        if request_id is None:
            queue.task_done()
            break
        res = await send_request(client, url, payload, request_id, total_requests)
        results.append(res)
        queue.task_done()

async def main(args):
    # Track initial API usage stats
    usage_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "cartesia_usage.json"))
    initial_usage = {}
    if os.path.exists(usage_file):
        try:
            with open(usage_file, "r") as f:
                initial_usage = json.load(f)
        except Exception:
            pass

    url = args.url
    concurrency = args.concurrency
    total_requests = args.requests
    
    # Load payload
    if args.payload:
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError:
            print(f"{Fore.RED}Error: Invalid JSON payload string.")
            sys.exit(1)
    else:
        payload = DEFAULT_PAYLOAD

    print(Fore.CYAN + Style.BRIGHT + "==================================================")
    print(Fore.CYAN + Style.BRIGHT + "             STARTING LOAD TEST                   ")
    print(Fore.CYAN + Style.BRIGHT + "==================================================")
    print(f"{Fore.WHITE}Target Endpoint:   {Fore.YELLOW}{url}")
    print(f"{Fore.WHITE}Total Requests:    {Fore.YELLOW}{total_requests}")
    print(f"{Fore.WHITE}Concurrency:       {Fore.YELLOW}{concurrency}")
    print(f"{Fore.WHITE}Prompt Length:     {Fore.YELLOW}{len(payload.get('prompt', ''))} chars")
    print(Fore.CYAN + "--------------------------------------------------")

    queue = asyncio.Queue()
    for i in range(total_requests):
        await queue.put(i)
    
    # Add termination sentinels for workers
    for _ in range(concurrency):
        await queue.put(None)

    results = []
    
    # Configure httpx client limits to avoid bottlenecking on client side
    limits = httpx.Limits(max_keepalive_connections=concurrency, max_connections=concurrency * 2)
    
    start_test_time = time.perf_counter()
    
    async with httpx.AsyncClient(limits=limits) as client:
        workers = [
            asyncio.create_task(worker(queue, client, url, payload, results, total_requests))
            for _ in range(concurrency)
        ]
        await queue.join()
        await asyncio.gather(*workers)
        
    end_test_time = time.perf_counter()
    total_duration = end_test_time - start_test_time

    # Calculate metrics
    successes = [r for r in results if r["success"]]
    failures = [r for r in results if not r["success"]]
    latencies = [r["latency"] for r in results]
    
    status_codes = {}
    errors = {}
    for r in results:
        code = r["status_code"]
        if code:
            status_codes[code] = status_codes.get(code, 0) + 1
        err = r["error"]
        if err:
            errors[err] = errors.get(err, 0) + 1

    print("\n" + Fore.MAGENTA + Style.BRIGHT + "=" * 50)
    print(Fore.MAGENTA + Style.BRIGHT + "                    RESULTS                    ")
    print(Fore.MAGENTA + Style.BRIGHT + "=" * 50)
    print(f"{Fore.WHITE}Total Duration:         {Fore.YELLOW}{total_duration:.2f} seconds")
    print(f"{Fore.WHITE}Total Requests:         {Fore.YELLOW}{total_requests}")
    print(f"{Fore.WHITE}Successful Requests:    {Fore.GREEN}{len(successes)}")
    print(f"{Fore.WHITE}Failed Requests:        {Fore.RED}{len(failures)}")
    print(f"{Fore.WHITE}Requests per Second:    {Fore.CYAN}{total_requests / total_duration:.2f} RPS")
    
    if latencies:
        print("\n" + Fore.CYAN + Style.BRIGHT + "Latency Metrics (seconds):")
        print(f"  Min:                  {Fore.GREEN}{min(latencies):.4f}s")
        print(f"  Max:                  {Fore.RED}{max(latencies):.4f}s")
        print(f"  Average:              {Fore.YELLOW}{np.mean(latencies):.4f}s")
        print(f"  Median (p50):         {Fore.YELLOW}{np.percentile(latencies, 50):.4f}s")
        print(f"  p90:                  {Fore.YELLOW}{np.percentile(latencies, 90):.4f}s")
        print(f"  p95:                  {Fore.YELLOW}{np.percentile(latencies, 95):.4f}s")
        print(f"  p99:                  {Fore.YELLOW}{np.percentile(latencies, 99):.4f}s")

    if status_codes:
        print("\n" + Fore.CYAN + Style.BRIGHT + "HTTP Status Codes:")
        for code, count in status_codes.items():
            color = Fore.GREEN if code == 200 else Fore.RED
            print(f"  {color}{code}: {count}")

    if errors:
        print("\n" + Fore.RED + Style.BRIGHT + "Errors encountered:")
        for err, count in errors.items():
            print(f"  {Fore.RED}'{err}': {count}")
            
    # Read final API usage stats (waiting briefly for async background agents to establish connections)
    print(f"\n{Fore.CYAN}Waiting 8 seconds for async background agents to connect and register usage...")
    await asyncio.sleep(8.0)
    
    final_usage = {}
    if os.path.exists(usage_file):
        try:
            with open(usage_file, "r") as f:
                final_usage = json.load(f)
        except Exception:
            pass
            
    # Calculate difference
    diff = {}
    for key, count in final_usage.items():
        start_count = initial_usage.get(key, 0)
        diff[key] = count - start_count
        
    print("\n" + Fore.MAGENTA + Style.BRIGHT + "Cartesia API Key Usage during this load test:")
    if any(count > 0 for count in diff.values()):
        for key, count in diff.items():
            if count > 0:
                print(f"  {Fore.GREEN}{key}: {count} calls")
    else:
        print(f"  {Fore.YELLOW}No new Cartesia calls registered during the load test (or keys not hit).")
    print(Fore.MAGENTA + Style.BRIGHT + "=" * 50)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mantra Voice Agent Load Tester")
    parser.add_argument("--url", default="http://localhost:8081/dispatch-test", help="Target API endpoint")
    parser.add_argument("--requests", type=int, default=10, help="Total number of requests to send")
    parser.add_argument("--concurrency", type=int, default=2, help="Number of concurrent workers")
    parser.add_argument("--payload", default=None, help="JSON payload to send (falls back to EyeMantra default payload)")
    
    args = parser.parse_args()
    asyncio.run(main(args))
