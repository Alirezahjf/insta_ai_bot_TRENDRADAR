# 🎯 TrendRadar — موتور ترند‌یابی چند‌پیام‌رسانی

> توسعه‌ی ربات ترند‌یابی: از اینستاگرام به **تلگرام** و آماده برای هر پیام‌رسان دیگر.

---

## 📐 معماری در یک نگاه

```
                          ┌──────────────────────────┐
                          │      TrendRadar          │  ← واسط sync (Facade)
                          │  scan() / health() /     │     ربات موجود با این کار می‌کند
                          │  auth_*() / join_*()     │
                          └────────────┬─────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              │                        │                        │
     ┌────────▼────────┐    ┌──────────▼─────────┐   ┌──────────▼─────────┐
     │   AsyncRuntime  │    │  ProviderRegistry  │   │     Storage        │
     │  پل async↔sync  │    │  رجیستری پلاگین‌ها  │   │  SQLite + سری‌زمانی │
     └────────┬────────┘    └──────────┬─────────┘   └──────────┬─────────┘
              │                        │                        │
              │              ┌─────────▼──────────┐             │
              │              │  MessengerProvider │ ← قرارداد یکسان
              │              │      (interface)   │             │
              │              └─────────┬──────────┘             │
              │                        │                        │
       ┌──────▼────────────────────────▼────────────────────────▼──────┐
       │                          TrendPipeline                        │
       │  ۱. کانال هدف → ۲. عضویت (با گارد) → ۳. جمع‌آوری → ۴. ذخیره    │
       │                    → ۵. تحلیل → ۶. گزارش                      │
       └───────────────────────────────┬───────────────────────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │      TrendAnalyzer       │  ← مستقل از پیام‌رسان!
                          │  متریک‌های قابل افزودن   │     فقط با Post کار می‌کند
                          └──────────────────────────┘

  ┌──────────────────────────┐   ┌──────────────────┐   ┌─────────────────┐
  │  providers/telegram/     │   │ providers/bale/  │   │ providers/...   │
  │  Telethon (MTProto)      │   │  (آینده)         │   │  (آینده)        │
  └──────────────────────────┘   └──────────────────┘   └─────────────────┘
```

### اصل طراحی مرکزی

> **هیچ‌کدام از لایه‌های بالایی نمی‌دانند تلگرام وجود دارد.**
>
> `analyzer` فقط `Post` می‌شناسد. `pipeline` فقط `MessengerProvider` می‌شناسد.
> `storage` فقط مدل‌های دامنه را ذخیره می‌کند.
> تنها جایی که «تلگرام» معنا دارد، پوشه‌ی `providers/telegram/` است.

---

## 📂 ساختار فایل‌ها

```
trendradar/
├── __init__.py                   ← API عمومی پکیج
├── trend_radar.py                ← 🎯 Facade: واسط sync برای ربات
├── models.py                     ← 🧱 مدل‌های دامنه (Post, ChannelRef, ...)
├── config.py                     ← ⚙️ تنظیمات (از .env)
├── errors.py                     ← 🚨 خطاهای دامنه (مستقل از کتابخانه)
├── runtime.py                    ← 🌉 پل async ↔ sync
├── registry.py                   ← 🗂️ رجیستری پرووایدرها (پلاگین)
├── storage.py                    ← 💾 SQLite + سری زمانی
├── analyzer.py                   ← 🧠 موتور امتیازدهی (متریک‌های پلاگین‌شدنی)
├── pipeline.py                   ← 🏭 ارکستریتور اسکن
│
├── providers/
│   ├── base.py                   ← 📜 قرارداد MessengerProvider
│   ├── __init__.py               ← ثبت خودکار پرووایدرها
│   └── telegram/
│       ├── __init__.py           ← ثبت در رجیستری
│       ├── provider.py           ← پیاده‌سازی قرارداد
│       ├── client.py             ← اتصال، session، احراز هویت، ترجمه‌ی خطا
│       ├── guard.py              ← 🛡️ گاردهای ضد‌بن
│       ├── search.py             ← 🔎 ۳ استراتژی جستجوی هشتگ
│       ├── discovery.py          ← 🔭 ۴ منبع کشف کانال
│       ├── membership.py         ← 🚪 عضویت/ترک با گارد
│       └── mapper.py             ← 🔄 تبدیل TL objects → مدل‌های دامنه
│
└── tools/
    ├── trend_cli.py              ← ⌨️ ابزار خط فرمان
    └── test_pipeline.py          ← 🧪 ۹۸ تست (بدون نیاز به شبکه)
```

