# ⏱ دليل الربط بجدولة خارجية (cron-job.org) — تحديث في الثانية المحددة

GitHub Actions لا يضمن الالتزام بمواعيد الجدولة (cron) بدقة — كثيرًا ما يتأخر 10–45 دقيقة.
الحل: خدمة جدولة خارجية مجانية (cron-job.org) تستدعي GitHub API في الموعد **بالثانية**،
فينطلق التحليل فورًا. جدولة GitHub تبقى كاحتياط.

## كيف يعمل الربط

```
cron-job.org (كل 15 دقيقة، بالثانية المحددة)
        │  POST إلى GitHub API
        ▼
GitHub → تنفيذ analyze-deploy.yml فورًا (تحليل ← التزام البيانات ← نشر الصفحة)
        │
        ▼
الموقع https://turky4500.github.io/binance-trading-dashboard/ يلتقط البيانات الجديدة
تلقائيًا (العداد في الصفحة يصل للصفر → يسحب → يعرض)
```

تم اختبار هذا المسار فعليًا: إطلاق خارجي في `10:24:43` → دورة كاملة ناجحة (اختبارات + تحليل + نشر) خلال ~90 ثانية.

---

## الخطوات (مرة واحدة، ~3 دقائق)

### 1) أنشئ توكن GitHub جديدًا (مهم للأمان)

التوكن القديم تمت مشاركته في المحادثة، لذا أنشئ توكنًا جديدًا **صلاحياته محدودة** فقط على هذا المستودع:

1. افتح https://github.com/settings/tokens?type=beta
2. **Generate new token** (Fine-grained)
3. الاسم: `cron-job-dashboard` — المدة: سنة أو كما تريد
4. **Repository access** → `Only select repositories` → اختر `turky4500/binance-trading-dashboard`
5. **Permissions** → **Actions** → `Read and write` (فقط هذه الصلاحية)
6. **Generate token** وانسخه (يبدأ بـ `github_pat_...`)

> ملاحظة: التوكن الجديد هذا سيوضع في cron-job.org — ولا يحتاج أي صلاحية على الكود، فقط تشغيل الـ workflow.

### 2) أنشئ حسابًا مجانيًا في cron-job.org

1. افتح https://cron-job.org → **Sign up** (بريدك الإلكتروني + كلمة مرور)
2. فعّل الحساب من رسالة التأكيد، ثم سجّل الدخول

### 3) أنشئ المهمة (Cronjob)

من لوحة التحكم اضغط **Create cronjob** واملأ بالضبط:

| الحقل | القيمة |
|---|---|
| **Title** | `Binance Dashboard Analysis` |
| **Address (URL)** | `https://api.github.com/repos/turky4500/binance-trading-dashboard/actions/workflows/analyze-deploy.yml/dispatches` |
| **Schedule** | `*/15 * * * *` (كل 15 دقيقة) — أو `*/5 * * * *` لمراقبة أدق |
| **Request method** | `POST` |

ثم في **Advanced**:

| الحقل | القيمة |
|---|---|
| **Header 1 name** | `Authorization` |
| **Header 1 value** | `Bearer github_pat_XXXXXXXX` (التوكن الجديد من الخطوة 1) |
| **Header 2 name** | `Accept` |
| **Header 2 value** | `application/vnd.github+json` |
| **Header 3 name** | `X-GitHub-Api-Version` |
| **Header 3 value** | `2022-11-28` |
| **Header 4 name** | `Content-Type` |
| **Header 4 value** | `application/json` |
| **Request body** | `{"ref":"main"}` |

فعّل **Error notification** (بريدك) لتصلك رسالة إذا فشل الإطلاق لأي سبب، ثم **Save**.

### 4) اختبر فورًا

1. في صفحة المهمة اضغط زر **Run** (تشغيل يدوي)
2. النتيجة المطلوبة في سجل المهمة: **HTTP 204 No Content** = تم الإطلاق بنجاح
3. تحقق هنا https://github.com/turky4500/binance-trading-dashboard/actions — سترى تشغيلًا جديدًا باسم `workflow_dispatch` بدأ خلال ثوانٍ

---

## الإطلاق يدويًا من أي جهاز (بديل/اختبار)

```bash
GITHUB_TOKEN=github_pat_XXX python3 scripts/dispatch.py
```

أو مباشرة:

```bash
curl -X POST \
  -H "Authorization: Bearer github_pat_XXX" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -H "Content-Type: application/json" \
  -d '{"ref":"main"}' \
  https://api.github.com/repos/turky4500/binance-trading-dashboard/actions/workflows/analyze-deploy.yml/dispatches
```

الاستجابة المتوقعة: `HTTP 204`.

---

## الأسئلة الشائعة

- **هل يمكن تشغيل المهمة كل دقيقة؟** نعم تقنيًا (التحليل نفسه يستغرق ~90 ثانية، و`concurrency` يمنع التداخل)، لكن 15 دقيقة كافية وتوفر موارد.
- **ماذا لو انطلق الإطلاقان معًا (cron-job + جدولة GitHub)؟** لا ضرر — التشغيل الثاني ينتظر في الطابور ثم ينفذ، والنتيجة بيانات أحدث.
- **هل جدولة GitHub تبقى ضرورية؟** لا، لكنها تعمل كاحتياط مجاني إذا توقف cron-job.org مؤقتًا.
- **أمان التوكن:** توكن Fine-grained بصلاحية Actions فقط على مستودع واحد — أسوأ سيناريو هو تشغيل التحليل، لا وصول للكود ولا أي شيء آخر. ويمكن إلغاؤه فورًا من إعدادات GitHub في أي وقت.
