# email_alerts.py
import os
import smtplib
import traceback
import asyncio
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

async def send_crash_email(service_name: str, error: Exception, context_data: dict = None):
    """
    Sends a formatted HTML email alert when a crash or pipeline error occurs.
    Executes on a background thread via asyncio.to_thread to prevent blocking the async loop.
    """
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = os.getenv("SMTP_PORT", "587")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    alert_emails_str = os.getenv("ALERT_EMAIL_IDS", "")
    
    if not all([smtp_host, smtp_user, smtp_pass, alert_emails_str]):
        # Silently skip if email configurations are missing
        return

    to_emails = [email.strip() for email in alert_emails_str.split(",") if email.strip()]
    if not to_emails:
        return

    # Generate complete traceback logs
    stack_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
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
        <title>Pipeline Alert: Critical Failure</title>
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
                <h2>⚠️ Pipeline Alert: Critical Component Crash</h2>
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
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"🔥 PIPELINE CRASH: [{service_name}] - {type(error).__name__}"
            msg["From"] = smtp_user
            msg["To"] = ", ".join(to_emails)
            msg.attach(MIMEText(html_template, "html"))

            port = int(smtp_port)
            if port == 465:
                with smtplib.SMTP_SSL(smtp_host, port) as smtp_conn:
                    smtp_conn.login(smtp_user, smtp_pass)
                    smtp_conn.sendmail(smtp_user, to_emails, msg.as_string())
            else:
                with smtplib.SMTP(smtp_host, port) as smtp_conn:
                    smtp_conn.starttls()
                    smtp_conn.login(smtp_user, smtp_pass)
                    smtp_conn.sendmail(smtp_user, to_emails, msg.as_string())
        except Exception as smtp_err:
            print(f"Failed to transmit email alert via SMTP: {smtp_err}")

    # Safely offload blocking synchronous I/O execution away from async thread loop
    await asyncio.to_thread(send_sync)