---

## 🔌 افزودن پیام‌رسان جدید (مثلاً «بله»)

فقط **یک پوشه** اضافه می‌کنی. هیچ فایل موجودی تغییر نمی‌کند.

### گام ۱ — پوشه بساز

```
trendradar/providers/bale/
├── __init__.py
├── provider.py
└── mapper.py
```

### گام ۲ — پرووایدر را بنویس

```python
# trendradar/providers/bale/provider.py
from ..base import Capability, MessengerProvider
from ...models import ChannelRef, Post, ProviderHealth, MediaType
from ...registry import register_provider


@register_provider                        # ← ثبت خودکار! همین یه خط کافیه
class BaleProvider(MessengerProvider):
    name = "bale"
    display_name = "بله"
    capabilities = {
        Capability.SEARCH_HASHTAG,
        Capability.SEARCH_IN_CHANNEL,
        Capability.DISCOVER_CHANNELS,
        Capability.JOIN_CHANNEL,
        Capability.LIST_JOINED,
    }

    def __init__(self, settings=None):
        super().__init__(settings)
        self.token = ...                  # از settings

    async def connect(self) -> None:
        ...                               # در صورت نیاز AuthRequired بده

    async def disconnect(self) -> None: ...

    async def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, name=self.display_name,
                              available=True, authorized=True)

    async def discover_channels(self, query, *, limit=20, min_members=0, exclude=None):
        # داده‌ی خام را به ChannelRef تبدیل کن
        return [ChannelRef(provider="bale", native_id=..., title=..., members=...)]

    async def search_hashtag(self, tag, *, channels=(), limit=50, since=None):
        # داده‌ی خام را به Post تبدیل کن
        return [Post(provider="bale", channel_uid=..., message_id=..., date=...)]

    async def join_channel(self, channel, *, dry_run=False):
        ...                               # گاردها را این‌جا هم اعمال کن
        return channel

    async def list_joined(self, *, limit=200): ...
    async def resolve(self, identifier): ...
```

### گام ۳ — در لیست ثبت کن

```python
# trendradar/providers/__init__.py
_BUILTIN = [
    ("telegram", "trendradar.providers.telegram", False),
    ("bale",     "trendradar.providers.bale",     False),   # ← این خط
]
```

### گام ۴ — در `.env` فعال کن

```env
TREND_PROVIDERS=telegram,bale
```

**تمام.** ✅
موتور تحلیل، ذخیره‌سازی، ادغام چند‌پلتفرمی و پل اینستاگرام **خودکار** کار می‌کنند.

> 💡 نکته: `load_builtin_providers()` با **import تنبل** کار می‌کند. اگر کتابخانه‌ی
> «بله» نصب نباشد، فقط یک لاگ INFO می‌دهد و تلگرام بدون اختلال کار می‌کند.

---

## ⚖️ افزودن متریک ترند جدید

```python
from trendradar.analyzer import Metric

class VideoBiasMetric(Metric):
    name = "video_bias"          # کلید یکتا
    label = "تمایل به ویدیو"
    description = "سهم پست‌های ویدیویی از کل پست‌ها"

    def compute(self, ctx) -> float:
        if not ctx.posts:
            return 0.0
        n = sum(1 for p in ctx.posts if p.media_type == MediaType.VIDEO)
        return n / len(ctx.posts)          # ۰..۱

    def explain(self, ctx, value) -> str:
        return f"🎬 {value*100:.0f}% پست‌ها ویدیویی"

# استفاده
analyzer = TrendAnalyzer().add_metric(VideoBiasMetric(), weight=0.10)
```

وزن‌ها **خودکار نرمال** می‌شوند (جمعشان ۱ می‌ماند).
می‌توانی وزن‌ها را از `.env` هم بدهی:

```env
TREND_W_VELOCITY=0.30
TREND_W_REACH=0.22
```

### متریک‌های فعلی

