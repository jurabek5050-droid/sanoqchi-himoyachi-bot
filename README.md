# Sanoqchi Himoyachi Bot — AI 18+ rasm/video filtri

Bu versiyada 18+ rasm va video thumbnail AI orqali tekshiriladi.

## Railway Variables

```env
BOT_TOKEN=BotFather tokeni
ADMIN_IDS=123456789
CHANNEL_USERNAME=@kanalingiz

GOOGLE_VISION_API_KEY=google_vision_api_key
SAFESEARCH_BLOCK_LEVEL=3
MAX_AI_FILE_MB=8

BAD_WORDS=soz1,soz2,arabcha_soz
ADULT_WORDS=18soz1,18soz2,arabcha_soz

WARN_LIMIT=3
MUTE_MINUTES=60
```

Qo‘shimcha:

```env
DELETE_SERVICE_MESSAGES=1
CHECK_SUBSCRIPTION_IN_GROUPS=1
DELETE_LINKS=1
DELETE_BAD_WORDS=1
DELETE_ADULT_MEDIA=1
BLOCK_ARABIC_BOTS=1
BLOCK_NEW_BOTS=0
AUTO_BAN_ENABLED=1
```

## 18+ filter qanday ishlaydi?

- Rasm yuborilsa: rasm Google SafeSearch bilan tekshiriladi.
- Video yuborilsa: Telegram bergan video thumbnail tekshiriladi.
- GIF/animation/document image: thumbnail yoki rasm tekshiriladi.
- Caption/matnda ADULT_WORDS dagi so‘z bo‘lsa, xabar o‘chiriladi.

Video ichidagi har bir kadrni tekshirish juda ko‘p resurs yeydi. Bu versiya Railway limitni tejash uchun video thumbnailni tekshiradi.
