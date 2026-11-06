# Bank statement fixtures

**The most useful thing you can send this project is the *shape* of your bank's statement.**

OfinTech Desk reads a statement file and turns it into lines a person can reconcile. Banks write the same five
facts in endlessly different ways: the header three rows down under a letterhead, money in and money out as two
columns or one signed column, the minus sign written *after* the figure, `CR`/`DR` in a column of its own,
dates as `03/04/2026` (which is two different days depending on the country), Arabic headers, a totals row in
the middle. No one can guess them all. A reader that works for one country's banks quietly breaks for another's
unless the shapes are kept and re-read on every build — which is what this folder is.

You do not need to write code, and you must not send a real statement.

## What a fixture is

A folder with an invented statement file and a note of what the file says:

```
fixtures/statements/<country>/<bank-or-shape>/
    statement.csv         the invented statement (.csv or .xlsx)
    expected.json         what the file says, in words and numbers
```

`<country>` is the two-letter code (`gb`, `us`, `sa`, `ae`, `eg`…), `<bank-or-shape>` a short slug —
`barclays-business`, `chase-checking`, `trailing-cr-dr`. Two worked examples are in
[`statements/example/`](statements/example/): one with money-in/money-out columns and a letterhead above the
header, one with a single signed amount column, a minus after the figure and a negative in brackets.

`expected.json`:

```json
{
  "about": "One line on what makes this shape awkward.",
  "country": "gb",
  "bank": "Example Bank plc (invented)",
  "file": "statement.csv",
  "invented": true,
  "expect": {
    "lines": 8,
    "first_date": "2026-06-02",
    "last_date": "2026-06-30",
    "total": -10134.39,
    "closing": { "date": "2026-06-30", "amount": 109865.61 }
  }
}
```

`total` is every line added up with **money in positive and money out negative**. `closing` is the last running
balance in the file, or `null` when the file has no balance column. Leave out anything your file does not have.

## How to make one safely

Take your real statement, then **replace every figure, name, account number and reference**. Keep only the
shape: the same columns in the same order, the same date format, the same way the sign is written, the same
rows above and below the table. Eight or ten rows is plenty.

Nothing real belongs here — no account numbers, IBANs, card numbers, customer names or amounts from a real
account. `validate.py` refuses a file with a card-shaped number or something shaped like an IBAN, but the rule
is yours to keep, not the script's to enforce.

Make the figures add up: if your file has a running balance, let the balances follow from the amounts, because
that is what the product checks when it reconciles.

## Check it before you send it

```bash
python fixtures/validate.py                                   # all of them
python fixtures/validate.py fixtures/statements/gb/my-bank    # just yours
```

Write the fixture first and run it **without** an `expect` block: the script prints what the engine read, and
you compare each number with the file by eye before putting it into `expected.json`. Copying the engine's
output in without looking would lock a wrong reading in place, which is worse than no fixture at all.

If the engine refuses your file or reads it wrongly, **that is the valuable case.** Send the fixture anyway,
with `expect` set to what the file really says, and open an issue with
[*A bank statement is not read correctly*](../../issues/new?template=statement-not-read.yml). A failing fixture
is a bug report nobody can misunderstand.

Every fixture here runs in `engine/tests/test_statement_corpus.py` on every build, so once your bank's shape is
in, it stays read.

The other half of the same idea is next door: [`charts-of-accounts/`](charts-of-accounts/README.md), where the
chart your country's accountants use is kept against what each account is *for*.

---

# أشكال كشوف البنوك

**أنفع ما يمكن أن ترسله لهذا المشروع هو *شكل* كشف بنكك.**

البنوك تكتب الحقائق الخمس نفسها بطرق لا تنتهي: العنوان تحت ترويسة في الصف الثالث، ومدين ودائن في عمودين أو في
عمود واحد بإشارة، وإشارة السالب **بعد** الرقم، و`CR`/`DR` في عمود مستقل، وتاريخ `03/04/2026` الذي يعني يومين
مختلفين حسب البلد، وعناوين عربية، وصف مجاميع في وسط الجدول. لا أحد يستطيع تخمينها كلها، والقارئ الذي يعمل مع
بنوك بلد يتعطّل بصمت مع بنوك بلد آخر إن لم تُحفظ الأشكال وتُقرأ في كل بناء — وهذا هو عمل هذا المجلد.

لا تحتاج أن تكتب كوداً، ولا يجوز أن ترسل كشفاً حقيقياً.

**الشكل:** مجلد باسم `fixtures/statements/<رمز البلد>/<البنك أو الشكل>/` فيه `statement.csv` (أو `.xlsx`)
و`expected.json` يقول ما يقوله الملف: عدد السطور، أول تاريخ وآخره، المجموع (الداخل موجب والخارج سالب)، والرصيد
الختامي إن كان في الملف عمود أرصدة — وإلا `null`.

**كيف تصنعه بأمان:** خُذ كشفك الحقيقي، ثم **استبدل كل رقم واسم وحساب ومرجع**. أبقِ الشكل وحده: الأعمدة نفسها
بالترتيب نفسه، وصيغة التاريخ نفسها، وطريقة كتابة الإشارة نفسها، والصفوف التي تعلو الجدول وتذيّله. ثمانية أو عشرة
صفوف تكفي. ولا شيء حقيقي هنا: لا أرقام حسابات ولا IBAN ولا أرقام بطاقات ولا أسماء عملاء ولا مبالغ من حساب قائم.

**تحقّق قبل الإرسال:** `python fixtures/validate.py` — واكتب الـ fixture أولاً **بدون** `expect`، فيطبع لك ما
قرأه المحرّك، فتقابل كل رقم بالملف بعينك قبل أن تضعه في `expected.json`. نسخ مخرجات المحرّك بلا مراجعة يثبّت
قراءة خاطئة، وهذا أسوأ من لا شيء.

وإن رفض المحرّك ملفك أو قرأه خطأً **فهذه هي الحالة الثمينة**: أرسل الـ fixture على حاله مع `expect` الصحيح،
وافتح issue. والعربية مرحّب بها في الـ issues.