| متریک | وزن پیش‌فرض | چه می‌سنجد |
|---|---|---|
| `velocity` | ۰.۳۰ | سرعت انتشار (پست/ساعت) + **شتاب** نسبت به گذشته |
| `reach` | ۰.۲۲ | میانه‌ی بازدید یا اندازه‌ی مخاطب |
| `engagement` | ۰.۲۰ | نرخ تعامل (ری‌اکشن + فوروارد + کامنت) |
| `spread` | ۰.۱۵ | تعداد کانال‌های مستقل (با جریمه‌ی تک‌منبع) |
| `freshness` | ۰.۱۳ | تازگی با نیم‌عمر نمایی (پیش‌فرض ۳۶ ساعت) |

**نکته‌ی مهم:** همه‌ی متریک‌ها **نسبی به همان بچ** نرمال می‌شوند. یعنی الگوریتم
روی هشتگ کوچک و بزرگ به یک اندازه درست کار می‌کند — همان فلسفه‌ی `scoring.py`
پروژه‌ی اینستاگرام.

---

## 🔬 متدهای واقعی Telethon که استفاده شده

همه از **Telethon 1.45.0، Layer 229** استخراج و با تست تأیید شده‌اند
(`test_telegram_real_api`).

| عملیات | متد واقعی | محل استفاده |
|---|---|---|
| **جستجوی هشتگ در چنل** ⭐ | `channels.SearchPostsRequest(offset_rate, offset_peer, offset_id, limit, hashtag=...)` | `search.py` |
| جستجوی سراسری | `messages.SearchGlobalRequest(q, filter, min_date, offset_rate, offset_peer, offset_id, limit, ...)` | `search.py` |
| جستجو در تاریخ چت | `messages.SearchRequest` (از طریق `client.iter_messages(search=...)`) | `search.py` |
| کشف کانال | `contacts.SearchRequest(q, limit, broadcasts=None)` | `discovery.py` |
| کانال‌های مشابه | `channels.GetChannelRecommendationsRequest(channel)` | `discovery.py` |
| آمار کانال | `channels.GetFullChannelRequest(channel)` → `ChannelFull` | `discovery.py` |
| لیست عضوها | `client.iter_dialogs()` | `discovery.py` |
| عضویت | `channels.JoinChannelRequest(channel)` | `membership.py` |
| ترک | `channels.LeaveChannelRequest(channel)` | `membership.py` |
| احراز هویت | `send_code_request` / `sign_in` / `sign_in(password=)` | `client.py` |

### نکته‌ی کلیدی درباره‌ی `SearchPostsRequest`

این متد **دو پارامتر متفاوت** دارد که نباید اشتباه شوند:

```python
# ✅ درست — هشتگ بدون علامت #، جستجو در ایندکس هشتگ تلگرام (دقیق‌تر)
SearchPostsRequest(..., hashtag="ترند_مدلینگ", query=None)

# ❌ اشتباه — با # کار نمی‌کند
SearchPostsRequest(..., hashtag="#ترند_مدلینگ")
```

صفحه‌بندی با فیلد `next_rate` از خروجی `messages.MessagesSlice` انجام می‌شود:

```python
result = await client(SearchPostsRequest(offset_rate=0, offset_id=0,
                                          offset_peer=InputPeerEmpty(), limit=100,
                                          hashtag=tag))
# صفحه‌ی بعد:
result = await client(SearchPostsRequest(offset_rate=result.next_rate,
                                          offset_id=result.messages[-1].id, ...))
```

### خطاهایی که مدیریت می‌شوند

| خطای Telethon | ترجمه‌ی ما | اقدام |
|---|---|---|
| `FloodWaitError` (فیلد `.seconds`) | `RateLimited` | توقف + مسدودسازی گارد |
| `AuthKeyUnregisteredError` | `SessionInvalid` | درخواست لاگین مجدد |
| `UserDeactivatedBanError` | `SessionInvalid` | هشدار بن شدن اکانت |
| `SessionPasswordNeededError` | `PasswordRequired` | درخواست رمز ۲مرحله‌ای |
| `ChannelPrivateError` | `ChannelNotAccessible` | رد کردن کانال |
| `ChannelsTooMuchError` | `DailyCapReached` | هشدار سقف کانال‌ها |
| `UserAlreadyParticipantError` | (موفقیت) | ادامه |
| `InviteRequestSentError` | (نیمه‌موفق) | علامت `pending_approval` |

---

## 🛡️ سیستم ضد‌بن (بخش حیاتی)

