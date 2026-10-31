from __future__ import annotations

import asyncio
import json
import logging
import html
from datetime import datetime, timezone
from io import BytesIO

from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, BufferedInputFile, FSInputFile, InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from .config import settings
from .db import SessionLocal
from .models import Answer, Exam, Participant, PersonMeasure, Question, User, ReportExport
from .keyboards import user_menu, admin_menu, mcq_keyboard, question_controls, written_controls, exam_admin_keyboard, exam_list_keyboard
from .services import (
    ensure_user, get_open_exam, start_participant, get_participant, get_question, save_answer,
    list_exams, exam_participant_count, create_exam_from_payload, open_exam, close_exam,
    grade_exam, publish_exam, get_latest_published_result,
)
from .reports import build_excel, build_pdf
from .excel_importer import exam_payload_from_xlsx

logger = logging.getLogger(__name__)
router = Router()


class AdminStates(StatesGroup):
    waiting_exam_json = State()


_GRADING_TASKS: set[str] = set()


def is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.admin_id_set


async def _expire_participant_if_needed(session, exam: Exam, participant: Participant) -> bool:
    if participant.status != "IN_PROGRESS" or not participant.started_at:
        return participant.status != "IN_PROGRESS"
    started = participant.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    deadline = started.timestamp() + exam.duration_minutes * 60
    if datetime.now(timezone.utc).timestamp() > deadline:
        participant.status = "AUTO_SUBMITTED"
        participant.submitted_at = datetime.now(timezone.utc)
        await session.commit()
        return True
    return False


def admin_panel_text() -> str:
    return (
        "<b>AI MATH · ADMIN PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Imtihonlarni yaratish, boshqarish, tekshirish va hisobotlarni olish.\n\n"
        "<b>Asosiy bo‘limlar:</b>\n"
        "📥 Excel/JSON orqali 45 savol yuklash\n"
        "📚 Imtihonlarni boshqarish\n"
        "📊 Statistikani ko‘rish\n"
        "📄 Excel va PDF hisobotlarni olish"
    )


async def send_question(message: Message, exam_id: str, user_id: int, number: int | None = None) -> None:
    async with SessionLocal() as session:
        exam_res = await session.execute(select(Exam).where(Exam.id == exam_id))
        exam = exam_res.scalar_one_or_none()
        user_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = user_res.scalar_one_or_none()
        if not exam or not user:
            await message.answer("Imtihon yoki foydalanuvchi topilmadi.")
            return
        participant = await get_participant(session, exam_id, user.id)
        if not participant:
            await message.answer("Avval imtihonni boshlang.")
            return
        if exam.status != "OPEN":
            await message.answer("Imtihon hozir javob qabul qilmayapti.")
            return
        if participant.status != "IN_PROGRESS":
            await message.answer("Siz imtihonni allaqachon topshirgansiz.")
            return
        qnum = number or participant.current_question
        q = await get_question(session, exam_id, qnum)
        if not q:
            await message.answer("Savol topilmadi.")
            return
        participant.current_question = qnum
        participant.last_activity_at = datetime.now(timezone.utc)
        if q.qtype == "written2":
            pres = await session.execute(select(Answer).where(Answer.participant_id == participant.id))
            existing = {a.item_code: a for a in pres.scalars().all()}
            has_a = f"Q{qnum:02d}_A" in existing
            has_b = f"Q{qnum:02d}_B" in existing
            part = "A" if not has_a else ("B" if not has_b else None)
            participant.current_part = part
            await session.commit()
            if part is None:
                await message.answer(
                    f"<b>{qnum}-savol · YOZMA</b>\n━━━━━━━━━━━━\n{html.escape(q.prompt)}\n\n"
                    "✅ A va B qismlaridagi javoblaringiz saqlangan.\n"
                    "Tahrirlash tugmalaridan foydalaning yoki keyingi savolga o‘ting.",
                    reply_markup=written_controls(exam_id, qnum, qnum == 45),
                )
            else:
                await message.answer(
                    f"<b>{qnum}-savol · YOZMA</b>\n━━━━━━━━━━━━\n{html.escape(q.prompt)}\n\n"
                    f"<b>A)</b> {html.escape(q.answer_key['A']['prompt'])}\n"
                    f"<b>B)</b> {html.escape(q.answer_key['B']['prompt'])}\n\n"
                    f"✍️ Hozir <b>{part}</b> qismining javobini yuboring.\n"
                    "Javobsiz qoldirish uchun <code>-</code> yuboring."
                )
        else:
            qtype_label = "A–D" if q.qtype == "mcq4" else "A–F"
            text = (
                f"<b>{qnum}-savol</b>  <code>{qtype_label}</code>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"{html.escape(q.prompt)}\n\n"
            )
            for key, value in q.options.items():
                text += f"<b>{key})</b> {html.escape(str(value))}\n"
            text += "\n<b>Bitta javobni tanlang:</b>"
            await session.commit()
            await message.answer(text, reply_markup=mcq_keyboard(exam_id, qnum, q.options))


