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
from app.core.timeutil import KST
from app.jobs.queue import enqueue
from app.jobs.worker import InfraUnavailable, JobCtx, fenced_complete, handler

log = logging.getLogger("app.notify")
TEMPLATES = ("APPLICATION_RECEIVED", "RESULT_RELEASED")
ACCOUNT_TEMPLATES = ("PASSWORD_RESET",)
SIGNUP_TEMPLATE = "SIGNUP_CODE"


async def queue_mail(db: AsyncSession, template: str, application_id: UUID, *, dedupe: str | None = None) -> None:
    """Call inside the transaction of the change (apply, decision). No-op while mail is switched off."""
    assert template in TEMPLATES
    if not get_settings().mail_enabled:
        return
    await enqueue(db, "send_email", {"template": template, "application_id": str(application_id)},
                  application_id=application_id, dedupe_key=dedupe)


async def queue_account_mail(db: AsyncSession, template: str, user_id: UUID, extra: dict) -> None:
    """Mails about the account itself (no application). The address is still read at send time."""
    assert template in ACCOUNT_TEMPLATES
    if not get_settings().mail_enabled:
        return
    await enqueue(db, "send_email", {"template": template, "user_id": str(user_id), **extra})


async def queue_signup_code(db: AsyncSession, code_id: UUID, code: str) -> None:
    """Mail the 6-digit company-signup code (SC-R01). The address is read from signup_codes at send time."""
    if not get_settings().mail_enabled:
        return
    await enqueue(db, "send_email", {"template": SIGNUP_TEMPLATE, "code_id": str(code_id), "code": code})


def render_account(template: str, *, extra: dict, base_url: str) -> tuple[str, str]:
    link = f"{base_url.rstrip('/')}/reset-password?token={extra['token']}"
    subject = "[모의면접] 비밀번호 재설정 안내"
    body = ("비밀번호 재설정을 요청하셨습니다.\n아래 링크에서 30분 안에 새 비밀번호를 정해 주십시오.\n"
            f"{link}\n\n요청하지 않으셨다면 이 메일을 무시하셔도 됩니다. 본 메일은 발신 전용입니다.")
    return subject, body


def render_recruiter_mail(body: str, starts_at, place: str | None, token: str | None, base_url: str) -> str:
    """The recruiter's own text; a mailed interview date adds the time, place and the link to answer (SC-R14)."""
    text_ = body.rstrip()
    if starts_at is not None:
        when = starts_at.astimezone(KST).strftime("%Y-%m-%d %H:%M")
        text_ += f"\n\n면접 일시: {when} (한국 시간)"
        if place:
            text_ += f"\n장소: {place}"
        if token:
            text_ += f"\n\n일정 수락 또는 조율 요청: {base_url.rstrip('/')}/schedule-response?token={token}"
    return text_ + "\n\n본 메일은 발신 전용입니다."


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
    template = ctx.payload["template"]
    ref = ctx.payload.get("application_id") or ctx.payload.get("user_id") or ctx.payload.get("code_id")
    async with session_scope() as db:
        if template == SIGNUP_TEMPLATE:
            email = await db.scalar(text("SELECT email FROM signup_codes WHERE id = :c"), {"c": ctx.payload["code_id"]})
            content = ("[모의면접] 기업회원 가입 인증번호",
                       f"인증번호는 {ctx.payload['code']} 입니다. 10분 안에 입력해 주십시오.\n"
                       "요청하지 않으셨다면 이 메일을 무시하셔도 됩니다. 본 메일은 발신 전용입니다.") if email else None
        elif template == "RECRUITER_MAIL":
            row = (await db.execute(text(
                "SELECT u.email, m.subject, m.body, m.kind, s.starts_at, s.place FROM recruiter_mails m "
                "JOIN applications a ON a.id = m.application_id JOIN users u ON u.id = a.candidate_id "
                "LEFT JOIN interview_schedules s ON s.id = m.schedule_id WHERE m.id = :m"), {"m": ctx.payload["mail_id"]})).first()
            email = row.email if row is not None else None
            content = (row.subject, render_recruiter_mail(row.body, row.starts_at, row.place, ctx.payload.get("token"),
                                                           s.public_base_url)) if email else None
        elif template in ACCOUNT_TEMPLATES:
            email = await db.scalar(text("SELECT email FROM users WHERE id = :u AND status = 'ACTIVE'"),
                                    {"u": ctx.payload["user_id"]})
            content = render_account(template, extra=ctx.payload, base_url=s.public_base_url) if email else None
        else:
            row = (await db.execute(text(
                "SELECT u.email, c.name, p.title FROM applications a JOIN users u ON u.id = a.candidate_id "
                "JOIN job_postings p ON p.id = a.posting_id JOIN companies c ON c.id = a.company_id WHERE a.id = :a"),
                {"a": ctx.payload["application_id"]})).first()
            email = row.email if row is not None else None
            content = render(template, company=row.name, posting=row.title, base_url=s.public_base_url) if email else None
    if email and content:
        subject, body = content
        msg = EmailMessage()
        msg["From"] = formataddr((s.mail_from_name, s.mail_from))
        msg["To"] = email
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=s.mail_from.split("@")[-1])
        msg.set_content(body)
        try:
            await asyncio.to_thread(_send, msg)
        except (OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected) as e:
            raise InfraUnavailable(f"smtp: {type(e).__name__}") from e
        log_event(log, "mail_sent", ref=ref, template=template)  # never the address
    else:
        log_event(log, "mail_skipped", ref=ref, template=template)  # withdrawn / no address
    async with session_scope() as db:
        await fenced_complete(db, ctx)