این پرووایدر با **اکانت کاربری** کار می‌کند، نه بات رسمی. تلگرام رفتار
ربات‌گونه را تشخیص می‌دهد و اکانت را **بن** می‌کند. سه لایه محافظت:

### ۱. `RateLimiter` — فاصله‌ی بین درخواست‌ها
Token bucket + **jitter تصادفی**. jitter حیاتی است: الگوی زمانی کاملاً منظم
(هر دقیقاً ۳ ثانیه یک درخواست) خودش یک امضای رباتی است.

### ۲. `JoinGuard` — حسّاس‌ترین بخش
- فاصله‌ی **تصادفی** بین ۹۰ تا ۲۴۰ ثانیه بین دو عضویت
- سقف روزانه (پیش‌فرض ۲۰ — از این بالاتر نبر)
- لیست سیاه کانال‌هایی که خطا دادند
- **خنک‌شدن ۱۰ دقیقه‌ای** بعد از ۳ خطای پشت‌سرهم

```
گارد عضویت: 3 خطای پشت‌سرهم → 600 ثانیه خنک‌شدن
عضویت ثبت شد (telegram:-1001234) | امروز: 7/20 | عضویت بعدی 183 ثانیه دیگر
```

### ۳. `DailyCounter` — سقف روزانه
با ریست خودکار در روز جدید و **قابل ذخیره بین اجراها** — یعنی اگر ربات
ری‌استارت شود، سقف روزانه از صفر شروع نمی‌شود.

```python
stats = radar.guard_stats()
# {'joined_today': 7, 'daily_cap': 20, 'remaining_today': 13,
#  'seconds_until_next': 183.4, 'blacklisted': 2, 'cooldown_active': False}
```

### 💡 توصیه‌ی عملی

از **همین اکانت شخصی‌ات** استفاده نکن. یک اکانت جداگانه بساز و حداقل
یک هفته بگذار «گرم» شود (عضویت در چند کانال، چت عادی) قبل از اینکه
ترند‌یابی خودکار را روشن کنی.

---

## 🔗 اتصال به ربات موجود

```python
# در jobs.py یا هر جای ربات تلگرام‌باتی
from trendradar import TrendRadar

radar = TrendRadar()
radar.register_default_providers()

# ۱) وضعیت
for h in radar.health():
    print(f"{h.status_emoji} {h.display_name}: {h.account or h.error}")

# ۲) ورود تعاملی (یک‌بار)
msg = radar.auth_send_code()          # "کد تایید از طریق اپلیکیشن ارسال شد"
# ← کاربر کد را در ربات می‌فرستد
radar.auth_sign_in("12345")
# در صورت داشتن رمز دومرحله‌ای:
radar.auth_sign_in_password("mypass")

# ۳) اسکن
report = radar.scan(hashtags=["ترند", "مدلینگ"], window_hours=48)
for s in report.signals[:5]:
    print(f"#{s.rank} #{s.tag} — {s.score}/100")
    print(s.reason)

# ۴) ⭐ اتصال به اینستاگرام: ترندهای تلگرام → اسکرپ اینستاگرام
terms = radar.signals_to_instagram_terms(report.signals, top=5)
for t in terms:
    scrape_hashtag(t)     # از apify_service.py موجود
```

> همه‌ی متدهای بالا **sync** هستند و از تردهای `pyTelegramBotAPI` بدون
> تغییر کد قابل استفاده‌اند. پل `AsyncRuntime` این را ممکن می‌کند.

---

## ⌨️ ابزار خط فرمان

```bash
cd /root/Desktop/insta_ai_bot

# وضعیت + گارد + آمار
python -m trendradar.tools.trend_cli health

# ورود به تلگرام (کد را می‌پرسد)
python -m trendradar.tools.trend_cli login

# کشف کانال با یک عبارت
python -m trendradar.tools.trend_cli find "مدلینگ" --min-members 1000

# اسکن ترند
python -m trendradar.tools.trend_cli scan ترند_مدلینگ فروش_لباس --hours 48 --json

# کانال‌های عضو
python -m trendradar.tools.trend_cli joined

# لیست پرووایدرها و قابلیت‌هایشان
python -m trendradar.tools.trend_cli providers

# اجرای تست‌ها (بدون نیاز به شبکه)
python trendradar/tools/test_pipeline.py
```

---

