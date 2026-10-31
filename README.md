# AI MATH — Telegram exam bot

45 ta savolli imtihon, 55 ta Rasch javob birligi, avtomatik tekshiruv, Rasch 1PL va admin uchun XLSX/PDF hisobot.

## Muhim: Render Free cheklovlari

Ushbu loyiha Render Free'da sinov/prototip sifatida ishga tushishi uchun ixchamlashtirilgan. Render Free web service 15 daqiqalik kiruvchi trafik bo'lmasa uxlaydi; qayta uyg'onish taxminan bir daqiqa olishi mumkin. Render Free Postgres 30 kundan keyin tugaydi va Free Key Value 25 MB hamda xotirada ishlaydi. Shuning uchun muhim imtihonlar uchun Free tarifni production darajasida ishonchli deb hisoblamang. Postgres backup/eksportini muntazam bajaring va DB muddatini oldindan kuzating.

Asosiy imtihon javoblari PostgreSQL'da saqlanadi. Redis faqat ixtiyoriy FSM/cache qatlamidir; Redis o'chsa, asosiy javoblar o'chmaydi.

## Test formati

- 1–32: `mcq4` — A/B/C/D dan bittasi.
- 33–35: `mcq6` — A/B/C/D/E/F dan bittasi.
- 36–45: `written2` — A va B yozma qismlari.
- Jami: 45 ta savol, 55 ta alohida 0/1 Rasch javob birligi.

MCQ vazni 1–3 oralig'ida, yozma A/B qismlarining vazni 4–6 oralig'ida belgilanadi. Vaznli xom ball va Rasch asosida kutilgan ball alohida ko'rsatiladi. Darajalar Rasch ballining 0–100 ko'rsatkichiga qo'llanadi: A+ >=70, A >=65, B+ >=60, B >=55, C+ >=50, C >=46, aks holda talabga yetmagan.

Rasch 1PL uchun javoblar 0/1 shaklida ishlatiladi. Bu loyiha yengil, normal priorli MML/EAP kalibrlash prototipini o'z ichiga oladi. Namuna kichik bo'lsa, Beta va Theta baholari dastlabki/provisional deb belgilanadi. Bir imtihonning o'zi to'liq psixometrik validatsiya uchun yetarli bo'lmasligi mumkin.

## Local ishga tushirish

Python 3.12 tavsiya etiladi.

```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env   # Windows; Linux/macOS: cp .env.example .env
```

`.env` ichida `BOT_TOKEN`, `ADMIN_IDS` va `DATABASE_URL` ni kiriting. Lokal sinov uchun SQLite ishlatish mumkin:

```env
DATABASE_URL=sqlite+aiosqlite:///./ai_math.db
REDIS_URL=
USE_WEBHOOK=false
```

```bash
uvicorn ai_math.main:app --app-dir src --host 127.0.0.1 --port 8000
```

Bot polling rejimida ishlaydi. Webhook rejimida ishlatish uchun `USE_WEBHOOK=true`, `PUBLIC_BASE_URL` va `WEBHOOK_SECRET` belgilang.

## Admin orqali imtihon yaratish

1. Botga `/start` yuboring. `ADMIN_IDS` ichiga kiritilgan Telegram ID admin bo'ladi.
2. `📄 Excel/JSON shablon` tugmasi bilan Excel va JSON shablonlarini oling. Excel jadvalini tahrirlash qulayroq.
3. JSON ichida savol matnlari, variantlar, to'g'ri javoblar, yozma javob kalitlari va vaznlarni almashtiring.
4. `➕ Imtihon yuklash` ni bosing va tahrirlangan `.xlsx` yoki `.json` faylni yuboring.
5. `📚 Imtihonlar` ichidan imtihonni tanlab, oching.
6. Ishtirokchilar testni topshiradi.
7. Admin imtihonni yopsin. Bot avtomatik grading, Rasch hisoblash va XLSX/PDF hisobot tayyorlaydi.
8. Admin natijalarni tekshirib, `Natijalarni e'lon qilish` tugmasini bosadi.

## JSON savol shakli (Excel import ham mavjud)

MCQ:

```json
{
  "number": 1,
  "type": "mcq4",
  "topic": "Algebra",
  "prompt": "3x + 7 = 22. x ni toping.",
  "options": {"A": "3", "B": "5", "C": "7", "D": "9"},
  "correct": "B",
  "weight": 2
}
```

Six-choice MCQ uses `"type": "mcq6"` and keys A-F. Written:

```json
{
  "number": 36,
  "type": "written2",
  "topic": "Arifmetika",
  "prompt": "Hisoblang.",
  "parts": {
    "A": {"prompt": "25 × 4 = ?", "answer": "100", "answer_type": "numeric", "tolerance": 0, "weight": 4},
    "B": {"prompt": "120 ÷ 6 = ?", "answer": "20", "answer_type": "numeric", "tolerance": 0, "weight": 5}
  }
}
```

Supported written answer types: `numeric`, `fraction`, `expression`, `text`. Expression comparison uses SymPy; use it only when answer is an algebraic expression. For text answers matching is normalized exact text.

## Render deployment

1. GitHub repositoryga loyihani yuklang.
2. Render Dashboard → New → Blueprint orqali `render.yaml` ni import qiling.
3. `BOT_TOKEN`, `ADMIN_IDS`, `PUBLIC_BASE_URL` qiymatlarini so'ralganda kiriting.
4. Web service, Free Postgres va Free Key Value yaratiladi (tarif mavjudligi va workspace cheklovlariga bog'liq).
5. Deploy yakunlangach `/health/ready` endpointini tekshiring.
6. Telegram bot webhook'i `PUBLIC_BASE_URL/telegram/webhook` ga o'rnatiladi.

`PUBLIC_BASE_URL` qiymati `https://` bilan boshlanadigan Render servis URL bo'lishi kerak. Web service va datastoreni bir xil regionda saqlang.

## Backup

Free Postgres 30 kunlik muddat bilan yaratiladi va Render Free Postgres boshqariladigan backup/PITR taqdim etmaydi. Muhim imtihonlar oldidan va keyin ma'lumotlarni eksport qilib, xavfsiz joyda saqlang. Botning XLSX hisoboti javoblar va savollar tahlilini chiqaradi, lekin u to'liq database backup o'rnini bosmaydi.

## Testlar

```bash
pytest -q
```

## Ishonchlilik eslatmasi

Dastur kodi barcha holatlarda 100% ishlashini oldindan kafolatlab bo'lmaydi. Uni avval test Telegram botida sinab, kamida bitta to'liq pilot imtihon o'tkazing. Rasch kalibrlash natijalarini rasmiy baholash sifatida ishlatishdan oldin psixometrik mutaxassis bilan tekshiring.
