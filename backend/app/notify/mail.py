"""Two notices, queued in the same transaction as the change that causes them and sent by the `send_email` job.

The job payload holds only the application id and the template name; the address is read when the mail goes out,
so a withdrawn account (address erased, USR-07) gets nothing. Mails carry no result, score or personal detail:
they say where to look (SEC-04). SMTP down = InfraUnavailable (retried later without using an attempt).
"""
import asyncio
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import log_event
from app.jobs.queue import enqueue
from app.jobs.worker import InfraUnavailable, JobCtx, fenced_complete, handler

log = logging.getLogger("app.notify")
TEMPLATES = ("APPLICATION_RECEIVED", "RESULT_RELEASED")


async def queue_mail(db: AsyncSession, template: str, application_id: UUID, *, dedupe: str | None = None) -> None:
    """Call inside the transaction of the change (apply, decision). No-op while mail is switched off."""
    assert template in TEMPLATES
    if not get_settings().mail_enabled:
        return
    await enqueue(db, "send_email", {"template": template, "application_id": str(application_id)},
                  application_id=application_id, dedupe_key=dedupe)


def render(template: str, *, company: str, posting: str, base_url: str) -> tuple[str, str]:
    link = f"{base_url.rstrip('/')}/me/applications"
    if template == "APPLICATION_RECEIVED":
        subject = f"[{company}] {posting} 지원이 접수되었습니다"
        body = (f"{company}의 '{posting}' 공고에 지원서가 접수되었습니다.\n\n"
                f"AI 면접은 지원 현황에서 바로 시작하거나 마감 전에 나중에 볼 수 있습니다.\n{link}\n\n"
                "본 메일은 발신 전용입니다.")
    else:
        subject = f"[{company}] {posting} 지원 결과가 등록되었습니다"
        body = (f"{company}의 '{posting}' 공고 지원 결과가 등록되었습니다.\n"
                f"결과와 피드백은 로그인 후 지원 현황에서 확인할 수 있습니다.\n{link}\n\n"
                "본 메일은 발신 전용입니다.")
    return subject, body


def _send(msg: EmailMessage) -> None:
    s = get_settings()
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        if s.smtp_starttls:
            smtp.starttls()
        if s.smtp_username:
            smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
        smtp.send_message(msg)


@handler("send_email")
async def send_email(ctx: JobCtx) -> None:
    s = get_settings()
    template, app_id = ctx.payload["template"], ctx.payload["application_id"]
    async with session_scope() as db:
        row = (await db.execute(text(
            "SELECT u.email, c.name, p.title FROM applications a JOIN users u ON u.id = a.candidate_id "
            "JOIN job_postings p ON p.id = a.posting_id JOIN companies c ON c.id = a.company_id WHERE a.id = :a"),
            {"a": app_id})).first()
    if row is not None and row.email:
        subject, body = render(template, company=row.name, posting=row.title, base_url=s.public_base_url)
        msg = EmailMessage()
        msg["From"] = formataddr((s.mail_from_name, s.mail_from))
        msg["To"] = row.email
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=s.mail_from.split("@")[-1])
        msg.set_content(body)
        try:
            await asyncio.to_thread(_send, msg)
        except (OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected) as e:
            raise InfraUnavailable(f"smtp: {type(e).__name__}") from e
        log_event(log, "mail_sent", application_id=app_id, template=template)  # never the address
    else:
        log_event(log, "mail_skipped", application_id=app_id, template=template)  # withdrawn / no address
    async with session_scope() as db:
        await fenced_complete(db, ctx)
