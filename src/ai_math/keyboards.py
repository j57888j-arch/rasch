from __future__ import annotations

from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton,
)


def user_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Imtihon"), KeyboardButton(text="🏅 Natijalarim")],
            [KeyboardButton(text="👤 Profilim"), KeyboardButton(text="ℹ️ Yordam")],
        ], resize_keyboard=True, input_field_placeholder="Kerakli bo‘limni tanlang",
    )


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Imtihon yuklash"), KeyboardButton(text="📄 Excel/JSON shablon")],
            [KeyboardButton(text="📚 Imtihonlar"), KeyboardButton(text="📊 Tizim statistikasi")],
            [KeyboardButton(text="👥 Foydalanuvchilar"), KeyboardButton(text="🏠 Foydalanuvchi menyusi")],
        ], resize_keyboard=True, input_field_placeholder="Admin bo‘limini tanlang",
    )


def mcq_keyboard(exam_id: str, number: int, options: dict[str, str]) -> InlineKeyboardMarkup:
    labels = list(options.keys())
    rows = []
    for i in range(0, len(labels), 4):
        rows.append([
            InlineKeyboardButton(text=letter, callback_data=f"ans:{exam_id}:{number}:{letter}")
            for letter in labels[i:i + 4]
        ])
    rows.append([InlineKeyboardButton(text="⏭ Javobsiz o‘tish", callback_data=f"skip:{exam_id}:{number}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def question_controls(exam_id: str, number: int, is_last: bool = False) -> InlineKeyboardMarkup:
    if is_last:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Imtihonni topshirish", callback_data=f"finish:{exam_id}")
        ]])
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✏️ Javobni o‘zgartirish", callback_data=f"edit:{exam_id}:{number}"),
        InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"next:{exam_id}:{number}"),
    ]])


def written_controls(exam_id: str, number: int, is_last: bool = False) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(text="✏️ A qismini", callback_data=f"editpart:{exam_id}:{number}:A"),
        InlineKeyboardButton(text="✏️ B qismini", callback_data=f"editpart:{exam_id}:{number}:B"),
    ]]
    if is_last:
        rows.append([InlineKeyboardButton(text="✅ Imtihonni topshirish", callback_data=f"finish:{exam_id}")])
    else:
        rows.append([InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"next:{exam_id}:{number}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def exam_admin_keyboard(exam_id: str, status: str) -> InlineKeyboardMarkup:
    rows = []
    if status in {"DRAFT", "PAUSED"}:
        rows.append([InlineKeyboardButton(text="▶️ Imtihonni ochish", callback_data=f"admopen:{exam_id}")])
    if status == "OPEN":
        rows.append([InlineKeyboardButton(text="🔒 Imtihonni yopish va tekshirish", callback_data=f"admclose:{exam_id}")])
    if status in {"CLOSED", "GRADING"}:
        rows.append([InlineKeyboardButton(text="⚙️ Hisoblashni boshlash", callback_data=f"admgrade:{exam_id}")])
    if status in {"REVIEW", "PUBLISHED"}:
        rows.append([InlineKeyboardButton(text="📊 Excel/PDF hisobot", callback_data=f"admreport:{exam_id}")])
    if status == "REVIEW":
        rows.append([InlineKeyboardButton(text="📣 Natijalarni e’lon qilish", callback_data=f"admpublish:{exam_id}")])
    rows.append([InlineKeyboardButton(text="👥 Ishtirokchilar", callback_data=f"admparticipants:{exam_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def exam_list_keyboard(exams: list) -> InlineKeyboardMarkup:
    rows = []
    for exam in exams:
        title = exam.title[:32]
        rows.append([InlineKeyboardButton(
            text=f"{title} · {exam.status}", callback_data=f"admsel:{exam.id}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else InlineKeyboardMarkup(inline_keyboard=[])