async def send_next_prompt(message: Message, exam_id: str, user_id: int, from_number: int) -> None:
    if from_number >= 45:
        await message.answer(
            "<b>Testning oxirgi savoliga yetdingiz.</b>\nJavoblaringizni topshirish uchun tugmani bosing.",
            reply_markup=question_controls(exam_id, 45, True),
        )
    else:
        await message.answer(
            f"✅ {from_number}-savol javobi saqlandi.\nKeyingi savolga o‘ting.",
            reply_markup=question_controls(exam_id, from_number, False),
        )


async def create_and_send_reports(bot: Bot, exam_id: str) -> None:
    if exam_id in _GRADING_TASKS:
        return
    _GRADING_TASKS.add(exam_id)
    try:
        async with SessionLocal() as session:
            exam_res = await session.execute(select(Exam).where(Exam.id == exam_id))
            exam = exam_res.scalar_one_or_none()
            if not exam:
                return
            if exam.status in {"CLOSED", "GRADING"}:
                await grade_exam(session, exam_id, settings.min_calibration_participants)
            elif exam.status not in {"REVIEW", "PUBLISHED"}:
                return
        async with SessionLocal() as session:
            exam_res = await session.execute(select(Exam).where(Exam.id == exam_id))
            exam = exam_res.scalar_one()
            xlsx = await build_excel(session, exam)
            pdf = await build_pdf(session, exam)
            now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
            xlsx_name = f"AI_MATH_{exam_id[:8]}_{now}.xlsx"
            pdf_name = f"AI_MATH_{exam_id[:8]}_{now}.pdf"
            for admin_id in settings.admin_id_set:
                await bot.send_document(
                    admin_id, BufferedInputFile(xlsx.getvalue(), filename=xlsx_name),
                    caption=f"📊 <b>{html.escape(exam.title)}</b>\nExcel: barcha ishtirokchilar va 55 ta javob birligi statistikasi.",
                )
                await bot.send_document(
                    admin_id, BufferedInputFile(pdf.getvalue(), filename=pdf_name),
                    caption=f"📄 <b>{html.escape(exam.title)}</b>\nUmumiy imtihon hisoboti.",
                )
            async with SessionLocal() as log_session:
                log_session.add(ReportExport(exam_id=exam_id, file_type="XLSX", filename=xlsx_name, created_by=0))
                log_session.add(ReportExport(exam_id=exam_id, file_type="PDF", filename=pdf_name, created_by=0))
                await log_session.commit()
        logger.info("Reports created for exam %s", exam_id)
    except Exception:
        logger.exception("Grading/report generation failed for exam %s", exam_id)
        for admin_id in settings.admin_id_set:
            try:
                await bot.send_message(admin_id, f"⚠️ {exam_id[:8]} imtihonini hisoblashda xatolik yuz berdi. Loglarni tekshiring.")
            except Exception:
                pass
    finally:
        _GRADING_TASKS.discard(exam_id)


@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with SessionLocal() as session:
        await ensure_user(
            session, message.from_user.id, message.from_user.username,
            message.from_user.full_name or "Foydalanuvchi",
        )
    if is_admin(message.from_user.id):
        await message.answer(admin_panel_text(), reply_markup=admin_menu())
    else:
        await message.answer(
            "<b>AI MATH'ga xush kelibsiz!</b>\n\n"
            "Bu yerda 45 savollik matematik imtihonda qatnashishingiz va e'lon qilingan natijalaringizni ko‘rishingiz mumkin.",
            reply_markup=user_menu(),
        )