## 🧪 تست‌ها

```bash
python trendradar/tools/test_pipeline.py
# ⇒ نتیجه: 98/98 تست موفق
```

تست‌ها **بدون نیاز به اینترنت/تلگرام** اجرا می‌شوند (با `MockProvider`).
پوشش:

| # | تست | چه چیزی |
|---|---|---|
| ۱ | مدل‌های دامنه | نرمال‌سازی هشتگ فارسی/عربی، استخراج، uid |
| ۲ | رجیستری | ثبت، cache نمونه، توصیف قابلیت‌ها |
| ۳ | پل async↔sync | اجرا از sync، موازی، **جلوگیری از deadlock** |
| ۴ | موتور تحلیل | **رتبه‌بندی درست با داده‌ی کنترل‌شده** |
| ۵ | متریک پلاگین | افزودن متریک در زمان اجرا + نرمال‌سازی وزن |
| ۶ | ذخیره‌سازی | upsert، سری زمانی، فیلتر، بازیابی وضعیت |
| ۷ | خط لوله | **انتها به انتها: کشف→جمع‌آوری→تحلیل→گزارش** |
| ۸ | ادغام چندپلتفرمی | پاداش دیده‌شدن روی چند پیام‌رسان |
| ۹ | پل اینستاگرام | تبدیل ترند → هشتگ اینستاگرام |
| ۱۰ | گاردهای ضد‌بن | سقف روزانه، فاصله، خنک‌شدن، بازیابی وضعیت |
| ۱۱ | **API واقعی Telethon** | تأیید تطابق کد با امضاهای واقعی ۱.۴۵.۰ |
| ۱۲ | **رگرسیون deadlock** | هر متد sync با watchdog زمانی بررسی می‌شود |

> 🐛 تست ۱۲ از یک باگ واقعی متولد شد: `JoinGuard.stats()` قفل را می‌گرفت و
> داخلش پراپرتی‌ای را صدا می‌زد که همان قفل را می‌خواست → برنامه **بی‌صدا
> هنگ می‌کرد**. حالا قفل‌ها `RLock` هستند و watchdog تست، چنین باگی را
> فوراً می‌گیرد.

---

## ⚠️ نکات عملیاتی

### اول: نصب و تنظیمات

```bash
pip install -r requirements.txt          # شامل telethon و PySocks
cp .env.example .env && nano .env        # TG_API_ID, TG_API_HASH, TG_PHONE
```

`TG_API_ID` و `TG_API_HASH` را از **[my.telegram.org](https://my.telegram.org)**
→ API development tools بگیر.
> ⚠️ این‌ها با توکن ربات `@BotFather` **کاملاً متفاوت** اند. ربات‌های رسمی
> نمی‌توانند در کانال عضو شوند یا هشتگ سرچ کنند — به همین دلیل MTProto لازم است.

### دوم: پروکسی (برای سرورهای ایران)

```env
TG_PROXY=socks5://127.0.0.1:1080
```

### سوم: فایل session

بعد از اولین لاگین، فایلی به نام `trend_telegram.session` ساخته می‌شود.
**این فایل مثل پسورد است** — هر کسی آن را داشته باشد به اکانت دسترسی دارد.

```gitignore
*.session
*.session-journal
trendradar.db
```

### چهارم: نگهداری دیتابیس

```python
radar.storage.prune(keep_days=60)        # حذف داده‌ی قدیمی
```

---

## 🗺️ نقشه‌ی راه پیشنهادی

- [x] پرووایدر تلگرام (MTProto)
- [x] موتور تحلیل با متریک‌های پلاگین
- [x] گارد ضد‌بن
- [x] ذخیره‌سازی + سری زمانی
- [x] ابزار CLI
- [ ] **افزودن دکمه‌های ترند‌یابی به منوی ربات** ← قدم بعدی
- [ ] ورود تعاملی از داخل ربات (کد/رمز در چت)
- [ ] اسکژول خودکار ترند‌یابی (مثل اسکژول اینستاگرام موجود)
- [ ] ارسال گزارش خودکار ترندها به چت
- [ ] پرووایدرهای دیگر (بله، روبیکا، واتساپ)
- [ ] ذخیره‌ی رسانه‌ی پست ترند (عکس/ویدیو)
- [ ] مقایسه‌ی ترند تلگرام vs اینستاگرام
