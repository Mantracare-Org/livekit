# email_alerts.py
import os
import random
import smtplib
import traceback
import asyncio
import urllib.request
import urllib.parse
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage


async def send_crash_email(
    service_name: str, error: Exception, context_data: dict = None
):
    """
    Sends a formatted HTML email alert when a crash or pipeline error occurs.
    Executes on a background thread via asyncio.to_thread to prevent blocking the async loop.
    """
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = os.getenv("SMTP_PORT", "587")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("SMTP_FROM_EMAIL", smtp_user)
    alert_emails_str = os.getenv("ALERT_EMAIL_IDS", "")
    admin_emails_str = os.getenv("ADMIN_MAIL_ID", "")

    if not all([smtp_host, smtp_user, smtp_pass, from_email]):
        # Silently skip if email configurations are missing
        return

    all_emails = set()
    for email in alert_emails_str.split(",") + admin_emails_str.split(","):
        email = email.strip()
        if email:
            all_emails.add(email)

    to_emails = list(all_emails)
    if not to_emails:
        return

    # Generate complete traceback logs
    stack_trace = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

    # Process custom runtime metadata into HTML structure
    context_html = ""
    if context_data:
        for key, val in context_data.items():
            context_html += f"<li><strong>{key}:</strong> {val}</li>"
    else:
        context_html = "<li>No specific pipeline metadata recorded.</li>"

    # Premium Responsive HTML Email Template
    html_template = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>{service_name} Alert: Critical Failure</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background-color: #f4f6f8; margin: 0; padding: 20px; color: #333; }}
            .container {{ max-width: 750px; background: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.08); border: 1px solid #e1e4e8; margin: 0 auto; }}
            .header {{ background-color: #d9383a; padding: 25px; color: white; text-align: left; }}
            .header h2 {{ margin: 0; font-size: 20px; font-weight: 600; }}
            .content {{ padding: 30px; }}
            .error-box {{ background-color: #fff5f5; border-left: 4px solid #d9383a; padding: 15px; border-radius: 4px; margin-bottom: 25px; }}
            .error-title {{ font-size: 15px; font-weight: bold; color: #b71c1c; margin: 0 0 5px 0; }}
            .error-msg {{ font-family: "SFMono-Regular", Consolas, Monaco, monospace; font-size: 13.5px; color: #444; margin: 0; }}
            .meta-section {{ margin-bottom: 25px; }}
            .meta-section h3 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; color: #6a737d; margin-bottom: 10px; border-bottom: 1px solid #e1e4e8; padding-bottom: 5px; }}
            .meta-list {{ list-style-type: none; padding-left: 0; margin: 0; font-size: 14px; line-height: 1.6; }}
            .trace-section h3 {{ font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; color: #6a737d; margin-bottom: 10px; }}
            pre {{ background-color: #1e1e1e; color: #d4d4d4; padding: 15px; border-radius: 6px; overflow-x: auto; font-family: "SFMono-Regular", Consolas, monospace; font-size: 12.5px; line-height: 1.5; max-height: 450px; }}
            .footer {{ background-color: #fafbfc; padding: 15px 30px; text-align: center; font-size: 12px; color: #6a737d; border-top: 1px solid #e1e4e8; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2>⚠️ {service_name} Alert: Critical Component Crash</h2>
            </div>
            <div class="content">
                <div class="error-box">
                    <p class="error-title">Exception Raised: {type(error).__name__}</p>
                    <p class="error-msg">{str(error)}</p>
                </div>
                
                <div class="meta-section">
                    <h3>Environment & Context</h3>
                    <ul class="meta-list">
                        <li><strong>Service/Component:</strong> {service_name}</li>
                        <li><strong>Timestamp:</strong> {timestamp}</li>
                        {context_html}
                    </ul>
                </div>
                
                <div class="trace-section">
                    <h3>Stack Trace</h3>
                    <pre><code>{stack_trace}</code></pre>
                </div>
            </div>
            <div class="footer">
                This is an automated operational system alert from your Mantra Assistant Platform. Please review runtime health immediately.
            </div>
        </div>
    </body>
    </html>
    """

    def send_sync():

        meme_templates = [
            "fine",
            "pigeon",
            "harold",
            "disastergirl",
            "rollsafe",
            "sad-biden",
            "spiderman",
            "spongebob",
            "buzz",
            "doge",
            "drake",
            "trade",
            "wonka",
            "fry",
            "panik-kalm-panik",
        ]

        def escape_memegen(text):
            if not text:
                return "_"
            text = str(text)
            for o, n in [
                ("-", "--"),
                ("_", "__"),
                (" ", "_"),
                ("?", "~q"),
                ("&", "~a"),
                ("%", "~p"),
                ("#", "~h"),
                ("/", "~s"),
                ("\\", "~b"),
                ("<", "~l"),
                (">", "~g"),
                ('"', "''"),
            ]:
                text = text.replace(o, n)
            return urllib.parse.quote(text)

        admin_mail_ids = [
            m.strip().lower()
            for m in os.getenv("ADMIN_MAIL_ID", "").split(",")
            if m.strip()
        ]

        try:
            port = int(smtp_port)

            def send_to_recipient(smtp_conn, recipient):
                is_meme_recipient = bool(recipient.lower() in admin_mail_ids)

                # We need "related" to embed inline images properly
                msg = MIMEMultipart("related")
                msg["From"] = from_email
                msg["To"] = recipient
                if is_meme_recipient:
                    msg["Subject"] = (
                        f"🔥 {service_name.upper()} CRASH (Meme Edition): [{service_name}] - {type(error).__name__}"
                    )
                else:
                    msg["Subject"] = (
                        f"🔥 {service_name.upper()} CRASH: [{service_name}] - {type(error).__name__}"
                    )

                msg_alt = MIMEMultipart("alternative")
                msg.attach(msg_alt)

                final_html = html_template

                # Robustly fetch a meme image, retrying if 404
                img_data = None
                if is_meme_recipient:
                    top_text = f"{service_name} crashed"
                    bottom_text = f"Error: {type(error).__name__}"

                    random.shuffle(meme_templates)
                    for template in meme_templates:
                        meme_url = f"https://api.memegen.link/images/{template}/{escape_memegen(top_text)}/{escape_memegen(bottom_text)}.jpg"
                        try:
                            req = urllib.request.Request(
                                meme_url, headers={"User-Agent": "Mozilla/5.0"}
                            )
                            with urllib.request.urlopen(req, timeout=5) as response:
                                img_data = response.read()
                                break  # Success!
                        except Exception as img_err:
                            print(f"Skipping meme {meme_url}: {img_err}")
                            continue

                if img_data:
                    meme_html = f"""
                    <div style="text-align: center; margin-top: 20px;">
                        <h3 style="color: #d9383a;">Don't panic! Our {service_name.lower()} is just taking a nap.</h3>
                        <img src="cid:crash_meme" alt="Crash meme" style="max-width: 100%; border-radius: 8px; border: 2px solid #e1e4e8;">
                    </div>
                    """
                    # Inject the meme html before the footer
                    final_html = final_html.replace(
                        '</div>\n            <div class="footer">',
                        f'{meme_html}\n            </div>\n            <div class="footer">',
                    )

                msg_alt.attach(MIMEText(final_html, "html"))

                if img_data:
                    image = MIMEImage(img_data, _subtype="jpeg")
                    image.add_header("Content-ID", "<crash_meme>")
                    image.add_header("Content-Disposition", "inline")
                    msg.attach(image)

                smtp_conn.sendmail(from_email, [recipient], msg.as_string())

            if port == 465:
                with smtplib.SMTP_SSL(smtp_host, port) as smtp_conn:
                    smtp_conn.login(smtp_user, smtp_pass)
                    for recipient in to_emails:
                        send_to_recipient(smtp_conn, recipient)
            else:
                with smtplib.SMTP(smtp_host, port) as smtp_conn:
                    smtp_conn.starttls()
                    smtp_conn.login(smtp_user, smtp_pass)
                    for recipient in to_emails:
                        send_to_recipient(smtp_conn, recipient)
        except Exception as smtp_err:
            print(f"Failed to transmit email alert via SMTP: {smtp_err}")

    # Safely offload blocking synchronous I/O execution away from async thread loop
    await asyncio.to_thread(send_sync)