@router.message(Command("admin"))
async def admin_command(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(admin_panel_text(), reply_markup=admin_menu())


@router.message(F.text == "🏠 Foydalanuvchi menyusi")
async def switch_user_menu(message: Message) -> None:
    if is_admin(message.from_user.id):
        await message.answer("Foydalanuvchi menyusi.", reply_markup=user_menu())


@router.message(F.text == "➕ Imtihon yuklash")
async def ask_exam_json(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.waiting_exam_json)
    await message.answer(
        "📥 <b>Imtihon faylini yuboring.</b>\n\n"
        "Excel (.xlsx) yoki JSON (.json) qabul qilinadi. Faylda aynan 45 ta savol bo‘lishi kerak: 1–32 ABCD, 33–35 ABCDEF, 36–45 yozma A/B.\n"
        "Excel shablonini <b>📄 Excel/JSON shablon</b> tugmasi orqali oling."
    )


@router.message(F.text == "📄 Excel/JSON shablon")
async def send_template(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    xlsx_path = root / "data" / "exam_template.xlsx"
    json_path = root / "data" / "exam_template.json"
    if xlsx_path.exists():
        await message.answer_document(FSInputFile(xlsx_path), caption="📊 Excel shablon — savollarni jadvalda tahrirlang va qayta yuboring.")
    if json_path.exists():
        await message.answer_document(FSInputFile(json_path), caption="🧩 JSON shablon — texnik import formati.")
    if not xlsx_path.exists() and not json_path.exists():
        await message.answer("Shablon fayllari serverda topilmadi.")


@router.message(AdminStates.waiting_exam_json, F.document)
async def receive_exam_json(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    filename = (message.document.file_name or "").lower()
    if not filename.endswith((".json", ".xlsx")):
        await message.answer("Faqat .xlsx yoki .json fayl yuboring.")
        return
    try:
        if message.document.file_size and message.document.file_size > 1_000_000:
            await message.answer("Fayl hajmi 1 MB dan kichik bo‘lishi kerak.")
            return
        buffer = BytesIO()
        await message.bot.download(message.document.file_id, destination=buffer)
        raw_bytes = buffer.getvalue()
        if filename.endswith(".xlsx"):
            payload = exam_payload_from_xlsx(raw_bytes)
        else:
            payload = json.loads(raw_bytes.decode("utf-8-sig"))
        async with SessionLocal() as session:
            exam = await create_exam_from_payload(session, payload, message.from_user.id)
        await state.clear()
        await message.answer(
            f"✅ <b>Imtihon yaratildi!</b>\n\n"
            f"Nomi: <b>{html.escape(exam.title)}</b>\nSavollar: 45\nRasch birliklari: 55\nHolati: DRAFT\n\n"
            "Imtihonni ochish uchun <b>📚 Imtihonlar</b> bo‘limiga kiring.",
            reply_markup=admin_menu(),
        )
    except Exception as exc:
        logger.exception("Exam JSON import failed")
        await message.answer(f"❌ Imtihon fayli yuklanmadi.\n<code>{html.escape(str(exc)[:900])}</code>\n\nFaylni tuzatib qayta yuboring.")


@router.message(F.text == "📚 Imtihonlar")
async def list_exams_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    async with SessionLocal() as session:
        exams = await list_exams(session, 15)
    if not exams:
        await message.answer("Hozircha imtihon yo‘q. Avval Excel yoki JSON shablon asosida imtihon yuklang.")
        return
    await message.answer("<b>Imtihonlar ro‘yxati</b>\nKeraklisini tanlang:", reply_markup=exam_list_keyboard(exams))


@router.callback_query(F.data.startswith("admsel:"))
async def select_exam_admin(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo‘q.", show_alert=True)
        return
    exam_id = call.data.split(":", 1)[1]
    async with SessionLocal() as session:
        exam_res = await session.execute(select(Exam).where(Exam.id == exam_id))
        exam = exam_res.scalar_one_or_none()
        if not exam:
            await call.answer("Imtihon topilmadi.", show_alert=True)
            return
        total, submitted = await exam_participant_count(session, exam_id)
        qcount = await session.scalar(select(func.count(Question.id)).where(Question.exam_id == exam_id)) or 0
    await call.message.answer(
        f"<b>{html.escape(exam.title)}</b>\n━━━━━━━━━━━━\n"
        f"Holati: <b>{exam.status}</b>\nSavollar: {qcount}/45\n"
        f"Qatnashchilar: {total}\nTopshirganlar: {submitted}\n"
        f"Vaqt: {exam.duration_minutes} daqiqa",
        reply_markup=exam_admin_keyboard(exam_id, exam.status),
    )
    await call.answer()


@router.callback_query(F.data.startswith("admopen:"))
async def admin_open_exam(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo‘q.", show_alert=True)
        return
    exam_id = call.data.split(":", 1)[1]
    try:
        async with SessionLocal() as session:
            exam = await open_exam(session, exam_id)
        await call.message.answer(f"🟢 <b>{html.escape(exam.title)}</b> imtihoni ochildi. Foydalanuvchilar javob yuborishi mumkin.")
    except Exception as exc:
        await call.message.answer(f"⚠️ {html.escape(str(exc))}")
    await call.answer()


@router.callback_query(F.data.startswith("admclose:"))
async def admin_close_exam(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo‘q.", show_alert=True)
        return
    exam_id = call.data.split(":", 1)[1]
    try:
        async with SessionLocal() as session:
            exam = await close_exam(session, exam_id)
        await call.message.answer(
            f"🔒 <b>{html.escape(exam.title)}</b> yopildi. Endi javoblar tekshiriladi va Rasch hisoblash bajariladi."
        )
        asyncio.create_task(create_and_send_reports(call.bot, exam_id))
    except Exception as exc:
        await call.message.answer(f"⚠️ {html.escape(str(exc))}")
    await call.answer()


@router.callback_query(F.data.startswith("admgrade:"))
async def admin_grade_exam(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo‘q.", show_alert=True)
        return
    exam_id = call.data.split(":", 1)[1]
    asyncio.create_task(create_and_send_reports(call.bot, exam_id))
    await call.message.answer("⚙️ Hisoblash jarayoni boshlandi. Tayyor bo‘lgach, Excel va PDF fayllar yuboriladi.")
    await call.answer()


@router.callback_query(F.data.startswith("admreport:"))
async def admin_report_again(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo‘q.", show_alert=True)
        return
    exam_id = call.data.split(":", 1)[1]
    async def resend() -> None:
        try:
            async with SessionLocal() as session:
                exam_res = await session.execute(select(Exam).where(Exam.id == exam_id))
                exam = exam_res.scalar_one_or_none()
                if not exam:
                    return
                xlsx = await build_excel(session, exam)
                pdf = await build_pdf(session, exam)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
            await call.bot.send_document(call.from_user.id, BufferedInputFile(xlsx.getvalue(), filename=f"AI_MATH_{stamp}.xlsx"))
            await call.bot.send_document(call.from_user.id, BufferedInputFile(pdf.getvalue(), filename=f"AI_MATH_{stamp}.pdf"))
        except Exception:
            logger.exception("Report resend failed for %s", exam_id)
            await call.bot.send_message(call.from_user.id, "Hisobotni qayta yaratishda xatolik yuz berdi. Server loglarini tekshiring.")
    asyncio.create_task(resend())
    await call.message.answer("📁 Hisobotlar qayta tayyorlanmoqda va sizga yuboriladi.")
    await call.answer()


@router.callback_query(F.data.startswith("admpublish:"))
async def admin_publish(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo‘q.", show_alert=True)
        return
    exam_id = call.data.split(":", 1)[1]
    try:
        async with SessionLocal() as session:
            exam = await publish_exam(session, exam_id)
            res = await session.execute(
                select(Participant, User, PersonMeasure)
                .join(User, User.id == Participant.user_id)
                .join(PersonMeasure, PersonMeasure.participant_id == Participant.id)
                .where(Participant.exam_id == exam_id)
            )
            rows = res.all()
        sent = 0
        for participant, user, measure in rows:
            try:
                await call.bot.send_message(
                    user.telegram_id,
                    f"🏅 <b>{html.escape(exam.title)} natijalari e'lon qilindi!</b>\n━━━━━━━━━━━━\n"
                    f"Rasch ball: <b>{measure.rasch_score:.2f}/100</b>\n"
                    f"Daraja: <b>{measure.grade}</b>\n"
                    f"Xom natija: {measure.raw_points}/{measure.max_points}\n"
                    f"Theta: <code>{measure.theta:.3f}</code>\n"
                    + ("⚠️ Natija dastlabki kalibrlash asosida. " if measure.provisional else ""),
                    reply_markup=user_menu(),
                )
                sent += 1
                await asyncio.sleep(0.04)
            except Exception:
                logger.warning("Could not notify user %s", user.telegram_id)
        await call.message.answer(f"📣 Natijalar e'lon qilindi. Xabar yuborildi: {sent}/{len(rows)}.")
    except Exception as exc:
        await call.message.answer(f"⚠️ Natijalarni e'lon qilishda xatolik: {exc}")
    await call.answer()


@router.callback_query(F.data.startswith("admparticipants:"))
async def admin_participants(call: CallbackQuery) -> None:
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo‘q.", show_alert=True)
        return
    exam_id = call.data.split(":", 1)[1]
    async with SessionLocal() as session:
        res = await session.execute(
            select(Participant, User).join(User, User.id == Participant.user_id).where(Participant.exam_id == exam_id)
        )
        rows = res.all()
    lines = ["<b>Imtihon qatnashchilari</b>"]
    for i, (participant, user) in enumerate(rows[:50], 1):
        lines.append(f"{i}. {html.escape(user.first_name)} · <code>{user.telegram_id}</code> · {participant.status}")
    if len(rows) > 50:
        lines.append(f"... yana {len(rows) - 50} kishi. To‘liq ro‘yxat Excel faylida.")
    await call.message.answer("\n".join(lines) if rows else "Hali qatnashchilar yo‘q.")
    await call.answer()


@router.message(F.text == "📊 Tizim statistikasi")
async def admin_stats(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    async with SessionLocal() as session:
        users = await session.scalar(select(func.count(User.id))) or 0
        exams = await session.scalar(select(func.count(Exam.id))) or 0
        open_exams = await session.scalar(select(func.count(Exam.id)).where(Exam.status == "OPEN")) or 0
    await message.answer(
        f"<b>AI MATH statistikasi</b>\nFoydalanuvchilar: {users or 0}\n"
        f"Imtihonlar: {exams or 0}\nOchiq imtihonlar: {open_exams}"
    )


@router.message(F.text == "👥 Foydalanuvchilar")
async def admin_users(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    async with SessionLocal() as session:
        total = await session.scalar(select(func.count(User.id))) or 0
    await message.answer(f"Ro‘yxatdan o‘tgan foydalanuvchilar: <b>{total}</b>.")


@router.message(F.text == "📝 Imtihon")
async def user_exam(message: Message) -> None:
    if is_admin(message.from_user.id):
        await message.answer("Admin uchun imtihonlar bo‘limidan foydalaning.", reply_markup=admin_menu())
        return
    async with SessionLocal() as session:
        user = await ensure_user(session, message.from_user.id, message.from_user.username, message.from_user.full_name)
        exam = await get_open_exam(session)
        if not exam:
            await message.answer("Hozircha ochiq imtihon mavjud emas. Imtihon ochilganda shu bo‘limda ko‘rinadi.")
            return
        participant = await start_participant(session, exam.id, user.id)
        if participant.status in {"SUBMITTED", "AUTO_SUBMITTED"}:
            await message.answer("Siz bu imtihonni topshirgansiz. Natija admin tomonidan e'lon qilinadi.")
            return
    await message.answer(
        f"<b>{html.escape(exam.title)}</b>\n━━━━━━━━━━━━\n{html.escape(exam.description)}\n\n"
        "45 ta savol: 1–32 ABCD, 33–35 ABCDEF, 36–45 yozma A/B.\n"
        f"Ajratilgan vaqt: {exam.duration_minutes} daqiqa.\n"
        "Javoblar imtihon yopilgandan keyin tekshiriladi.\n\nBoshlaymiz!"
    )
    await send_question(message, exam.id, message.from_user.id, participant.current_question)


@router.callback_query(F.data.startswith("ans:"))
async def user_mcq_answer(call: CallbackQuery) -> None:
    try:
        _, exam_id, number_text, choice = call.data.split(":")
        number = int(number_text)
    except Exception:
        await call.answer("Javob formati noto‘g‘ri.", show_alert=True)
        return
    async with SessionLocal() as session:
        exam_res = await session.execute(select(Exam).where(Exam.id == exam_id))
        exam = exam_res.scalar_one_or_none()
        user_res = await session.execute(select(User).where(User.telegram_id == call.from_user.id))
        user = user_res.scalar_one_or_none()
        participant = await get_participant(session, exam_id, user.id) if user else None
        q = await get_question(session, exam_id, number)
        if not exam or exam.status != "OPEN" or not participant or participant.status != "IN_PROGRESS" or not q:
            await call.answer("Imtihon javob qabul qilmayapti.", show_alert=True)
            return
        if await _expire_participant_if_needed(session, exam, participant):
            await call.answer("Ajratilgan vaqt tugadi. Javoblar topshirildi.", show_alert=True)
            await call.message.answer("⏰ Imtihon vaqtingiz tugadi. Javoblaringiz saqlandi.")
            return
        if q.qtype not in {"mcq4", "mcq6"} or choice not in q.options:
            await call.answer("Variant noto‘g‘ri.", show_alert=True)
            return
        await save_answer(session, participant, f"Q{number:02d}", choice, int(q.weights.get("MAIN", 1)))
        participant.current_question = number
        await session.commit()
    await call.answer("Javob saqlandi.")
    await call.message.edit_reply_markup(reply_markup=question_controls(exam_id, number, number == 45))
    if number == 45:
        await call.message.answer("Oxirgi savol. Imtihonni topshirish uchun pastdagi tugmani bosing.")


@router.callback_query(F.data.startswith("skip:"))
async def user_skip_mcq(call: CallbackQuery) -> None:
    _, exam_id, number_text = call.data.split(":")
    number = int(number_text)
    async with SessionLocal() as session:
        exam_res = await session.execute(select(Exam).where(Exam.id == exam_id))
        exam = exam_res.scalar_one_or_none()
        user_res = await session.execute(select(User).where(User.telegram_id == call.from_user.id))
        user = user_res.scalar_one_or_none()
        participant = await get_participant(session, exam_id, user.id) if user else None
        q = await get_question(session, exam_id, number)
        if not exam or exam.status != "OPEN" or not participant or participant.status != "IN_PROGRESS" or not q:
            await call.answer("Imtihon yopilgan.", show_alert=True)
            return
        if await _expire_participant_if_needed(session, exam, participant):
            await call.answer("Vaqt tugadi.", show_alert=True)
            return
        await save_answer(session, participant, f"Q{number:02d}", "", int(q.weights.get("MAIN", 1)))
    await call.answer("Savol javobsiz qoldirildi.")
    await call.message.edit_reply_markup(reply_markup=question_controls(exam_id, number, number == 45))


@router.callback_query(F.data.startswith("next:"))
async def user_next_question(call: CallbackQuery) -> None:
    _, exam_id, number_text = call.data.split(":")
    number = int(number_text)
    async with SessionLocal() as session:
        user_res = await session.execute(select(User).where(User.telegram_id == call.from_user.id))
        user = user_res.scalar_one_or_none()
        participant = await get_participant(session, exam_id, user.id) if user else None
        exam_res = await session.execute(select(Exam).where(Exam.id == exam_id))
        exam = exam_res.scalar_one_or_none()
        if not participant or participant.status != "IN_PROGRESS" or not exam or exam.status != "OPEN":
            await call.answer("Imtihon hozir ochiq emas.", show_alert=True)
            return
        if participant.current_question != number:
            await call.answer("Bu tugma eskirgan. Hozirgi savoldan davom eting.", show_alert=True)
            return
        if await _expire_participant_if_needed(session, exam, participant):
            await call.answer("Vaqt tugadi.", show_alert=True)
            await call.message.answer("⏰ Ajratilgan vaqt tugadi. Javoblaringiz saqlandi.")
            return
        next_number = min(number + 1, 45)
        participant.current_question = next_number
        participant.current_part = None
        await session.commit()
    await call.answer()
    await send_question(call.message, exam_id, call.from_user.id, next_number)


@router.callback_query(F.data.startswith("edit:"))
async def user_edit_answer(call: CallbackQuery) -> None:
    _, exam_id, number_text = call.data.split(":")
    number = int(number_text)
    await call.answer("Javobni qayta kiritishingiz mumkin.")
    await send_question(call.message, exam_id, call.from_user.id, number)


@router.callback_query(F.data.startswith("editpart:"))
async def user_edit_written_part(call: CallbackQuery) -> None:
    try:
        _, exam_id, number_text, part = call.data.split(":")
        number = int(number_text)
        if part not in {"A", "B"}:
            raise ValueError
    except Exception:
        await call.answer("Tahrirlash so‘rovi noto‘g‘ri.", show_alert=True)
        return
    async with SessionLocal() as session:
        user_res = await session.execute(select(User).where(User.telegram_id == call.from_user.id))
        user = user_res.scalar_one_or_none()
        participant = await get_participant(session, exam_id, user.id) if user else None
        exam_res = await session.execute(select(Exam).where(Exam.id == exam_id))
        exam = exam_res.scalar_one_or_none()
        q = await get_question(session, exam_id, number)
        if not exam or exam.status != "OPEN" or not participant or participant.status != "IN_PROGRESS" or not q:
            await call.answer("Imtihon endi javob qabul qilmaydi.", show_alert=True)
            return
        if await _expire_participant_if_needed(session, exam, participant):
            await call.answer("Vaqt tugadi.", show_alert=True)
            return
        participant.current_question = number
        participant.current_part = part
        await session.commit()
        prompt = q.answer_key[part]["prompt"]
    await call.message.answer(
        f"<b>{number}-savol · {part} qismini qayta kiriting</b>\n{html.escape(prompt)}\n\n"
        "Javobni yuboring yoki <code>-</code> yozing."
    )
    await call.answer("Tahrirlash rejimi yoqildi.")


@router.callback_query(F.data.startswith("finish:"))
async def user_finish_exam(call: CallbackQuery) -> None:
    exam_id = call.data.split(":", 1)[1]
    async with SessionLocal() as session:
        user_res = await session.execute(select(User).where(User.telegram_id == call.from_user.id))
        user = user_res.scalar_one_or_none()
        participant = await get_participant(session, exam_id, user.id) if user else None
        exam_res = await session.execute(select(Exam).where(Exam.id == exam_id))
        exam = exam_res.scalar_one_or_none()
        if not participant or participant.status != "IN_PROGRESS" or not exam or exam.status != "OPEN":
            await call.answer("Imtihon yopilgan.", show_alert=True)
            return
        if participant.current_question != 45 or participant.current_part in {"A", "B"}:
            await call.answer("Avval 45-savolning A va B qismlarini yakunlang.", show_alert=True)
            return
        if await _expire_participant_if_needed(session, exam, participant):
            await call.answer("Ajratilgan vaqt tugadi.", show_alert=True)
            await call.message.answer("⏰ Ajratilgan vaqt tugadi. Javoblaringiz saqlandi.")
            return
        # Missing parts will be counted as incorrect on final grading.
        participant.status = "SUBMITTED"
        participant.submitted_at = datetime.now(timezone.utc)
        await session.commit()
    await call.message.answer("🎉 <b>Imtihoningiz topshirildi!</b>\nJavoblar admin imtihonni yopgandan keyin tekshiriladi.", reply_markup=user_menu())
    await call.answer("Imtihon topshirildi.")


@router.message(F.text == "🏅 Natijalarim")
async def user_results(message: Message) -> None:
    async with SessionLocal() as session:
        user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = user_res.scalar_one_or_none()
        if not user:
            await message.answer("Avval /start buyrug‘ini yuboring.")
            return
        results = await get_latest_published_result(session, user.id)
    if not results:
        await message.answer("Hozircha e'lon qilingan natijangiz yo‘q.")
        return
    text = "<b>🏅 Sizning natijalaringiz</b>\n━━━━━━━━━━━━\n"
    for exam, measure in results:
        text += (
            f"<b>{html.escape(exam.title)}</b>\nRasch ball: <b>{measure.rasch_score:.2f}/100</b>\n"
            f"Daraja: <b>{measure.grade}</b>\nXom ball: {measure.raw_points}/{measure.max_points}\n"
            f"Theta: <code>{measure.theta:.3f}</code>\n"
            f"Kalibrlash: {'Dastlabki' if measure.provisional else 'Tekshirilgan'}\n\n"
        )
    await message.answer(text)


@router.message(F.text == "👤 Profilim")
async def user_profile(message: Message) -> None:
    async with SessionLocal() as session:
        res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = res.scalar_one_or_none()
        count = await session.scalar(
            select(func.count(Participant.id)).where(Participant.user_id == user.id)
        ) if user else 0
    if not user:
        await message.answer("Profil topilmadi. /start yuboring.")
        return
    await message.answer(
        f"<b>👤 Profilingiz</b>\nIsm: {html.escape(user.first_name)}\n"
        f"Telegram ID: <code>{user.telegram_id}</code>\n"
        f"Username: @{html.escape(user.username) if user.username else 'yo‘q'}\nImtihonlarda qatnashgan: {count or 0}"
    )


@router.message(F.text == "ℹ️ Yordam")
async def user_help(message: Message) -> None:
    await message.answer(
        "<b>AI MATH yordam</b>\n"
        "1. 📝 Imtihon bo‘limidan ochiq imtihonni boshlang.\n"
        "2. Variantli savollarda bitta javobni tanlang.\n"
        "3. Yozma savollarda A va B javoblarini alohida yuboring.\n"
        "4. Javobsiz qoldirish uchun '-' yuborish mumkin.\n"
        "5. Imtihon yopilgach, natijalar admin tomonidan e'lon qilinadi."
    )


@router.message(F.text)
async def user_written_answer(message: Message, state: FSMContext) -> None:
    if is_admin(message.from_user.id):
        return
    if message.text.startswith("/"):
        return
    async with SessionLocal() as session:
        user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = user_res.scalar_one_or_none()
        if not user:
            return
        exam = await get_open_exam(session)
        if not exam:
            return
        participant = await get_participant(session, exam.id, user.id)
        if not participant or participant.status != "IN_PROGRESS":
            return
        if await _expire_participant_if_needed(session, exam, participant):
            await message.answer("⏰ Imtihon uchun ajratilgan vaqt tugadi. Javoblaringiz saqlandi.")
            return
        q = await get_question(session, exam.id, participant.current_question)
        if not q or q.qtype != "written2":
            return
        part = participant.current_part
        if part not in {"A", "B"}:
            await message.answer(
                "Bu savolda javob kiritish oynasi hozir ochiq emas. "
                "A yoki B qismini tahrirlash tugmasidan foydalaning."
            )
            return
        text = message.text.strip()
        if len(text) > 250:
            await message.answer("Javob juda uzun. Iltimos, faqat matematik javobni yuboring (250 belgigacha).")
            return
        await save_answer(session, participant, f"Q{q.number:02d}_{part}", "" if text == "-" else text, int(q.weights.get(part, 4)))
        if part == "A":
            existing_b = await get_answer(session, participant.id, f"Q{q.number:02d}_B")
            if existing_b is None:
                participant.current_part = "B"
                await session.commit()
                await message.answer(f"✅ {q.number}-savol A qismi saqlandi.\n\n<b>B)</b> {html.escape(q.answer_key['B']['prompt'])}\n\nJavobni yuboring yoki <code>-</code> yozing.")
            else:
                participant.current_part = None
                await session.commit()
                await message.answer(
                    f"✅ {q.number}-savol A qismi yangilandi. B javobi avval saqlangan.",
                    reply_markup=written_controls(exam.id, q.number, q.number == 45),
                )
        else:
            participant.current_part = None
            await session.commit()
            await message.answer(
                f"✅ {q.number}-savolning A va B javoblari saqlandi.",
                reply_markup=written_controls(exam.id, q.number, q.number == 45),
            )


async def recover_pending_grading(bot: Bot) -> None:
    await asyncio.sleep(3)
    try:
        async with SessionLocal() as session:
            res = await session.execute(select(Exam.id).where(Exam.status.in_(["CLOSED", "GRADING"])))
            exam_ids = [row[0] for row in res.all()]
        for exam_id in exam_ids:
            asyncio.create_task(create_and_send_reports(bot, exam_id))
    except Exception:
        logger.exception("Could not recover grading tasks")
