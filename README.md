# Sanoqchi Himoyachi — Low Limit + Gemini 1 Key

Bu versiya Railway limitni kamroq ishlatishga moslangan.

## Qo‘shilgan himoyalar

- Yangi kirgan odam 10 daqiqa link tashlasa o‘chiradi va mute qiladi
- Virus/spamga o‘xshash linklar: "telegram premium", "sovg‘a", "ovoz bering", "bonus", "pul ishlash" kabi matnlarni link bilan ushlaydi
- Link/reklama bloklash
- Yomon so‘zlar filtri
- 18+ caption/matn filtri
- Gemini 1 ta API key bilan rasm/video thumbnail 18+ tekshirish
- Kim odam qo‘shganini sanash: /top faqat admin, 20 tagacha chiqaradi uchun, /men hamma uchun, lekin kuniga 2 marta
- Kirdi/chiqdi xabarlarini tozalash
- Shubhali botlarni chiqarish
- Adminlarga alert

## Railway Variables

Asosiy:

```env
BOT_TOKEN=BotFather_token
ADMIN_IDS=123456789
CHANNEL_USERNAME=@kanalingiz
```

Himoya:

```env
BAD_WORDS=soz1,soz2,arabcha_soz
ADULT_WORDS=18soz1,18soz2,arabcha_soz
WARN_LIMIT=3
MUTE_MINUTES=60
MEN_DAILY_LIMIT=2
DELETE_ADULT_MEDIA=1
```

Gemini 18+ media:

```env
GEMINI_API_KEY=gemini_api_key
GEMINI_MODEL=gemini-3.5-flash
AI_MEDIA_FILTER=1
MAX_AI_FILE_MB=5
GEMINI_MAX_DAILY_CHECKS=100
```

Anti-virus spam:

```env
NEW_MEMBER_LINK_LOCK_MINUTES=10
SPAM_MUTE_MINUTES=60
SPAM_KEYWORDS=telegram premium,premium,sovg'a,sovga,ovoz bering,bonus,pul ishlash,kirib oling,yutuq,bepul,aksiya
ADMIN_ALERTS=1
```

Limitni tejash uchun:

```env
CHECK_SUBSCRIPTION_IN_GROUPS=0
```

Obuna tekshirish kerak bo‘lsa, kanalga botni admin qiling va keyin guruhda:

```text
/sub_on
```

## Guruh buyruqlari

```text
/sozlama
/top  # faqat admin, TOP 20
/men   # hamma uchun, kuniga 2 marta
/reset_top
/link_on /link_off
/bad_on /bad_off
/media_on /media_off
/ai18_on /ai18_off
/service_on /service_off
/sub_on /sub_off
/arabbot_on /arabbot_off
/autoban_on /autoban_off
/alert_on /alert_off
/post matn
```

## Bot admin huquqlari

Guruhda botga bering:

- Delete messages
- Ban users
- Manage chat
- Invite users

## Railway limitni kamaytirish uchun nima qilindi?

- Video ichidagi har bir kadr emas, faqat thumbnail tekshiriladi
- Bir xil rasm qayta kelsa, AI yana chaqirilmaydi — cache ishlaydi
- Kunlik Gemini tekshiruv limiti bor
- Obuna tekshirish default o‘chirilgan
- Google Vision kerak emas, faqat bitta Gemini key yetadi


## /men limiti

Oddiy foydalanuvchi `/men` buyrug‘ini kuniga 2 marta ishlata oladi. Adminlarga limit yo‘q. Railway Variables orqali o‘zgartirish mumkin:

```env
MEN_DAILY_LIMIT=2
```


## TOP 20

`/top` buyrug‘i faqat adminlar uchun va 20 tagacha foydalanuvchini chiqaradi.
